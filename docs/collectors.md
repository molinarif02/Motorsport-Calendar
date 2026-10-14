# Collectors

A collector turns one data source into `Session` objects (see
[architecture.md](architecture.md)). Four types are built in.

| type | module | sources | reliability |
|---|---|---|---|
| `motocal` | `mscal/collectors/motocal.py` | [motorsport-calendar](https://github.com/naviss29/motorsport-calendar) library: fiawec.com and ACO sites (JSON-LD), SRO timetables (HTML), Dorna Pulselive API, OpenF1/Jolpica, f1calendar dataset | maintained upstream; update the pinned version to pick up fixes |
| `wrc` | `mscal/collectors/wrc.py` | the JSON API behind wrc.com's results/itinerary pages | API checked 2026-10 (the old `api.wrc.com` is gone) |
| `creventic` | `mscal/collectors/creventic.py` | 24hseries.com race listing + per-race timetables (HTML) | site markup, checked 2026-10 |
| `imsa` | `mscal/collectors/imsa.py` | imsa.com season pages + event schedules (HTML) | site markup, checked 2026-10 |
| `driftmasters` | `mscal/collectors/driftmasters.py` | dm.gp season page + the site's own JSON API (timetable, streams) | site markup/API, checked 2026-10 |
| `formuladrift` | `mscal/collectors/formuladrift.py` | formulad.com schedule + round pages (HTML) | site markup, checked 2026-10 |
| `gtopen` | `mscal/collectors/gtopen.py` | gtopen.gtsport.es calendar (Next.js payload) + next-race schedule PDF | PDF layout is the weak point |
| `jsonld` | `mscal/collectors/jsonld.py` | schema.org events embedded in web pages | depends on each site's markup |
| `tribe` | `mscal/collectors/tribe.py` | WordPress "The Events Calendar" REST API (Monza) | API checked 2026-10 |
| `manual` | `mscal/collectors/manual.py` | `config/manual.yaml` | exactly as good as what you type |

## Failure handling

`collect()` raises when something is wrong, and an empty result also counts as a
failure. The runner then:

- keeps the collector's **last good output** in the feed, for up to `stale_max_days`
  (or, with `0`, until each cached season is over);
- reports the failure in `/www/status.json`:

```json
{"generated": "2026-10-14T13:00:00+00:00", "events": 412,
 "collectors": {"wrc": {"ok": false, "sessions": 96, "errors": ["2026: HTTPStatusError: ..."], "using_cache": true}}}
```

A scraper that breaks therefore never silently empties the calendar. Monitor
`status.json`, for example with an Uptime Kuma JSON-query monitor on
`collectors.*.ok`.

## Per-collector notes

### motocal

- Upstream session types map to ours: `FP1/FP2/FP3/FREE_PRACTICE/TEST → FP`,
  `QUALIFYING/HYPERPOLE/SPRINT_QUALIFYING → QUALI`, `RACE/SPRINT → RACE`, and anything
  else → `OTHER`.
- The UID key is `round | upstream type | title`, so a renamed round or session
  becomes a new event.
- `imsa` and `worldsbk` are registered upstream but have no working source in
  0.3.0. The `providers` command marks them as not being in the catalog.
- To upgrade the library, bump `motorsport-calendar==` in `requirements.txt`, run
  the tests, and rebuild.

### wrc

- wrc.com moved to Red Bull's platform in 2026 and the old `api.wrc.com` endpoints
  stopped working. The results widget's API is used now: `seasons.json`, then
  `season-detail.json`, then `events/{id}.json`, then
  `events/{id}/itineraries/{id}.json`.
- Times come from each stage's `StageStart` control (`firstCarDueDateTimeLocal`,
  with offset).
- The timezone is the event's `timeZoneId` when it is a valid IANA zone in the
  expected region. Unpublished events carry placeholders (the 2027 Monte-Carlo had
  "AUS Eastern Standard Time"); those fall back to the built-in table
  (`rally_timezones` to extend it).
- The stage start place is derived from the control name ("Toudon / Saint-Antonin"
  becomes "Toudon") and geocoded with the country.
- Rallies whose itinerary isn't published yet are all-day placeholders over the
  event dates.

### creventic (24H Series)

- The race listing at `/races` gives each round's dates and venue. Each race page
  has a "Time Schedule" with track times and the IANA zone.
- The race is the `Start <race>` row, ending at the next `Finish` row. Races split
  into parts (some 12H events) become one event per part.
- Qualifying is run per class in 12-minute slots. With `merge_qualifying: true`
  (the default), consecutive slots become one "Qualifying (all classes)" event.
- Track days, the grid walk, parade and podium are not sessions and are dropped.
- Races whose timetable isn't published yet appear as an all-day `[Event]` over the
  weekend. It is replaced automatically when the timetable appears.

### imsa (WeatherTech)

- Season page URLs differ between years, so they are read from the season
  selector. Pin one with `season_urls: {2027: https://...}` if needed.
- Event pages list every IMSA series. `series_regex` (default `WeatherTech`) picks
  practice and qualifying. The race row carries only the race name, so it is
  matched by name or by the duration from the season card.
