# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Command line entry point and the main pipeline.

Commands::

    python -m mscal [run] [--loop]   collect -> merge -> label -> streams -> motorsport.ics
    python -m mscal validate         check config.yaml / manual.yaml offline, print the plan
    python -m mscal providers        list championships the motocal collector can enable

One run (:func:`run_once`):

1. build the collectors from ``collectors:`` plus one per ``tracked_circuits`` entry
   that has a ``calendar:`` block;
2. run each collector for every season (current year, plus next year from October);
   a failing collector falls back to its last good output (``stale_max_days``);
3. post-process: dedupe by UID, drop old events, hide all-day placeholders already
   covered by timed sessions, apply tracked-circuit labels and coordinates;
4. attach stream links (:mod:`mscal.streams`);
5. render the calendar (:mod:`mscal.ics_out`); the file is rewritten only if its
   bytes changed, and ``status.json`` reports every collector's health.

``--loop`` repeats this forever, faster when a session is near (``schedule:``).
The config files are re-read before every run, so edits need no restart.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import signal
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import yaml

from .collectors import TYPES, Geocoder
from .ics_out import build_calendar
from .models import Session, SessionType
from .state import State, atomic_write
from .streams import StreamResolver

log = logging.getLogger("mscal")
_stop = False

#: accepted values of ``log_level:`` in config.yaml (case-insensitive)
LOG_LEVELS = {
    "debug": logging.DEBUG, "info": logging.INFO,
    "warn": logging.WARNING, "warning": logging.WARNING,
    "error": logging.ERROR, "crit": logging.CRITICAL, "critical": logging.CRITICAL,
}


def apply_log_level(cfg: dict) -> None:
    """Set the log level from ``log_level:`` in config.yaml.

    Falls back to the LOG_LEVEL environment variable, then ``info``. Called after
    every config load, so a change applies on the next run without a restart.
    Third-party HTTP libraries log every request at INFO; they are kept at WARNING
    unless the level is ``debug``.
    """
    name = str(cfg.get("log_level") or os.environ.get("LOG_LEVEL") or "info").lower()
    level = LOG_LEVELS.get(name)
    if level is None:
        log.warning("unknown log_level %r, using info (valid: %s)", name, ", ".join(LOG_LEVELS))
        level = logging.INFO
    logging.getLogger().setLevel(level)
    for noisy in ("httpx", "httpcore"):
        logging.getLogger(noisy).setLevel(level if level == logging.DEBUG else max(level, logging.WARNING))


# ---------------------------------------------------------------------------------
# configuration -> collectors
# ---------------------------------------------------------------------------------
def collector_specs(cfg: dict) -> list[tuple[str, type, dict]]:
    """Return ``(name, collector class, merged config)`` for every enabled collector.

    Built-in defaults (``Collector.defaults``) are applied first, then the user's
    keys on top. Tracked circuits with a ``calendar:`` block add a ``jsonld``
    collector named ``circuit-<key>``.
    """
    blocks: dict[str, dict] = dict(cfg.get("collectors") or {})
    blocks.update(_circuit_collectors(cfg))
    specs = []
    for name, block in blocks.items():
        block = block or {}
        if not block.get("enabled", True):
            continue
        ctype = block.get("type")
        if ctype not in TYPES:
            raise ValueError(f"collector '{name}': unknown type '{ctype}' "
                             f"(available: {', '.join(TYPES)})")
        cls = TYPES[ctype]
        specs.append((name, cls, {**cls.defaults(block), **block}))
    return specs


def _circuits(cfg: dict) -> dict[str, dict]:
    """``tracked_circuits`` with defaults: label = key title-cased, match = [key]."""
    out = {}
    for key, c in (cfg.get("tracked_circuits") or {}).items():
        c = dict(c or {})
        c.setdefault("label", key.replace("_", " ").title())
        c.setdefault("match", [key])
        c.setdefault("tz", "Europe/Rome")
        out[key] = c
    return out


def _circuit_collectors(cfg: dict) -> dict[str, dict]:
    """One ``jsonld`` collector per tracked circuit that has a ``calendar:`` block."""
    blocks = {}
    for key, c in _circuits(cfg).items():
        cal = c.get("calendar")
        if not cal:
            continue
        blocks[f"circuit-{key}"] = {
            "type": "jsonld",
            "series": cal.get("series", c["label"].upper()),
            "championship": c.get("name", c["label"]),
            "discipline": cal.get("discipline", "Circuit event"),
            "tz": c["tz"], "geo": c.get("geo"), "location": c.get("location", c["label"]),
            "extra_labels": [c["label"]],
            **{k: v for k, v in cal.items() if k not in ("series", "discipline")},
        }
    return blocks


