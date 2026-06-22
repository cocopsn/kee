"""Kee desktop window — pywebview shell over the SvelteKit dashboard.

Run as:    python -m kee.main desktop
            python -m kee.main desktop --mode hud      # compact corner overlay
            python -m kee.main desktop --mode full     # full window
            python -m kee.main desktop --url <URL>     # custom dashboard URL

The window listens for **show/hide signals** by polling
``data/desktop_signal.json`` every 250 ms. Other surfaces (voice, telegram,
heartbeat) write that file to surface the window without owning it
directly. Signal payload:

    { "action": "show" | "hide" | "toggle" | "switch_mode",
      "mode":   "hud" | "full",
      "ts":     <epoch>,
      "reason": "wake_word" | "notification" | "manual" }

The app is intentionally a pure view: every action goes through the
existing REST API. No duplicated state. No process duplication: the
dashboard's Vite dev or a built static bundle serves the HTML, and the
desktop process just hosts a WebView pointed at it.
"""

from __future__ import annotations

import json
import logging
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Optional

from kee.config import settings

logger = logging.getLogger(__name__)

SIGNAL_FILENAME = "desktop_signal.json"
DEFAULT_URL = os.environ.get("KEE_DESKTOP_URL", "http://127.0.0.1:7330/app")
# Hologram = small frameless window in the top-right corner. After the
# window is created we apply Win32 `SetLayeredWindowAttributes` with
# COLORKEY=black so every pure-black pixel becomes truly transparent.
# The page draws on a black canvas; the orb's colored pixels stay
# visible, the empty black background disappears, leaving a Bonzi
# Buddy-style floating sprite. Tested on Win11 + Edge WebView2.
HUD_W, HUD_H = 320, 320
FULL_W, FULL_H = 1280, 820

# Polling interval for the signal file. 250 ms feels instant but only burns
# ~4 stat() calls per second.
POLL_S = 0.25


def signal_path() -> Path:
    return settings.data_dir / SIGNAL_FILENAME


# ── Cross-process signal API (read by app, written by other surfaces) ───
def write_signal(action: str, mode: str = "hud", reason: str = "manual",
                 extra: Optional[dict] = None) -> None:
    """Drop a signal for the desktop window to react to."""
    p = signal_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "action": action, "mode": mode, "reason": reason,
        "ts": time.time(), **(extra or {}),
    }
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(payload), encoding="utf-8")
    tmp.replace(p)


def read_and_clear_signal() -> Optional[dict]:
    """Read the latest signal once and delete the file (consume-once)."""
    p = signal_path()
    if not p.exists():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        try:
            p.unlink()
        except Exception:
            pass
        return data
    except Exception as e:
        logger.warning("desktop signal read failed: %s", e)
        return None


# ── Bridge object exposed to JS (window.pywebview.api.*) ────────────────
class DesktopBridge:
    """Methods on this object are callable from the dashboard JS as
    ``window.pywebview.api.<method>(...)``. Used by the HUD to switch modes,
    minimize to tray, take a screenshot for Watch Mode, etc.
    """

    def __init__(self, app: "DesktopApp"):
        self._app = app

    # — window control —
    def show(self) -> None:
        self._app.show()
    def hide(self) -> None:
        self._app.hide()
    def minimize(self) -> None:
        self._app.minimize()
    def switch_mode(self, mode: str) -> None:
        self._app.switch_mode(mode)
    def quit(self) -> None:
        self._app.quit()
    def start_drag(self) -> None:
        """Tell Windows to treat the current mouse-down as a window drag.

        Win32: ReleaseCapture + SendMessage(WM_NCLBUTTONDOWN, HTCAPTION, 0).
        The OS then handles dragging natively. No JS bookkeeping needed.
        """
        try:
            import ctypes
            user32 = ctypes.windll.user32
            hwnd = getattr(self._app, "_hwnd", None)
            if not hwnd:
                # Find by title as fallback
                hwnd = user32.FindWindowW(None, "Kee")
            if not hwnd:
                return
            WM_NCLBUTTONDOWN = 0x00A1
            HTCAPTION = 2
            user32.ReleaseCapture()
            user32.SendMessageW(hwnd, WM_NCLBUTTONDOWN, HTCAPTION, 0)
        except Exception as e:
            logger.warning("start_drag failed: %s", e)

    # — system —
    def take_screenshot(self) -> dict:
        """Capture the visible screen via the existing ``screen`` tool.
        Returns ``{ok, path, ocr_text}``. Used by Watch Mode in the HUD."""
        try:
            import asyncio
            from kee.tools.screen import tool as screen_tool
            loop = asyncio.new_event_loop()
            try:
                r = loop.run_until_complete(screen_tool.execute(action="screenshot"))
            finally:
                loop.close()
            return {"ok": True, "result": r}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    def watch_observe(self) -> dict:
        """Single observation pass: screenshot + OCR + send to agent for
        a one-line description ("looking at: VS Code with kee/main.py").

        Heavier than ``take_screenshot``: spends an Ollama call. Triggered
        by the Watch Mode toggle in the HUD."""
        try:
            import asyncio
            from kee.tools.screen import tool as screen_tool
            loop = asyncio.new_event_loop()
            try:
                shot = loop.run_until_complete(
                    screen_tool.execute(action="find_text", query="*"),
                )
                # Then ask the agent to describe it via the lightest Ollama tier
                from kee.core.agent import KeeAgent
                a = KeeAgent()
                a.bootstrap()
                prompt = (
                    "Describe in one short Spanish sentence what's on Coco's "
                    f"screen right now. OCR sample: {str(shot)[:600]}"
                )
                resp, _ = loop.run_until_complete(a.process(prompt, source="watch"))
            finally:
                loop.close()
            return {"ok": True, "description": resp[:200] if resp else ""}
        except Exception as e:
            return {"ok": False, "error": str(e)}


