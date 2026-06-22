"""Tool: worker_health — probe the Auctorum worker stack.

Polls the health aggregator running on Auctorum (`scripts/auctorum/
health_server.py` → :8080) and returns a structured snapshot of every
subsystem (chroma, ollama, reranker, vision, gpu, disk, load).

When the worker is offline this returns `{ok: false, reason: …}` instead
of raising, so the agent can decide gracefully (fall back to local
inference, skip semantic memory, etc.).

Risk: 0 — read-only HTTP probe over Tailscale.
"""

from __future__ import annotations

import os
import time
from typing import Any

import httpx

from kee.tools.base import Tool


def _worker_url() -> str:
    return os.environ.get(
        "KEE_WORKER_HEALTH_URL",
        f"http://{os.environ.get('AUCTORUM_HOST','auctorum')}:8080",
    )


class WorkerHealthTool(Tool):
    name = "worker_health"
    description = (
        "Probe el worker node (Auctorum) y devuelve estado de cada "
        "subsistema: chroma, ollama, reranker, vision, gpu, disk, load. "
        "Usa esto ANTES de decidir si vale la pena llamar memory_search "
        "(requiere chroma+reranker), o si la inferencia se va a fallback "
        "local porque el worker está caído. URL configurable vía "
        "`KEE_WORKER_HEALTH_URL` (default `http://auctorum:8080`).\n"
        "Acciones:\n"
        "  - 'snapshot' (default): full JSON de subsystems\n"
        "  - 'subsystem': solo uno (`name` required: chroma | ollama | "
        "reranker | vision | gpu | disk | load)\n"
        "  - 'summary': string one-liner para citar"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["snapshot", "subsystem", "summary", "reindex"],
                "default": "snapshot",
            },
            "name": {
                "type": "string",
                "description": "Subsystem name (subsystem action only).",
            },
            "force": {
                "type": "boolean", "default": False,
                "description": "Force reindex even if no vault changes "
                               "(reindex action only).",
            },
            "timeout_s": {"type": "number", "default": 4.0},
        },
    }

    async def execute(
        self,
        action: str = "snapshot",
        name: str | None = None,
        force: bool = False,
        timeout_s: float = 4.0,
    ) -> dict[str, Any]:
        if action == "reindex":
            from kee.cognition.worker_reindex import maybe_reindex
            return await maybe_reindex(force=bool(force))
        url = _worker_url()
        path = "/health" if action != "subsystem" else f"/health/{name}"
        if action == "subsystem" and not name:
            return {"ok": False, "error": "name required for subsystem"}

        t0 = time.time()
        try:
            async with httpx.AsyncClient(timeout=float(timeout_s)) as client:
                r = await client.get(f"{url}{path}")
        except Exception as e:
            return {
                "ok": False,
                "reason": "worker unreachable",
                "error": str(e)[:160],
                "url": url + path,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

        if r.status_code >= 300:
            return {
                "ok": False,
                "reason": f"worker returned HTTP {r.status_code}",
                "url": url + path,
                "elapsed_ms": int((time.time() - t0) * 1000),
            }

        data = r.json()
        elapsed_ms = int((time.time() - t0) * 1000)

        if action == "summary":
            ok = data.get("ok", False)
            host = data.get("host", "?")
            subs = data.get("subsystems") or []
            up = sum(1 for s in subs if s.get("ok"))
            down = [s.get("name") for s in subs if not s.get("ok")]
            line = (
                f"{host}: {'OK' if ok else 'DEGRADED'} ({up}/{len(subs)} subsystems)."
            )
            if down:
                line += f" Down: {', '.join(down[:5])}."
            return {"ok": ok, "summary": line, "elapsed_ms": elapsed_ms}

        return {**data, "elapsed_ms_round_trip": elapsed_ms,
                "ok": data.get("ok", True) if action == "snapshot"
                else data.get("ok", False)}


tool = WorkerHealthTool()
