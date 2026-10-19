# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Adapter over the `motorsport-calendar` library (MIT, by Alan Yvenou).

That library maintains scrapers/API clients for many championships; this adapter
reuses them and converts their events into our :class:`~mscal.models.Session`.
Any championship it supports can be enabled with two lines of config::

    collectors:
      motogp:
        type: motocal
        provider: motogp

Everything else (series tag, championship name, race type, upstream source) comes
from :data:`CATALOG` and can be overridden per collector. Run ``python -m mscal
providers`` to list what the installed library version supports.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from zoneinfo import ZoneInfo

from ..models import Session, SessionType
from .base import Collector

log = logging.getLogger(__name__)

#: Defaults per upstream provider id. ``source`` is the upstream data source name;
#: the rest become labels. Override any key in config.yaml.
CATALOG: dict[str, dict] = {
    # --- endurance (ACO) ---
    "wec": {"source": "official", "series": "WEC", "discipline": "Endurance",
            "championship": "FIA World Endurance Championship", "info_url": "https://www.fiawec.com"},
    "elms": {"source": "aco_scraper", "series": "ELMS", "discipline": "Endurance",
             "championship": "European Le Mans Series", "info_url": "https://www.europeanlemansseries.com"},
    "mlmc": {"source": "aco_scraper", "series": "MLMC", "discipline": "Endurance",
             "championship": "Michelin Le Mans Cup", "info_url": "https://www.lemanscup.com"},
    # --- GT (SRO) ---
    "gtwc-europe": {"source": "sro_scraper", "series": "GTWCE", "discipline": "GT",
                    "championship": "GT World Challenge Europe",
                    "info_url": "https://www.gt-world-challenge-europe.com"},
    "gtwc-america": {"source": "sro_scraper", "series": "GTWCA", "discipline": "GT",
                     "championship": "GT World Challenge America",
                     "info_url": "https://www.gt-world-challenge-america.com"},
    "gtwc-asia": {"source": "sro_scraper", "series": "GTWCAS", "discipline": "GT",
                  "championship": "GT World Challenge Asia",
                  "info_url": "https://www.gt-world-challenge-asia.com"},
    "igtc": {"source": "sro_scraper", "series": "IGTC", "discipline": "GT",
             "championship": "Intercontinental GT Challenge",
             "info_url": "https://www.intercontinentalgtchallenge.com"},
    # --- motorcycles (Dorna) ---
    "motogp": {"source": "pulselive", "series": "MotoGP", "discipline": "Motorcycle",
               "championship": "MotoGP World Championship", "info_url": "https://www.motogp.com"},
    "moto2": {"source": "pulselive", "series": "Moto2", "discipline": "Motorcycle",
              "championship": "Moto2 World Championship", "info_url": "https://www.motogp.com"},
    "moto3": {"source": "pulselive", "series": "Moto3", "discipline": "Motorcycle",
              "championship": "Moto3 World Championship", "info_url": "https://www.motogp.com"},
    # --- single-seaters ---
    "formula1": {"source": "openf1", "series": "F1", "discipline": "Single-seater",
                 "championship": "FIA Formula One World Championship"},
    "formula2": {"source": "f1calendar", "series": "F2", "discipline": "Single-seater",
                 "championship": "FIA Formula 2 Championship"},
    "formula3": {"source": "f1calendar", "series": "F3", "discipline": "Single-seater",
                 "championship": "FIA Formula 3 Championship"},
    "f1-academy": {"source": "f1calendar", "series": "F1A", "discipline": "Single-seater",
                   "championship": "F1 Academy"},
    "formula-e": {"source": "f1calendar", "series": "FE", "discipline": "Single-seater",
                  "championship": "ABB FIA Formula E World Championship"},
}

#: upstream session type -> our session type (anything unknown becomes OTHER)
_TYPE_MAP = {
    "FP1": SessionType.FP, "FP2": SessionType.FP, "FP3": SessionType.FP,
    "FREE_PRACTICE": SessionType.FP, "TEST": SessionType.FP,
    "QUALIFYING": SessionType.QUALI, "HYPERPOLE": SessionType.QUALI,
    "SPRINT_QUALIFYING": SessionType.QUALI,
    "RACE": SessionType.RACE, "SPRINT": SessionType.RACE,
}


def available_providers() -> dict[str, list[str]]:
    """Return ``{provider_id: [source names]}`` known to the installed library."""
    from motorsport_calendar.core.registry import registry
    from motorsport_calendar.core.source_registry import source_registry

    registry.discover()
    source_registry.discover()
    return {p: source_registry.list_for(p) for p in registry.list_all()}


