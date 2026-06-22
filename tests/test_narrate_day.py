"""narrate_day tool — $0, no worker needed.

Verifies the per-source SQL extractors return the documented shape and
the markdown formatter handles empty / non-empty days gracefully.

Run::

    .venv\\Scripts\\python.exe tests/test_narrate_day.py
"""

from __future__ import annotations

import asyncio
from datetime import date, timedelta


def test_today_returns_shape() -> int:
    from kee.tools.narrate_day import tool
    out = asyncio.run(tool.execute(date="today"))
    needed = {"ok", "date", "elapsed_ms", "counts", "markdown", "raw"}
    if not (out.get("ok") and needed.issubset(out.keys())):
        print(f"  [FAIL] missing keys: {needed - set(out.keys())}")
        return 1
    print(f"  [ok] today shape OK (counts={out['counts']})")
    return 0


def test_yesterday_alias() -> int:
    from kee.tools.narrate_day import tool, _parse_target
    out = asyncio.run(tool.execute(date="yesterday"))
    expected = (date.today() - timedelta(days=1)).isoformat()
    if out["date"] == expected:
        print(f"  [ok] yesterday alias resolves to {expected}")
        return 0
    print(f"  [FAIL] expected {expected}, got {out['date']}")
    return 1


def test_iso_date() -> int:
    from kee.tools.narrate_day import tool
    out = asyncio.run(tool.execute(date="2020-01-15"))
    if out.get("ok") and out["date"] == "2020-01-15":
        print(f"  [ok] ISO date accepted (counts={out['counts']})")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_bad_date() -> int:
    from kee.tools.narrate_day import tool
    out = asyncio.run(tool.execute(date="not-a-date"))
    if not out.get("ok") and "bad date" in (out.get("error") or ""):
        print("  [ok] bad date returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_empty_day_no_crash() -> int:
    """Year 2010 likely has no events — the markdown formatter must
    handle that without crashing (single-line "Nada registrado")."""
    from kee.tools.narrate_day import tool
    out = asyncio.run(tool.execute(date="2010-06-15"))
    if (out.get("ok") and isinstance(out["markdown"], str)
            and len(out["markdown"]) > 0):
        print(f"  [ok] empty day produces non-empty markdown "
              f"({len(out['markdown'])} chars)")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_save_to_vault() -> int:
    """save_to_vault=True should write to vault/_kee/daily/<date>-narrative.md."""
    from kee.tools.narrate_day import tool
    from kee.config import settings
    out = asyncio.run(tool.execute(date="2020-01-15", save_to_vault=True))
    p = settings.vault_dir / "_kee" / "daily" / "2020-01-15-narrative.md"
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
    print("=== narrate_day tool ===")
    fails = 0
    fails += test_today_returns_shape()
    fails += test_yesterday_alias()
    fails += test_iso_date()
    fails += test_bad_date()
    fails += test_empty_day_no_crash()
    fails += test_save_to_vault()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
