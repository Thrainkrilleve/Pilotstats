"""
Settings, each overridable from Alliance Auth's ``local.py``.

Read once at import; restart Auth after changing one.
"""

# Django
from django.conf import settings

from . import __version__

# Shared ---------------------------------------------------------------------

USER_AGENT = getattr(
    settings,
    "PILOTSTATS_USER_AGENT",
    f"aa-pilotstats/{__version__} (+https://github.com/Thrainkrilleve/PilotStats)",
)

# ESI ------------------------------------------------------------------------

# Reviewed ESI compatibility date. ESI serves the oldest supported behaviour
# when none is sent, so this is pinned on purpose; bump it after checking the
# changelog at https://developers.eveonline.com/docs/services/esi/overview/
ESI_COMPATIBILITY_DATE = getattr(settings, "PILOTSTATS_ESI_COMPATIBILITY_DATE", "2026-11-19")

WALLET_SCOPE = "esi-wallet.read_character_wallet.v1"
FLEET_SCOPE = "esi-fleets.read_fleet.v1"

# Itemized payout lines are kept a little longer than the journal's 30-day
# window so a journal id is never seen twice; daily totals are kept forever.
PAYOUT_RETENTION_DAYS = int(getattr(settings, "PILOTSTATS_PAYOUT_RETENTION_DAYS", 35))

# Kills and fleet hulls are matched to a Gamelog session when they fall
# within this many minutes of it. Payouts use PAYOUT_TAIL_MINUTES below.
SESSION_LINK_MINUTES = int(getattr(settings, "PILOTSTATS_SESSION_LINK_MINUTES", 15))

# ISK per hour: bounties are paid in ticks every 20 minutes, so a payout up to
# this long after a session's last combat line still belongs to it.
PAYOUT_TAIL_MINUTES = int(getattr(settings, "PILOTSTATS_PAYOUT_TAIL_MINUTES", 20))

# In systems with an ESS, part of every bounty is paid later as one ESS escrow
# transfer. Each transfer is shared out over the character's bounty ticks
# since the previous transfer, but only ticks at most this many hours older
# than it: older ones were robbed from the bank or earned without an ESS.
ESS_MAX_DELAY_HOURS = float(getattr(settings, "PILOTSTATS_ESS_MAX_DELAY_HOURS", 4))

# Leaderboard -----------------------------------------------------------------

# Seconds a computed leaderboard is kept in Django's cache. New sessions,
# payouts and killmails clear it at once, so this mostly bounds how long a
# renamed character or a changed main shows the old name. 0 turns caching off.
LEADERBOARD_CACHE_SECONDS = int(getattr(settings, "PILOTSTATS_LEADERBOARD_CACHE_SECONDS", 300))

# zKillboard R2Z2 ---------------------------------------------------------------

R2Z2_URL = getattr(settings, "PILOTSTATS_R2Z2_URL", "https://r2z2.zkillboard.com/ephemeral").rstrip("/")
# Files fetched per run of drain_r2z2 (it runs every minute).
R2Z2_MAX_PER_RUN = int(getattr(settings, "PILOTSTATS_R2Z2_MAX_PER_RUN", 300))
# Pause between files. zKill allows 15 requests/second per IP; stay far under.
R2Z2_REQUEST_INTERVAL = float(getattr(settings, "PILOTSTATS_R2Z2_REQUEST_INTERVAL", 0.15))
# Auth states whose mains' corporations and alliances are tracked automatically.
ZKILL_AUTO_STATES = list(getattr(settings, "PILOTSTATS_ZKILL_AUTO_STATES", ["Member"]))

# EVE static data -------------------------------------------------------------

SDE_URL = getattr(settings, "PILOTSTATS_SDE_URL", "https://sde.evetech.net/tranquility/latest").rstrip("/")

# Gamelog folder scanner ------------------------------------------------------

# Most uploads one user's scanner may send per minute. A first scan of a
# large Gamelogs folder is paced to this; 0 turns the limit off.
SCANNER_UPLOADS_PER_MINUTE = int(getattr(settings, "PILOTSTATS_SCANNER_UPLOADS_PER_MINUTE", 30))

# By default the scanner only sends logs modified in the last N days; pilots
# can tick "include older logs" for a one-time backfill.
SCANNER_RECENT_DAYS = int(getattr(settings, "PILOTSTATS_SCANNER_RECENT_DAYS", 7))
