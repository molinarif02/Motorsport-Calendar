# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""IMSA WeatherTech SportsCar Championship, from imsa.com.

Site structure (checked October 2026):

* Season pages list rounds as ``.schedule-item`` cards: ``.schedule-title`` (the race
  name), ``.schedule-date`` ("Sep 30 - Oct 3", no year), ``.schedule-location``,
  ``.schedule-duration`` ("DURATION: 10 Hours", "100 Minutes", "N/A" for tests) and an
  "Event Details" link. Season URLs are not consistent between years
  (``/weathertech/2027-schedule/`` vs ``/weathertech/weathertech-2026-schedule/``), so
  they are read from the season ``<select>`` on any schedule page.
* Event pages carry the weekend's on-track schedule for **all** IMSA series in
  ``.race-event-schedule-container``: ``.day-event-header`` ("Saturday, October 3,
  2026") followed by rows of ``.event-time`` ("12:10 PM to 10:10 PM ET") and
  ``.event-name``. Times are always US Eastern, even on the West Coast.
* WeatherTech practice/qualifying rows name the series ("Practice 1 - WeatherTech
  Championship", "WeatherTech Championship Qualifying"); the race row is just the
  race name ("Motul Petit Le Mans"). Races are matched by name, or else by the
  duration from the season card. Races over midnight are split into two rows
  (Rolex 24: "1:40 PM to 11:59 PM", then "12:00 AM to 1:40 PM") and are merged.
* The venue's US state / Canadian province (``.track-location``) gives the local zone.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import httpx
from bs4 import BeautifulSoup

from ..models import Session, SessionType
from .base import Collector, unique_names

log = logging.getLogger(__name__)

#: abbreviations used on imsa.com -> zone the times are written in
SOURCE_ZONES = {"ET": "America/New_York", "CT": "America/Chicago",
                "MT": "America/Denver", "PT": "America/Los_Angeles"}

#: state / province code of the venue -> its local zone
STATE_ZONES = {
    **{s: "America/New_York" for s in
       ("FL", "GA", "VA", "NY", "MI", "NC", "SC", "OH", "PA", "NJ", "MD", "CT", "MA", "NH", "WV")},
    "IN": "America/Indiana/Indianapolis", "KY": "America/New_York",
    **{s: "America/Chicago" for s in ("WI", "IL", "TX", "MN", "IA", "MO", "AL", "TN", "KS", "LA")},
    "CO": "America/Denver", "UT": "America/Denver", "AZ": "America/Phoenix",
    "CA": "America/Los_Angeles", "NV": "America/Los_Angeles", "OR": "America/Los_Angeles",
    "WA": "America/Los_Angeles", "ON": "America/Toronto", "QC": "America/Toronto",
}
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_TIME = re.compile(r"(\d{1,2}:\d{2}\s*[AP]M)\s*to\s*(\d{1,2}:\d{2}\s*[AP]M)\s*([ECMP]T)?", re.I)


class ImsaCollector(Collector):
    """Config keys: ``season_index_url``, ``season_urls`` ({year: url} overrides),
    ``series_regex`` (rows of the followed series, default "WeatherTech"),
    ``venue_timezones`` ({venue name: zone} overrides), ``session_types``."""

    required = ("series", "season_index_url")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {"series": "IMSA", "championship": "IMSA WeatherTech SportsCar Championship",
                "discipline": "Endurance", "info_url": "https://www.imsa.com",
                "season_index_url": "https://www.imsa.com/weathertech/weathertech-2026-schedule/",
                "series_regex": "WeatherTech"}

    def collect(self, season: int) -> list[Session]:
        url = self._season_url(season)
        r = self.http.get(url)
        r.raise_for_status()
        cards = parse_season(r.text)
        if not cards:
            raise RuntimeError(f"imsa: no events on {url}")
        wanted = {SessionType(t) for t in self.cfg.get("session_types", ["FP", "QUALI", "RACE"])}
        out: list[Session] = []
        for card in cards:
            try:
                out += self._event(season, card, wanted)
            except Exception as e:  # one broken event page must not drop the season
                log.warning("imsa: %s skipped: %s", card["title"], e)
        return out

    def _season_url(self, season: int) -> str:
        """Season page URL: explicit override, else the season selector, else patterns."""
        if str(season) in {str(k) for k in (self.cfg.get("season_urls") or {})}:
            return {str(k): v for k, v in self.cfg["season_urls"].items()}[str(season)]
        cached = self.state.kv_get("imsa_season_url", str(season), ttl=timedelta(days=7))
        if cached:
            return cached
        r = self.http.get(self.cfg["season_index_url"])
        r.raise_for_status()
        options = parse_season_options(r.text)
        url = options.get(season)
        if url is None:
            raise RuntimeError(f"imsa: season {season} not published (known: {sorted(options)})")
        self.state.kv_set("imsa_season_url", str(season), url)
        return url

    def _event(self, season: int, card: dict, wanted: set) -> list[Session]:
        page = None
        if card["link"]:
            try:
                r = self.http.get(card["link"])
                r.raise_for_status()
                page = parse_event_page(r.text, season)
            except httpx.HTTPError as e:  # page missing/broken: fall back to the card
                log.warning("imsa: event page for %s unavailable (%s); using season card", card["title"], e)
        venue = (page or {}).get("venue") or card["location"]
        tzname = self._venue_tz(venue, (page or {}).get("region"))
        location = ", ".join(x for x in (venue, (page or {}).get("address")) if x)
        common = dict(series=self.series, championship=self.championship,
                      discipline=self.discipline, season=season, event_name=card["title"],
                      tz=tzname, location=location, geo=self.geo.lookup(venue) if venue else None,
                      info_url=card["link"] or self.cfg.get("info_url"))
        sessions = []
        if page and page["rows"]:
            sessions = select_sessions(page["rows"], card, re.compile(self.cfg["series_regex"], re.I))
        out = [Session(**common, session_type=t, name=n,
                       start=s.astimezone(ZoneInfo(tzname)), end=e.astimezone(ZoneInfo(tzname)),
                       key=f"{card['slug']}|{n}")
               for t, n, s, e in sessions if t in wanted]
        if not sessions:  # schedule not published yet: whole-weekend placeholder
            first, last = (page or {}).get("dates") or _card_dates(card["date"], season)
            if first:
                out.append(Session(**common, session_type=SessionType.EVENT, name=card["title"],
                                   start=first, end=last + timedelta(days=1),
                                   key=f"{card['slug']}|weekend"))
        return out

    def _venue_tz(self, venue: str, region: str | None) -> str:
        for name, tz in (self.cfg.get("venue_timezones") or {}).items():
            if name.lower() in (venue or "").lower():
                return tz
        if region and region in STATE_ZONES:
            return STATE_ZONES[region]
        log.warning("imsa: no timezone for %s (%s), using America/New_York", venue, region)
        return "America/New_York"


# ---- parsing (pure functions, unit-tested against captured pages) ------------------
def parse_season_options(html: str) -> dict[int, str]:
    """{year: season page URL} from the season <select>."""
    soup = BeautifulSoup(html, "lxml")
    out = {}
    for o in soup.select("option"):
        t = o.get_text(strip=True)
        if re.fullmatch(r"20\d\d", t) and o.get("value", "").startswith("http"):
            out[int(t)] = o["value"]
    return out


def parse_season(html: str) -> list[dict]:
    """Season page -> list of round cards."""
    soup = BeautifulSoup(html, "lxml")
    cards = []
    for item in soup.select(".schedule-item"):
        title = item.select_one(".schedule-title")
        link = next((a.get("href") for a in item.select("a") if "event details" in a.get_text().lower()), None)
        dur = item.select_one(".schedule-duration")
        cards.append({
            "title": " ".join(title.get_text().split()) if title else "",
            "date": item.select_one(".schedule-date").get_text(strip=True) if item.select_one(".schedule-date") else "",
            "location": item.select_one(".schedule-location").get_text(strip=True) if item.select_one(".schedule-location") else "",
            "duration": parse_duration(dur.get_text() if dur else ""),
            "link": link,
            "slug": (link or (title.get_text() if title else "")).rstrip("/").rsplit("/", 1)[-1],
        })
    return [c for c in cards if c["title"]]


def parse_duration(text: str) -> timedelta | None:
    """'DURATION: 2 Hours 40 Minutes' -> 2:40; 'N/A' -> None."""
    h = re.search(r"(\d+)\s*Hour", text, re.I)
    m = re.search(r"(\d+)\s*Min", text, re.I)
    if not h and not m:
        return None
    return timedelta(hours=int(h.group(1)) if h else 0, minutes=int(m.group(1)) if m else 0)


def parse_event_page(html: str, season: int) -> dict:
    """Event page -> {venue, address, region, dates, rows[(name, start, end)]}."""
    soup = BeautifulSoup(html, "lxml")
    venue = soup.select_one(".event-when .track-name")
    lines = [d.get_text(" ", strip=True) for d in soup.select(".event-when .track-location > div")]
    region = None
    for line in lines:
        m = re.search(r",\s*([A-Z]{2})\b", line)
        if m:
            region = m.group(1)
    dates = None
    when = soup.select_one(".event-when .event-datetime")
    if when:
        found = re.findall(r"[A-Z][a-z]+ \d{1,2}, \d{4}", when.get_text(" ", strip=True))
        if len(found) >= 1:
            parsed = [datetime.strptime(f, "%B %d, %Y").date() for f in found[:2]]
            dates = (parsed[0], parsed[-1])
    rows = []
    day = None
    container = soup.select_one(".race-event-schedule-container")
    for e in (container.select(".day-event-header, .day-event-details-container") if container else []):
        if "day-event-header" in e.get("class", []):
            try:
                day = datetime.strptime(e.get_text(strip=True), "%A, %B %d, %Y").date()
            except ValueError:
                day = None
            continue
        t, n = e.select_one(".event-time"), e.select_one(".event-name")
        m = _TIME.search(t.get_text(" ", strip=True)) if t else None
        if not (day and m and n):
            continue
        zone = ZoneInfo(SOURCE_ZONES.get((m.group(3) or "ET").upper(), "America/New_York"))
        start = datetime.combine(day, _clock(m.group(1)), zone)
        end = datetime.combine(day, _clock(m.group(2)), zone)
        if end <= start:
            end += timedelta(days=1)
        rows.append((" ".join(n.get_text().split()), start, end))
    return {"venue": venue.get_text(strip=True) if venue else "",
            "address": ", ".join(lines[:2]) if lines else "", "region": region,
            "dates": dates, "rows": rows}


def select_sessions(rows: list[tuple], card: dict, series_rx: re.Pattern) -> list[tuple]:
    """Pick the followed series' sessions: (type, name, start, end), races merged."""
    out: list[list] = []
    title = _norm(card["title"])
    for name, start, end in rows:
        kind = None
        if series_rx.search(name):
            if "qualif" in name.lower():
                kind, label = SessionType.QUALI, "Qualifying"
            elif "practice" in name.lower() or "test" in name.lower():
                kind, label = SessionType.FP, re.split(r"\s+-\s+", name)[0]
            elif "race" in name.lower():
                kind, label = SessionType.RACE, "Race"
        elif _norm(name) == title or (card["duration"] and " - " not in name
                                      and abs((end - start) - card["duration"]) <= timedelta(minutes=15)):
            kind, label = SessionType.RACE, "Race"
        if kind is None:
            continue
        prev = out[-1] if out else None
        if (prev and kind == SessionType.RACE and prev[0] == SessionType.RACE
                and start - prev[3] <= timedelta(minutes=5)):
            prev[3] = end  # race split at midnight -> one event
            continue
        out.append([kind, label, start, end])
    return unique_names([tuple(x) for x in out])


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def _clock(s: str):
    return datetime.strptime(s.replace(" ", "").upper(), "%I:%M%p").time()


def _card_dates(text: str, season: int) -> tuple[date | None, date | None]:
    """'Sep 30 - Oct 3' (+ season year) -> (first, last)."""
    parts = re.findall(r"([A-Za-z]{3})[a-z]*\s+(\d{1,2})", text)
    if not parts:
        return None, None
    d = [date(season, _MONTHS[m[:3].lower()], int(day)) for m, day in parts[:2]]
    return d[0], d[-1]
