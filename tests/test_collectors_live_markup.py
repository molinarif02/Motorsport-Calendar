# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Collectors run against pages captured from the live sites (tests/fixtures/)."""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

import httpx

from mscal.collectors import Geocoder
from mscal.collectors.creventic import CreventicCollector
from mscal.collectors.imsa import ImsaCollector
from mscal.models import SessionType
from mscal.state import State

FIX = Path(__file__).parent / "fixtures"


def _client(routes: dict[str, str]) -> httpx.Client:
    def handler(req: httpx.Request) -> httpx.Response:
        url = str(req.url)
        for needle, name in routes.items():
            if needle in url:
                return httpx.Response(200, text=(FIX / name).read_text())
        return httpx.Response(404)
    return httpx.Client(transport=httpx.MockTransport(handler))


def _make(cls, routes, tmp_path, **cfg):
    http = _client(routes)
    state = State(tmp_path)
    full = {**cls.defaults({}), **cfg}
    return cls("x", full, http, state, Geocoder(http, state, enabled=False))


# ---- 24H Series -------------------------------------------------------------------
def test_24h_kyalami_timetable(tmp_path):
    c = _make(CreventicCollector, {
        "/races/michelin-24h-kyalami-2027": "24h_race_kyalami_2027.html",
        "/races/michelin-12h-red-bull-ring-2027": "24h_race_no_timetable.html",
        "/races": "24h_listing.html",
    }, tmp_path)
    out = c.collect(2027)
    ky = {s.name: s for s in out if "KYALAMI" in s.event_name}
    assert set(ky) == {"Free Practice", "Night Practice (sunset 19.04)", "Qualifying (all classes)", "Race"}

    race = ky["Race"]
    assert race.session_type == SessionType.RACE and race.tz == "Africa/Johannesburg"
    assert race.start.isoformat() == "2027-01-09T13:00:00+02:00"
    assert race.end - race.start == timedelta(hours=24)            # Start/Finish pairing

    q = ky["Qualifying (all classes)"]                              # 9 class slots merged
    assert (q.start.strftime("%H:%M"), q.end.strftime("%H:%M")) == ("13:45", "16:31")
    assert ky["Free Practice"].session_type == SessionType.FP
    assert not any("Track Day" in n or "Grid" in n or "Podium" in n for n in ky)
    assert ky["Race"].location == "Kyalami Grand Prix Circuit"

    # no timetable yet -> all-day placeholder over the listed weekend
    rbr = [s for s in out if "RED BULL" in s.event_name]
    assert len(rbr) == 1 and rbr[0].session_type == SessionType.EVENT and rbr[0].all_day
    assert (str(rbr[0].start), str(rbr[0].end)) == ("2027-05-14", "2027-05-17")
    # duplicate card on the listing page doesn't duplicate events
    assert len([s for s in out if s.name == "Race"]) == 1


def test_24h_unmerged_qualifying(tmp_path):
    c = _make(CreventicCollector, {"/races/michelin-24h-kyalami-2027": "24h_race_kyalami_2027.html",
                                   "/races/michelin-12h": "24h_race_no_timetable.html",
                                   "/races": "24h_listing.html"}, tmp_path, merge_qualifying=False)
    q = [s for s in c.collect(2027) if s.session_type == SessionType.QUALI]
    assert len(q) == 9 and q[0].name == "Qualifying 1 - Class TCE, GT4 & GTX"


# ---- IMSA -------------------------------------------------------------------------
IMSA = {
    "weathertech-2026-schedule": "imsa_season_2026.html",
    "2026-rolex-24-at-daytona": "imsa_event_daytona_2026.html",
    "2026-long-beach-street-circuit": "imsa_event_longbeach_2026.html",
}


def test_imsa_daytona_and_long_beach(tmp_path):
    out = _make(ImsaCollector, IMSA, tmp_path).collect(2026)
    day = {s.name: s for s in out if s.event_name == "Rolex 24 At DAYTONA"}
    assert set(day) == {"Practice 1", "Practice 2", "Practice 3", "Qualifying", "Race"}
    race = day["Race"]                                   # split at midnight -> merged
    assert race.tz == "America/New_York"
    assert race.start.isoformat() == "2026-01-24T13:40:00-05:00"
    assert race.end - race.start == timedelta(hours=24)
    assert "BMW M Endurance" not in " ".join(day)         # other IMSA series ignored

    lb = {s.name: s for s in out if "LONG BEACH" in s.event_name}
    assert set(lb) == {"Practice 1", "Practice 2", "Qualifying", "Race"}
    # site lists ET; event is stored in the venue's Pacific time
    assert lb["Race"].tz == "America/Los_Angeles"
    assert lb["Race"].start.isoformat() == "2026-04-18T13:05:00-07:00"
    assert lb["Race"].end - lb["Race"].start == timedelta(minutes=100)
    assert "Long Beach" in lb["Race"].location

    # the Roar has no event page here (404) -> placeholder from the card dates
    roar = [s for s in out if "Roar" in s.event_name]
    assert len(roar) == 1 and roar[0].session_type == SessionType.EVENT
    assert (str(roar[0].start), str(roar[0].end)) == ("2026-01-16", "2026-01-19")


def test_imsa_season_url_from_selector(tmp_path):
    c = _make(ImsaCollector, IMSA, tmp_path)
    assert c._season_url(2027) == "https://www.imsa.com/weathertech/2027-schedule/"
    assert c._season_url(2026).endswith("weathertech-2026-schedule/")
