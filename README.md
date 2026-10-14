# Motorsport Calendar

One calendar, **Motorsport**, with every session of the championships you follow:
WEC, WRC (every special stage), GT World Challenge, GT Open, 24H Series, drift
series, and everything that happens at the circuits you track (Monza by default).
Each session has its correct local time, the circuit or stage-start location, and a
link to the live stream.

It is published as a standard iCalendar (`.ics`) feed, so it works with **iCloud,
Proton Calendar, Google Calendar, Outlook, Thunderbird**, and anything else that can
subscribe to a URL. It updates itself and runs comfortably on a **Raspberry Pi** with
Docker Compose.

```
[WEC] [Qualifying] Hyperpole – 8 Hours of Bahrain        Sat 13:00 · Bahrain International Circuit
[WRC] [Stage] SS5 Ouninpohja 1 (23.4 km) – Rally Finland  Fri 08:05 · Stage start: Ouninpohja, Finland
[GTWCE] [Race] Race 1 – Misano                           Sat 17:30 · Misano World Circuit · ▶ YouTube
```

## Features

- **One calendar**, labelled per event: series, championship, race type and session
  type in the title and in `CATEGORIES`, plus the circuit label for tracked circuits.
- **Correct times**: every event carries its venue's timezone (with embedded
  VTIMEZONE data), so your calendar shows it in your own time.
- **WRC per stage**: one event per special stage, located at the stage start.
- **Streaming links**: the exact YouTube live video when the channel has scheduled
  it, otherwise the channel's streams page, Rally.TV (WRC) or FIAWEC+ (WEC).
- **GT and circuit series** are filtered to Free Practice, Qualifying and Race.
- **Self-updating**: checks every 3 h, and every 30 min on race weekends. Changed
  sessions update in place (stable UIDs + `SEQUENCE`), and cancelled ones are marked.
- **Robust**: a broken scraper keeps serving its last good data and is reported in
  `status.json`.
- **Configurable**: enable MotoGP / Moto2 / Moto3, GT World Challenge America and
  Asia, IGTC, ELMS, F1 and more with one line each, and track additional circuits.
- **Small**: about 50 MB of dependencies on python:3.13-slim (arm64 and amd64), and a
  32 MB nginx container serves the feed.

## Quick start (Raspberry Pi / any Docker host)

```bash
git clone https://github.com/molinarif02/Motorsport-Calendar.git
cd Motorsport-Calendar
$EDITOR config/config.yaml                         # contact e-mail, FIAWEC+ link, championships
echo "YOUTUBE_API_KEY=" > .env                       # optional, see docs/streaming.md
docker compose pull || docker compose build          # prebuilt image from GHCR, or build locally
docker compose run --rm generator validate           # check the config offline
docker compose up -d
curl -s localhost:8080/status.json
```

Then make `http://<pi>:8080/motorsport.ics` reachable over HTTPS (for example with
the bundled Cloudflare Tunnel) and subscribe to it. Both steps are covered in
[docs/deployment.md](docs/deployment.md) and [docs/subscribing.md](docs/subscribing.md).

## Adding championships and circuits

```yaml
collectors:
  motogp: {type: motocal, provider: motogp}          # MotoGP
  gtwca:  {type: motocal, provider: gtwc-america}    # GT World Challenge America

tracked_circuits:
  imola: {label: Imola, match: [imola], geo: [44.3439, 11.7167]}
```

`docker compose run --rm generator providers` lists every championship the bundled
library supports. See [docs/configuration.md](docs/configuration.md) for all the options.

## Documentation

| | |
|---|---|
| [Architecture](docs/architecture.md) | how a run works, data model, UIDs and change detection |
| [Configuration](docs/configuration.md) | every key of `config.yaml` and `manual.yaml` |
| [Collectors](docs/collectors.md) | the data sources, their caveats, and how to write a new one |
| [Streaming](docs/streaming.md) | how stream links are chosen, YouTube API key setup |
| [Deployment](docs/deployment.md) | Docker Compose, Raspberry Pi, HTTPS, updates, monitoring |
| [Subscribing](docs/subscribing.md) | iCloud, Proton, Google, Outlook, Thunderbird |
| [Development](docs/development.md) | local setup, tests, CI, releasing |

## Data sources and credits

- WEC, ELMS, GT World Challenge, IGTC, MotoGP, F1 and other championships come via
  [motorsport-calendar](https://github.com/naviss29/motorsport-calendar) by Alan
  Yvenou (MIT), used as a library.
- WRC itineraries come from the public endpoints of the WRC live-timing service.
  These endpoints are undocumented and may change.
- Coordinates come from [OpenStreetMap Nominatim](https://nominatim.org) (© OpenStreetMap
  contributors, ODbL).
- This project is not affiliated with the FIA, WRC Promoter, SRO, ACO, Dorna, or any
  circuit or series. Schedules are scraped from public pages, so always check the
  official timetable before travelling.

## License

Copyright (C) 2026 Federico Molinari

This program is free software: you can redistribute it and/or modify it under the
terms of the **GNU General Public License** as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.
It is distributed in the hope that it will be useful, but WITHOUT ANY WARRANTY; see
[LICENSE](LICENSE) for details.
