"""
ISK per hour: which wallet payouts belong to which Gamelog session, how the
rate is computed, and where it shows up. ESI is mocked; nothing leaves the test.
"""

# Standard Library
from datetime import timedelta
from decimal import Decimal
from unittest import mock

# Django
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

# Alliance Auth
from allianceauth.tests.auth_utils import AuthUtils

from .. import payouts, tasks
from ..app_settings import WALLET_SCOPE
from ..leaderboard import leaderboard_rows
from ..models import CombatLogUpload, PilotSettings
from .test_sources import _entry, _pilot, _token

M = Decimal(1_000_000)


class IskPerHourTestCase(TestCase):
    def setUp(self):
        self.now = timezone.now().replace(microsecond=0)
        self.user, self.character = _pilot("ratter", "Example Ratter", 1000000701)
        self.n = 0

    def session(self, start_ago, minutes, character=None, user=None):
        """A session that started *start_ago* before now and lasted *minutes*."""
        self.n += 1
        start = self.now - start_ago
        return CombatLogUpload.objects.create(
            character=character or self.character, uploaded_by=user or self.user,
            source_filename=f"{self.n}.txt", file_hash=f"isk-{self.n}",
            session_started=start, session_ended=start + timedelta(minutes=minutes),
        )

    def pay(self, ref_id, when, amount, ref_type="bounty_prizes", character=None):
        payouts.store_journal(character or self.character, [_entry(ref_id, ref_type, float(amount), when=when)])

    def connect_wallet(self, user=None, character=None):
        return _token(user or self.user, character or self.character, WALLET_SCOPE)


class MatchingTests(IskPerHourTestCase):
    def test_counts_bounties_and_missions_in_the_window(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        start, end = session.session_started, session.session_ended
        self.pay(1, start + timedelta(minutes=20), 10 * M)                      # during
        self.pay(2, end + timedelta(minutes=19), 5 * M)                          # last tick, within 20 min
        self.pay(3, start + timedelta(minutes=30), 2 * M, "agent_mission_reward")
        self.pay(5, end + timedelta(minutes=30), 7 * M)                          # too late
        self.pay(6, start - timedelta(minutes=5), 7 * M)                          # before it started
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 17 * M)
        self.assertEqual(session.isk_per_hour, 17 * M)

    def test_overlapping_windows_give_the_payout_to_the_later_session(self):
        self.connect_wallet()
        first = self.session(timedelta(hours=5), 60)
        second = self.session(timedelta(hours=5) - timedelta(minutes=65), 60)  # starts 5 min after the first ends
        self.pay(1, first.session_ended + timedelta(minutes=10), 4 * M)       # inside both windows
        payouts.match_sessions(self.character)
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertEqual(first.payout_isk, 0)
        self.assertEqual(second.payout_isk, 4 * M)
        self.assertEqual(first.payout_isk + second.payout_isk, 4 * M)

    def test_no_wallet_means_unknown_not_zero(self):
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 10 * M)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertIsNone(session.payout_isk)
        self.assertIsNone(session.isk_per_hour)

    def test_connected_wallet_with_no_payouts_is_zero(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 0)
        self.assertEqual(session.isk_per_hour, 0)

    def test_short_sessions_get_no_rate(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 5)
        self.pay(1, session.session_started + timedelta(minutes=3), 10 * M)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 10 * M)
        self.assertIsNone(session.isk_per_hour)

    def test_sessions_past_retention_keep_their_value(self):
        self.connect_wallet()
        old = self.session(timedelta(days=60), 60)
        old.payout_isk = Decimal("123.00")
        old.save(update_fields=["payout_isk"])
        payouts.match_sessions(self.character)
        old.refresh_from_db()
        self.assertEqual(old.payout_isk, Decimal("123.00"))

    def test_other_characters_payouts_are_ignored(self):
        self.connect_wallet()
        other_user, other = _pilot("other", "Example Other", 1000000702)
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 9 * M, character=other)
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 0)

    def test_opting_out_clears_session_isk(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 10 * M)
        payouts.match_sessions(self.character)
        PilotSettings.objects.create(user=self.user, wallet_opt_out=True)
        payouts.delete_wallet_data(self.user)
        session.refresh_from_db()
        self.assertIsNone(session.payout_isk)
        # And it stays unknown while opted out.
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        self.assertIsNone(session.payout_isk)


