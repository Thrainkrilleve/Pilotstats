"""
Pilot Stats Views

leaderboard      — fleet standings, rolled up to each user's main character
upload           — drag-and-drop (or picker) upload of a combat log; JSON for the scanner
upload_characters— the user's character names, for the folder scanner's local filter
                   (desktop.py serves the same two for AA Desktop, behind a bearer token)
session_detail   — the upload-result dashboard for one stored session
my_uploads       — a user's own upload history, with delete
delete_upload    — POST endpoint backing the delete button on my_uploads
events           — recent activity and killmails, plus the privacy and wallet cards
connect_wallet   — optional: grant the wallet scope for a character (django-esi SSO)
fleet_list / fleet_detail / track_fleet / stop_fleet — fleet-boss roster tracking
"""

# Standard Library
import csv
import hashlib
import logging
import time
from dataclasses import dataclass
from datetime import timedelta
from datetime import timezone as dt_timezone
from decimal import Decimal
from typing import Optional

# Django
from django.contrib import messages
from django.contrib.auth.decorators import login_required, permission_required
from django.core.cache import cache
from django.core.exceptions import PermissionDenied
from django.db import IntegrityError, transaction
from django.db.models import Count, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.template.loader import render_to_string
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

# Alliance Auth
from allianceauth.authentication.models import CharacterOwnership

from esi.decorators import token_required
from esi.exceptions import HTTPClientError
from esi.models import Token

from . import fleets, payouts, tasks, trends, typenames
from .app_settings import (
    FLEET_SCOPE,
    PAYOUT_TAIL_MINUTES,
    SCANNER_RECENT_DAYS,
    SCANNER_UPLOADS_PER_MINUTE,
    SESSION_LINK_MINUTES,
    WALLET_SCOPE,
)
from .forms import LogUploadForm, PrivacyForm
from .leaderboard import (
    DETAIL_FIELDS,
    SORT_FIELDS,
    SUM_FIELDS,
    cached_leaderboard_rows,
    group_rows,
    normalize_sort_key,
    rank_of,
    within_days,
)
from .models import (
    CombatLogUpload,
    KillmailParticipant,
    Payout,
    PayoutDaily,
    PilotSettings,
    TrackedFleet,
)
from .parsing import (
    ParseStats,
    iter_events_from_lines,
    parse_header_timestamp,
    read_header_from_lines,
    summarize,
)

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = 25 * 1024 * 1024  # a full multi-hour session log is a few MB at most

# Events page caps. The per-session feed is already capped at 60; this keeps
# one long log from filling the shared list.
EVENTS_SESSION_LIMIT = 25
EVENTS_PER_SESSION = 8
EVENTS_ROW_LIMIT = 60
EVENTS_KILL_LIMIT = 25


def _owned_characters(user) -> dict:
    """Map character_name -> EveCharacter for every character *user* owns."""
    return {
        co.character.character_name: co.character
        for co in user.character_ownerships.select_related("character")
    }


def _eve_time(dt):
    """Gamelog timestamps are EVE time, which is UTC — not the server's TIME_ZONE."""
    if dt is None:
        return None
    if timezone.is_naive(dt):
        return dt.replace(tzinfo=dt_timezone.utc)
    return dt


def _can_delete(user, log_upload) -> bool:
    return user.has_perm("pilotstats.basic_access") and log_upload.uploaded_by_id == user.id


# ---------------------------------------------------------------------------
# Leaderboard
# ---------------------------------------------------------------------------


_CSV_RISKY = ("=", "+", "-", "@", "\t", "\r")


def _csv_text(value) -> str:
    """Names as CSV text that a spreadsheet won't run as a formula."""
    text = str(value or "")
    return "'" + text if text.startswith(_CSV_RISKY) else text


