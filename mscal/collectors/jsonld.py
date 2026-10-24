# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Generic schema.org Event scraper (JSON-LD).

Used for the event calendars of tracked circuits (see ``tracked_circuits`` in
config.yaml, which generates one of these per circuit with a ``calendar:`` block),
and usable directly as ``type: jsonld`` for any site without a dedicated parser.

Many event/CMS sites embed <script type="application/ld+json"> blocks with
{"@type": "Event" | "SportsEvent", "name", "startDate", "endDate", "location", "subEvent"}.
Date-only entries become all-day "EVENT" placeholders; timed ones keep their times.
Midnight-to-midnight ranges ("2026-10-02T00:00:00+02:00" .. "2026-10-04T00:00:00+02:00",
as spa-francorchamps.be writes them) are dates too, with the end day included.
Blocks with raw line breaks inside strings (also Spa) are accepted.

cfg:
  urls: [listing page, ...]
  follow_links_regex: optional regex of detail-page links to fetch from the listing
  max_pages: 40
  series_map: {regex: SERIES_TAG}   # classify events (Monza hosts many series)
  skip_regex: events already covered by a dedicated collector
  tz, location, geo: defaults for the venue
"""
from __future__ import annotations

import json
import logging
import re
from datetime import date, datetime, timedelta
from urllib.parse import urljoin
from zoneinfo import ZoneInfo

from ..models import Session, SessionType
from .base import Collector

log = logging.getLogger(__name__)
_LD = re.compile(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', re.S | re.I)
_HREF = re.compile(r'href="([^"#]+)"', re.I)


class JsonLdCollector(Collector):
    """Crawl ``urls`` (plus matching detail pages) and convert every schema.org Event."""

    required = ("series", "urls")

    def collect(self, season: int) -> list[Session]:
        # breadth-first over the listing page(s) and, optionally, linked detail pages
        pages, seen = list(self.cfg["urls"]), set()
        follow = re.compile(self.cfg["follow_links_regex"]) if self.cfg.get("follow_links_regex") else None
        max_pages = int(self.cfg.get("max_pages", 40))
        found: dict[str, dict] = {}
        while pages and len(seen) < max_pages:
            url = pages.pop(0)
            if url in seen:
                continue
            seen.add(url)
            r = self.http.get(url)
            r.raise_for_status()
            for obj in _ld_objects(r.text):
                for ev in _events(obj):
                    found.setdefault(f"{ev.get('name')}|{ev.get('startDate')}", ev)
            if follow:
                for href in _HREF.findall(r.text):
                    full = urljoin(url, href)
                    if follow.search(full) and full not in seen:
                        pages.append(full)
        out = [s for ev in found.values() for s in self._to_sessions(ev, season)]
        if not out and self.cfg.get("fail_if_empty", True):
            raise RuntimeError(f"{self.name}: no schema.org events found")
        return out

    def _to_sessions(self, ev: dict, season: int) -> list[Session]:
        """One schema.org Event -> zero or one Session (filtered by season/skip_regex)."""
        name = str(ev.get("name") or "").strip()
        if not name or not ev.get("startDate"):
            return []
        if self.cfg.get("skip_regex") and re.search(self.cfg["skip_regex"], name, re.I):
            return []
        tzname = self.cfg.get("tz", "Europe/Rome")
        tz = ZoneInfo(tzname)
        start, timed = _parse(ev["startDate"], tz)
        end, _ = _parse(ev.get("endDate") or ev["startDate"], tz)
        if timed and start.time() == end.time() == datetime.min.time():
            timed = False   # midnight to midnight: a date range, end day included
        if start.year != season:
            return []
        series = self.series
        for rx, tag in (self.cfg.get("series_map") or {}).items():
            if re.search(rx, name, re.I):
                series = tag
                break
        if timed:
            if end <= start:
                end = start + timedelta(hours=2)
        else:  # all-day: DTEND is exclusive
            start, end = start.date(), end.date() + timedelta(days=1)
        geo = self.cfg.get("geo")
        return [Session(
            series=series, championship=self.championship, discipline=self.discipline,
            season=season, event_name=name,
            session_type=SessionType.EVENT if not timed else SessionType.OTHER,
            name=name, start=start, end=end, tz=tzname,
            location=self.cfg.get("location", ""), geo=tuple(geo) if geo else None,
            info_url=ev.get("url") or self.cfg["urls"][0],
            key=f"{self.name}|{name}|{ev['startDate']}",
            extra_labels=list(self.cfg.get("extra_labels", [])),
        )]


def _ld_objects(html: str):
    """Yield every parseable JSON-LD block of a page (invalid blocks are skipped)."""
    for block in _LD.findall(html):
        try:
            # strict=False: some CMSs emit raw newlines inside strings (invalid, but common)
            yield json.loads(block.strip(), strict=False)
        except json.JSONDecodeError:
            continue


def _events(obj):
    """Walk a JSON-LD tree and yield every object whose @type ends in 'Event'."""
    if isinstance(obj, list):
        for o in obj:
            yield from _events(o)
    elif isinstance(obj, dict):
        t = obj.get("@type")
        types = t if isinstance(t, list) else [t]
        if any(str(x).endswith("Event") for x in types):
            yield obj
        for k in ("@graph", "subEvent", "itemListElement", "item"):
            if k in obj:
                yield from _events(obj[k])


def _parse(v: str, tz: ZoneInfo) -> tuple[datetime, bool]:
    """ISO date or datetime -> (aware datetime in ``tz``, has_time)."""
    v =v.strip().replace("Z", "+00:00")
    if len(v) == 10:
        return datetime.combine(date.fromisoformat(v), datetime.min.time(), tz), False
    dt = datetime.fromisoformat(v)
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(tz), True