class EssShareTests(IskPerHourTestCase):
    """ESS escrow transfers are shared back onto the bounty ticks they paid for."""

    def setUp(self):
        super().setUp()
        self.connect_wallet()

    def isk(self, session):
        payouts.match_sessions(self.character)
        session.refresh_from_db()
        return session.payout_isk

    def test_transfer_after_the_session_counts_for_it(self):
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 6 * M)
        self.pay(2, session.session_started + timedelta(minutes=40), 6 * M)
        # 40% of each tick went to the ESS bank; it pays out well after the session.
        self.pay(3, session.session_ended + timedelta(minutes=50), 8 * M, "ess_escrow_transfer")
        self.assertEqual(self.isk(session), 20 * M)

    def test_one_transfer_is_split_over_two_sessions_by_size(self):
        first = self.session(timedelta(hours=6), 30)
        second = self.session(timedelta(hours=5), 30)
        self.pay(1, first.session_started + timedelta(minutes=20), 10 * M)
        self.pay(2, second.session_started + timedelta(minutes=20), 30 * M)
        self.pay(3, second.session_ended + timedelta(minutes=5), 20 * M, "ess_escrow_transfer")  # in second's window
        self.assertEqual(self.isk(first), 15 * M)   # 10M + 5M
        self.assertEqual(self.isk(second), 45 * M)  # 30M + 15M; the transfer's arrival time doesn't matter

    def test_a_transfer_only_covers_ticks_since_the_previous_one(self):
        first = self.session(timedelta(hours=6), 30)
        second = self.session(timedelta(hours=5), 30)
        self.pay(1, first.session_started + timedelta(minutes=20), 9 * M)
        self.pay(2, first.session_ended + timedelta(minutes=15), 6 * M, "ess_escrow_transfer")
        self.pay(3, second.session_started + timedelta(minutes=20), 3 * M)
        self.pay(4, second.session_ended + timedelta(minutes=15), 2 * M, "ess_escrow_transfer")
        self.assertEqual(self.isk(first), 15 * M)
        self.assertEqual(self.isk(second), 5 * M)

    def test_ticks_too_long_before_the_transfer_get_none(self):
        # A day apart: the first tick's bank was robbed, or it was earned without an ESS.
        old = self.session(timedelta(hours=30), 30)
        new = self.session(timedelta(hours=5), 30)
        self.pay(1, old.session_started + timedelta(minutes=20), 10 * M)
        self.pay(2, new.session_started + timedelta(minutes=20), 10 * M)
        self.pay(3, new.session_ended + timedelta(minutes=30), 4 * M, "ess_escrow_transfer")
        self.assertEqual(self.isk(old), 10 * M)
        self.assertEqual(self.isk(new), 14 * M)

    def test_share_arrives_with_a_later_pull(self):
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 6 * M)
        self.assertEqual(self.isk(session), 6 * M)
        self.pay(2, session.session_ended + timedelta(minutes=40), 4 * M, "ess_escrow_transfer")
        self.assertEqual(self.isk(session), 10 * M)

    def test_missions_get_no_share_and_a_lone_transfer_counts_for_nothing(self):
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=10), 4 * M, "ess_escrow_transfer")  # nothing before it
        self.pay(2, session.session_started + timedelta(minutes=20), 5 * M, "agent_mission_reward")
        self.pay(3, session.session_started + timedelta(minutes=30), 2 * M, "ess_escrow_transfer")  # no ticks to share
        self.assertEqual(self.isk(session), 5 * M)

    def test_session_page_lists_the_share_and_matches_isk_per_hour(self):
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 12 * M)
        self.pay(2, session.session_ended + timedelta(minutes=60), 8 * M, "ess_escrow_transfer")  # outside the window
        self.isk(session)
        self.client.force_login(self.user)
        response = self.client.get(reverse("pilotstats:session", args=[session.pk]))
        linked = response.context["linked"]
        self.assertEqual(linked["payout_total"], session.payout_isk)
        self.assertEqual(dict(linked["payouts_by_type"]), {"bounty_prizes": 12 * M, "ess_share": 8 * M})
        self.assertContains(response, "ESS share")
        self.assertContains(response, "20.00M ISK/h")


