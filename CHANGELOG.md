# Changelog

## Unreleased

Run `migrate` (adds `0004_combatlogupload_payout_isk`, `0005_combatlogupload_timeline` and `0006_combatlogupload_activity`), then `collectstatic`. Then run `python manage.py pilotstats_classify` once to give stored sessions their activity. Existing sessions from the last month get their ISK per hour at the next hourly wallet pull.

- **ISK per hour.** Each session stores the bounty and mission payouts its character earned from the first combat line until 20 minutes after the last one, the bounty tick interval (`PILOTSTATS_PAYOUT_TAIL_MINUTES`).
  - **Session page:** a new "ISK per hour" tile.
  - **Leaderboard:** a sortable **ISK/h** column, weighted by logged hours: total earned ISK divided by total session time, not an average of per-session rates.
  - **What counts:** ESS escrow is left out, since it's paid hours later in one lump. Sessions under 10 minutes get no rate. A payout two overlapping sessions could claim goes to the later one, so nothing is counted twice.
  - **No wallet connected:** pilots without one show "—", not 0.
  - **Opting out:** "Don't read my wallet" also clears this.
- The session page's **Payouts** and **Rats killed** tiles now use the same window as ISK per hour, from the session's start to 20 minutes after its end, so the three agree. They used 15 minutes either side, which could miss the last bounty tick. Kills and the hull still use 15 minutes either side.
- **Damage and reps over time.** The session page (and the upload result) has a chart of damage dealt, damage received, reps received and reps given, per second.
  - **Buckets:** one-minute steps, which grow for long sessions so the chart never has more than 360 points.
  - **Storage:** the totals are stored with the session when it's parsed (new `timeline` field). List pages don't load them.
  - **Chart library:** the chart uses Alliance Auth's own Chart.js bundle. If that's blocked, the page says so instead of showing an empty box.
  - **Older sessions:** sessions uploaded before this have no chart. Uploading the same log file again adds it; that only works for your own sessions, and a re-upload that already has a chart is still a duplicate.
- **My Stats page** (new tab), for each pilot's own numbers over the last 12 EVE weeks (Monday 00:00 EVE time).
  - **This week vs last week:** average DPS, ISK per hour, hours logged, and rats killed.
  - **Trend chart:** weekly average DPS and ISK per hour.
  - **Table:** sessions, hours, damage, DPS, ISK/h, payouts and rats per week.
  - **Personal records:** most damage in a session, best session DPS, best session ISK/h, best payout day, and most rats in a day.
  - **How it's counted:** rates are weighted by time and skip sessions under 10 minutes, like ISK per hour elsewhere. Payouts and rats come from the wallet and need it connected. Only the pilot's own data is shown.
- **Activity type per session:** Ratting, Incursion, Abyssal or Missions.
  - **Wallet signals:** a mission reward in the session's payout window means Missions, and a bounty tick means Ratting.
  - **NPC-name signals:** Sansha incursion NPCs and abyssal NPCs (Triglavians, Drifters, Ephialtes, Karybdis) make up at least a quarter of the damage dealt. An abyssal also needs no bounties, so Triglavian ratting stays Ratting.
  - **When neither applies:** "Other" if the wallet is connected, blank if not.
  - **Where it shows:** a badge on the session page, a column on My Uploads, and a filter on the leaderboard. With an activity picked, only the session columns and ISK/h show, because payouts, rats and killmails can't be split by activity.
  - **When it updates:** sessions are classified on upload and after every wallet pull. `pilotstats_classify` fills in stored sessions; older ones go by NPC names only.
  - **Opting out:** "Don't read my wallet" clears the wallet-based activities.
  - **Name lists:** they live in `aa_pilotstats/activity.py`.
- **Corporation and alliance totals on the leaderboard.** A Pilots / Corporations / Alliances toggle groups pilots under their main's corporation or alliance.
  - **Columns:** member count and the same columns as the pilot view, for the same range and activity filter.
  - **Per-group rules:** ISK/h is weighted over all members' logged hours, and a killmail several members are on counts once.
  - **Details:** each group expands to its members. Pilots without a main go into an "Unknown" group at the bottom.
