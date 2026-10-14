# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Render sessions into one RFC 5545 calendar ("Motorsport").

Choices that matter for subscribers:

* ``DTSTART;TZID=<venue zone>`` plus embedded VTIMEZONE blocks: clients convert to
  the viewer's zone themselves, and the description keeps the local track time.
* Labels go both into the title prefix (``[WEC] [Race]``, the only thing every client
  shows) and into ``CATEGORIES`` (used by Apple, Outlook, Thunderbird).
* ``DTSTAMP`` is the last-modified time, not "now", so an unchanged calendar renders
  to identical bytes: the web server's ETag stays the same and polling is cheap.
* ``X-APPLE-STRUCTURED-LOCATION`` gives Apple Calendar a map pin and travel time.
* ``REFRESH-INTERVAL`` / ``X-PUBLISHED-TTL`` are hints; most clients use their own.
"""
from __future__ import annotations

from datetime import UTC, datetime, timedelta

from icalendar import Alarm, Calendar, Event
from icalendar.prop import vUri

from .models import Session, SessionType
from .state import State


def build_calendar(sessions: list[Session], state: State, cfg: dict) -> bytes:
    """Return the serialized calendar. Also stamps each session in ``state``
    (assigning/bumping its SEQUENCE), so call it once per run."""
    out = cfg.get("output", {})
    now = datetime.now(UTC).replace(microsecond=0)

    cal = Calendar()
    cal.add("prodid", "-//motorsport-cal//EN")
    cal.add("version", "2.0")
    cal.add("calscale", "GREGORIAN")
    cal.add("method", "PUBLISH")
    name = out.get("calendar_name", "Motorsport")
    cal.add("name", name)                      # RFC 7986
    cal.add("x-wr-calname", name)              # Apple / Google / Proton
    cal.add("x-wr-caldesc", out.get("description", "WEC, WRC, GT, drift and Monza events"))
    cal.add("x-wr-timezone", out.get("display_timezone", "Europe/Rome"))
    if out.get("color"):
        cal.add("x-apple-calendar-color", out["color"])
        cal.add("color", out["color"])
    refresh = timedelta(minutes=int(out.get("refresh_minutes", 60)))
    cal.add("refresh-interval", refresh)        # RFC 7986 hint
    cal.add("x-published-ttl", refresh)         # legacy hint (Outlook/Apple)

    alarm_min = int(out.get("alarm_minutes", 0))

    for s in sorted(sessions, key=lambda x: (str(x.start), x.series, x.name)):
        rev = state.stamp(s, now)
        ev = Event()
        ev.add("uid", s.uid())
        ev.add("summary", s.summary())
        # DTSTAMP = last change (not "now") so an unchanged calendar renders to
        # identical bytes -> stable ETag -> cheap 304s for every subscriber
        ev.add("dtstamp", datetime.fromisoformat(rev["modified"]))
        ev.add("created", datetime.fromisoformat(rev["created"]))
        ev.add("last-modified", datetime.fromisoformat(rev["modified"]))
        ev.add("sequence", rev["sequence"])
        ev.add("dtstart", s.start)              # aware datetime -> TZID=<venue zone>
        ev.add("dtend", s.end)                  # date -> VALUE=DATE (all-day)
        ev.add("categories", s.categories())
        ev.add("status", "CANCELLED" if s.cancelled else "CONFIRMED")
        ev.add("transp", "TRANSPARENT")         # don't block free/busy
        if s.location:
            ev.add("location", s.location)
        if s.geo:
            ev.add("geo", s.geo)
            # Apple Calendar shows a map pin + travel time with this
            ev.add("x-apple-structured-location", vUri(f"geo:{s.geo[0]},{s.geo[1]}"),
                   parameters={"VALUE": "URI", "X-APPLE-RADIUS": "500",
                               "X-TITLE": s.location or s.event_name})
        link = s.stream_url or s.info_url
        if link:
            ev.add("url", link)
        ev.add("description", _description(s))
        if alarm_min and not s.all_day and s.session_type in (SessionType.RACE, SessionType.QUALI):
            a = Alarm()
            a.add("action", "DISPLAY")
            a.add("description", s.summary())
            a.add("trigger", timedelta(minutes=-alarm_min))
            ev.add_component(a)
        cal.add_component(ev)

    cal.add_missing_timezones()  # embeds VTIMEZONE for every TZID used
    return cal.to_ical()


def _description(s: Session) -> str:
    """Plain-text body: championship, session, local time, links and labels."""
    lines = [f"{s.championship} – {s.event_name}", f"Session: {s.name} ({s.label})"]
    if not s.all_day:
        lines.append(f"Local track time: {s.start:%a %d %b %H:%M} ({s.tz})")
    if s.location:
        lines.append(f"Location: {s.location}")
    if s.stream_url:
        lines.append(f"Watch: {s.stream_label or 'Live stream'} – {s.stream_url}")
    if s.info_url and s.info_url != s.stream_url:
        lines.append(f"Info: {s.info_url}")
    lines.append("Labels: " + ", ".join(s.categories()))
    return "\n".join(lines)
