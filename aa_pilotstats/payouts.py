"""
Wallet-journal payouts: what is kept, and how.

Only payout lines are stored: bounty, ESS and mission rewards (the
``Payout.EntryType`` values). Every ``*_tax`` line, every other journal
type, and every field besides id / date / type / amount is dropped. For
``bounty_prizes`` lines the NPC kill counts are derived from the line's
``reason`` (a ``typeID: count`` list); the raw text is not stored.

``match_sessions`` turns the stored lines into each Gamelog session's
``payout_isk`` (bounties and mission rewards earned while it ran), which
gives ISK per hour.
"""

# Standard Library
import bisect
import logging
from collections import defaultdict
from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal

# Django
from django.db import transaction
from django.db.models import F
from django.utils import timezone

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from .leaderboard import invalidate_cache

from esi.models import Token

from .app_settings import PAYOUT_RETENTION_DAYS, PAYOUT_TAIL_MINUTES, WALLET_SCOPE
from .models import CombatLogUpload, NpcKillDaily, Payout, PayoutDaily, PilotSettings

logger = logging.getLogger(__name__)

PAYOUT_TYPES = frozenset(choice.value for choice in Payout.EntryType)
NPC_KILL_TYPES = frozenset({Payout.EntryType.BOUNTY_PRIZES.value})
# Payouts that count toward a session's ISK per hour. ESS escrow is left out:
# it's paid in one lump hours after the bounties it came from, so its time
# says nothing about which session earned it.
EARNED_TYPES = PAYOUT_TYPES - {Payout.EntryType.ESS_ESCROW_TRANSFER.value}


def is_payout(ref_type: str) -> bool:
    return bool(ref_type) and ref_type in PAYOUT_TYPES and not ref_type.endswith("_tax")


def parse_npc_kills(reason) -> dict:
    """``"23707: 3,23708: 1"`` → ``{23707: 3, 23708: 1}``. Malformed parts are skipped."""
    kills: dict = defaultdict(int)
    for part in str(reason or "").split(","):
        type_id, sep, count = part.partition(":")
        if not sep:
            continue
        try:
            type_id, count = int(type_id.strip()), int(count.strip())
        except ValueError:
            continue
        if type_id > 0 and count > 0:
            kills[type_id] += count
    return dict(kills)


def _field(entry, name):
    if isinstance(entry, dict):
        return entry.get(name)
    return getattr(entry, name, None)


def store_journal(character, entries) -> int:
    """Store the payout lines among *entries* for *character*. Returns how many were new.

    Daily totals and NPC counts are added only for journal ids not stored
    before, in the same transaction, so a re-pull never double-counts.
    """
    wanted = {}
    for entry in entries:
        ref_type = _field(entry, "ref_type")
        if not is_payout(ref_type):
            continue
        ref_id = _field(entry, "id")
        occurred_at = _field(entry, "date")
        amount = _field(entry, "amount")
        if ref_id is None or occurred_at is None or amount is None:
            continue
        if occurred_at.tzinfo is None:
            occurred_at = occurred_at.replace(tzinfo=dt_timezone.utc)
        wanted[int(ref_id)] = Payout(
            character=character,
            occurred_at=occurred_at,
            entry_type=ref_type,
            amount=Decimal(str(amount)),
            ref_id=int(ref_id),
            npc_kills=(
                {str(k): v for k, v in parse_npc_kills(_field(entry, "reason")).items()}
                if ref_type in NPC_KILL_TYPES else {}
            ),
        )
    if not wanted:
        return 0

    with transaction.atomic():
        known = set(
            Payout.objects.filter(ref_id__in=wanted).values_list("ref_id", flat=True)
        )
        new_rows = [row for ref_id, row in wanted.items() if ref_id not in known]
        if not new_rows:
            return 0
        Payout.objects.bulk_create(new_rows)

        day_totals: dict = defaultdict(Decimal)
        npc_totals: dict = defaultdict(int)
        for row in new_rows:
            day = row.occurred_at.astimezone(dt_timezone.utc).date()
            day_totals[(day, row.entry_type)] += row.amount
            for type_id, count in row.npc_kills.items():
                npc_totals[(day, int(type_id))] += count

        for (day, entry_type), amount in day_totals.items():
            obj, created = PayoutDaily.objects.get_or_create(
                character=character, day=day, entry_type=entry_type,
                defaults={"amount": amount},
            )
            if not created:
                PayoutDaily.objects.filter(pk=obj.pk).update(amount=F("amount") + amount)
        for (day, type_id), count in npc_totals.items():
            obj, created = NpcKillDaily.objects.get_or_create(
                character=character, day=day, npc_type_id=type_id,
                defaults={"count": count},
            )
            if not created:
                NpcKillDaily.objects.filter(pk=obj.pk).update(count=F("count") + count)
        invalidate_cache()  # the F() updates above don't send signals
    return len(new_rows)


