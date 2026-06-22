"""Plan ↔ commit linker — proposes plan executions from real git activity.

Sleep Cycle (or anyone) calls `propose_plan_links()`. We:

  1. Pull pending plans from `plan_history` (executed = 0, recent N days).
  2. Pull commits across Coco's repos in the same window.
  3. For each pending plan, score commits by token overlap on the longest
     significant words from `task` (length ≥ 5). Threshold: ≥1 token match
     AND commit timestamp ≥ plan.timestamp.
  4. Return a list of `{plan_id, plan_task, commits: [{sha, subject, repo}]}`
     proposals. **Never auto-marks**: the agent or Coco decides.

Optional `apply=True` flips the plans to executed=1 with outcome
`"linked: {N} commit(s) across {repos}"` — only use this when the agent
is sure (e.g. matched ≥3 commits with a strong unique token).

Risk: 0 (read-only by default; opt-in mutation).
"""

from __future__ import annotations

import logging
import re
from collections import Counter
from typing import Any

from kee.core import db

logger = logging.getLogger(__name__)


_STOP = {
    "para", "esto", "esta", "estos", "estas", "como", "cuando", "donde",
    "porque", "pero", "todos", "todas", "tienes", "tiene", "puede",
    "puedes", "deberia", "necesito", "favor", "rapido", "ahora", "auctorum",
    # english stopwords too — pending plans may be mixed-language
    "with", "from", "that", "this", "have", "should", "their", "would",
    "could", "about", "make", "build", "ship",
}


def _tokens(s: str) -> set[str]:
    """Significant words (≥5 chars, alpha) for matching, lowercased,
    with a small stopword filter. Keeps 'auctorum' OUT since it's so
    common it would match everything."""
    if not s:
        return set()
    return {
        w for w in re.findall(r"[a-zA-ZáéíóúñÁÉÍÓÚÑ]{5,}", s.lower())
        if w not in _STOP
    }


def _pending_plans(window_days: int) -> list[dict]:
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, timestamp, task FROM plan_history "
        "WHERE executed = 0 "
        "AND timestamp >= datetime('now', ? || ' days') "
        "ORDER BY id ASC",
        (f"-{int(window_days)}",),
    ).fetchall()
    return [{"id": r[0], "timestamp": r[1], "task": r[2]} for r in rows]


def propose_plan_links(
    window_days: int = 14,
    min_token_overlap: int = 1,
    apply: bool = False,
) -> dict[str, Any]:
    """Walk pending plans, score against recent commits, return proposals."""
    plans = _pending_plans(window_days=window_days)
    if not plans:
        return {"window_days": window_days, "pending": 0, "proposals": []}

    # Pull all commits in the window from the existing tool's helpers.
    try:
        from kee.tools.commits import _find_repos, _git_log, _DEFAULT_ROOTS
    except Exception as e:
        return {"error": f"commits helpers unavailable: {e}"}

    repos = _find_repos(_DEFAULT_ROOTS, max_depth=2)
    all_commits: list[dict] = []
    since = f"{int(window_days)} days ago"
    for repo in repos:
        all_commits.extend(_git_log(repo, since=since))
    if not all_commits:
        return {"window_days": window_days, "pending": len(plans),
                "proposals": [], "note": "no commits in window"}

    proposals: list[dict[str, Any]] = []
    for plan in plans:
        plan_tokens = _tokens(plan["task"])
        if not plan_tokens:
            continue
        plan_ts = str(plan["timestamp"])
        matches: list[dict] = []
        for c in all_commits:
            # Only commits that landed AFTER the plan was created
            if str(c.get("ts", "")) <= plan_ts:
                continue
            commit_tokens = _tokens(c.get("subject", ""))
            overlap = plan_tokens & commit_tokens
            if len(overlap) >= int(min_token_overlap):
                matches.append({
                    "sha": c["sha"], "subject": c["subject"],
                    "repo": c["repo"], "ts": c.get("ts"),
                    "matched_tokens": sorted(overlap),
                })
        if not matches:
            continue
        proposal: dict[str, Any] = {
            "plan_id": plan["id"],
            "plan_task": plan["task"],
            "commits": matches[:5],
            "match_count": len(matches),
        }
        # Optional auto-apply when the match is unambiguous (≥3 matches OR
        # any single match with ≥3 token overlap).
        if apply:
            strong = (len(matches) >= 3
                      or any(len(m["matched_tokens"]) >= 3 for m in matches))
            if strong:
                _apply(plan["id"], matches)
                proposal["applied"] = True
        proposals.append(proposal)
    return {
        "window_days": window_days,
        "pending": len(plans),
        "proposals": proposals,
        "applied_count": sum(1 for p in proposals if p.get("applied")),
    }


def _apply(plan_id: int, matches: list[dict]) -> None:
    """Flip plan to executed=1 with a deterministic outcome string."""
    repos = sorted({m["repo"] for m in matches})
    outcome = (f"auto-linked: {len(matches)} commit(s) across "
               f"{','.join(repos)} (sample: {matches[0]['sha']} "
               f"{matches[0]['subject'][:60]})")
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE plan_history SET executed = 1, "
                "executed_at = CURRENT_TIMESTAMP, outcome = ? "
                "WHERE id = ?",
                (outcome, int(plan_id)),
            )
    except Exception as e:
        logger.warning("plan_commit_linker: apply failed for %s: %s",
                       plan_id, e)