- **CSV export** of whatever the leaderboard shows: pilots (with corporation and alliance), corporations or alliances, for the chosen range and activity. Names that start like a spreadsheet formula are quoted so they can't run as one.
- New [roadmap](ROADMAP.md) with the planned work: session DPS chart, personal trends, activity types, corp totals, MariaDB CI, leaderboard caching, and AA Desktop upload.
- **Automatic upload with AA Desktop.** When the optional `aa-desktop-bridge` plugin is installed, Pilot Stats registers a desktop module with two routes under `/api/desktop/v1/modules/pilotstats/`: `POST upload` and `GET characters`. Each needs a bearer token with the `desktop.pilotstats.upload` scope and the `pilotstats.basic_access` permission. Uploads use the folder scanner's code, results and per-user limit. Setup is in the README under "Automatic upload with AA Desktop". Without the bridge nothing changes.
- The JSON upload path is now one function, `views.upload_json`, shared by the folder scanner, multi-file drop and AA Desktop. Behavior is unchanged.
- **Leaderboard caching.** The leaderboard, the rank on My Uploads, and the dashboard widget reuse a computed board instead of rebuilding it on every page load.
  - **When it clears:** right away, once a new or deleted session, wallet pull, killmail, wallet opt-out, or change of main is saved.
  - **How long it's kept:** otherwise up to 5 minutes (`PILOTSTATS_LEADERBOARD_CACHE_SECONDS`, `0` turns it off), so a renamed character shows its new name within that time.
  - **Where:** Alliance Auth's own cache (Redis). Nothing to set up. The Discord daily post still reads the live numbers.

## v0.1.1

- **Discord settings: a "Save and send a test message" button** next to Save. It saves the form, then posts a test message, so a newly pasted webhook can be checked in one click. Before, the test was an admin action on the list page that was easy to miss.
- *Admin → Pilot Stats → Discord settings* opens the settings directly instead of a one-row list (or the add form, the first time).

## v0.1.0 — Pilot Stats

The first release on PyPI as `aa-pilotstats`. PvE Tracker is renamed to Pilot Stats, Gamelog uploads can be automated, and payouts, killmails, fleets and a Discord feed join the combat logs.

**Upgrading from PvE Tracker:** follow "Upgrading from PvE Tracker" in the README: swap `aa-pvetracker` for `aa-pilotstats`, replace `"aa_pvetracker"` with `"aa_pilotstats"` in `INSTALLED_APPS`, add the beat tasks from the README to `local.py`, then `migrate` and `collectstatic`. `migrate` adopts the old tables (`0001_initial`) and adds `0002_esi_zkill_fleets` and `0003_discord`.

### Renamed to Pilot Stats

| | Before | Now |
|---|---|---|
| Package | `aa-pvetracker` / `aa_pvetracker` | `aa-pilotstats` / `aa_pilotstats` |
| Django label | `pvetracker` | `pilotstats` |
| Permission | `pvetracker.basic_access` | `pilotstats.basic_access` |
| URLs | `/pvetracker/…`, `pvetracker:` | `/pilotstats/…`, `pilotstats:` |

- **Data and permissions carry over.** The new `0001_initial` renames the old tables in place with their rows, moves the content types so `basic_access` group grants keep working, renames the permission's display name, and removes PvE Tracker's migration rows. A fresh install just creates the tables.
- Old `/pvetracker/…` links redirect permanently to `/pilotstats/…`, with the query string kept.
- System check `pilotstats.E001` refuses to start while `aa_pvetracker` is still in `INSTALLED_APPS`.
- Templates, static files, CSS classes (`.pstats-*`), and the dashboard panel moved to the new name. Database index and constraint names keep their `pvetracker_` prefix so upgraded tables match.
- The PvE Tracker migrations 0001–0004 are replaced by the single Pilot Stats `0001_initial`.

### Gamelog folder scanning

- **Gamelog folder scanning** on the Upload page.
  - In Chrome or Edge, pick the Gamelogs folder once. While the tab is open, new and grown logs upload every minute, and a growing log updates its session.
  - Other browsers can scan a folder once.
  - Only the user's own characters' logs leave the computer. Logs from the last 7 days go by default, and a checkbox includes older ones.
- **Multi-file drop**: dropping several logs uploads each in turn, with a per-file result list. A single file still posts the form and shows its session dashboard.
- The upload endpoint also answers JSON (`Accept: application/json`) with a result code (`created`, `updated`, `duplicate`, `no_new_lines`, `no_combat`, `not_gamelog`, `not_owned`, `other_account`, `too_large`). New `upload/characters.json` lists the user's characters for the scanner's local filter.
- JSON uploads are limited per user (`PILOTSTATS_SCANNER_UPLOADS_PER_MINUTE`, default 30; 429 with `Retry-After`). The upload form is unchanged and unlimited.

### Wallet payouts, zKill kills and losses, fleet tracking

- **Wallet payouts (optional per pilot).**
  - Pilots can connect a character's wallet (`esi-wallet.read_character_wallet.v1`). An hourly task stores bounty, ESS and mission payout lines, nothing else, and adds a **Payout ISK** column (with a per-type breakdown) to the leaderboard.
  - Bounty ticks also give **rats killed** per NPC type.
  - A "Don't read my wallet" checkbox on Events stops reading and deletes what was stored.
