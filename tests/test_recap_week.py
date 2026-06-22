"""recap_week tool — $0, no worker needed.

Verifies the 7-day rollup returns the documented shape and the markdown
formatter handles both empty and non-empty weeks.

Run::

    .venv\\Scripts\\python.exe tests/test_recap_week.py
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta


def test_basic_shape() -> int:
    from kee.tools.recap_week import tool
    out = asyncio.run(tool.execute())
    needed = {"ok", "week_start", "week_end", "elapsed_ms",
              "week_totals", "markdown"}
    if not (out.get("ok") and needed.issubset(out.keys())):
        print(f"  [FAIL] missing keys: {needed - set(out.keys())}")
        return 1
    print(f"  [ok] shape OK ({out['week_start']} -> {out['week_end']})")
    return 0


def test_week_span_is_7_days() -> int:
    """week_end should be today, week_start should be 6 days earlier."""
    from kee.tools.recap_week import tool
    out = asyncio.run(tool.execute())
    today = date.today()
    expected_start = (today - timedelta(days=6)).isoformat()
    expected_end = today.isoformat()
    if out["week_start"] == expected_start and out["week_end"] == expected_end:
        print(f"  [ok] span is 7 days exactly")
        return 0
    print(f"  [FAIL] start={out['week_start']} (want {expected_start}) "
          f"end={out['week_end']} (want {expected_end})")
    return 1


def test_markdown_has_header() -> int:
    """Markdown must include the week heading and totals section."""
    from kee.tools.recap_week import tool
    out = asyncio.run(tool.execute())
    md = out.get("markdown") or ""
    if md.startswith("# Semana ") and ("## Totales" in md
                                        or "Nada registrado" in md):
        print(f"  [ok] markdown well-formed ({len(md)} chars)")
        return 0
    print(f"  [FAIL] header missing in:\n{md[:200]}")
    return 1


def test_totals_is_dict() -> int:
    from kee.tools.recap_week import tool
    out = asyncio.run(tool.execute())
    if isinstance(out.get("week_totals"), dict):
        n = sum(out["week_totals"].values())
        print(f"  [ok] week_totals is dict (sum={n})")
        return 0
    print(f"  [FAIL] week_totals={type(out.get('week_totals'))}")
    return 1


def test_save_to_vault() -> int:
    from kee.tools.recap_week import tool
    from kee.config import settings
    out = asyncio.run(tool.execute(save_to_vault=True))
    p = settings.vault_dir / "_kee" / "daily" / f"{date.today().isoformat()}-week.md"
    try:
        if out.get("saved_to") == str(p) and p.exists():
            print(f"  [ok] saved to {p.name}")
            return 0
        print(f"  [FAIL] saved_to={out.get('saved_to')}, exists={p.exists()}")
        return 1
    finally:
        try:
            p.unlink()
        except Exception:
            pass


if __name__ == "__main__":
    print("=== recap_week tool ===")
    fails = 0
    fails += test_basic_shape()
    fails += test_week_span_is_7_days()
    fails += test_markdown_has_header()
    fails += test_totals_is_dict()
    fails += test_save_to_vault()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
