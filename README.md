# Pilot Stats

> Pilot and fleet stats for [Alliance Auth](https://allianceauth.readthedocs.io/): upload EVE combat logs, attribute stats to characters (and roll alts up to mains), and see a fleet leaderboard.

Pilot Stats was called **PvE Tracker** (`aa-pvetracker`) up to v0.0.11. To upgrade an existing install, see [Upgrading from PvE Tracker](#upgrading-from-pve-tracker). Your stored sessions and upload permissions carry over.

Each EVE client's combat log only ever records damage *involving that pilot*, so no single file shows a whole fleet's damage. Pilot Stats solves this the same way FAT solves fleet attendance: every pilot uploads their own combat log, the app verifies the log actually belongs to a character on their Alliance Auth account, and the results roll up into one fleet-wide leaderboard.

## Status

Alliance Auth 5.x only (`allianceauth>=5.0.0,<6`). Alliance Auth 4 is not supported. Combat stats still come from uploaded EVE Gamelog `.txt` files. The current version is in [`aa_pilotstats/__init__.py`](aa_pilotstats/__init__.py), changes are listed in the [changelog](CHANGELOG.md), and planned work is in the [roadmap](ROADMAP.md).

Any logged-in member can view the leaderboard, a stored session, and the events list. Uploading and deleting logs needs `pilotstats.basic_access`.

A note on naming: the installable/importable package is `aa_pilotstats` (distributed as `aa-pilotstats`), and the Django app *label* is the shorter `pilotstats`. The label is what you see in permission strings (`pilotstats.basic_access`), the URL namespace (`{% url 'pilotstats:...' %}`), table names, and migrations. Test discovery follows the import package, so the suite is `manage.py test aa_pilotstats`, not `pilotstats`.

## Features

- **Drag-and-drop upload** of one or more Gamelog `.txt` files (from `Documents\EVE\logs\Gamelogs`). It falls back to a plain file picker with no JS. Upload and delete require `pilotstats.basic_access`.
- **Gamelog folder scanning** on the Upload page, with no install:
  - **Chrome / Edge**: pick the Gamelogs folder once. While the tab stays open, it's rescanned every minute and new or grown logs are uploaded, so an ongoing session refreshes about once a minute. The folder is remembered, so the next visit is one "Resume" click (the browser asks for permission again each visit).
  - **Firefox / Safari**: "Scan a folder once" does the same pass a single time.
  - Logs are read on the pilot's computer, and only logs whose `Listener:` is one of the user's own characters are sent; the server re-checks ownership anyway.
  - By default only logs from the last 7 days go up. A checkbox includes older logs for a one-time backfill.
- **Automatic upload with AA Desktop** (optional): pilots who run the AA Desktop app get their logs uploaded in the background with no browser tab open. See [Automatic upload with AA Desktop](#automatic-upload-with-aa-desktop).
- **Ownership-verified attribution** — a log is only accepted if its `Listener:` character is one the uploading user actually owns on Alliance Auth.
- **Main/alt roll-up** — leaderboard rows are grouped by each user's designated main character, with a per-character breakdown on expand. The grouping lives in `aa_pilotstats.leaderboard` so later Discord and ISK/kill columns can use the same buckets.
- **Re-uploading a growing log updates the same session.** The EVE client keeps writing to one file per client session. A session is identified by its character plus the log header's `Session Started:` time, so uploading a longer copy replaces that session's totals instead of counting the fight twice.
- **Fleet leaderboard** for the last 7 days, last 30 days, or all time, with rank badges and sortable damage and rep columns. The 7- and 30-day windows go by when the fight happened (the log's first combat line, in EVE time / UTC), not by when the log was uploaded.
- **Corporation and alliance totals** — the leaderboard can group pilots by their main's corporation or alliance, and any view downloads as CSV.
- **Activity types** — each session is labeled Ratting, Incursion, Abyssal or Missions from the wallet (bounty ticks, mission rewards) and the NPCs shot, and the leaderboard can be filtered by activity. After upgrading, run `python manage.py pilotstats_classify` once for stored sessions.
- **My Stats** — each pilot's own last 12 weeks: average DPS, ISK per hour, hours logged, payouts and rats per week, with a trend chart and personal records (most damage, best DPS, best ISK/h, best payout day, most rats in a day). Any logged-in member sees their own.
- **Session page** — the upload-result dashboard (tiles, a damage-and-reps-over-time chart, hit-quality bars, top targets and attackers, recent activity) has a stable URL, linked from My Uploads. Any logged-in member can open it.
- **My Uploads** shows duration, your last session, and your 30-day damage rank.
- **Events** lists recent lines from uploaded sessions. One checkbox hides your sessions from that list; leaderboard totals are unchanged. Live tailing is not in this version.
- **Dashboard widget** with your last-7-days damage and reps, last session, and 7-day damage rank.
- **Shared section nav** on every page: Leaderboard, Upload, My Uploads, Events. Upload and My Uploads are shown only when you can upload.

## Known limitations

- **Near-live, not live.** The folder scanner re-uploads a growing log about once a minute while its tab is open; there's no line-by-line live view yet, and closing the tab stops scanning. [AA Desktop](#automatic-upload-with-aa-desktop) uploads without an open tab.
- Sessions uploaded before the "same session" matching was added have no header time stored, so a re-upload of one of those logs is stored as a new session; delete the old one from My Uploads.
- **Only the combat log has DPS.** ESI has no combat or damage data. Payouts, rat counts, killmails and fleet rosters sit next to the Gamelog numbers. Damage, payout ISK, and kill value are never added together.
- **Payouts lag up to an hour.** ESI caches the wallet journal for an hour, and the journal only goes back 30 days, so payouts from before a pilot connected their wallet (older than 30 days) can't be fetched.
- **Killmails are mostly PvP.** Killing rats makes no killmail, so the kill columns don't reflect ratting; "Rats" (from bounty ticks) does. Killmail damage is only damage on killmails.
- **`reps_given_total` is an estimate.** The regex for a logi pilot's own *outgoing* remote-repair line was written by mirroring the incoming-rep line shape, since a real logi pilot's log was not available to test against. If the numbers look off, open an issue with a sample line (amounts and names redacted is fine).
- **Trust model**: a log's *character* has to be one you own via Alliance Auth, but the *numbers in it* are still whatever that client wrote to disk.

## Requirements

- Python >= 3.10
- Alliance Auth >= 5.0.0, < 6
- Redis (Alliance Auth already requires it)

## Installation

1. Install the package:

   ```bash
   pip install aa-pilotstats
   ```

2. Add `"aa_pilotstats"` to `INSTALLED_APPS` in your Alliance Auth settings file (e.g. `local.py`).

3. Rebuild the image, run migrations, and collect static files:

   ```bash
   docker compose build
   docker compose exec allianceauth_gunicorn bash -c "auth migrate && auth collectstatic --noinput"
   docker compose up -d
   ```

4. Grant `pilotstats.basic_access` to the group(s) that should upload and delete logs. Viewing does not need it: any logged-in member can open the leaderboard, sessions, and events.

5. Schedule the background tasks in `local.py`:

   ```python
   from celery.schedules import crontab

   CELERYBEAT_SCHEDULE["pilotstats_pull_wallet_journals"] = {
       "task": "aa_pilotstats.tasks.pull_all_wallet_journals",
       "schedule": crontab(minute="7"),  # hourly; ESI caches the journal for an hour
   }
   CELERYBEAT_SCHEDULE["pilotstats_drain_r2z2"] = {
       "task": "aa_pilotstats.tasks.drain_r2z2",
       "schedule": crontab(minute="*"),  # zKill killmails, every minute
   }
   CELERYBEAT_SCHEDULE["pilotstats_poll_tracked_fleets"] = {
       "task": "aa_pilotstats.tasks.poll_tracked_fleets",
       "schedule": crontab(minute="*"),  # fleet rosters, every minute (idle when none tracked)
   }
   CELERYBEAT_SCHEDULE["pilotstats_purge_old_payouts"] = {
       "task": "aa_pilotstats.tasks.purge_old_payouts",
       "schedule": crontab(minute="23", hour="4"),
   }
   CELERYBEAT_SCHEDULE["pilotstats_post_discord_daily"] = {
       "task": "aa_pilotstats.tasks.post_discord_daily",
       "schedule": crontab(minute="*/5"),  # posts once a day at the time set in admin
   }
   ```

   Skip a task to turn its source off. The Gamelog features don't need any of them.

## Data sources

| Source | What it adds | How it's collected | Needs |
|---|---|---|---|
| Gamelog | DPS, hit quality, reps | Upload, drag-and-drop, the folder scanner, or AA Desktop | `pilotstats.basic_access` |
| ESI wallet journal | Payout ISK (bounty, ESS, mission), rats killed | Hourly per character that connected its wallet | Optional per pilot: `esi-wallet.read_character_wallet.v1` |
| zKillboard R2Z2 | Kills, losses, ISK destroyed/lost, killmail damage | Every minute from zKill's public feed | Nothing from pilots |
| ESI fleet | Who was in a fleet, hull, combat-log coverage | Every minute while a fleet is tracked | Optional, fleet boss only: `esi-fleets.read_fleet.v1` |

**What's stored, and what isn't:**
- **Wallet:**
  - Kept: character, time, type, amount and journal id for bounty, ESS and mission payout lines only. For bounty ticks, the per-NPC-type kill counts are derived from the line.
  - Never stored: balance, other transactions, `*_tax` lines, the other party, or the line's raw text.
  - Itemized lines are kept for 35 days; daily totals for longer.
  - A pilot can untick their wallet any time ("Don't read my wallet" on Events), which also deletes what was stored.
- **zKill:** a mail is kept only when one of your Auth characters in a tracked corporation or alliance is on it, and only those characters are stored.
  - By default, the corps and alliances of mains in the `Member` state are tracked.
  - Edit the list under *Admin → Pilot Stats → tracked corporations and alliances*.
- **Fleets:** pilot id, hull, and first and last time seen. Tracking stops by itself when the boss leaves or hands over the fleet.

ESI calls use django-esi's client with a pinned compatibility date (`PILOTSTATS_ESI_COMPATIBILITY_DATE`) and its built-in ETag and rate-limit handling. Only the wallet journal, character fleet and fleet members routes are called.

## Discord feed

Posts to one Discord channel through a webhook. There are no bot or slash commands. Set it up under *Admin → Pilot Stats → Discord settings*:

1. In Discord, go to the channel's *Settings → Integrations → Webhooks → New Webhook → Copy Webhook URL*.
2. Paste it into **webhook URL** and tick **enabled**. The URL is stored in the database only and is never shown again; leave the field blank to keep it.
3. Click **Save and send a test message**. It saves the settings, then posts a test message to the channel.

**What gets posted:**
- **Daily top 10**: once a day at the set time and time zone (default 11:05 UTC, after EVE downtime).
  - Top 10 for damage dealt and for payout ISK over the last 24 hours, plus the biggest kills and losses.
  - Pilots are grouped by main, like the leaderboard.
- **Major events**, as they happen:
  - A single payout line at or over the payout threshold (default 300M). These arrive up to an hour late, because ESI caches the wallet journal.
  - A member's kill or loss at or over the kill threshold (default 300M).
  - New **fleet records** (and personal records, after five sessions) for session damage and session DPS.
  - A new fleet record for one main's payouts in a day.
- Event posts are capped per hour (default 6); extras are skipped, not queued. The daily post is never capped.
- Each post goes out once (there's a log under *Discord posts*), only names and numbers are sent, and nobody is ever pinged. Keep in mind the channel may be visible to people who aren't on Auth.

## Automatic upload with AA Desktop

AA Desktop is a desktop app that signs in to your Alliance Auth. With it, a pilot's Gamelogs are uploaded automatically while the app runs, with no browser tab open. It uses the same upload code as the folder scanner: the same ownership check, result codes and per-user limit (`PILOTSTATS_SCANNER_UPLOADS_PER_MINUTE`, shared between the scanner and the app).

This needs the `aa-desktop-bridge` plugin on your Auth. It isn't published yet, so for now this applies only to Auths that already run it. Without it, Pilot Stats doesn't register anything for the desktop app and works as before.

1. Install and set up `aa-desktop-bridge` as its README says.
2. Add the Pilot Stats scope to the scopes the OIDC provider knows about. Merge it into your existing `OAUTH2_PROVIDER["SCOPES"]` in `local.py`; don't replace the scopes already there:

   ```python
   OAUTH2_PROVIDER.setdefault("SCOPES", {})["desktop.pilotstats.upload"] = (
       "Upload your own characters' EVE combat logs to Pilot Stats."
   )
   ```

3. Restart Auth. `python manage.py desktop_modules` should list `pilotstats` as mounted, with the scope registered.

The app asks each pilot to approve the `desktop.pilotstats.upload` scope in the browser the first time they turn on automatic upload. Each request needs both that scope and `pilotstats.basic_access`, so removing the permission stops a pilot's uploads, and so does revoking the app's access in Auth. The routes are under `/api/desktop/v1/modules/pilotstats/` (`POST upload`, `GET characters`) and accept only bearer tokens, never a browser session.

## Settings

Optional, in `local.py`:

| Setting | Default | What it does |
|---|---|---|
| `PILOTSTATS_SCANNER_UPLOADS_PER_MINUTE` | `30` | Most uploads one user's folder scanner (or multi-file drop) may send per minute. Over it, the server answers 429 and the scanner waits. `0` turns the limit off. The plain upload form is never limited. |
| `PILOTSTATS_SCANNER_RECENT_DAYS` | `7` | The scanner only sends logs modified in the last N days unless "include older logs" is ticked. |
| `PILOTSTATS_LEADERBOARD_CACHE_SECONDS` | `300` | Seconds a computed leaderboard is kept in Auth's cache. New sessions, payouts and killmails clear it right away; this bounds how long other changes (a renamed character) take to show. `0` turns caching off. |
| `PILOTSTATS_ESI_COMPATIBILITY_DATE` | `"2026-11-19"` | ESI compatibility date sent on every call. Bump it only after checking CCP's ESI changelog. |
| `PILOTSTATS_PAYOUT_RETENTION_DAYS` | `35` | Days itemized payout lines are kept (daily totals are kept longer). |
| `PILOTSTATS_SESSION_LINK_MINUTES` | `15` | How far before and after a session kills and fleet hulls are matched to it. |
| `PILOTSTATS_PAYOUT_TAIL_MINUTES` | `20` | How long after a session ends its last bounty tick can still arrive. Payouts, rats and ISK per hour count from the session start to this long after its end. |
| `PILOTSTATS_ESS_MAX_DELAY_HOURS` | `4` | ISK per hour adds each bounty tick's ESS share: every ESS escrow transfer is split over the bounty ticks since the previous one, in proportion to their size. Ticks older than this before a transfer get none of it (the bank was robbed, or there was no ESS). |
| `PILOTSTATS_ZKILL_AUTO_STATES` | `["Member"]` | Auth states whose mains' corporations and alliances are tracked on zKill automatically. |
| `PILOTSTATS_R2Z2_MAX_PER_RUN` | `300` | zKill sequence files read per run of `drain_r2z2`. |
| `PILOTSTATS_R2Z2_REQUEST_INTERVAL` | `0.15` | Seconds between zKill requests (their limit is 15 a second per IP). |
| `PILOTSTATS_USER_AGENT` | `aa-pilotstats/<version> (+repo URL)` | User-Agent for zKill and the SDE. zKill asks for a real one; you can add your site's contact. |

## Upgrading from PvE Tracker

Pilot Stats takes over a PvE Tracker install in place. Its first migration renames the `pvetracker_*` tables to `pilotstats_*` with every row, moves the permission so existing `basic_access` group grants keep working, and removes PvE Tracker's migration history. The database index and constraint names keep their old `pvetracker_` prefix; that's expected.

1. Back up the database.
2. Swap the package:

   ```bash
   pip uninstall aa-pvetracker
   pip install aa-pilotstats
   ```

   On Docker, change `aa-pvetracker` to `aa-pilotstats` in `conf/requirements.txt` and rebuild the image.
3. In `local.py`, replace `"aa_pvetracker"` with `"aa_pilotstats"` in `INSTALLED_APPS`. If both are listed, Auth refuses to start with check `pilotstats.E001`.
4. Run migrations and collect static files, then restart Auth:

   ```bash
   auth migrate
   auth collectstatic --noinput
   ```

Old links keep working: `/pvetracker/...` redirects permanently to `/pilotstats/...`.

## Development

The SQLite harness (`manage.py`, `settings_dev.py`, `urls_dev.py`) runs the parser tests, the leaderboard-helper tests, and the Django view tests. Alliance Auth's own system checks need a Redis server and the placeholder settings in `settings_dev.py` (every URL there is `https://example.com`; nothing in that file is a credential).

```bash
python -m pip install "allianceauth==5.6.0"
python -m pip install -e . --no-deps

# no database: parser + roll-up helper
python -m unittest aa_pilotstats.tests.test_parsing aa_pilotstats.tests.test_leaderboard

# full suite, including views (Redis on localhost:6379)
python manage.py test aa_pilotstats

# migration matches the models:
python manage.py makemigrations pilotstats --check --dry-run
```

`settings_dev.py` is not a production settings file. Confirm the pages against your real Alliance Auth theme in the Auth container when you care about Bootswatch colors; the nested leaderboard detail table is styled with the app's own classes for that reason.

GitHub Actions runs `python manage.py test aa_pilotstats` and the `makemigrations --check` step on Python 3.12 with Redis 7.4:
- on SQLite, against the oldest supported Alliance Auth (5.0.0, with django-esi 9.0.0 and the newest) and 5.6.0;
- on MariaDB 10.11 and 11.8 with Alliance Auth 5.6.0, since real sites run MariaDB or MySQL.

To run the suite on MariaDB or MySQL locally, point `settings_dev.py` at a throwaway server with environment variables (the database user needs to create the `test_` database):

```bash
docker run -d --rm --name pilotstats-test-db -e MARIADB_ROOT_PASSWORD=example-test-password -p 3306:3306 mariadb:11.8
PILOTSTATS_TEST_DB=mysql python manage.py test aa_pilotstats
```

`PILOTSTATS_DB_HOST`, `PILOTSTATS_DB_PORT`, `PILOTSTATS_DB_USER`, `PILOTSTATS_DB_PASSWORD` and `PILOTSTATS_DB_NAME` override the defaults (`127.0.0.1`, `3306`, `root`, `example-test-password`, `pilotstats`).
