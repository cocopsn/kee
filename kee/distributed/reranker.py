"""Reranker — Phase 3 surgical-RAG precision filter.

Vector search (ChromaDB + nomic-embed) gives broad recall; the reranker
turns that into precision. We score each candidate against the query with
a cross-encoder (`bge-reranker-base` via `flashrank` — ONNX, no torch
dependency, ~280 MB model downloaded on first use, ~50 ms per pair on CPU).

Two backends:
  * **Local** (default) — `flashrank.Ranker` running in-process.
  * **Remote** — when `KEE_RERANKER_URL` is set, POST `{query, documents}`
    to that URL and expect `[{score, document}, ...]`. The Auctorum PC
    runs this in production so the i3-7100 doesn't bottleneck on every
    user turn.

Falls back gracefully: if flashrank is missing or model download fails,
`rerank()` returns the input order unchanged. Surgical RAG keeps working
with vector ranking only.
"""

from __future__ import annotations

import asyncio
import logging
import os
from typing import Any, Sequence

import httpx

logger = logging.getLogger(__name__)


_LOCAL_MODEL_NAME = os.environ.get("KEE_RERANKER_MODEL", "ms-marco-MiniLM-L-12-v2")
# Note: flashrank's bundled `bge-reranker-base` ID is "rank-BGE-base".
# `ms-marco-MiniLM-L-12-v2` is smaller (~33MB) and faster, with slightly
# lower quality — a better default for the 8GB-VRAM dev box. Override
# via KEE_RERANKER_MODEL if you want bge-base or bge-large.

_REMOTE_URL = os.environ.get("KEE_RERANKER_URL")  # e.g. "http://auctorum:8001/rerank"


class Reranker:
    """Singleton-ish reranker. Lazy-loads the local model on first call."""

    _local: Any = None  # flashrank.Ranker | None
    _local_failed: bool = False

    @classmethod
    def _get_local(cls) -> Any | None:
        if cls._local_failed:
            return None
        if cls._local is not None:
            return cls._local
        try:
            from flashrank import Ranker  # type: ignore[import-not-found]
        except ImportError:
            logger.warning("flashrank not installed — reranker disabled.")
            cls._local_failed = True
            return None
        try:
            cls._local = Ranker(model_name=_LOCAL_MODEL_NAME)
        except Exception as e:
            logger.warning("flashrank model load failed (%s) — reranker disabled.", e)
            cls._local_failed = True
            return None
        return cls._local

    @classmethod
    async def rerank(
        cls,
        query: str,
        documents: Sequence[str],
        top_k: int | None = None,
    ) -> list[tuple[str, float]]:
        """Return `[(document, score), ...]` sorted descending by relevance.

        On any error returns the input documents in original order with
        score=0.0 — callers can keep going.
        """
        if not documents:
            return []
        top_k = top_k or len(documents)

        # 1. Try the remote backend first if configured.
        if _REMOTE_URL:
            try:
                async with httpx.AsyncClient(timeout=8.0) as client:
                    r = await client.post(
                        _REMOTE_URL,
                        json={"query": query, "documents": list(documents)},
                    )
                if r.status_code == 200:
                    raw = r.json()
                    # Tolerate both shapes:
                    #   array of {document, score, index?}     (legacy)
                    #   {results: [...], query, model, …}      (current Auctorum server)
                    items = raw if isinstance(raw, list) else (raw or {}).get("results", [])
                    pairs = [
                        (item.get("document") or item.get("text") or "",
                         float(item.get("score", 0.0)))
                        for item in items
                        if isinstance(item, dict)
                    ]
                    pairs = [(d, s) for d, s in pairs if d]
                    pairs.sort(key=lambda p: p[1], reverse=True)
                    return pairs[:top_k]
                logger.warning("remote reranker %s returned %d", _REMOTE_URL, r.status_code)
            except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError) as e:
                logger.debug("remote reranker unreachable (%s) — falling back to local", e)

        # 2. Local flashrank.
        ranker = cls._get_local()
        if ranker is None:
            return [(d, 0.0) for d in documents][:top_k]

        loop = asyncio.get_running_loop()
        # flashrank's Ranker is synchronous + CPU-bound; punt to a thread.
        def _do_rank() -> list[tuple[str, float]]:
            passages = [{"id": i, "text": doc} for i, doc in enumerate(documents)]
            from flashrank import RerankRequest  # type: ignore[import-not-found]
            results = ranker.rerank(RerankRequest(query=query, passages=passages))
            return [(r["text"], float(r.get("score", 0.0))) for r in results]

        try:
            scored = await loop.run_in_executor(None, _do_rank)
        except Exception as e:
            logger.warning("flashrank rerank raised: %s — returning original order", e)
            return [(d, 0.0) for d in documents][:top_k]
        return scored[:top_k]


reranker = Reranker()