def _leaderboard_csv(rows, groups, group, range_key, activity):
    """The leaderboard as shown (pilots, corporations or alliances), as CSV."""
    metrics = [
        ("upload_count", "Sessions"),
        ("damage_out", "Damage dealt"),
        ("damage_in", "Damage received"),
        ("reps_received_total", "Reps received"),
        ("reps_given_total", "Reps given"),
        ("isk_per_hour", "ISK per hour"),
    ]
    if not activity:
        metrics += [
            ("payout_isk", "Payout ISK"),
            ("rats_killed", "Rats killed"),
            ("kills", "Kills"),
            ("losses", "Losses"),
            ("isk_destroyed", "ISK destroyed"),
            ("isk_lost", "ISK lost"),
            ("km_damage", "Killmail damage"),
        ]

    def number(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.2f}"
        if isinstance(value, Decimal):
            return str(value.quantize(Decimal("0.01")))
        return str(value)

    name = "-".join(filter(None, ["pilotstats", group or "pilots", range_key, activity]))
    response = HttpResponse(content_type="text/csv; charset=utf-8")
    response["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    writer = csv.writer(response)
    if groups is not None:
        writer.writerow([group.capitalize(), "Pilots"] + [label for _key, label in metrics])
        for row in groups:
            label = row["label"] if row["known"] else "Unknown"
            writer.writerow(
                [_csv_text(label), row["pilot_count"]] + [number(row.get(key)) for key, _label in metrics]
            )
    else:
        writer.writerow(["Pilot", "Corporation", "Alliance"] + [label for _key, label in metrics])
        for row in rows:
            main = row.get("main")
            writer.writerow([
                _csv_text(row["label"]),
                _csv_text(getattr(main, "corporation_name", "")),
                _csv_text(getattr(main, "alliance_name", "")),
            ] + [number(row.get(key)) for key, _label in metrics])
    return response


def _sort_groups(activity):
    """Sort buttons. With an activity picked, only columns that come from the sessions."""
    gamelog = (_("Gamelog"), [
        ("damage_out", _("Damage Dealt")),
        ("damage_in", _("Damage Received")),
        ("reps_received_total", _("Reps Received")),
        ("reps_given_total", _("Reps Given")),
    ])
    if activity:
        return [gamelog, (_("Wallet"), [("isk_per_hour", _("ISK/h"))])]
    return [
        gamelog,
        (_("Wallet"), [
            ("payout_isk", _("Payout ISK")),
            ("isk_per_hour", _("ISK/h")),
            ("rats_killed", _("Rats Killed")),
        ]),
        (_("Killmails"), [
            ("kills", _("Kills")),
            ("isk_destroyed", _("ISK Destroyed")),
            ("losses", _("Losses")),
            ("km_damage", _("Killmail Damage")),
        ]),
    ]


@login_required
def leaderboard(request):
    range_key = request.GET.get("range", "30d")
    activity = request.GET.get("activity", "")
    if activity not in CombatLogUpload.Activity.values:
        activity = ""
    sort_key = normalize_sort_key(request.GET.get("sort", "damage_out"), SORT_FIELDS)
    if activity and sort_key not in SUM_FIELDS + ("isk_per_hour",):
        sort_key = "damage_out"  # wallet and killmail columns aren't split by activity
    group = request.GET.get("group", "")
    if group not in ("corporation", "alliance"):
        group = ""
    rows = cached_leaderboard_rows(range_key, sort_key, activity=activity or None)
    groups = group_rows(rows, by=group, sort_key=sort_key) if group else None

    if request.GET.get("format") == "csv":
        return _leaderboard_csv(rows, groups, group, range_key, activity)

    return render(request, "pilotstats/leaderboard.html", {
        "rows": rows,
        "groups": groups,
        "group": group,
        "group_choices": [
            ("", _("Pilots")), ("corporation", _("Corporations")), ("alliance", _("Alliances")),
        ],
        "range_key": range_key,
        "sort_key": sort_key,
        "activity": activity,
        "activities": [("", _("All activities"))] + [
            (value, label) for value, label in CombatLogUpload.Activity.choices
            if value != CombatLogUpload.Activity.OTHER
        ],
        "ranges": [("7d", _("Last 7 days")), ("30d", _("Last 30 days")), ("all", _("All time"))],
        "sort_groups": _sort_groups(activity),
        "payout_labels": dict(Payout.EntryType.choices),
    })


# ---------------------------------------------------------------------------
# Upload
# ---------------------------------------------------------------------------


@dataclass
class UploadResult:
    """Outcome of one Gamelog upload, shared by the HTML form and the scanner's JSON."""

    code: str
    level: int
    message: str
    session: Optional[CombatLogUpload] = None
    new_lines: int = 0

    def as_json(self) -> dict:
        data = {"result": self.code, "message": str(self.message), "new_lines": self.new_lines}
        if self.session is not None:
            data["session_id"] = self.session.pk
            data["session_url"] = reverse("pilotstats:session", kwargs={"pk": self.session.pk})
            data["character"] = self.session.character.character_name
        return data


def _wants_json(request) -> bool:
    return "application/json" in request.headers.get("Accept", "")


def _throttled(user) -> int:
    """Seconds to wait when *user* is over the scanner's per-minute upload cap, else 0."""
    limit = SCANNER_UPLOADS_PER_MINUTE
    if not limit:
        return 0
    now = int(time.time())
    key = f"pilotstats:scanner-uploads:{user.pk}:{now // 60}"
    cache.add(key, 0, timeout=120)
    try:
        count = cache.incr(key)
    except ValueError:  # evicted between add and incr
        cache.set(key, 1, timeout=120)
        count = 1
    return (60 - now % 60) if count > limit else 0


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def upload(request):
    """
    Upload one Gamelog file.

    The page's form posts here and gets the page back with a flash message.
    The folder scanner and multi-file drop post the same field with
    ``Accept: application/json`` and get an ``UploadResult`` as JSON.
    """
    if request.method == "POST" and _wants_json(request):
        return upload_json(request)

    result = None
    if request.method == "POST":
        form = LogUploadForm(request.POST, request.FILES)
        if form.is_valid():
            outcome = _upload_and_queue(request.user, form.cleaned_data["log_file"])
            messages.add_message(request, outcome.level, outcome.message)
            result = outcome.session if outcome.code in ("created", "updated") else None
    else:
        form = LogUploadForm()

    return render(request, "pilotstats/upload.html", {
        "form": form,
        "result": result,
        "scanner_recent_days": SCANNER_RECENT_DAYS,
    })


def upload_json(request) -> JsonResponse:
    """
    The JSON upload used by the folder scanner, multi-file drops, and AA
    Desktop (``desktop.py``). The caller has already checked who the user is
    and that they have ``pilotstats.basic_access``. All three share one
    per-user throttle.
    """
    wait = _throttled(request.user)
    if wait:
        response = JsonResponse(
            {"result": "throttled", "message": str(_("Too many uploads; slowing down.")),
             "retry_after": wait},
            status=429,
        )
        response["Retry-After"] = str(wait)
        return response
    form = LogUploadForm(request.POST, request.FILES)
    if not form.is_valid():
        return JsonResponse(
            {"result": "invalid", "message": str(_("No log file in the request.")),
             "errors": form.errors.get_json_data()},
            status=400,
        )
    return JsonResponse(_upload_and_queue(request.user, form.cleaned_data["log_file"]).as_json())


def _upload_and_queue(user, uploaded_file) -> UploadResult:
    outcome = _process_upload(user, uploaded_file)
    if outcome.code in ("created", "updated"):
        transaction.on_commit(
            lambda pk=outcome.session.pk: tasks.check_session_records.delay(pk)
        )
    return outcome


def own_character_names(user) -> list:
    return sorted(_owned_characters(user))


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def upload_characters(request):
    """Names of the user's own characters, so the scanner only sends their logs."""
    return JsonResponse({"characters": own_character_names(request.user)})


def _process_upload(user, uploaded_file) -> UploadResult:
    if uploaded_file.size > MAX_UPLOAD_BYTES:
        return UploadResult(
            "too_large", messages.ERROR,
            _("That file is larger than a real combat log should be — rejected."),
        )

    raw = uploaded_file.read()
    file_hash = hashlib.sha256(raw).hexdigest()

    duplicate = (
        CombatLogUpload.objects.filter(file_hash=file_hash)
        .only("pk", "uploaded_by_id", "timeline").first()
    )
    if duplicate is not None:
        if duplicate.uploaded_by_id == user.id and not duplicate.timeline:
            return _add_timeline(duplicate, raw)
        return UploadResult(
            "duplicate", messages.WARNING, _("This exact file has already been uploaded."),
        )

    text = raw.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)

    header = read_header_from_lines(lines)
    character_name = header.get("character")
    if not character_name:
        return UploadResult(
            "not_gamelog", messages.ERROR,
            _("Couldn't find a 'Listener:' line — is this really a Gamelog file?"),
        )

    owned = _owned_characters(user)
    character = owned.get(character_name)
    if character is None:
        return UploadResult(
            "not_owned", messages.ERROR,
            _(
                "This log belongs to '%(name)s', which isn't a character on your Alliance Auth "
                "account. Make sure that character has been added (and its ownership verified) "
                "before uploading its logs."
            ) % {"name": character_name},
        )

    stats = ParseStats()
    events = list(iter_events_from_lines(lines, stats))
    if stats.combat_lines == 0:
        return UploadResult(
            "no_combat", messages.WARNING,
            _("No (combat) channel lines found in this file — nothing to record."),
        )

    summary = summarize(events)
    log_started = _eve_time(parse_header_timestamp(header.get("session_started")))

    values = dict(
        uploaded_by=user,
        session_started=_eve_time(summary["session_started"]),
        session_ended=_eve_time(summary["session_ended"]),
        source_filename=uploaded_file.name,
        file_hash=file_hash,
        damage_out=summary["damage_out"],
        damage_in=summary["damage_in"],
        shots_out=summary["shots_out"],
        shots_in=summary["shots_in"],
        reps_received_total=summary["reps_received_total"],
        reps_given_total=summary["reps_given_total"],
        quality_out=summary["quality_out"],
        quality_in=summary["quality_in"],
        top_targets=summary["top_targets"],
        top_attackers=summary["top_attackers"],
        reps_by_healer=summary["reps_by_healer"],
        reps_by_recipient=summary["reps_by_recipient"],
        feed=summary["feed"],
        timeline=summary["timeline"],
        combat_lines=stats.combat_lines,
        matched_lines=stats.matched,
    )

    try:
        with transaction.atomic():
            existing = None
            if log_started is not None:
                existing = (
                    CombatLogUpload.objects.select_for_update()
                    .filter(character=character, log_started=log_started)
                    .first()
                )
            if existing is None:
                log_upload = CombatLogUpload.objects.create(
                    character=character, log_started=log_started, **values
                )
                _match_payouts(log_upload)
                return UploadResult(
                    "created", messages.SUCCESS,
                    _("Uploaded and parsed — %(n)d combat lines scored.") % {"n": stats.matched},
                    session=log_upload,
                    new_lines=stats.combat_lines,
                )
            return _update_existing_session(user, existing, values)
    except IntegrityError:
        # Two uploads of the same file (or the same log) raced each other; the
        # other one won. Same outcome as the duplicate checks above.
        return UploadResult(
            "duplicate", messages.WARNING, _("This log has already been uploaded."),
        )


