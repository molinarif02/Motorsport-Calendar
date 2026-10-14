# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""WRC: one calendar event per special stage, from the API behind wrc.com's results pages.

The old ``api.wrc.com`` endpoints are gone (wrc.com moved to Red Bull's platform in
2026). The results/itinerary widget on wrc.com now reads (checked October 2026)::

    {api_base}/seasons.json                         -> [{seasonId, name, year}]
    {api_base}/season-detail.json?seasonId=N        -> seasonRounds[{order, event{...}}]
    {api_base}/events/{eventId}.json                -> rallies[{isMain, itineraryId}]
    {api_base}/events/{eventId}/itineraries/{id}.json -> legs > sections > controls + stages

``event`` carries the rally name, country, location, start/finish dates and a
``timeZoneId``.

* **Start time**: the scheduled first-car time at the stage's ``StageStart`` control.
  ``firstCarDueDateTimeLocal`` (with offset) is preferred; ``firstCarDueDateTime`` is UTC.
* **Timezone**: the event's ``timeZoneId`` when it is a valid IANA zone in the
  expected region. Unpublished events carry placeholders (the 2027 Monte-Carlo had
  "AUS Eastern Standard Time"), so a Windows name or a zone on the wrong continent
  falls back to :data:`DEFAULT_RALLY_TIMEZONES` (name/country fragments, overridable
  with ``rally_timezones``).
* **Location**: the start place is derived from the stage-start control name
  ("Toudon / Saint-Antonin" -> "Toudon") and geocoded with the country (cached).
* **End time**: start + ``stage_window_minutes`` (first car to roughly the last
  priority car); the itinerary has no stage end time.
* Rallies whose itinerary is not published yet (empty ``itineraryLegs``) become an
  all-day placeholder over the event dates.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

from ..models import Session, SessionType
from .base import Collector

log = logging.getLogger(__name__)

#: Rally name / country fragment -> IANA timezone. Earlier entries win on substring
#: matches, so specific names (Canarias) come before countries (Spain).
DEFAULT_RALLY_TIMEZONES: dict[str, str] = {
    "Canarias": "Atlantic/Canary", "Monte": "Europe/Monaco", "Sweden": "Europe/Stockholm",
    "Safari": "Africa/Nairobi", "Kenya": "Africa/Nairobi", "Croatia": "Europe/Zagreb",
    "Portugal": "Europe/Lisbon", "Sardegna": "Europe/Rome", "Italy": "Europe/Rome",
    "Acropolis": "Europe/Athens", "Greece": "Europe/Athens", "Estonia": "Europe/Tallinn",
    "Finland": "Europe/Helsinki", "Latvia": "Europe/Riga", "Poland": "Europe/Warsaw",
    "Central European": "Europe/Prague", "Czech": "Europe/Prague",
    "Paraguay": "America/Asuncion", "Chile": "America/Santiago",
    "Argentina": "America/Argentina/Buenos_Aires", "Mexico": "America/Mexico_City",
    "Japan": "Asia/Tokyo", "Saudi": "Asia/Riyadh", "New Zealand": "Pacific/Auckland",
    "Spain": "Europe/Madrid", "United Kingdom": "Europe/London",
}


