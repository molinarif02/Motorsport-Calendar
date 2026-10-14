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


# ---- Drift Masters ----------------------------------------------------------------
DM = {
    "/umbraco/api/v1/seasons/de78e5bb-ebc3-4d79-abdf-fdcf4fd99c26/rounds/f684aa51-e27b-4f95-bb76-99862f3abf10/event": "dm_round_7_event.json",
    "/umbraco/api/v1/seasons/de78e5bb-ebc3-4d79-abdf-fdcf4fd99c26/rounds/f684aa51-e27b-4f95-bb76-99862f3abf10/live": "dm_round_7_live.json",
    "/rounds/round-7": "dm_round_7.html",
    "/seasons/drift-masters-2026/": "dm_season_2026.html",
}


def test_driftmasters_round_with_timetable(tmp_path):
    from mscal.collectors.driftmasters import DriftMastersCollector

    out = _make(DriftMastersCollector, DM, tmp_path).collect(2026)
    r7 = {s.name: s for s in out if s.event_name == "Round 7 – Warsaw"}
    assert set(r7) == {"Qualification Round 7 – 1st Session", "Qualification Round 7 – 2nd Session",
                       "TOP 32 Drift Masters Battles", "TOP 16 Battles", "TOP 8 Battles"}
    q1 = r7["Qualification Round 7 – 1st Session"]
    assert q1.session_type == SessionType.QUALI and q1.tz == "Europe/Warsaw"
    assert (q1.start.isoformat(), q1.end.strftime("%H:%M")) == ("2026-09-11T17:30:00+02:00", "19:15")
    top32 = r7["TOP 32 Drift Masters Battles"]
    assert top32.session_type == SessionType.RACE
    assert (top32.start.isoformat(), top32.end.strftime("%H:%M")) == ("2026-09-12T15:00:00+02:00", "17:15")
    # exact streams from the site's /live endpoint
    assert q1.stream_url == "https://www.youtube.com/watch?v=vPhV55wK1JA"   # exact video from dm.gp
    # battles: the site lists Red Bull TV embeds; those are ignored so the DM
    # streaming rules (Red Bull Motorsports on YouTube) apply instead
    assert top32.stream_url is None and r7["TOP 8 Battles"].stream_url is None
    assert "PGE Narodowy, Poland" == q1.location


def test_driftmasters_round_without_details_falls_back_to_card(tmp_path):
    from mscal.collectors.driftmasters import DriftMastersCollector

    out = _make(DriftMastersCollector, DM, tmp_path).collect(2026)
    r6 = [s for s in out if s.event_name.startswith("Round 6")]
    assert len(r6) == 1 and r6[0].session_type == SessionType.EVENT
    assert (str(r6[0].start), str(r6[0].end), r6[0].tz) == ("2026-08-14", "2026-08-17", "Europe/Berlin")


# ---- Formula DRIFT ----------------------------------------------------------------
FD = {"/schedule/2026/long-beach2": "fd_event_long-beach2_2026.html", "/schedule": "fd_listing_2026.html"}


def test_formuladrift_round(tmp_path):
    from mscal.collectors.formuladrift import FormulaDriftCollector

    out = _make(FormulaDriftCollector, FD, tmp_path).collect(2026)
    lb = {s.name: s for s in out if s.event_name == "Shoreline Showdown"}
    assert set(lb) == {"Practice", "Practice 2", "Qualifying", "Top 32", "Top 16"}
    q = lb["Qualifying"]
    assert q.tz == "America/Los_Angeles" and q.session_type == SessionType.QUALI
    assert (q.start.isoformat(), q.end.strftime("%H:%M")) == ("2026-10-23T18:15:00-07:00", "21:15")
    assert q.stream_url == "https://www.formulad.com/live"
    assert lb["Top 32"].session_type == SessionType.RACE
    assert lb["Top 16"].start.isoformat() == "2026-10-24T18:00:00-07:00"
    assert lb["Practice"].stream_url is None
    assert "Long Beach Convention & Entertainment Center" in q.location

    # Atlanta: page not captured (404) -> placeholder from the listing card
    atl = [s for s in out if s.event_name == "Road To The Championship"]
    assert len(atl) == 1 and atl[0].session_type == SessionType.EVENT
    assert (str(atl[0].start), str(atl[0].end), atl[0].tz) == ("2026-05-07", "2026-05-10", "America/New_York")


