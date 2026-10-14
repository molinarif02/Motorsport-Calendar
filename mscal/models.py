# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Normalized data model shared by every collector.

Every source - a JSON API, an HTML timetable, a YAML file - is converted into
:class:`Session` objects. Everything downstream (labels, stream links, the .ics
renderer, change detection) only ever sees this one shape.

Identity and change detection:

* :meth:`Session.identity` = ``series | season | key`` -> :meth:`Session.uid`.
  It must not depend on anything that can change (times, names of streams...),
  otherwise a rescheduled session would show up as a new event in calendars.
* :meth:`Session.content_hash` covers *everything*; when it changes, the state
  bumps the event's SEQUENCE so clients replace their copy.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo


class SessionType(StrEnum):
    """Kind of session; drives the ``[Label]`` in titles and filters like
    ``session_types: [FP, QUALI, RACE]``."""

    FP = "FP"                # free practice / test
    QUALI = "QUALI"          # qualifying / hyperpole / pre-quali
    RACE = "RACE"            # race / sprint
    STAGE = "STAGE"          # rally special stage
    SHAKEDOWN = "SHAKEDOWN"  # rally shakedown
    EVENT = "EVENT"          # whole-weekend placeholder (only dates known)
    OTHER = "OTHER"


SESSION_LABEL = {
    SessionType.FP: "Free Practice",
    SessionType.QUALI: "Qualifying",
    SessionType.RACE: "Race",
    SessionType.STAGE: "Stage",
    SessionType.SHAKEDOWN: "Shakedown",
    SessionType.EVENT: "Event",
    SessionType.OTHER: "Session",
}


@dataclass
class Session:
    """One calendar event: a practice, qualifying, race, rally stage or a
    whole-weekend placeholder. Times are aware datetimes in the venue's zone
    (or plain dates for all-day events, with an exclusive ``end``)."""

    series: str            # short tag shown in the title, e.g. "WEC"
    championship: str      # full name, e.g. "FIA World Endurance Championship"
    discipline: str        # race type label: Endurance | GT | Rally | Drift | ...
    season: int
    event_name: str        # e.g. "8 Hours of Bahrain"
    session_type: SessionType
    name: str              # e.g. "FP1", "SS5 Ouninpohja 1", "Race 2"
    start: datetime | date  # tz-aware datetime, or a date for all-day events
    end: datetime | date
    tz: str                # IANA zone of the venue
    location: str = ""
    geo: tuple[float, float] | None = None
    info_url: str | None = None
    stream_url: str | None = None
    stream_label: str | None = None
    cancelled: bool = False
    key: str = ""          # stable identity inside (series, season); defaults to event|name
    extra_labels: list[str] = field(default_factory=list)  # e.g. ["Monza"]

    # ---- identity -------------------------------------------------------
    @property
    def all_day(self) -> bool:
        return not isinstance(self.start, datetime)

    def identity(self) -> str:
        k = self.key or f"{self.event_name}|{self.name}"
        return f"{self.series}|{self.season}|{k}".lower()

    def uid(self) -> str:
        return hashlib.sha1(self.identity().encode()).hexdigest()[:24] + "@motorsport-cal"

    # ---- presentation ---------------------------------------------------
    @property
    def label(self) -> str:
        return SESSION_LABEL[self.session_type]

    def summary(self) -> str:
        prefix = "CANCELLED " if self.cancelled else ""
        tags = f"[{self.series}] [{self.label}]"
        if self.session_type == SessionType.EVENT:
            return f"{prefix}{tags} {self.event_name}"
        return f"{prefix}{tags} {self.name} – {self.event_name}"

    def categories(self) -> list[str]:
        cats = [self.series, self.championship, self.discipline, self.label, *self.extra_labels]
        return list(dict.fromkeys(c for c in cats if c))  # dedupe, keep order

    # ---- change detection / (de)serialization ---------------------------
    def to_dict(self) -> dict:
        def dt(v: datetime | date) -> str:
            return v.isoformat()
        return {
            "series": self.series, "championship": self.championship,
            "discipline": self.discipline, "season": self.season,
            "event_name": self.event_name, "session_type": self.session_type.value,
            "name": self.name, "start": dt(self.start), "end": dt(self.end),
            "all_day": self.all_day, "tz": self.tz, "location": self.location,
            "geo": list(self.geo) if self.geo else None, "info_url": self.info_url,
            "stream_url": self.stream_url, "stream_label": self.stream_label,
            "cancelled": self.cancelled, "key": self.key,
            "extra_labels": self.extra_labels,
        }

    @classmethod
    def from_dict(cls, d: dict) -> Session:
        tz = ZoneInfo(d["tz"])
        if d.get("all_day"):
            start, end = date.fromisoformat(d["start"]), date.fromisoformat(d["end"])
        else:
            start = datetime.fromisoformat(d["start"]).astimezone(tz)
            end = datetime.fromisoformat(d["end"]).astimezone(tz)
        return cls(
            series=d["series"], championship=d["championship"], discipline=d["discipline"],
            season=d["season"], event_name=d["event_name"],
            session_type=SessionType(d["session_type"]), name=d["name"],
            start=start, end=end, tz=d["tz"], location=d.get("location", ""),
            geo=tuple(d["geo"]) if d.get("geo") else None, info_url=d.get("info_url"),
            stream_url=d.get("stream_url"), stream_label=d.get("stream_label"),
            cancelled=d.get("cancelled", False), key=d.get("key", ""),
            extra_labels=d.get("extra_labels", []),
        )

    def content_hash(self) -> str:
        return hashlib.sha1(json.dumps(self.to_dict(), sort_keys=True).encode()).hexdigest()


def localize(naive: datetime, tz: str) -> datetime:
    return naive.replace(tzinfo=ZoneInfo(tz))


def default_end(start: datetime, minutes: int) -> datetime:
    return start + timedelta(minutes=minutes)
