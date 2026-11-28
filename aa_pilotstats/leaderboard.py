"""
Shared fleet leaderboard roll-up.

Web views, the dashboard widget, and a later Discord digest all group the
same way: everything a user's characters did folds up to their designated
main character. Gamelog sessions give damage and reps; wallet payouts, rat
counts, and killmails are merged into the same buckets as separate
columns. Damage, ISK, and kill value are never added together.
"""

# Standard Library
import uuid
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from .app_settings import LEADERBOARD_CACHE_SECONDS

SUM_FIELDS = (
    "damage_out",
    "damage_in",
    "reps_received_total",
    "reps_given_total",
)

# Columns from the other sources (see merge_sources). isk_per_hour is
# filled by rollup_sessions from each session's matched payouts.
SOURCE_FIELDS = (
    "payout_isk",
    "isk_per_hour",
    "rats_killed",
    "kills",
    "losses",
    "isk_destroyed",
    "isk_lost",
    "km_damage",
)

SORT_FIELDS = SUM_FIELDS + SOURCE_FIELDS

# "all" is the absence of a cutoff, not a day count.
RANGE_DAYS = {"7d": 7, "30d": 30}

# Per-session JSON breakdowns. Roll-ups never read them, so list queries
# defer them rather than pull every session's feed and charts into memory.
DETAIL_FIELDS = (
    "quality_out",
    "quality_in",
    "top_targets",
    "top_attackers",
    "reps_by_healer",
    "reps_by_recipient",
    "feed",
    "timeline",
)


def within_days(qs, days, now=None):
    """
    Keep sessions that were *flown* in the last ``days`` days.

    The fight time is the log's first combat line (``session_started``).
    Rows without one fall back to the upload time, so an old log uploaded
    today doesn't count as this week's work.
    """
    # Django
    from django.db.models.functions import Coalesce
    from django.utils import timezone

    if now is None:
        now = timezone.now()
    return qs.annotate(
        flown_at=Coalesce("session_started", "uploaded_at"),
    ).filter(flown_at__gte=now - timedelta(days=days))


def user_main(user):
    """Designated main character, or None when the user has no profile or no main."""
    profile = getattr(user, "profile", None)
    return getattr(profile, "main_character", None)


def normalize_sort_key(sort_key, sum_fields=SUM_FIELDS):
    """Fall back to the first sum field when the requested sort is unknown."""
    if sort_key in sum_fields:
        return sort_key
    return sum_fields[0]


def rollup_sessions(sessions, *, sort_key="damage_out", sum_fields=SUM_FIELDS):
    """
    Bucket session-like rows by the uploader's main character.

    Each item needs ``uploaded_by`` (user), ``character`` (with
    ``character_name``), and the numeric attributes named in ``sum_fields``.
    ``uploaded_by_id`` is used when present so a user with no main stays in
    their own bucket.

    Returns a list of dicts, highest ``sort_key`` first::

        {
            "key": <character_id or "user:<id>">,
            "label": str,
            "main": character or None,
            "user_id": int,
            "upload_count": int,
            "<sum field>": int,
            "isk_per_hour": Decimal or None,
            "by_character": [(name, {field: int, ...}), ...],
        }

    ``isk_per_hour`` is weighted by time: payout ISK earned in the bucket's
    sessions divided by their total hours. Only sessions with wallet data
    and at least ``ISK_RATE_MIN_SECONDS`` long count, so pilots without a
    connected wallet get None rather than 0.
    """
    from .models import ISK_RATE_MIN_SECONDS

    fields = tuple(sum_fields)
    sort_key = normalize_sort_key(sort_key, fields)
    buckets: dict = {}

    for row in sessions:
        user = row.uploaded_by
        main = user_main(user)
        user_id = getattr(row, "uploaded_by_id", None)
        if user_id is None:
            user_id = user.pk
        key = main.character_id if main else f"user:{user_id}"
        label = main.character_name if main else user.username

        bucket = buckets.get(key)
        if bucket is None:
            bucket = {
                "key": key,
                "label": label,
                "main": main,
                "user_id": user_id,
                "upload_count": 0,
                "by_character": {},
                "isk_earned": Decimal(0),
                "isk_seconds": 0.0,
            }
            for field in fields:
                bucket[field] = 0
            buckets[key] = bucket

        bucket["upload_count"] += 1
        earned = getattr(row, "payout_isk", None)
        seconds = _session_seconds(row)
        if earned is not None and seconds >= ISK_RATE_MIN_SECONDS:
            bucket["isk_earned"] += earned
            bucket["isk_seconds"] += seconds
        for field in fields:
            bucket[field] += getattr(row, field, 0) or 0

        char_name = row.character.character_name
        char_bucket = bucket["by_character"].get(char_name)
        if char_bucket is None:
            char_bucket = {field: 0 for field in fields}
            bucket["by_character"][char_name] = char_bucket
        for field in fields:
            char_bucket[field] += getattr(row, field, 0) or 0

    rows = list(buckets.values())
    for row in rows:
        row["isk_per_hour"] = _rate(row["isk_earned"], row["isk_seconds"])
    rows.sort(key=lambda item: item[sort_key], reverse=True)
    for row in rows:
        row["by_character"] = sorted(
            row["by_character"].items(),
            key=lambda kv: kv[1][sort_key],
            reverse=True,
        )
    return rows


