# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""``python -m mscal``: ``run --loop`` goes to the lightweight supervisor (stdlib only);
everything else loads the full CLI."""
import sys

from .supervisor import maybe_loop

code = maybe_loop(sys.argv[1:])
if code is None:
    from .main import main

    code = main()
sys.exit(code)