class TriggerTests(IskPerHourTestCase):
    def test_wallet_pull_fills_sessions(self):
        token = self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        journal = [_entry(10, "bounty_prizes", 3_000_000.0, when=session.session_started + timedelta(minutes=20))]
        with mock.patch.object(tasks.Token, "get_token", return_value=token), \
                mock.patch.object(tasks.providers, "wallet_journal", return_value=journal), \
                mock.patch.object(tasks.resolve_type_names, "delay"):
            tasks.pull_wallet_journal(self.character.character_id)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 3 * M)

    def test_pull_with_nothing_new_still_marks_sessions_known(self):
        token = self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        with mock.patch.object(tasks.Token, "get_token", return_value=token), \
                mock.patch.object(tasks.providers, "wallet_journal", return_value=[]):
            tasks.pull_wallet_journal(self.character.character_id)
        session.refresh_from_db()
        self.assertEqual(session.payout_isk, 0)

    def test_upload_picks_up_payouts_already_stored(self):
        AuthUtils.add_permissions_to_user_by_name(["pilotstats.basic_access"], self.user)
        self.client.force_login(self.user)
        self.connect_wallet()
        start = self.now - timedelta(hours=3)
        end = start + timedelta(minutes=30)
        self.pay(1, start + timedelta(minutes=20), 6 * M)
        stamp = "%Y.%m.%d %H:%M:%S"
        log = (
            "------------------------------------------------------------\n"
            "  Gamelog\n"
            "  Listener: Example Ratter\n"
            f"  Session Started: {start:{stamp}}\n"
            "------------------------------------------------------------\n"
            f"[ {start:{stamp}} ] (combat) <color=0xff00ffff><b>1022</b> "
            "<color=0x77ffffff><font size=10>to</font> <b><color=0xffffffff>Example Rat</b>"
            "<font size=10><color=0x77ffffff> - Example Laser - Hits\n"
            f"[ {end:{stamp}} ] (combat) <color=0xff00ffff><b>900</b> "
            "<color=0x77ffffff><font size=10>to</font> <b><color=0xffffffff>Example Rat</b>"
            "<font size=10><color=0x77ffffff> - Example Laser - Hits\n"
        )
        with mock.patch.object(tasks.check_session_records, "delay"):
            self.client.post(reverse("pilotstats:upload"), {
                "log_file": SimpleUploadedFile("ratting.txt", log.encode("utf-8")),
            })
        session = CombatLogUpload.objects.get()
        self.assertEqual(session.payout_isk, 6 * M)
        self.assertEqual(session.isk_per_hour, 12 * M)


class LeaderboardTests(IskPerHourTestCase):
    def test_rate_is_weighted_by_hours_and_skips_unknown_sessions(self):
        self.connect_wallet()
        one = self.session(timedelta(hours=10), 60)     # 1 h, 10M
        two = self.session(timedelta(hours=5), 180)     # 3 h, 50M
        self.pay(1, one.session_started + timedelta(minutes=20), 10 * M)
        self.pay(2, two.session_started + timedelta(minutes=20), 50 * M)
        payouts.match_sessions(self.character)
        rows = leaderboard_rows("7d", "isk_per_hour")
        row = next(r for r in rows if r["label"] == "Example Ratter")
        self.assertEqual(row["isk_per_hour"], 15 * M)   # 60M over 4 h, not the mean of 10M and 16.7M

    def test_pilot_without_wallet_has_no_rate(self):
        self.session(timedelta(hours=5), 60)
        payouts.match_sessions(self.character)
        rows = leaderboard_rows("7d", "isk_per_hour")
        self.assertIsNone(rows[0]["isk_per_hour"])

    def test_sorting_by_rate(self):
        self.connect_wallet()
        slow = self.session(timedelta(hours=5), 60)
        self.pay(1, slow.session_started + timedelta(minutes=20), 10 * M)
        fast_user, fast = _pilot("fast", "Example Fast", 1000000703)
        _token(fast_user, fast, WALLET_SCOPE)
        quick = self.session(timedelta(hours=4), 60, character=fast, user=fast_user)
        self.pay(2, quick.session_started + timedelta(minutes=20), 40 * M, character=fast)
        payouts.match_sessions(self.character)
        payouts.match_sessions(fast)
        rows = leaderboard_rows("7d", "isk_per_hour")
        self.assertEqual([r["label"] for r in rows[:2]], ["Example Fast", "Example Ratter"])

    def test_page_shows_the_column(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 60)
        self.pay(1, session.session_started + timedelta(minutes=20), 20 * M)
        payouts.match_sessions(self.character)
        self.client.force_login(self.user)
        response = self.client.get(reverse("pilotstats:leaderboard") + "?range=7d&sort=isk_per_hour")
        self.assertContains(response, "ISK/h")
        self.assertContains(response, "20.00M ISK")


class SessionPageTests(IskPerHourTestCase):
    def setUp(self):
        super().setUp()
        self.client.force_login(self.user)

    def test_shows_rate(self):
        self.connect_wallet()
        session = self.session(timedelta(hours=5), 120)
        self.pay(1, session.session_started + timedelta(minutes=20), 30 * M)
        payouts.match_sessions(self.character)
        response = self.client.get(reverse("pilotstats:session", args=[session.pk]))
        self.assertContains(response, "ISK per hour")
        self.assertContains(response, "15.00M ISK/h")

    def test_explains_missing_wallet(self):
        session = self.session(timedelta(hours=5), 60)
        response = self.client.get(reverse("pilotstats:session", args=[session.pk]))
        self.assertContains(response, "wallet connected")
