"""End-to-end RAG against the real Auctorum worker.

**SKIPPED unless `KEE_TEST_REAL_RAG=1` is set** (and the worker is
reachable). This is the integration test you run on the dev box where
the Tailscale tailnet has the worker live.

Verifies:
  - Embedder hits the remote Ollama (returns 768-dim vector)
  - ChromaClient resolves collection name to UUID
  - Vault `vault` collection is non-empty
  - Surgical RAG (vector + rerank + compress) returns a sensible
    snippet for a known query

Run::

    KEE_TEST_REAL_RAG=1 .venv\\Scripts\\python.exe tests/test_real_rag.py
"""

from __future__ import annotations

import asyncio
import os
import sys


_ENABLED = os.environ.get("KEE_TEST_REAL_RAG", "0") in ("1", "true", "yes")


def _skip(label: str) -> int:
    print(f"  [SKIP] {label} (set KEE_TEST_REAL_RAG=1 to enable)")
    return 0


def test_worker_reachable() -> int:
    if not _ENABLED:
        return _skip("worker probe")
    import httpx
    host = os.environ.get(
        "KEE_WORKER_HEALTH_URL",
        f"http://{os.environ.get('AUCTORUM_HOST','auctorum')}:8080",
    )
    try:
        r = httpx.get(f"{host}/health", timeout=4.0)
        if r.status_code != 200:
            print(f"  [FAIL] worker {host} returned {r.status_code}")
            return 1
    except Exception as e:
        print(f"  [FAIL] worker {host} unreachable: {e}")
        return 1
    body = r.json()
    if not body.get("ok"):
        print(f"  [FAIL] worker degraded: {body}")
        return 1
    print(f"  [ok] worker OK ({len(body.get('subsystems', []))} subsystems)")
    return 0


def test_embedder_returns_768_dim() -> int:
    if not _ENABLED:
        return _skip("embedder dim")
    from kee.distributed.embedder import Embedder
    emb = Embedder()
    vecs = asyncio.run(emb.embed(["AUCTORUM is an AI agency"]))
    if len(vecs) == 1 and len(vecs[0]) == 768:
        print(f"  [ok] embedder returns 768-dim vector")
        return 0
    print(f"  [FAIL] expected 1 vector of 768 dims, got "
          f"{len(vecs)} vectors of dim {len(vecs[0]) if vecs else 0}")
    return 1


def test_chroma_collection_resolves_uuid() -> int:
    if not _ENABLED:
        return _skip("chroma uuid")
    from kee.distributed.chroma_client import ChromaClient
    c = ChromaClient()
    uuid = asyncio.run(c._coll_uuid("vault"))
    # uuid format: 8-4-4-4-12 hex chars
    import re
    if re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                uuid):
        print(f"  [ok] chroma resolved 'vault' -> {uuid}")
        return 0
    print(f"  [FAIL] not a uuid: {uuid!r}")
    return 1


def test_surgical_rag_finds_identity() -> int:
    if not _ENABLED:
        return _skip("surgical RAG")
    from kee.core.memory import MemoryManager
    from kee.distributed.indexer import VaultIndexer
    mm = MemoryManager()
    mm.indexer = VaultIndexer()
    out = asyncio.run(mm.retrieve("kee identity sovereignty", top_k=2))
    if isinstance(out, str) and "identity" in out.lower():
        print(f"  [ok] surgical RAG returns identity-shaped result "
              f"({len(out)} chars)")
        return 0
    print(f"  [FAIL] {out!r}")
    return 1


if __name__ == "__main__":
    if not _ENABLED:
        print("=== real RAG (DISABLED — set KEE_TEST_REAL_RAG=1) ===")
    else:
        print("=== real RAG against Auctorum worker ===")
    fails = 0
    fails += test_worker_reachable()
    fails += test_embedder_returns_768_dim()
    fails += test_chroma_collection_resolves_uuid()
    fails += test_surgical_rag_finds_identity()
    print()
    print(f"Done. failures={fails}")
    sys.exit(0 if fails == 0 else 1)
