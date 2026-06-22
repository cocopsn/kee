"""Self-editing codebase daemon.

Phase 7 §"Self-editing codebase daemon". Strictly *propose-only*: Kee
never edits its own source unattended. The flow is:

    1. `analyze_recent_runtime()` reads `audit_log` + `anomalies` for
       the last 7 days, picks the top friction (most-failed tool, the
       slowest LLM call, recurrent rollback flags, etc.).
    2. The LLM (free Ollama tier — this never spends $) drafts ONE
       markdown proposal explaining: what the symptom is, which file is
       likely to blame, a one-paragraph fix outline. ≤ 1 proposal/day.
    3. Proposal is written to
       `vault/_kee/code_proposals/<YYYY-MM-DD>.md`, hash-tracked so the
       same friction doesn't get re-proposed daily.
    4. Coco reads it. If he wants Kee to *actually* try it, he calls
       `apply_via_claude_code(date)` — that spawns the existing
       `claude_code` tool against the proposal text in a new git branch
       (`kee/proposal-<date>`). The diff lands as a PR via the existing
       `github` tool. Coco reviews + merges.

What this *deliberately* does NOT do:

    - No direct writes to `kee/**/*.py`.
    - No git commits without `apply_via_claude_code` being called.
    - No fork bombs: dedup by content hash so re-running today doesn't
      pile up duplicate proposals.
    - No paid LLM by default. The proposal LLM is Ollama (free); the
      *application* (claude_code) is opt-in and uses Coco's Pro/Max
      subscription.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Optional

from kee.config import settings
from kee.core import db
from kee.core.ollama_client import OllamaClient

logger = logging.getLogger(__name__)

PROPOSALS_DIR_NAME = "code_proposals"


def proposals_dir() -> Path:
    p = settings.vault_dir / "_kee" / PROPOSALS_DIR_NAME
    p.mkdir(parents=True, exist_ok=True)
    return p


# ── Friction detection ───────────────────────────────────────────────────
def analyze_recent_runtime(window_days: int = 7) -> dict[str, Any]:
    """Walk the audit_log + anomalies tables and pull out the most painful
    spots for the LLM to reason about.

    Returns a dict with: ``failing_tools`` (name → fail_count),
    ``slow_llm_calls`` (rows over the p95 latency), ``rollback_flags``
    (anomalies where Kee triggered a rollback), and ``error_types``.
    """
    conn = db.get_connection()
    cur = conn.cursor()
    since = (datetime.utcnow() - timedelta(days=window_days)).isoformat()

    failing: Counter = Counter()
    error_types: Counter = Counter()
    cur.execute("""
        SELECT tool_name, success, error
        FROM audit_log
        WHERE timestamp >= ? AND tool_name IS NOT NULL
    """, (since,))
    for tool, ok, err in cur.fetchall():
        if not ok:
            failing[tool] += 1
            if err:
                # bucket by exception class
                m = re.match(r"([A-Z][a-zA-Z_]+(?:Error|Exception)?)", str(err))
                if m:
                    error_types[m.group(1)] += 1

    slow: list[dict] = []
    try:
        cur.execute("""
            SELECT tool_name, model, latency_ms, tier, timestamp
            FROM audit_log
            WHERE latency_ms IS NOT NULL AND timestamp >= ?
            ORDER BY latency_ms DESC LIMIT 5
        """, (since,))
        for row in cur.fetchall():
            slow.append({
                "tool_name": row[0], "model": row[1], "latency_ms": row[2],
                "tier": row[3], "timestamp": row[4],
            })
    except Exception:
        pass

    rollbacks: list[dict] = []
    try:
        cur.execute("""
            SELECT timestamp, tool_name, description
            FROM anomalies
            WHERE timestamp >= ? AND description LIKE '%rollback%'
            LIMIT 10
        """, (since,))
        for row in cur.fetchall():
            rollbacks.append({"timestamp": row[0], "tool_name": row[1],
                              "description": row[2]})
    except Exception:
        pass

    return {
        "window_days": window_days,
        "failing_tools": failing.most_common(10),
        "error_types": error_types.most_common(5),
        "slow_llm_calls": slow,
        "rollback_flags": rollbacks,
    }


# ── Proposal drafting ────────────────────────────────────────────────────
PROMPT_TEMPLATE = """\
You are reviewing 7 days of operational data for an autonomous AI agent
named Kee. Your job: propose ONE small, actionable code change that
would meaningfully reduce friction.

Constraints:
- Single change. Do NOT propose architectural overhauls.
- Identify one likely-responsible file in the kee/ package and explain why.
- Output a short markdown proposal. NO code blocks, NO patches —
  just a clear human-readable rationale + 3 step plan.
- If the data shows nothing notable, output exactly: NO_PROPOSAL_TODAY.

Operational data (last 7 days):

{data_block}

Repository layout (top-level):
- kee/core/        agent, db, memory, identity, scheduler, verify
- kee/tools/       built-in tools (shell, files, web, claude_code, …)
- kee/perception/  voice, ambient_sound, heartbeat, filesystem watcher
- kee/cognition/   sleep_cycle, world_model, planner, self_healing
- kee/surfaces/    api, telegram, terminal
- kee/distributed/ chroma, embedder, indexer, fleet
- kee/daemon/      supervisor, autostart, tray

