# Development

## Setup

```bash
python3.14 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
```

Python 3.12 or newer is required; 3.12 to 3.15 are tested and the container uses 3.14.
Runtime dependencies are pinned exactly in `requirements.txt`, and test tools in
`requirements-dev.txt`.

## Running locally

```bash
python -m mscal validate  --config config/config.yaml
python -m mscal providers --config config/config.yaml
python -m mscal run       --config config/config.yaml --data ./data --out ./www
```

The default `config.yaml` has no `manual` collector. If you add one for local runs,
point `file` at a local path (in the container it is `/config/manual.yaml`).

## Tests

The tests run fully offline. Each source is faked with `httpx.MockTransport`, and the
real pipeline runs against the fakes.

| file | covers |
|---|---|
| `tests/test_pipeline.py` | full run: WRC stages and timezones, Monza JSON-LD, manual entries, YouTube matching, labels, stable UIDs and SEQUENCE bumps, byte-identical output, fallback to cached data |
| `tests/test_collectors_live_markup.py` | 24H Series, IMSA, Drift Masters, Formula DRIFT and GT Open collectors against pages, API responses and PDF text captured from the live sites (`tests/fixtures/`) |
| `tests/test_motocal.py` | the motocal adapter against fake upstream sources: cache handed to sources that ignore it, next-season copies from year-blind sources dropped |
| `tests/test_config.py` | catalog defaults, enabling extra championships, overrides, tracked circuits, `validate` |

## Continuous integration (`.github/workflows/`)

| workflow | trigger | what it does |
|---|---|---|
| `ci.yml` | push, pull request | lint (ruff) and tests on Python 3.12 and 3.14 (required) and 3.15 (early warning); deprecation warnings fail the run |
| `image.yml` | push to `main`, tags `v*` | builds the multi-arch image (amd64 and arm64) and pushes it to GHCR as `latest` / `<tag>` / `sha-…` |
| `live-check.yml` | weekly + manual | runs the **real** scrapers on a GitHub runner and uploads `motorsport.ics` and `status.json` as artifacts. It fails if a core collector is broken, so you hear about website changes. |

## Versions

| component | version | why |
|---|---|---|
| Python | 3.14 (`python:3.14-slim-trixie`) | latest stable series with arm64 wheels for every dependency (3.15.0 is days old; PyYAML has no arm64 wheel for it yet) |
| Debian | 13 "trixie" | current Debian stable |
| nginx | 1.30 (`nginx:1.30-alpine`) | current nginx stable branch |
| cloudflared | 2026.10.0 | pinned, bump deliberately |
| GitHub Actions | checkout/setup-python/upload-artifact v7, docker actions v4–v7 | latest majors (Node 24) |

To update: bump the pins, run `pytest`, push, and watch the CI and Live check workflows.

## Releasing

1. Update `CHANGELOG.md` and `mscal/__init__.py` (`__version__`).
2. Run `git tag v1.1.0 && git push --tags`. The image workflow publishes `:1.1.0`.

## Code layout

```
mscal/
  main.py            CLI, run pipeline, post-processing, validate/providers
  models.py          Session, SessionType, UID and hashing
  state.py           revisions (SEQUENCE), last-good output, KV cache, atomic writes
  ics_out.py         VCALENDAR rendering
  streams.py         stream link rules, YouTube matching
  collectors/
    base.py          Collector base class, Geocoder
    motocal.py       motorsport-calendar adapter + CATALOG
    wrc.py           WRC per-stage collector
    jsonld.py        schema.org event scraper (tracked circuits)
    manual.py        manual.yaml
```

Every source file starts with an SPDX header (`GPL-3.0-or-later`). Keep it in new
files.