def _rate(earned, seconds):
    """ISK per hour from earned ISK and seconds, or None without any time."""
    return earned * 3600 / Decimal(str(seconds)) if seconds else None


def _session_seconds(row) -> float:
    started = getattr(row, "session_started", None)
    ended = getattr(row, "session_ended", None)
    if not started or not ended:
        return 0.0
    return max((ended - started).total_seconds(), 0.0)


def rank_of(rows, user):
    """1-based rank of ``user``'s bucket, or None when they have no row."""
    main = user_main(user)
    key = main.character_id if main else f"user:{user.pk}"
    for index, row in enumerate(rows, start=1):
        if row["key"] == key:
            return index
    return None


def leaderboard_rows(
    range_key="30d", sort_key="damage_out", *, sum_fields=SUM_FIELDS, now=None, sources=True,
    activity=None,
):
    """
    Roll up stored combat-log sessions, then (``sources=True``) merge in
    payouts, rat counts, and killmails per main.

    ``range_key`` is ``"7d"``, ``"30d"``, or anything else (treated as all time,
    matching the leaderboard's existing query-string behavior). The window is
    on when things happened, not when they were uploaded or fetched (see
    ``within_days``).

    ``activity`` (a ``CombatLogUpload.Activity`` value) keeps only sessions of
    that kind. Wallet and killmail totals can't be split by activity, so they
    aren't merged in then; ISK per hour still is, since it comes from the
    sessions themselves.
    """
    # Django
    from django.utils import timezone

    from .models import CombatLogUpload

    if now is None:
        now = timezone.now()
    qs = CombatLogUpload.objects.select_related(
        "character",
        "uploaded_by",
        "uploaded_by__profile",
        "uploaded_by__profile__main_character",
    ).defer(*DETAIL_FIELDS)
    days = RANGE_DAYS.get(range_key)
    if days is not None:
        qs = within_days(qs, days, now)
    if activity:
        qs = qs.filter(activity=activity)
        sources = False
    session_sort = sort_key if sort_key in sum_fields else sum_fields[0]
    rows = rollup_sessions(qs, sort_key=session_sort, sum_fields=sum_fields)
    if sources:
        rows = merge_sources(rows, days=days, now=now)
    if sources or sort_key == "isk_per_hour":
        sort_key = normalize_sort_key(sort_key, tuple(sum_fields) + SOURCE_FIELDS)
        rows.sort(key=lambda row: row.get(sort_key) or 0, reverse=True)
    return rows


# ---- Cache ------------------------------------------------------------------------

# Every cached leaderboard's key includes this version. Bumping it makes all
# of them stale at once; the old entries expire on their own.
CACHE_VERSION_KEY = "pilotstats:leaderboard:version"


def _cache_key(version, range_key, sort_key, activity) -> str:
    return f"pilotstats:leaderboard:{version}:{range_key}:{sort_key}:{activity or '-'}"


def cached_leaderboard_rows(range_key="30d", sort_key="damage_out", *, activity=None):
    """``leaderboard_rows`` through Django's cache, for the web pages.

    Kept for ``PILOTSTATS_LEADERBOARD_CACHE_SECONDS`` and cleared by
    ``invalidate_cache`` whenever sessions, payouts, rat counts, killmails,
    or who owns which character change. Arguments are normalized first, so
    odd query strings can't fill the cache with copies of the same board.
    """
    from .models import CombatLogUpload

    if range_key not in RANGE_DAYS:
        range_key = "all"
    sort_key = normalize_sort_key(sort_key, SORT_FIELDS)
    if activity not in CombatLogUpload.Activity.values:
        activity = None
    if LEADERBOARD_CACHE_SECONDS <= 0:
        return leaderboard_rows(range_key, sort_key, activity=activity)

    # Django
    from django.core.cache import cache

    version = cache.get(CACHE_VERSION_KEY)
    if version is None:
        cache.add(CACHE_VERSION_KEY, uuid.uuid4().hex, timeout=None)
        version = cache.get(CACHE_VERSION_KEY)
    key = _cache_key(version, range_key, sort_key, activity)
    rows = cache.get(key)
    if rows is None:
        rows = leaderboard_rows(range_key, sort_key, activity=activity)
        cache.set(key, rows, timeout=LEADERBOARD_CACHE_SECONDS)
    return rows


