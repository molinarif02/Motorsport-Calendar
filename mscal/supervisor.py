# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Federico Molinari
"""Lightweight run loop for ``python -m mscal run --loop``.

Imports nothing but the standard library, so the long-lived process stays at about
the size of a bare interpreter (~12 MB). Each run happens in a fresh child process
(``python -m mscal run ...``) that loads the collectors, does its work and exits,
handing all its memory back to the system: the Pi only pays for the generator
while it is actually running.

The child writes ``next_run_minutes`` into ``status.json``; the loop sleeps that
long (shorter on race weekends, see ``schedule:`` in config.yaml).
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_WAIT_MINUTES = 180
_stop = False
_child: subprocess.Popen | None = None


def _on_signal(signum, _frame) -> None:
    global _stop
    _stop = True
    if _child is not None and _child.poll() is None:
        _child.send_signal(signum)  # let a running collection finish cleanly


def _next_wait(out_dir: str) -> int:
    """Seconds to sleep, from the hint the last run left in status.json."""
    try:
        status = json.loads((Path(out_dir) / "status.json").read_text())
        return max(60, int(float(status.get("next_run_minutes", DEFAULT_WAIT_MINUTES)) * 60))
    except (OSError, ValueError, TypeError):
        return DEFAULT_WAIT_MINUTES * 60


def loop(child_args: list[str], out_dir: str) -> int:
    """Run ``python -m mscal run <child_args>`` forever, sleeping between runs."""
    global _child
    signal.signal(signal.SIGTERM, _on_signal)
    signal.signal(signal.SIGINT, _on_signal)
    cmd = [sys.executable, "-m", "mscal", "run", *child_args]
    while not _stop:
        _child = subprocess.Popen(cmd)
        code = _child.wait()
        _child = None
        if code != 0:
            print(f"mscal: run exited with status {code}; retrying after the normal interval",
                  file=sys.stderr, flush=True)
        wait = _next_wait(out_dir)
        print(f"mscal: next run in {wait // 60} min", file=sys.stderr, flush=True)
        deadline = time.monotonic() + wait
        while not _stop and time.monotonic() < deadline:
            time.sleep(min(5.0, deadline - time.monotonic()))
    return 0


def maybe_loop(argv: list[str]) -> int | None:
    """If ``--loop`` was given to ``run``, run the loop and return its exit code;
    otherwise return None so the normal (heavy) CLI handles the command."""
    if "--loop" not in argv:
        return None
    if argv and argv[0] in ("validate", "providers"):
        return None
    args = [a for a in argv if a not in ("--loop", "run")]
    out = os.environ.get("MSCAL_OUT", "/www")
    for i, a in enumerate(args):
        if a == "--out" and i + 1 < len(args):
            out = args[i + 1]
        elif a.startswith("--out="):
            out = a.split("=", 1)[1]
    return loop(args, out)
