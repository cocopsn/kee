"""Tool: system_control — volume + brightness + sleep on Windows.

Voice ergonomics. "Súbele al volumen", "bájale el brillo", "duerme la
laptop". Cross-platform-safe-degrades — uses pycaw on Windows, falls
back to nothing on Linux/Mac (will degrade gracefully).

Risk: 1 (UI side-effect, reversible).
"""

from __future__ import annotations

import logging
import platform
import subprocess
from typing import Any, Optional

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _set_volume_win(level: int) -> tuple[bool, str]:
    """Windows volume via pycaw. level=0..100."""
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        # pycaw uses scalar 0.0-1.0
        volume.SetMasterVolumeLevelScalar(max(0, min(100, level)) / 100.0, None)
        return True, f"volume set to {level}%"
    except Exception as e:
        return False, f"pycaw failed: {e}"


def _get_volume_win() -> Optional[int]:
    try:
        from comtypes import CLSCTX_ALL
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        devices = AudioUtilities.GetSpeakers()
        interface = devices.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = interface.QueryInterface(IAudioEndpointVolume)
        return int(volume.GetMasterVolumeLevelScalar() * 100)
    except Exception:
        return None


def _set_brightness(level: int) -> tuple[bool, str]:
    """Cross-platform via screen-brightness-control."""
    try:
        import screen_brightness_control as sbc
        sbc.set_brightness(max(0, min(100, level)))
        return True, f"brightness set to {level}%"
    except Exception as e:
        return False, str(e)


def _get_brightness() -> Optional[int]:
    try:
        import screen_brightness_control as sbc
        vals = sbc.get_brightness()
        return int(vals[0]) if vals else None
    except Exception:
        return None


def _system_sleep() -> tuple[bool, str]:
    """Put the machine to sleep."""
    s = platform.system()
    try:
        if s == "Windows":
            subprocess.run(
                ["rundll32.exe", "powrprof.dll,SetSuspendState", "0,1,0"],
                check=False,
            )
            return True, "sleep requested"
        if s == "Linux":
            subprocess.run(["systemctl", "suspend"], check=False)
            return True, "sleep requested"
        if s == "Darwin":
            subprocess.run(["pmset", "sleepnow"], check=False)
            return True, "sleep requested"
        return False, f"unsupported OS: {s}"
    except Exception as e:
        return False, str(e)


def _system_lock() -> tuple[bool, str]:
    s = platform.system()
    try:
        if s == "Windows":
            import ctypes
            ctypes.windll.user32.LockWorkStation()
            return True, "workstation locked"
        if s == "Linux":
            for cmd in (["loginctl", "lock-session"],
                        ["xdg-screensaver", "lock"]):
                try:
                    subprocess.run(cmd, check=False); return True, "locked"
                except Exception: continue
            return False, "no lock command available"
        if s == "Darwin":
            subprocess.run(["pmset", "displaysleepnow"], check=False)
            return True, "display sleep"
        return False, f"unsupported OS: {s}"
    except Exception as e:
        return False, str(e)


class SystemControlTool(Tool):
    name = "system_control"
    description = (
        "Control physical hardware: volume, brightness, sleep, lock. Voice "
        "ergonomics — when Coco says 'súbele al volumen', 'bájale el brillo', "
        "'duerme la compu', 'bloquea la pantalla'.\n"
        "Actions:\n"
        "  - 'volume': get or set system volume (0-100)\n"
        "  - 'brightness': get or set display brightness (0-100)\n"
        "  - 'sleep': put the machine to sleep\n"
        "  - 'lock': lock the screen (no shutdown)"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["volume", "brightness", "sleep", "lock"],
            },
            "level": {"type": "integer", "minimum": 0, "maximum": 100,
                      "description": "Target level for volume/brightness. Omit to read current."},
        },
        "required": ["action"],
    }

    async def execute(self, action: str, level: Optional[int] = None) -> dict[str, Any]:
        if action == "volume":
            if level is None:
                cur = _get_volume_win()
                return {"ok": cur is not None, "current_pct": cur}
            ok, msg = _set_volume_win(level)
            return {"ok": ok, "message": msg if ok else None,
                    "error": None if ok else msg}
        if action == "brightness":
            if level is None:
                cur = _get_brightness()
                return {"ok": cur is not None, "current_pct": cur}
            ok, msg = _set_brightness(level)
            return {"ok": ok, "message": msg if ok else None,
                    "error": None if ok else msg}
        if action == "sleep":
            ok, msg = _system_sleep()
            return {"ok": ok, "message": msg if ok else None,
                    "error": None if ok else msg}
        if action == "lock":
            ok, msg = _system_lock()
            return {"ok": ok, "message": msg if ok else None,
                    "error": None if ok else msg}
        return {"ok": False, "error": f"unknown action '{action}'"}


tool = SystemControlTool()
