# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Persistent state: per-UID revision tracking and last-good collector output.

Two things make subscribers see changes reliably:
  * a stable UID per session (same session -> same UID across runs), and
  * SEQUENCE / LAST-MODIFIED bumped whenever the session's content changes.

If a collector fails (site down, layout changed) its last good output is reused,
so a broken scraper never wipes a series out of everybody's calendar.
"""
from __future__ import annotations

import json
import logging
import os
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import Session

log = logging.getLogger(__name__)


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        with os.fdopen(fd, mode) as f:
            f.write(data)
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def _end_utc(s: Session) -> datetime:
    """Session end as an aware UTC datetime (all-day ends are exclusive dates)."""
    if isinstance(s.end, datetime):
        return s.end.astimezone(UTC)
    return datetime.combine(s.end, datetime.min.time(), UTC)


class State:
    def __init__(self, data_dir: Path):
        self.dir = data_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self.rev_file = self.dir / "revisions.json"
        self.kv_file = self.dir / "kv.json"
        self.revisions: dict[str, dict] = self._load(self.rev_file)
        self.kv: dict[str, dict] = self._load(self.kv_file)

    @staticmethod
    def _load(p: Path) -> dict:
        try:
            return json.loads(p.read_text())
        except FileNotFoundError:
            return {}

    # ---- revisions --------------------------------------------------------
    def stamp(self, s: Session, now: datetime) -> dict:
        """Return {sequence, created, modified} for a session, bumping on change."""
        uid, h = s.uid(), s.content_hash()
        rev = self.revisions.get(uid)
        if rev is None:
            rev = {"hash": h, "sequence": 0, "created": now.isoformat(), "modified": now.isoformat()}
        elif rev["hash"] != h:
            rev = {**rev, "hash": h, "sequence": rev["sequence"] + 1, "modified": now.isoformat()}
            log.info("changed: %s (seq %d)", s.summary(), rev["sequence"])
        rev["seen"] = now.isoformat()
        self.revisions[uid] = rev
        return rev

    def prune(self, now: datetime, keep_days: int = 400) -> None:
        cutoff = (now - timedelta(days=keep_days)).isoformat()
        self.revisions = {u: r for u, r in self.revisions.items() if r.get("seen", "") >= cutoff}

    # ---- last-good collector output ------------------------------------------
    def save_collector(self, name: str, sessions: list[Session]) -> None:
        payload = {"saved": datetime.now(UTC).isoformat(), "sessions": [s.to_dict() for s in sessions]}
        atomic_write(self.dir / "collectors" / f"{name}.json", json.dumps(payload))

    def load_collector(self, name: str, max_age_days: int) -> list[Session] | None:
        """Last good output of a collector, used when it fails.

        ``max_age_days > 0``: the whole copy is dropped once it is older than that.
        ``max_age_days == 0``: no age limit; instead each season's sessions are kept
        until that season is over (its last session has ended), so a scraper that
        breaks mid-season keeps the rest of that season in the calendar.
        """
        p = self.dir / "collectors" / f"{name}.json"
        try:
            payload = json.loads(p.read_text())
        except FileNotFoundError:
            return None
        sessions = [Session.from_dict(d) for d in payload["sessions"]]
        if max_age_days > 0:
            age = datetime.now(UTC) - datetime.fromisoformat(payload["saved"])
            if age > timedelta(days=max_age_days):
                log.warning("cached output for %s is %s old, dropping it", name, age)
                return None
            return sessions
        # 0 = keep until the end of each season
        now = datetime.now(UTC)
        season_end: dict[int, datetime] = {}
        for s in sessions:
            end = _end_utc(s)
            season_end[s.season] = max(season_end.get(s.season, end), end)
        over = {season for season, end in season_end.items() if end < now}
        if over:
            log.info("cached output for %s: dropping finished season(s) %s", name, sorted(over))
        kept = [s for s in sessions if s.season not in over]
        return kept or None

    # ---- small TTL key/value cache (geocoding, YouTube lookups) --------------
    def kv_get(self, ns: str, key: str, ttl: timedelta | None = None):
        entry = self.kv.get(ns, {}).get(key)
        if entry is None:
            return None
        if ttl and datetime.now(UTC) - datetime.fromisoformat(entry["t"]) > ttl:
            return None
        return entry["v"]

    def kv_set(self, ns: str, key: str, value) -> None:
        self.kv.setdefault(ns, {})[key] = {"t": datetime.now(UTC).isoformat(), "v": value}

    def flush(self) -> None:
        atomic_write(self.rev_file, json.dumps(self.revisions, indent=1, sort_keys=True))
        atomic_write(self.kv_file, json.dumps(self.kv, indent=1, sort_keys=True))
