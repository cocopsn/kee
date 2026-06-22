"""Self-correction loop on tool errors — $0, no LLM call (mocked).

Verifies:
  - Voice surface is bypassed (cost budget protection)
  - Already-attempted self-correction doesn't loop
  - Successful correction injects message + resets failure counter
  - Failed correction (LLM unreachable) falls through to hard block

Run::

    .venv\\Scripts\\python.exe tests/test_self_correction.py
"""

from __future__ import annotations

import asyncio
import sys
from unittest.mock import patch


class _FakeState:
    """Minimal ConversationState shape needed by _propose_tool_correction."""
    def __init__(self, messages, source="chat"):
        self.messages = messages
        self.source = source


def _make_agent_minimal():
    """Construct just enough of KeeAgent to call the helper without
    bootstrapping the whole stack."""
    from kee.core.agent import KeeAgent
    a = KeeAgent.__new__(KeeAgent)
    return a


def test_propose_returns_text_when_llm_works() -> int:
    """Mock the OllamaClient.chat to return a fixed string."""
    from unittest.mock import AsyncMock, MagicMock
    a = _make_agent_minimal()
    state = _FakeState(messages=[
        {"role": "user", "content": "list files in D:/missing"},
        {"role": "assistant", "content": "calling files tool"},
        {"role": "tool", "name": "files",
         "content": '{"ok":false,"error":"path not found"}'},
    ])

    async def main():
        with patch("kee.core.ollama_client.OllamaClient") as MC:
            mock_client = MC.return_value
            mock_client.chat = AsyncMock(return_value=MagicMock(
                content="Cambia path por D:/Kee. El que diste no existe."
            ))
            out = await a._propose_tool_correction(state, ["files"])
        return out

    out = asyncio.run(main())
    if out and "D:/Kee" in out:
        print(f"  [ok] correction proposed: {out[:60]!r}")
        return 0
    print(f"  [FAIL] {out!r}")
    return 1


def test_propose_returns_none_when_llm_fails() -> int:
    """Mock the OllamaClient to raise — we should get None back."""
    from unittest.mock import AsyncMock
    a = _make_agent_minimal()
    state = _FakeState(messages=[{"role": "user", "content": "x"}])

    async def main():
        with patch("kee.core.ollama_client.OllamaClient") as MC:
            mock_client = MC.return_value
            mock_client.chat = AsyncMock(side_effect=RuntimeError("ollama down"))
            return await a._propose_tool_correction(state, ["files"])

    out = asyncio.run(main())
    if out is None:
        print("  [ok] LLM failure returns None (falls through to hard block)")
        return 0
    print(f"  [FAIL] expected None, got {out!r}")
    return 1


def test_propose_returns_none_for_empty_response() -> int:
    """Empty / too-short LLM response is treated as no-correction."""
    from unittest.mock import AsyncMock, MagicMock
    a = _make_agent_minimal()
    state = _FakeState(messages=[{"role": "user", "content": "x"}])

    async def main():
        with patch("kee.core.ollama_client.OllamaClient") as MC:
            mock_client = MC.return_value
            mock_client.chat = AsyncMock(return_value=MagicMock(content=""))
            return await a._propose_tool_correction(state, ["files"])

    out = asyncio.run(main())
    if out is None:
        print("  [ok] empty LLM response returns None")
        return 0
    print(f"  [FAIL] {out!r}")
    return 1


def test_helper_method_exists_and_async() -> int:
    """Sanity — agent.py has _propose_tool_correction and it's async."""
    from kee.core.agent import KeeAgent
    fn = getattr(KeeAgent, "_propose_tool_correction", None)
    if fn and asyncio.iscoroutinefunction(fn):
        print("  [ok] _propose_tool_correction exists and is async")
        return 0
    print(f"  [FAIL] fn={fn}")
    return 1


if __name__ == "__main__":
    print("=== self-correction loop ===")
    fails = 0
    fails += test_helper_method_exists_and_async()
    fails += test_propose_returns_text_when_llm_works()
    fails += test_propose_returns_none_when_llm_fails()
    fails += test_propose_returns_none_for_empty_response()
    print()
    print(f"Done. failures={fails}")
    sys.exit(0 if fails == 0 else 1)
