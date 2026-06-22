"""worker_health tool — $0, no real network needed.

Verifies graceful behaviour when the worker is unreachable (the common
case during dev). When the worker IS up, the snapshot shape is
documented but not asserted (env-dependent).

Run::

    .venv\\Scripts\\python.exe tests/test_worker_health.py
"""

from __future__ import annotations

import asyncio
import os


def test_unreachable_returns_ok_false() -> int:
    from kee.tools.worker_health import tool
    # Force a clearly-unreachable URL so the test is deterministic.
    saved = os.environ.get("KEE_WORKER_HEALTH_URL")
    os.environ["KEE_WORKER_HEALTH_URL"] = "http://127.0.0.1:1"  # nothing here
    try:
        out = asyncio.run(tool.execute(action="snapshot", timeout_s=2))
        if (out.get("ok") is False
                and "reason" in out
                and "elapsed_ms" in out):
            print(f"  [ok] unreachable -> graceful: {out['reason']}")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        if saved is None:
            os.environ.pop("KEE_WORKER_HEALTH_URL", None)
        else:
            os.environ["KEE_WORKER_HEALTH_URL"] = saved


def test_summary_action_returns_string() -> int:
    from kee.tools.worker_health import tool
    saved = os.environ.get("KEE_WORKER_HEALTH_URL")
    os.environ["KEE_WORKER_HEALTH_URL"] = "http://127.0.0.1:1"
    try:
        out = asyncio.run(tool.execute(action="summary", timeout_s=2))
        if isinstance(out.get("summary"), str) or out.get("ok") is False:
            print(f"  [ok] summary returns shape (ok={out.get('ok')})")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        if saved is None:
            os.environ.pop("KEE_WORKER_HEALTH_URL", None)
        else:
            os.environ["KEE_WORKER_HEALTH_URL"] = saved


def test_subsystem_requires_name() -> int:
    from kee.tools.worker_health import tool
    out = asyncio.run(tool.execute(action="subsystem"))
    if not out.get("ok") and "name required" in (out.get("error") or ""):
        print("  [ok] subsystem without name returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


if __name__ == "__main__":
    print("=== worker_health tool ===")
    fails = 0
    fails += test_unreachable_returns_ok_false()
    fails += test_summary_action_returns_string()
    fails += test_subsystem_requires_name()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
