"""Episodic memory — semantic recall over EVERYTHING that happened.

Aggregates the historical signals scattered across SQLite tables into a
single ChromaDB collection (`episodic`), embeds them via the worker, and
exposes them through the same surgical RAG pipeline `MemoryManager` uses
for vault notes.

What gets indexed:

  - **conversations**: per-conv summary (what we talked about + outcome)
  - **dispatches**: project-level breadcrumbs ("worked on auctorum: …")
  - **plan_history**: each plan's task + selected approach
  - **focus_sessions**: closed sessions with outcome
  - **learnings**: explicit pinned knowledge nuggets (high reinforce → boost)
  - **notifications**: outbound notifications worth recalling later
  - **conversation_qa**: low-quality replies (so we can recall and avoid)
  - **perception_events** (optional): screenshot descriptions (when
    `passive_perception` heartbeat check is feeding them)

Each indexed event becomes a doc with metadata `{kind, ref, ts, source}`
so a recall query like "what were we doing about auctorum stripe last
week" can return: a focus session + 3 dispatches + 5 plan tasks +
2 learnings, all ranked by semantic similarity.

Idempotent: each event has a stable `id` (e.g. `dispatch:42`,
`learning:7`); upsert means re-running just refreshes any changed
content.

Skips silently when the worker is offline — the sleep_cycle phase that
calls this won't crash, just won't update.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Iterable

from kee.core import db
from kee.distributed.chroma_client import ChromaClient, ChromaUnavailable
from kee.distributed.embedder import Embedder, EmbedderUnavailable

logger = logging.getLogger(__name__)


EPISODIC_COLLECTION = "episodic"


# ── Source extractors ────────────────────────────────────────────────────
def _conversations(window_days: int) -> list[tuple[str, str, dict]]:
    """Return [(id, text, metadata)] for each conversation summary."""
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, summary, last_active, source FROM conversations "
            "WHERE summary IS NOT NULL AND summary != '' "
            "AND last_active >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
        for r in rows:
            out.append((
                f"conversation:{r[0]}",
                f"Conversation summary: {r[1]}",
                {"kind": "conversation", "ref": r[0],
                 "ts": str(r[2]), "source": r[3] or "?"},
            ))
    except Exception as e:
        logger.debug("conversations source failed: %s", e)
    return out


def _dispatches(window_days: int) -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, project, kind, summary, timestamp FROM dispatches "
            "WHERE timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
        for r in rows:
            text = (
                f"Dispatch [{r[2]}] on project {r[1]!r}: "
                f"{r[3] or '(no summary)'}"
            )
            out.append((
                f"dispatch:{r[0]}", text,
                {"kind": "dispatch", "ref": r[0],
                 "ts": str(r[4]), "project": r[1] or "?"},
            ))
    except Exception as e:
        logger.debug("dispatches source failed: %s", e)
    return out


def _plans(window_days: int) -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, task, selected_json, executed, outcome, timestamp "
            "FROM plan_history "
            "WHERE timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
        import json as _json
        for r in rows:
            try:
                sel = _json.loads(r[2] or "{}")
                approach = sel.get("name", "?")
            except Exception:
                approach = "?"
            tag = "[executed]" if r[3] else "[pending]"
            text = (
                f"Plan {tag} for task {r[1]!r}: chose {approach!r}. "
                f"Outcome: {r[4] or '(none yet)'}"
            )
            out.append((
                f"plan:{r[0]}", text,
                {"kind": "plan", "ref": r[0], "ts": str(r[5]),
                 "executed": bool(r[3])},
            ))
    except Exception as e:
        logger.debug("plans source failed: %s", e)
    return out


def _focus_sessions(window_days: int) -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, project, intent, ended_at, outcome, drift_count "
            "FROM focus_sessions "
            "WHERE ended_at IS NOT NULL "
            "AND ended_at >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
        for r in rows:
            text = (
                f"Focus session on project {r[1]!r}: intent {r[2] or '?'}. "
                f"Outcome: {r[4] or 'unspecified'}. "
                f"Drift count: {r[5]}."
            )
            out.append((
                f"focus:{r[0]}", text,
                {"kind": "focus", "ref": r[0],
                 "ts": str(r[3]), "project": r[1] or "?"},
            ))
    except Exception as e:
        logger.debug("focus source failed: %s", e)
    return out


def _learnings(window_days: int) -> list[tuple[str, str, dict]]:
    """All non-forgotten learnings, regardless of window — they're
    explicitly pinned so they should always be recallable."""
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, topic, content, reinforced, timestamp FROM learnings "
            "WHERE forgotten = 0",
        ).fetchall()
        for r in rows:
            text = f"Learning [{r[1]}] (reinforced ×{r[3]}): {r[2]}"
            out.append((
                f"learning:{r[0]}", text,
                {"kind": "learning", "ref": r[0],
                 "ts": str(r[4]), "reinforced": int(r[3])},
            ))
    except Exception as e:
        logger.debug("learnings source failed: %s", e)
    return out


def _notifications(window_days: int) -> list[tuple[str, str, dict]]:
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, source, title, body, urgency, timestamp "
            "FROM notifications "
            "WHERE timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
        for r in rows:
            text = f"Notification [{r[1]}] {r[2]}: {(r[3] or '')[:300]}"
            out.append((
                f"notif:{r[0]}", text,
                {"kind": "notification", "ref": r[0],
                 "ts": str(r[5]), "source": r[1] or "?",
                 "urgency": int(r[4] or 1)},
            ))
    except Exception as e:
        logger.debug("notifications source failed: %s", e)
    return out


def _perception_events(window_days: int) -> list[tuple[str, str, dict]]:
    """Optional. Reads `audit_log` rows with action='perception_screenshot'
    that the passive perception heartbeat check writes. Schema: parameters
    is JSON {window_title, description, image_path?}."""
    out: list[tuple[str, str, dict]] = []
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, parameters, timestamp FROM audit_log "
            "WHERE action='perception_screenshot' "
            "AND timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
        import json as _json
        for r in rows:
            try:
                p = _json.loads(r[1] or "{}")
            except Exception:
                continue
            desc = p.get("description", "").strip()
            win = p.get("window_title", "?")
            if not desc:
                continue
            text = f"Perception @ {win}: {desc[:400]}"
            out.append((
                f"perception:{r[0]}", text,
                {"kind": "perception", "ref": r[0],
                 "ts": str(r[2]), "window_title": win},
            ))
    except Exception as e:
        logger.debug("perception source failed: %s", e)
    return out


_SOURCES = [
    ("conversations", _conversations),
    ("dispatches", _dispatches),
    ("plans", _plans),
    ("focus_sessions", _focus_sessions),
    ("learnings", _learnings),
    ("notifications", _notifications),
    ("perception", _perception_events),
]


# ── Indexer ──────────────────────────────────────────────────────────────
class EpisodicIndexer:
    def __init__(self) -> None:
        self.embedder = Embedder()
        self.chroma = ChromaClient()
        self._collection_ready = False

    async def ensure_collection(self) -> bool:
        if self._collection_ready:
            return True
        try:
            await self.chroma.get_or_create_collection(
                EPISODIC_COLLECTION,
                metadata={"hnsw:space": "cosine"},
            )
            self._collection_ready = True
            return True
        except ChromaUnavailable as e:
            logger.info("episodic: chroma offline (%s)", e)
            return False

    async def index_window(self, window_days: int = 7) -> dict[str, Any]:
        """Index every event in the last `window_days`. Idempotent."""
        if not await self.ensure_collection():
            return {"indexed": 0, "by_source": {}, "offline": True}

        per_source: dict[str, int] = {}
        all_ids: list[str] = []
        all_texts: list[str] = []
        all_meta: list[dict] = []

        for name, fn in _SOURCES:
            rows = fn(window_days)
            per_source[name] = len(rows)
            for row_id, text, meta in rows:
                all_ids.append(row_id)
                all_texts.append(text)
                all_meta.append(meta)

        if not all_texts:
            return {"indexed": 0, "by_source": per_source, "offline": False}

        try:
            # Batch embed in groups of 32 — Ollama handles batches well.
            embeddings: list[list[float]] = []
            for i in range(0, len(all_texts), 32):
                batch = all_texts[i:i + 32]
                vecs = await self.embedder.embed(batch)
                embeddings.extend(vecs)
        except EmbedderUnavailable as e:
            logger.warning("episodic: embedder failed (%s)", e)
            return {"indexed": 0, "by_source": per_source,
                    "offline": True, "embedder_error": str(e)[:120]}

        try:
            await self.chroma.upsert(
                EPISODIC_COLLECTION,
                ids=all_ids, documents=all_texts,
                embeddings=embeddings, metadatas=all_meta,
            )
        except ChromaUnavailable as e:
            return {"indexed": 0, "by_source": per_source,
                    "offline": True, "chroma_error": str(e)[:120]}

        return {"indexed": len(all_ids), "by_source": per_source,
                "offline": False}

    async def query(
        self, query: str, n_results: int = 5,
        kinds: Iterable[str] | None = None,
    ) -> dict[str, Any]:
        """Semantic recall. Returns ranked events with metadata."""
        if not await self.ensure_collection():
            return {"ok": False, "reason": "chroma offline", "hits": []}
        try:
            qv = (await self.embedder.embed([query]))[0]
        except EmbedderUnavailable as e:
            return {"ok": False, "reason": "embedder offline",
                    "error": str(e)[:120], "hits": []}

        where = None
        if kinds:
            kinds_list = list(kinds)
            if len(kinds_list) == 1:
                where = {"kind": kinds_list[0]}
            else:
                where = {"$or": [{"kind": k} for k in kinds_list]}

        try:
            r = await self.chroma.query(
                EPISODIC_COLLECTION, [qv],
                n_results=int(n_results), where=where,
            )
        except ChromaUnavailable as e:
            return {"ok": False, "reason": "chroma query failed",
                    "error": str(e)[:120], "hits": []}

        docs = (r.get("documents") or [[]])[0]
        metas = (r.get("metadatas") or [[]])[0]
        dists = (r.get("distances") or [[]])[0]
        hits = []
        for d, m, dist in zip(docs, metas, dists):
            hits.append({
                "snippet": d, "metadata": m,
                "similarity": round(1.0 - float(dist), 3) if dist is not None else None,
            })
        return {"ok": True, "query": query, "count": len(hits),
                "hits": hits}
