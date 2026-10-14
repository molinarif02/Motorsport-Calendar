# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Collector registry.

``type:`` in a ``collectors:`` block of config.yaml selects one of these classes:

=========  ==============================================================
motocal    any championship supported by the motorsport-calendar library
           (WEC, ELMS, GTWC Europe/America/Asia, IGTC, MotoGP/2/3, F1...)
wrc        WRC, one event per special stage
creventic  Michelin 24H Series (24hseries.com)
imsa       IMSA WeatherTech SportsCar Championship (imsa.com)
jsonld     schema.org events scraped from a web page (circuit calendars)
manual     hand-written sessions from manual.yaml
=========  ==============================================================

To add a new source, subclass :class:`Collector` and register it here; see
``docs/collectors.md``.
"""
from .base import Collector, Geocoder
from .creventic import CreventicCollector
from .imsa import ImsaCollector
from .jsonld import JsonLdCollector
from .manual import ManualCollector
from .motocal import MotocalCollector
from .wrc import WrcCollector

TYPES: dict[str, type[Collector]] = {
    "motocal": MotocalCollector,
    "wrc": WrcCollector,
    "creventic": CreventicCollector,
    "imsa": ImsaCollector,
    "jsonld": JsonLdCollector,
    "manual": ManualCollector,
}

__all__ = ["TYPES", "Collector", "Geocoder"]
