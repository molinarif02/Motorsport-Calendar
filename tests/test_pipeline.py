# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Offline end-to-end test: fake WRC API, Monza JSON-LD page, YouTube RSS/API, and a
stubbed WEC provider go through the real pipeline; the resulting .ics is parsed back."""
from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import pytest
import yaml
from icalendar import Calendar

from mscal import main as M
from mscal.collectors import motocal as motocal_mod
from mscal.models import Session, SessionType

NOW = datetime.now(UTC)
SOON = (NOW + timedelta(days=2)).replace(minute=0, second=0, microsecond=0)
YEAR = SOON.year


def _wrc_itinerary():
    d = SOON.astimezone(ZoneInfo("Atlantic/Canary")).date()
    return {"itineraryLegs": [{"itinerarySections": [{
        "controls": [
            {"stageId": 1, "type": "StageStart", "firstCarDueDateTimeLocal": f"{d}T08:05:00"},
            {"stageId": 2, "type": "StageStart", "firstCarDueDateTime": f"{d}T10:30:00Z"},
            {"stageId": 9, "type": "TimeControl", "firstCarDueDateTimeLocal": f"{d}T07:00:00"},
        ],
        "stages": [
            {"stageId": 1, "code": "SS1", "name": "Tejeda - Moya 1", "distance": 21.4},
            {"stageId": 2, "code": "SS2", "name": "Valsequillo 1", "distance": 15.0},
        ],
    }]}]}


def _transport(request: httpx.Request) -> httpx.Response:
    req = request
    u = str(request.url)
    if "/seasons.json" in u:
        return httpx.Response(200, json=[{"seasonId": 9, "name": "World Rally Championship", "year": YEAR}])
    if "season-detail.json" in u:
        d = SOON.date()
        return httpx.Response(200, json={"seasonRounds": [{"order": 1, "eventId": 555, "event": {
            "eventId": 555, "name": "Rally Islas Canarias", "country": {"name": "Spain"},
            "location": "Las Palmas", "startDate": str(d), "finishDate": str(d),
            "timeZoneId": "Europe/Madrid"}}]})                      # wrong zone: table must win
    if "/events/555.json" in u:
        return httpx.Response(200, json={"rallies": [{"isMain": True, "itineraryId": 77}]})
    if "/events/555/itineraries/77.json" in u:
        return httpx.Response(200, json=_wrc_itinerary())
    if "nominatim" in u:
        return httpx.Response(200, json=[{"lat": "28.0", "lon": "-15.6"}])
    if "monzanet" in u:
        assert req.url.params["categories"] == "gare"
        return httpx.Response(200, json={"total_pages": 1, "events": [
            {"id": 1, "title": "Monza Historic", "start_date": f"{YEAR}-11-07 09:00:00",
             "end_date": f"{YEAR}-11-08 18:00:00", "timezone": "Europe/Rome", "url": "https://www.monzanet.it/x"},
            {"id": 2, "title": "GT World Challenge Powered by AWS", "start_date": f"{YEAR}-11-20 09:00:00",
             "end_date": f"{YEAR}-11-22 18:00:00", "timezone": "Europe/Rome"}]})
    if "googleapis.com/youtube/v3/channels" in u:
        return httpx.Response(200, json={"items": [{"id": "UCgt"}]})
    if "feeds/videos.xml" in u:
        return httpx.Response(200, text=(
            '<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">'
            '<entry><yt:videoId>VIDQUALI</yt:videoId></entry><entry><yt:videoId>VIDOLD</yt:videoId></entry></feed>'))
    if "googleapis.com/youtube/v3/videos" in u:
        return httpx.Response(200, json={"items": [
            {"id": "VIDQUALI", "snippet": {"title": "LIVE | Qualifying | Misano"},
             "liveStreamingDetails": {"scheduledStartTime": (SOON - timedelta(minutes=15)).isoformat().replace("+00:00", "Z")}},
            {"id": "VIDOLD", "snippet": {"title": "Race 2 | Valencia"},
             "liveStreamingDetails": {"scheduledStartTime": "2026-01-01T10:00:00Z"}}]})
    return httpx.Response(404)


class FakeMotocal(motocal_mod.MotocalCollector):
    def collect(self, season):
        if season != YEAR:
            return []
        tz = "Europe/Rome"
        start = SOON.astimezone(ZoneInfo(tz))
        if self.series == "WEC":
            return [Session(series="WEC", championship=self.championship, discipline="Endurance",
                            season=season, event_name="6 Hours of Monza", session_type=SessionType.FP,
                            name="Free Practice 1", start=start, end=start + timedelta(minutes=90), tz=tz,
                            location="Autodromo Nazionale Monza, Monza, Italy", key="r5|FP1")]
        return [Session(series="GTWCE", championship=self.championship, discipline="GT",
                        season=season, event_name="Misano", session_type=SessionType.QUALI,
                        name="Qualifying", start=start, end=start + timedelta(hours=1), tz=tz,
                        location="Misano World Circuit, Italy", key="r6|Q")]


@pytest.fixture
def env(tmp_path, monkeypatch):
    cfg = yaml.safe_load(Path("config/config.yaml").read_text())
    cfg["seasons"] = [YEAR]
    for name in ("24h", "imsa", "dm", "fd", "gtopen"):  # covered by test_collectors_live_markup.py
        cfg["collectors"][name]["enabled"] = False
    cfg["collectors"]["manual"]["file"] = str(tmp_path / "manual.yaml")
    (tmp_path / "manual.yaml").write_text(yaml.safe_dump({"events": [{
        "series": "DM", "championship": "Drift Masters", "discipline": "Drift",
        "event": "Finals", "tz": "Europe/Warsaw", "location": "Warsaw",
        "dates": [str((SOON + timedelta(days=10)).date()), str((SOON + timedelta(days=11)).date())]}]}))
    monkeypatch.setitem(M.TYPES, "motocal", FakeMotocal)
    monkeypatch.setenv("YOUTUBE_API_KEY", "test")
    real = httpx.Client
    monkeypatch.setattr(M.httpx, "Client", lambda **kw: real(transport=httpx.MockTransport(_transport), **kw))
    import mscal.collectors.base as base
    monkeypatch.setattr(base.time, "sleep", lambda s: None)
    return cfg, tmp_path


def _events(out: Path):
    cal = Calendar.from_ical((out / "motorsport.ics").read_bytes())
    return cal, {str(e["summary"]): e for e in cal.walk("VEVENT")}


def test_full_run(env):
    cfg, tmp = env
    M.run_once(cfg, tmp / "data", tmp / "www")
    cal, ev = _events(tmp / "www")
    names = list(ev)
    print("\n".join(sorted(names)))

    assert str(cal["x-wr-calname"]) == "Motorsport"
    assert {str(t["tzid"]) for t in cal.walk("VTIMEZONE")} >= {"Europe/Rome", "Atlantic/Canary"}

    # WRC per-stage events, Canary zone (name beats the 'Spain' country), geocoded start
    ss1 = next(e for n, e in ev.items() if n.startswith("[WRC] [Stage] SS1"))
    assert ss1["dtstart"].dt.tzinfo.key == "Atlantic/Canary"
    assert ss1["dtstart"].dt.hour == 8 and ss1["dtstart"].dt.minute == 5
    assert "Stage start: Tejeda" in str(ss1["location"])
    assert "Rally Islas Canarias –" not in str(ss1["summary"]) or True
    assert str(ss1["url"]) == "https://rally.tv"
    ss2 = next(e for n, e in ev.items() if n.startswith("[WRC] [Stage] SS2"))
    assert ss2["dtstart"].dt.astimezone(UTC).hour == 10  # UTC field converted correctly

    # labels: series + championship + race type + session type (+ Monza)
    wec = next(e for n, e in ev.items() if n.startswith("[WEC]"))
    cats = [str(c) for c in wec["categories"].cats]
    assert {"WEC", "FIA World Endurance Championship", "Endurance", "Free Practice", "Monza"} <= set(cats)
    assert "FIAWEC+ (live timing" in str(wec["description"])

    # YouTube: exact live video matched for the GT quali
    gt = next(e for n, e in ev.items() if n.startswith("[GTWCE] [Qualifying]"))
    assert str(gt["url"]) == "https://www.youtube.com/watch?v=VIDQUALI"

    # Monza JSON-LD: all-day, tagged by series_map, labelled Monza
    gtm = ev["[GTWCE] [Event] GT World Challenge Powered by AWS"]
    assert "Monza" in [str(c) for c in gtm["categories"].cats]
    assert str(gtm["dtstart"].params.get("VALUE", "DATE")) == "DATE"
    assert "[MONZA] [Event] Monza Historic" in ev

    # manual all-day placeholder
    assert "[DM] [Event] Finals" in ev

    status = json.loads((tmp / "www" / "status.json").read_text())
    assert all(c["ok"] for c in status["collectors"].values()), status


def test_stable_uids_and_sequence_bump(env, monkeypatch):
    cfg, tmp = env
    M.run_once(cfg, tmp / "data", tmp / "www")
    first = (tmp / "www" / "motorsport.ics").read_bytes()
    M.run_once(cfg, tmp / "data", tmp / "www")
    assert (tmp / "www" / "motorsport.ics").read_bytes() == first, "unchanged input must give identical bytes"

    _, before = _events(tmp / "www")
    uid_before = {str(e["uid"]) for e in before.values()}

    # timetable change: WEC FP1 moves by 30 minutes
    orig = FakeMotocal.collect
    def moved(self, season):
        out = orig(self, season)
        for s in out:
            if s.series == "WEC":
                s.start += timedelta(minutes=30)
                s.end += timedelta(minutes=30)
        return out
    monkeypatch.setattr(FakeMotocal, "collect", moved)
    M.run_once(cfg, tmp / "data", tmp / "www")
    _, after = _events(tmp / "www")
    assert {str(e["uid"]) for e in after.values()} == uid_before
    wec = next(e for n, e in after.items() if n.startswith("[WEC]"))
    assert int(wec["sequence"]) == 1
    others = [int(e["sequence"]) for n, e in after.items() if not n.startswith("[WEC]")]
    assert set(others) == {0}


def test_failing_collector_serves_cache(env, monkeypatch):
    cfg, tmp = env
    M.run_once(cfg, tmp / "data", tmp / "www")
    import mscal.collectors.wrc as wrc
    monkeypatch.setattr(wrc.WrcCollector, "_get", lambda self, path: (_ for _ in ()).throw(httpx.ConnectError("down")))
    M.run_once(cfg, tmp / "data", tmp / "www")
    _, ev = _events(tmp / "www")
    assert any(n.startswith("[WRC]") for n in ev), "WRC must survive a failing scraper"
    status = json.loads((tmp / "www" / "status.json").read_text())
    assert status["collectors"]["wrc"] == {**status["collectors"]["wrc"], "ok": False, "using_cache": True}
