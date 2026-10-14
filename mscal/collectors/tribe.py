# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Circuit event calendars published with WordPress "The Events Calendar" (Tribe).

Many circuit websites use this plugin, which exposes a public REST API::

    {api}?start_date=YYYY-01-01&end_date=YYYY-12-31&per_page=50&categories=<slug>&page=N
    -> {events: [{title, url, start_date, end_date, all_day, timezone, categories}],
        total_pages, next_rest_url}

Monza (monzanet.it, checked October 2026) files races under the category ``gare``,
next to tours, track days, tests and cycling events. Event times there are gate
opening hours (09:00-18:00), not sessions, so by default events become all-day
entries over their dates (``all_day: true``). ``series_map`` tags events by name
(e.g. the WEC 6 Hours becomes ``[WEC]``), so they are hidden automatically when the
series' own collector provides timed sessions on those days.

Used for tracked circuits via ``calendar: {type: tribe, api: ..., categories: [...]}``
and usable directly as ``type: tribe``.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta
from html import unescape
from zoneinfo import ZoneInfo

from ..models import Session, SessionType
from .base import Collector

log = logging.getLogger(__name__)


class TribeEventsCollector(Collector):
    """Config keys: ``api`` (the ``/wp-json/tribe/events/v1/events`` URL), ``categories``
    (slugs, optional), ``all_day`` (true), ``series_map``, ``skip_regex``, ``tz``,
    ``location``, ``geo``, ``extra_labels``, ``max_pages`` (10)."""

    required = ("series", "api")

    def collect(self, season: int) -> list[Session]:
        params = {"start_date": f"{season}-01-01", "end_date": f"{season}-12-31", "per_page": 50}
        if self.cfg.get("categories"):
            params["categories"] = ",".join(self.cfg["categories"])
        events, page = [], 1
        while page <= int(self.cfg.get("max_pages", 10)):
            r = self.http.get(self.cfg["api"], params={**params, "page": page})
            if r.status_code == 404 and page == 1:
                return []  # plugin answers 404 when the range has no events
            r.raise_for_status()
            j = r.json()
            events += j.get("events") or []
            if page >= int(j.get("total_pages") or 1):
                break
            page += 1
        out = [s for e in events if (s := self._session(e, season))]
        log.info("%s: %d events", self.name, len(out))
        return out

    def _session(self, e: dict, season: int) -> Session | None:
        title = " ".join(unescape(e.get("title") or "").split())
        if not title or (self.cfg.get("skip_regex") and re.search(self.cfg["skip_regex"], title, re.I)):
            return None
        tzname = e.get("timezone") or self.cfg.get("tz", "Europe/Rome")
        try:
            tz = ZoneInfo(tzname)
        except Exception:
            tzname, tz = self.cfg.get("tz", "Europe/Rome"), ZoneInfo(self.cfg.get("tz", "Europe/Rome"))
        start = datetime.fromisoformat(e["start_date"]).replace(tzinfo=tz)
        end = datetime.fromisoformat(e["end_date"]).replace(tzinfo=tz)
        if start.year != season:
            return None
        series = self.series
        for rx, tag in (self.cfg.get("series_map") or {}).items():
            if re.search(rx, title, re.I):
                series = tag
                break
        all_day = self.cfg.get("all_day", True) or e.get("all_day")
        s_start: date | datetime = start.date() if all_day else start
        s_end: date | datetime = end.date() + timedelta(days=1) if all_day else end
        geo = self.cfg.get("geo")
        return Session(
            series=series, championship=self.championship, discipline=self.discipline,
            season=season, event_name=title,
            session_type=SessionType.EVENT if all_day else SessionType.OTHER,
            name=title, start=s_start, end=s_end, tz=tzname,
            location=self.cfg.get("location", ""), geo=tuple(geo) if geo else None,
            info_url=e.get("url"), key=f"{self.name}|{e.get('id') or title}",
            extra_labels=list(self.cfg.get("extra_labels", [])),
        )