def invalidate_cache() -> None:
    """Make every cached leaderboard stale once the current transaction commits.

    Waiting for the commit means a page that loads in between can't cache
    the old numbers under the new version. A rolled-back write changes
    nothing, so it clears nothing.
    """
    if LEADERBOARD_CACHE_SECONDS <= 0:
        return
    # Django
    from django.db import transaction

    transaction.on_commit(_bump_cache_version)


def _bump_cache_version() -> None:
    # Django
    from django.core.cache import cache

    cache.set(CACHE_VERSION_KEY, uuid.uuid4().hex, timeout=None)


def character_buckets(character_pks) -> dict:
    """``{EveCharacter pk: (key, label, main, user_id)}`` using the same keys as rollup_sessions.

    A character nobody owns on Auth gets its own ``char:<pk>`` bucket.
    """
    # Alliance Auth
    from allianceauth.authentication.models import CharacterOwnership
    from allianceauth.eveonline.models import EveCharacter

    result = {}
    owned = CharacterOwnership.objects.filter(character_id__in=character_pks).select_related(
        "user", "user__profile", "user__profile__main_character",
    )
    for ownership in owned:
        user = ownership.user
        main = user_main(user)
        key = main.character_id if main else f"user:{user.pk}"
        label = main.character_name if main else user.username
        result[ownership.character_id] = (key, label, main, user.pk)
    for character in EveCharacter.objects.filter(pk__in=set(character_pks) - set(result)):
        result[character.pk] = (f"char:{character.pk}", character.character_name, None, None)
    return result


def merge_sources(rows, *, days=None, now=None):
    """Add payout ISK, rat counts, and killmail columns to *rows* (and new rows as needed).

    Each killmail counts once per bucket, so two alts of one main on the same
    kill are one kill and one kill's value. Killmail damage is summed per
    character (it's that character's own damage on the mail).
    """
    # Django
    from django.db.models import Sum
    from django.utils import timezone

    from esi.models import Token

    from .app_settings import WALLET_SCOPE
    from .models import KillmailParticipant, NpcKillDaily, PayoutDaily, PilotSettings

    if now is None:
        now = timezone.now()
    since = now - timedelta(days=days) if days else None

    payout_qs = PayoutDaily.objects.all()
    npc_qs = NpcKillDaily.objects.all()
    km_qs = KillmailParticipant.objects.all()
    if since is not None:
        payout_qs = payout_qs.filter(day__gte=since.date())
        npc_qs = npc_qs.filter(day__gte=since.date())
        km_qs = km_qs.filter(killmail__killmail_time__gte=since)

    payouts = list(payout_qs.values("character_id", "entry_type").annotate(total=Sum("amount")))
    rats = list(npc_qs.values("character_id").annotate(total=Sum("count")))
    entries = list(km_qs.values(
        "character_id", "is_victim", "killmail_id", "killmail__total_value", "damage",
    ))

    character_pks = (
        {p["character_id"] for p in payouts}
        | {r["character_id"] for r in rats}
        | {e["character_id"] for e in entries}
    )
    buckets_for = character_buckets(character_pks)
    by_key = {row["key"]: row for row in rows}

    def bucket(character_pk):
        key, label, main, user_id = buckets_for[character_pk]
        row = by_key.get(key)
        if row is None:
            row = {
                "key": key, "label": label, "main": main, "user_id": user_id,
                "upload_count": 0, "by_character": [],
                "isk_earned": Decimal(0), "isk_seconds": 0.0, "isk_per_hour": None,
                **{field: 0 for field in SUM_FIELDS},
            }
            by_key[key] = row
            rows.append(row)
        return row

    for row in rows:
        row.update({
            "payout_isk": Decimal(0), "payout_by_type": defaultdict(Decimal),
            "rats_killed": 0, "km_damage": 0,
            "_kills": {}, "_losses": {},
        })

    for item in payouts:
        row = bucket(item["character_id"])
        _ensure_source_fields(row)
        row["payout_isk"] += item["total"] or 0
        row["payout_by_type"][item["entry_type"]] += item["total"] or 0
    for item in rats:
        row = bucket(item["character_id"])
        _ensure_source_fields(row)
        row["rats_killed"] += item["total"] or 0
    for item in entries:
        row = bucket(item["character_id"])
        _ensure_source_fields(row)
        value = item["killmail__total_value"] or Decimal(0)
        if item["is_victim"]:
            row["_losses"][item["killmail_id"]] = value
        else:
            row["_kills"][item["killmail_id"]] = value
            row["km_damage"] += item["damage"] or 0

    opted_out = set(PilotSettings.objects.filter(wallet_opt_out=True).values_list("user_id", flat=True))
    wallet_users = set(
        Token.objects.filter(scopes__name=WALLET_SCOPE).values_list("user_id", flat=True)
    ) - opted_out
    for row in rows:
        _ensure_source_fields(row)
        kills, losses = row.pop("_kills"), row.pop("_losses")
        # Kept so group_rows can count a kill once per corporation too.
        row["kill_values"], row["loss_values"] = kills, losses
        row["kills"], row["isk_destroyed"] = len(kills), sum(kills.values(), Decimal(0))
        row["losses"], row["isk_lost"] = len(losses), sum(losses.values(), Decimal(0))
        row["payout_by_type"] = dict(row["payout_by_type"])
        row["wallet_connected"] = row.get("user_id") in wallet_users
    return rows


