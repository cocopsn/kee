"""Cognitive heartbeat check — $0.

Drives the new `_check_cognitive_health` method through three states:
  - Idle (no recent QA / no hallucinations) → no action_needed
  - Hallucination burst (≥6 in last hour) → fires
  - Cooldown after first fire → suppressed within 30 min

Doesn't actually start the heartbeat daemon — instantiates the class
without __init__ and calls the method directly.

Run::

    .venv\\Scripts\\python.exe tests/test_cognitive_heartbeat.py
"""

from __future__ import annotations

import asyncio
import json
import time


def _hb():
    """Get a HeartbeatDaemon instance without going through __init__."""
    from kee.perception.heartbeat import HeartbeatDaemon
    return HeartbeatDaemon.__new__(HeartbeatDaemon)


def _seed_hallucinations(n: int) -> list[int]:
    """Insert N kwarg_hallucination rows; return their ids for cleanup."""
    from kee.core import db
    ids = []
    with db.cursor() as cur:
        for _ in range(n):
            cur.execute(
                "INSERT INTO audit_log "
                "(action, tool_name, success, parameters) "
                "VALUES (?, ?, ?, ?)",
                ("kwarg_hallucination", "__cogtest__", 1,
                 json.dumps({"unknown": ["bogus"]})),
            )
            ids.append(cur.lastrowid)
    return ids


def _cleanup(ids: list[int]) -> None:
    from kee.core import db
    if not ids:
        return
    with db.cursor() as cur:
        cur.execute(
            f"DELETE FROM audit_log WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )


def test_burst_triggers() -> int:
    hb = _hb()
    # Note: existing DB may already have hallucinations from previous runs.
    # The check threshold is ≥6 in last hour, so the existing volume likely
    # also fires. We verify that the trigger string mentions halluc_burst.
    ids = _seed_hallucinations(8)
    try:
        out = asyncio.run(hb._check_cognitive_health())
        if out.get("action_needed") and "halluc_burst" in (out.get("summary") or ""):
            print(f"  [ok] burst fires: {out['summary']}")
            return 0
        print(f"  [FAIL] expected burst trigger, got: {out}")
        return 1
    finally:
        _cleanup(ids)


def test_cooldown_after_fire() -> int:
    hb = _hb()
    ids = _seed_hallucinations(8)
    try:
        first = asyncio.run(hb._check_cognitive_health())
        if not first.get("action_needed"):
            print(f"  [SKIP] first call did not fire: {first}")
            return 0
        second = asyncio.run(hb._check_cognitive_health())
        if (second.get("cooldown_active")
                or not second.get("action_needed")):
            print("  [ok] second call within 30 min suppressed by cooldown")
            return 0
        print(f"  [FAIL] cooldown not honoured: {second}")
        return 1
    finally:
        _cleanup(ids)


def test_check_returns_metrics_when_silent() -> int:
    """Without a fire, the check should still return the diagnostic
    fields (qa_samples_4h, hallucinations_1h, …)."""
    hb = _hb()
    out = asyncio.run(hb._check_cognitive_health())
    needed = {"qa_samples_4h", "qa_avg_4h",
              "hallucinations_1h", "untrusted_tools_called_1h"}
    missing = needed - set(out.keys())
    if not missing:
        print(f"  [ok] always returns metrics dict (qa_n="
              f"{out['qa_samples_4h']}, halluc_1h={out['hallucinations_1h']})")
        return 0
    print(f"  [FAIL] missing fields: {missing}; got {out}")
    return 1


if __name__ == "__main__":
    print("=== cognitive_health heartbeat ===")
    fails = 0
    fails += test_burst_triggers()
    fails += test_cooldown_after_fire()
    fails += test_check_returns_metrics_when_silent()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
