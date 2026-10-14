# Changelog

## 1.2.1 – 2026-10-14

- Drift Masters streams: the battles (Top 32/16/8) link to Red Bull Motorsports on
  YouTube (`@redbullmotorsports`) instead of Red Bull TV. Qualifying stays on the
  Drift Masters channel. The collector only takes exact YouTube videos from dm.gp;
  everything else goes through the `DM` streaming rules.

## 1.2.0 – 2026-10-14

Lower resource use on the Raspberry Pi.

- `run --loop` is now a standard-library-only supervisor (~15 MB idle, measured) that
  starts a short-lived process per run, instead of a resident ~55–80 MB process.
- Per-source refresh: sources with nothing running or starting soon are served
  from cache for up to `schedule.idle_refresh_hours` (12), with no network requests.
  Config edits force a refresh.
- The image ships precompiled bytecode (the read-only container can't cache it).
- Requests send standard `Accept`/`Accept-Language` headers. Failures for
  next year's not-yet-published calendars are reported as notes, not errors.
- Live check: IMSA is reported but not required (imsa.com returns 403 to GitHub's
  datacenter IPs).

## 1.1.1 – 2026-10-14

Maintenance: latest stable versions everywhere, all verified.

- Python 3.14 (`python:3.14-slim-trixie`, Debian 13) in the image, up from 3.13.
  Tested on 3.12, 3.13, 3.14 and 3.15. 3.15.0 is not used in the image yet: PyYAML
  has no arm64 wheel for it.
- nginx 1.30 stable (`nginx:1.30-alpine`, up from 1.27); cloudflared pinned to
  2026.10.0 instead of `latest`.
- GitHub Actions on their latest majors (Node 24): checkout, setup-python and
  upload-artifact v7; setup-qemu, setup-buildx and login v4; metadata v6;
  build-push v7.
- Runtime dependencies pinned exactly (`requirements.txt`). `beautifulsoup4` and
  `lxml` are now declared, where before they came in only as transitive
  dependencies. Test tools are in `requirements-dev.txt`.
- CI: Python 3.12 + 3.14 required, 3.15 as an early warning; deprecation warnings fail
  the run.
- New test renders a real PDF and parses it through pypdf (the GT Open path).
- Container health check uses JSON (exec) form.

## 1.1.0 – 2026-10-14

- New collector `creventic`: Michelin 24H Series from 24hseries.com, with per-race
  timetables, Start/Finish pairing for races (including split races), merged
  per-class qualifying, and weekend placeholders until timetables are published.
- New collector `imsa`: IMSA WeatherTech SportsCar Championship from imsa.com, with
  the season selector, ET→venue timezone conversion, races matched by name or
  duration, and the midnight split of the Rolex 24 merged.
- New collector `driftmasters`: Drift Masters from dm.gp, with per-round timetables and
  exact stream links (YouTube for qualifying, Red Bull TV for the battles).
- New collector `formuladrift`: Formula DRIFT from formulad.com, with practice,
  qualifying, Top 32/16 and the livestream link.
- New collector `gtopen`: International GT Open; the calendar comes from the site,
  and sessions from the next-race schedule PDF (kept per round after the weekend).
  Adds `pypdf`.
- Stream channels checked against the official sites: `@GTOPENseries`,
  `@IMSAOfficial`, `user:24HSeries`, `@DriftMastersGrandPrix`.
- WRC: switched to the API behind wrc.com's results pages (the old `api.wrc.com` is
  gone). Uses the event's own timezone when it is plausible, with placeholders
  detected. Unpublished rallies become date placeholders.
- New collector `tribe` (WordPress "The Events Calendar" REST API). Monza now
  reads monzanet.it's race category (12 races in 2026) instead of scraping pages.
- `stale_max_days: 0` keeps a failing collector's data until each season is over.
- `log_level` in config.yaml.
- Streaming rules accept legacy YouTube usernames (`user:Name`).
- Tests against pages captured from the live sites.

## 1.0.0 – 2026-10-14

First release.

- One "Motorsport" iCalendar feed with series, championship, race type, session type
  and circuit labels.
- Collectors:
  - `motocal`: WEC, ELMS, GT World Challenge Europe/America/Asia, IGTC,
    MotoGP/Moto2/Moto3, F1 and others via the motorsport-calendar library.
  - `wrc`: per-stage WRC events.
  - `jsonld`: circuit event calendars.
  - `manual`: hand-written sessions in `manual.yaml`.
- Tracked circuits (Monza by default): labels and exact coordinates for every
  session held there, with an optional scrape of the circuit's own calendar.
- Streaming links: YouTube live-video matching (RSS + 1 API unit per channel), and
  static rules (Rally.TV, FIAWEC+).
- Stable UIDs, `SEQUENCE` bumps on change, last-good fallback per collector, and a
  `status.json` health report.
- `validate` and `providers` commands.
- Docker Compose stack for Raspberry Pi (arm64), nginx, and an optional Cloudflare
  Tunnel.
