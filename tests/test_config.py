# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Configuration features: catalog defaults, enabling extra championships,
tracked circuits, and the offline `validate` command."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import yaml

from mscal import main as M
from mscal.collectors.motocal import CATALOG, available_providers
from mscal.models import Session, SessionType

CFG = Path("config/config.yaml")


def _cfg() -> dict:
    cfg = yaml.safe_load(CFG.read_text())
    cfg["collectors"]["manual"]["file"] = "config/manual.yaml"
    return cfg


def test_catalog_covers_every_installed_provider():
    """New upstream championships should get labels here; this flags them."""
    missing = set(available_providers()) - set(CATALOG) - {"imsa", "worldsbk"}  # upstream stubs
    assert not missing, f"add to CATALOG: {missing}"


def test_defaults_and_enable_flags():
    specs = {name: cfg for name, _, cfg in M.collector_specs(_cfg())}
    assert specs["wec"]["series"] == "WEC" and specs["wec"]["source"] == "official"
    assert specs["wrc"]["api_base"].startswith("https://")
    assert "motogp" not in specs  # enabled: false
    assert "circuit-monza" in specs and specs["circuit-monza"]["extra_labels"] == ["Monza"]

    cfg = _cfg()
    cfg["collectors"]["motogp"]["enabled"] = True
    cfg["collectors"]["gtwca"]["enabled"] = True
    specs = {name: c for name, _, c in M.collector_specs(cfg)}
    assert specs["motogp"]["series"] == "MotoGP" and specs["motogp"]["discipline"] == "Motorcycle"
    assert specs["gtwca"]["championship"] == "GT World Challenge America"


def test_user_overrides_win():
    cfg = _cfg()
    cfg["collectors"]["motogp"] = {"type": "motocal", "provider": "motogp", "series": "MGP"}
    specs = {name: c for name, _, c in M.collector_specs(cfg)}
    assert specs["motogp"]["series"] == "MGP"
    assert specs["motogp"]["championship"] == "MotoGP World Championship"


def test_validate_shipped_config_and_catches_mistakes(capsys):
    assert M.validate(_cfg()) == []
    bad = _cfg()
    bad["collectors"]["oops"] = {"type": "motocal", "provider": "motogpp"}
    bad["tracked_circuits"]["nowhere"] = {"tz": "Mars/Olympus"}
    errors = M.validate(bad)
    assert any("unknown provider 'motogpp'" in e for e in errors)
    assert any("Mars/Olympus" in e for e in errors)


def test_tracked_circuit_label_and_coordinates():
    cfg = _cfg()
    cfg["tracked_circuits"]["imola"] = {"label": "Imola", "match": ["imola"], "geo": [44.3439, 11.7167]}
    start = datetime.now(UTC).astimezone(ZoneInfo("Europe/Rome")) + timedelta(days=3)
    s = Session(series="WEC", championship="FIA WEC", discipline="Endurance", season=start.year,
                event_name="6 Hours of Imola", session_type=SessionType.RACE, name="Race",
                start=start, end=start + timedelta(hours=6), tz="Europe/Rome",
                location="Autodromo Enzo e Dino Ferrari, Imola, Italy", geo=(0.0, 0.0))
    (out,) = M.postprocess([s], cfg)
    assert "Imola" in out.categories() and "Monza" not in out.categories()
    assert out.geo == (44.3439, 11.7167)  # tracked coordinates replace geocoded ones


def test_log_level_from_config():
    import logging

    for value, expected in [("debug", logging.DEBUG), ("WARN", logging.WARNING),
                            ("crit", logging.CRITICAL), ("info", logging.INFO)]:
        M.apply_log_level({"log_level": value})
        assert logging.getLogger().level == expected
        httpx_level = logging.getLogger("httpx").level
        assert httpx_level == (logging.DEBUG if expected == logging.DEBUG else max(expected, logging.WARNING))
    assert any("log_level" in e for e in M.validate({**_cfg(), "log_level": "verbose"}))


def test_stale_zero_keeps_cache_until_season_end(tmp_path):
    from mscal.state import State

    now = datetime.now(UTC)
    tz = "Europe/Rome"

    def sess(season, days, name):
        start = (now + timedelta(days=days)).astimezone(ZoneInfo(tz))
        return Session(series="WEC", championship="FIA WEC", discipline="Endurance", season=season,
                       event_name="X", session_type=SessionType.RACE, name=name,
                       start=start, end=start + timedelta(hours=2), tz=tz)

    state = State(tmp_path)
    # season A: finished (last session in the past); season B: one past + one future session
    state.save_collector("wec", [sess(2025, -300, "old"), sess(2026, -10, "past"), sess(2026, 20, "future")])
    # pretend the copy is a year old: a positive limit drops it, 0 does not
    import json

    p = tmp_path / "collectors" / "wec.json"
    payload = json.loads(p.read_text())
    payload["saved"] = (now - timedelta(days=365)).isoformat()
    p.write_text(json.dumps(payload))
    assert state.load_collector("wec", 14) is None
    kept = state.load_collector("wec", 0)
    assert sorted(s.name for s in kept) == ["future", "past"]   # finished season 2025 dropped

    state.save_collector("wec", [sess(2025, -300, "old")])
    assert state.load_collector("wec", 0) is None              # everything finished


def test_validate_stale_max_days():
    assert M.validate({**_cfg(), "stale_max_days": 0}) == []
    assert any("stale_max_days" in e for e in M.validate({**_cfg(), "stale_max_days": -1}))
