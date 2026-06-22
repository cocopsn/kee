"""Embedding service.

Wraps `POST /api/embed` on either the local Ollama or the Auctorum worker.
Default model is `nomic-embed-text` (lightweight, ~270MB, 768-dim).

The embedder is *aware* of where it's running. Strategy:

  * If `KEE_EMBED_HOST` is set, use that.
  * Else if Auctorum is reachable, prefer it (frees the primary GPU for the
    main LLM).
  * Else fall back to the local Ollama.

A single instance auto-selects on first call and caches the host.
"""

from __future__ import annotations

import logging
import os
from typing import Sequence

import httpx

from kee.config import settings

logger = logging.getLogger(__name__)


DEFAULT_EMBED_MODEL = os.environ.get("KEE_EMBED_MODEL", "nomic-embed-text")


class EmbedderUnavailable(RuntimeError):
    """No reachable Ollama instance, or the embedding model is missing."""


class Embedder:
    def __init__(self, model: str = DEFAULT_EMBED_MODEL) -> None:
        self.model = model
        self._host: str | None = os.environ.get("KEE_EMBED_HOST")

    async def host(self) -> str:
        """Resolve the host to use. Cached for the lifetime of the embedder."""
        if self._host:
            return self._host

        candidates = [settings.auctorum_ollama, settings.ollama_host]
        for url in candidates:
            if not url:
                continue
            if await self._probe(url):
                self._host = url
                logger.info("Embedder using %s", url)
                return url

        raise EmbedderUnavailable(
            f"No Ollama instance reachable for embeddings. Tried: {candidates}"
        )

    @staticmethod
    async def _probe(url: str) -> bool:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{url}/api/tags")
                return r.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException, httpx.ReadError):
            return False

    async def embed(self, texts: Sequence[str]) -> list[list[float]]:
        """Return one embedding vector per input text. Order is preserved.

        Bulletproof: if the cached host fails (e.g. on a network where
        Auctorum is unreachable mid-session), we drop the cache, re-probe
        candidates, and retry once. This is what makes the agent survive
        the Tec wifi blocking outbound to the worker.
        """
        if not texts:
            return []
        for attempt in (1, 2):
            host = await self.host()
            try:
                async with httpx.AsyncClient(timeout=60.0) as client:
                    r = await client.post(
                        f"{host}/api/embed",
                        json={"model": self.model, "input": list(texts)},
                    )
            except httpx.HTTPError as e:
                if attempt == 1:
                    logger.warning(
                        "Embedder host %s unreachable (%s) — invalidating "
                        "cache and re-probing", host, e,
                    )
                    self._host = None  # force re-probe
                    continue
                raise EmbedderUnavailable(f"Embed call failed: {e}") from e

            if r.status_code != 200:
                if attempt == 1 and r.status_code >= 500:
                    self._host = None
                    continue
                raise EmbedderUnavailable(
                    f"Embed returned {r.status_code}: {r.text[:200]}"
                )
            data = r.json()
            return data.get("embeddings", [])
        return []  # unreachable, satisfies the type checker

    async def health(self) -> dict:
        try:
            host = await self.host()
        except EmbedderUnavailable as e:
            return {"ok": False, "host": None, "error": str(e)}
        return {"ok": True, "host": host, "model": self.model}
