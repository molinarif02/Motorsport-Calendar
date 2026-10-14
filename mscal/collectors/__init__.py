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
driftmasters Drift Masters (dm.gp JSON API)
formuladrift Formula DRIFT (formulad.com)
gtopen     International GT Open (gtopen.gtsport.es + schedule PDF)
jsonld     schema.org events scraped from a web page (circuit calendars)
tribe      WordPress "The Events Calendar" REST API (circuit calendars, e.g. Monza)
manual     hand-written sessions from manual.yaml
=========  ==============================================================

To add a new source, subclass :class:`Collector` and register it here; see
``docs/collectors.md``.
"""
from .base import Collector, Geocoder
from .creventic import CreventicCollector
from .driftmasters import DriftMastersCollector
from .formuladrift import FormulaDriftCollector
from .gtopen import GtOpenCollector
from .imsa import ImsaCollector
from .jsonld import JsonLdCollector
from .manual import ManualCollector
from .motocal import MotocalCollector
from .tribe import TribeEventsCollector
from .wrc import WrcCollector

TYPES: dict[str, type[Collector]] = {
    "motocal": MotocalCollector,
    "wrc": WrcCollector,
    "creventic": CreventicCollector,
    "imsa": ImsaCollector,
    "driftmasters": DriftMastersCollector,
    "formuladrift": FormulaDriftCollector,
    "gtopen": GtOpenCollector,
    "jsonld": JsonLdCollector,
    "tribe": TribeEventsCollector,
    "manual": ManualCollector,
}

__all__ = ["TYPES", "Collector", "Geocoder"]
