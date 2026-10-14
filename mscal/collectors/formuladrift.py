# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Formula DRIFT (US pro drift championship), from formulad.com.

Site structure (checked October 2026; Next.js + Payload CMS, whose REST API is
not public, so the server-rendered HTML is read):

* ``/schedule`` links every round of the current season as ``/schedule/<year>/<slug>``
  (round cards and an "up next" banner). Cards carry ``<time datetime>`` (first day),
  the range text ("MAY 07 - MAY 09"), the title and "City, State, USA".
* Round pages: a header ``h3`` with ``<time datetime="2026-10-23">OCT 23 - OCT 24</time>``
  and "LONG BEACH, CALIFORNIA, USA"; the "Track" section has the venue name and
  street address ("… Long Beach, CA, 90802"); the schedule is
  ``[aria-label="Event schedule"] article[aria-label="<weekday, date>"]`` with
  ``dl > div > dt time`` ("6:15PM - 9:15PM", local track time) and ``dd`` text.
* Sessions: "PRACTICE" rows are free practice. Qualifying and the battles appear as
  livestream rows: "LIVESTREAM: <link> (QUALIFYING)", "(MAIN EVENT: TOP 32)",
  "(OPENING CEREMONY + MAIN EVENT: TOP 16)". The link in the row is the stream.
  Warm-ups, ceremonies, box office and the like are skipped.
* Rounds without a published schedule become an all-day placeholder.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Session, SessionType
from .base import Collector, unique_names
from .imsa import STATE_ZONES

log = logging.getLogger(__name__)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
#: full US state / Canadian province names used in page headers -> code
STATE_NAMES = {
    "california": "CA", "georgia": "GA", "florida": "FL", "connecticut": "CT",
    "indiana": "IN", "washington": "WA", "nevada": "NV", "new jersey": "NJ", "texas": "TX",
    "new york": "NY", "pennsylvania": "PA", "illinois": "IL", "wisconsin": "WI",
    "arizona": "AZ", "utah": "UT", "colorado": "CO", "oregon": "OR", "ohio": "OH",
    "michigan": "MI", "virginia": "VA", "north carolina": "NC", "tennessee": "TN",
    "ontario": "ON", "quebec": "QC", "missouri": "MO", "massachusetts": "MA",
}


class FormulaDriftCollector(Collector):
    """Config keys: ``base_url``, ``venue_timezones`` ({venue: zone}), ``session_types``."""

    required = ("series", "base_url")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {"series": "FD", "championship": "Formula DRIFT", "discipline": "Drift",
                "base_url": "https://www.formulad.com", "info_url": "https://www.formulad.com"}

    def collect(self, season: int) -> list[Session]:
        base = self.cfg["base_url"].rstrip("/")
        r = self.http.get(f"{base}/schedule")
        r.raise_for_status()
        rounds = parse_listing(r.text, season)
        if not rounds:
            raise RuntimeError(f"formuladrift: no rounds listed for {season}")
        wanted = {SessionType(t) for t in self.cfg.get("session_types", ["FP", "QUALI", "RACE"])}
        out: list[Session] = []
        for slug, card in rounds.items():
            try:
                out += self._round(season, base, slug, card, wanted)
            except Exception as e:
                log.warning("formuladrift: %s skipped: %s", slug, e)
        return out

    def _round(self, season, base, slug, card, wanted) -> list[Session]:
        url = f"{base}/schedule/{season}/{slug}"
        page = None
        try:
            r = self.http.get(url)
            r.raise_for_status()
            page = parse_round_page(r.text, season)
        except Exception as e:
            log.info("formuladrift: %s page unavailable (%s); using listing card", slug, e)
        info = {**(card or {}), **{k: v for k, v in (page or {}).items() if v}}
        if not info.get("title"):
            return []
        tzname = self._tz(info)
        location = ", ".join(x for x in (info.get("venue"), info.get("address") or info.get("place")) if x)
        common = dict(series=self.series, championship=self.championship,
                      discipline=self.discipline, season=season, event_name=info["title"].title(),
                      tz=tzname, location=location,
                      geo=self.geo.lookup(info.get("address") or info.get("place") or "") if (info.get("address") or info.get("place")) else None,
                      info_url=url)
        sessions = build_sessions((page or {}).get("rows", []), ZoneInfo(tzname))
        out = [Session(**common, session_type=k, name=n, start=s, end=e, stream_url=link,
                       stream_label="Formula DRIFT live" if link else None, key=f"{slug}|{n}")
               for k, n, s, e, link in sessions if k in wanted]
        if not sessions and info.get("first"):
            out.append(Session(**common, session_type=SessionType.EVENT, name=common["event_name"],
                               start=info["first"], end=info["last"] + timedelta(days=1),
                               key=f"{slug}|weekend"))
        return out

    def _tz(self, info: dict) -> str:
        for name, tz in (self.cfg.get("venue_timezones") or {}).items():
            if name.lower() in f"{info.get('venue', '')} {info.get('place', '')}".lower():
                return tz
        code = info.get("state")
        if code in STATE_ZONES:
            return STATE_ZONES[code]
        log.warning("formuladrift: no timezone for %s, using America/Los_Angeles", info.get("place"))
        return "America/Los_Angeles"


