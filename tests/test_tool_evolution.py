"""End-to-end test for the tool_evolution proposal pipeline — $0.

Injects a fake LLM (no Ollama needed) so the full pipeline runs offline:
  1. Trigger N kwarg_hallucination audit rows for `user_patterns`.
  2. Run `draft_rewrite_proposals` with a stub LLM that returns a known
     reply.
  3. Verify a markdown file lands in `vault/_kee/tool_rewrites/<date>-<tool>.md`.
  4. Verify it contains both the current AND proposed descriptions.
  5. Verify the threshold gate works (below `min_hits` -> no proposal).

Run::

    .venv\\Scripts\\python.exe tests/test_tool_evolution.py
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from pathlib import Path


class _FakeReply:
    def __init__(self, content: str) -> None:
        self.content = content


class _FakeLLM:
    """Minimal Ollama-shaped stub — only `chat()` is needed."""
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls = 0

    async def chat(self, messages, temperature=0.0, owner=None, **kw):
        self.calls += 1
        return _FakeReply(self.reply)


def _trigger_halluc(n: int, kwargs: dict) -> None:
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry(); r.load_builtins()
    for _ in range(n):
        asyncio.run(r.execute("user_patterns",
                              {"view": "summary", **kwargs}))


def _today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _proposal_path(tool: str) -> Path:
    from kee.config import settings
    return settings.vault_dir / "_kee" / "tool_rewrites" / f"{_today()}-{tool}.md"


def test_proposal_written_when_threshold_met() -> int:
    from kee.cognition import tool_evolution as te
    # Make sure no stale proposal lingers
    p = _proposal_path("user_patterns")
    if p.exists():
        p.unlink()
    _trigger_halluc(3, {"query": "x", "limit": 5})
    fake = _FakeLLM(reply="Behavioural intelligence about Coco. NOT accepted: query, limit.")
    out = asyncio.run(te.draft_rewrite_proposals(
        llm=fake, window_days=7, min_hits=2,
    ))
    if not out:
        print("  [FAIL] no proposals returned")
        return 1
    if not p.exists():
        print(f"  [FAIL] expected file missing: {p}")
        return 1
    body = p.read_text(encoding="utf-8")
    if "Proposed rewrite" not in body or "Current description" not in body:
        print("  [FAIL] proposal markdown missing sections")
        return 1
    if "NOT accepted" not in body:
        print("  [FAIL] LLM reply not present in proposal body")
        return 1
    print(f"  [ok] proposal written: {p.name} ({len(body)}b, "
          f"LLM calls={fake.calls})")
    return 0


def test_threshold_gate_blocks_low_hit_tools() -> int:
    from kee.cognition import tool_evolution as te
    fake = _FakeLLM(reply="should not be called")
    # Use a very high threshold; even with our test rows, gate trips
    out = asyncio.run(te.draft_rewrite_proposals(
        llm=fake, window_days=7, min_hits=999_999,
    ))
    if not out and fake.calls == 0:
        print("  [ok] high threshold -> no proposals, no LLM calls")
        return 0
    print(f"  [FAIL] expected zero, got {len(out)}, calls={fake.calls}")
    return 1


def test_returns_empty_on_no_hallucinations_at_all() -> int:
    """Even with no audit rows in window=0, code must not crash."""
    from kee.cognition import tool_evolution as te
    fake = _FakeLLM(reply="x")
    out = asyncio.run(te.draft_rewrite_proposals(
        llm=fake, window_days=0, min_hits=2,
    ))
    if isinstance(out, list):
        print(f"  [ok] window=0 returned list (len={len(out)}), "
              f"no exceptions")
        return 0
    print(f"  [FAIL] unexpected return type: {type(out)}")
    return 1


if __name__ == "__main__":
    print("=== tool_evolution proposal pipeline ===")
    fails = 0
    fails += test_proposal_written_when_threshold_met()
    fails += test_threshold_gate_blocks_low_hit_tools()
    fails += test_returns_empty_on_no_hallucinations_at_all()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
