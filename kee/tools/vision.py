"""Tool: vision — describe images via the Auctorum vision endpoint.

Sends a base64-encoded image to the worker's vision FastAPI service
(`scripts/auctorum/vision_server.py` → :8003), which wraps a small
multimodal LLM (default `llava-phi3:3.8b`) on Ollama.

Two input modes:
  - `image_path`: a local file the agent reads + b64-encodes
  - `image_b64`: pre-encoded data (e.g. from `screen` tool's screenshot)

If the worker / vision endpoint is offline, returns a structured
`{ok:false, reason}` so callers can fall back gracefully (skip vision,
ask the user to describe, etc.) instead of crashing.

Risk: 1 — sends image bytes over Tailscale. Bounded — no scraping, no
mass upload, single image per call.
"""

from __future__ import annotations

import base64
import logging
import os
import time
from pathlib import Path
from typing import Any

import httpx

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _vision_url() -> str:
    return os.environ.get(
        "KEE_VISION_URL",
        f"http://{os.environ.get('AUCTORUM_HOST','auctorum')}:8003",
    )


# Cap the image size we'll ship across the wire. Most screenshots are
# fine; truly huge files (say 10MB photos) get rejected before hitting
# the worker so we don't OOM the GPU.
_MAX_IMAGE_BYTES = 6 * 1024 * 1024  # 6 MB


class VisionTool(Tool):
    name = "vision"
    description = (
        "Describe lo que aparece en una imagen usando el endpoint vision "
        "del worker (Auctorum). Útil cuando Coco te pasa un screenshot, "
        "o cuando combinas con `screen` para ver qué hay en pantalla y "
        "razonar sobre eso. Modelo backend: `llava-phi3:3.8b` por default "
        "(lazy-loaded en el worker; primer call ~3-5s cold).\n"
        "Inputs (uno requerido):\n"
        "  - `image_path`: ruta local que yo leo + base64-encode\n"
        "  - `image_b64`: imagen ya pre-encoded (e.g. del `screen.screenshot`)\n"
        "El prompt opcional dirige la descripción ('describe', 'lee el "
        "texto', 'qué app es', etc.). Cap: 6 MB por imagen."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "image_path": {"type": "string"},
            "image_b64": {"type": "string"},
            "prompt": {
                "type": "string",
                "default": "Describe brevemente lo que ves en la imagen.",
            },
            "max_tokens": {"type": "integer", "default": 200},
            "timeout_s": {"type": "number", "default": 60.0},
        },
    }

    async def execute(
        self,
        image_path: str | None = None,
        image_b64: str | None = None,
        prompt: str = "Describe brevemente lo que ves en la imagen.",
        max_tokens: int = 200,
        timeout_s: float = 60.0,
    ) -> dict[str, Any]:
        if not image_path and not image_b64:
            return {"ok": False,
                    "error": "image_path OR image_b64 required"}

        if image_path:
            p = Path(image_path).expanduser()
            if not p.exists():
                return {"ok": False, "error": f"file not found: {p}"}
            data = p.read_bytes()
            if len(data) > _MAX_IMAGE_BYTES:
                return {"ok": False,
                        "error": (f"image too large: {len(data)} bytes "
                                  f"> {_MAX_IMAGE_BYTES}")}
            image_b64 = base64.b64encode(data).decode("ascii")

        url = _vision_url() + "/describe"
        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
                r = await client.post(
                    url,
                    json={
                        "image_b64": image_b64,
                        "prompt": prompt,
                        "max_tokens": int(max_tokens),
                    },
                )
        except Exception as e:
            return {"ok": False, "reason": "vision endpoint unreachable",
                    "error": str(e)[:160], "url": url,
                    "elapsed_ms": int((time.time() - t0) * 1000)}

        if r.status_code >= 400:
            return {"ok": False,
                    "reason": f"vision endpoint returned HTTP {r.status_code}",
                    "body": r.text[:200], "url": url,
                    "elapsed_ms": int((time.time() - t0) * 1000)}

        body = r.json()
        return {
            "ok": True,
            "description": body.get("description", "").strip(),
            "model": body.get("model"),
            "server_elapsed_ms": body.get("elapsed_ms"),
            "elapsed_ms_round_trip": int((time.time() - t0) * 1000),
        }


tool = VisionTool()
