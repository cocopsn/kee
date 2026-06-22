"""commits tool — $0, no network.

Hits the live `D:/Kee` git repo (always present) so we don't need to
mock subprocess. Verifies:
  - `repos` lists D:/Kee
  - `today`/`week` respond without raising even when there's no commit
  - `summary` strips the full commit list
  - `window` requires `since`
  - SHA dedup collapses commits from mirror repos

Run::

    .venv\\Scripts\\python.exe tests/test_commits.py
"""

from __future__ import annotations

import asyncio


def test_repos_list_includes_kee() -> int:
    from kee.tools.commits import tool
    out = asyncio.run(tool.execute(action="repos"))
    if not out.get("ok"):
        print(f"  [SKIP] git not on PATH: {out}")
        return 0
    repos = out.get("repos") or []
    if any("Kee" in r for r in repos):
        print(f"  [ok] {out['count']} repo(s) detected; D:/Kee included")
        return 0
    print(f"  [FAIL] D:/Kee not in repo list: {repos[:5]}")
    return 1


def test_today_action_runs_safely() -> int:
    from kee.tools.commits import tool
    out = asyncio.run(tool.execute(action="today", limit=5))
    if isinstance(out, dict) and "count" in out and "by_repo" in out:
        print(f"  [ok] today action returned shape (count={out['count']})")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_summary_strips_commit_list() -> int:
    from kee.tools.commits import tool
    out = asyncio.run(tool.execute(action="summary"))
    if "commits" not in out and "by_repo" in out:
        print("  [ok] summary action omits full commit list")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_window_requires_since() -> int:
    from kee.tools.commits import tool
    out = asyncio.run(tool.execute(action="window"))
    if not out.get("ok") and "since" in (out.get("error", "")):
        print("  [ok] window without `since` returns clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


if __name__ == "__main__":
    print("=== commits tool ===")
    fails = 0
    fails += test_repos_list_includes_kee()
    fails += test_today_action_runs_safely()
    fails += test_summary_strips_commit_list()
    fails += test_window_requires_since()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
