"""End-to-end smoke for the kwarg-hallucination feedback loop — $0.

Pipeline tested:
  1. Call a tool with bogus kwargs through the registry.
  2. Verify a `kwarg_hallucination` row landed in `audit_log`.
  3. Verify `tool_reliability` exposes it (`hallucinated_kwargs`).
  4. Verify `SleepCycleDaemon._phase_stats` rolls it up.
  5. Verify the `/system/hallucinations` endpoint surfaces it.

Run::

    .venv\\Scripts\\python.exe tests/test_hallucination_loop.py
"""

from __future__ import annotations

import asyncio


def _trigger_hallucination() -> None:
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.load_builtins()
    asyncio.run(r.execute("user_patterns",
                          {"view": "summary",
                           "fake_kwarg_xyz": "abc",
                           "another_bogus": 99}))


def test_audit_row_written() -> int:
    _trigger_hallucination()
    from kee.core import db
    con = db.get_connection()
    row = con.execute(
        "SELECT tool_name, parameters FROM audit_log "
        "WHERE action='kwarg_hallucination' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row and row[0] == "user_patterns" and "fake_kwarg_xyz" in (row[1] or ""):
        print(f"  [ok] hallucination audit row: {row[0]} -> {row[1][:80]}")
        return 0
    print(f"  [FAIL] expected user_patterns row, got: {row}")
    return 1


def test_tool_reliability_picks_it_up() -> int:
    from kee.tools.tool_reliability import tool
    out = asyncio.run(tool.execute(window_days=14, tool="user_patterns"))
    rows = out.get("tools") or []
    if not rows:
        print("  [FAIL] tool_reliability returned no rows for user_patterns")
        return 1
    h = rows[0].get("hallucinated_kwargs") or []
    names = {k for k, _ in h}
    if "fake_kwarg_xyz" in names and "another_bogus" in names:
        print(f"  [ok] tool_reliability surfaces hallucinated kwargs: "
              f"{sorted(names)[:4]}")
        return 0
    print(f"  [FAIL] hallucinated_kwargs missing expected names: {h}")
    return 1


def test_sleep_cycle_stats_include_hallucinations() -> int:
    from kee.cognition.sleep_cycle import SleepCycleDaemon
    from kee.core.memory import MemoryManager
    from kee.core.ollama_client import OllamaClient
    from kee.core.audit import AuditLogger
    d = SleepCycleDaemon(memory=MemoryManager(), llm=OllamaClient(),
                         audit=AuditLogger())
    stats = d._phase_stats(hours=24 * 7)
    n = stats.get("kwarg_hallucinations", 0) or 0
    per = stats.get("kwarg_hallucinations_per_tool") or {}
    if n >= 1 and per.get("user_patterns", 0) >= 1:
        print(f"  [ok] sleep_cycle stats: hallucinations={n}, "
              f"user_patterns={per['user_patterns']}")
        return 0
    print(f"  [FAIL] sleep_cycle stats missing hallucinations: {n=}, {per=}")
    return 1


def test_system_hallucinations_endpoint() -> int:
    from kee.surfaces.api import system_hallucinations
    out = asyncio.run(system_hallucinations(window_days=14))
    tools = out.get("tools") or []
    if any(t.get("tool") == "user_patterns" and t.get("count", 0) >= 1
           for t in tools):
        print(f"  [ok] /system/hallucinations surfaces user_patterns "
              f"(total={out.get('total')})")
        return 0
    print(f"  [FAIL] endpoint payload missing user_patterns: {out}")
    return 1


if __name__ == "__main__":
    print("=== kwarg-hallucination feedback loop ===")
    fails = 0
    fails += test_audit_row_written()
    fails += test_tool_reliability_picks_it_up()
    fails += test_sleep_cycle_stats_include_hallucinations()
    fails += test_system_hallucinations_endpoint()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
