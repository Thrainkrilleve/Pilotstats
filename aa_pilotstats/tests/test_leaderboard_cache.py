"""
Leaderboard caching: pages reuse a computed board until new data arrives,
and every write path that changes the numbers clears it.
"""

# Standard Library
from datetime import timedelta
from decimal import Decimal
from unittest import mock

# Django
from django.core.cache import cache
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .. import leaderboard, payouts
from ..app_settings import WALLET_SCOPE
from ..leaderboard import cached_leaderboard_rows
from ..models import CombatLogUpload, Killmail, KillmailParticipant, PilotSettings
from .test_sources import _entry, _pilot, _token

M = Decimal(1_000_000)
LOCMEM = {"default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache", "LOCATION": "pilotstats-lb"}}


@override_settings(CACHES=LOCMEM)
class LeaderboardCacheTestCase(TestCase):
    def setUp(self):
        cache.clear()
        patcher = mock.patch.object(leaderboard, "LEADERBOARD_CACHE_SECONDS", 300)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.now = timezone.now().replace(microsecond=0)
        self.user, self.character = _pilot("pilot", "Example Pilot", 1000002001)
        self.n = 0

    def session(self, damage, hours_ago=5, minutes=60):
        self.n += 1
        start = self.now - timedelta(hours=hours_ago)
        with self.captureOnCommitCallbacks(execute=True):
            return CombatLogUpload.objects.create(
                character=self.character, uploaded_by=self.user, source_filename=f"{self.n}.txt",
                file_hash=f"lb-cache-{self.n}", session_started=start,
                session_ended=start + timedelta(minutes=minutes), damage_out=damage,
            )

    def damage(self, **kwargs):
        rows = cached_leaderboard_rows("7d", "damage_out", **kwargs)
        return rows[0]["damage_out"] if rows else None


class CachingTests(LeaderboardCacheTestCase):
    def test_second_load_needs_no_queries(self):
        self.session(1000)
        self.assertEqual(self.damage(), 1000)
        with self.assertNumQueries(0):
            self.assertEqual(self.damage(), 1000)

    def test_stale_without_a_signal_until_cleared(self):
        session = self.session(1000)
        self.damage()
        CombatLogUpload.objects.filter(pk=session.pk).update(damage_out=5)  # no signal
        self.assertEqual(self.damage(), 1000)
        with self.captureOnCommitCallbacks(execute=True):
            leaderboard.invalidate_cache()
        self.assertEqual(self.damage(), 5)

    def test_cleared_only_after_commit(self):
        self.session(1000)
        self.damage()
        with self.captureOnCommitCallbacks(execute=False) as callbacks:
            leaderboard.invalidate_cache()
        version = cache.get(leaderboard.CACHE_VERSION_KEY)
        self.assertEqual(len(callbacks), 1)
        callbacks[0]()
        self.assertNotEqual(cache.get(leaderboard.CACHE_VERSION_KEY), version)

    def test_each_range_sort_and_activity_is_its_own_entry(self):
        self.session(1000)
        self.session(50, hours_ago=24 * 20)  # in 30 days, not in 7
        self.assertEqual(cached_leaderboard_rows("7d", "damage_out")[0]["damage_out"], 1000)
        self.assertEqual(cached_leaderboard_rows("30d", "damage_out")[0]["damage_out"], 1050)
        self.assertEqual(self.damage(activity=CombatLogUpload.Activity.RATTING), None)

    def test_odd_arguments_share_one_entry(self):
        self.session(1000)
        cached_leaderboard_rows("forever", "nonsense", activity="bogus")
        with self.assertNumQueries(0):
            cached_leaderboard_rows("all", "damage_out")
        boards = [key for key in cache._cache if "pilotstats:leaderboard:" in key and "version" not in key]
        self.assertEqual(len(boards), 1)

    def test_zero_seconds_turns_it_off(self):
        self.session(1000)
        cache.clear()
        with mock.patch.object(leaderboard, "LEADERBOARD_CACHE_SECONDS", 0):
            self.damage()
            CombatLogUpload.objects.update(damage_out=7)
            self.assertEqual(self.damage(), 7)
        self.assertEqual([key for key in cache._cache if "version" not in key], [])


