"""Smoke test for the `user_patterns` tool — no LLM, no network, $0.

Just exercises every `view` against the live SQLite db and confirms the
tool returns a dict with the expected top-level keys. Doesn't assert on
data shape because audit_log content is environment-dependent.

Run::

    .venv\\Scripts\\python.exe tests/test_user_patterns.py
"""

from __future__ import annotations

import asyncio


def _run(view: str) -> dict:
    from kee.tools.user_patterns import tool
    return asyncio.run(tool.execute(view=view, window_days=14))


def test_summary() -> int:
    out = _run("summary")
    if isinstance(out, dict) and "summary" in out:
        print(f"  [ok] summary view: {out['summary'][:80]}")
        return 0
    print("  [FAIL] summary view shape:", out)
    return 1


def test_peak() -> int:
    out = _run("peak")
    if isinstance(out, dict) and ("hours" in out or out.get("no_data")):
        print("  [ok] peak view returns hours[] or no_data")
        return 0
    print("  [FAIL] peak view shape:", out)
    return 1


def test_tools() -> int:
    out = _run("tools")
    if isinstance(out, dict) and "top_tools" in out:
        print(f"  [ok] tools view: {len(out['top_tools'])} tools listed")
        return 0
    print("  [FAIL] tools view shape:", out)
    return 1


def test_surfaces() -> int:
    out = _run("surfaces")
    if isinstance(out, dict) and "by_source" in out:
        print(f"  [ok] surfaces view: {len(out['by_source'])} sources")
        return 0
    print("  [FAIL] surfaces view shape:", out)
    return 1


def test_cost() -> int:
    out = _run("cost")
    if isinstance(out, dict) and "days" in out and "total_cost_usd" in out:
        print(f"  [ok] cost view: ${out['total_cost_usd']} over "
              f"{len(out['days'])}d")
        return 0
    print("  [FAIL] cost view shape:", out)
    return 1


def test_axioms() -> int:
    out = _run("axioms")
    if isinstance(out, dict) and "current_axioms" in out:
        print(f"  [ok] axioms view: {len(out['current_axioms'])} axioms")
        return 0
    print("  [FAIL] axioms view shape:", out)
    return 1


def test_unknown_view() -> int:
    out = _run("does_not_exist")
    if isinstance(out, dict) and out.get("ok") is False:
        print("  [ok] unknown view rejected gracefully")
        return 0
    print("  [FAIL] unknown view did not return error:", out)
    return 1


if __name__ == "__main__":
    print("=== user_patterns tool ===")
    fails = 0
    fails += test_summary()
    fails += test_peak()
    fails += test_tools()
    fails += test_surfaces()
    fails += test_cost()
    fails += test_axioms()
    fails += test_unknown_view()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
