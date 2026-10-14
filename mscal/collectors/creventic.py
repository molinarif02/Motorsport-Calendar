# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Michelin 24H Series (Creventic), from 24hseries.com.

Site structure (checked October 2026):

* ``/races`` lists every race as ``a.raceItem[data-year]`` cards with the weekend's
  start/end dates (``.dateWrapper .start/.end``), the venue (``.infoContent
  .smallTitle``) and the race name (first ``.infoContent .mediumTitle``).
* Each race page carries a "Time Schedule" block once the timetable is published:
  ``.timeschedule .entry[data-date="January 8, 2027 10:30"]`` with ``.name`` and
  ``.startTime/.endTime[data-tz][data-time]`` (track time + IANA zone). A YouTube
  icon marks sessions that are streamed.
* The race is a ``Start <race>`` row followed by a ``Finish <race>`` row (no date,
  only the end time). 12H races can be split into parts with one Start/Finish pair
  each. "Start Grid" is the grid walk, not the race.
* Qualifying runs per class group (TCE/GT4/GTX, 992, GT3) in 12-minute slots; by
  default consecutive slots are merged into one "Qualifying (all classes)" event.
* Past races replace the timetable with results; races whose timetable is not out
  yet become an all-day placeholder over the weekend from the listing.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Session, SessionType
from .base import Collector, unique_names

