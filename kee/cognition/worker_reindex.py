"""Worker reindex — Sleep Cycle Phase 12.

Decides whether to kick a full vault re-index against ChromaDB:

  - If the worker is unreachable → skip (nothing to index against).
  - If no .md file in vault has changed since the last successful run
    → skip (nothing new to index).
  - Otherwise → run `VaultIndexer.index_vault()` and persist the marker.

The marker lives in `data/worker_reindex_state.json` so it survives
across Sleep Cycle runs without burning a SQLite migration on something
this lightweight.

No LLM cost — embeddings hit Ollama on the worker (free), ChromaDB is
local to the worker.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any

import httpx

from kee.config import settings

logger = logging.getLogger(__name__)


_STATE_PATH = settings.data_dir / "worker_reindex_state.json"


def _load_state() -> dict[str, Any]:
    if not _STATE_PATH.exists():
        return {"last_run_ts": 0, "last_indexed": 0}
    try:
        return json.loads(_STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"last_run_ts": 0, "last_indexed": 0}


def _save_state(state: dict[str, Any]) -> None:
    try:
        _STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _STATE_PATH.write_text(json.dumps(state, indent=2),
                               encoding="utf-8")
    except Exception as e:
        logger.warning("worker_reindex state save failed: %s", e)


async def _worker_is_alive(timeout_s: float = 4.0) -> bool:
    """Probe the health aggregator. False if unreachable / degraded."""
    import os
    url = os.environ.get(
        "KEE_WORKER_HEALTH_URL",
        f"http://{os.environ.get('AUCTORUM_HOST','auctorum')}:8080",
    ) + "/health"
    try:
        async with httpx.AsyncClient(timeout=timeout_s) as client:
            r = await client.get(url)
        if r.status_code != 200:
            return False
        return bool(r.json().get("ok"))
    except Exception:
        return False


def _vault_has_changed_since(ts: float) -> tuple[bool, int]:
    """Return (any_newer, newest_mtime). `ts` is unix timestamp."""
    newest = 0.0
    has_newer = False
    for md in settings.vault_dir.rglob("*.md"):
        try:
            m = md.stat().st_mtime
        except OSError:
            continue
        if m > newest:
            newest = m
        if m > ts:
            has_newer = True
    return has_newer, int(newest)


async def maybe_reindex(force: bool = False) -> dict[str, Any]:
    """Decide + execute. Returns a stat dict for Sleep Cycle's report."""
    state = _load_state()
    last_run = float(state.get("last_run_ts", 0))

    if not await _worker_is_alive():
        return {"ran": False, "reason": "worker offline",
                "last_run_ts": last_run}

    has_newer, newest = _vault_has_changed_since(last_run)
    if not force and not has_newer:
        return {"ran": False, "reason": "no vault changes since last run",
                "last_run_ts": last_run, "newest_mtime": newest}

    t0 = time.time()
    try:
        from kee.distributed.indexer import VaultIndexer
        idx = VaultIndexer()
        # Force a fresh ensure_collection by ignoring the 60s offline cache.
        idx._offline_until = 0
        idx._collection_ready = False
        result = await idx.index_vault()
    except Exception as e:
        return {"ran": False, "reason": f"indexer error: {e}",
                "last_run_ts": last_run}

    elapsed_s = round(time.time() - t0, 1)
    new_state = {
        "last_run_ts": time.time(),
        "last_indexed": result.get("indexed", 0),
        "last_skipped": result.get("skipped", 0),
        "last_elapsed_s": elapsed_s,
        "newest_mtime": newest,
    }
    _save_state(new_state)
    return {
        "ran": True,
        "indexed": result.get("indexed", 0),
        "skipped": result.get("skipped", 0),
        "elapsed_s": elapsed_s,
    }