def _ensure_source_fields(row):
    if "_kills" not in row and "kills" not in row:
        row.update({
            "payout_isk": Decimal(0), "payout_by_type": defaultdict(Decimal),
            "rats_killed": 0, "km_damage": 0, "_kills": {}, "_losses": {},
        })


# ---- Corporation and alliance totals ------------------------------------------------

GROUP_SUM_FIELDS = SUM_FIELDS + ("upload_count", "payout_isk", "rats_killed", "km_damage")


def group_rows(rows, by="corporation", sort_key="damage_out"):
    """Fold pilot rows (from ``leaderboard_rows``) into one row per corporation or alliance.

    A pilot counts under their main's corporation (or alliance). Pilots with
    no main, and characters nobody owns, go into one "unknown" group.
    Totals are sums; ISK per hour is time-weighted again (all earned ISK over
    all logged hours); a killmail several members are on counts once.
    """
    groups: dict = {}
    for row in rows:
        main = row.get("main")
        if by == "alliance":
            key = getattr(main, "alliance_id", None) or None
            label = getattr(main, "alliance_name", "") if key else ""
        else:
            key = getattr(main, "corporation_id", None) or None
            label = getattr(main, "corporation_name", "") if key else ""
        group = groups.get(key)
        if group is None:
            group = {
                "key": key,
                "label": label,
                "known": key is not None,
                "alliance": getattr(main, "alliance_name", "") if by == "corporation" and key else "",
                "pilots": [],
                "isk_earned": Decimal(0),
                "isk_seconds": 0.0,
                "kill_values": {},
                "loss_values": {},
                "wallet_connected": False,
                **{field: 0 for field in GROUP_SUM_FIELDS},
            }
            groups[key] = group
        group["pilots"].append(row)
        for field in GROUP_SUM_FIELDS:
            group[field] += row.get(field) or 0
        group["isk_earned"] += row.get("isk_earned") or 0
        group["isk_seconds"] += row.get("isk_seconds") or 0
        group["kill_values"].update(row.get("kill_values") or {})
        group["loss_values"].update(row.get("loss_values") or {})
        group["wallet_connected"] = group["wallet_connected"] or bool(row.get("wallet_connected"))

    result = list(groups.values())
    for group in result:
        group["pilot_count"] = len(group["pilots"])
        group["isk_per_hour"] = _rate(group["isk_earned"], group["isk_seconds"])
        kills, losses = group["kill_values"], group["loss_values"]
        group["kills"], group["isk_destroyed"] = len(kills), sum(kills.values(), Decimal(0))
        group["losses"], group["isk_lost"] = len(losses), sum(losses.values(), Decimal(0))
    sort_key = normalize_sort_key(sort_key, SORT_FIELDS)
    result.sort(key=lambda group: (group["known"], group.get(sort_key) or 0), reverse=True)
    for group in result:
        group["pilots"].sort(key=lambda row: row.get(sort_key) or 0, reverse=True)
    return result