def _add_timeline(session, raw: bytes) -> UploadResult:
    """The same file again, for a session stored before charts existed: add its chart."""
    lines = raw.decode("utf-8", errors="replace").splitlines(keepends=True)
    session.timeline = summarize(iter_events_from_lines(lines))["timeline"]
    session.save(update_fields=["timeline"])
    return UploadResult(
        "updated", messages.SUCCESS,
        _("This log was already uploaded; added the damage chart to its session."),
        session=CombatLogUpload.objects.get(pk=session.pk),
        new_lines=0,
    )


def _match_payouts(session) -> None:
    """Give a new or grown session its payout ISK and activity from what's already stored."""
    payouts.match_sessions(session.character)
    session.refresh_from_db(fields=["payout_isk", "activity"])
    if not session.activity:
        # Older than payout retention, so match_sessions skipped it: names only.
        payouts.classify_unmatched([session])


def _update_existing_session(user, existing, values) -> UploadResult:
    """
    A log the server already has a session for (same character and header
    start). The EVE client appends to one file for the whole client session,
    so a longer copy replaces the stored totals instead of being counted twice.
    """
    if existing.uploaded_by_id != user.id:
        return UploadResult(
            "other_account", messages.ERROR,
            _("Another Alliance Auth account already uploaded this character's log for that session."),
        )
    if values["combat_lines"] <= existing.combat_lines:
        return UploadResult(
            "no_new_lines", messages.WARNING,
            _("This session is already stored, and this copy of the log has no new combat lines."),
            session=existing,
        )

    added = values["combat_lines"] - existing.combat_lines
    for field, value in values.items():
        setattr(existing, field, value)
    existing.save()
    _match_payouts(existing)
    return UploadResult(
        "updated", messages.SUCCESS,
        _("Updated your existing session for this log — %(n)d new combat lines.") % {"n": added},
        session=existing,
        new_lines=added,
    )