# ---- parsing (pure functions, unit-tested against captured pages) ------------------
def parse_listing(html: str, season: int) -> dict[str, dict | None]:
    """{slug: card info or None} for every round of ``season`` linked from /schedule."""
    soup = BeautifulSoup(html, "lxml")
    out: dict[str, dict | None] = {}
    for a in soup.select(f'a[href^="/schedule/{season}/"]'):
        slug = a["href"].rstrip("/").rsplit("/", 1)[-1]
        card = None
        art = a.find_parent("article")
        if art is not None:
            t = art.select_one("time[datetime]")
            heads = [h.get_text(strip=True) for h in art.select("h3")]
            place = heads[0] if heads else ""
            first, last = _range(t, season) if t else (None, None)
            card = {"title": heads[-1] if heads else "", "place": place, "first": first, "last": last,
                    "state": _state_from_place(place)}
        if out.get(slug) is None:
            out[slug] = card
    return out


def parse_round_page(html: str, season: int) -> dict:
    """Round page -> {title, first, last, place, state, venue, address, rows}."""
    soup = BeautifulSoup(html, "lxml")
    h1 = soup.select_one("h1 span") or soup.select_one("h1")
    head_time = soup.select_one("h3 time[datetime]")
    first, last = _range(head_time, season) if head_time else (None, None)
    place = ""
    if head_time:
        spans = [s.get_text(strip=True) for s in head_time.find_parent("h3").find_all("span", recursive=False)]
        place = spans[-1] if spans else ""
    venue = address = ""
    for sec in soup.find_all("section"):
        heads = sec.find_all("h3")
        if heads and heads[0].get_text(strip=True).lower() == "track":
            venue = heads[1].get_text(strip=True) if len(heads) > 1 else ""
            p = sec.find("p")
            address = p.get_text(" ", strip=True) if p else ""
    state = None
    m = re.search(r",\s*([A-Z]{2}),?\s*\d{5}", address)
    state = m.group(1) if m else _state_from_place(place)
    rows = []
    for art in soup.select('[aria-label="Event schedule"] article'):
        try:
            day = datetime.strptime(art.get("aria-label", ""), "%A, %B %d, %Y").date()
        except ValueError:
            continue
        for row in art.select("dl > div"):
            t, dd = row.select_one("dt time"), row.select_one("dd")
            if not (t and dd):
                continue
            link = dd.select_one("a[href]")
            rows.append((day, t.get_text(strip=True), " ".join(dd.get_text(" ").split()),
                         link["href"] if link else None))
    return {"title": h1.get_text(strip=True) if h1 else "", "first": first, "last": last,
            "place": place.title(), "state": state, "venue": venue, "address": address, "rows": rows}


def classify(text: str) -> tuple[SessionType, str] | None:
    t = text.upper()
    m = re.search(r"\bTOP\s*(\d+)\b", t)
    if m and ("MAIN EVENT" in t or "BATTLE" in t or "LIVESTREAM" in t):
        return SessionType.RACE, f"Top {m.group(1)}"
    if "QUALIFYING" in t:
        return SessionType.QUALI, "Qualifying"
    if t.strip() == "PRACTICE" or t.startswith("PRACTICE"):
        return SessionType.FP, "Practice"
    return None


def build_sessions(rows: list[tuple], tz: ZoneInfo) -> list[tuple]:
    """(type, name, start, end, stream link) from schedule rows."""
    out = []
    for day, span, text, link in rows:
        c = classify(text)
        if c is None:
            continue
        times = re.findall(r"\d{1,2}:\d{2}\s*[AP]M", span, re.I)
        if not times:
            continue
        start = datetime.combine(day, _clock(times[0]), tz)
        end = datetime.combine(day, _clock(times[1]), tz) if len(times) > 1 else start + timedelta(hours=1)
        if end <= start:
            end += timedelta(days=1)
        out.append((c[0], c[1], start, end, link))
    named = unique_names([(k, n, s, e) for k, n, s, e, _ in out])
    return [(*x, link) for x, (*_, link) in zip(named, out, strict=True)]


def _clock(s: str):
    return datetime.strptime(s.replace(" ", "").upper(), "%I:%M%p").time()


def _range(time_tag, season: int) -> tuple[date | None, date | None]:
    """<time datetime="2026-05-07">MAY 07 - MAY 09</time> -> (first, last)."""
    try:
        first = date.fromisoformat(time_tag["datetime"][:10])
    except (KeyError, ValueError):
        return None, None
    parts = re.findall(r"([A-Za-z]{3})[A-Za-z]*\s+(\d{1,2})", time_tag.get_text())
    last = first
    if len(parts) >= 2:
        mon, day = parts[-1]
        last = date(first.year, _MONTHS[mon[:3].lower()], int(day))
        if last < first:
            last = date(first.year + 1, last.month, last.day)
    return first, last


def _state_from_place(place: str) -> str | None:
    """'Long Beach, California, USA' -> 'CA'."""
    parts = [p.strip().lower() for p in place.split(",")]
    for p in parts[1:]:
        if p in STATE_NAMES:
            return STATE_NAMES[p]
    return None
