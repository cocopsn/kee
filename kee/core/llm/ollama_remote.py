"""Remote Ollama provider — points at the Auctorum worker's Ollama
instance over Tailscale instead of localhost.

Why a separate class instead of just a different env var: the chain
should be able to fail OVER from local Ollama → remote Ollama (or vice
versa) when one is overloaded, so they need to coexist as distinct
providers in the chain. Both report cost = $0 (still local hardware,
just on the worker node).

Default model: qwen3.5:9b (already pulled on the worker per
`scripts/auctorum/provision.sh`). Override via `KEE_REMOTE_MODEL`.

Construction is gated by `AUCTORUM_OLLAMA` env var being set — without
it, the provider raises at __init__ and `build_default_chain` skips it
silently.
"""

from __future__ import annotations

import logging
import os
import time
from typing import Any

from kee.core.llm.base import (
    ChatResponse, LLMProvider, ProviderUnavailable,
)
from kee.core.ollama_client import OllamaClient, OllamaUnavailable

logger = logging.getLogger(__name__)


class OllamaRemoteProvider(LLMProvider):
    name = "ollama_remote"
    cost_in_per_mtok = 0.0
    cost_out_per_mtok = 0.0

    def __init__(
        self,
        model: str | None = None,
        host: str | None = None,
        num_ctx: int | None = None,
    ) -> None:
        host = host or os.environ.get("AUCTORUM_OLLAMA")
        if not host:
            raise RuntimeError(
                "OllamaRemoteProvider requires AUCTORUM_OLLAMA env var"
            )
        self.model_name = (
            model or os.environ.get("KEE_REMOTE_MODEL", "qwen3.5:9b")
        )
        self._client = OllamaClient(
            model=self.model_name, host=host, num_ctx=num_ctx,
        )

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        t0 = time.monotonic()
        try:
            resp = await self._client.chat(
                messages=messages,
                tools=tools,
                temperature=temperature,
            )
        except OllamaUnavailable as e:
            raise ProviderUnavailable(f"OllamaRemote: {e}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Same rough token estimate as the local provider — Ollama doesn't
        # report usage in the chat API by default.
        ti = sum(len(str(m.get("content", ""))) for m in messages) // 4
        to = len(resp.content) // 4

        return ChatResponse(
            content=resp.content,
            tool_calls=resp.tool_calls,
            raw=resp.raw,
            provider_name=self.name,
            model_name=self.model_name,
            latency_ms=elapsed_ms,
            tokens_in=ti,
            tokens_out=to,
            cost_usd=0.0,
        )

    async def health(self) -> bool:
        return await self._client.health()

    async def wait_for_ready(self, timeout_s: int = 60) -> bool:
        return await self._client.wait_for_ready(timeout_s=timeout_s)
