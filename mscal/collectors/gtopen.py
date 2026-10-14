# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""International GT Open, from gtopen.gtsport.es.

Site structure (checked October 2026; Next.js + Payload CMS):

* ``/calendario`` embeds the season in its Next.js flight payload
  (``self.__next_f.push([1, "..."])`` chunks) as ``"eventos": [...]``: per round
  ``nombre``, ``fecha`` (race day, UTC timestamp), ``tipo`` (sprint/endurance),
  ``circuito.nombre`` and ``circuito.pais`` (sometimes in Spanish: "Bélgica").
  Only those fields are read. (The payload also carries unrelated CMS data.)
* The session timetable exists only as a PDF, linked from the homepage's
  "NEXT RACE" box (``[class*=nextRace__title]``, ``[class*=nextRace__date]``
  "25.10.2026", and an ``a[href$=.pdf]``). One table holds every GT Sport series
  (GT Open, GT Cup, Euroformula, TCR): day headings
  "Friday/Viernes, 23 October / Octubre 2026" and rows
  "HH.MM/HH.MM  NN´  <series>  <session>", local track time. Race rows give only
  the start and the race length ("Rolling start - 70´"), and the series name may
  sit on the line just before or after the row.
* Because only the next race's PDF is linked, sessions parsed from it are kept
  per round in the state, so they stay in the calendar after the weekend. Other
  rounds are all-day placeholders over the race weekend (Friday to race day).

