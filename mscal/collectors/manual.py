# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Hand-maintained sessions (manual.yaml) for series without a usable data source
(Drift Masters, Formula Drift, GT Open, 24H Series...) or to patch a scraper.

Edit the file on the Pi; the next run publishes it. Format:

events:
  - series: GTOPEN
    championship: International GT Open
    discipline: GT
    event: Monza
    tz: Europe/Rome
    location: Autodromo Nazionale Monza, Italy
    geo: [45.6156, 9.2811]
    stream_url: https://www.youtube.com/@gtopen/streams   # optional
    sessions:
      - {type: FP,    name: FP1,    start: "2026-10-16 10:30", minutes: 60}
      - {type: QUALI, name: Q1,     start: "2026-10-17 09:00", minutes: 30}
      - {type: RACE,  name: Race 1, start: "2026-10-17 15:05", minutes: 70}
  - series: DM
    championship: Drift Masters
    discipline: Drift
    event: Round 5 – Poland
    tz: Europe/Warsaw
    location: PGE Narodowy, Warsaw
    dates: ["2026-08-29", "2026-08-30"]       # all-day placeholder when times are unknown
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from ..models import Session, SessionType
from .base import Collector


class ManualCollector(Collector):
    """Reads ``file`` (default ``/config/manual.yaml``). Each entry carries its own
    series/championship labels, so one manual collector can feed many series."""

    required = ("file",)

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {"series": "MANUAL", "championship": "Manual entries", "file": "/config/manual.yaml"}

    @classmethod
    def validate(cls, name: str, cfg: dict) -> list[str]:
        errors = super().validate(name, cfg)
        path = Path(cfg.get("file", ""))
        if not path.exists():
            return errors + [f"{name}: {path} not found (it is optional; create it or disable the collector)"]
        try:
            doc = yaml.safe_load(path.read_text()) or {}
        except yaml.YAMLError as e:
            return errors + [f"{name}: {path}: invalid YAML: {e}"]
        for i, ev in enumerate(doc.get("events") or []):
            where = f"{name}: {path.name} events[{i}]"
            for k in ("series", "event"):
                if k not in ev:
                    errors.append(f"{where}: missing '{k}'")
            try:
                ZoneInfo(ev.get("tz", "Europe/Rome"))
            except Exception:
                errors.append(f"{where}: unknown timezone '{ev.get('tz')}'")
            for s in ev.get("sessions") or []:
                if s.get("type", "OTHER") not in SessionType.__members__:
                    errors.append(f"{where}: unknown session type '{s.get('type')}'")
                try:
                    datetime.fromisoformat(str(s.get("start")))
                except ValueError:
                    errors.append(f"{where}: bad start '{s.get('start')}' (use 'YYYY-MM-DD HH:MM')")
        return errors

    def collect(self, season: int) -> list[Session]:
        path = Path(self.cfg.get("file", "/config/manual.yaml"))
        if not path.exists():
            return []
        doc = yaml.safe_load(path.read_text()) or {}
        out: list[Session] = []
        for ev in doc.get("events") or []:
            tzname = ev.get("tz", "Europe/Rome")
            tz = ZoneInfo(tzname)
            common = dict(
                series=ev["series"], championship=ev.get("championship", ev["series"]),
                discipline=ev.get("discipline", ""), season=season, event_name=ev["event"],
                tz=tzname, location=ev.get("location", ""),
                geo=tuple(ev["geo"]) if ev.get("geo") else None,
                info_url=ev.get("info_url"), stream_url=ev.get("stream_url"),
                stream_label=ev.get("stream_label"), cancelled=ev.get("cancelled", False),
                extra_labels=ev.get("labels", []),
            )
            if ev.get("dates"):
                d0, d1 = date.fromisoformat(str(ev["dates"][0])), date.fromisoformat(str(ev["dates"][-1]))
                if d0.year == season:
                    out.append(Session(**common, session_type=SessionType.EVENT, name=ev["event"],
                                       start=d0, end=d1 + timedelta(days=1),
                                       key=f"manual|{ev['event']}|{d0}"))
            for s in ev.get("sessions") or []:
                start = datetime.fromisoformat(str(s["start"])).replace(tzinfo=tz)
                if start.year != season:
                    continue
                out.append(Session(
                    **{**common, "stream_url": s.get("stream_url", common["stream_url"])},
                    session_type=SessionType(s.get("type", "OTHER")), name=s["name"],
                    start=start, end=start + timedelta(minutes=int(s.get("minutes", 60))),
                    key=f"manual|{ev['event']}|{s['name']}",
                ))
        return out
