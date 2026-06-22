"""OpenAI provider — fallback after Claude.

Uses gpt-4o-mini by default ($0.15/$0.60 per Mtok) — cheap and fast.
The OpenAI Chat Completions API uses essentially the same schema as
our neutral format, so translation is minimal.
"""

from __future__ import annotations

import json
import logging
import os
import time
from typing import Any

from kee.core.llm.base import (
    ChatResponse, LLMProvider, ProviderHardFail, ProviderUnavailable,
)

logger = logging.getLogger(__name__)


class OpenAIProvider(LLMProvider):
    name = "openai"
    model_name = "gpt-4o-mini"
    cost_in_per_mtok = 0.15
    cost_out_per_mtok = 0.60

    def __init__(
        self,
        model: str | None = None,
        api_key: str | None = None,
        max_tokens_default: int = 2048,
    ) -> None:
        self.model_name = model or os.environ.get("KEE_OPENAI_MODEL", "gpt-4o-mini")
        key = api_key or os.environ.get("OPENAI_API_KEY", "").strip()
        if not key:
            raise ProviderUnavailable("OPENAI_API_KEY not set.")
        from openai import AsyncOpenAI
        self._client = AsyncOpenAI(api_key=key)
        self.max_tokens_default = max_tokens_default

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        from openai import (
            APIConnectionError, APITimeoutError, BadRequestError, RateLimitError,
        )

        # OpenAI uses 'tool_call_id' on tool messages — pass through
        oa_messages = _to_openai_messages(messages)

        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": oa_messages,
            "max_tokens": max_tokens or self.max_tokens_default,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        if tools:
            kwargs["tools"] = tools  # already in OpenAI shape
            kwargs["tool_choice"] = "auto"

        t0 = time.monotonic()
        try:
            resp = await self._client.chat.completions.create(**kwargs)
        except RateLimitError as e:
            raise ProviderUnavailable(f"OpenAI rate-limited: {e}") from e
        except (APIConnectionError, APITimeoutError) as e:
            raise ProviderUnavailable(f"OpenAI network: {e}") from e
        except BadRequestError as e:
            raise ProviderHardFail(f"OpenAI bad request: {e}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        choice = resp.choices[0].message
        content = choice.content or ""
        tool_calls: list[dict[str, Any]] = []
        for tc in (choice.tool_calls or []):
            try:
                args = json.loads(tc.function.arguments or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc.function.arguments}
            tool_calls.append({
                "name": tc.function.name,
                "arguments": args,
                "id": tc.id,
            })

        usage = resp.usage
        ti = getattr(usage, "prompt_tokens", 0) if usage else 0
        to = getattr(usage, "completion_tokens", 0) if usage else 0

        return ChatResponse(
            content=content.strip(),
            tool_calls=tool_calls,
            raw={"finish_reason": resp.choices[0].finish_reason, "id": resp.id},
            provider_name=self.name,
            model_name=self.model_name,
            latency_ms=elapsed_ms,
            tokens_in=ti,
            tokens_out=to,
            cost_usd=self.estimate_cost(ti, to),
        )

    async def health(self) -> bool:
        try:
            await self._client.chat.completions.create(
                model=self.model_name,
                max_tokens=1,
                messages=[{"role": "user", "content": "."}],
            )
            return True
        except Exception as e:
            logger.warning("OpenAI health check failed: %s", e)
            return False

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """OpenAI streaming via stream=True. Yields content deltas. Tool
        calls fall back to non-streaming."""
        if tools:
            resp = await self.chat(messages, tools, temperature, max_tokens)
            if resp.content:
                yield resp.content
            return
        oa_messages = _to_openai_messages(messages)
        kwargs: dict[str, Any] = {
            "model": self.model_name,
            "messages": oa_messages,
            "max_tokens": max_tokens or self.max_tokens_default,
            "stream": True,
        }
        if temperature is not None:
            kwargs["temperature"] = temperature
        stream = await self._client.chat.completions.create(**kwargs)
        async for chunk in stream:
            try:
                delta = chunk.choices[0].delta
                if delta and delta.content:
                    yield delta.content
            except (IndexError, AttributeError):
                continue


def _to_openai_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Convert neutral message format → OpenAI's required shape.

    Two transformations:
      1. tool messages need `tool_call_id` (we may have stored it as `id`)
      2. assistant messages carrying tool_calls need each call as
         {id, type: "function", function: {name, arguments: <json string>}}
         — our neutral format stores them as {name, arguments: dict, id}.
         Without `type: "function"` OpenAI returns 400 missing param.
    """
    import json as _json
    out: list[dict[str, Any]] = []
    for m in messages:
        role = m.get("role")
        if role == "tool":
            out.append({
                "role": "tool",
                "tool_call_id": m.get("tool_call_id") or m.get("id") or "",
                "content": m.get("content", ""),
            })
        elif role == "assistant" and m.get("tool_calls"):
            # Re-serialize tool_calls into OpenAI's expected envelope.
            # Two normalizations:
            #   - need `type: "function"` on every tool_call
            #   - `function.arguments` MUST be a JSON string (not dict)
            converted = []
            for tc in m["tool_calls"]:
                if "function" in tc:           # already OpenAI-ish
                    fn = dict(tc["function"])
                    args = fn.get("arguments", "{}")
                    if isinstance(args, dict):
                        args = _json.dumps(args)
                    elif args is None:
                        args = "{}"
                    fn["arguments"] = args
                    converted.append({
                        "id": tc.get("id", "") or "call_0",
                        "type": tc.get("type", "function"),
                        "function": fn,
                    })
                else:                           # neutral shape → wrap
                    args = tc.get("arguments", {})
                    if isinstance(args, dict):
                        args = _json.dumps(args)
                    elif args is None:
                        args = "{}"
                    converted.append({
                        "id": tc.get("id", "") or "call_0",
                        "type": "function",
                        "function": {
                            "name": tc.get("name", ""),
                            "arguments": args,
                        },
                    })
            out.append({
                "role": "assistant",
                "content": m.get("content", "") or None,
                "tool_calls": converted,
            })
        else:
            out.append(m)
    return out