# ---------------------------------------------------------------------------
# Session detail
# ---------------------------------------------------------------------------


@login_required
def session_detail(request, pk):
    """Full session dashboard. Any logged-in member can open it."""
    session = get_object_or_404(
        CombatLogUpload.objects.select_related("character", "uploaded_by"),
        pk=pk,
    )
    return render(request, "pilotstats/session_detail.html", {
        "session": session,
        "can_delete": _can_delete(request.user, session),
        "linked": _session_links(session),
    })


def _session_links(session) -> dict:
    """Payouts, rats, kills/losses, and hull for the session's character and time window.

    Shown next to the Gamelog numbers, never added to them.
    """
    if not session.session_started or not session.session_ended:
        return {}
    slack = timedelta(minutes=SESSION_LINK_MINUTES)
    start, end = session.session_started - slack, session.session_ended + slack
    # Payouts and rats use the same window as ISK per hour (start of the
    # session to one bounty tick after it), so the tiles agree with each other.
    payout_window = (
        session.session_started,
        session.session_ended + timedelta(minutes=PAYOUT_TAIL_MINUTES),
    )
    payouts = list(
        Payout.objects.filter(character=session.character, occurred_at__range=payout_window)
        .only("entry_type", "amount", "npc_kills")
    )
    payout_total = sum((p.amount for p in payouts), Decimal(0))
    by_type: dict = {}
    rats: dict = {}
    for payout in payouts:
        by_type[payout.entry_type] = by_type.get(payout.entry_type, Decimal(0)) + payout.amount
        for type_id, count in (payout.npc_kills or {}).items():
            rats[int(type_id)] = rats.get(int(type_id), 0) + count
    participants = list(
        KillmailParticipant.objects.filter(
            character=session.character, killmail__killmail_time__range=(start, end),
        ).select_related("killmail").order_by("killmail__killmail_time")
    )
    hull = fleets.hull_for_session(session)
    ids = set(rats) | {p.killmail.victim_ship_type_id for p in participants} | {hull}
    return {
        "payout_total": payout_total,
        "payouts_by_type": sorted(by_type.items(), key=lambda kv: kv[1], reverse=True),
        "payout_count": len(payouts),
        "rats": sorted(rats.items(), key=lambda kv: kv[1], reverse=True),
        "rat_total": sum(rats.values()),
        "kills": [p for p in participants if not p.is_victim],
        "losses": [p for p in participants if p.is_victim],
        "hull": hull,
        "names": typenames.names(ids),
        "payout_labels": dict(Payout.EntryType.choices),
        "window_minutes": SESSION_LINK_MINUTES,
        "tail_minutes": PAYOUT_TAIL_MINUTES,
    }


