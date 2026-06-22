"""Anthropic Claude provider.

Wraps the official `anthropic` SDK. Translates between OpenAI-style
neutral schema and Claude's native message/tool formats:

  - System messages → top-level `system` parameter (concatenated)
  - User/assistant → `messages` array
  - Tool calls in assistant turns → `content: [{type:tool_use,...}]`
  - Tool results from user side → `content: [{type:tool_result,...}]`
  - Tools schema → `tools: [{name, description, input_schema}]`

Uses claude-sonnet-4-6 by default (good capability + sane price for
medium-tier turns). Returns a `ChatResponse` identical in shape to
the Ollama provider so the agent loop is provider-agnostic.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from kee.core.llm.base import (
    ChatResponse, LLMProvider, ProviderHardFail, ProviderUnavailable,
)

logger = logging.getLogger(__name__)


class ClaudeProvider(LLMProvider):
    """Anthropic Sonnet — primary heavy-tier provider.

    Sub-classed by `ClaudeHaikuProvider` for the cheaper Haiku model. All
    schema translation, history-degradation, and error handling is shared.
    Subclasses override `name`, `model_name`, and the per-Mtok costs.
    """
    name = "claude"
    model_name = "claude-sonnet-4-6"
    # Sonnet 4.6 pricing (per Mtok)
    cost_in_per_mtok = 3.0
    cost_out_per_mtok = 15.0
    # Env var that overrides the model_name; subclasses pick a different one
    _model_env_var = "KEE_CLAUDE_MODEL"
    _default_model = "claude-sonnet-4-6"

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens_default: int = 2048,
    ) -> None:
        self.model_name = model or os.environ.get(self._model_env_var, self._default_model)
        key = api_key or os.environ.get("ANTHROPIC_API_KEY", "").strip()
        if not key:
            raise ProviderUnavailable(
                "ANTHROPIC_API_KEY not set. Get one at https://console.anthropic.com "
                "and add it to D:/Kee/.env"
            )
        # Lazy import so the SDK is only loaded when this provider exists.
        from anthropic import AsyncAnthropic
        self._client = AsyncAnthropic(api_key=key)
        self.max_tokens_default = max_tokens_default

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        from anthropic import APIConnectionError, APIStatusError, RateLimitError

        sys_text, claude_messages = _to_claude_messages(messages)
        claude_tools = _to_claude_tools(tools) if tools else None

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens or self.max_tokens_default,
            "messages": claude_messages,
        }
        if sys_text:
            kwargs["system"] = sys_text
        if claude_tools:
            kwargs["tools"] = claude_tools
        if temperature is not None:
            kwargs["temperature"] = temperature

        t0 = time.monotonic()
        try:
            resp = await self._client.messages.create(**kwargs)
        except RateLimitError as e:
            raise ProviderUnavailable(f"Claude rate-limited: {e}") from e
        except APIConnectionError as e:
            raise ProviderUnavailable(f"Claude network failure: {e}") from e
        except APIStatusError as e:
            # 4xx that's our fault — bubble up so chain doesn't retry blindly.
            if 400 <= e.status_code < 500 and e.status_code not in (408, 429):
                raise ProviderHardFail(f"Claude {e.status_code}: {e.message}") from e
            raise ProviderUnavailable(f"Claude {e.status_code}: {e.message}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Extract content + tool_use blocks
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []
        for block in resp.content:
            btype = getattr(block, "type", None)
            if btype == "text":
                text_parts.append(block.text)
            elif btype == "tool_use":
                tool_calls.append({
                    "name": block.name,
                    "arguments": block.input,
                    "id": block.id,
                })

        usage = getattr(resp, "usage", None)
        tokens_in = getattr(usage, "input_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "output_tokens", 0) if usage else 0

        return ChatResponse(
            content="".join(text_parts).strip(),
            tool_calls=tool_calls,
            raw={"stop_reason": resp.stop_reason, "id": resp.id},
            provider_name=self.name,
            model_name=self.model_name,
            latency_ms=elapsed_ms,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=self.estimate_cost(tokens_in, tokens_out),
        )

    async def health(self) -> bool:
        try:
            # Cheapest possible call: 1 token completion
            await self._client.messages.create(
                model=self.model_name,
                max_tokens=1,
                messages=[{"role": "user", "content": "."}],
            )
            return True
        except Exception as e:
            logger.warning("Claude health check failed: %s", e)
            return False

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """Anthropic streaming via `client.messages.stream`. Yields text
        deltas as they arrive. Tool calls bypass streaming (need full
        response) — falls back to non-streaming `chat()` if tools given."""
        if tools:
            resp = await self.chat(messages, tools, temperature, max_tokens)
            if resp.content:
                yield resp.content
            return
        sys_text, claude_messages = _to_claude_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "max_tokens": max_tokens or self.max_tokens_default,
            "messages": claude_messages,
        }
        if sys_text:
            kwargs["system"] = sys_text
        if temperature is not None:
            kwargs["temperature"] = temperature
        async with self._client.messages.stream(**kwargs) as stream:
            async for text in stream.text_stream:
                if text:
                    yield text


# ── Schema translation helpers ───────────────────────────────────────────

def _to_claude_messages(
    messages: list[dict[str, Any]],
) -> tuple[str, list[dict[str, Any]]]:
    """Split out system messages (concatenated as a single Claude `system`
    string) and convert the rest into Claude's message format.

    Claude is strict: every `tool_result` block must reference a
    `tool_use_id` produced by the immediately preceding assistant turn.
    Our conversation history can contain tool results from earlier
    Ollama-served turns whose IDs Claude has never seen — converting
    those naively would 400. Instead, when we see a tool_result whose
    matching tool_use is NOT in any earlier ASSISTANT turn we've kept,
    we degrade it to a plain text `user` message describing the result.
    """
    sys_parts: list[str] = []
    out: list[dict[str, Any]] = []
    seen_use_ids: set[str] = set()  # IDs we've kept on assistant turns

    def _append_user(text_or_blocks: Any) -> None:
        if out and out[-1]["role"] == "user":
            cur = out[-1]["content"]
            if isinstance(cur, list) and isinstance(text_or_blocks, list):
                cur.extend(text_or_blocks)
                return
            if isinstance(cur, str) and isinstance(text_or_blocks, str):
                out[-1]["content"] = cur + "\n\n" + text_or_blocks
                return
        out.append({"role": "user", "content": text_or_blocks})

    for m in messages:
        role = m.get("role")
        content = m.get("content", "")
        if role == "system":
            if isinstance(content, str) and content:
                sys_parts.append(content)
            continue
        if role == "tool":
            tc_id = m.get("tool_call_id") or m.get("id") or ""
            payload = content if isinstance(content, str) else str(content)
            if tc_id and tc_id in seen_use_ids:
                # Real Claude tool_use → tool_result match
                _append_user([{
                    "type": "tool_result",
                    "tool_use_id": tc_id,
                    "content": payload,
                }])
            else:
                # Synthetic — degrade to plain text so Claude doesn't 400
                tool_name = m.get("name") or "tool"
                _append_user(
                    f"[Earlier `{tool_name}` result]: {payload[:1000]}"
                )
            continue
        if role == "assistant":
            # Track any tool_use IDs we're sending so subsequent tool_result
            # blocks can be matched. Assistant messages here are usually
            # plain text from Ollama (no native tool_use blocks) — only
            # Claude itself emits tool_use blocks via its API response.
            if isinstance(content, list):
                for blk in content:
                    if isinstance(blk, dict) and blk.get("type") == "tool_use":
                        if blk.get("id"):
                            seen_use_ids.add(blk["id"])
                out.append({"role": "assistant", "content": content})
            else:
                if out and out[-1]["role"] == "assistant" and isinstance(out[-1]["content"], str):
                    out[-1]["content"] += "\n\n" + (content or "")
                else:
                    out.append({"role": "assistant", "content": content or ""})
            continue
        if role == "user":
            if isinstance(content, str):
                _append_user(content)
            else:
                out.append({"role": "user", "content": content})
            continue

    # Claude rejects empty assistant content — drop empties
    out = [m for m in out
           if not (isinstance(m.get("content"), str) and not m["content"].strip())]

    # Claude requires the conversation to start with a user message.
    while out and out[0]["role"] != "user":
        out.pop(0)

    return ("\n\n".join(sys_parts)).strip(), out


def _to_claude_tools(tools: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert OpenAI-style function tools to Claude's format."""
    out = []
    for t in tools:
        fn = t.get("function", t)
        out.append({
            "name": fn.get("name"),
            "description": fn.get("description", ""),
            "input_schema": fn.get("parameters", {"type": "object", "properties": {}}),
        })
    return out


class ClaudeHaikuProvider(ClaudeProvider):
    """Anthropic Haiku 4.5 — cheap & fast medium-tier provider.

    Same SDK + schema translation as ClaudeProvider, just a different
    model and pricing. Used by router for the `medium` tier so common
    contextual answers (status questions, file reads) are 75% cheaper
    than going through Sonnet.

    Pricing reference: claude-haiku-4-5 = $0.80 in / $4 out per Mtok
    vs Sonnet 4.6 = $3 / $15 per Mtok.
    """
    name = "haiku"
    model_name = "claude-haiku-4-5"
    cost_in_per_mtok = 0.80
    cost_out_per_mtok = 4.0
    _model_env_var = "KEE_HAIKU_MODEL"
    _default_model = "claude-haiku-4-5"
