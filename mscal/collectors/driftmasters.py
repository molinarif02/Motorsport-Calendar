# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Drift Masters (European drift championship), from dm.gp.

Site structure (checked October 2026). dm.gp is an Astro site; its components carry
their data as JSON in ``<astro-island props="...">`` attributes, where every value is
wrapped as ``[0, value]`` (or ``[1, [...]]`` for arrays).

* Season page ``/seasons/drift-masters-<year>/``: links to
  ``/rounds/round-N`` plus, per round card, a ``CommonFlag`` island (``countryISO``,
  ISO 3166 alpha-3) followed by an ``AddToCalendarButton`` island (``roundNumber``,
  ``startDate``/``endDate`` in UTC, ``circuitName``).
* Round page: a ``RoundTabsContent`` island with ``seasonId`` and ``roundId``; the
  page title names the event and its city ("… Grand Finale 2026 | Warsaw").
* JSON API (same origin, used by the site's own front end):
  ``/umbraco/api/v1/seasons/{seasonId}/rounds/{roundId}/event`` returns blocks; the
  ``roundTimetablesBlock`` has days ``{date, lines: [{time, bodyHTML}]}``. Line times
  carry a dummy date (1753-01-01) and read as local track time (gates 15:00,
  qualifying 17:30, gates closed 23:00 in Warsaw), despite their "Z" suffix.
  ``…/live`` returns the stream links per day and the channel links.
* Streams: qualifying is on the Drift Masters YouTube channel, the battles on Red
  Bull Motorsports' YouTube channel. Exact YouTube videos listed by ``/live`` are
  used directly; everything else falls to the ``DM`` streaming rules in config.yaml.
* Sessions: "Qualification …" lines are qualifying; "TOP 32/16/8 … Battles" lines
  are the competition (labelled Race). A session ends at the next timetable line.
  Shows, gates and podium lines are skipped. Rounds without a timetable become an
  all-day placeholder from the season card.
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Session, SessionType
from .base import Collector, unique_names

log = logging.getLogger(__name__)

#: ISO 3166 alpha-3 -> (country name, timezone) for countries drift series visit
COUNTRIES = {
    "ITA": ("Italy", "Europe/Rome"), "ESP": ("Spain", "Europe/Madrid"),
    "IRL": ("Ireland", "Europe/Dublin"), "GBR": ("United Kingdom", "Europe/London"),
    "FIN": ("Finland", "Europe/Helsinki"), "LVA": ("Latvia", "Europe/Riga"),
    "LTU": ("Lithuania", "Europe/Vilnius"), "EST": ("Estonia", "Europe/Tallinn"),
    "DEU": ("Germany", "Europe/Berlin"), "POL": ("Poland", "Europe/Warsaw"),
    "FRA": ("France", "Europe/Paris"), "BEL": ("Belgium", "Europe/Brussels"),
    "NLD": ("Netherlands", "Europe/Amsterdam"), "AUT": ("Austria", "Europe/Vienna"),
    "CZE": ("Czechia", "Europe/Prague"), "SVK": ("Slovakia", "Europe/Bratislava"),
    "HUN": ("Hungary", "Europe/Budapest"), "SVN": ("Slovenia", "Europe/Ljubljana"),
    "HRV": ("Croatia", "Europe/Zagreb"), "PRT": ("Portugal", "Europe/Lisbon"),
    "SWE": ("Sweden", "Europe/Stockholm"), "NOR": ("Norway", "Europe/Oslo"),
    "DNK": ("Denmark", "Europe/Copenhagen"), "GRC": ("Greece", "Europe/Athens"),
    "CHE": ("Switzerland", "Europe/Zurich"), "TUR": ("Türkiye", "Europe/Istanbul"),
    "ARE": ("United Arab Emirates", "Asia/Dubai"), "SAU": ("Saudi Arabia", "Asia/Riyadh"),
}


class DriftMastersCollector(Collector):
    """Config keys: ``base_url``, ``season_slug`` (default ``drift-masters-{year}``),
    ``country_timezones`` ({ISO3: zone} overrides), ``session_types``."""

    required = ("series", "base_url")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {"series": "DM", "championship": "Drift Masters", "discipline": "Drift",
                "base_url": "https://dm.gp", "info_url": "https://dm.gp",
                "season_slug": "drift-masters-{year}",
                "session_types": ["FP", "QUALI", "RACE"]}

    def collect(self, season: int) -> list[Session]:
        base = self.cfg["base_url"].rstrip("/")
        slug = self.cfg["season_slug"].format(year=season)
        r = self.http.get(f"{base}/seasons/{slug}/")
        r.raise_for_status()
        cards = parse_season(r.text)
        if not cards:
            raise RuntimeError(f"driftmasters: no rounds for {season}")
        wanted = {SessionType(t) for t in self.cfg["session_types"]}
        out: list[Session] = []
        for card in cards:
            try:
                out += self._round(season, base, slug, card, wanted)
            except Exception as e:  # one broken round must not drop the season
                log.warning("driftmasters: round %s skipped: %s", card["round"], e)
        return out

    def _round(self, season, base, slug, card, wanted) -> list[Session]:
        iso = card["country"]
        overrides = self.cfg.get("country_timezones") or {}
        country, tzname = COUNTRIES.get(iso, (iso, overrides.get(iso, "UTC")))
        tzname = overrides.get(iso, tzname)
        url = f"{base}/seasons/{slug}/rounds/round-{card['round']}"
        city, timetable, live = None, None, None
        try:
            page = self.http.get(url)
            page.raise_for_status()
            ids, city = parse_round_page(page.text)
            api = f"{base}/umbraco/api/v1/seasons/{ids['seasonId']}/rounds/{ids['roundId']}"
            timetable = parse_timetable(self.http.get(f"{api}/event").raise_for_status().json())
            live = self.http.get(f"{api}/live").raise_for_status().json()
        except Exception as e:
            log.info("driftmasters: round %s details unavailable (%s); using season card", card["round"], e)
        name = f"Round {card['round']} – {city or card['circuit']}"
        common = dict(series=self.series, championship=self.championship,
                      discipline=self.discipline, season=season, event_name=name, tz=tzname,
                      location=f"{card['circuit']}, {country}",
                      geo=self.geo.lookup(f"{card['circuit']}, {country}"), info_url=url)
        sessions = build_sessions(timetable or [], ZoneInfo(tzname))
        out = []
        for kind, sname, start, end, day in sessions:
            if kind not in wanted:
                continue
            link, label = stream_for(live or {}, kind, sname, day)
            out.append(Session(**common, session_type=kind, name=sname, start=start, end=end,
                               stream_url=link, stream_label=label,
                               key=f"r{card['round']}|{sname}"))
        if not sessions:
            tz = ZoneInfo(tzname)
            first = card["start"].astimezone(tz).date()
            last = card["end"].astimezone(tz).date()
            out.append(Session(**common, session_type=SessionType.EVENT, name=name,
                               start=first, end=last + timedelta(days=1),
                               key=f"r{card['round']}|weekend"))
        return out


# ---- parsing (pure functions, unit-tested against captured responses) ---------------
def astro_props(raw: str):
    """Decode an Astro island ``props`` attribute: [0, v] -> v, [1, [...]] -> list."""
    def dec(v):
        if isinstance(v, list) and len(v) == 2 and isinstance(v[0], int):
            return [dec(x) for x in v[1]] if v[0] == 1 else dec(v[1])
        if isinstance(v, dict):
            return {k: dec(x) for k, x in v.items()}
        return v
    return dec(json.loads(unescape(raw)))


def parse_season(html: str) -> list[dict]:
    """Season page -> [{round, start, end, circuit, country}] from the card islands."""
    soup = BeautifulSoup(html, "lxml")
    cards, country = [], None
    for isl in soup.find_all("astro-island"):
        comp = isl.get("component-url", "")
        if "CommonFlag" in comp:
            country = astro_props(isl.get("props", "{}")).get("countryISO")
        elif "AddToCalendarButton" in comp:
            p = astro_props(isl.get("props", "{}"))
            cards.append({
                "round": int(p["roundNumber"]),
                "start": datetime.fromisoformat(p["startDate"].replace("Z", "+00:00")),
                "end": datetime.fromisoformat(p["endDate"].replace("Z", "+00:00")),
                "circuit": p.get("circuitName", ""), "country": country or "",
            })
            country = None
    # the same card can appear twice (e.g. "next round" banners): keep one per round
    return list({c["round"]: c for c in cards}.values())


def parse_round_page(html: str) -> tuple[dict, str | None]:
    """Round page -> ({seasonId, roundId}, city from the title)."""
    soup = BeautifulSoup(html, "lxml")
    isl = next(i for i in soup.find_all("astro-island") if "RoundTabsContent" in i.get("component-url", ""))
    p = astro_props(isl.get("props", "{}"))
    title = soup.title.get_text() if soup.title else ""
    city = title.rsplit("|", 1)[-1].strip() if "|" in title else None
    return {"seasonId": p["seasonId"], "roundId": p["roundId"]}, city


def parse_timetable(event_json: dict) -> list[tuple[date, list[tuple[str, str]]]]:
    """API /event response -> [(day, [(HH:MM, text)])]."""
    out = []
    for block in event_json.get("blocks", []):
        if block.get("contentType") != "roundTimetablesBlock":
            continue
        for day in block.get("days", []):
            d = date.fromisoformat(day["date"][:10])
            lines = []
            for line in day.get("lines", []):
                text = " ".join(BeautifulSoup(line.get("bodyHTML") or "", "lxml").get_text(" ").split())
                lines.append((line["time"][11:16], text))
            out.append((d, lines))
    return out


def classify(text: str) -> SessionType | None:
    t = text.lower()
    if "qualif" in t:
        return SessionType.QUALI
    if re.search(r"\btop\s*\d+\b", t) and "battle" in t:
        return SessionType.RACE
    if "practice" in t or "training" in t:
        return SessionType.FP
    return None


def build_sessions(days, tz: ZoneInfo) -> list[tuple]:
    """(type, name, start, end, day_number); a session ends at the next timetable line."""
    out = []
    for n, (d, lines) in enumerate(days, 1):
        for i, (hhmm, text) in enumerate(lines):
            kind = classify(text)
            if kind is None:
                continue
            start = datetime.combine(d, datetime.strptime(hhmm, "%H:%M").time(), tz)
            nxt = next((h for h, _ in lines[i + 1:] if h > hhmm), None)
            end = (datetime.combine(d, datetime.strptime(nxt, "%H:%M").time(), tz)
                   if nxt else start + timedelta(hours=1))
            out.append((kind, text.strip(), start, end, n))
    named = unique_names([(k, nm, s, e) for k, nm, s, e, _ in out])
    return [(*x, day) for x, (*_, day) in zip(named, out, strict=True)]


def stream_for(live: dict, kind: SessionType, name: str, day: int) -> tuple[str | None, str | None]:
    """Exact YouTube video for a session from dm.gp's /live data, else (None, None).

    Only YouTube links are taken from the site: qualifying is streamed on the Drift
    Masters channel and the battles on Red Bull Motorsports' channel, and those are
    configured as streaming rules (series ``DM`` in config.yaml), which apply whenever
    this returns nothing - e.g. when the site lists a Red Bull TV embed instead.
    """
    for s in live.get(f"embedLiveStreamLinksDay{day}") or []:
        title = (s.get("title") or "").lower()
        if (kind == SessionType.QUALI and "qualif" in title) or (title and title in name.lower()):
            m = re.search(r"youtube\.com/embed/([\w-]+)", s.get("link", ""))
            if m:
                label = "Drift Masters (YouTube)" if kind == SessionType.QUALI else "YouTube live"
                return f"https://www.youtube.com/watch?v={m.group(1)}", label
    return None, None
