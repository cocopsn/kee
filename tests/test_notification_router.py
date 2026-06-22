"""Smart notification router — $0.

Pure decision-table tests, no actual notification sent.

Run::

    .venv\\Scripts\\python.exe tests/test_notification_router.py
"""

from __future__ import annotations

from datetime import datetime


def _at(hour: int) -> datetime:
    return datetime(2026, 5, 4, hour, 0, 0)


def test_critical_always_all() -> int:
    from kee.perception.notification_router import decide
    cases = [(3, "quiet hours"), (10, "morning"), (23, "late night")]
    fails = 0
    for h, label in cases:
        d = decide("critical", now=_at(h))
        if d.desktop and d.telegram:
            print(f"  [ok] critical at {h:02d}h ({label}) -> both channels")
        else:
            fails += 1
            print(f"  [FAIL] critical at {h:02d}h: {d}")
    return fails


def test_low_quiet_silent() -> int:
    from kee.perception.notification_router import decide
    d = decide("low", now=_at(3))
    if not d.desktop and not d.telegram:
        print("  [ok] low + quiet hours -> silent")
        return 0
    print(f"  [FAIL] {d}")
    return 1


def test_low_awake_desktop_only() -> int:
    from kee.perception.notification_router import decide
    d = decide("low", now=_at(10))
    if d.desktop and not d.telegram:
        print("  [ok] low + awake -> desktop only")
        return 0
    print(f"  [FAIL] {d}")
    return 1


def test_normal_quiet_telegram_only() -> int:
    from kee.perception.notification_router import decide
    d = decide("normal", now=_at(3))
    if not d.desktop and d.telegram:
        print("  [ok] normal + quiet -> telegram only")
        return 0
    print(f"  [FAIL] {d}")
    return 1


def test_normal_awake_both() -> int:
    from kee.perception.notification_router import decide
    d = decide("normal", now=_at(10))
    if d.desktop and d.telegram:
        print("  [ok] normal + awake -> both")
        return 0
    print(f"  [FAIL] {d}")
    return 1


def test_focus_offtopic_downgrades() -> int:
    """If a focus session is active on project X and notification is about
    Y, normal urgency should downgrade to telegram only."""
    from kee.core import db
    from kee.tools.focus import tool as focus_tool
    from kee.perception.notification_router import decide
    import asyncio
    # Start a focus session
    with db.cursor() as cur:
        cur.execute("DELETE FROM focus_sessions")
    asyncio.run(focus_tool.execute(action="start", project="auctorum"))
    try:
        d = decide("normal", project_hint="random_other_project",
                   now=_at(10))
        if not d.desktop and d.telegram and "telegram only" in d.reason:
            print(f"  [ok] focus on auctorum + msg about other -> "
                  f"telegram only ({d.reason[:60]})")
            return 0
        print(f"  [FAIL] {d}")
        return 1
    finally:
        asyncio.run(focus_tool.execute(action="end"))
        with db.cursor() as cur:
            cur.execute("DELETE FROM focus_sessions")


def test_dnd_gaming_silences_low() -> int:
    """When the active window matches a DND pattern, low urgency must
    drop both channels (no telegram either)."""
    from kee.perception import notification_router as nr
    # Monkeypatch the window detector
    saved = nr._active_window_dnd
    nr._active_window_dnd = lambda: (True, "cities skylines")
    try:
        d = nr.decide("low", now=_at(20))
        if not d.desktop and not d.telegram and d.dnd_window:
            print("  [ok] DND + low -> silent")
            return 0
        # Strip non-ASCII for cp1252 console safety
        clean = d.reason.encode("ascii", "ignore").decode("ascii")
        print(f"  [FAIL] {d.dnd_window=}, reason={clean!r}")
        return 1
    finally:
        nr._active_window_dnd = saved


def test_dnd_gaming_normal_telegram_only() -> int:
    """Normal urgency during DND keeps telegram so Coco sees it later."""
    from kee.perception import notification_router as nr
    saved = nr._active_window_dnd
    nr._active_window_dnd = lambda: (True, "gta v")
    try:
        d = nr.decide("normal", now=_at(20))
        if not d.desktop and d.telegram and d.dnd_window:
            print("  [ok] DND + normal -> telegram only")
            return 0
        print(f"  [FAIL] {d}")
        return 1
    finally:
        nr._active_window_dnd = saved


def test_dnd_critical_overrides() -> int:
    """Critical must escalate even during gaming."""
    from kee.perception import notification_router as nr
    saved = nr._active_window_dnd
    nr._active_window_dnd = lambda: (True, "cities skylines")
    try:
        d = nr.decide("critical", now=_at(20))
        if d.desktop and d.telegram:
            print("  [ok] DND + critical -> both (override)")
            return 0
        print(f"  [FAIL] {d}")
        return 1
    finally:
        nr._active_window_dnd = saved


def test_quiet_window_env_override() -> int:
    """Custom KEE_QUIET_HOURS=22-8 should silence low urgency at 23:00."""
    import os
    from kee.perception.notification_router import decide
    saved = os.environ.get("KEE_QUIET_HOURS")
    os.environ["KEE_QUIET_HOURS"] = "22-8"
    try:
        d = decide("low", now=_at(23))
        if not d.desktop and not d.telegram and d.quiet:
            print("  [ok] env override 22-8 silences low at 23h")
            return 0
        print(f"  [FAIL] {d}")
        return 1
    finally:
        if saved is None:
            os.environ.pop("KEE_QUIET_HOURS", None)
        else:
            os.environ["KEE_QUIET_HOURS"] = saved


if __name__ == "__main__":
    print("=== notification_router ===")
    fails = 0
    fails += test_critical_always_all()
    fails += test_low_quiet_silent()
    fails += test_low_awake_desktop_only()
    fails += test_normal_quiet_telegram_only()
    fails += test_normal_awake_both()
    fails += test_focus_offtopic_downgrades()
    fails += test_dnd_gaming_silences_low()
    fails += test_dnd_gaming_normal_telegram_only()
    fails += test_dnd_critical_overrides()
    fails += test_quiet_window_env_override()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
