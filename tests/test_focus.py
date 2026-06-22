"""focus tool — $0.

Lifecycle: start, current, drift, end, history. Plus single-active
invariant (start auto-closes the previous open session).

Run::

    .venv\\Scripts\\python.exe tests/test_focus.py
"""

from __future__ import annotations

import asyncio


def _wipe() -> None:
    from kee.core import db
    with db.cursor() as cur:
        cur.execute("DELETE FROM focus_sessions")


def test_start_creates_session() -> int:
    from kee.tools.focus import tool
    _wipe()
    out = asyncio.run(tool.execute(action="start", project="kee_test",
                                   intent="run unit test"))
    if out.get("ok") and out.get("project") == "kee_test":
        print(f"  [ok] start created session id={out['id']}")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_current_returns_active() -> int:
    from kee.tools.focus import tool
    _wipe()
    asyncio.run(tool.execute(action="start", project="kee_test"))
    out = asyncio.run(tool.execute(action="current"))
    active = out.get("active") or {}
    if active.get("project") == "kee_test" and active.get("ended_at") is None:
        print("  [ok] current returns the open session")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_start_auto_closes_previous() -> int:
    from kee.tools.focus import tool
    _wipe()
    first = asyncio.run(tool.execute(action="start", project="A"))
    second = asyncio.run(tool.execute(action="start", project="B"))
    if (second.get("auto_closed_id") == first["id"]
            and second.get("project") == "B"):
        print("  [ok] start auto-closes previous session")
        return 0
    print(f"  [FAIL] first={first}, second={second}")
    return 1


def test_drift_increments_count() -> int:
    from kee.tools.focus import tool
    _wipe()
    asyncio.run(tool.execute(action="start", project="kee_test"))
    asyncio.run(tool.execute(action="drift", reason="x"))
    asyncio.run(tool.execute(action="drift", reason="y"))
    out = asyncio.run(tool.execute(action="current"))
    active = out.get("active") or {}
    if active.get("drift_count") == 2:
        print("  [ok] drift bumps count to 2")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_end_with_outcome() -> int:
    from kee.tools.focus import tool
    _wipe()
    asyncio.run(tool.execute(action="start", project="kee_test"))
    end = asyncio.run(tool.execute(action="end", outcome="finished"))
    cur = asyncio.run(tool.execute(action="current"))
    if end.get("ok") and (cur.get("active") is None):
        print("  [ok] end closes session, current returns None")
        return 0
    print(f"  [FAIL] end={end}, cur={cur}")
    return 1


def test_end_without_active_errors() -> int:
    from kee.tools.focus import tool
    _wipe()
    out = asyncio.run(tool.execute(action="end"))
    if not out.get("ok") and "no active" in (out.get("error") or ""):
        print("  [ok] end without active session returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_history_lists_recent() -> int:
    from kee.tools.focus import tool
    _wipe()
    for p in ("A", "B", "C"):
        asyncio.run(tool.execute(action="start", project=p))
    asyncio.run(tool.execute(action="end", outcome="last"))
    out = asyncio.run(tool.execute(action="history", limit=10))
    sessions = out.get("sessions") or []
    if len(sessions) == 3:
        print("  [ok] history returns 3 sessions")
        return 0
    print(f"  [FAIL] expected 3, got {len(sessions)}")
    return 1


if __name__ == "__main__":
    print("=== focus tool ===")
    fails = 0
    fails += test_start_creates_session()
    fails += test_current_returns_active()
    fails += test_start_auto_closes_previous()
    fails += test_drift_increments_count()
    fails += test_end_with_outcome()
    fails += test_end_without_active_errors()
    fails += test_history_lists_recent()
    # Final cleanup so we don't leave state for other suites.
    _wipe()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