# ---------------------------------------------------------------------------------
# one run
# ---------------------------------------------------------------------------------
def run_once(cfg: dict, data_dir: Path, out_dir: Path) -> list[Session]:
    """Run every collector once and publish the calendar. Returns the sessions."""
    state = State(data_dir)
    contact = cfg.get("contact", "admin@example.com")
    http = httpx.Client(timeout=25, follow_redirects=True,
                        headers={"User-Agent": f"motorsport-cal/1.0 (personal calendar; {contact})"})
    geo = Geocoder(http, state, enabled=cfg.get("geocoding", True))
    seasons = _seasons(cfg)
    status: dict[str, dict] = {}
    sessions: list[Session] = []

    for name, cls, ccfg in collector_specs(cfg):
        ccfg = {**ccfg, "_cache_dir": str(data_dir / "http-cache")}
        collector = cls(name, ccfg, http, state, geo)
        got: list[Session] = []
        errors = []
        for season in seasons:
            try:
                got += collector.collect(season)
            except Exception as e:  # noqa: BLE001 - any scraper failure is non-fatal
                errors.append(f"{season}: {type(e).__name__}: {e}")
        # success = at least one season produced data (next year is often empty)
        if got:
            state.save_collector(name, got)
            status[name] = {"ok": True, "sessions": len(got), "errors": errors}
        else:
            cached = state.load_collector(name, int(cfg.get("stale_max_days", 14)))
            got = cached or []
            status[name] = {"ok": False, "sessions": len(got), "errors": errors,
                            "using_cache": cached is not None}
            log.error("collector %s failed (%s); using %d cached sessions", name, errors, len(got))
        sessions += got

    sessions = postprocess(sessions, cfg)
    stream_cfg = cfg.get("streaming", {})
    StreamResolver(stream_cfg, http, state, os.environ.get("YOUTUBE_API_KEY")).apply(
        sessions, stream_cfg.get("series", {}))

    ics = build_calendar(sessions, state, cfg)
    target = out_dir / cfg.get("output", {}).get("filename", "motorsport.ics")
    if not target.exists() or target.read_bytes() != ics:
        atomic_write(target, ics)
        log.info("wrote %s (%d events)", target, len(sessions))
    else:
        log.info("no changes (%d events)", len(sessions))
    status_doc = {"generated": datetime.now(UTC).isoformat(), "events": len(sessions),
                  "collectors": status}
    atomic_write(out_dir / "status.json", json.dumps(status_doc, indent=1))
    state.prune(datetime.now(UTC))
    state.flush()
    http.close()
    return sessions


def _seasons(cfg: dict) -> list[int]:
    """Explicit ``seasons:`` or: this year, plus next year from October onwards."""
    if cfg.get("seasons"):
        return [int(s) for s in cfg["seasons"]]
    today = date.today()
    return [today.year, today.year + 1] if today.month >= 10 else [today.year]


def postprocess(sessions: list[Session], cfg: dict) -> list[Session]:
    """Dedupe, drop old events, hide covered placeholders, apply circuit labels."""
    keep_past = timedelta(days=int(cfg.get("output", {}).get("keep_past_days", 45)))
    cutoff = datetime.now(UTC) - keep_past

    def end_utc(s: Session) -> datetime:
        if s.all_day:
            return datetime.combine(s.end, datetime.min.time(), UTC)
        return s.end.astimezone(UTC)

    # dedupe by UID: the first collector in config order wins
    by_uid: dict[str, Session] = {}
    for s in sessions:
        by_uid.setdefault(s.uid(), s)
    sessions = [s for s in by_uid.values() if end_utc(s) >= cutoff]

    # an all-day placeholder disappears once timed sessions of its series exist on those days
    timed = [(s.series, s.start.date()) for s in sessions if not s.all_day]

    def covered(p: Session) -> bool:
        return any(ser == p.series and p.start <= d < p.end for ser, d in timed)
    sessions = [s for s in sessions if not (s.session_type == SessionType.EVENT and covered(s))]

    # tracked circuits: label + authoritative coordinates for anything held there
    circuits = _circuits(cfg)
    for s in sessions:
        hay = f"{s.location} {s.event_name}".lower()
        for c in circuits.values():
            if any(m.lower() in hay for m in c["match"]):
                if c["label"] not in s.extra_labels:
                    s.extra_labels.append(c["label"])
                if c.get("geo"):
                    s.geo = (float(c["geo"][0]), float(c["geo"][1]))
    return sessions


