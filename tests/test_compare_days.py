"""compare_days tool — $0, no worker needed.

Verifies date parsing, the diff math, and the markdown formatter.

Run::

    .venv\\Scripts\\python.exe tests/test_compare_days.py
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta


def test_basic_shape() -> int:
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="yesterday", date_b="today"))
    needed = {"ok", "date_a", "date_b", "counts_a", "counts_b",
              "delta", "markdown"}
    if not (out.get("ok") and needed.issubset(out.keys())):
        print(f"  [FAIL] missing keys: {needed - set(out.keys())}")
        return 1
    print(f"  [ok] shape OK ({out['date_a']} vs {out['date_b']})")
    return 0


def test_relative_offsets() -> int:
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="-3", date_b="-1"))
    expected_a = (date.today() - timedelta(days=3)).isoformat()
    expected_b = (date.today() - timedelta(days=1)).isoformat()
    if out["date_a"] == expected_a and out["date_b"] == expected_b:
        print(f"  [ok] -N parses ({expected_a} vs {expected_b})")
        return 0
    print(f"  [FAIL] got {out['date_a']} vs {out['date_b']}")
    return 1


def test_iso_dates() -> int:
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="2020-01-15",
                                    date_b="2020-01-16"))
    if out.get("ok") and out["date_a"] == "2020-01-15":
        print("  [ok] ISO dates accepted")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_bad_date() -> int:
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="not-a-date", date_b="today"))
    if not out.get("ok") and "bad date" in (out.get("error") or ""):
        print("  [ok] bad date returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_delta_math() -> int:
    """Same date on both sides → all deltas should be 0."""
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="today", date_b="today"))
    deltas = out.get("delta") or {}
    if all(v == 0 for v in deltas.values()):
        print(f"  [ok] same-day delta is all zeros ({len(deltas)} cats)")
        return 0
    nonzero = {k: v for k, v in deltas.items() if v != 0}
    print(f"  [FAIL] non-zero deltas for same date: {nonzero}")
    return 1


def test_markdown_has_table() -> int:
    """Markdown must contain header row and column separators."""
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="yesterday", date_b="today"))
    md = out.get("markdown") or ""
    has_header = "| Category |" in md
    has_sep = "|---|" in md
    if has_header and has_sep:
        print(f"  [ok] markdown table well-formed ({len(md)} chars)")
        return 0
    print(f"  [FAIL] header={has_header} sep={has_sep}")
    return 1


def test_empty_days_no_crash() -> int:
    """Two ancient dates with no data should still produce valid output."""
    from kee.tools.compare_days import tool
    out = asyncio.run(tool.execute(date_a="2010-01-01",
                                    date_b="2010-01-02"))
    if out.get("ok") and isinstance(out.get("markdown"), str):
        print("  [ok] empty days produce valid markdown")
        return 0
    print(f"  [FAIL] {out}")
    return 1


if __name__ == "__main__":
    print("=== compare_days tool ===")
    fails = 0
    fails += test_basic_shape()
    fails += test_relative_offsets()
    fails += test_iso_dates()
    fails += test_bad_date()
    fails += test_delta_math()
    fails += test_markdown_has_table()
    fails += test_empty_days_no_crash()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