class MotocalCollector(Collector):
    """Config keys: ``provider`` (required), plus optional ``source``, ``series``,
    ``championship``, ``discipline``, ``info_url``, ``session_types``
    (default ``[FP, QUALI, RACE]``) and ``http_cache_minutes`` (default 50)."""

    required = ("provider", "source", "series")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._seen: dict[int, frozenset] = {}  # season -> fingerprint, see collect()

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return dict(CATALOG.get(cfg.get("provider", ""), {}))

    @classmethod
    def validate(cls, name: str, cfg: dict) -> list[str]:
        errors = super().validate(name, cfg)
        try:
            known = available_providers()
        except Exception as e:  # library missing/broken: report, don't crash
            return errors + [f"{name}: cannot load motorsport-calendar ({e})"]
        pid = cfg.get("provider")
        if pid not in known:
            errors.append(f"{name}: unknown provider '{pid}'. Available: {', '.join(sorted(known))}")
        elif cfg.get("source") and cfg["source"] not in known[pid]:
            errors.append(f"{name}: provider '{pid}' has no source '{cfg['source']}' "
                          f"(available: {', '.join(known[pid])})")
        for t in cfg.get("session_types", []):
            if t not in SessionType.__members__:
                errors.append(f"{name}: unknown session type '{t}'")
        return errors

    def collect(self, season: int) -> list[Session]:
        from motorsport_calendar.cache import HttpCache
        from motorsport_calendar.core.registry import registry
        from motorsport_calendar.core.source_registry import source_registry

        registry.discover()
        source_registry.discover()
        pid, src = self.cfg["provider"], self.cfg["source"]
        # upstream's own HTTP cache, kept a bit shorter than our run interval
        cache = HttpCache(cache_dir=Path(self.cfg.get("_cache_dir", "/data/http-cache")) / pid,
                          ttl=int(self.cfg.get("http_cache_minutes", 50)) * 60)
        source = source_registry.get(pid, src)(cache, False)
        # Some upstream factories ignore the cache argument (wec, worldsbk and imsa in
        # 0.3.0: `lambda cache, refresh: OfficialWecSource()`), so the source falls back
        # to ~/.cache, which is read-only in the container. Hand it ours.
        if hasattr(source, "_cache") and source._cache is not cache:
            source._cache = cache
        provider = registry.get(pid)(source)
        events = asyncio.run(provider.fetch_events(pid, season))

        # Some sources ignore the year: the SRO scraper always reads the site's single
        # /calendar page and stamps the dates with the requested year, so asking for
        # next season returns this season's rounds moved a year on. A season that is
        # an exact copy of the previous one (same events on the same days) is discarded.
        fingerprint = frozenset((ev.name, s.start_datetime.month, s.start_datetime.day)
                                for ev in events for s in ev.sessions)
        if fingerprint and fingerprint == self._seen.get(season - 1):
            log.info("%s: %d data is a copy of %d (source ignores the year); skipped",
                     self.name, season, season - 1)
            return []
        self._seen[season] = fingerprint

        wanted = {SessionType(t) for t in self.cfg.get("session_types", ["FP", "QUALI", "RACE"])}
        out: list[Session] = []
        for ev in events:
            c = ev.circuit
            tz = ZoneInfo(c.timezone)
            location = ", ".join(x for x in (c.name, c.city, c.country) if x)
            # coordinates: tracked circuits are filled in later by the runner;
            # otherwise ask the geocoder (cached forever after the first hit)
            geo = self.geo.lookup(f"{c.name}, {c.country}")
            cancelled = str(getattr(ev, "status", "")).lower() == "cancelled"
            for ss in ev.sessions:
                st = _TYPE_MAP.get(str(ss.type), SessionType.OTHER)
                if st not in wanted:
                    continue
                out.append(Session(
                    series=self.series, championship=self.championship,
                    discipline=self.discipline, season=season, event_name=ev.name,
                    session_type=st, name=ss.title,
                    start=ss.start_datetime.astimezone(tz), end=ss.end_datetime.astimezone(tz),
                    tz=c.timezone, location=location, geo=geo,
                    info_url=self.cfg.get("info_url"), cancelled=cancelled,
                    # round + upstream type + title: stable across runs
                    key=f"r{ev.round}|{ss.type}|{ss.title}",
                ))
        log.info("%s: %d events, %d sessions kept", self.name, len(events), len(out))
        return out
