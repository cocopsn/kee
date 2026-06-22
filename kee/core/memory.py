"""Memory manager.

Wraps two stores:

  * **SQLite** for structured memory: conversations and messages.
  * **ChromaDB** (via the lightweight `VaultIndexer` in `kee.distributed`)
    for semantic memory over the Obsidian vault.

When the indexer's dependencies aren't available (no embedder, no ChromaDB
server), `retrieve()` returns "" and the agent keeps working — semantic
memory is an enhancement, not a hard requirement.

Also provides `summarize_conversation()` — when a conversation ends or
gets long, it asks the LLM to produce a one-paragraph summary that's
stored in `conversations.summary` for future retrieval.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from kee.core import db
from kee.distributed.indexer import VaultIndexer

if TYPE_CHECKING:
    from kee.core.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


@dataclass
class ConversationState:
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    source: str = "terminal"
    messages: list[dict[str, Any]] = field(default_factory=list)
    iteration: int = 0
    # Last router decision attached for the current turn — surfaces and
    # tests inspect this. Untyped to avoid an import cycle with router.py.
    router_decision: Any = None
    # Files Coco attached during this conversation (absolute paths under
    # data/attachments/). Surfaces through agent.process() as an extra
    # system message so the model knows it can read them via `files` tool.
    attached_files: list[str] = field(default_factory=list)


class MemoryManager:
    def __init__(self, indexer: VaultIndexer | None = None) -> None:
        self.indexer = indexer or VaultIndexer()

    # ── Conversation persistence ──────────────────────────────────────────
    def start_conversation(self, source: str) -> ConversationState:
        state = ConversationState(source=source)
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO conversations (id, source, last_active) VALUES (?, ?, ?)",
                (state.id, source, datetime.utcnow()),
            )
        return state

    def store_message(
        self,
        conversation_id: str,
        role: str,
        content: str,
        tool_name: str | None = None,
        tool_result: str | None = None,
    ) -> None:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO messages (conversation_id, role, content, tool_name, tool_result)
                VALUES (?, ?, ?, ?, ?)
                """,
                (conversation_id, role, content, tool_name, tool_result),
            )
            cur.execute(
                "UPDATE conversations SET last_active = ? WHERE id = ?",
                (datetime.utcnow(), conversation_id),
            )

    def store_conversation(self, state: ConversationState) -> None:
        """Persist the full message list (used when batch-saving)."""
        with db.cursor() as cur:
            cur.execute("BEGIN")
            try:
                for m in state.messages:
                    role = m.get("role", "")
                    content = m.get("content", "") or ""
                    tool_name = m.get("name") or m.get("tool_name")
                    cur.execute(
                        """
                        INSERT INTO messages (conversation_id, role, content, tool_name)
                        VALUES (?, ?, ?, ?)
                        """,
                        (state.id, role, content, tool_name),
                    )
                cur.execute(
                    "UPDATE conversations SET last_active = ? WHERE id = ?",
                    (datetime.utcnow(), state.id),
                )
                cur.execute("COMMIT")
            except Exception:
                cur.execute("ROLLBACK")
                raise

    def recent_conversations(self, limit: int = 10) -> list[dict[str, Any]]:
        with db.cursor() as cur:
            cur.execute(
                "SELECT * FROM conversations ORDER BY last_active DESC LIMIT ?",
                (limit,),
            )
            return [dict(r) for r in cur.fetchall()]

    def recent_summaries(
        self,
        limit: int = 5,
        exclude_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Return summaries of the most-recently-active conversations that
        already have a summary cached. Used by `agent.process()` first-turn
        path to seed cross-conversation context.
        """
        with db.cursor() as cur:
            if exclude_id:
                cur.execute(
                    "SELECT id, source, last_active, summary FROM conversations "
                    "WHERE summary IS NOT NULL AND length(summary) > 0 AND id != ? "
                    "ORDER BY last_active DESC LIMIT ?",
                    (exclude_id, limit),
                )
            else:
                cur.execute(
                    "SELECT id, source, last_active, summary FROM conversations "
                    "WHERE summary IS NOT NULL AND length(summary) > 0 "
                    "ORDER BY last_active DESC LIMIT ?",
                    (limit,),
                )
            return [dict(r) for r in cur.fetchall()]

    def cross_conversation_context(self, exclude_id: str | None = None, limit: int = 5) -> str:
        """Build a compact markdown block summarizing the last few completed
        conversations, suitable to inject as a system message on a new turn.
        Empty string if there are no summaries yet.
        """
        rows = self.recent_summaries(limit=limit, exclude_id=exclude_id)
        if not rows:
            return ""
        lines = ["## Lo que has hablado recientemente con Kee"]
        lines.append(
            "_(resúmenes de conversaciones pasadas — usa esto como "
            "contexto, no respondas a estos turnos directamente)_\n"
        )
        for r in rows:
            ts = (r.get("last_active") or "")
            if isinstance(ts, str):
                ts_short = ts[:16]
            else:
                ts_short = ts.strftime("%Y-%m-%d %H:%M") if hasattr(ts, "strftime") else str(ts)[:16]
            src = r.get("source", "?")
            summary = (r.get("summary") or "").strip()
            if summary:
                lines.append(f"- **{ts_short}** _({src})_ — {summary}")
        return "\n".join(lines)

    def stale_conversations(self, idle_minutes: int = 15, limit: int = 20) -> list[str]:
        """IDs of conversations idle longer than N minutes that DON'T have a
        summary yet — eligible for background summarization."""
        with db.cursor() as cur:
            cur.execute(
                "SELECT id FROM conversations "
                "WHERE (summary IS NULL OR length(summary) = 0) "
                "  AND last_active < datetime('now', ?) "
                "ORDER BY last_active DESC LIMIT ?",
                (f"-{idle_minutes} minutes", limit),
            )
            return [r[0] for r in cur.fetchall()]

    def get_messages(self, conversation_id: str) -> list[dict[str, Any]]:
        with db.cursor() as cur:
            cur.execute(
                "SELECT role, content, tool_name FROM messages "
                "WHERE conversation_id = ? ORDER BY id ASC",
                (conversation_id,),
            )
            return [dict(r) for r in cur.fetchall()]

    # ── Conversation summarization ────────────────────────────────────────
    async def summarize_conversation(
        self,
        conversation_id: str,
        llm: "OllamaClient",
        force: bool = False,
    ) -> str | None:
        """Ask the LLM to summarize a conversation; persist + return the summary.

        Returns None if the conversation has no user/assistant messages or
        if a summary already exists and `force=False`.
        """
        with db.cursor() as cur:
            cur.execute(
                "SELECT summary FROM conversations WHERE id = ?",
                (conversation_id,),
            )
            row = cur.fetchone()
        if row and row["summary"] and not force:
            return row["summary"]

        messages = self.get_messages(conversation_id)
        # Skip pure-system or empty conversations.
        meaningful = [m for m in messages if m["role"] in ("user", "assistant")]
        if not meaningful:
            return None

        transcript = "\n".join(
            f"[{m['role']}]: {(m['content'] or '').strip()[:400]}"
            for m in meaningful
        )

        prompt = [
            {
                "role": "system",
                "content": (
                    "You are a precise summarizer. Read the transcript and "
                    "produce a 1-3 sentence summary in Spanish that captures "
                    "what the user wanted, what was done, and the outcome. "
                    "No filler. No quotation marks."
                ),
            },
            {"role": "user", "content": transcript},
        ]

        try:
            response = await llm.chat(messages=prompt, temperature=0.2, owner="summarizer")
        except Exception as e:
            logger.warning("Summarization failed: %s", e)
            return None

        summary = (response.content or "").strip()
        if not summary:
            return None

        with db.cursor() as cur:
            cur.execute(
                "UPDATE conversations SET summary = ? WHERE id = ?",
                (summary, conversation_id),
            )
        return summary

    # ── Semantic memory (vault) — surgical RAG ────────────────────────────
    async def retrieve(
        self,
        query: str,
        top_k: int = 3,
        recall_k: int = 10,
        max_chars: int = 1200,
    ) -> str:
        """Return a markdown string of vault passages relevant to `query`.

        Surgical RAG (v2 §1.4):
          1. Vector search recalls `recall_k` candidates (broad).
          2. Cross-encoder reranker selects top `top_k` (precise).
          3. Each chunk is compressed (boilerplate stripped, capped to
             ~150 words) so the agent's small context budget isn't burned.

        Returns "" when ChromaDB or the embedder is offline, or when no
        passages match — the agent loop treats both cases the same.
        """
        try:
            recalled = await self.indexer.query(query, top_k=recall_k)
        except Exception as e:
            logger.debug("Indexer query raised: %s", e)
            return ""
        if not recalled:
            return ""

        candidates = [r["text"] for r in recalled]

        # Lazy import to avoid pulling flashrank at module load time.
        try:
            from kee.distributed.reranker import reranker as _rr
            scored = await _rr.rerank(query, candidates, top_k=top_k)
        except Exception as e:
            logger.debug("Reranker unavailable (%s) — using vector order", e)
            scored = [(c, 0.0) for c in candidates[:top_k]]

        # Map reranked text back to its original metadata so we can keep
        # the source path in the output.
        text_to_meta = {r["text"]: (r.get("metadata") or {}) for r in recalled}

        out_chunks: list[str] = []
        running_chars = 0
        for text, score in scored:
            compressed = self._compress(text)
            if not compressed:
                continue
            meta = text_to_meta.get(text, {})
            origin = meta.get("path", "?")
            block = f"### {origin}  _(rerank={score:.2f})_\n{compressed}"
            running_chars += len(block)
            if running_chars > max_chars and out_chunks:
                break  # we already have at least one chunk, stop before blowing budget
            out_chunks.append(block)

        if not out_chunks:
            return ""
        return "## Relevant memory from vault\n\n" + "\n\n---\n\n".join(out_chunks)

    @staticmethod
    def _compress(text: str, max_words: int = 200) -> str:
        """Strip markdown noise and truncate to ~max_words. Keeps facts."""
        if not text:
            return ""
        # Drop common markdown ornamentation that wastes tokens.
        cleaned = (
            text.replace("**", "")
                .replace("##", "")
                .replace("__", "")
                .strip()
        )
        words = cleaned.split()
        if len(words) > max_words:
            cleaned = " ".join(words[:max_words]) + "..."
        return cleaned
