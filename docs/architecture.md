# Architecture

## Overview

```
                    ┌──────────── generator container (python -m mscal run --loop) ───────────┐
config.yaml ───────►│ collectors ──► post-process ──► stream resolver ──► ics renderer         │
manual.yaml         │  motocal         dedupe          YouTube RSS+API      TZID + VTIMEZONE     │──► /www/motorsport.ics
                    │  wrc             prune past      static rules         CATEGORIES, GEO      │──► /www/status.json
                    │  jsonld          circuit labels                       SEQUENCE from state  │
                    │  manual                                                                    │
                    │        ▲ last-good output, revisions, caches: /data (state volume)         │
                    └────────────────────────────────────────────────────────────────────────────┘
                                                       │ shared volume (read-only)
                                                 nginx :8080 ──► reverse proxy / Cloudflare Tunnel ──► iCloud, Proton, ...
```

The generator never talks to calendar providers. It only writes a file, and every
client pulls it. Because of that, one feed serves any number of calendars and
people, and there are no credentials to manage.

## One run, step by step (`mscal/main.py: run_once`)

1. **Build collectors.** Each block under `collectors:` becomes one instance of
   the class chosen by `type:`. Its built-in defaults (`Collector.defaults`) are
   merged under the user's keys. Every tracked circuit with a `calendar:` block adds
   a `jsonld` collector named `circuit-<key>`.
2. **Collect.** Each collector runs once per season. The seasons are the current
   year, plus the next year from October on, when new calendars get published.
   - If a collector produced data for at least one season, that output is saved as
     its *last good* copy.
   - If it produced nothing (it raised, or there are no results), its last good copy
     is used instead, as long as it is newer than `stale_max_days`.
3. **Post-process** (`postprocess`).
   - Sessions are de-duplicated by UID, and the first collector in config order wins.
   - Events that ended more than `keep_past_days` ago are dropped.
   - All-day placeholders are hidden once timed sessions of the same series exist on
     those days.
   - Tracked-circuit labels and coordinates are applied.
4. **Streams** (`streams.py`). For each session without a link, the per-series rules
   are tried in order. See [streaming.md](streaming.md).
5. **Render** (`ics_out.py`). This produces one VCALENDAR. Each session gets its
   revision from the state, and the file is replaced atomically, only when its bytes
   changed. `status.json` is always rewritten, which also feeds the container
   health check.
6. **Sleep.** The pause is `interval_minutes`, or `race_weekend_interval_minutes`
   when a session starts within `race_weekend_window_hours`. Both config files are
   re-read before the next run.

### Keeping the Raspberry Pi idle

- **No resident heavyweight process.** `run --loop` starts a supervisor
  (`mscal/supervisor.py`) that imports only the standard library (about 15 MB). Each
  run happens in a child process that loads the collectors, works, and exits, so its
  memory (about 55–80 MB) is returned after every run.
- **Fetch only what can change.** Before collecting, each source is checked:
  if it was refreshed successfully within `idle_refresh_hours`, its config is
  unchanged, and none of its sessions is running or starting soon, its cached
  output is reused and no request is made. Race weekends stay fresh, and quiet
  weeks cost almost nothing. `status.json` marks such sources `"skipped": "fresh"`.
- **Precompiled bytecode.** The image compiles all Python modules at build time. The
  container is read-only, so otherwise every run would recompile from source.
- The run writes `next_run_minutes` into `status.json`, and the supervisor sleeps
  exactly that long.

## Data model (`mscal/models.py`)

Every collector emits `Session` objects:

| field | meaning |
|---|---|
| `series`, `championship`, `discipline` | labels: `WEC`, `FIA World Endurance Championship`, `Endurance` |
| `session_type` | `FP`, `QUALI`, `RACE`, `STAGE`, `SHAKEDOWN`, `EVENT` (all-day placeholder), `OTHER` |
| `name`, `event_name` | `Hyperpole`, `8 Hours of Bahrain` |
| `start`, `end`, `tz` | aware datetimes in the venue zone, or dates for all-day events (`end` exclusive) |
| `location`, `geo` | text location and `(lat, lon)` |
| `info_url`, `stream_url`, `stream_label` | links |
| `key` | stable identity within `series` + `season` (e.g. `r5|QUALIFYING|Hyperpole`) |
| `extra_labels` | e.g. `Monza` for tracked circuits |

## Identity and updates

A calendar client recognises an event by its **UID**. For subscribed calendars it
also uses **SEQUENCE** and **LAST-MODIFIED** to decide whether to replace its copy.

- `UID = sha1(series | season | key)`. The key must never contain anything that
  changes when a session is rescheduled, so a moved session updates in place
  instead of duplicating.
- `state.py` stores a content hash per UID. When the hash changes, `SEQUENCE` is
  incremented and `LAST-MODIFIED` is set.
- `DTSTAMP` is set to the last-modified time, not to the time of the run. An
  unchanged calendar therefore renders to identical bytes, the nginx ETag stays the
  same, and clients polling every few minutes get a cheap `304 Not Modified`.
- A session that disappears from its source is simply absent from the next feed,
  and clients delete it. A session marked cancelled stays, with `STATUS:CANCELLED`.

## Persistent state (`/data`)

| path | content |
|---|---|
| `revisions.json` | per-UID hash, sequence, created/modified/last-seen (pruned after 400 days) |
| `kv.json` | caches: geocoding results (forever), YouTube channel ids and matched videos |
| `collectors/<name>.json` | last good output per collector |
| `http-cache/` | HTTP cache used by the motorsport-calendar library |

Deleting `/data` is safe. You lose the fallback copies and the caches, and every
event's `SEQUENCE` restarts at 0. That is harmless, because the UIDs stay the same.