# ---------------------------------------------------------------------------
# My stats
# ---------------------------------------------------------------------------


@login_required
def my_stats(request):
    """The logged-in pilot's own weekly trends and personal records."""
    weeks = trends.weekly(request.user)
    this_week, last_week = weeks[-1], weeks[-2]
    characters = [
        ownership.character
        for ownership in CharacterOwnership.objects.filter(user=request.user).select_related("character")
    ]
    chart = {
        "labels": [week["week"].strftime("%b %d") for week in weeks],
        "dps": [round(week["dps"]) if week["dps"] is not None else None for week in weeks],
        "isk_per_hour": [
            float(round(week["isk_per_hour"])) if week["isk_per_hour"] is not None else None
            for week in weeks
        ],
    }
    return render(request, "pilotstats/my_stats.html", {
        "weeks": list(reversed(weeks)),
        "this_week": this_week,
        "last_week": last_week,
        "chart": chart,
        "has_sessions": any(week["sessions"] for week in weeks),
        "records": trends.records(request.user),
        "wallet_connected": any(payouts.wallet_tracked(character) for character in characters),
        "week_count": trends.WEEKS,
    })


# ---------------------------------------------------------------------------
# My uploads
# ---------------------------------------------------------------------------


def _my_uploads_context(user, confirm_delete=None):
    uploads = (
        CombatLogUpload.objects.filter(uploaded_by=user)
        .select_related("character")
        .defer(*DETAIL_FIELDS)
    )
    rows = cached_leaderboard_rows("30d", "damage_out")
    return {
        "uploads": uploads,
        "last_session": uploads.first(),
        "rank": rank_of(rows, user),
        "confirm_delete": confirm_delete,
    }


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def my_uploads(request):
    return render(request, "pilotstats/my_uploads.html", _my_uploads_context(request.user))


