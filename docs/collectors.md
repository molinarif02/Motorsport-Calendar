# Collectors

A collector turns one data source into `Session` objects (see
[architecture.md](architecture.md)). Four types are built in.

| type | module | sources | reliability |
|---|---|---|---|
| `motocal` | `mscal/collectors/motocal.py` | [motorsport-calendar](https://github.com/naviss29/motorsport-calendar) library: fiawec.com and ACO sites (JSON-LD), SRO timetables (HTML), Dorna Pulselive API, OpenF1/Jolpica, f1calendar dataset | maintained upstream; update the pinned version to pick up fixes |
| `wrc` | `mscal/collectors/wrc.py` | WRC live-timing JSON (`api.wrc.com`) | undocumented API, may move between seasons |
| `jsonld` | `mscal/collectors/jsonld.py` | schema.org events embedded in web pages | depends on each site's markup |
| `manual` | `mscal/collectors/manual.py` | `config/manual.yaml` | exactly as good as what you type |

## Failure handling

`collect()` raises when something is wrong, and an empty result also counts as a
failure. The runner then:

- keeps the collector's **last good output** (up to `stale_max_days`) in the feed;
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

- Times come from each stage's `StageStart` control. The `firstCarDueDateTimeLocal`
  field is preferred, and `firstCarDueDateTime` (UTC) is the fallback.
- The rally timezone comes from a built-in table, matched on id, then name, then
  country. A missing entry logs a warning and falls back to UTC; add it with
  `rally_timezones`.
- The stage start place is derived from the name ("Tejeda - Moya 1" → "Tejeda") and
  geocoded with the country.
- If the season endpoint changes, set `events: [{id, name, country}]`. Event ids
  appear in the URLs of wrc.com's results pages.

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
