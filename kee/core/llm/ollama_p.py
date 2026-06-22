"""Ollama provider — local fallback. Wraps the existing OllamaClient
so we don't reimplement scheduler+_strip_thinking+_strip_repetition_loop."""

from __future__ import annotations

import logging
import time
from typing import Any

from kee.config import settings
from kee.core.llm.base import (
    ChatResponse, LLMProvider, ProviderUnavailable,
)
from kee.core.ollama_client import OllamaClient, OllamaUnavailable

logger = logging.getLogger(__name__)


class OllamaProvider(LLMProvider):
    name = "ollama"
    cost_in_per_mtok = 0.0
    cost_out_per_mtok = 0.0

    def __init__(self, model: str | None = None, num_ctx: int | None = None) -> None:
        self.model_name = model or settings.model
        self._client = OllamaClient(model=self.model_name, num_ctx=num_ctx)

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
            raise ProviderUnavailable(f"Ollama: {e}") from e
        elapsed_ms = int((time.monotonic() - t0) * 1000)

        # Rough token estimation (no usage from Ollama by default in chat API)
        # We trust prompt length / output length as char approximations
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