PDF text is read with pypdf (layout mode). Parsing is line-based and tolerant of
the column order, but a PDF layout change is the most likely failure here.
"""
from __future__ import annotations

import io
import json
import logging
import re
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from bs4 import BeautifulSoup

from ..models import Session, SessionType
from .base import Collector, unique_names

log = logging.getLogger(__name__)

#: country names as used by the site (English and Spanish) -> timezone
COUNTRY_ZONES = {
    "spain": "Europe/Madrid", "españa": "Europe/Madrid", "portugal": "Europe/Lisbon",
    "italy": "Europe/Rome", "italia": "Europe/Rome", "belgium": "Europe/Brussels",
    "bélgica": "Europe/Brussels", "belgica": "Europe/Brussels", "france": "Europe/Paris",
    "francia": "Europe/Paris", "germany": "Europe/Berlin", "alemania": "Europe/Berlin",
    "hungary": "Europe/Budapest", "hungría": "Europe/Budapest", "austria": "Europe/Vienna",
    "netherlands": "Europe/Amsterdam", "países bajos": "Europe/Amsterdam",
    "united kingdom": "Europe/London", "reino unido": "Europe/London",
    "czech republic": "Europe/Prague", "czechia": "Europe/Prague",
}
SERIES_NAMES = ("International GT Open", "GT Cup Europe", "Euroformula Open", "TCR Europe",
                "TCR Euorope", "All categories", "All Series")
_MONTHS = {m: i for i, m in enumerate(
    ["january", "february", "march", "april", "may", "june", "july", "august", "september",
     "october", "november", "december"], 1)}
_DAY = re.compile(r"(Monday|Tuesday|Wednesday|Thursday|Friday|Saturday|Sunday)\s*/\s*\S+,?\s*"
                  r"(\d{1,2})\s+([A-Za-z]+)", re.I)
_ROW = re.compile(r"^\s*(\d{1,2})[.:](\d{2})(?:\s*/\s*(\d{1,2})[.:](\d{2}))?\s+(.*)$")


def pdf_to_text(content: bytes) -> str:
    """PDF bytes -> text, one visual row per line (pypdf layout mode)."""
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(content))
    return "\n".join(p.extract_text(extraction_mode="layout") or "" for p in reader.pages)


class GtOpenCollector(Collector):
    """Config keys: ``base_url``, ``series_name`` (the PDF's category label, default
    "International GT Open"), ``skip_regex`` (rounds to ignore, default tests),
    ``country_timezones`` ({country: zone}), ``session_types``."""

    required = ("series", "base_url")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {"series": "GTOPEN", "championship": "International GT Open", "discipline": "GT",
                "base_url": "https://gtopen.gtsport.es", "info_url": "https://gtopen.gtsport.es",
                "series_name": "International GT Open", "skip_regex": r"\btest\b"}

    def collect(self, season: int) -> list[Session]:
        base = self.cfg["base_url"].rstrip("/")
        r = self.http.get(f"{base}/calendario")
        r.raise_for_status()
        rounds = [e for e in parse_calendar(r.text) if e["race_day"].year == season
                  and not re.search(self.cfg.get("skip_regex") or "$^", e["name"], re.I)]
        if not rounds:
            raise RuntimeError(f"gtopen: no rounds for {season}")
        self._refresh_next_race_schedule(base, rounds)
        wanted = {SessionType(t) for t in self.cfg.get("session_types", ["FP", "QUALI", "RACE"])}
        out: list[Session] = []
        for ev in rounds:
            tzname = self._tz(ev["country"])
            common = dict(series=self.series, championship=self.championship,
                          discipline=self.discipline, season=season,
                          event_name=ev["circuit"].strip(), tz=tzname,
                          location=f"{ev['circuit'].strip()}, {ev['country']}",
                          geo=self.geo.lookup(f"{ev['circuit'].strip()} circuit, {ev['country']}"),
                          info_url=f"{base}/calendario/{ev['id']}")
            cached = self.state.kv_get("gtopen_schedule", ev["id"]) or []
            tz = ZoneInfo(tzname)
            sessions = [(SessionType(k), n, datetime.fromisoformat(s).replace(tzinfo=tz),
                         datetime.fromisoformat(e).replace(tzinfo=tz)) for k, n, s, e in cached]
            out += [Session(**common, session_type=k, name=n, start=s, end=e, key=f"{ev['id']}|{n}")
                    for k, n, s, e in sessions if k in wanted]
            if not sessions:
                day = ev["race_day"]
                out.append(Session(**common, session_type=SessionType.EVENT,
                                   name=common["event_name"], start=day - timedelta(days=2),
                                   end=day + timedelta(days=1), key=f"{ev['id']}|weekend"))
        return out

    def _refresh_next_race_schedule(self, base: str, rounds: list[dict]) -> None:
        """Parse the homepage's next-race PDF and remember its sessions per round."""
        try:
            home = self.http.get(f"{base}/")
            home.raise_for_status()
            nxt = parse_next_race(home.text)
            if not nxt or not nxt.get("pdf"):
                return
            ev = next((e for e in rounds if e["circuit"].strip().lower() == nxt["venue"].lower()
                       and e["race_day"] == nxt["date"]), None)
            if ev is None:
                log.info("gtopen: next race %s not in this season's list", nxt)
                return
            pdf = self.http.get(nxt["pdf"])
            pdf.raise_for_status()
            sessions = parse_schedule(pdf_to_text(pdf.content), ev["race_day"].year,
                                      self.cfg.get("series_name", "International GT Open"))
            if sessions:
                self.state.kv_set("gtopen_schedule", ev["id"],
                                  [[k.value, n, s.isoformat(), e.isoformat()] for k, n, s, e in sessions])
                log.info("gtopen: %d sessions parsed for %s", len(sessions), ev["circuit"])
            else:
                log.warning("gtopen: schedule PDF for %s had no %s sessions (layout changed?)",
                            ev["circuit"], self.cfg.get("series_name"))
        except Exception as e:  # the season list still works without the PDF
            log.warning("gtopen: next-race schedule unavailable: %s", e)

    def _tz(self, country: str) -> str:
        overrides = {k.lower(): v for k, v in (self.cfg.get("country_timezones") or {}).items()}
        key = (country or "").strip().lower()
        return overrides.get(key) or COUNTRY_ZONES.get(key) or "Europe/Madrid"


