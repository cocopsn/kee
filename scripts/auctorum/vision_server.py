"""Auctorum vision endpoint.

Wraps a small VLM (defaults to `llava-phi3:3.8b` ~2.4 GB or `gemma2:2b`)
behind a FastAPI server on port 8003. Receives a base64-encoded image
plus a prompt, returns a description.

NOT loaded at startup — first request triggers the model swap. Callers
must respect VRAM budget (Alienware-side `VRAMArbiter` should hold a
`VISION` lease before invoking this).

Deploy:
    sudo mkdir -p /opt/keevision && sudo chown $USER /opt/keevision
    python3.12 -m venv /opt/keevision/venv
    source /opt/keevision/venv/bin/activate
    pip install fastapi uvicorn[standard] httpx pydantic

Pull the model first (one-time):
    ollama pull llava-phi3:3.8b      # default, faster
    # or:
    ollama pull gemma2:2b            # smaller fallback

Then save this file as /opt/keevision/server.py and create
/etc/systemd/system/keevision.service (analogous to reranker.service).
"""

from __future__ import annotations

import base64
import logging
import os
import time

import httpx
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
import uvicorn

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("vision")


VISION_MODEL = os.environ.get("VISION_MODEL", "llava-phi3:3.8b")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")


app = FastAPI(title="Kee vision", version="1.0.0")


class VisionRequest(BaseModel):
    image_b64: str = Field(..., min_length=8)
    prompt: str = Field(default="Describe the image briefly.",
                        min_length=1, max_length=1000)
    max_tokens: int = Field(default=200, ge=10, le=2000)


class VisionResponse(BaseModel):
    model: str
    elapsed_ms: int
    prompt: str
    description: str


@app.get("/health")
def health() -> dict:
    return {"status": "ok", "model": VISION_MODEL,
            "backend": OLLAMA_URL}


@app.post("/describe", response_model=VisionResponse)
async def describe(req: VisionRequest) -> VisionResponse:
    # Validate image is decodable b64 (don't fully load to keep this cheap)
    try:
        base64.b64decode(req.image_b64[:1024], validate=True)
    except Exception:
        raise HTTPException(400, "image_b64 not valid base64")

    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            r = await client.post(
                f"{OLLAMA_URL}/api/generate",
                json={
                    "model": VISION_MODEL,
                    "prompt": req.prompt,
                    "images": [req.image_b64],
                    "stream": False,
                    "options": {"num_predict": req.max_tokens},
                },
            )
        if r.status_code >= 300:
            raise HTTPException(502, f"ollama {r.status_code}: {r.text[:200]}")
        body = r.json()
        elapsed_ms = int((time.time() - t0) * 1000)
        return VisionResponse(
            model=VISION_MODEL,
            elapsed_ms=elapsed_ms,
            prompt=req.prompt,
            description=(body.get("response") or "").strip(),
        )
    except httpx.RequestError as e:
        raise HTTPException(502, f"ollama unreachable: {e}")


if __name__ == "__main__":
    port = int(os.environ.get("VISION_PORT", "8003"))
    log.info("Starting vision endpoint on 0.0.0.0:%d (model=%s)",
             port, VISION_MODEL)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