# ---- International GT Open --------------------------------------------------------
GTO = {
    "BARCELONA%202026%20schedule": "gto_schedule_barcelona_2026.txt",   # served as the "PDF"
    "/calendario": "gto_calendario_2026.html",
    "gtopen.gtsport.es/": "gto_home_2026.html",
}


def test_gtopen_parses_next_race_pdf_and_keeps_it(tmp_path, monkeypatch):
    from mscal.collectors import gtopen

    # the fixture is the PDF's extracted text; skip the PDF decoding step
    monkeypatch.setattr(gtopen, "pdf_to_text", lambda content: content.decode())
    c = _make(gtopen.GtOpenCollector, GTO, tmp_path)
    out = c.collect(2026)

    bcn = {s.name: s for s in out if s.event_name == "Barcelona"}
    assert set(bcn) == {"Free Practice 1", "Free Practice 2", "Free Practice 3 (Bronze)",
                        "Qualifying 1", "Qualifying 2", "Race 1", "Race 2"}
    r1, r2 = bcn["Race 1"], bcn["Race 2"]
    assert r1.tz == "Europe/Madrid"
    assert (r1.start.isoformat(), r1.end.strftime("%H:%M")) == ("2026-10-24T15:10:00+02:00", "16:20")
    # race 2: series name is on the line before the row; GT Cup's race 2 (series after) is not taken
    assert (r2.start.isoformat(), r2.end.strftime("%H:%M")) == ("2026-10-25T13:30:00+01:00", "14:30")
    assert bcn["Qualifying 1"].start.strftime("%a %H:%M") == "Sat 10:40"
    assert bcn["Free Practice 1"].session_type == SessionType.FP
    assert "Private test" not in " ".join(bcn)

    # other rounds: weekend placeholders; the winter test is skipped by default
    spa = [s for s in out if s.event_name == "Spa-Francorchamps"]
    assert len(spa) == 1 and spa[0].tz == "Europe/Brussels"                  # "Bélgica"
    assert (str(spa[0].start), str(spa[0].end)) == ("2026-05-15", "2026-05-18")
    assert not any("Winter" in s.event_name for s in out)

    # after the weekend the homepage links another race: Barcelona keeps its sessions
    c.state.flush()  # the runner saves state at the end of every run
    monkeypatch.setattr(gtopen, "parse_next_race", lambda html: None)
    again = _make(gtopen.GtOpenCollector, GTO, tmp_path).collect(2026)
    assert {s.name for s in again if s.event_name == "Barcelona"} == set(bcn)


# ---- WRC --------------------------------------------------------------------------
WRC = {
    "/seasons.json": "wrc/seasons.json",
    "season-detail.json?seasonId=47": "wrc/season-detail-47.json",
    "season-detail.json?seasonId=50": "wrc/season-detail-50.json",
    "/events/635/itineraries/1359.json": "wrc/itinerary-635-1359.json",
    "/events/635.json": "wrc/event-635.json",
    "/events/698/itineraries/1501.json": "wrc/itinerary-698-1501.json",
    "/events/698.json": "wrc/event-698.json",
}


def test_wrc_stages_from_live_api(tmp_path):
    from mscal.collectors.wrc import WrcCollector

    out = _make(WrcCollector, WRC, tmp_path).collect(2026)
    mc = {s.name.split(" ")[0]: s for s in out if s.event_name == "Rallye Monte Carlo"}
    assert set(mc) == {"SS1", "SS2", "SS3", "SS4", "SS5"}
    ss1 = mc["SS1"]
    assert ss1.session_type == SessionType.STAGE and ss1.tz == "Europe/Monaco"
    assert ss1.start.isoformat() == "2026-01-22T16:05:00+01:00"
    assert ss1.name == "SS1 Toudon / Saint-Antonin (21.9 km)"
    assert ss1.location == "Stage start: Toudon, Monaco"
    assert mc["SS5"].start.isoformat() == "2026-01-23T10:32:00+01:00"
    # Sweden: event page not captured (404) -> placeholder over the event dates
    se = [s for s in out if s.event_name == "Rally Sweden"]
    assert len(se) == 1 and se[0].session_type == SessionType.EVENT and se[0].tz == "Europe/Stockholm"
    assert (str(se[0].start), str(se[0].end)) == ("2026-02-12", "2026-02-16")


