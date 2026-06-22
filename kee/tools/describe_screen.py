"""Tool: describe_screen — screenshot + vision in one call.

Convenience wrapper that:
  1. Takes a screenshot via the `screen` tool (mss, all-monitors or one)
  2. Sends the resulting PNG to the `vision` tool (Auctorum llava endpoint)
  3. Returns the description + raw screenshot path

Use case: Coco asks "qué hay en mi pantalla" or "¿qué dice esa
notificación?" and you want a single LLM-grounded answer instead of two
separate tool calls.

Risk: 1 — captures screen pixels (potentially sensitive) and ships them
over Tailscale to the worker.
"""

from __future__ import annotations

import base64
import logging
from pathlib import Path
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


class DescribeScreenTool(Tool):
    name = "describe_screen"
    description = (
        "Captura un screenshot y se lo manda al endpoint vision para que "
        "describa qué hay en pantalla. Útil cuando Coco pregunta '¿qué "
        "tengo abierto?' o '¿qué dice ese mensaje?' o necesita razonar "
        "sobre una UI sin describirla. Wraps `screen.screenshot` + "
        "`vision.describe` en una sola call.\n"
        "Por seguridad: requiere el worker online (vision endpoint). "
        "Cap: 6 MB por imagen (definido en `vision`)."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "monitor": {
                "type": "integer", "default": 0,
                "description": "Monitor index (0 = all monitors, 1 = primary, 2 = secondary).",
            },
            "prompt": {
                "type": "string",
                "default": "Describe brevemente qué se ve en pantalla: app activa, contenido principal, elementos destacados.",
            },
            "save_screenshot": {
                "type": "boolean", "default": False,
                "description": "Mantener el .png en data/screenshots/ después de la descripción (default false: cleanup auto).",
            },
        },
    }

    async def execute(
        self,
        monitor: int = 0,
        prompt: str = "Describe brevemente qué se ve en pantalla.",
        save_screenshot: bool = False,
    ) -> dict[str, Any]:
        # 1. Screenshot via screen tool
        from kee.tools.screen import tool as screen_tool
        shot = await screen_tool.execute(action="screenshot", monitor=int(monitor))
        if not shot.get("ok") and "path" not in shot:
            return {"ok": False, "error": "screenshot failed",
                    "screen_result": shot}
        path = shot.get("path") or shot.get("file")
        if not path:
            return {"ok": False, "error": "screenshot returned no path",
                    "screen_result": shot}

        # 2. Read + base64
        p = Path(path)
        if not p.exists():
            return {"ok": False,
                    "error": f"screenshot file vanished: {p}"}
        try:
            data = p.read_bytes()
        except OSError as e:
            return {"ok": False, "error": f"could not read screenshot: {e}"}

        # 3. Send to vision
        from kee.tools.vision import tool as vision_tool
        out = await vision_tool.execute(
            image_b64=base64.b64encode(data).decode("ascii"),
            prompt=prompt,
            timeout_s=60.0,
        )

        # 4. Cleanup unless asked to keep
        if not save_screenshot:
            try:
                p.unlink()
            except OSError:
                pass
        else:
            out["screenshot_path"] = str(p)

        # Tag the result with provenance
        if out.get("ok"):
            out["bytes_captured"] = len(data)
        return out


tool = DescribeScreenTool()
