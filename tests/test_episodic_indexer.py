"""Episodic indexer source extractors — $0, no worker needed.

Tests the per-source SQL helpers (`_conversations`, `_dispatches`, etc.)
return the documented shape. Skips the embedder + chroma upsert (which
need the worker). For the live-worker path see `test_real_rag.py`.

Run::

    .venv\\Scripts\\python.exe tests/test_episodic_indexer.py
"""

from __future__ import annotations


def test_each_source_callable() -> int:
    """Every source extractor returns a list of (id, text, metadata)
    tuples without raising, even on empty windows."""
    from kee.cognition.episodic_indexer import _SOURCES
    fails = 0
    for name, fn in _SOURCES:
        try:
            rows = fn(window_days=7)
            if not isinstance(rows, list):
                print(f"  [FAIL] {name}: not a list -> {type(rows)}")
                fails += 1
                continue
            for row in rows[:2]:
                if not (isinstance(row, tuple) and len(row) == 3):
                    print(f"  [FAIL] {name}: bad row shape {row!r}")
                    fails += 1
                    break
                rid, text, meta = row
                if not (isinstance(rid, str) and isinstance(text, str)
                        and isinstance(meta, dict)):
                    print(f"  [FAIL] {name}: bad row types")
                    fails += 1
                    break
                if "kind" not in meta or "ref" not in meta or "ts" not in meta:
                    print(f"  [FAIL] {name}: meta missing kind/ref/ts: {meta}")
                    fails += 1
                    break
            else:
                print(f"  [ok] {name}: {len(rows)} rows, shape OK")
        except Exception as e:
            print(f"  [FAIL] {name}: raised {type(e).__name__}: {e}")
            fails += 1
    return fails


def test_unique_ids_within_source() -> int:
    """A single source must return unique ids (so upsert is idempotent)."""
    from kee.cognition.episodic_indexer import _SOURCES
    fails = 0
    for name, fn in _SOURCES:
        rows = fn(window_days=14)
        ids = [r[0] for r in rows]
        if len(ids) != len(set(ids)):
            dupes = [i for i in ids if ids.count(i) > 1]
            print(f"  [FAIL] {name}: duplicate ids {dupes[:3]}")
            fails += 1
        else:
            print(f"  [ok] {name}: {len(ids)} unique ids")
    return fails


def test_kind_matches_source_name() -> int:
    """Each row's metadata.kind should be a sensible label for the
    source (used as a filter in `episodic.query(kinds=…)`)."""
    from kee.cognition.episodic_indexer import _SOURCES
    expected = {
        "conversations": "conversation",
        "dispatches": "dispatch",
        "plans": "plan",
        "focus_sessions": "focus",
        "learnings": "learning",
        "notifications": "notification",
        "perception": "perception",
    }
    fails = 0
    for name, fn in _SOURCES:
        rows = fn(window_days=7)
        if not rows:
            print(f"  [SKIP] {name}: no rows in window")
            continue
        kinds = {r[2].get("kind") for r in rows}
        want = expected.get(name)
        if not kinds.issubset({want}):
            print(f"  [FAIL] {name}: expected kind={want!r}, got {kinds}")
            fails += 1
        else:
            print(f"  [ok] {name}: kind={want!r}")
    return fails


if __name__ == "__main__":
    print("=== episodic_indexer source extractors ===")
    fails = 0
    fails += test_each_source_callable()
    fails += test_unique_ids_within_source()
    fails += test_kind_matches_source_name()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
