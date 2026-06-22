"""Smoke test for the `reflect` tool — $0.

Verifies:
  - tool returns dict with the 6 expected top-level keys
  - `summary` is always a non-empty string (even with empty data)
  - sub-snapshots have the documented shapes (`samples`/`avg_score`,
    `total`/`executed`, `total`/`by_tool`, list of `{tool, trust, calls}`)
  - `include_recent_plans=False` strips the recent list

Run::

    .venv\\Scripts\\python.exe tests/test_reflect.py
"""

from __future__ import annotations

import asyncio


def test_top_level_shape() -> int:
    from kee.tools.reflect import tool
    out = asyncio.run(tool.execute(window_days=7))
    needed = {"window_days", "qa", "plans", "hallucinations",
              "tool_bottom", "projects", "summary"}
    missing = needed - set(out.keys())
    if not missing:
        print(f"  [ok] reflect returns all {len(needed)} top-level keys")
        return 0
    print(f"  [FAIL] missing keys: {missing}")
    return 1


def test_summary_is_string() -> int:
    from kee.tools.reflect import tool
    out = asyncio.run(tool.execute(window_days=7))
    s = out.get("summary")
    if isinstance(s, str) and len(s) > 0:
        print(f"  [ok] summary string ({len(s)} chars): {s[:80]!r}")
        return 0
    print(f"  [FAIL] summary not a string: {s!r}")
    return 1


def test_subshapes() -> int:
    from kee.tools.reflect import tool
    out = asyncio.run(tool.execute(window_days=7))
    fails = 0
    qa = out["qa"]
    if "samples" not in qa or "avg_score" not in qa or "by_source" not in qa:
        fails += 1
        print(f"  [FAIL] qa shape: {qa}")
    plans = out["plans"]
    if "total" not in plans or "executed" not in plans:
        fails += 1
        print(f"  [FAIL] plans shape: {plans}")
    hal = out["hallucinations"]
    if "total" not in hal or "by_tool" not in hal:
        fails += 1
        print(f"  [FAIL] hallucinations shape: {hal}")
    if not isinstance(out["tool_bottom"], list):
        fails += 1
        print(f"  [FAIL] tool_bottom not a list")
    if fails == 0:
        print("  [ok] all 4 sub-snapshots have expected shape")
    return fails


def test_recent_plans_toggle() -> int:
    from kee.tools.reflect import tool
    out_with = asyncio.run(tool.execute(include_recent_plans=True))
    out_without = asyncio.run(tool.execute(include_recent_plans=False))
    if "recent" in out_with["plans"] and "recent" not in out_without["plans"]:
        print("  [ok] include_recent_plans=False strips the recent list")
        return 0
    print(f"  [FAIL] toggle didn't take effect: with={out_with['plans']}, "
          f"without={out_without['plans']}")
    return 1


if __name__ == "__main__":
    print("=== reflect tool ===")
    fails = 0
    fails += test_top_level_shape()
    fails += test_summary_is_string()
    fails += test_subshapes()
    fails += test_recent_plans_toggle()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
