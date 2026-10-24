# Configuration reference

The configuration folder (`config/`, or `config.local/` with `CONFIG_DIR`, see
[deployment.md](deployment.md#updating)) is mounted read-only at `/config`:

- `config.yaml`: everything below.
- `manual.yaml`: optional hand-written sessions, only if you add the `manual`
  collector (see [Manual events](#manual-events)).

Both are re-read before every run. To check them offline without touching anything:

```bash
docker compose run --rm generator validate     # prints the plan and any errors
docker compose run --rm generator providers    # championships available to `motocal`
```

## Top level

| key | default | |
|---|---|---|
| `log_level` | `info` | `debug`, `info`, `warn`, `error` or `crit` (case-insensitive). Applied on the next run, no restart needed. If unset, the `LOG_LEVEL` environment variable is used. HTTP request lines appear only at `debug`. |
| `contact` | `admin@example.com` | sent in the User-Agent. OpenStreetMap requires a contact; use a real address. |
| `geocoding` | `true` | look up coordinates of venues and WRC stage starts. Results are cached forever. |
| `stale_max_days` | `14` | how long a failing collector may serve its last good data. `0` means no age limit: each season's cached sessions stay until that season is over (its last session has ended). |
| `seasons` | auto | e.g. `[2026, 2027]`. By default it is the current year, plus next year from October. |

## `output`

| key | default | |
|---|---|---|
| `filename` | `motorsport.ics` | file name under `/www` (also the URL path) |
| `calendar_name` | `Motorsport` | name shown when subscribing |
| `description` | … | calendar description |
| `display_timezone` | `Europe/Rome` | `X-WR-TIMEZONE` hint. Events always carry their own zone. |
| `color` | none | initial colour (Apple, RFC 7986 `COLOR`) |
| `refresh_minutes` | `60` | refresh hint for clients. Most clients use their own setting. |
| `keep_past_days` | `45` | how long finished events stay in the feed |
| `alarm_minutes` | `0` | if greater than 0, adds a reminder that many minutes before every qualifying and race |
| `per_calendar` | `true` | also write one calendar per championship and per tracked circuit, under `/calendars/` (see below) |

### Per-championship calendars

Besides the combined `motorsport.ics`, each run writes `calendars/<name>.ics`:

- one per **championship you have configured**: the series tag of each enabled
  collector (`wec.ics`, `gtwce.ics`, `wrc.ics`, `gtopen.ics`, `24h.ics`, `dm.ics`,
  `fd.ics`…). Entries in `manual.yaml` go into the file of the series they're tagged
  with; a series that only exists in `manual.yaml` gets its own file. Disabled
  championships get no file, and a file is deleted when its championship is removed.
- one per **tracked circuit** (`monza.ics`), with every session held there, whatever
  the series, including events from the circuit's own list (F1, Lamborghini…). Series
  that come only from a circuit's list get no championship file.

They are named "Motorsport – <championship>", carry no colour (so each app picks a
different one), and use the same event UIDs as the combined feed. The status page
links each source to the calendars it feeds, and `status.html?calendar=<name>` shows
one calendar's own page with its Subscribe button. Subscribe to either the combined
feed or the separate ones, not both, or you'll see every event twice. Cost: about half
a second more per run on a Raspberry Pi 5, and a few hundred KB of disk.

## `schedule`

| key | default | |
|---|---|---|
| `interval_minutes` | `180` | normal time between runs |
| `race_weekend_interval_minutes` | `30` | used when any session starts within the window |
| `race_weekend_window_hours` | `72` | the window used above |
| `idle_refresh_hours` | `12` | a source with no session running or starting inside the window (all-day placeholders: within 7 days) is re-fetched at most this often; its cached data is reused in between. A config change to that source forces a refresh. `0` = fetch everything on every run. |

## `collectors`

Each key is a name you choose. It appears in logs, in `status.json` and in cache
file names. Each value is a block with a `type`, plus that type's options. Every
block accepts these common keys:

| key | |
|---|---|
| `type` | `motocal`, `wrc`, `creventic`, `imsa`, `driftmasters`, `formuladrift`, `gtopen`, `tribe`, `jsonld` or `manual` |
| `enabled` | `false` to switch it off without deleting it |
| `series` | short tag used in titles: `[WEC]` |
| `championship` | full name, used as a label |
| `discipline` | race type label: `Endurance`, `GT`, `Rally`, `Motorcycle`, … |
| `info_url` | link used when no stream link exists |

The built-in defaults fill in `series`, `championship`, `discipline` and `info_url`
for every known championship. Anything you write overrides them.

### `type: motocal`

This type gives access to any championship of the
[motorsport-calendar](https://github.com/naviss29/motorsport-calendar) library.

| key | default | |
|---|---|---|
| `provider` | required | `wec`, `elms`, `mlmc`, `gtwc-europe`, `gtwc-america`, `gtwc-asia`, `igtc`, `motogp`, `moto2`, `moto3`, `formula1`, `formula2`, `formula3`, `f1-academy`, `formula-e` |
| `source` | from catalog | upstream source name (see `providers`) |
| `session_types` | `[FP, QUALI, RACE]` | sessions to keep. `FP` includes tests; `RACE` includes sprints. |
| `http_cache_minutes` | `50` | upstream HTTP cache lifetime |

```yaml
collectors:
  motogp: {type: motocal, provider: motogp}
  moto3:  {type: motocal, provider: moto3, session_types: [QUALI, RACE]}
  f1:     {type: motocal, provider: formula1, source: jolpica}   # alternative upstream source
```

### `type: wrc`

| key | default | |
|---|---|---|
| `api_base` | `https://p-p.redbull.com/rb-wrccom-lintegration-yv-prod/api` | wrc.com's results API |
| `season_name` | `World Rally Championship` | season to read (e.g. `European Rally Championship`) |
| `stage_window_minutes` | `90` | event length (the API has no stage end time) |
| `include_shakedown` | `true` | |
| `rally_timezones` | built-in table | fallback `fragment: Area/City` entries when the API's zone is missing or a placeholder |

### `type: creventic` (24H Series)

| key | default | |
|---|---|---|
| `base_url` | `https://www.24hseries.com` | |
| `merge_qualifying` | `true` | merge per-class qualifying slots into one event |
| `qualifying_gap_minutes` | `30` | maximum gap between slots that get merged |
| `session_types` | `[FP, QUALI, RACE]` | |

### `type: imsa`

| key | default | |
|---|---|---|
| `season_index_url` | 2026 WeatherTech schedule page | any season page; its season selector gives the other years |
| `season_urls` | none | `{2027: https://...}` to pin a season page |
| `series_regex` | `WeatherTech` | which series' practice/qualifying rows to keep |
| `venue_timezones` | built-in by US state / province | `{"Venue": "Area/City"}` overrides |
| `session_types` | `[FP, QUALI, RACE]` | |

### `type: driftmasters`

| key | default | |
|---|---|---|
| `base_url` | `https://dm.gp` | |
| `season_slug` | `drift-masters-{year}` | season page slug |
| `country_timezones` | built-in | `{ISO3: zone}` overrides, e.g. `{GBR: Europe/London}` |
| `session_types` | `[FP, QUALI, RACE]` | `RACE` = the battles |

### `type: formuladrift`

| key | default | |
|---|---|---|
| `base_url` | `https://www.formulad.com` | |
| `venue_timezones` | by US state | `{"Venue": "Area/City"}` overrides |
| `session_types` | `[FP, QUALI, RACE]` | `RACE` = Top 32 / Top 16 |

### `type: gtopen`

| key | default | |
|---|---|---|
| `base_url` | `https://gtopen.gtsport.es` | |
| `series_name` | `International GT Open` | category label in the PDF to keep |
| `skip_regex` | `\btest\b` | rounds to ignore |
| `country_timezones` | built-in (EN/ES names) | `{country: zone}` overrides |
| `session_types` | `[FP, QUALI, RACE]` | |

### `type: tribe`

| key | default | |
|---|---|---|
| `api` | required | `https://<site>/wp-json/tribe/events/v1/events` |
| `categories` | all | category slugs to keep, e.g. `[gare]` |
| `all_day` | `true` | all-day entries over the event dates (gate hours aren't sessions) |
| `series_map`, `skip_regex`, `tz`, `location`, `geo`, `extra_labels` | | as for `jsonld` |

### `type: jsonld`

This type reads schema.org `Event` / `SportsEvent` objects embedded as JSON-LD in web
pages. It is normally generated from `tracked_circuits` (see below), but you can
also use it directly.

| key | default | |
|---|---|---|
| `urls` | required | listing page(s) |
| `follow_links_regex` | none | also fetch linked pages whose URL matches |
| `max_pages` | `40` | crawl limit per run |
| `series_map` | none | `regex: SERIES` to tag events by name |
| `skip_regex` | none | ignore events whose name matches |
| `tz`, `location`, `geo` | | venue defaults |
| `extra_labels` | `[]` | labels added to every event |
| `fail_if_empty` | `true` | treat "nothing found" as a failure, which keeps the cached data |

Events that have only dates become all-day `[Event]` entries. Events with times keep
them.

### `type: manual`

| key | default | |
|---|---|---|
| `file` | `/config/manual.yaml` | path inside the container |

Optional, and not in the default `config.yaml`. See [Manual events](#manual-events).

## `tracked_circuits`

A tracked circuit adds its label (for example `Monza`) and its exact coordinates to
**every** session held there, whichever series it belongs to. Optionally, it can also
scrape the circuit's own event list.

| key | default | |
|---|---|---|
| `label` | the key, title-cased | label added to matching sessions |
| `match` | `[<key>]` | case-insensitive words looked for in a session's location and event name |
| `name` | label | full name (used as the championship of scraped events) |
| `tz` | `Europe/Rome` | |
| `geo` | none | `[lat, lon]`. Replaces geocoded coordinates for matching sessions. |
| `location` | label | address for scraped events |
| `calendar` | none | the circuit's own event list: `type: tribe` (WordPress events API) or `type: jsonld` (default), plus that type's options |

```yaml
tracked_circuits:
  imola:   {label: Imola, match: [imola, enzo e dino ferrari], geo: [44.3439, 11.7167]}
  mugello: {label: Mugello, geo: [43.9975, 11.3719]}
```

Keep `match` words specific. A word that is too short can match unrelated event
names, which is why `spa-francorchamps` is safer than `spa`.

## `streaming`

See [streaming.md](streaming.md).

## Manual events

For anything no collector covers: a series without a data source, a one-off event,
or a fix while a scraper is broken. It's **optional, and off by default**: the shipped
`config.yaml` has no `manual` collector.

**If you need it**, add the collector to `config.yaml` and create `manual.yaml` next
to it (start from the example):

```yaml
collectors:
  # ...your other collectors...
  manual:
    type: manual
    file: /config/manual.yaml       # path inside the container
```

```bash
cp config/manual.example.yaml config.local/manual.yaml   # then replace the example entries
docker compose run --rm generator validate
```

**If you don't need it (anymore)**, remove the `manual:` block from `config.yaml`;
the `manual.yaml` file can then go too. If you only delete or empty the file and keep
the block, nothing breaks: the source adds no events and is reported as OK with 0
sessions, but `validate` will point out the missing file.

The file format (times are local to each entry's `tz`):

```yaml
events:
  - series: GTOPEN                 # tag; also selects the streaming rules
    championship: International GT Open
    discipline: GT
    event: Monza
    tz: Europe/Rome
    location: Autodromo Nazionale Monza, Italy
    geo: [45.6156, 9.2811]         # optional
    stream_url: https://...        # optional, overrides streaming rules
    labels: [Season finale]        # optional extra labels
    sessions:
      - {type: FP,    name: FP1,    start: "2026-10-16 10:30", minutes: 60}
      - {type: QUALI, name: Q1,     start: "2026-10-17 09:00", minutes: 30}
      - {type: RACE,  name: Race 1, start: "2026-10-17 15:05", minutes: 70}

  - series: DM
    championship: Drift Masters
    discipline: Drift
    event: Round 5 – Warsaw
    tz: Europe/Warsaw
    dates: ["2026-08-29", "2026-08-30"]     # all-day placeholder until times are known
```

A session's identity is `series + event + session name`. If you change a start time,
the event updates in calendars. If you rename the session or the event, calendars see
a different event.
