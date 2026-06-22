"""Auctorum health aggregator.

Single endpoint Kee can poll from Alienware to see if the worker stack
is healthy. Bound to 0.0.0.0:8080.

  GET /health      → JSON snapshot of every subsystem
  GET /health/<k>  → just one subsystem (k ∈ chroma | ollama | reranker | gpu | disk)

Deploy:

    sudo mkdir -p /opt/keehealth && sudo chown $USER /opt/keehealth
    python3.12 -m venv /opt/keehealth/venv
    source /opt/keehealth/venv/bin/activate
    pip install fastapi uvicorn[standard] httpx psutil

Save this file to /opt/keehealth/server.py, then systemd unit
/etc/systemd/system/keehealth.service:

    [Unit]
    After=network.target chromadb.service ollama.service reranker.service

    [Service]
    Type=simple
    ExecStart=/opt/keehealth/venv/bin/python /opt/keehealth/server.py
    Restart=always
    User=<your-user>
    Environment=PYTHONUNBUFFERED=1

    [Install]
    WantedBy=multi-user.target
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess
import time

import httpx
from fastapi import FastAPI, HTTPException
import uvicorn

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("health")


CHROMA_URL = os.environ.get("CHROMA_URL", "http://127.0.0.1:8000")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://127.0.0.1:11434")
RERANKER_URL = os.environ.get("RERANKER_URL", "http://127.0.0.1:8002")
VISION_URL = os.environ.get("VISION_URL", "http://127.0.0.1:8003")
TIMEOUT = 3.0


app = FastAPI(title="Kee worker health", version="1.0.0")


async def _probe_http(name: str, url: str, path: str = "/") -> dict:
    t0 = time.time()
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT) as client:
            r = await client.get(f"{url}{path}")
        elapsed = int((time.time() - t0) * 1000)
        return {
            "name": name, "ok": 200 <= r.status_code < 300,
            "status_code": r.status_code, "elapsed_ms": elapsed,
            "url": url + path,
        }
    except Exception as e:
        return {
            "name": name, "ok": False, "error": str(e)[:120],
            "elapsed_ms": int((time.time() - t0) * 1000),
            "url": url + path,
        }


def _probe_gpu() -> dict:
    """nvidia-smi snapshot, no GPU = ok=false but doesn't error."""
    if not shutil.which("nvidia-smi"):
        return {"name": "gpu", "ok": False, "error": "no nvidia-smi"}
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=name,memory.used,memory.total,utilization.gpu",
             "--format=csv,noheader,nounits"],
            text=True, timeout=2,
        )
        parts = [p.strip() for p in out.strip().split(",")]
        return {
            "name": "gpu", "ok": True,
            "model": parts[0],
            "mem_used_mb": int(parts[1]),
            "mem_total_mb": int(parts[2]),
            "util_pct": int(parts[3]),
            "mem_used_pct": round(int(parts[1]) / int(parts[2]) * 100, 1),
        }
    except Exception as e:
        return {"name": "gpu", "ok": False, "error": str(e)[:120]}


def _probe_disk() -> dict:
    try:
        import psutil
        d = psutil.disk_usage("/")
        return {
            "name": "disk", "ok": d.percent < 90,
            "percent": d.percent,
            "free_gb": round(d.free / 1e9, 1),
            "total_gb": round(d.total / 1e9, 1),
        }
    except Exception as e:
        return {"name": "disk", "ok": False, "error": str(e)[:120]}


def _probe_load() -> dict:
    try:
        import psutil
        cpu = psutil.cpu_percent(interval=0.2)
        mem = psutil.virtual_memory()
        return {
            "name": "load", "ok": cpu < 95 and mem.percent < 95,
            "cpu_pct": cpu, "ram_pct": mem.percent,
            "ram_used_gb": round(mem.used / 1e9, 1),
            "ram_total_gb": round(mem.total / 1e9, 1),
        }
    except Exception as e:
        return {"name": "load", "ok": False, "error": str(e)[:120]}


@app.get("/health")
async def health() -> dict:
    chroma, ollama, reranker, vision = await asyncio.gather(
        _probe_http("chroma", CHROMA_URL, "/api/v1/heartbeat"),
        _probe_http("ollama", OLLAMA_URL, "/api/tags"),
        _probe_http("reranker", RERANKER_URL, "/health"),
        _probe_http("vision", VISION_URL, "/health"),
    )
    gpu = _probe_gpu()
    disk = _probe_disk()
    load = _probe_load()
    subsystems = [chroma, ollama, reranker, vision, gpu, disk, load]
    overall_ok = all(s.get("ok") for s in (chroma, ollama, disk))
    return {
        "host": os.uname().nodename if hasattr(os, "uname") else "auctorum",
        "ts": int(time.time()),
        "ok": overall_ok,
        "subsystems": subsystems,
    }


@app.get("/health/{name}")
async def health_one(name: str) -> dict:
    snap = await health()
    for s in snap["subsystems"]:
        if s.get("name") == name:
            return s
    raise HTTPException(404, f"unknown subsystem {name!r}")


if __name__ == "__main__":
    port = int(os.environ.get("HEALTH_PORT", "8080"))
    log.info("Starting worker health aggregator on 0.0.0.0:%d", port)
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")