- **zKillboard kills and losses** from the R2Z2 feed, every minute.
  - New columns: Kills / Losses, ISK destroyed / lost, and killmail damage. Events lists recent kills and losses.
  - Mails are kept for tracked corps and alliances (by default those of Member-state mains; editable in admin) and only for Auth characters.
  - A mail with two alts of one main counts once.
- **Fleet tracking (optional, fleet boss).**
  - The boss presses "Track my fleet" (`esi-fleets.read_fleet.v1`). The roster, the hulls, and how many Auth pilots uploaded a combat log are recorded every minute.
  - Sessions flown in a tracked fleet show their hull.
- **Session page**: "Around this session" tiles show payouts, rats killed, kills and losses, and the hull for that character and time (±15 min). They sit beside the combat-log numbers and are never added into them.
- Dashboard widget adds 7-day payouts and kills.
- The leaderboard now has rows for pilots with payouts or kills but no uploaded log.
- Hull and NPC names come from the `eve_sde` app when installed, otherwise from CCP's SDE over HTTP, cached locally. Pages never wait on it.
- New dependencies: `django-esi>=9.0` (already required by Alliance Auth) and `requests`. Works with django-esi 9.x and 10.x; CI tests both.

### Discord webhook feed

- **Discord webhook feed** (Admin → Pilot Stats → Discord settings).
  - A daily top 10 for damage and payout ISK (last 24 h), with the biggest kills and losses, at a set time and time zone.
  - Major events: a payout line or a member's kill/loss over a threshold (default 300M), and new fleet / personal records for session damage, session DPS, and payouts in a day.
  - Every post goes out once, event posts are capped per hour, and nobody is ever pinged. Embeds stay within Discord's limits and long posts split across messages. 429s and server errors are retried.
  - The webhook URL lives in the database only and is write-only in admin. There's a "send a test message" admin action.

## v0.0.11

Run `migrate` after upgrading (adds migration `0004_combatlogupload_log_started`).

- **Alliance Auth 5 only.** Requires `allianceauth>=5.0.0,<6`. The migrations depended on an Auth migration (`eveonline` 0025) that only ships in Auth 5.1.4 and later, so `migrate` failed on Auth 4 and on early 5.x. They now depend on `eveonline` 0021 (in every 5.x release). Already-applied installs are unaffected.
- **Re-uploading a growing log updates the same session** instead of adding a second one and double-counting the leaderboard. Sessions are keyed by character plus the log header's `Session Started:` time (new `log_started` field, migration 0004). A copy with no new combat lines is rejected, and a session uploaded by a different account is never overwritten. Sessions stored before this change have no header time and are left alone.
- **Remote reps from faction / deadspace modules are counted.** The rep patterns rejected any module with a hyphen (`Centum A-Type …`, `Gist X-Type …`), so those reps were dropped entirely. Reps given was affected the same way.
- **Player attackers no longer split per weapon** in Top Attackers; the weapon is read separately from the attacker name.
- **7- and 30-day windows go by fight time**, not upload time, on the leaderboard, My Uploads rank, and dashboard widget. An old log uploaded today no longer counts as this week.
- **Log times are stored as UTC (EVE time)** whatever the server's `TIME_ZONE` is.
- Two uploads of the same log at the same moment show the "already uploaded" message instead of a server error.
- Events orders lines by their own EVE date and time across sessions and shows the date. Feed rows now carry a `date`; older rows fall back to the session's date.
- Dropping several files on the upload box shows "one file at a time" instead of silently ignoring all but one.
- Leaderboard and list queries no longer load every session's JSON breakdowns, and the dashboard widget sums in the database.
- The dev URL config mounts the app through its url hook with Auth's `main_character_required`, as a real Auth does, and the view tests log in users with a main. CI runs on Auth 5.0.0 and 5.6.0 and checks that migrations match the models.
## v0.0.8 – v0.0.10

