# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Collector interface and shared helpers.

A *collector* turns one data source (a website, an API, a YAML file) into a list of
normalized :class:`~mscal.models.Session` objects. Collectors are configured in the
``collectors:`` section of ``config.yaml``; the ``type:`` key selects the class
(see :data:`mscal.collectors.TYPES`).

Contract
--------
* :meth:`Collector.collect` is called once per season and returns that season's
  sessions. It should **raise** on failure (network error, page layout changed,
  nothing parsed) rather than return an empty list: the runner then falls back to
  the collector's last good output, so a broken scraper never empties the calendar.
* Collectors must not set stream links unless the source itself provides one; the
  :class:`~mscal.streams.StreamResolver` handles that afterwards.
* Session ``key`` values must be stable across runs (e.g. round + session type),
  because the UID shown to calendar clients is derived from them.
"""
from __future__ import annotations

import logging
import time
from abc import ABC, abstractmethod

import httpx

from ..models import Session
from ..state import State

log = logging.getLogger(__name__)


class Collector(ABC):
    """Base class for every data source.

    Args:
        name: the collector's key in ``config.yaml`` (used for logs, cache files, status).
        cfg: that collector's config block, already merged with :meth:`defaults`.
        http: shared HTTP client (timeouts, User-Agent and proxy already set).
        state: persistent state, e.g. for small caches via ``state.kv_get/kv_set``.
        geo: geocoder for turning place names into coordinates.
    """

    #: config keys that must be present after defaults are applied
    required: tuple[str, ...] = ("series",)
    #: True if returning no sessions is a valid answer (a local file with no entries).
    #: Otherwise an empty result counts as a failure and the last good data is served,
    #: because for a scraper it usually means the website changed.
    may_be_empty: bool = False

    def __init__(self, name: str, cfg: dict, http: httpx.Client, state: State, geo: Geocoder):
        self.name = name
        self.cfg = cfg
        self.http = http
        self.state = state
        self.geo = geo

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        """Return built-in defaults for this config block (overridden by user values).

        Subclasses use this to let users write short configs, e.g. the motocal
        collector fills series/championship/discipline from its catalog.
        """
        return {}

    @classmethod
    def validate(cls, name: str, cfg: dict) -> list[str]:
        """Static checks run by ``mscal validate`` (no network). Returns error strings."""
        return [f"{name}: missing '{k}'" for k in cls.required if not cfg.get(k)]

    @abstractmethod
    def collect(self, season: int) -> list[Session]:
        """Return all sessions of ``season`` from this source, or raise on failure."""

    # ---- labels shared by every session this collector produces ---------------
    @property
    def series(self) -> str:
        """Short tag shown in titles, e.g. ``WEC``."""
        return self.cfg["series"]

    @property
    def championship(self) -> str:
        """Full championship name, used as a label and in descriptions."""
        return self.cfg.get("championship", self.series)

    @property
    def discipline(self) -> str:
        """Race type label, e.g. ``Endurance``, ``GT``, ``Rally``, ``Motorcycle``."""
        return self.cfg.get("discipline", "")


def unique_names(sessions: list[tuple]) -> list[tuple]:
    """Make the name (index 1) of ``(type, name, start, end)`` tuples unique in order of
    appearance: a second "Qualifying" becomes "Qualifying 2". Names feed the session
    key, and therefore the UID, so duplicates would collapse into one event."""
    seen: dict[str, int] = {}
    out = []
    for t, name, start, end in sessions:
        seen[name] = seen.get(name, 0) + 1
        out.append((t, name if seen[name] == 1 else f"{name} {seen[name]}", start, end))
    return out


class Geocoder:
    """Place name -> (lat, lon) via OpenStreetMap Nominatim.

    Results (including "not found") are cached forever in the state file, so a
    steady-state run makes no requests at all. Requests are spaced at least 1.1 s
    apart, as required by the Nominatim usage policy, which also requires a
    User-Agent with contact details (set from ``contact:`` in config.yaml).
    """

    URL = "https://nominatim.openstreetmap.org/search"

    def __init__(self, http: httpx.Client, state: State, enabled: bool = True):
        self.http, self.state, self.enabled = http, state, enabled
        self._last = 0.0

    def lookup(self, query: str) -> tuple[float, float] | None:
        """Return coordinates for ``query`` or ``None`` (unknown, disabled or error)."""
        key = query.strip().lower()
        cached = self.state.kv_get("geo", key)
        if cached is not None:
            return tuple(cached) if cached else None  # [] caches a confirmed "not found"
        if not self.enabled:
            return None
        wait = 1.1 - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.monotonic()
        try:
            r = self.http.get(self.URL, params={"q": query, "format": "jsonv2", "limit": 1})
            r.raise_for_status()
            hits = r.json()
        except httpx.HTTPError as e:
            log.warning("geocode failed for %r: %s", query, e)
            return None  # deliberately not cached: retry on the next run
        result = (float(hits[0]["lat"]), float(hits[0]["lon"])) if hits else None
        self.state.kv_set("geo", key, list(result) if result else [])
        return result
