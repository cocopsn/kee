"""Async wrapper around the Ollama API.

Uses the official `ollama` Python SDK's `AsyncClient`. Two extras over the
naive wrapper:

  * `wait_for_ready()` — poll the daemon at startup until it responds, with
    a clear timeout error. Avoids "ConnectionRefused" panic when Kee starts
    a few seconds before `ollama serve`.
  * Every `chat()` call goes through the scheduler's `llm_lock` so that
    heartbeat / voice / user input can't pile up on top of each other.

Raises `OllamaUnavailable` for clear, recoverable failure modes (daemon
down, model not pulled). Other errors propagate.
"""

from __future__ import annotations

import asyncio
import logging
import os
from dataclasses import dataclass, field
from typing import Any

import httpx
from ollama import AsyncClient, ResponseError

from kee.config import settings
from kee.core import scheduler as sched

logger = logging.getLogger(__name__)


def _env_bool(name: str, default: bool = False) -> bool:
    """Parse a boolean env var with the standard truthy aliases."""
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


class OllamaUnavailable(RuntimeError):
    """Daemon unreachable, or the requested model isn't pulled."""


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class OllamaClient:
    def __init__(
        self,
        host: str | None = None,
        model: str | None = None,
        temperature: float | None = None,
        num_ctx: int | None = None,
        scheduler: sched.KeeScheduler | None = None,
    ):
        self.host = host or settings.ollama_host
        self.model = model or settings.model
        self.temperature = temperature if temperature is not None else settings.temperature
        self.num_ctx = num_ctx or settings.num_ctx
        self._client = AsyncClient(host=self.host)
        self._scheduler = scheduler or sched.get_default()
        self._ready = asyncio.Event()

    # ── Lifecycle ─────────────────────────────────────────────────────────
    async def wait_for_ready(self, timeout_s: int = 60) -> bool:
        """Poll Ollama's /api/tags until it responds. Sets an internal flag.

        Returns True if Ollama is up and our model is present, False if up
        but model missing. Raises OllamaUnavailable on timeout.
        """
        url = f"{self.host}/api/tags"
        deadline_loops = max(1, timeout_s)
        async with httpx.AsyncClient(timeout=2.0) as client:
            for attempt in range(deadline_loops):
                try:
                    r = await client.get(url)
                    if r.status_code == 200:
                        self._ready.set()
                        models = [m.get("name", "") for m in r.json().get("models", [])]
                        present = any(self.model in n for n in models)
                        if not present:
                            logger.warning(
                                "Ollama up at %s but model '%s' not pulled. "
                                "Available: %s",
                                self.host, self.model, models,
                            )
                        return present
                except (httpx.ConnectError, httpx.ReadError, httpx.TimeoutException):
                    pass
                if attempt < deadline_loops - 1:
                    await asyncio.sleep(1)
        raise OllamaUnavailable(
            f"Ollama did not respond at {self.host} within {timeout_s}s. "
            f"Run `ollama serve` (or start the Ollama desktop app on Windows)."
        )

    # ── Chat ──────────────────────────────────────────────────────────────
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        priority: sched.Priority = sched.Priority.HIGH,
        owner: str = "agent",
    ) -> ChatResponse:
        options: dict[str, Any] = {
            "temperature": temperature if temperature is not None else self.temperature,
            "num_ctx": self.num_ctx,
            # Anti-repetition guard for local Qwen-class models. Without
            # these, some prompts can fall into paragraph loops.
            # repeated 30+ times) on open-ended prompts.
            "repeat_penalty": 1.18,
            "repeat_last_n": 256,
            # Cap output length so a runaway loop hits the wall fast
            # instead of streaming for 60 s. 1500 tokens ≈ ~1100 words,
            # plenty for any single-turn answer.
            "num_predict": 1500,
        }

        kwargs: dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "options": options,
            "stream": False,
            # Disable thinking mode by default — Qwen3-thinking variants
            # otherwise burn the entire `num_predict` budget inside
            # <think> blocks that Ollama strips, leaving 0 visible
            # tokens for the actual reply. Override with KEE_OLLAMA_THINK=1
            # if you specifically want thinking output.
            "think": _env_bool("KEE_OLLAMA_THINK", default=False),
        }
        if tools:
            kwargs["tools"] = tools

        async with self._scheduler.llm_call(owner=owner, priority=priority):
            try:
                resp = await self._client.chat(**kwargs)
            except (httpx.ConnectError, httpx.ReadError) as e:
                raise OllamaUnavailable(
                    f"Cannot reach Ollama at {self.host}. Is it running?"
                ) from e
            except ResponseError as e:
                msg = str(e).lower()
                if "model" in msg and ("not found" in msg or "not loaded" in msg):
                    raise OllamaUnavailable(
                        f"Model '{self.model}' not found. "
                        f"Run: ollama pull {self.model}"
                    ) from e
                # Other 5xx (e.g. Qwen emits malformed tool-call XML and
                # Ollama's qwen35.go parser returns 500 with `EOF`). Don't
                # kill the agent loop — surface a synthetic empty response
                # tagged with a parse-error hint so the agent can retry
                # without tools instead of looping into the same crash.
                msg_str = str(e)
                tool_parse_failure = self._is_tool_parser_failure(msg_str)
                log = logger.debug if tool_parse_failure else logger.warning
                log(
                    "Ollama returned an error response (status≈%s, "
                    "tool_parse_failure=%s). Treating as empty completion. "
                    "Detail: %s",
                    getattr(e, "status_code", "?"),
                    tool_parse_failure,
                    msg_str[:200],
                )
                return ChatResponse(
                    content="",
                    tool_calls=[],
                    raw={
                        "_error": msg_str[:400],
                        "_tool_parse_failure": tool_parse_failure,
                    },
                )

        return self._normalize(resp)

    # ── Helpers ───────────────────────────────────────────────────────────
    @staticmethod
    def _is_tool_parser_failure(msg: str) -> bool:
        """Return True for Ollama failures caused by tool parser generation."""
        text = (msg or "").lower()
        return (
            "tool call parsing" in text
            or text.strip().endswith("eof")
            or "eof " in text
            or "unable to generate parser" in text
            or "automatic parser generation failed" in text
            or "parser for this template" in text
        )

    # Patterns that signal qwen3 internal reasoning leaking as plain text
    # (no <think> tags wrapping it). When a paragraph BEGINS with one of
    # these, drop the whole paragraph — it's a chain-of-thought leak, not
    # part of the user-facing reply.
    _REASONING_LEAK_PREFIXES = (
        "okay, let's see", "okay, let me", "okay so", "okay,",
        "hmm,", "hmm so", "wait,", "wait —", "wait...",
        "let me think", "let me check", "let me see", "let me figure",
        "so the user", "the user is", "the user wants", "the user probably",
        "i should explain", "i should check", "i should make",
        "first, i need", "first, let me", "first, i should",
        "looking at the", "looking back",
    )

    @staticmethod
    def _strip_repetition_loop(text: str) -> str:
        """Cut a runaway repetition loop after its 2nd full repeat.

        Local Qwen-class models can degenerate into looping the same paragraph
        endlessly. We split on blank-line paragraphs, normalise whitespace,
        and as soon as we see a paragraph repeated for a 2nd time keep
        only what came before its 2nd appearance.
        """
        if not text or len(text) < 400:
            return text  # nothing big enough to be a loop
        import re
        blocks = re.split(r"\n\s*\n", text)
        seen: dict[str, int] = {}
        keep_until = len(blocks)
        for i, b in enumerate(blocks):
            key = re.sub(r"\s+", " ", b.strip().lower())[:160]
            if not key:
                continue
            if key in seen:
                seen[key] += 1
                if seen[key] >= 2:  # 3rd occurrence → it's a loop
                    keep_until = i
                    break
            else:
                seen[key] = 1
        if keep_until < len(blocks):
            return "\n\n".join(blocks[:keep_until]).rstrip()
        return text

    @staticmethod
    def _strip_thinking(text: str) -> str:
        """Strip qwen3 reasoning leak — both <think> tag form and bare form.

        Removes:
        1. Paired `<think>…</think>` / `<thinking>…</thinking>` blocks.
        2. Orphan opening / closing tags.
        3. Bare-text leak: paragraphs whose first non-empty line begins
           with a phrase from `_REASONING_LEAK_PREFIXES`. Run blockwise
           so we don't nuke legitimate text that happens to follow.
        Run on every assistant content before it leaves the client.
        """
        import re
        if not text:
            return text
        # Paired blocks first
        text = re.sub(r"<think(?:ing)?\b[^>]*>.*?</think(?:ing)?>", "",
                      text, flags=re.IGNORECASE | re.DOTALL)
        # Orphan opening — drop everything before the next break or to start
        text = re.sub(r"<think(?:ing)?\b[^>]*>.*?(?=\n\n|\Z)", "",
                      text, flags=re.IGNORECASE | re.DOTALL)
        # Orphan closing — drop the tag itself
        text = re.sub(r"</think(?:ing)?>", "", text, flags=re.IGNORECASE)

        # Bare reasoning leak: split on blank lines, drop blocks whose first
        # line starts with a known reasoning prefix.
        blocks = re.split(r"\n\s*\n", text)
        kept = []
        for blk in blocks:
            head = blk.lstrip().lower()[:60]
            if any(head.startswith(p) for p in OllamaClient._REASONING_LEAK_PREFIXES):
                continue
            kept.append(blk)
        text = "\n\n".join(kept)
        return text.strip()

    @staticmethod
    def _normalize(resp: Any) -> ChatResponse:
        msg = resp.get("message", {}) if isinstance(resp, dict) else resp.message
        raw = (msg.get("content", "") if isinstance(msg, dict) else msg.content) or ""
        content = OllamaClient._strip_thinking(raw)
        content = OllamaClient._strip_repetition_loop(content)
        raw_calls = (
            msg.get("tool_calls", []) if isinstance(msg, dict) else (msg.tool_calls or [])
        )

        normalized: list[dict[str, Any]] = []
        for tc in raw_calls:
            if isinstance(tc, dict):
                fn = tc.get("function", {})
                name = fn.get("name")
                args = fn.get("arguments", {})
            else:
                fn = tc.function
                name = fn.name
                args = fn.arguments
            normalized.append({"name": name, "arguments": args})

        return ChatResponse(
            content=content,
            tool_calls=normalized,
            raw=resp if isinstance(resp, dict) else None,
        )

    async def health(self) -> bool:
        """Quick health check — does Ollama respond and is our model present?"""
        try:
            resp = await self._client.list()
        except Exception as e:
            logger.warning("Ollama health check failed: %s", e)
            return False
        models = resp.get("models", []) if isinstance(resp, dict) else resp.models
        names = []
        for m in models:
            if isinstance(m, dict):
                names.append(m.get("name") or m.get("model", ""))
            else:
                names.append(getattr(m, "name", "") or getattr(m, "model", ""))
        return any(self.model in n for n in names)
