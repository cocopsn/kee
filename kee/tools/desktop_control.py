"""Tool: desktop_control — voice-driven control of the hologram window.

Lets the agent show/hide/watch the desktop overlay in response to natural
language. Tied to the same signal-pipe the supervisor + tray + heartbeat
use, so it's instant and one-way (no callback needed).

Examples:
  - "Kee, ocultate"           → desktop_control(action='hide')
  - "Kee, ven aca"            → desktop_control(action='show')
  - "Kee, ve mi pantalla"     → desktop_control(action='watch_on')
  - "Kee, deja de mirar"      → desktop_control(action='watch_off')
  - "Kee, pantalla completa"  → desktop_control(action='switch_full')

Risk: 1 (UI side-effect, fully reversible by saying the opposite).
"""

from __future__ import annotations

from typing import Any

from kee.tools.base import Tool


class DesktopControlTool(Tool):
    name = "desktop_control"
    description = (
        "Show, hide, or change the Kee hologram overlay on the user's "
        "screen. Use when the user asks Kee to disappear, come back, "
        "watch the screen, or switch between modes. The hologram is a "
        "transparent overlay with the neural canvas — hiding it puts "
        "Kee in pure background mode (heartbeat + voice still listening). "
        "Showing brings the orb back to the screen. Watch turns on a "
        "30-second screen-observation loop. Always reversible.\n"
        "Actions:\n"
        "  - 'show':         summon the hologram (overrides dismiss-lockout)\n"
        "  - 'hide':         hide the hologram entirely, lock out auto-summons 60 s\n"
        "  - 'minimize':     send to taskbar (Coco can click to restore)\n"
        "  - 'switch_full':  expand to the full dashboard window\n"
        "  - 'switch_hologram': back to the transparent overlay\n"
        "  - 'watch_on':     start observing the screen every 30 s\n"
        "  - 'watch_off':    stop the observation loop"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "show", "hide", "minimize", "switch_full",
                    "switch_hologram", "watch_on", "watch_off",
                ],
            },
        },
        "required": ["action"],
    }

    async def execute(self, action: str) -> dict[str, Any]:
        from kee.desktop.app import write_signal
        if action == "show":
            write_signal("show", mode="hologram", reason="user")
            return {"ok": True, "did": "shown the hologram"}
        if action == "hide":
            write_signal("hide", reason="user")
            return {"ok": True, "did": "hidden, won't auto-summon for 60s"}
        if action == "minimize":
            write_signal("minimize", reason="user")
            return {"ok": True, "did": "minimized to taskbar"}
        if action == "switch_full":
            write_signal("show", mode="full", reason="user")
            return {"ok": True, "did": "switched to full dashboard"}
        if action == "switch_hologram":
            write_signal("show", mode="hologram", reason="user")
            return {"ok": True, "did": "switched to hologram overlay"}
        if action == "watch_on":
            # Watch toggle is in the hologram JS — we can't flip it from
            # Python directly. Surface the hologram + drop a signal the
            # JS can pick up via a polling state file.
            write_signal("show", mode="hologram", reason="user",
                         extra={"watch": "on"})
            return {"ok": True, "did": "watch mode requested (hologram surfaced)",
                    "note": "Toggle in HUD if the click didn't auto-fire."}
        if action == "watch_off":
            write_signal("show", mode="hologram", reason="user",
                         extra={"watch": "off"})
            return {"ok": True, "did": "watch mode off requested"}
        return {"ok": False, "error": f"unknown action '{action}'"}


tool = DesktopControlTool()
