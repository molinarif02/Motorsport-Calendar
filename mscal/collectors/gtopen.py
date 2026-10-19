# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""International GT Open, from gtopen.gtsport.es.

Site structure (checked October 2026; Next.js + Payload CMS):

* ``/calendario`` embeds the season in its Next.js flight payload
  (``self.__next_f.push([1, "..."])`` chunks) as ``"eventos": [...]``: per round
  ``nombre``, ``fecha`` (race day, UTC timestamp), ``tipo`` (sprint/endurance),
  ``circuito.nombre`` and ``circuito.pais`` (sometimes in Spanish: "Bélgica"), and
  ``docs``: the round's documents, ``[{nombre, doc: {title, filename, url,
  updatedAt, id}}]``, among them the timetable ("Prov Schedule", "Schedule_prov";
  a new upload replaces the old one). Only those fields are read.
* The session timetable exists only as that PDF. One table holds every GT Sport series
  (GT Open, GT Cup, Euroformula, TCR): day headings
  "Friday/Viernes, 23 October / Octubre 2026" and rows
  "HH.MM/HH.MM  NN´  <series>  <session>", local track time. Race rows give only
  the start and the race length ("Rolling start - 70´"), and the series name may
  sit on the line just before or after the row.
* Every round's PDF is parsed, and the result is kept per round in the state with
  the document's id and upload time, so a PDF is downloaded once and again only
  when the site uploads a new version. Rounds without a timetable yet are
  all-day placeholders over the race weekend (Friday to race day).

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
        for ev in rounds:
            self._refresh_schedule(ev)
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

    def _refresh_schedule(self, ev: dict) -> None:
        """Parse the round's timetable PDF if it is new or was re-uploaded."""
        doc = ev.get("schedule")
        if not doc or self.state.kv_get("gtopen_schedule_version", ev["id"]) == doc["version"]:
            return
        series = self.cfg.get("series_name", "International GT Open")
        try:
            pdf = self.http.get(doc["url"])
            pdf.raise_for_status()
            sessions = parse_schedule(pdf_to_text(pdf.content), ev["race_day"].year, series)
        except Exception as e:  # keep what we had; the round falls back to a placeholder
            log.warning("gtopen: schedule for %s unavailable: %s", ev["circuit"].strip(), e)
            return
        if sessions:
            self.state.kv_set("gtopen_schedule", ev["id"],
                              [[k.value, n, s.isoformat(), e.isoformat()] for k, n, s, e in sessions])
            log.info("gtopen: %d sessions parsed for %s", len(sessions), ev["circuit"].strip())
        else:
            log.warning("gtopen: schedule PDF for %s had no %s sessions (layout changed?)",
                        ev["circuit"].strip(), series)
        self.state.kv_set("gtopen_schedule_version", ev["id"], doc["version"])

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
                    "country": circuit.get("pais", ""), "schedule": _schedule_doc(ev)})
    return out


def _schedule_doc(ev: dict) -> dict | None:
    """The round's newest timetable document -> {url, version}, or None."""
    found = []
    for d in ev.get("docs") or []:
        doc = d.get("doc") or {}
        label = " ".join(str(x) for x in (d.get("nombre"), doc.get("title"), doc.get("filename")))
        if doc.get("url") and re.search(r"schedule|horario", label, re.I):
            found.append(doc)
    if not found:
        return None
    doc = max(found, key=lambda d: d.get("updatedAt") or "")
    return {"url": doc["url"], "version": f"{doc.get('id')}@{doc.get('updatedAt')}"}


def _classify(text: str) -> tuple[SessionType, str] | None:
    m = re.match(r"Race\b(?:\s*[–-]\s*(\d))?", text)   # "Race – 1", or "Race" (Monza 500 km)
    if m:
        return SessionType.RACE, f"Race {m.group(1)}" if m.group(1) else "Race"
    m = re.search(r"Qualifying\s*(\d)?", text)
    if m:
        return SessionType.QUALI, f"Qualifying {m.group(1)}" if m.group(1) else "Qualifying"
    m = re.search(r"Free practice\s*(\d)", text, re.I)
    if m:
        return SessionType.FP, f"Free Practice {m.group(1)}" + (" (Bronze)" if "bronze" in text.lower() else "")
    return None


def _duration_minutes(text: str) -> int:
    """Race length from "70´", "2 h. 55´" or "max. 2 h. 55´ + 1 lap" (default 60)."""
    m = re.search(r"(\d+)\s*h\.?\s*(\d+)\s*´", text)
    if m:
        return int(m.group(1)) * 60 + int(m.group(2))
    m = re.search(r"(\d+)\s*´", text)
    return int(m.group(1)) if m else 60


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
            end = start + timedelta(minutes=_duration_minutes(rest))
        if end <= start:
            end += timedelta(days=1)
        out.append((kind[0], kind[1], start, end))
    return unique_names(out)
