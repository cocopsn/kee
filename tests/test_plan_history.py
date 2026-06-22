"""End-to-end test for plan_history persistence + planner actions — $0.

Covers the full flow without calling Ollama:
  1. Insert a plan via the internal `_persist_plan` helper.
  2. Verify `history`, `recall`, `mark_executed` all work through the tool.
  3. Verify Sleep Cycle's `_phase_stats` exposes plans_total / plans_executed.
  4. Verify the API endpoint returns the row.

Run::

    .venv\\Scripts\\python.exe tests/test_plan_history.py
"""

from __future__ import annotations

import asyncio


def _seed_plan(task: str = "ship landing TEST_xyz") -> int:
    from kee.tools.planner import _persist_plan
    pid = _persist_plan(
        task=task,
        context="unit test",
        selected={"name": "Approach A", "score": 9.0},
        alternatives=[{"name": "Approach B", "score": 6.0}],
        world_entity=None,
        world_impact=None,
    )
    return int(pid) if pid else -1


def _cleanup(ids: list[int]) -> None:
    from kee.core import db
    if not ids:
        return
    with db.cursor() as cur:
        cur.execute(
            f"DELETE FROM plan_history WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )


def test_history_action() -> int:
    from kee.tools.planner import tool
    pid = _seed_plan()
    try:
        out = asyncio.run(tool.execute(action="history", limit=3))
        plans = out.get("plans") or []
        if any(p["id"] == pid for p in plans):
            print(f"  [ok] history surfaces plan id={pid}")
            return 0
        print(f"  [FAIL] plan {pid} missing from history: {plans}")
        return 1
    finally:
        _cleanup([pid])


def test_recall_action() -> int:
    from kee.tools.planner import tool
    pid = _seed_plan(task="recall test target Z9X")
    try:
        out = asyncio.run(tool.execute(action="recall", query="Z9X"))
        plans = out.get("plans") or []
        if out["count"] >= 1 and plans[0]["id"] == pid:
            print(f"  [ok] recall finds plan by substring (count={out['count']})")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        _cleanup([pid])


def test_mark_executed_action() -> int:
    from kee.tools.planner import tool, _list_history
    pid = _seed_plan()
    try:
        res = asyncio.run(tool.execute(
            action="mark_executed", plan_id=pid, outcome="shipped",
        ))
        if not res.get("ok"):
            print(f"  [FAIL] mark_executed: {res}")
            return 1
        rows = _list_history(limit=5, executed=True)
        if any(r["id"] == pid and r["executed"] and r["outcome"] == "shipped"
               for r in rows):
            print(f"  [ok] plan {pid} flipped to executed=True with outcome")
            return 0
        print(f"  [FAIL] expected executed row, got: {rows}")
        return 1
    finally:
        _cleanup([pid])


def test_pending_only_filter() -> int:
    from kee.tools.planner import tool
    pid = _seed_plan()
    try:
        out = asyncio.run(tool.execute(
            action="history", pending_only=True, limit=10,
        ))
        plans = out.get("plans") or []
        if any(p["id"] == pid and not p["executed"] for p in plans):
            print("  [ok] pending_only filter includes unexecuted plan")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        _cleanup([pid])


def test_sleep_cycle_stats_count_plans() -> int:
    from kee.cognition.sleep_cycle import SleepCycleDaemon
    from kee.core.memory import MemoryManager
    from kee.core.ollama_client import OllamaClient
    from kee.core.audit import AuditLogger
    pid = _seed_plan()
    try:
        d = SleepCycleDaemon(memory=MemoryManager(), llm=OllamaClient(),
                             audit=AuditLogger())
        stats = d._phase_stats(hours=24 * 7)
        if (stats.get("plans_total") or 0) >= 1:
            print(f"  [ok] sleep_cycle stats: plans_total="
                  f"{stats['plans_total']}, executed="
                  f"{stats['plans_executed']}, "
                  f"rate={stats['plan_execution_rate']}")
            return 0
        print(f"  [FAIL] sleep_cycle stats missing plans: {stats}")
        return 1
    finally:
        _cleanup([pid])


def test_api_endpoint_lists_plans() -> int:
    from kee.surfaces.api import plans_recent
    pid = _seed_plan()
    try:
        out = asyncio.run(plans_recent(limit=5))
        plans = out.get("plans") or []
        if any(p["id"] == pid for p in plans):
            print(f"  [ok] /plans/recent surfaces plan id={pid}")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        _cleanup([pid])


if __name__ == "__main__":
    print("=== plan_history persistence ===")
    fails = 0
    fails += test_history_action()
    fails += test_recall_action()
    fails += test_mark_executed_action()
    fails += test_pending_only_filter()
    fails += test_sleep_cycle_stats_count_plans()
    fails += test_api_endpoint_lists_plans()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
