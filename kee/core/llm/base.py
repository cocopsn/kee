"""LLM provider abstraction.

A provider takes a list of OpenAI-style chat messages + an optional list
of tool schemas (also OpenAI-style) and returns a normalized
`ChatResponse`. Each provider is responsible for translating the
neutral schema to its own format internally.

The neutral tool schema is the same shape as `OllamaClient` already used,
which mirrors OpenAI's function-calling format:

    [{"type": "function",
      "function": {
          "name": "calendar",
          "description": "...",
          "parameters": { JSON schema }
      }}]

A normalized `ChatResponse` is identical to the existing
`kee.core.ollama_client.ChatResponse` so the agent loop is untouched.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChatResponse:
    content: str
    tool_calls: list[dict[str, Any]] = field(default_factory=list)
    raw: dict[str, Any] | None = None
    # Provenance — set by chain
    provider_name: str | None = None
    model_name: str | None = None
    latency_ms: int | None = None
    tokens_in: int | None = None
    tokens_out: int | None = None
    cost_usd: float | None = None

    @property
    def has_tool_calls(self) -> bool:
        return bool(self.tool_calls)


class ProviderUnavailable(RuntimeError):
    """Raised when a provider can't serve THIS request — chain falls
    over to next. Examples: rate limit, network, auth, daemon down."""


class ProviderHardFail(RuntimeError):
    """Raised when the provider responded but with an unrecoverable
    error (malformed request, prompt too long). Chain does NOT retry
    on next provider — bubbles up to caller."""


class LLMProvider(ABC):
    """Abstract base for any chat-capable LLM."""

    name: str = "abstract"
    model_name: str = "unknown"
    # Per-Mtok prices (input, output) in USD; 0 for local
    cost_in_per_mtok: float = 0.0
    cost_out_per_mtok: float = 0.0

    @abstractmethod
    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> ChatResponse:
        ...

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ):
        """Async generator yielding text chunks. Default implementation
        falls back to a single-yield based on `chat()`. Subclasses override
        to do real token-by-token streaming."""
        resp = await self.chat(messages=messages, tools=tools,
                               temperature=temperature, max_tokens=max_tokens)
        if resp.content:
            yield resp.content

    @abstractmethod
    async def health(self) -> bool:
        ...

    def estimate_cost(self, tokens_in: int, tokens_out: int) -> float:
        return (tokens_in / 1_000_000) * self.cost_in_per_mtok \
             + (tokens_out / 1_000_000) * self.cost_out_per_mtok