- The site lists all times in ET. They are converted to the venue's zone, derived
  from its state or province. Use `venue_timezones: {"Venue name": "Area/City"}`
  for venues outside the built-in table.
- Races that cross midnight (Rolex 24) are listed as two rows and are merged.
- If an event page is missing, the round becomes an all-day placeholder from the
  season card.

### driftmasters

- The season page lists rounds (dates, circuit, country). Each round page holds
  the IDs the site's front end uses for its JSON API: `/event` gives the
  timetable, and `/live` gives the stream links.
- Timetable times are read as local track time. The API marks them "Z", but the
  values only make sense locally (Warsaw qualifying at 17:30, gates closing 23:00).
- "Qualification …" lines are qualifying, and "TOP 32/16/8 … Battles" lines are the
  competition, labelled `Race`. A session ends at the next timetable line.
- Stream links come from `/live`: the exact YouTube video for qualifying, and Red
  Bull TV for the battles.

### formuladrift

- `/schedule` links the season's rounds. Round pages carry the schedule
  (`dl` rows per day, local time) and the venue address, whose state gives the
  timezone.
- Practice rows are free practice. Qualifying and the Top 32 / Top 16 battles
  appear as "LIVESTREAM: <link> (…)" rows; the link becomes the stream URL.

### gtopen

- The season comes from the calendar JSON embedded in `/calendario`.
- Session times exist only in the PDF linked from the homepage's "next race" box,
  which mixes every GT Sport series. The parser keeps rows labelled
  `series_name` (default `International GT Open`). For race rows that carry the
  series name on the neighbouring line, it looks at that line instead.
- Parsed sessions are stored per round, so they stay in the calendar after the
  weekend. Other rounds are Friday-to-race-day placeholders.
- Rounds matching `skip_regex` (default: tests) are ignored.
- If the PDF layout changes, the log says "had no International GT Open sessions",
  and the round falls back to its placeholder.

### tribe (circuit calendars)

- Many circuit websites use the WordPress plugin "The Events Calendar", which
  exposes `/wp-json/tribe/events/v1/events`. Monza files races under the category
  `gare`, separate from tours, track days, tests and cycling events.
- Monza's event times are gate hours (09:00–18:00), not sessions, so events become
  all-day entries over their dates. `series_map` tags known series; those
  placeholders hide automatically when that series' collector has timed sessions
  on those days.
- To track another circuit on the same plugin, add
  `calendar: {type: tribe, api: https://<site>/wp-json/tribe/events/v1/events}`. List
  its categories at `/wp-json/tribe/events/v1/categories`.

### jsonld

- Only pages that embed `<script type="application/ld+json">` with `Event` objects
  can be read. If a circuit's site doesn't provide any, use `manual.yaml` for its
  events and keep the circuit as label-only tracking.
- `follow_links_regex` lets it crawl from a listing page to event detail pages.
  `max_pages` caps the work per run.

## Writing a new collector

1. Create `mscal/collectors/myseries.py`:

   ```python
   # SPDX-License-Identifier: GPL-3.0-or-later
   """My Series: where the data comes from and any caveats."""
   from datetime import datetime, timedelta
   from zoneinfo import ZoneInfo

   from ..models import Session, SessionType
   from .base import Collector


   class MySeriesCollector(Collector):
       required = ("series", "url")

       @classmethod
       def defaults(cls, cfg):
           return {"series": "MYS", "championship": "My Series", "discipline": "GT",
                   "url": "https://example.org/api/schedule"}

       def collect(self, season):
           r = self.http.get(self.cfg["url"], params={"year": season})
           r.raise_for_status()                    # raising = keep last good data
           out = []
           for ev in r.json()["events"]:
               tz = ev["timezone"]
               for s in ev["sessions"]:
                   start = datetime.fromisoformat(s["start"]).astimezone(ZoneInfo(tz))
                   out.append(Session(
                       series=self.series, championship=self.championship,
                       discipline=self.discipline, season=season,
                       event_name=ev["name"], session_type=SessionType.RACE,
                       name=s["name"], start=start, end=start + timedelta(minutes=s["minutes"]),
                       tz=tz, location=ev["venue"],
                       key=f"{ev['id']}|{s['id']}",      # stable, no times in it!
                   ))
           if not out:
               raise RuntimeError("nothing parsed")
           return out
   ```

2. Register it in `mscal/collectors/__init__.py` by adding
   `"myseries": MySeriesCollector` to `TYPES`.
3. Enable it with `myseries: {type: myseries}` in `config.yaml`, and add streaming
   rules for its tag `MYS`.
4. Add a test using `httpx.MockTransport` with a recorded response (see
   `tests/test_pipeline.py`).

Guidelines:

- Use `self.http` (it already has a timeout and User-Agent set) and `self.geo.lookup()`
  for coordinates.
- Times must be timezone-aware and in the venue's zone, with `tz` set to its IANA name.
- Return only the requested `season`.
- Build `key` from ids, never from times.
- Be polite: fetch as little as possible, and cache through `self.state.kv_get/kv_set`
  where it helps.