# ── Window manager ──────────────────────────────────────────────────────
class DesktopApp:
    """Owns the pywebview window, signal-poller thread, and mode switching."""

    def __init__(self, mode: str = "hud", url: str = DEFAULT_URL):
        self.url = url.rstrip("/")
        self.mode = mode
        self._win = None
        self._stop = threading.Event()
        # Dismiss-lockout: when the user manually hides the window via the
        # `_` button or `quit` action, we ignore subsequent `show` signals
        # for `_DISMISS_LOCKOUT_S` seconds so the wake-word / heartbeat /
        # market alerts can't immediately resurrect a window the user just
        # closed. Wake-word reasons can override (so "Kee" still works);
        # other reasons (heartbeat, notification, tray-pop) respect it.
        self._user_dismissed_at = 0.0
        self._DISMISS_LOCKOUT_S = 60.0

    # ── Lifecycle ────────────────────────────────────────────────────
    def run(self) -> int:
        try:
            import webview  # type: ignore
        except ImportError:
            print("Desktop app needs `pywebview`. Install with:")
            print("    D:\\Kee\\.venv\\Scripts\\pip install pywebview")
            return 1

        bridge = DesktopBridge(self)
        target_url = self._url_for_mode(self.mode)

        if self.mode in ("hud", "hologram"):
            # Small frameless window. easy_drag=False is CRITICAL — when
            # True it makes pywebview swallow ALL mousedown events as
            # window-drag, breaking every button. We do dragging via an
            # explicit CSS region in the page (data-pywebview-drag-region
            # attribute or our own ondragstart handler).
            self._win = webview.create_window(
                "Kee",
                url=target_url,
                width=HUD_W, height=HUD_H,
                x=self._right_edge_x(HUD_W), y=24,
                frameless=True,
                on_top=True,
                transparent=False,
                resizable=False,
                easy_drag=False,
                background_color="#0A0A0F",
                js_api=bridge,
            )
        else:
            self._win = webview.create_window(
                "Kee",
                url=target_url,
                width=FULL_W, height=FULL_H,
                resizable=True,
                background_color="#0A0A0F",
                js_api=bridge,
            )

        # Signal poller in a background thread — pywebview owns the main loop.
        threading.Thread(target=self._signal_loop, daemon=True).start()

        # Apply color-key transparency once the window is up — Bonzi Buddy
        # style: every #000000 pixel disappears, only colored pixels show.
        if sys.platform == "win32" and self.mode in ("hud", "hologram"):
            threading.Thread(
                target=self._apply_colorkey_transparency, daemon=True,
            ).start()

        logger.info("Kee desktop starting (mode=%s, url=%s)", self.mode, target_url)
        # webview.start() blocks until the window closes.
        # gui='edgechromium' explicitly selects WebView2 backend; on
        # Windows pywebview otherwise auto-detects but EdgeChromium gives
        # us reliable -webkit-app-region: drag/no-drag support, which is
        # what makes the titlebar drag + button clicks coexist.
        webview.start(http_server=False, debug=False, gui='edgechromium')
        self._stop.set()
        return 0

    # ── Signal polling ───────────────────────────────────────────────
    def _signal_loop(self) -> None:
        while not self._stop.is_set():
            sig = read_and_clear_signal()
            if sig:
                self._handle_signal(sig)
            time.sleep(POLL_S)

    def _handle_signal(self, sig: dict) -> None:
        action = sig.get("action")
        reason = sig.get("reason", "")
        if action == "minimize":
            self.minimize()
            return
        if action == "show":
            # Honour user-dismiss lockout for everything except wake-word
            # and explicit tray clicks (those are direct user intent).
            if reason not in ("wake_word", "tray", "user"):
                age = time.time() - self._user_dismissed_at
                if age < self._DISMISS_LOCKOUT_S:
                    logger.debug(
                        "ignoring show (reason=%s) — dismiss lockout %ds left",
                        reason, int(self._DISMISS_LOCKOUT_S - age),
                    )
                    return
            self.show()
            mode = sig.get("mode")
            if mode and mode != self.mode:
                self.switch_mode(mode)
        elif action == "hide":
            self.hide()
        elif action == "toggle":
            self.toggle()
        elif action == "switch_mode":
            self.switch_mode(sig.get("mode", "hud"))

    # ── Window ops ───────────────────────────────────────────────────
    def show(self) -> None:
        if self._win is None:
            return
        try:
            self._win.show()
            # Bring to foreground (Windows-specific helper exists)
            try:
                self._win.restore()
            except Exception:
                pass
        except Exception as e:
            logger.warning("show() failed: %s", e)

    def hide(self) -> None:
        if self._win is None:
            return
        try:
            self._win.hide()
            # Mark as user-dismissed so heartbeat / market alerts can't
            # resurrect the window we just closed.
            self._user_dismissed_at = time.time()
        except Exception as e:
            logger.warning("hide() failed: %s", e)

    def minimize(self) -> None:
        if self._win is None:
            return
        try:
            self._win.minimize()
            self._user_dismissed_at = time.time()
        except Exception as e:
            logger.warning("minimize() failed: %s", e)

    def toggle(self) -> None:
        # pywebview doesn't expose a public "is_visible" flag uniformly across
        # backends; flip via best-effort show().
        self.show()

    def quit(self) -> None:
        if self._win is None:
            return
        try:
            self._win.destroy()
        except Exception:
            pass
        self._stop.set()

    def switch_mode(self, mode: str) -> None:
        """Resize/reload the window into the new mode. Re-loads the URL."""
        if mode not in ("hud", "full") or mode == self.mode:
            return
        self.mode = mode
        if self._win is None:
            return
        try:
            self._win.load_url(self._url_for_mode(mode))
            if mode == "hud":
                self._win.resize(HUD_W, HUD_H)
                self._win.move(self._right_edge_x(HUD_W), 80)
            else:
                self._win.resize(FULL_W, FULL_H)
        except Exception as e:
            logger.warning("switch_mode failed: %s", e)

    def _url_for_mode(self, mode: str) -> str:
        # /hologram is the new transparent overlay (just the NeuralCanvas).
        # /hud is kept around as the deprecated chrome'd HUD card.
        # `mode='hud'` and `mode='hologram'` both route to the hologram now.
        base = self.url.rstrip("/")
        if base.endswith("/app"):
            base = base[:-4]
        if mode in ("hud", "hologram"):
            return base + "/app/hologram"
        return base + "/app"

    # ── Helpers ──────────────────────────────────────────────────────
    @staticmethod
    def _right_edge_x(window_w: int) -> int:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            screen_w = user32.GetSystemMetrics(0)
            return max(0, screen_w - window_w - 24)
        except Exception:
            return 1200      # safe default for 1920px display

    @staticmethod
    def _center_x(window_w: int) -> int:
        try:
            import ctypes
            screen_w = ctypes.windll.user32.GetSystemMetrics(0)
            return max(0, (screen_w - window_w) // 2)
        except Exception:
            return 600

    @staticmethod
    def _center_y(window_h: int) -> int:
        try:
            import ctypes
            screen_h = ctypes.windll.user32.GetSystemMetrics(1)
            return max(0, (screen_h - window_h) // 2)
        except Exception:
            return 200

    @staticmethod
    def _screen_size() -> tuple[int, int]:
        try:
            import ctypes
            user32 = ctypes.windll.user32
            return user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        except Exception:
            return 1920, 1080

    def _apply_colorkey_transparency(self) -> None:
        """After the window is up, force HWND_TOPMOST via Win32 + repoll
        every 3 s so other apps can't steal the top spot.

        (We used to also apply WS_EX_LAYERED + LWA_COLORKEY for pure-black
        transparency, but that made the whole window click-through on
        WebView2 — buttons & orb both unreachable. Dropped that piece;
        kept the topmost guarantee.)
        """
        try:
            import ctypes
            from ctypes import wintypes
        except Exception as e:
            logger.warning("ctypes unavailable: %s", e)
            return
        user32 = ctypes.windll.user32
        SWP_NOSIZE = 0x0001
        SWP_NOMOVE = 0x0002
        SWP_NOACTIVATE = 0x0010
        HWND_TOPMOST = -1
        FindWindowW = user32.FindWindowW
        SetWindowPos = user32.SetWindowPos

        hwnd = 0
        for _ in range(50):
            time.sleep(0.1)
            hwnd = FindWindowW(None, "Kee")
            if hwnd:
                break
        if not hwnd:
            logger.warning("HWND_TOPMOST: never found Kee window")
            return
        self._hwnd = hwnd
        SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                     SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
        logger.info("HWND_TOPMOST applied to HWND %s", hwnd)

        def _topmost_keeper():
            while not self._stop.is_set():
                try:
                    SetWindowPos(hwnd, HWND_TOPMOST, 0, 0, 0, 0,
                                 SWP_NOSIZE | SWP_NOMOVE | SWP_NOACTIVATE)
                except Exception:
                    return
                time.sleep(3.0)
        threading.Thread(target=_topmost_keeper, daemon=True).start()


# ── Public entrypoint ────────────────────────────────────────────────────
def run_desktop(mode: str = "hud", url: str = DEFAULT_URL) -> int:
    return DesktopApp(mode=mode, url=url).run()
