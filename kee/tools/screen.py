"""Computer Use stub — screen + cursor I/O without vision LLM.

Phase 4 stretch goal in v2 was full Computer Use via Gemma on the worker
node. Until Auctorum comes online we ship a simpler subset that's
already useful:

  - `screenshot`: capture full screen or region → write PNG to data/
  - `mouse_move` / `mouse_click`: move + click at (x, y)
  - `type_text`: type via the OS keyboard (works on focused window)
  - `find_text`: best-effort OCR locate of a text string on screen

OCR uses pytesseract if available; otherwise returns "ocr_unavailable"
so the agent can fall back gracefully. Screenshots use mss (cross
platform, no admin needed).

All actions are gated by `risk_level=2` (system) so the verification
loop captures pre/post state and the audit trail is rich.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


class ScreenTool(Tool):
    name = "screen"
    description = (
        "Direct OS-level screen + cursor + keyboard control. "
        "Actions: screenshot (full or region), mouse_move(x,y), "
        "mouse_click(x,y,button), type_text(text), find_text(query) → returns "
        "the (x,y) center of the first OCR match. All coordinates are "
        "physical pixels (DPI-aware). Use sparingly — irreversible."
    )
    risk_level = 2  # system-level
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["screenshot", "mouse_move", "mouse_click",
                         "type_text", "find_text"],
            },
            "x": {"type": "integer"},
            "y": {"type": "integer"},
            "region": {
                "type": "array",
                "description": "[x, y, width, height] for region screenshot",
                "items": {"type": "integer"},
                "minItems": 4, "maxItems": 4,
            },
            "button": {"type": "string", "enum": ["left", "right", "middle"], "default": "left"},
            "text": {"type": "string"},
            "query": {"type": "string"},
        },
        "required": ["action"],
    }

    async def execute(self, action: str, **kwargs: Any) -> dict[str, Any]:
        try:
            if action == "screenshot":
                return await self._screenshot(kwargs.get("region"))
            if action == "mouse_move":
                return await self._mouse_move(int(kwargs["x"]), int(kwargs["y"]))
            if action == "mouse_click":
                return await self._mouse_click(
                    int(kwargs["x"]), int(kwargs["y"]),
                    kwargs.get("button", "left"),
                )
            if action == "type_text":
                return await self._type_text(kwargs["text"])
            if action == "find_text":
                return await self._find_text(kwargs["query"])
            return {"status": "error", "reason": f"unknown action {action!r}"}
        except KeyError as e:
            return {"status": "error", "reason": f"missing param: {e}"}
        except Exception as e:
            logger.exception("screen tool failed")
            return {"status": "error", "reason": str(e)}

    # ── Screenshot ───────────────────────────────────────────────────────
    async def _screenshot(self, region: list[int] | None) -> dict[str, Any]:
        try:
            import mss
        except ImportError:
            return {"status": "error", "reason": "mss not installed (pip install mss)"}
        out_dir = settings.data_dir / "screenshots"
        out_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.utcnow().strftime("%Y%m%dT%H%M%S")
        path = out_dir / f"screen-{ts}.png"
        with mss.mss() as sct:
            if region and len(region) == 4:
                bbox = {"left": region[0], "top": region[1],
                        "width": region[2], "height": region[3]}
            else:
                bbox = sct.monitors[1]  # primary monitor
            img = sct.grab(bbox)
            mss.tools.to_png(img.rgb, img.size, output=str(path))
        return {
            "status": "ok",
            "path": str(path),
            "width": img.width, "height": img.height,
            "bbox": bbox,
        }

    # ── Mouse + keyboard ─────────────────────────────────────────────────
    async def _mouse_move(self, x: int, y: int) -> dict[str, Any]:
        try:
            import pyautogui
            pyautogui.FAILSAFE = True
            pyautogui.moveTo(x, y, duration=0.15)
        except ImportError:
            return {"status": "error", "reason": "pyautogui not installed"}
        return {"status": "ok", "x": x, "y": y}

    async def _mouse_click(self, x: int, y: int, button: str) -> dict[str, Any]:
        try:
            import pyautogui
            pyautogui.click(x=x, y=y, button=button)
        except ImportError:
            return {"status": "error", "reason": "pyautogui not installed"}
        return {"status": "ok", "x": x, "y": y, "button": button}

    async def _type_text(self, text: str) -> dict[str, Any]:
        try:
            import pyautogui
            pyautogui.typewrite(text, interval=0.02)
        except ImportError:
            return {"status": "error", "reason": "pyautogui not installed"}
        return {"status": "ok", "chars": len(text)}

    # ── OCR find ─────────────────────────────────────────────────────────
    async def _find_text(self, query: str) -> dict[str, Any]:
        try:
            import mss
            import pytesseract
            from PIL import Image
        except ImportError as e:
            return {"status": "ocr_unavailable", "missing": str(e)}
        with mss.mss() as sct:
            bbox = sct.monitors[1]
            img = sct.grab(bbox)
            pil = Image.frombytes("RGB", img.size, img.rgb)
        try:
            data = pytesseract.image_to_data(pil, output_type=pytesseract.Output.DICT)
        except pytesseract.TesseractNotFoundError:
            return {"status": "ocr_unavailable",
                    "reason": "Tesseract binary not on PATH (install via choco/scoop)"}
        q = query.lower().strip()
        matches: list[dict[str, Any]] = []
        for i, txt in enumerate(data["text"]):
            if not txt or txt.lower().find(q) < 0:
                continue
            matches.append({
                "text": txt,
                "x": int(data["left"][i] + data["width"][i] / 2),
                "y": int(data["top"][i] + data["height"][i] / 2),
                "w": int(data["width"][i]),
                "h": int(data["height"][i]),
                "conf": int(data.get("conf", [0])[i] or 0),
            })
        return {
            "status": "ok",
            "query": query,
            "match_count": len(matches),
            "matches": matches[:10],
        }


tool = ScreenTool()
