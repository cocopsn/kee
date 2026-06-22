"""learn tool — $0.

Lifecycle: record, recall (substring), reinforce (bumps count), forget
(soft delete), top (sorted by reinforced desc).

Run::

    .venv\\Scripts\\python.exe tests/test_learn.py
"""

from __future__ import annotations

import asyncio


def _wipe() -> None:
    from kee.core import db
    with db.cursor() as cur:
        cur.execute("DELETE FROM learnings")


def test_record_creates() -> int:
    from kee.tools.learn import tool
    _wipe()
    out = asyncio.run(tool.execute(
        action="record", topic="test_topic_xyz",
        content="some durable knowledge",
    ))
    if out.get("ok") and out.get("id"):
        print(f"  [ok] record id={out['id']}")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_record_requires_both() -> int:
    from kee.tools.learn import tool
    out = asyncio.run(tool.execute(action="record", topic="only_topic"))
    if not out.get("ok") and "required" in (out.get("error") or ""):
        print("  [ok] record requires both topic + content")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_recall_substring() -> int:
    from kee.tools.learn import tool
    _wipe()
    asyncio.run(tool.execute(action="record",
                             topic="npm path", content="use D:/node-globals"))
    asyncio.run(tool.execute(action="record",
                             topic="vad model", content="silero h+c separate"))
    out = asyncio.run(tool.execute(action="recall", query="silero"))
    if out["count"] == 1 and "vad" in out["learnings"][0]["topic"]:
        print("  [ok] recall finds by content substring")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_reinforce_bumps() -> int:
    from kee.tools.learn import tool
    _wipe()
    started = asyncio.run(tool.execute(
        action="record", topic="x", content="y",
    ))
    pid = started["id"]
    asyncio.run(tool.execute(action="reinforce", id=pid))
    asyncio.run(tool.execute(action="reinforce", id=pid))
    top = asyncio.run(tool.execute(action="top"))
    if top["learnings"][0]["reinforced"] == 3:
        print("  [ok] 2 reinforces -> 3 (initial 1 + 2 bumps)")
        return 0
    print(f"  [FAIL] {top}")
    return 1


def test_forget_soft_deletes() -> int:
    from kee.tools.learn import tool
    _wipe()
    started = asyncio.run(tool.execute(
        action="record", topic="x", content="y",
    ))
    pid = started["id"]
    asyncio.run(tool.execute(action="forget", id=pid))
    listing = asyncio.run(tool.execute(action="list"))
    if not listing["learnings"]:
        print("  [ok] forget removes from active list (soft delete)")
        return 0
    print(f"  [FAIL] {listing}")
    return 1


def test_top_sorts_by_reinforced() -> int:
    from kee.tools.learn import tool
    _wipe()
    a = asyncio.run(tool.execute(action="record", topic="A", content="a"))
    b = asyncio.run(tool.execute(action="record", topic="B", content="b"))
    # Reinforce B 3 times so it ranks above A
    for _ in range(3):
        asyncio.run(tool.execute(action="reinforce", id=b["id"]))
    top = asyncio.run(tool.execute(action="top"))
    if top["learnings"][0]["topic"] == "B" and top["learnings"][0]["reinforced"] == 4:
        print("  [ok] top ranks B (reinforced=4) above A (reinforced=1)")
        return 0
    print(f"  [FAIL] {top}")
    return 1


if __name__ == "__main__":
    print("=== learn tool ===")
    fails = 0
    fails += test_record_creates()
    fails += test_record_requires_both()
    fails += test_recall_substring()
    fails += test_reinforce_bumps()
    fails += test_forget_soft_deletes()
    fails += test_top_sorts_by_reinforced()
    _wipe()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
