"""Pre-validate `required` kwargs in `_filter_kwargs` — $0.

Verifies:
  - calling a tool without a `required` kwarg returns a structured error
    (ok=False) instead of raising TypeError mid-execute
  - the error names the missing argument(s)
  - a `kwarg_missing_required` audit row lands in the DB
  - tools without `required` (e.g. user_patterns) still execute normally
  - tools with `required` and the kwarg supplied still execute normally

Run::

    .venv\\Scripts\\python.exe tests/test_missing_required.py
"""

from __future__ import annotations

import asyncio


def test_missing_returns_structured_error() -> int:
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry(); r.load_builtins()
    out = asyncio.run(r.execute("memory_search", {"top_k": 3}))
    if (isinstance(out, dict)
            and out.get("ok") is False
            and "query" in (out.get("required") or [])):
        print(f"  [ok] structured error: {out['error']}")
        return 0
    print(f"  [FAIL] expected structured error, got: {out}")
    return 1


def test_audit_row_written() -> int:
    """Trigger another miss, then look it up."""
    from kee.core.tool_registry import ToolRegistry
    from kee.core import db
    r = ToolRegistry(); r.load_builtins()
    asyncio.run(r.execute("memory_search", {}))
    row = db.get_connection().execute(
        "SELECT tool_name, parameters FROM audit_log "
        "WHERE action='kwarg_missing_required' "
        "ORDER BY id DESC LIMIT 1"
    ).fetchone()
    if row and row[0] == "memory_search" and "query" in (row[1] or ""):
        print(f"  [ok] audit row: {row[0]} -> {row[1][:80]}")
        return 0
    print(f"  [FAIL] missing audit row: {row}")
    return 1


def test_no_required_tool_still_executes() -> int:
    """user_patterns has no required kwargs — must still run."""
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry(); r.load_builtins()
    out = asyncio.run(r.execute("user_patterns", {}))
    if isinstance(out, dict) and "summary" in out:
        print("  [ok] no-required tool runs normally")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_required_supplied_runs() -> int:
    """memory_search WITH `query` should attempt execution (even if RAG
    fallback says ChromaDB is offline — that's still a successful path)."""
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry(); r.load_builtins()
    out = asyncio.run(r.execute("memory_search", {"query": "test", "top_k": 1}))
    if isinstance(out, dict) and out.get("ok") is not False:
        print("  [ok] required-supplied path runs end-to-end")
        return 0
    # Acceptable degradation: ChromaDB not initialized → still not the
    # missing-required path.
    if isinstance(out, dict) and "missing required" not in str(out.get("error", "")):
        print(f"  [ok] required-supplied path bypassed missing-required check "
              f"(degraded: {out.get('error') or out.get('note')!r})")
        return 0
    print(f"  [FAIL] {out}")
    return 1


if __name__ == "__main__":
    print("=== missing-required pre-validation ===")
    fails = 0
    fails += test_missing_returns_structured_error()
    fails += test_audit_row_written()
    fails += test_no_required_tool_still_executes()
    fails += test_required_supplied_runs()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