- Events, session cards, and the section nav stay readable on Alliance Auth's dark themes. Darkly leaves `--bs-tertiary-bg` light and never sets `data-bs-theme="dark"`, so those surfaces were white under white text. Surfaces use `bg-body-tertiary` / `text-body` / `text-body-secondary`, the active tab is a `nav-pills` pill on `--bs-primary`, and Darkly remaps tertiary to `--bs-dark`.
- README now says v0.0.7, matching the package.
- Leaderboard roll-up lives in `aa_pvetracker.leaderboard` so the site, the dashboard rank, and a later Discord post share one grouping. Extra numeric columns can be summed later without a second grouping.
- Any logged-in member can view the leaderboard, a session, and the events list. `pvetracker.basic_access` is the upload and delete permission.
- Shared section nav (Leaderboard / Upload / My Uploads / Events), a session page that reuses the upload dashboard, and My Uploads plus the dashboard widget now show duration, last session, and rank.
- One checkbox hides your sessions from the events list. Leaderboard totals stay.
- Dashboard widget matches sessions by character row. On Alliance Auth 5 the EVE character id is not the primary key, so the old id comparison never matched an upload.
- `Payout` (character, time, type, amount, journal id) and `CombatLogUpload.ship_type_id` are in the schema for a later release. Nothing writes them yet.
- GitHub Actions runs the test suite on Alliance Auth 5.6.0.

## v0.0.7

Fix the leaderboard's per-character detail row rendering as a solid white bar — turned out to be a second, different bug from the v0.0.5 collapse fix. DevTools inspection confirmed the collapse mechanism itself was working correctly (Bootstrap marked the trigger `collapsed`/`aria-expanded="false"` as expected); the actual bug was Bootstrap's `bg-body-tertiary` utility not resolving to a dark background on a nested `<table class="table ...">` under Alliance Auth's Bootswatch-based theme (which pre-compiles a fixed theme rather than using Bootstrap 5.3's `data-bs-theme` variable-swap dark mode, so some table-specific background variables don't carry through the same way). Fixed by dropping Bootstrap's `.table` classes for this nested element and giving it fully explicit, self-contained CSS (`.pvet-detail-table`) instead — consistent with how the rest of this app's custom `.pvet-*` components are already styled, all of which were rendering correctly.

## v0.0.6

Closes the remaining visual gap with the standalone prototype's dashboard: a "Recent Activity" feed at the bottom of the upload result page, showing the last 60 events of the session (most recent first), same row layout and color-coding as the original. This required a small schema addition:

- New `CombatLogUpload.feed` JSONField (migration `0002_combatlogupload_feed`) — a capped, most-recent-first slice of the session's events, built in `parsing.summarize()` alongside the existing aggregates.
- Upload result page header now also shows session duration (`HH:MM:SS`) and the source filename, matching the prototype's compact header line.

## v0.0.5

- Fix the leaderboard's per-character expand row rendering as a solid white bar in dark mode. Bootstrap's `.collapse` doesn't animate `<tr>` elements correctly (table rows don't support the height transition it relies on), so the row was showing at an awkward in-between state with Bootstrap's default (light) table background instead of collapsing. Fixed by collapsing a `<div>` inside an always-present `<tr>`/`<td>` instead of collapsing the row itself — the standard workaround for this well-known Bootstrap limitation.

## v0.0.4

- Fix a stray absolute `from pvetracker.parsing import ...` in `test_parsing.py` left over from the v0.0.3 rename — missed because it's an absolute import rather than a relative one, so `git mv` didn't touch it and it wasn't caught until re-running the test suite post-rename. Caught immediately by that same test suite before this was reported by anyone; no functional code was affected, only the test module's own import.

## v0.0.3

Visual redesign — brought over the standalone prototype's dashboard look (big color-coded stat tiles, hit-quality bar charts) into the Alliance Auth app, built on Bootstrap 5 theme tokens so it still respects the site's light/dark toggle:

- Upload result page now shows full stat tiles (damage/DPS, reps received/given), top targets/attackers panels, and hit-quality bar charts — same data as before, much closer to the standalone dashboard's presentation.
- Leaderboard rows get numbered rank badges (gold/silver/bronze for top 3) and color-coded damage/rep columns (cyan/red/gold, matching the stat tiles).

## v0.0.2

- **Breaking (pre-install only):** renamed the importable package from `pvetracker` to `aa_pvetracker` so the `INSTALLED_APPS` entry matches the `aa-pvetracker` distribution name from `requirements.txt` (PEP 503 normalization aside — same name). If you already added `'pvetracker'` to `local.py`, change it to `'aa_pvetracker'`.
- Everything else (permission names, URL namespace, template tags, admin URLs) is unchanged — the Django app `label` stays `pvetracker` internally, so `pvetracker.basic_access` and `{% url 'pvetracker:...' %}` still work as documented.

## v0.0.1

Initial release — upload + leaderboard (Phase 1).

- Drag-and-drop (or file-picker) upload of an EVE Gamelog combat log.
- Server-side parsing ported from the standalone prototype, validated against real combat logs.
- Ownership-verified character attribution (log's `Listener:` must be a character the uploader owns).
- Fleet leaderboard with main/alt roll-up, date-range filter, and sortable columns.
- My Uploads page with delete.
- Alliance Auth dashboard widget.
