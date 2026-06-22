"""projects tool — $0.

Smoke test against the live `vault/projects/` directory (always present
on this dev box). Verifies basic shape; doesn't assert specific slugs
because the project set evolves.

Run::

    .venv\\Scripts\\python.exe tests/test_projects.py
"""

from __future__ import annotations

import asyncio


def test_list_returns_projects() -> int:
    from kee.tools.projects import tool
    out = asyncio.run(tool.execute(action="list"))
    if out.get("ok") and isinstance(out.get("projects"), list):
        n = len(out["projects"])
        if n >= 1:
            print(f"  [ok] list returned {n} projects")
            return 0
    print(f"  [FAIL] {out}")
    return 1


def test_get_unknown_slug_errors() -> int:
    from kee.tools.projects import tool
    out = asyncio.run(tool.execute(
        action="get", slug="__definitely_not_a_real_project_xyz_456",
    ))
    if not out.get("ok") and "no project" in (out.get("error") or ""):
        print("  [ok] unknown slug returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_search_substring() -> int:
    from kee.tools.projects import tool
    # Search for something likely to exist in the project set
    out = asyncio.run(tool.execute(action="search", query="git", limit=3))
    if out.get("ok") and "hits" in out:
        print(f"  [ok] search returned shape (hits={out['count']})")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_stats_returns_metadata() -> int:
    from kee.tools.projects import tool
    listing = asyncio.run(tool.execute(action="list"))
    projects = listing.get("projects") or []
    if not projects:
        print("  [SKIP] no projects in vault")
        return 0
    slug = projects[0]["slug"]
    stats = asyncio.run(tool.execute(action="stats", slug=slug))
    if stats.get("ok") and "bytes" in stats and "lines" in stats:
        print(f"  [ok] stats for {slug}: {stats['bytes']}b, "
              f"{stats['lines']}L")
        return 0
    print(f"  [FAIL] {stats}")
    return 1


def test_append_and_cleanup() -> int:
    """Append a unique block, verify it's there, then remove it via
    direct file edit so we don't leave test residue."""
    from kee.tools.projects import tool, _slug_to_path
    listing = asyncio.run(tool.execute(action="list"))
    projects = listing.get("projects") or []
    if not projects:
        print("  [SKIP] no projects")
        return 0
    slug = projects[0]["slug"]
    p = _slug_to_path(slug)
    saved = p.read_text(encoding="utf-8")
    try:
        marker = "__test_append_marker_xyz_789__"
        out = asyncio.run(tool.execute(
            action="append", slug=slug, note=marker,
        ))
        body = p.read_text(encoding="utf-8")
        if out.get("ok") and marker in body:
            print(f"  [ok] append wrote {out['appended_bytes']}b to {slug}")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        # Restore original content
        p.write_text(saved, encoding="utf-8")


if __name__ == "__main__":
    print("=== projects tool ===")
    fails = 0
    fails += test_list_returns_projects()
    fails += test_get_unknown_slug_errors()
    fails += test_search_substring()
    fails += test_stats_returns_metadata()
    fails += test_append_and_cleanup()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
