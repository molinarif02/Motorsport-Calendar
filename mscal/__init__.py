# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""mscal - motorsport calendar generator.

Collects race schedules from many sources into one iCalendar feed ("Motorsport")
that any calendar app can subscribe to.

Modules:
    main        CLI and the run pipeline (collect -> post-process -> streams -> .ics)
    models      the normalized ``Session`` every collector produces
    collectors  data sources (motocal, wrc, jsonld, manual)
    streams     attaches streaming links (YouTube live videos, static services)
    ics_out     renders the RFC 5545 calendar
    state       persistent revisions (SEQUENCE), last-good collector output, caches

This program is free software: you can redistribute it and/or modify it under the
terms of the GNU General Public License as published by the Free Software
Foundation, either version 3 of the License, or (at your option) any later version.
It is distributed WITHOUT ANY WARRANTY; see the LICENSE file for details.
"""

__version__ = "1.3.1"
