"""Cross-platform active-window inspection.

Phase 3 perception piece: report the currently focused window. Used by the
heartbeat for context-switch detection.

Strategy:
  * Windows: `pygetwindow` (already in deps under `sys_platform == 'win32'`).
  * Linux:   `xdotool` via subprocess (no Python lib needed).
  * macOS / unknown: returns a stub `{title: '?', source: 'unsupported'}`.

All functions are sync — getting the active window is a system call, not I/O.
The caller is expected to invoke this from a background thread or accept the
microsecond-scale block.
"""

from __future__ import annotations

import logging
import subprocess
import sys
from typing import Any

logger = logging.getLogger(__name__)


def _windows() -> dict[str, Any]:
    try:
        import pygetwindow as gw  # type: ignore[import-not-found]
    except ImportError:
        return {"title": None, "app": None, "source": "no_pygetwindow"}
    try:
        win = gw.getActiveWindow()
    except Exception as e:  # noqa: BLE001 — pygetwindow raises various
        return {"title": None, "app": None, "source": "pygetwindow_error", "error": str(e)}
    if win is None:
        return {"title": None, "app": None, "source": "windows"}
    title = getattr(win, "title", "") or ""
    # Heuristic: app name is usually the trailing segment after the last " - ".
    app = title.split(" - ")[-1] if " - " in title else title
    return {"title": title, "app": app, "source": "pygetwindow"}


def _linux() -> dict[str, Any]:
    try:
        r = subprocess.run(
            ["xdotool", "getactivewindow", "getwindowname"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired) as e:
        return {"title": None, "app": None, "source": "no_xdotool", "error": str(e)}
    title = r.stdout.strip()
    app = title.split(" - ")[-1] if " - " in title else title
    return {"title": title, "app": app, "source": "xdotool"}


def get_active_window() -> dict[str, Any]:
    """Return `{title, app, source, [error]}`. Fields can be None."""
    if sys.platform == "win32":
        return _windows()
    if sys.platform.startswith("linux"):
        return _linux()
    return {"title": None, "app": None, "source": "unsupported"}