log = logging.getLogger(__name__)
_MONTHS = {m: i for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_JS_TZ = re.compile(r'timezoneDateToLocalTimezone\(\s*"([^"]+)"\s*,\s*"(\d{4}-\d\d-\d\d \d\d:\d\d):\d\d"\)')


class CreventicCollector(Collector):
    """Config keys: ``base_url``, ``merge_qualifying`` (true), ``session_types``,
    ``qualifying_gap_minutes`` (30, max gap between merged qualifying slots)."""

    required = ("series", "base_url")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {"series": "24H", "championship": "Michelin 24H Series", "discipline": "Endurance",
                "base_url": "https://www.24hseries.com", "info_url": "https://www.24hseries.com"}

    def collect(self, season: int) -> list[Session]:
        base = self.cfg["base_url"].rstrip("/")
        r = self.http.get(f"{base}/races")
        r.raise_for_status()
        races = parse_listing(r.text, season)
        if not races:
            raise RuntimeError(f"24h: no races listed for {season}")
        wanted = {SessionType(t) for t in self.cfg.get("session_types", ["FP", "QUALI", "RACE"])}
        out: list[Session] = []
        for race in races:
            try:
                page = self.http.get(base + race["href"])
                page.raise_for_status()
                out += self._race_sessions(season, race, page.text, wanted, base + race["href"])
            except Exception as e:  # one broken race page must not drop the season
                log.warning("24h: %s skipped: %s", race["name"], e)
        return out

    def _race_sessions(self, season: int, race: dict, html: str, wanted: set, url: str) -> list[Session]:
        rows, tzname = parse_race_page(html)
        tzname = tzname or "UTC"
        location = race["venue"]
        geo = self.geo.lookup(location) if location else None
        common = dict(series=self.series, championship=self.championship, discipline=self.discipline,
                      season=season, event_name=race["name"], tz=tzname, location=location,
                      geo=geo, info_url=url)
        sessions = build_sessions(rows, race["name"], ZoneInfo(tzname),
                                  merge_qualifying=self.cfg.get("merge_qualifying", True),
                                  gap=timedelta(minutes=int(self.cfg.get("qualifying_gap_minutes", 30))))
        out = [Session(**common, session_type=t, name=n, start=s, end=e, key=f"{race['slug']}|{n}")
               for t, n, s, e in sessions if t in wanted]
        if not rows:  # timetable not published yet: whole-weekend placeholder
            out.append(Session(**common, session_type=SessionType.EVENT, name=race["name"],
                               start=race["start"], end=race["end"] + timedelta(days=1),
                               key=f"{race['slug']}|weekend"))
        return out


# ---- parsing (pure functions, unit-tested against captured pages) ------------------
def parse_listing(html: str, season: int) -> list[dict]:
    """Race cards of ``season`` from the /races page."""
    soup = BeautifulSoup(html, "lxml")
    races, seen = [], set()
    for a in soup.select(f'a.raceItem[data-year="{season}"]'):
        href = a.get("href", "")
        if href in seen or "/races/" not in href:
            continue
        seen.add(href)
        titles = [t.get_text(strip=True) for t in a.select(".infoContent .mediumTitle")
                  if "year" not in (t.get("class") or [])]
        venue = a.select_one(".infoContent .smallTitle")
        races.append({
            "href": href, "slug": href.rstrip("/").rsplit("/", 1)[-1],
            "name": titles[0] if titles else href,
            "venue": venue.get_text(strip=True) if venue else "",
            "start": _card_date(a.select_one(".dateWrapper .start")),
            "end": _card_date(a.select_one(".dateWrapper .end")),
        })
    return [r for r in races if r["start"] and r["end"]]


def _card_date(node) -> date | None:
    """<span class=start><span>14</span><span>May</span><span>2027</span></span> -> date."""
    if node is None:
        return None
    parts = [s.get_text(strip=True) for s in node.select(".day")]
    try:
        return date(int(parts[2]), _MONTHS[parts[1][:3].lower()], int(parts[0]))
    except (IndexError, KeyError, ValueError):
        return None


def parse_race_page(html: str) -> tuple[list[dict], str | None]:
    """Timetable rows ``{date, name, start, end, youtube}`` and the race's IANA zone."""
    soup = BeautifulSoup(html, "lxml")
    rows = []
    tz = None
    for e in soup.select(".timeschedule .entry"):
        st, en = e.select_one(".startTime"), e.select_one(".endTime")
        tz = tz or (st.get("data-tz") if st else None) or (en.get("data-tz") if en else None)
        name = e.select_one("span.name")
        rows.append({
            "date": (e.get("data-date") or "").strip(),
            "name": " ".join(name.get_text().split()) if name else "",
            "start": (st.get("data-time") if st else None) or "",
            "end": (en.get("data-time") if en else None) or "",
            "youtube": e.select_one('a[href*="youtube"]') is not None,
        })
    if tz is None:
        m = _JS_TZ.search(html)
        tz = m.group(1) if m else None
    return rows, tz


def classify(name: str) -> SessionType | None:
    """Row name -> session type; None for rows that are not sessions (Finish, podium...)."""
    n = name.lower()
    if n.startswith("finish"):
        return None
    if n.startswith("start") and not n.startswith("start grid"):
        return SessionType.RACE
    if "track day" in n:
        return SessionType.OTHER
    if "qualifying" in n:
        return SessionType.QUALI
    if "practice" in n:
        return SessionType.FP
    return SessionType.OTHER


def build_sessions(rows: list[dict], race_name: str, tz: ZoneInfo, merge_qualifying: bool = True,
                   gap: timedelta = timedelta(minutes=30)) -> list[tuple]:
    """Turn timetable rows into ``(type, name, start, end)`` tuples (aware datetimes)."""
    out: list[list] = []
    race_hours = _race_hours(race_name)
    for i, row in enumerate(rows):
        kind = classify(row["name"])
        if kind is None or not row["date"] or not _is_time(row["start"]):
            continue
        start = datetime.strptime(row["date"], "%B %d, %Y %H:%M").replace(tzinfo=tz)
        if kind == SessionType.RACE:
            end = _race_end(rows, i, start, race_hours)
            name = "Race" + _part_suffix(row["name"])
        else:
            end = _at(start, row["end"]) if _is_time(row["end"]) else start + timedelta(hours=1)
            name = row["name"]
        if end <= start:
            end += timedelta(days=1)
        if (merge_qualifying and kind == SessionType.QUALI and out and out[-1][0] == SessionType.QUALI
                and start - out[-1][3] <= gap):
            out[-1][1], out[-1][3] = "Qualifying (all classes)", max(out[-1][3], end)
            continue
        out.append([kind, name, start, end])
    return unique_names([tuple(x) for x in out])


def _race_end(rows: list[dict], i: int, start: datetime, hours: int | None) -> datetime:
    """End of the race part starting at rows[i]: the next Finish row's end time."""
    for row in rows[i + 1:]:
        n = row["name"].lower()
        if n.startswith("start") and not n.startswith("start grid"):
            break  # next part starts before any finish: unknown end
        if n.startswith("finish") and _is_time(row["end"]):
            end = _at(start, row["end"])
            if row["date"]:
                end = datetime.strptime(row["date"].rsplit(" ", 1)[0] + " " + row["end"],
                                        "%B %d, %Y %H:%M").replace(tzinfo=start.tzinfo)
            while end <= start:
                end += timedelta(days=1)
            return end
    return start + timedelta(hours=hours or 12)


def _at(day: datetime, hhmm: str) -> datetime:
    h, m = (int(x) for x in hhmm.split(":"))
    return day.replace(hour=h, minute=m)


def _is_time(v: str) -> bool:
    return bool(re.fullmatch(r"\d{1,2}:\d{2}", v or ""))


def _race_hours(name: str) -> int | None:
    m = re.search(r"\b(\d{1,2})\s*H\b", name, re.I)
    return int(m.group(1)) if m else None


def _part_suffix(name: str) -> str:
    m = re.search(r"part\s*(\d)", name, re.I)
    return f" (part {m.group(1)})" if m else ""
