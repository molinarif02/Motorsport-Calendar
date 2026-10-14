# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Attach a streaming link to each session.

Per series, an ordered list of rules is tried; the first one that yields a link wins:

    streaming:
      - match: {session_types: [RACE, QUALI]}   # optional filter
        youtube: "@GTWorld"                      # try the exact live video...
      - url: "https://www.youtube.com/@GTWorld/streams"   # ...else a static fallback
        label: "GT World (YouTube)"

YouTube resolution is quota-friendly:
  * the channel's public RSS feed (no quota) lists its latest uploads, including
    scheduled live streams once the channel has created them (usually a few days ahead);
  * one videos.list call (1 unit per 50 ids) returns their scheduledStartTime;
  * a video whose scheduled start is close to the session start is the match.
A resolved link is cached per session forever, so it survives as a replay link.
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from datetime import UTC, datetime, timedelta

import httpx

from .models import Session
from .state import State

log = logging.getLogger(__name__)
YT_API = "https://www.googleapis.com/youtube/v3"
ATOM = {"a": "http://www.w3.org/2005/Atom", "yt": "http://www.youtube.com/xml/schemas/2015"}


class StreamResolver:
    def __init__(self, cfg: dict, http: httpx.Client, state: State, api_key: str | None):
        self.cfg = cfg
        self.http = http
        self.state = state
        self.api_key = api_key
        yt = cfg.get("youtube", {})
        self.lookahead = timedelta(days=int(yt.get("lookahead_days", 7)))
        self.before = timedelta(minutes=int(yt.get("match_before_minutes", 75)))
        self.after = timedelta(minutes=int(yt.get("match_after_minutes", 120)))
        self._uploads: dict[str, list[dict]] = {}  # per-run memo: channel_id -> videos

    def apply(self, sessions: list[Session], rules_by_series: dict[str, list[dict]]) -> None:
        for s in sessions:
            if s.stream_url:  # collector already set one (e.g. manual.yaml)
                continue
            for rule in rules_by_series.get(s.series, []):
                if not _matches(rule.get("match", {}), s):
                    continue
                url, label = self._resolve(rule, s)
                if url:
                    s.stream_url, s.stream_label = url, label
                    break

    # ---------------------------------------------------------------------
    def _resolve(self, rule: dict, s: Session) -> tuple[str | None, str | None]:
        if "youtube" in rule:
            return self._youtube(rule["youtube"], s), rule.get("label", "YouTube live")
        return rule.get("url"), rule.get("label")

    def _youtube(self, handle: str, s: Session) -> str | None:
        cached = self.state.kv_get("yt_match", s.uid())
        if cached:
            return cached
        if not self.api_key or s.all_day:
            return None
        now = datetime.now(UTC)
        if not (now - timedelta(days=1) <= s.start <= now + self.lookahead):
            return None  # too far ahead: the channel hasn't scheduled it yet
        try:
            channel_id = self._channel_id(handle)
            videos = self._scheduled_videos(channel_id)
        except (httpx.HTTPError, ET.ParseError, KeyError) as e:
            log.warning("youtube lookup failed for %s: %s", handle, e)
            return None
        best = _best_match(videos, s, self.before, self.after)
        if best:
            url = f"https://www.youtube.com/watch?v={best['id']}"
            self.state.kv_set("yt_match", s.uid(), url)
            log.info("stream: %s -> %s", s.summary(), url)
            return url
        return None

    def _channel_id(self, handle: str) -> str:
        if handle.startswith("UC"):
            return handle
        cached = self.state.kv_get("yt_channel", handle)
        if cached:
            return cached
        r = self.http.get(f"{YT_API}/channels", params={
            "part": "id", "forHandle": handle, "key": self.api_key})
        r.raise_for_status()
        cid = r.json()["items"][0]["id"]
        self.state.kv_set("yt_channel", handle, cid)
        return cid

    def _scheduled_videos(self, channel_id: str) -> list[dict]:
        if channel_id in self._uploads:
            return self._uploads[channel_id]
        r = self.http.get("https://www.youtube.com/feeds/videos.xml",
                          params={"channel_id": channel_id})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        ids = [e.findtext("yt:videoId", namespaces=ATOM) for e in root.findall("a:entry", ATOM)]
        ids = [i for i in ids if i]
        videos: list[dict] = []
        if ids:
            r = self.http.get(f"{YT_API}/videos", params={
                "part": "snippet,liveStreamingDetails", "id": ",".join(ids[:50]),
                "key": self.api_key})
            r.raise_for_status()
            for it in r.json().get("items", []):
                lsd = it.get("liveStreamingDetails") or {}
                start = lsd.get("scheduledStartTime") or lsd.get("actualStartTime")
                if start:
                    videos.append({"id": it["id"], "title": it["snippet"]["title"],
                                   "start": datetime.fromisoformat(start.replace("Z", "+00:00"))})
        self._uploads[channel_id] = videos
        return videos


def _matches(m: dict, s: Session) -> bool:
    if "session_types" in m and s.session_type.value not in m["session_types"]:
        return False
    if "name_regex" in m and not re.search(m["name_regex"], s.name, re.I):
        return False
    if "event_regex" in m and not re.search(m["event_regex"], s.event_name, re.I):
        return False
    return True


def _best_match(videos: list[dict], s: Session, before: timedelta, after: timedelta) -> dict | None:
    """Closest scheduled start inside the window; title keyword overlap breaks ties."""
    words = {w for w in re.findall(r"[a-z0-9]+", f"{s.name} {s.event_name}".lower()) if len(w) > 2}
    cands = []
    for v in videos:
        delta = v["start"] - s.start
        if -before <= delta <= after:
            overlap = len(words & set(re.findall(r"[a-z0-9]+", v["title"].lower())))
            cands.append((-overlap, abs(delta.total_seconds()), v))
    return min(cands, key=lambda c: (c[0], c[1]))[2] if cands else None