Output the proposal now (markdown, ≤ 250 words):
"""


def _hash_summary(s: str) -> str:
    return hashlib.sha1(s.encode("utf-8")).hexdigest()[:10]


async def draft_proposal(window_days: int = 7,
                         llm: Optional[OllamaClient] = None) -> dict[str, Any]:
    """Generate a markdown proposal from the last `window_days` of
    runtime data. Returns ``{ok, path, hash, dedup, body, friction}``.

    Idempotent for a given day: if today's proposal already exists with
    the same content hash, returns ``dedup=True`` without re-writing.
    """
    friction = analyze_recent_runtime(window_days=window_days)
    summary_str = json.dumps(friction, indent=2, default=str)
    fhash = _hash_summary(summary_str)

    today = datetime.now().strftime("%Y-%m-%d")
    proposal_path = proposals_dir() / f"{today}.md"

    # Dedup: if today's file exists and embeds the same friction hash,
    # skip — prevents the daemon from generating duplicates within a day.
    if proposal_path.exists():
        existing = proposal_path.read_text(encoding="utf-8")
        if f"<!-- friction-hash: {fhash} -->" in existing:
            return {"ok": True, "dedup": True, "path": str(proposal_path),
                    "hash": fhash, "friction": friction}

    # Empty workload? Kick out cleanly.
    if (not friction["failing_tools"]
        and not friction["rollback_flags"]
        and not friction["slow_llm_calls"]):
        return {"ok": False, "reason": "no friction signal in window",
                "friction": friction}

    llm = llm or OllamaClient()
    prompt = PROMPT_TEMPLATE.format(data_block=summary_str)
    try:
        # OllamaClient.chat returns a dict {role, content, tool_calls?}
        msg = await llm.chat(
            messages=[{"role": "user", "content": prompt}],
            tools=None,
        )
        resp = msg.get("content") if isinstance(msg, dict) else str(msg)
    except Exception as e:
        return {"ok": False, "reason": f"LLM error: {e}", "friction": friction}
    body = (resp or "").strip()
    if not body or "NO_PROPOSAL_TODAY" in body.upper():
        return {"ok": False, "reason": "LLM declined (no_proposal_today)",
                "friction": friction}

    # Wrap with metadata header
    full = (
        f"# Code proposal — {today}\n\n"
        f"<!-- friction-hash: {fhash} -->\n"
        f"<!-- generated-at: {datetime.utcnow().isoformat()}Z -->\n"
        f"<!-- window-days: {window_days} -->\n\n"
        f"## Friction observed\n\n```json\n{summary_str}\n```\n\n"
        f"## Kee's proposal\n\n{body}\n\n"
        f"---\n\n"
        f"*To attempt this fix automatically, call*\n"
        f"`POST /self_evolution/proposals/{today}/apply` *— it will spawn "
        f"`claude_code` against this proposal text in a new git branch and "
        f"open a PR via the github tool. Direct review/edit before merging.*\n"
    )
    proposal_path.write_text(full, encoding="utf-8")
    logger.info("Code proposal written: %s (hash=%s)", proposal_path, fhash)
    return {"ok": True, "dedup": False, "path": str(proposal_path),
            "hash": fhash, "body": body, "friction": friction}


# ── Apply via claude_code (opt-in, real cost) ────────────────────────────
async def apply_via_claude_code(proposal_date: str) -> dict[str, Any]:
    """Spawn claude_code against the proposal markdown.

    The claude_code tool already creates an isolated workspace, so the
    LLM gets a fresh checkout of Kee to edit. The branch name is
    deterministic so re-applying on the same date overwrites instead of
    forking history.
    """
    p = proposals_dir() / f"{proposal_date}.md"
    if not p.exists():
        return {"ok": False, "error": f"no proposal at {p}"}
    proposal_text = p.read_text(encoding="utf-8")
    # Lazy import to avoid pulling claude_code's deps at module load
    from kee.tools.claude_code import ClaudeCodeTool
    tool = ClaudeCodeTool()
    task = (
        f"You are Kee improving your own code. Read the proposal below and "
        f"implement it cleanly in the kee/ package. Touch only files clearly "
        f"implicated. Do NOT change Phase boundaries. Add tests if you can. "
        f"Branch: kee/proposal-{proposal_date}. Commit, push, open PR.\n\n"
        f"=== PROPOSAL ===\n{proposal_text}\n=== END ===\n"
    )
    try:
        result = await tool.execute(task=task)
        return {"ok": True, "claude_code_result": result, "proposal_path": str(p)}
    except Exception as e:
        return {"ok": False, "error": str(e), "proposal_path": str(p)}


# ── Listing / reading ────────────────────────────────────────────────────
def list_proposals(limit: int = 30) -> list[dict[str, Any]]:
    base = proposals_dir()
    out: list[dict[str, Any]] = []
    for p in sorted(base.glob("*.md"), reverse=True)[:limit]:
        try:
            text = p.read_text(encoding="utf-8")
        except Exception:
            continue
        m = re.search(r"<!-- friction-hash: (\w+) -->", text)
        applied = "<!-- applied-at:" in text
        out.append({
            "date": p.stem,
            "path": str(p),
            "bytes": len(text),
            "friction_hash": m.group(1) if m else None,
            "applied": applied,
        })
    return out


def read_proposal(proposal_date: str) -> dict[str, Any]:
    p = proposals_dir() / f"{proposal_date}.md"
    if not p.exists():
        return {"exists": False, "date": proposal_date}
    return {
        "exists": True, "date": proposal_date, "path": str(p),
        "body": p.read_text(encoding="utf-8"),
    }
