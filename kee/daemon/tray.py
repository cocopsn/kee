"""System-tray icon for Kee.

A tiny pystray-based status indicator. Optional — Kee runs perfectly without
it, but the tray makes the always-listening daemon visible (so Coco knows it's
there and can quit it without opening Task Manager).

The icon color reflects the supervisor state:
- cyan  → all enabled surfaces alive
- amber → at least one surface in backoff/restarting
- red   → supervisor not running

Menu:
- Open dashboard (http://localhost:5173)
- Open API docs (http://127.0.0.1:7330/docs)
- Restart supervisor
- Quit Kee (kills the supervisor)
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import threading
import time
import webbrowser

from kee.daemon.supervisor import read_state

logger = logging.getLogger(__name__)


def _make_icon_image(color: tuple[int, int, int]):
    """Render a simple circular dot — minimal dependencies on PIL."""
    from PIL import Image, ImageDraw  # type: ignore
    size = 64
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.ellipse((6, 6, size - 6, size - 6), fill=color + (255,))
    return img


def _state_to_color(state: dict) -> tuple[int, int, int]:
    if not state.get("running"):
        return (220, 60, 60)        # red
    surfaces = state.get("surfaces", [])
    enabled = [s for s in surfaces if s.get("enabled")]
    alive = [s for s in enabled if s.get("alive")]
    if enabled and len(alive) == len(enabled):
        return (60, 220, 220)       # cyan
    return (220, 180, 60)           # amber


def _kill_supervisor(state: dict) -> None:
    pid = state.get("supervisor_pid")
    if not pid:
        return
    try:
        if sys.platform == "win32":
            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True)
        else:
            os.kill(pid, signal.SIGTERM)
    except Exception as e:
        logger.warning("Could not stop supervisor pid=%s: %s", pid, e)


def _spawn_supervisor() -> None:
    """Start a fresh `python -m kee.main all` detached from this process."""
    cmd = [sys.executable, "-m", "kee.main", "all"]
    flags = 0
    if sys.platform == "win32":
        flags = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    subprocess.Popen(cmd, creationflags=flags, close_fds=True)


def run_tray() -> int:
    try:
        import pystray  # type: ignore
        from PIL import Image  # noqa: F401  # confirm Pillow available
    except ImportError:
        print("Tray icon needs `pystray` and `Pillow`. Install with:")
        print("    pip install pystray Pillow")
        return 1

    icon = pystray.Icon("kee", _make_icon_image((60, 220, 220)), "Kee")

    def on_open_dashboard(_icon, _item):
        webbrowser.open("http://localhost:5173")

    def on_open_api(_icon, _item):
        webbrowser.open("http://127.0.0.1:7330/docs")

    def on_show_hud(_icon, _item):
        # Drop a signal — desktop process picks it up if running.
        try:
            from kee.desktop.app import write_signal
            write_signal("show", mode="hud", reason="tray")
        except Exception:
            pass

    def on_show_full(_icon, _item):
        try:
            from kee.desktop.app import write_signal
            write_signal("show", mode="full", reason="tray")
        except Exception:
            pass

    def on_launch_desktop(_icon, _item):
        """Spawn `kee.main desktop` if no window is running yet."""
        cmd = [sys.executable, "-m", "kee.main", "desktop", "--mode", "hud"]
        flags = 0
        if sys.platform == "win32":
            flags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
        subprocess.Popen(cmd, creationflags=flags, close_fds=True)

    def on_restart(_icon, _item):
        state = read_state()
        _kill_supervisor(state)
        time.sleep(1.5)
        _spawn_supervisor()

    def on_quit(_icon, _item):
        state = read_state()
        _kill_supervisor(state)
        icon.stop()

    icon.menu = pystray.Menu(
        pystray.MenuItem("Show HUD", on_show_hud, default=True),
        pystray.MenuItem("Show full window", on_show_full),
        pystray.MenuItem("Launch desktop app", on_launch_desktop),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Open dashboard (browser)", on_open_dashboard),
        pystray.MenuItem("Open API docs", on_open_api),
        pystray.Menu.SEPARATOR,
        pystray.MenuItem("Restart supervisor", on_restart),
        pystray.MenuItem("Quit Kee", on_quit),
    )

    def status_loop():
        while True:
            try:
                state = read_state()
                surfaces = state.get("surfaces", [])
                enabled = [s for s in surfaces if s.get("enabled")]
                alive = sum(1 for s in enabled if s.get("alive"))
                running = "supervisor up" if state.get("running") else "supervisor down"
                icon.title = f"Kee — {running} · {alive}/{len(enabled)} surfaces"
                icon.icon = _make_icon_image(_state_to_color(state))
            except Exception as e:
                logger.warning("tray status update failed: %s", e)
            time.sleep(3)

    threading.Thread(target=status_loop, daemon=True).start()
    icon.run()
    return 0