class WrcCollector(Collector):
    """Config keys (all optional): ``api_base``, ``season_name`` (default "World Rally
    Championship"), ``stage_window_minutes`` (90), ``include_shakedown`` (true),
    ``rally_timezones``, plus the usual label keys."""

    required = ("series", "api_base")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {
            "series": "WRC", "championship": "FIA World Rally Championship",
            "discipline": "Rally", "info_url": "https://www.wrc.com/en/calendar",
            "api_base": "https://p-p.redbull.com/rb-wrccom-lintegration-yv-prod/api",
            "season_name": "World Rally Championship",
        }

    @classmethod
    def validate(cls, name: str, cfg: dict) -> list[str]:
        errors = super().validate(name, cfg)
        for k, tz in (cfg.get("rally_timezones") or {}).items():
            try:
                ZoneInfo(tz)
            except Exception:
                errors.append(f"{name}: rally_timezones.{k}: unknown timezone '{tz}'")
        return errors

    def _get(self, path: str):
        r = self.http.get(self.cfg["api_base"].rstrip("/") + path)
        r.raise_for_status()
        return r.json()

    def collect(self, season: int) -> list[Session]:
        seasons = self._get("/seasons.json")
        sid = next((s["seasonId"] for s in seasons if s.get("year") == season
                    and self.cfg["season_name"].lower() in s.get("name", "").lower()), None)
        if sid is None:
            raise RuntimeError(f"wrc: season {season} not published yet")
        rounds = sorted(self._get(f"/season-detail.json?seasonId={sid}").get("seasonRounds") or [],
                        key=lambda r: r.get("order", 0))
        if not rounds:
            raise RuntimeError(f"wrc: season {season} has no rounds")
        out: list[Session] = []
        for rnd in rounds:
            ev = rnd.get("event") or {}
            try:
                out += self._event(season, ev)
            except Exception as e:  # one broken rally must not drop the others
                log.warning("wrc: rally %s skipped: %s", ev.get("name"), e)
        return out

    def _event(self, season: int, ev: dict) -> list[Session]:
        rally = {"id": ev["eventId"], "name": _clean_name(ev.get("name", "")),
                 "country": (ev.get("country") or {}).get("name", ""),
                 "location": ev.get("location", ""), "api_tz": ev.get("timeZoneId")}
        legs = []
        try:
            detail = self._get(f"/events/{rally['id']}.json")
            main = next((r for r in detail.get("rallies") or [] if r.get("isMain")), None)
            if main and main.get("itineraryId"):
                legs = self._get(f"/events/{rally['id']}/itineraries/{main['itineraryId']}.json").get("itineraryLegs") or []
        except Exception as e:
            log.info("wrc: %s itinerary unavailable (%s); using event dates", rally["name"], e)
        stages = self._rally_stages(season, rally, legs)
        if stages:
            return stages
        first = date.fromisoformat(ev["startDate"][:10])
        last = date.fromisoformat(ev["finishDate"][:10])
        if first.year != season:
            return []
        return [Session(series=self.series, championship=self.championship, discipline=self.discipline,
                        season=season, event_name=rally["name"], session_type=SessionType.EVENT,
                        name=rally["name"], start=first, end=last + timedelta(days=1),
                        tz=self._rally_tz(rally),
                        location=", ".join(x for x in (rally["location"], rally["country"]) if x),
                        info_url=self.cfg.get("info_url"), key=f"{rally['id']}|weekend")]

    # ---- one rally --------------------------------------------------------------------
    def _rally_stages(self, season: int, rally: dict, legs: list) -> list[Session]:
        tzname = self._rally_tz(rally)
        tz = ZoneInfo(tzname)
        window = timedelta(minutes=int(self.cfg.get("stage_window_minutes", 90)))
        out: list[Session] = []

        for leg in legs:
            for sec in leg.get("itinerarySections") or []:
                controls = sec.get("controls") or []
                # stageId -> its StageStart control (holds the scheduled time + place)
                starts = {c.get("stageId"): c for c in controls
                          if str(c.get("type", "")).lower() in ("stagestart", "stage start")}
                for st in sec.get("stages") or []:
                    ctrl = starts.get(st.get("stageId"))
                    when = _parse_time(ctrl, tz) if ctrl else None
                    if when is None or when.year != season:
                        continue
                    code = st.get("code") or f"SS{st.get('number', '')}"
                    sname = (st.get("name") or "").strip()
                    is_sd = "shakedown" in sname.lower() or code.upper().startswith("SD")
                    if is_sd and not self.cfg.get("include_shakedown", True):
                        continue
                    place = _start_place(ctrl.get("location") or sname)
                    geo = self.geo.lookup(f"{place}, {rally['country']}") if place else None
                    km = st.get("distance")
                    out.append(Session(
                        series=self.series, championship=self.championship,
                        discipline=self.discipline, season=season, event_name=rally["name"],
                        session_type=SessionType.SHAKEDOWN if is_sd else SessionType.STAGE,
                        name=f"{code} {sname}" + (f" ({km} km)" if km else ""),
                        start=when, end=when + window, tz=tzname,
                        location=f"Stage start: {place}, {rally['country']}".strip(", "),
                        geo=geo, info_url=self.cfg.get("info_url"),
                        cancelled=str(st.get("status", "")).lower() == "cancelled",
                        key=f"{rally['id']}|{code}",
                    ))
        if out:
            log.info("wrc: %s -> %d stages (%s)", rally["name"], len(out), tzname)
        return out

    def _rally_tz(self, rally: dict) -> str:
        """Timezone for a rally: event id, then name fragment, then country fragment."""
        user = self.cfg.get("rally_timezones") or {}
        table = {**user, **{k: v for k, v in DEFAULT_RALLY_TIMEZONES.items() if k not in user}}
        if str(rally["id"]) in table:
            return table[str(rally["id"])]
        guess = None
        for field in ("name", "country"):
            hay = str(rally.get(field) or "").lower()
            guess = guess or next((v for k, v in table.items() if hay and k.lower() in hay), None)
        api = rally.get("api_tz")
        if api and "/" in api:
            try:
                ZoneInfo(api)
                # trust the API unless it contradicts the table's region (placeholders)
                if guess is None or guess.split("/")[0] == api.split("/")[0]:
                    return api
            except Exception:
                pass
        if guess:
            return guess
        log.warning("wrc: no timezone for %s/%s, using UTC (add it to rally_timezones)",
                    rally["name"], rally["country"])
        return "UTC"


def _parse_time(ctrl: dict, tz: ZoneInfo) -> datetime | None:
    """Scheduled first-car time of a control, in the rally's zone.

    ``firstCarDueDateTimeLocal`` may carry an offset or be naive (then it is local);
    ``firstCarDueDateTime`` is UTC (naive values are treated as UTC).
    """
    local = ctrl.get("firstCarDueDateTimeLocal")
    if local:
        dt = datetime.fromisoformat(local.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(tz)
    utc = ctrl.get("firstCarDueDateTime")
    if utc:
        dt = datetime.fromisoformat(utc.replace("Z", "+00:00"))
        return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(tz)
    return None


def _start_place(name: str) -> str:
    """'Cagnano - Pino - Canari 2' -> 'Cagnano'; 'SS5 Ouninpohja 1' -> 'Ouninpohja'."""
    first = re.split(r"\s+[-–]\s+|/", name)[0]
    first = re.sub(r"^(SS|SD)\s*\d+\s*", "", first, flags=re.I)
    return re.sub(r"\s+\d+$", "", first).strip()


def _clean_name(name: str) -> str:
    """Drop sponsor tails: 'Rally Finland powered by X' -> 'Rally Finland'."""
    return re.sub(r"\s+powered by.*$", "", name, flags=re.I).strip()