@login_required
@permission_required("pilotstats.basic_access", raise_exception=True)
def delete_upload(request, pk):
    log_upload = get_object_or_404(CombatLogUpload, pk=pk, uploaded_by=request.user)
    if request.method == "POST":
        log_upload.delete()
        messages.success(request, _("Upload deleted."))
        return redirect("pilotstats:my_uploads")
    return render(
        request,
        "pilotstats/my_uploads.html",
        _my_uploads_context(request.user, confirm_delete=log_upload),
    )


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------


def _privacy_initial(user) -> dict:
    settings_obj = PilotSettings.objects.filter(user=user).first()
    return {
        "hide_from_live_feed": bool(settings_obj and settings_obj.hide_from_live_feed),
        "wallet_opt_out": bool(settings_obj and settings_obj.wallet_opt_out),
    }


def _wallet_characters(user) -> list:
    """The user's characters, each with whether a wallet token is on file."""
    granted = set(
        Token.objects.filter(user=user, scopes__name=WALLET_SCOPE).values_list("character_id", flat=True)
    )
    return [
        {"name": c.character_name, "connected": c.character_id in granted}
        for c in sorted(_owned_characters(user).values(), key=lambda c: c.character_name)
    ]


def _recent_killmails(limit=EVENTS_KILL_LIMIT) -> list:
    hidden_users = PilotSettings.objects.filter(hide_from_live_feed=True).values("user_id")
    hidden_characters = CharacterOwnership.objects.filter(user_id__in=hidden_users).values("character_id")
    participants = (
        KillmailParticipant.objects.exclude(character_id__in=hidden_characters)
        .select_related("killmail", "character")
        .order_by("-killmail__killmail_time")[: limit * 3]
    )
    rows, seen = [], set()
    for p in participants:
        key = (p.killmail_id, p.is_victim)
        if key in seen:
            continue
        seen.add(key)
        rows.append(p)
        if len(rows) >= limit:
            break
    return rows


def _session_date(upload) -> str:
    """UTC date for feed rows stored before they carried one."""
    when = upload.session_started or upload.uploaded_at
    return when.astimezone(dt_timezone.utc).strftime("%Y-%m-%d") if when else ""


