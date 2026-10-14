# Changelog

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