def _next_interval(cfg: dict, sessions: list[Session]) -> int:
    """Seconds until the next run: shorter when a session starts soon."""
    sched = cfg.get("schedule", {})
    normal = int(sched.get("interval_minutes", 180))
    busy = int(sched.get("race_weekend_interval_minutes", 30))
    now = datetime.now(UTC)
    soon = now + timedelta(hours=int(sched.get("race_weekend_window_hours", 72)))
    upcoming = any(not s.all_day and now - timedelta(hours=6) <= s.start <= soon for s in sessions)
    return (busy if upcoming else normal) * 60


# ---------------------------------------------------------------------------------
# validate / providers
# ---------------------------------------------------------------------------------
def validate(cfg: dict) -> list[str]:
    """Offline checks of the whole configuration. Prints the plan, returns errors."""
    errors: list[str] = []
    if cfg.get("log_level") and str(cfg["log_level"]).lower() not in LOG_LEVELS:
        errors.append(f"log_level: '{cfg['log_level']}' is not one of {', '.join(LOG_LEVELS)}")
    try:
        specs = collector_specs(cfg)
    except ValueError as e:
        return [str(e)]
    series_tags = set()
    print(f"{'collector':<18} {'type':<8} {'series':<8} championship")
    for name, cls, ccfg in specs:
        errors += cls.validate(name, ccfg)
        series_tags.add(ccfg.get("series"))
        print(f"{name:<18} {ccfg['type']:<8} {str(ccfg.get('series')):<8} {ccfg.get('championship', '')}")
    for key, c in _circuits(cfg).items():
        try:
            ZoneInfo(c["tz"])
        except Exception:
            errors.append(f"tracked_circuits.{key}: unknown timezone '{c['tz']}'")
        if c.get("geo") and len(c["geo"]) != 2:
            errors.append(f"tracked_circuits.{key}: geo must be [lat, lon]")
    print("tracked circuits:", ", ".join(c["label"] for c in _circuits(cfg).values()) or "-")
    for tag in (cfg.get("streaming", {}).get("series") or {}):
        if tag not in series_tags:
            print(f"note: streaming rules for '{tag}' (fed by manual.yaml / series_map, or unused)")
    try:
        ZoneInfo(cfg.get("output", {}).get("display_timezone", "Europe/Rome"))
    except Exception:
        errors.append("output.display_timezone: unknown timezone")
    return errors


def print_providers(cfg: dict) -> None:
    """List every championship the installed motorsport-calendar library supports."""
    from .collectors.motocal import CATALOG, available_providers

    enabled = {c.get("provider") for _, _, c in collector_specs(cfg) if c.get("type") == "motocal"}
    print(f"{'provider':<14} {'sources':<24} {'enabled':<8} championship")
    for pid, sources in sorted(available_providers().items()):
        meta = CATALOG.get(pid, {})
        print(f"{pid:<14} {', '.join(sources):<24} {'yes' if pid in enabled else '':<8} "
              f"{meta.get('championship', '(not in catalog: set series/championship yourself)')}")


# ---------------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mscal", description="Motorsport calendar generator")
    ap.add_argument("command", nargs="?", default="run", choices=["run", "validate", "providers"])
    ap.add_argument("--config", default=os.environ.get("MSCAL_CONFIG", "/config/config.yaml"))
    ap.add_argument("--data", default=os.environ.get("MSCAL_DATA", "/data"),
                    help="state directory (persistent volume)")
    ap.add_argument("--out", default=os.environ.get("MSCAL_OUT", "/www"),
                    help="where motorsport.ics and status.json are written")
    ap.add_argument("--loop", action="store_true", help="run forever (container mode)")
    args = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    def load() -> dict:
        cfg = yaml.safe_load(Path(args.config).read_text()) or {}
        apply_log_level(cfg)
        return cfg

    if args.command == "validate":
        errors = validate(load())
        for e in errors:
            print("ERROR:", e)
        print("config OK" if not errors else f"{len(errors)} error(s)")
        return 1 if errors else 0
    if args.command == "providers":
        print_providers(load())
        return 0

    def _sigterm(*_):
        global _stop
        _stop = True
    signal.signal(signal.SIGTERM, _sigterm)
    signal.signal(signal.SIGINT, _sigterm)

    while True:
        sessions: list[Session] = []
        try:
            cfg = load()  # re-read every run: edits apply without a restart
            sessions = run_once(cfg, Path(args.data), Path(args.out))
        except Exception:
            log.exception("run failed")
            if not args.loop:
                return 1
            cfg = {}
        if not args.loop:
            return 0
        wait = _next_interval(cfg, sessions)
        log.info("next run in %d min", wait // 60)
        for _ in range(wait):  # 1 s steps so SIGTERM stops the container promptly
            if _stop:
                return 0
            time.sleep(1)


if __name__ == "__main__":
    sys.exit(main())
