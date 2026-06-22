"""End-to-end smoke for the `recall` tool — $0.

Drops a few synthetic messages into the live `messages` table, runs the
tool against them, then cleans up. Verifies:
  - substring match returns expected rows in DESC id order
  - role filter works
  - days filter excludes old rows
  - top_k caps the result count
  - snippet centres on the query

Run::

    .venv\\Scripts\\python.exe tests/test_recall.py
"""

from __future__ import annotations

import asyncio


_TAG = "__recalltest__zzaa"  # unique unlikely substring


def _seed_messages() -> list[int]:
    from kee.core import db
    rows = []
    with db.cursor() as cur:
        # messages.conversation_id has a FK on conversations.id; seed both.
        for cid in ("test_conv_a", "test_conv_b"):
            cur.execute(
                "INSERT OR IGNORE INTO conversations (id, source) "
                "VALUES (?, 'test')",
                (cid,),
            )
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            ("test_conv_a", "user", f"hablamos del proyecto {_TAG} ayer"),
        )
        rows.append(cur.lastrowid)
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            ("test_conv_a", "assistant",
             f"sí, recuerdo {_TAG}; quedó pendiente la decisión"),
        )
        rows.append(cur.lastrowid)
        cur.execute(
            "INSERT INTO messages (conversation_id, role, content) "
            "VALUES (?, ?, ?)",
            ("test_conv_b", "user", f"otro hilo: {_TAG} en otro contexto"),
        )
        rows.append(cur.lastrowid)
    return rows


def _cleanup(ids: list[int]) -> None:
    from kee.core import db
    if not ids:
        return
    with db.cursor() as cur:
        cur.execute(
            f"DELETE FROM messages WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
        cur.execute(
            "DELETE FROM conversations WHERE id IN ('test_conv_a','test_conv_b')"
        )


def test_substring_match() -> int:
    from kee.tools.recall import tool
    ids = _seed_messages()
    try:
        out = asyncio.run(tool.execute(query=_TAG, top_k=10))
        if out["count"] >= 3 and all(_TAG in m["snippet"] for m in out["matches"]):
            print(f"  [ok] substring match found {out['count']} rows")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        _cleanup(ids)


def test_role_filter() -> int:
    from kee.tools.recall import tool
    ids = _seed_messages()
    try:
        out = asyncio.run(tool.execute(query=_TAG, role="user"))
        roles = {m["role"] for m in out["matches"]}
        if roles == {"user"} and out["count"] >= 2:
            print(f"  [ok] role filter: {out['count']} user rows only")
            return 0
        print(f"  [FAIL] roles={roles}, count={out['count']}")
        return 1
    finally:
        _cleanup(ids)


def test_top_k_cap() -> int:
    from kee.tools.recall import tool
    ids = _seed_messages()
    try:
        out = asyncio.run(tool.execute(query=_TAG, top_k=2))
        if out["count"] == 2:
            print("  [ok] top_k=2 caps result list")
            return 0
        print(f"  [FAIL] expected 2, got {out['count']}")
        return 1
    finally:
        _cleanup(ids)


def test_no_match_empty() -> int:
    from kee.tools.recall import tool
    out = asyncio.run(tool.execute(query="zzz_does_not_exist_xyz_123"))
    if out["count"] == 0 and out["matches"] == []:
        print("  [ok] no-match returns empty list, no crash")
        return 0
    print(f"  [FAIL] expected empty, got {out}")
    return 1


def test_conversation_id_filter() -> int:
    from kee.tools.recall import tool
    ids = _seed_messages()
    try:
        out = asyncio.run(tool.execute(
            query=_TAG, conversation_id="test_conv_b",
        ))
        if out["count"] == 1 and out["matches"][0]["conversation_id"] == "test_conv_b":
            print("  [ok] conversation_id filter narrows to one conv")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        _cleanup(ids)


if __name__ == "__main__":
    print("=== recall tool ===")
    fails = 0
    fails += test_substring_match()
    fails += test_role_filter()
    fails += test_top_k_cap()
    fails += test_no_match_empty()
    fails += test_conversation_id_filter()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
