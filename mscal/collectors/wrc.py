# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""WRC: one calendar event per special stage, from the (unofficial) WRC results API.

Data flow::

    {season_url}                                   -> rallies of the active season
    {results_api}/rally-event/{eventId}/itinerary  -> legs > sections > controls + stages

* **Start time**: the scheduled first-car time at the stage's ``StageStart`` control.
  The ``...Local`` field is preferred; the UTC field is the fallback.
* **Timezone**: looked up from :data:`DEFAULT_RALLY_TIMEZONES` (overridable with
  ``rally_timezones:``), matched on event id, then rally name, then country - so
  "Rally Islas Canarias" gets ``Atlantic/Canary`` even though its country is Spain.
* **Location**: the API gives names, not coordinates. The start place is derived
  from the control/stage name ("Cagnano - Pino - Canari 2" -> "Cagnano") and
  geocoded together with the country (cached forever).
* **End time**: start + ``stage_window_minutes`` (first car to roughly the last
  priority car), since the API has no stage end time.

The endpoints are undocumented and have changed between seasons: every field read
here is defensive, and the endpoints are configurable. If the season list breaks,
pin the rallies by hand with ``events: [{id, name, country}]``.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime, timedelta
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
    """Config keys (all optional): ``season_url``, ``results_api``, ``events``,
    ``stage_window_minutes`` (90), ``include_shakedown`` (true), ``rally_timezones``,
    plus the usual label keys."""

    required = ("series", "results_api")

    @classmethod
    def defaults(cls, cfg: dict) -> dict:
        return {
            "series": "WRC", "championship": "FIA World Rally Championship",
            "discipline": "Rally", "info_url": "https://www.wrc.com",
            "season_url": "https://api.wrc.com/contel-page/83388/calendar/active-season/",
            "results_api": "https://api.wrc.com/results-api",
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

    def collect(self, season: int) -> list[Session]:
        rallies = self._rallies()
        out: list[Session] = []
        for r in rallies:
            try:
                out += self._rally_stages(season, r)
            except Exception as e:  # one broken rally must not drop the others
                log.warning("wrc: rally %s skipped: %s", r.get("name"), e)
        if not out:
            raise RuntimeError("wrc: no stages parsed (API changed or not published yet)")
        return out

    # ---- season -------------------------------------------------------------------
    def _rallies(self) -> list[dict]:
        """List of ``{id, name, country}`` for the active season (or the manual pin)."""
        if self.cfg.get("events"):
            return self.cfg["events"]
        r = self.http.get(self.cfg["season_url"])
        r.raise_for_status()
        items = _first_list(r.json(), ("content", "items", "events", "data")) or []
        rallies = []
        for it in items:
            eid = it.get("eventId") or it.get("id")
            name = it.get("name") or it.get("title") or f"Rally {eid}"
            country = it.get("country")
            if isinstance(country, dict):
                country = country.get("name")
            rallies.append({"id": eid, "name": _clean_name(name),
                            "country": country or it.get("countryName") or it.get("location") or ""})
        return [r for r in rallies if r["id"]]

    # ---- one rally ------------------------------------------------------------------
    def _rally_stages(self, season: int, rally: dict) -> list[Session]:
        base = self.cfg["results_api"].rstrip("/")
        r = self.http.get(f"{base}/rally-event/{rally['id']}/itinerary")
        r.raise_for_status()
        legs = r.json().get("itineraryLegs") or []
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
        log.info("wrc: %s -> %d stages (%s)", rally["name"], len(out), tzname)
        return out

    def _rally_tz(self, rally: dict) -> str:
        """Timezone for a rally: event id, then name fragment, then country fragment."""
        user = self.cfg.get("rally_timezones") or {}
        table = {**user, **{k: v for k, v in DEFAULT_RALLY_TIMEZONES.items() if k not in user}}
        if str(rally["id"]) in table:
            return table[str(rally["id"])]
        for field in ("name", "country"):
            hay = str(rally.get(field) or "").lower()
            for k, v in table.items():
                if hay and k.lower() in hay:
                    return v
        log.warning("wrc: no timezone for %s/%s, using UTC (add it to rally_timezones)",
                    rally["name"], rally["country"])
        return "UTC"


def _first_list(data, keys) -> list | None:
    """Find the first list under any of ``keys`` (searching nested dicts too)."""
    if isinstance(data, list):
        return data
    for k in keys:
        v = data.get(k) if isinstance(data, dict) else None
        if isinstance(v, list):
            return v
        if isinstance(v, dict):
            inner = _first_list(v, keys)
            if inner:
                return inner
    return None


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
