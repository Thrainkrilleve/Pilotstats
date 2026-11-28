# Roadmap

What's planned for Pilot Stats, roughly in order. Released work is in the [changelog](CHANGELOG.md).

## Next

1. **Leaderboard caching.** The leaderboard is computed on every page load. A short cache, cleared when new data arrives, keeps it fast as months of sessions, payouts and killmails build up.
2. **AA Desktop integration, the app side.** Pilot Stats' half is done (see below). What's left lives in the AA Desktop app: running from the tray with a Pilot Stats panel, and an installer with updates. The in-browser folder scanner on the Upload page stays for everyone else.

## Done, not released yet

These are merged and listed under "Unreleased" in the [changelog](CHANGELOG.md). They ship with the next release.

- **ISK per hour.** Bounty and mission payouts earned during a logged session, divided by its length. Shown on the session page, and as a leaderboard column weighted by logged hours.
- **Damage-over-time chart on the session page.** Per-minute DPS and reps for the session, to show ramp-up, downtime between waves, and when logi was busy.
- **Personal trends.** A "My Stats" page with DPS, ISK/h and rats killed per week, and the pilot's personal records.
- **Activity type per session.** Sessions are classified as ratting, incursions, abyssals, missions or other, so the leaderboard can compare like with like.
- **Corporation and alliance totals.** Totals per corporation or alliance next to the per-pilot leaderboard, plus a CSV export.
- **MariaDB in CI.** The test suite also runs on MariaDB 10.11 and 11.8, not only SQLite.
- **AA Desktop upload endpoint.** An optional hook the AA Desktop app uses to upload new and grown Gamelogs, signed in with the pilot's Alliance Auth login.
