# SPDX-License-Identifier: GPL-3.0-or-later
"""MotocalCollector against fake upstream sources with two real upstream flaws:
a factory that ignores the cache argument (wec/worldsbk/imsa in 0.3.0), and a
source that ignores the year (the SRO scraper)."""
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx

from mscal.collectors.base import Geocoder
from mscal.collectors.motocal import CATALOG, MotocalCollector
from mscal.state import State


class YearBlindSource:
    """Like SroTimetableSource: same rounds every year, dated with the asked year.
    Like the wec factory: built without our cache, so it would use ~/.cache."""

    def __init__(self, cache, refresh):
        self._cache = None  # the upstream default, not the cache it was given

    def season(self, year):
        def sess(month, day, kind, title):
            start = datetime(year, month, day, 13, 0, tzinfo=UTC)
            return SimpleNamespace(type=kind, title=title, start_datetime=start,
                                   end_datetime=start.replace(hour=14))
        circuit = SimpleNamespace(name="Monza", city="Monza", country="Italy", timezone="Europe/Rome")
        return [SimpleNamespace(name="Monza", round=1, circuit=circuit, status="scheduled",
                                sessions=(sess(5, 30, "QUALIFYING", "Qualifying"),
                                          sess(5, 31, "RACE", "Race")))]


def _collector(monkeypatch, tmp_path):
    from motorsport_calendar.core.registry import registry
    from motorsport_calendar.core.source_registry import source_registry

    built = {}

    def make_source(cache, refresh):
        built["given"], built["source"] = cache, YearBlindSource(cache, refresh)
        return built["source"]

    class Provider:
        def __init__(self, source):
            self.source = source

        async def fetch_events(self, pid, year):
            return self.source.season(year)

    monkeypatch.setattr(source_registry, "discover", lambda: None)
    monkeypatch.setattr(registry, "discover", lambda: None)
    monkeypatch.setattr(source_registry, "get", lambda pid, src: make_source)
    monkeypatch.setattr(registry, "get", lambda pid: Provider)

    state = State(tmp_path / "data")
    http = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    cfg = {**CATALOG["gtwc-europe"], "provider": "gtwc-europe", "_cache_dir": str(tmp_path / "hc")}
    return MotocalCollector("gtwce", cfg, http, state, Geocoder(http, state, enabled=False)), built


def test_source_gets_our_cache_even_if_factory_ignores_it(monkeypatch, tmp_path):
    col, built = _collector(monkeypatch, tmp_path)
    col.collect(2026)
    assert built["source"]._cache is built["given"]
    assert str(tmp_path / "hc") in str(built["given"]._cache_dir)


def test_next_season_copy_from_year_blind_source_is_dropped(monkeypatch, tmp_path):
    col, _ = _collector(monkeypatch, tmp_path)
    this_year = col.collect(2026)
    assert len(this_year) == 2 and this_year[0].start.year == 2026
    assert col.collect(2027) == []  # same rounds on the same days: a copy, not 2027
