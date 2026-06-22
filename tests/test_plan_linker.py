"""Plan ↔ commit auto-linker — $0.

Verifies the matcher logic without depending on real git activity:
  - back-dated plans get matched against real commits in D:/Kee + sibling
    repos (this is the integration smoke test against live data)
  - empty pending pool returns 0 proposals
  - apply=False never mutates plan_history.executed
  - apply=True with strong match flips executed=1 and writes outcome
  - token filter excludes stopwords ("para", "esta", …)

Run::

    .venv\\Scripts\\python.exe tests/test_plan_linker.py
"""

from __future__ import annotations


def _seed(task: str, days_ago: int = 10) -> int:
    from kee.core import db
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO plan_history "
            "(timestamp, task, selected_json, alternatives_json) "
            f"VALUES (datetime('now', '-{int(days_ago)} days'), ?, ?, ?)",
            (task, '{"name":"A"}', '[]'),
        )
        return int(cur.lastrowid)


def _cleanup(ids: list[int]) -> None:
    from kee.core import db
    if not ids:
        return
    with db.cursor() as cur:
        cur.execute(
            f"DELETE FROM plan_history WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )


def test_token_filter_excludes_stopwords() -> int:
    from kee.cognition.plan_commit_linker import _tokens
    toks = _tokens("hablamos para esta cosa porque deberia ser rapido")
    bad = {"para", "esta", "porque", "deberia", "rapido"}
    inter = toks & bad
    if not inter:
        print(f"  [ok] stopwords filtered (kept: {sorted(toks)})")
        return 0
    print(f"  [FAIL] stopwords leaked: {inter}")
    return 1


def test_no_pending_returns_empty() -> int:
    from kee.cognition.plan_commit_linker import propose_plan_links
    # window_days=0 means no plans qualify
    out = propose_plan_links(window_days=0)
    if out.get("pending") == 0 and out.get("proposals") == []:
        print("  [ok] empty pending pool returns no proposals")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_real_match_against_repo_commits() -> int:
    """Seed a plan that mentions a real subject keyword from D:/Kee history
    and verify the linker finds the commit."""
    from kee.cognition.plan_commit_linker import propose_plan_links
    pid = _seed("ship medconcierge stripe onboarding compliance",
                days_ago=10)
    try:
        out = propose_plan_links(window_days=14, apply=False)
        proposals = out.get("proposals") or []
        match = next((p for p in proposals if p["plan_id"] == pid), None)
        if match and match["commits"]:
            print(f"  [ok] matcher found {len(match['commits'])} commit(s) "
                  f"for plan {pid}")
            return 0
        print(f"  [FAIL] no commits matched for plan {pid}: {out}")
        return 1
    finally:
        _cleanup([pid])


def test_apply_false_does_not_mutate() -> int:
    from kee.core import db
    from kee.cognition.plan_commit_linker import propose_plan_links
    pid = _seed("ship medconcierge stripe", days_ago=10)
    try:
        propose_plan_links(window_days=14, apply=False)
        row = db.get_connection().execute(
            "SELECT executed FROM plan_history WHERE id = ?", (pid,),
        ).fetchone()
        if row and row[0] == 0:
            print("  [ok] apply=False leaves plan executed=0")
            return 0
        print(f"  [FAIL] apply=False mutated: row={row}")
        return 1
    finally:
        _cleanup([pid])


def test_apply_true_flips_strong_matches() -> int:
    """A strong-match plan (≥3 matching commits) should flip to
    executed=1 with an outcome string."""
    from kee.core import db
    from kee.cognition.plan_commit_linker import propose_plan_links
    pid = _seed("ship medconcierge stripe onboarding compliance",
                days_ago=10)
    try:
        out = propose_plan_links(window_days=14, apply=True)
        applied_count = out.get("applied_count") or 0
        row = db.get_connection().execute(
            "SELECT executed, outcome FROM plan_history WHERE id = ?",
            (pid,),
        ).fetchone()
        if row and row[0] == 1 and "auto-linked" in (row[1] or ""):
            print(f"  [ok] strong match flipped to executed=1 "
                  f"(applied_count={applied_count}); outcome="
                  f"{row[1][:60]}")
            return 0
        print(f"  [FAIL] expected executed=1 with outcome; row={row}, "
              f"out={out}")
        return 1
    finally:
        _cleanup([pid])


if __name__ == "__main__":
    print("=== plan-commit linker ===")
    fails = 0
    fails += test_token_filter_excludes_stopwords()
    fails += test_no_pending_returns_empty()
    fails += test_real_match_against_repo_commits()
    fails += test_apply_false_does_not_mutate()
    fails += test_apply_true_flips_strong_matches()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