class InvalidationTests(LeaderboardCacheTestCase):
    def test_new_and_deleted_sessions(self):
        session = self.session(1000)
        self.damage()
        self.session(500)
        self.assertEqual(self.damage(), 1500)
        with self.captureOnCommitCallbacks(execute=True):
            session.delete()
        self.assertEqual(self.damage(), 500)

    def test_wallet_pull_and_session_matching(self):
        _token(self.user, self.character, WALLET_SCOPE)
        session = self.session(1000)
        self.assertIsNone(cached_leaderboard_rows("7d", "payout_isk")[0]["isk_per_hour"])
        entry = _entry(1, "bounty_prizes", 10_000_000.0, when=session.session_started + timedelta(minutes=20))
        with self.captureOnCommitCallbacks(execute=True):
            payouts.store_journal(self.character, [entry])
            payouts.match_sessions(self.character)
        row = cached_leaderboard_rows("7d", "payout_isk")[0]
        self.assertEqual(row["payout_isk"], 10 * M)
        self.assertEqual(row["isk_per_hour"], 10 * M)

    def test_second_pull_adding_to_a_day_total(self):
        # The first line creates the day's total (a signal); the second only
        # increments it with F(), which sends none.
        when = self.now - timedelta(hours=2)
        with self.captureOnCommitCallbacks(execute=True):
            payouts.store_journal(self.character, [_entry(1, "bounty_prizes", 1_000_000.0, when=when)])
        self.assertEqual(cached_leaderboard_rows("7d", "payout_isk")[0]["payout_isk"], M)
        with self.captureOnCommitCallbacks(execute=True):
            payouts.store_journal(self.character, [_entry(2, "bounty_prizes", 2_000_000.0, when=when)])
        self.assertEqual(cached_leaderboard_rows("7d", "payout_isk")[0]["payout_isk"], 3 * M)

    def test_wallet_opt_out(self):
        with self.captureOnCommitCallbacks(execute=True):
            payouts.store_journal(self.character, [_entry(1, "bounty_prizes", 1_000_000.0, when=self.now)])
        self.assertEqual(cached_leaderboard_rows("7d", "payout_isk")[0]["payout_isk"], M)
        with self.captureOnCommitCallbacks(execute=True):
            PilotSettings.objects.create(user=self.user, wallet_opt_out=True)
            payouts.delete_wallet_data(self.user)
        self.assertEqual(cached_leaderboard_rows("7d", "payout_isk"), [])

    def test_killmails(self):
        self.session(1000)
        self.assertEqual(cached_leaderboard_rows("7d", "kills")[0]["kills"], 0)
        with self.captureOnCommitCallbacks(execute=True):
            mail = Killmail.objects.create(killmail_id=1, killmail_hash="h", killmail_time=self.now,
                                           total_value=Decimal("1000"))
            KillmailParticipant.objects.create(killmail=mail, character=self.character, damage=10)
        self.assertEqual(cached_leaderboard_rows("7d", "kills")[0]["kills"], 1)

    def test_rolled_back_write_clears_nothing(self):
        self.session(1000)
        self.damage()
        version = cache.get(leaderboard.CACHE_VERSION_KEY)
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                from django.db import transaction

                with transaction.atomic():
                    CombatLogUpload.objects.create(
                        character=self.character, uploaded_by=self.user, source_filename="x.txt",
                        file_hash="lb-cache-x", damage_out=1,
                    )
                    raise RuntimeError
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.assertEqual(cache.get(leaderboard.CACHE_VERSION_KEY), version)


class PageTests(LeaderboardCacheTestCase):
    def test_leaderboard_page_shows_a_new_session(self):
        self.client.force_login(self.user)
        url = reverse("pilotstats:leaderboard") + "?range=7d"
        self.session(1000)
        self.assertEqual(self.client.get(url).context["rows"][0]["damage_out"], 1000)
        self.session(234)
        self.assertEqual(self.client.get(url).context["rows"][0]["damage_out"], 1234)