def _recent_feed_rows():
    """
    Most recent lines across the latest sessions, newest first by fight time.

    Sessions are picked by when they were flown (an old log uploaded today
    doesn't jump the queue), and lines are ordered by their own EVE date and
    time, so lines from different sessions and days interleave correctly.
    """
    hidden_ids = PilotSettings.objects.filter(hide_from_live_feed=True).values("user_id")
    uploads = (
        CombatLogUpload.objects.exclude(uploaded_by_id__in=hidden_ids)
        .select_related("character")
        .only(
            "feed", "session_started", "session_ended", "uploaded_at",
            "character__character_name",
        )
        .annotate(flown_until=Coalesce("session_ended", "uploaded_at"))
        .order_by("-flown_until", "-pk")[:EVENTS_SESSION_LIMIT]
    )
    rows = []
    for upload in uploads:
        fallback_date = _session_date(upload)
        for item in (upload.feed or [])[:EVENTS_PER_SESSION]:
            rows.append({
                "date": item.get("date") or fallback_date,
                "ts": item.get("ts", ""),
                "kind": item.get("kind", ""),
                "amount": item.get("amount", ""),
                "party": item.get("party", ""),
                "quality": item.get("quality", ""),
                "character_name": upload.character.character_name,
                "session_id": upload.pk,
            })
    rows.sort(key=lambda row: (row["date"], row["ts"]), reverse=True)
    return rows[:EVENTS_ROW_LIMIT]


@login_required
def events(request):
    if request.method == "POST":
        form = PrivacyForm(request.POST)
        if form.is_valid():
            settings_obj, _created = PilotSettings.objects.get_or_create(user=request.user)
            turning_wallet_off = form.cleaned_data["wallet_opt_out"] and not settings_obj.wallet_opt_out
            settings_obj.hide_from_live_feed = form.cleaned_data["hide_from_live_feed"]
            settings_obj.wallet_opt_out = form.cleaned_data["wallet_opt_out"]
            settings_obj.save(update_fields=["hide_from_live_feed", "wallet_opt_out"])
            if turning_wallet_off:
                payouts.delete_wallet_data(request.user)
                messages.success(request, _("Saved. Your stored payouts and rat counts were deleted."))
            else:
                messages.success(request, _("Privacy settings saved."))
            return redirect("pilotstats:events")
    else:
        form = PrivacyForm(initial=_privacy_initial(request.user))

    killmails = _recent_killmails()
    return render(request, "pilotstats/events.html", {
        "form": form,
        "feed_rows": _recent_feed_rows(),
        "killmails": killmails,
        "names": typenames.names(p.killmail.victim_ship_type_id for p in killmails),
        "wallet_characters": _wallet_characters(request.user),
    })


@login_required
@token_required(scopes=[WALLET_SCOPE])
def connect_wallet(request, token):
    """Optional: grant the wallet scope for one character. Payout lines are read hourly."""
    if not CharacterOwnership.objects.filter(user=request.user, character__character_id=token.character_id).exists():
        messages.error(request, _("That character isn't one of yours on Auth, so its wallet won't be read."))
        return redirect("pilotstats:events")
    if PilotSettings.objects.filter(user=request.user, wallet_opt_out=True).exists():
        messages.warning(
            request,
            _("Wallet access granted, but \"Don't read my wallet\" is on. Untick it to start reading payouts."),
        )
        return redirect("pilotstats:events")
    tasks.pull_wallet_journal.delay(token.character_id)
    messages.success(
        request,
        _("Wallet connected for %(name)s. Payouts appear within the hour (ESI caches the journal for an hour).")
        % {"name": token.character_name},
    )
    return redirect("pilotstats:events")


# ---------------------------------------------------------------------------
# Fleets
# ---------------------------------------------------------------------------


@login_required
def fleet_list(request):
    tracked = (
        TrackedFleet.objects.select_related("boss", "started_by")
        .annotate(pilots=Count("members__eve_character_id", distinct=True))[:50]
    )
    return render(request, "pilotstats/fleets.html", {"fleets": tracked})


