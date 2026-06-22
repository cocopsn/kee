"""Multi-node fleet manager.

Phase 6 close. Kee is designed dual-node: this Alienware (primary) + the
Auctorum PC (worker, where ChromaDB / reranker / Gemma vision live). The
fleet manager:

* Reads node config from ``vault/config/fleet.json`` (or env).
* Probes each node: ping, Ollama health, optional WoL MAC.
* Returns a status snapshot the dashboard renders as a fleet strip.

It deliberately does *not* depend on Tailscale's CLI — Coco may run plain
LAN, Tailscale, or Headscale. Each node is just a hostname/IP + a few
service URLs, so all of those work transparently.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import socket
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import httpx

from kee.config import settings

logger = logging.getLogger(__name__)


# ── Node config ──────────────────────────────────────────────────────────
@dataclass
class FleetNode:
    name: str                             # e.g. "alienware", "auctorum"
    role: str                             # "primary" | "worker"
    host: str                             # hostname or IP for ping
    ollama_url: Optional[str] = None      # http://host:11434
    chroma_url: Optional[str] = None      # http://host:8000
    reranker_url: Optional[str] = None    # http://host:8002/rerank
    vision_url: Optional[str] = None      # http://host:8003
    health_url: Optional[str] = None      # http://host:8080/health
    api_url: Optional[str] = None         # http://host:7330
    mac: Optional[str] = None             # for WoL
    notes: str = ""


def fleet_config_path() -> Path:
    return settings.vault_dir / "config" / "fleet.json"


def _default_fleet() -> list[FleetNode]:
    """The dev-box default: this Alienware as primary, Auctorum from env
    if reachable. Persisted on first read so Coco can edit it."""
    nodes = [
        FleetNode(
            name=os.environ.get("KEE_NODE_NAME", socket.gethostname()),
            role="primary",
            host="127.0.0.1",
            ollama_url=settings.ollama_host,
            api_url="http://127.0.0.1:7330",
            notes="this machine — Alienware (RTX 5050, primary).",
        ),
    ]
    auctorum_host = settings.auctorum_host
    if auctorum_host:
        nodes.append(FleetNode(
            name="auctorum",
            role="worker",
            host=auctorum_host,
            ollama_url=settings.auctorum_ollama,
            chroma_url=settings.chromadb_host,
            mac=os.environ.get("KEE_WORKER_MAC"),
            notes="Auctorum PC — ChromaDB / reranker / Gemma vision.",
        ))
    return nodes


def load_fleet() -> list[FleetNode]:
    """Read ``vault/config/fleet.json`` or seed it with defaults."""
    p = fleet_config_path()
    if not p.exists():
        defaults = _default_fleet()
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                json.dumps([asdict(n) for n in defaults], indent=2),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("fleet seed failed: %s", e)
        return defaults
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("fleet config corrupt — using defaults: %s", e)
        return _default_fleet()
    out: list[FleetNode] = []
    fields = set(FleetNode.__dataclass_fields__)
    for entry in raw:
        cleaned = {k: v for k, v in entry.items() if k in fields}
        out.append(FleetNode(**cleaned))
    return out


def save_fleet(nodes: list[FleetNode]) -> None:
    p = fleet_config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps([asdict(n) for n in nodes], indent=2), encoding="utf-8")
    tmp.replace(p)


# ── Per-node probing ─────────────────────────────────────────────────────
async def _ping(host: str, port: int = None, timeout: float = 1.5) -> tuple[bool, float]:
    """TCP-ping a host:port. Returns (alive, latency_ms). Falls back to
    name resolution + dummy port if no port given (dns_only check)."""
    t0 = time.monotonic()
    if port is None:
        try:
            await asyncio.get_running_loop().run_in_executor(
                None, socket.gethostbyname, host,
            )
            return True, round((time.monotonic() - t0) * 1000, 1)
        except Exception:
            return False, 0.0
    try:
        fut = asyncio.open_connection(host, port)
        reader, writer = await asyncio.wait_for(fut, timeout=timeout)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        return True, round((time.monotonic() - t0) * 1000, 1)
    except Exception:
        return False, 0.0


def _url_to_host_port(url: str) -> tuple[str, int]:
    from urllib.parse import urlparse
    u = urlparse(url)
    port = u.port or (443 if u.scheme == "https" else 80)
    return u.hostname or "", port


async def _http_health(url: str, path: str = "/", timeout: float = 2.0) -> dict:
    """GET url+path, return ok flag + status code + latency."""
    full = url.rstrip("/") + path
    t0 = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            resp = await c.get(full)
            return {
                "ok": resp.status_code < 500,
                "status": resp.status_code,
                "latency_ms": round((time.monotonic() - t0) * 1000, 1),
            }
    except Exception as e:
        return {"ok": False, "error": type(e).__name__, "latency_ms": 0.0}


async def probe_node(node: FleetNode) -> dict:
    """Probe one node's reachability + each declared service."""
    out: dict = {
        "name": node.name,
        "role": node.role,
        "host": node.host,
        "is_self": node.host in ("127.0.0.1", "localhost", socket.gethostname()),
        "notes": node.notes,
        "mac": node.mac,
    }

    # Quick TCP ping on a likely-open port (Ollama or API).
    ping_targets: list[tuple[str, int]] = []
    if node.ollama_url:
        ping_targets.append(_url_to_host_port(node.ollama_url))
    if node.api_url:
        ping_targets.append(_url_to_host_port(node.api_url))
    if not ping_targets:
        ping_targets.append((node.host, 22))         # try SSH as a last resort
    alive_any = False
    best_latency = None
    for host, port in ping_targets:
        ok, lat = await _ping(host, port)
        if ok:
            alive_any = True
            best_latency = lat if best_latency is None else min(best_latency, lat)
            break
    out["alive"] = alive_any
    out["ping_ms"] = best_latency

    # Service checks (skip if unreachable to avoid 30s hang on dead worker).
    services: dict[str, dict] = {}
    if alive_any:
        if node.ollama_url:
            services["ollama"] = await _http_health(node.ollama_url, "/api/tags")
        if node.chroma_url:
            services["chroma"] = await _http_health(node.chroma_url, "/api/v2/heartbeat")
        if node.reranker_url:
            # Reranker URL has /rerank as path; probe /health on the same host
            base = node.reranker_url.rsplit("/", 1)[0] if node.reranker_url.endswith("/rerank") else node.reranker_url
            services["reranker"] = await _http_health(base, "/health")
        if node.vision_url:
            services["vision"] = await _http_health(node.vision_url, "/health")
        if node.health_url:
            # health_url already includes /health in the path — strip and re-add
            base = node.health_url.rsplit("/health", 1)[0] if node.health_url.endswith("/health") else node.health_url
            services["health"] = await _http_health(base, "/health")
        if node.api_url and not out["is_self"]:
            services["api"] = await _http_health(node.api_url, "/health")
    out["services"] = services
    return out


async def probe_fleet() -> dict:
    """Probe every configured node concurrently."""
    nodes = load_fleet()
    results = await asyncio.gather(*[probe_node(n) for n in nodes])
    alive = sum(1 for r in results if r["alive"])
    return {
        "updated_at": time.time(),
        "node_count": len(nodes),
        "alive_count": alive,
        "nodes": results,
    }


# ── Helpers exposed to other modules ─────────────────────────────────────
def find_node(name: str) -> Optional[FleetNode]:
    for n in load_fleet():
        if n.name.lower() == name.lower():
            return n
    return None