# ---- parsing (pure functions, unit-tested against captured data) -------------------
def parse_calendar(html: str) -> list[dict]:
    """/calendario -> [{id, name, race_day, type, circuit, country}] from the flight payload."""
    chunks = "".join(json.loads(f'"{m}"') for m in
                     re.findall(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)', html))
    k = chunks.find('"eventos":[')
    if k < 0:
        return []
    start = k + len('"eventos":')
    depth, end = 0, start
    for end in range(start, len(chunks)):
        c = chunks[end]
        if c in "[{":
            depth += 1
        elif c in "]}":
            depth -= 1
            if depth == 0:
                break
    out = []
    for ev in json.loads(chunks[start:end + 1]):
        circuit = ev.get("circuito") or {}
        try:
            day = datetime.fromisoformat(ev["fecha"].replace("Z", "+00:00")).date()
        except (KeyError, ValueError):
            continue
        out.append({"id": ev.get("id", ""), "name": ev.get("nombre", ""), "race_day": day,
                    "type": ev.get("tipo", ""), "circuit": circuit.get("nombre", ""),
                    "country": circuit.get("pais", "")})
    return out


def parse_next_race(html: str) -> dict | None:
    """Homepage -> {venue, date, pdf} of the "NEXT RACE" box."""
    soup = BeautifulSoup(html, "lxml")
    title = soup.select_one('[class*="nextRace__title"]')
    when = soup.select_one('[class*="nextRace__date"]')
    pdf = soup.select_one('[class*="nextRace"] a[href$=".pdf"]')
    if not (title and when):
        return None
    try:
        day = datetime.strptime(when.get_text(strip=True), "%d.%m.%Y").date()
    except ValueError:
        return None
    return {"venue": title.get_text(strip=True), "date": day, "pdf": pdf["href"] if pdf else None}


def _classify(text: str) -> tuple[SessionType, str] | None:
    m = re.search(r"Race\s*[–-]\s*(\d)", text)
    if m:
        return SessionType.RACE, f"Race {m.group(1)}"
    m = re.search(r"Qualifying\s*(\d)?", text)
    if m:
        return SessionType.QUALI, f"Qualifying {m.group(1)}" if m.group(1) else "Qualifying"
    m = re.search(r"Free practice\s*(\d)", text, re.I)
    if m:
        return SessionType.FP, f"Free Practice {m.group(1)}" + (" (Bronze)" if "bronze" in text.lower() else "")
    return None


def _series_in(text: str) -> str | None:
    for s in SERIES_NAMES:
        if s.lower() in text.lower():
            return s
    return None


def parse_schedule(text: str, year: int, series: str = "International GT Open") -> list[tuple]:
    """Timetable text -> [(type, name, start, end)] (naive local datetimes) for ``series``."""
    lines = [" ".join(raw.split()) for raw in text.splitlines()]
    out, day = [], None
    for i, line in enumerate(lines):
        m = _DAY.search(line)
        if m and m.group(3).lower() in _MONTHS:
            day = date(year, _MONTHS[m.group(3).lower()], int(m.group(2)))
            continue
        r = _ROW.match(line)
        if not r or day is None:
            continue
        rest = r.group(5)
        kind = _classify(rest)
        if kind is None:
            continue
        owner = _series_in(rest)
        if owner is None:  # race rows: series name on the neighbouring line
            prev = lines[i - 1] if i else ""
            nxt = lines[i + 1] if i + 1 < len(lines) else ""
            owner = (prev if prev in SERIES_NAMES else None) or (nxt if nxt in SERIES_NAMES else None)
        if owner is None or owner.lower() != series.lower():
            continue
        start = datetime.combine(day, datetime.min.time()).replace(hour=int(r.group(1)), minute=int(r.group(2)))
        if r.group(3):
            end = start.replace(hour=int(r.group(3)), minute=int(r.group(4)))
        else:
            dur = re.search(r"(\d+)\s*´", rest)
            end = start + timedelta(minutes=int(dur.group(1)) if dur else 60)
        if end <= start:
            end += timedelta(days=1)
        out.append((kind[0], kind[1], start, end))
    return unique_names(out)