@login_required
def fleet_detail(request, pk):
    fleet = get_object_or_404(TrackedFleet.objects.select_related("boss", "started_by"), pk=pk)
    cover = fleets.coverage(fleet)
    ship_ids = {hull for row in cover.rows for hull in row["hulls"]}
    return render(request, "pilotstats/fleet_detail.html", {
        "fleet": fleet,
        "coverage": cover,
        "names": typenames.names(ship_ids),
        "can_stop": fleet.is_open and _can_stop_fleet(request.user, fleet),
    })


@login_required
@token_required(scopes=[FLEET_SCOPE])
def track_fleet(request, token):
    try:
        tracked = fleets.start_tracking(request.user, token)
    except fleets.FleetError as exc:
        messages.error(request, str(exc))
        return redirect("pilotstats:fleets")
    except HTTPClientError:
        messages.error(request, _("ESI refused the fleet lookup. Try again in a minute."))
        return redirect("pilotstats:fleets")
    tasks.poll_fleet_now.delay(tracked.pk)
    messages.success(request, _("Tracking your fleet. The roster updates about once a minute."))
    return redirect("pilotstats:fleet", pk=tracked.pk)


def _can_stop_fleet(user, fleet) -> bool:
    if fleet.started_by_id == user.id:
        return True
    return CharacterOwnership.objects.filter(user=user, character=fleet.boss).exists()


@login_required
def stop_fleet(request, pk):
    fleet = get_object_or_404(TrackedFleet, pk=pk, ended_at__isnull=True)
    if request.method != "POST":
        return redirect("pilotstats:fleet", pk=pk)
    if not _can_stop_fleet(request.user, fleet):
        raise PermissionDenied
    fleets.end_tracking(fleet, f"Stopped by {request.user.username}.")
    messages.success(request, _("Stopped tracking that fleet."))
    return redirect("pilotstats:fleet", pk=pk)


# ---------------------------------------------------------------------------
# Dashboard teaser
# ---------------------------------------------------------------------------


def pilotstats_dashboard(request) -> str:
    """
    Render the dashboard hook snippet.

    Returns an HTML string (not a full response) so Alliance Auth can embed
    it on the dashboard alongside other app widgets. Any logged-in member
    gets the widget; uploading stays behind basic_access.
    """
    if not request.user.is_authenticated:
        return ""

    user = request.user
    # Filter on the character row, not EveCharacter.character_id. On Auth 5
    # the EVE id is no longer the primary key, so comparing it to the FK
    # column misses every upload.
    owned = list(_owned_characters(user).values())
    totals = within_days(CombatLogUpload.objects.filter(character__in=owned), 7).aggregate(
        damage_out=Sum("damage_out"),
        reps_given=Sum("reps_given_total"),
        upload_count=Count("id"),
    )
    last = (
        CombatLogUpload.objects.filter(character__in=owned)
        .select_related("character")
        .defer(*DETAIL_FIELDS)
        .first()
    )
    since = timezone.now() - timedelta(days=7)
    payout_isk = PayoutDaily.objects.filter(
        character__in=owned, day__gte=since.date(),
    ).aggregate(total=Sum("amount"))["total"] or 0
    kills = (
        KillmailParticipant.objects.filter(
            character__in=owned, is_victim=False, killmail__killmail_time__gte=since,
        ).values("killmail_id").distinct().count()
    )
    rows = cached_leaderboard_rows("7d", "damage_out")
    return render_to_string("pilotstats/dashboard_widget.html", {
        "damage_out": totals["damage_out"] or 0,
        "reps_given": totals["reps_given"] or 0,
        "upload_count": totals["upload_count"],
        "payout_isk": payout_isk,
        "kills": kills,
        "last_session": last,
        "rank": rank_of(rows, user),
        "can_upload": user.has_perm("pilotstats.basic_access"),
    }, request=request)
