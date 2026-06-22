"""Smoke test for the `quality_snapshot` tool — no LLM, no network, $0.

Drives a few replies through `conversation_monitor.observe()` then asks the
tool for the rolling snapshot. Confirms shape + that observed scores
actually feed through.

Run::

    .venv\\Scripts\\python.exe tests/test_quality_snapshot.py
"""

from __future__ import annotations

import asyncio


def _reset_monitor() -> None:
    """Clear the singleton's deque so each test starts clean."""
    from kee.cognition import conversation_monitor as cm
    cm._MONITOR._samples.clear()


def test_empty_snapshot() -> int:
    from kee.tools.quality_snapshot import tool
    _reset_monitor()
    out = asyncio.run(tool.execute(include_recent=True))
    if out.get("count") == 0 and out.get("avg_score") is None:
        print("  [ok] empty snapshot has count=0, avg_score=None")
        return 0
    print("  [FAIL] empty snapshot wrong shape:", out)
    return 1


def test_snapshot_with_samples() -> int:
    from kee.cognition import conversation_monitor as cm
    from kee.tools.quality_snapshot import tool
    _reset_monitor()
    cm.observe("Listo. Eventos cargados.", source="voice", expected_lang="es")
    cm.observe("Como una IA, no puedo responder eso.", source="voice")
    out = asyncio.run(tool.execute(include_recent=True))
    if out["count"] == 2 and out["avg_score"] is not None:
        print(f"  [ok] 2 samples observed: avg={out['avg_score']}")
        return 0
    print("  [FAIL] snapshot did not pick up observed samples:", out)
    return 1


def test_summary_string() -> int:
    from kee.cognition import conversation_monitor as cm
    from kee.tools.quality_snapshot import tool
    _reset_monitor()
    cm.observe("Listo.", source="voice")
    out = asyncio.run(tool.execute(include_recent=False))
    if isinstance(out.get("summary"), str) and "samples" in out["summary"]:
        print(f"  [ok] summary string: {out['summary']}")
        return 0
    print("  [FAIL] summary missing or wrong:", out)
    return 1


def test_include_recent_flag() -> int:
    from kee.cognition import conversation_monitor as cm
    from kee.tools.quality_snapshot import tool
    _reset_monitor()
    cm.observe("Listo.", source="voice")
    out = asyncio.run(tool.execute(include_recent=False))
    if "recent" not in out:
        print("  [ok] include_recent=False omits raw samples")
        return 0
    print("  [FAIL] include_recent=False did not strip recent:", out)
    return 1


if __name__ == "__main__":
    print("=== quality_snapshot tool ===")
    fails = 0
    fails += test_empty_snapshot()
    fails += test_snapshot_with_samples()
    fails += test_summary_string()
    fails += test_include_recent_flag()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