def wallet_opted_out_user_ids() -> set:
    return set(
        PilotSettings.objects.filter(wallet_opt_out=True).values_list("user_id", flat=True)
    )


def delete_wallet_data(user) -> None:
    """Remove every stored payout, daily total, NPC count, and session ISK for *user*'s characters."""
    character_ids = CharacterOwnership.objects.filter(user=user).values_list("character_id", flat=True)
    Payout.objects.filter(character_id__in=character_ids).delete()
    PayoutDaily.objects.filter(character_id__in=character_ids).delete()
    NpcKillDaily.objects.filter(character_id__in=character_ids).delete()
    CombatLogUpload.objects.filter(character_id__in=character_ids).update(payout_isk=None, activity="")
    invalidate_cache()
    # Activities that came from the wallet go; what the NPC names say stays.
    classify_unmatched(
        CombatLogUpload.objects.filter(character_id__in=character_ids).only("pk", "activity", "top_targets")
    )


# ---- ISK per hour ----------------------------------------------------------------


def wallet_tracked(character) -> bool:
    """True when *character*'s owner granted the wallet scope for it and hasn't opted out."""
    ownership = CharacterOwnership.objects.filter(character=character).first()
    if ownership is None or ownership.user_id in wallet_opted_out_user_ids():
        return False
    return Token.objects.filter(
        character_id=character.character_id, user_id=ownership.user_id, scopes__name=WALLET_SCOPE,
    ).exists()


def match_sessions(character, now=None) -> int:
    """Recompute ``payout_isk`` and ``activity`` for *character*'s recent sessions. Returns how many changed.

    A bounty or mission payout belongs to a session when it lands between the
    session's first combat line and ``PAYOUT_TAIL_MINUTES`` after its last
    one (bounties are paid in ticks every 20 minutes). When two sessions'
    windows overlap, the payout goes to the one that started last, so it's
    never counted twice.

    Only sessions whose payouts are still itemized (newer than the retention
    window) are recomputed; older sessions keep the value they were given.
    Sessions get ``None`` when the character's wallet isn't connected, and
    a number (possibly 0) when it is. All of the character's recent sessions
    are recomputed together, so the overlap rule always sees every candidate.

    The payout types that land in each session's window also decide its
    activity (missions, ratting, ...; see ``activity.classify``).
    """
    from . import activity

    now = now or timezone.now()
    tail = timedelta(minutes=PAYOUT_TAIL_MINUTES)
    # A day of margin keeps sessions whose payouts are only partly purged out.
    oldest = now - timedelta(days=PAYOUT_RETENTION_DAYS) + timedelta(days=1)
    qs = CombatLogUpload.objects.filter(
        character=character,
        session_started__isnull=False,
        session_ended__isnull=False,
        session_started__gte=oldest,
    )
    sessions = list(qs.order_by("session_started", "pk").only(
        "pk", "session_started", "session_ended", "payout_isk", "activity", "top_targets",
    ))
    if not sessions:
        return 0

    if not wallet_tracked(character):
        totals = {session.pk: None for session in sessions}
        kinds = {session.pk: None for session in sessions}
    else:
        totals = {session.pk: Decimal(0) for session in sessions}
        kinds = {session.pk: set() for session in sessions}
        starts = [session.session_started for session in sessions]
        payouts_in_range = Payout.objects.filter(
            character=character,
            entry_type__in=EARNED_TYPES,
            occurred_at__gte=sessions[0].session_started,
            occurred_at__lte=max(session.session_ended for session in sessions) + tail,
        ).values_list("occurred_at", "amount", "entry_type")
        for occurred_at, amount, entry_type in payouts_in_range:
            # The session that started last at or before the payout.
            index = bisect.bisect_right(starts, occurred_at) - 1
            if index >= 0 and occurred_at <= sessions[index].session_ended + tail:
                totals[sessions[index].pk] += amount
                kinds[sessions[index].pk].add(entry_type)

    changed = []
    for session in sessions:
        value = totals[session.pk]
        if value is not None:
            value = value.quantize(Decimal("0.01"))
        kind = activity.classify(session.top_targets, kinds[session.pk])
        if session.payout_isk != value or session.activity != kind:
            session.payout_isk = value
            session.activity = kind
            changed.append(session)
    if changed:
        CombatLogUpload.objects.bulk_update(changed, ["payout_isk", "activity"])
        invalidate_cache()
    return len(changed)


def classify_unmatched(sessions) -> int:
    """Classify sessions ``match_sessions`` doesn't reach (older than payout retention).

    Their payouts are gone, so only the names rule can apply. Sessions that
    already have an activity keep it. Returns how many were set.
    """
    from . import activity

    changed = []
    for session in sessions:
        if session.activity:
            continue
        kind = activity.classify(session.top_targets, None)
        if kind:
            session.activity = kind
            changed.append(session)
    if changed:
        CombatLogUpload.objects.bulk_update(changed, ["activity"])
        invalidate_cache()
    return len(changed)
