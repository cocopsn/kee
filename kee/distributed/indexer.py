"""Vault indexer.

Watches the Obsidian vault for `.md` changes, chunks each file, embeds the
chunks, and pushes them to ChromaDB. Provides a `query()` for semantic
retrieval that the `MemoryManager` delegates to.

Designed to fail gracefully:

  * If the embedder can't reach any Ollama → indexing skipped, logged once.
  * If ChromaDB is offline → indexing skipped, logged once.
  * The agent keeps working in both cases; semantic memory just returns
    empty and the LLM falls back to identity + system prompt + recency.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path

from kee.config import settings
from kee.distributed.chroma_client import ChromaClient, ChromaUnavailable
from kee.distributed.embedder import Embedder, EmbedderUnavailable

logger = logging.getLogger(__name__)


VAULT_COLLECTION = "vault"
CHUNK_SIZE = 800        # characters
CHUNK_OVERLAP = 200     # characters

# Skip noisy paths.
_SKIP_DIRS = {".obsidian", "logs", "_kee/tools/__pycache__"}


@dataclass
class Chunk:
    id: str
    text: str
    metadata: dict


def _chunk_text(text: str, size: int = CHUNK_SIZE, overlap: int = CHUNK_OVERLAP) -> list[str]:
    """Sliding-window character chunker that prefers paragraph boundaries."""
    text = text.strip()
    if len(text) <= size:
        return [text] if text else []

    # Split on double newlines first; coalesce small paragraphs.
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0

    for p in paragraphs:
        if buf_len + len(p) + 2 <= size:
            buf.append(p)
            buf_len += len(p) + 2
        else:
            if buf:
                chunks.append("\n\n".join(buf))
            if len(p) <= size:
                buf = [p]
                buf_len = len(p)
            else:
                # Long paragraph — fall back to character window.
                start = 0
                while start < len(p):
                    chunks.append(p[start : start + size])
                    start += size - overlap
                buf, buf_len = [], 0

    if buf:
        chunks.append("\n\n".join(buf))
    return chunks


def _file_id(path: Path, idx: int) -> str:
    h = hashlib.md5(str(path).encode("utf-8")).hexdigest()[:12]
    return f"{h}-{idx}"


def _should_skip(path: Path, root: Path) -> bool:
    rel = path.relative_to(root)
    parts = set(rel.parts)
    return any(skip in str(rel) for skip in _SKIP_DIRS) or rel.suffix.lower() != ".md"


class VaultIndexer:
    def __init__(
        self,
        embedder: Embedder | None = None,
        chroma: ChromaClient | None = None,
        vault_root: Path | None = None,
    ) -> None:
        self.embedder = embedder or Embedder()
        self.chroma = chroma or ChromaClient()
        self.vault_root = vault_root or settings.vault_dir
        self._collection_ready = False
        self._warned_offline = False
        self._offline_until: float = 0.0  # unix ts; set when chroma fails

    # ── Lifecycle ─────────────────────────────────────────────────────────
    async def ensure_collection(self) -> bool:
        if self._collection_ready:
            return True
        # Cache offline state for 60s so a single voice turn doesn't pay the
        # network timeout on every retrieve attempt.
        import time as _t
        if self._offline_until and _t.time() < self._offline_until:
            return False
        try:
            await self.chroma.get_or_create_collection(
                VAULT_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
        except ChromaUnavailable as e:
            if not self._warned_offline:
                logger.info("ChromaDB offline (%s) — indexing disabled (cached 60s).", e)
                self._warned_offline = True
            self._offline_until = _t.time() + 60
            return False
        self._collection_ready = True
        self._offline_until = 0
        return True

    # ── Single file ───────────────────────────────────────────────────────
    async def index_file(self, path: Path | str) -> dict:
        path = Path(path)
        if not path.exists() or _should_skip(path, self.vault_root):
            return {"status": "skipped", "path": str(path)}

        if not await self.ensure_collection():
            return {"status": "skipped_offline", "path": str(path)}

        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return {"status": "read_error", "path": str(path), "error": str(e)}

        chunks = _chunk_text(text)
        if not chunks:
            return {"status": "empty", "path": str(path)}

        try:
            embeddings = await self.embedder.embed(chunks)
        except EmbedderUnavailable as e:
            if not self._warned_offline:
                logger.info("Embedder offline (%s) — indexing disabled.", e)
                self._warned_offline = True
            return {"status": "skipped_offline", "path": str(path)}

        rel = str(path.relative_to(self.vault_root))
        ids = [_file_id(path, i) for i in range(len(chunks))]
        metadatas = [
            {"path": rel, "chunk_index": i, "indexed_at": time.time()}
            for i in range(len(chunks))
        ]

        try:
            await self.chroma.upsert(VAULT_COLLECTION, ids, chunks, embeddings, metadatas)
        except ChromaUnavailable as e:
            return {"status": "chroma_error", "path": str(path), "error": str(e)}

        return {"status": "indexed", "path": str(path), "chunks": len(chunks)}

    # ── Whole vault ───────────────────────────────────────────────────────
    async def index_vault(self) -> dict[str, int]:
        if not await self.ensure_collection():
            return {"indexed": 0, "skipped": 0, "offline": True}

        indexed = 0
        skipped = 0
        for md in self.vault_root.rglob("*.md"):
            result = await self.index_file(md)
            if result["status"] == "indexed":
                indexed += 1
            else:
                skipped += 1
        return {"indexed": indexed, "skipped": skipped, "offline": False}

    # ── Query ─────────────────────────────────────────────────────────────
    async def query(self, text: str, top_k: int = 5) -> list[dict]:
        if not await self.ensure_collection():
            return []
        try:
            [embedding] = await self.embedder.embed([text])
            result = await self.chroma.query(
                VAULT_COLLECTION, [embedding], n_results=top_k
            )
        except (EmbedderUnavailable, ChromaUnavailable):
            return []

        documents = (result.get("documents") or [[]])[0]
        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]
        out = []
        for i, doc in enumerate(documents):
            out.append({
                "text": doc,
                "metadata": metadatas[i] if i < len(metadatas) else {},
                "distance": distances[i] if i < len(distances) else None,
            })
        return out