def test_wrc_unpublished_itinerary_and_placeholder_timezone(tmp_path):
    from mscal.collectors.wrc import WrcCollector

    out = _make(WrcCollector, WRC, tmp_path).collect(2027)
    (mc,) = out
    assert mc.session_type == SessionType.EVENT
    # the API says "AUS Eastern Standard Time" for this unpublished rally: not trusted
    assert mc.tz == "Europe/Monaco"
    assert (str(mc.start), str(mc.end)) == ("2027-01-21", "2027-01-25")


# ---- Monza (The Events Calendar REST API) -----------------------------------------
def test_monza_races_from_tribe_api(tmp_path):
    import yaml

    from mscal import main as M
    from mscal.collectors.tribe import TribeEventsCollector

    cfg = yaml.safe_load(Path("config/config.yaml").read_text())
    block = {**M._circuit_collectors(cfg)["circuit-monza"]}
    assert block["type"] == "tribe" and block["categories"] == ["gare"]
    c = _make(TribeEventsCollector, {"monzanet.it/wp-json": "monza_tribe_gare_2026.json"}, tmp_path, **block)
    out = {s.name: s for s in c.collect(2026)}
    assert len(out) == 12
    wec = out["6 Hours of Monza – FIA World Endurance Championship"]           # entities decoded
    assert wec.series == "WEC" and wec.all_day and "Monza" in wec.categories()
    assert (str(wec.start), str(wec.end)) == ("2026-11-06", "2026-11-09")
    assert out["Formula 1 Pirelli Gran Premio d’Italia"].series == "F1"
    assert out["International GT Open"].series == "GTOPEN"
    assert out["Monza Revival"].series == "MONZA"
    assert wec.geo == (45.6156, 9.2811)


def test_gtopen_real_pdf_roundtrip():
    """Render the captured timetable rows into an actual PDF (columns as separate text
    runs, like the original) and parse it through pypdf, the path used in production."""
    import io

    import pytest

    canvas_mod = pytest.importorskip("reportlab.pdfgen.canvas")
    from reportlab.lib.pagesizes import A4

    from mscal.collectors import gtopen

    buf = io.BytesIO()
    c = canvas_mod.Canvas(buf, pagesize=A4)
    y = 800
    for line in (FIX / "gto_schedule_barcelona_2026.txt").read_text().splitlines():
        x = 30
        for cell in [p for p in line.split("  ") if p.strip()]:  # each column = own text run
            c.drawString(x, y, cell.strip())
            x += max(60, 6 * len(cell.strip()))
        y -= 12
        if y < 40:
            c.showPage()
            y = 800
    c.save()

    text = gtopen.pdf_to_text(buf.getvalue())
    sessions = {n: (s, e) for _, n, s, e in gtopen.parse_schedule(text, 2026)}
    assert set(sessions) == {"Free Practice 1", "Free Practice 2", "Free Practice 3 (Bronze)",
                             "Qualifying 1", "Qualifying 2", "Race 1", "Race 2"}
    assert sessions["Race 1"][0].strftime("%d %H:%M") == "24 15:10"
    assert sessions["Race 2"][0].strftime("%d %H:%M") == "25 13:30"


def test_driftmasters_battles_use_red_bull_motorsports_rule(tmp_path):
    """Through the real stream resolver with the shipped config (no API key)."""
    import yaml

    from mscal.collectors.driftmasters import DriftMastersCollector
    from mscal.streams import StreamResolver

    cfg = yaml.safe_load(Path("config/config.yaml").read_text())
    c = _make(DriftMastersCollector, DM, tmp_path)
    out = c.collect(2026)
    StreamResolver(cfg["streaming"], c.http, c.state, None).apply(out, cfg["streaming"]["series"])
    r7 = {s.name: s for s in out if s.event_name == "Round 7 – Warsaw"}
    assert r7["TOP 32 Drift Masters Battles"].stream_url == "https://www.youtube.com/@redbullmotorsports/streams"
    assert r7["TOP 32 Drift Masters Battles"].stream_label == "Red Bull Motorsports (YouTube)"
    assert r7["Qualification Round 7 – 1st Session"].stream_url == "https://www.youtube.com/watch?v=vPhV55wK1JA"
    # one "Qualifying" stream per day on dm.gp covers both qualifying sessions
    assert r7["Qualification Round 7 – 2nd Session"].stream_url == "https://www.youtube.com/watch?v=vPhV55wK1JA"
    assert r7["TOP 8 Battles"].stream_label == "Red Bull Motorsports (YouTube)"
