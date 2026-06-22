"""LLMChain — orchestrates primary provider + fallbacks.

Tries each provider in order. ProviderUnavailable triggers fallover.
ProviderHardFail bubbles up — caller decides what to do (typically
return an error to the user). Tracks per-call provenance for the
audit log + cost ticker.
"""

from __future__ import annotations

import logging
from typing import Any

# Importing kee.config at module load triggers .env loading via python-dotenv
from kee.config import settings  # noqa: F401
from kee.core.llm.base import (
    ChatResponse, LLMProvider, ProviderHardFail, ProviderUnavailable,
)

logger = logging.getLogger(__name__)


class LLMChain:
    def __init__(self, providers: list[LLMProvider]) -> None:
        if not providers:
            raise ValueError("LLMChain needs at least one provider.")
        self.providers = providers
        # last-good-provider gets a fast-path next time
        self._last_good_idx = 0

    @property
    def primary(self) -> LLMProvider:
        return self.providers[0]

    async def chat(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        force_provider: str | None = None,
    ) -> ChatResponse:
        """Try each provider until one succeeds.

        `force_provider` skips the chain and pins to that provider name —
        used by the router to send tier-specific routing.
        """
        if force_provider:
            for p in self.providers:
                if p.name == force_provider:
                    try:
                        return await self._call(p, messages, tools, temperature, max_tokens)
                    except ProviderUnavailable as e:
                        logger.warning(
                            "Forced provider '%s' failed (%s) — falling back to chain order",
                            force_provider, e,
                        )
                        break
            else:
                logger.warning(
                    "No provider named '%s' in chain — falling back to chain order",
                    force_provider,
                )

        last_err: Exception | None = None
        for i, p in enumerate(self.providers):
            try:
                resp = await self._call(p, messages, tools, temperature, max_tokens)
                self._last_good_idx = i
                return resp
            except ProviderUnavailable as e:
                logger.warning("Provider %s unavailable, trying next: %s", p.name, e)
                last_err = e
                continue
            except ProviderHardFail:
                # Don't retry — bubble up
                raise
        # All providers failed with ProviderUnavailable
        raise ProviderUnavailable(
            f"All {len(self.providers)} providers unavailable. Last: {last_err}"
        )

    async def _call(
        self,
        p: LLMProvider,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float | None,
        max_tokens: int | None,
    ) -> ChatResponse:
        return await p.chat(
            messages=messages, tools=tools,
            temperature=temperature, max_tokens=max_tokens,
        )

    async def chat_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        temperature: float | None = None,
        max_tokens: int | None = None,
        force_provider: str | None = None,
    ):
        """Streaming variant — yields text chunks from the first provider
        that doesn't raise ProviderUnavailable. force_provider pins the
        target the same way as chat()."""
        provider = None
        if force_provider:
            for p in self.providers:
                if p.name == force_provider:
                    provider = p
                    break
        if provider is None:
            provider = self.primary
        async for chunk in provider.chat_stream(
            messages=messages, tools=tools,
            temperature=temperature, max_tokens=max_tokens,
        ):
            yield chunk

    async def health_all(self) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for p in self.providers:
            try:
                out[p.name] = await p.health()
            except Exception:
                out[p.name] = False
        return out


def build_default_chain() -> LLMChain:
    """Build the standard chain from env config:
        KEE_LLM_PRIMARY = ollama | claude | haiku | openai | ollama_remote
            (default: ollama)

    Always includes ALL configured providers in the chain (so changing
    primary doesn't strand the others as fallbacks). Order: primary first,
    then the rest in the canonical fallback order:
        claude → haiku → openai → ollama → ollama_remote.

    Providers whose construction fails (missing key/host, etc.) are
    skipped silently so a single misconfig doesn't kill the agent.

    `ollama_remote` is included only when `AUCTORUM_OLLAMA` is set in
    the env. It's a separate provider from local `ollama` so the chain
    can fail OVER between them when one is busy.
    """
    import os
    primary = os.environ.get("KEE_LLM_PRIMARY", "ollama").strip().lower()
    # Canonical fallback order: power-quality first, free last.
    canonical = ["claude", "haiku", "openai", "ollama"]
    if os.environ.get("AUCTORUM_OLLAMA"):
        canonical.append("ollama_remote")
    if primary not in canonical:
        logger.warning("Unknown KEE_LLM_PRIMARY=%r — defaulting to ollama", primary)
        primary = "ollama"
    order = [primary] + [p for p in canonical if p != primary]

    def _construct(name: str) -> LLMProvider | None:
        try:
            if name == "claude":
                from kee.core.llm.claude import ClaudeProvider
                return ClaudeProvider()
            if name == "haiku":
                from kee.core.llm.claude import ClaudeHaikuProvider
                return ClaudeHaikuProvider()
            if name == "openai":
                from kee.core.llm.openai_p import OpenAIProvider
                return OpenAIProvider()
            if name == "ollama":
                from kee.core.llm.ollama_p import OllamaProvider
                return OllamaProvider()
            if name == "ollama_remote":
                from kee.core.llm.ollama_remote import OllamaRemoteProvider
                return OllamaRemoteProvider()
        except Exception as e:
            logger.warning("Provider '%s' unavailable at startup (%s) — skipping", name, e)
        return None

    providers: list[LLMProvider] = []
    for name in order:
        p = _construct(name)
        if p is not None:
            providers.append(p)

    if not providers:
        # Final safety net: pure local Ollama
        from kee.core.llm.ollama_p import OllamaProvider
        providers.append(OllamaProvider())
    return LLMChain(providers)
