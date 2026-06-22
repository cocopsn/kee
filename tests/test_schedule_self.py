"""schedule_self tool — $0.

Lifecycle: schedule, list, cancel, fire (heartbeat helper), history.
Uses past/future minute offsets so we can test fire timing without sleeping.

Run::

    .venv\\Scripts\\python.exe tests/test_schedule_self.py
"""

from __future__ import annotations

import asyncio


def _wipe() -> None:
    from kee.core import db
    with db.cursor() as cur:
        cur.execute("DELETE FROM scheduled_callbacks")


def test_start_creates_callback() -> int:
    from kee.tools.schedule_self import tool
    _wipe()
    out = asyncio.run(tool.execute(
        action="start", when_min=30, message="check deploy",
    ))
    if out.get("ok") and out.get("id"):
        print(f"  [ok] start created callback id={out['id']}")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_start_requires_when() -> int:
    from kee.tools.schedule_self import tool
    out = asyncio.run(tool.execute(action="start", message="x"))
    if not out.get("ok") and "when_min or at" in (out.get("error") or ""):
        print("  [ok] start without when_min/at returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_list_pending() -> int:
    from kee.tools.schedule_self import tool
    _wipe()
    asyncio.run(tool.execute(action="start", when_min=10, message="A"))
    asyncio.run(tool.execute(action="start", when_min=20, message="B"))
    out = asyncio.run(tool.execute(action="list"))
    if len(out.get("pending") or []) == 2:
        print("  [ok] list returns 2 pending callbacks")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_cancel_pending() -> int:
    from kee.tools.schedule_self import tool
    _wipe()
    started = asyncio.run(tool.execute(
        action="start", when_min=30, message="X",
    ))
    pid = started["id"]
    out = asyncio.run(tool.execute(action="cancel", id=pid))
    listing = asyncio.run(tool.execute(action="list"))
    if out.get("ok") and not listing["pending"]:
        print("  [ok] cancel removes from pending list")
        return 0
    print(f"  [FAIL] cancel={out}, listing={listing}")
    return 1


def test_fire_due_only() -> int:
    """fire_due_callbacks must only flip rows whose fire_at <= now()."""
    from kee.tools.schedule_self import tool, fire_due_callbacks
    _wipe()
    asyncio.run(tool.execute(action="start", when_min=-1, message="past"))
    asyncio.run(tool.execute(action="start", when_min=60, message="later"))
    fired = fire_due_callbacks()
    if len(fired) == 1 and fired[0]["message"] == "past":
        print(f"  [ok] only the past callback fired (id={fired[0]['id']})")
        return 0
    print(f"  [FAIL] fired: {fired}")
    return 1


def test_history_shows_fired() -> int:
    from kee.tools.schedule_self import tool, fire_due_callbacks
    _wipe()
    asyncio.run(tool.execute(action="start", when_min=-1, message="X"))
    fire_due_callbacks()
    hist = asyncio.run(tool.execute(action="history"))
    callbacks = hist.get("callbacks") or []
    if callbacks and callbacks[0]["fired"]:
        print("  [ok] history surfaces fired callback")
        return 0
    print(f"  [FAIL] {hist}")
    return 1


if __name__ == "__main__":
    print("=== schedule_self ===")
    fails = 0
    fails += test_start_creates_callback()
    fails += test_start_requires_when()
    fails += test_list_pending()
    fails += test_cancel_pending()
    fails += test_fire_due_only()
    fails += test_history_shows_fired()
    _wipe()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
