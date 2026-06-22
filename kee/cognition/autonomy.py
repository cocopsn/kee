"""Dynamic Autonomy Threshold — v2 §III Gap 11.

`identity.md` defines four static autonomy levels (0=read-free, 1=log,
2=log+notify, 3=ask). They're useful but rigid: a Level 1 tool that
fails 4 times in a row should temporarily get Level 2 treatment until
it earns trust back.

This module tracks *per-tool confidence* over a rolling window of recent
calls. The agent (or a future check in the verification loop) consults
`recommended_threshold(tool_name)` and either auto-runs or pauses for
confirmation.

Public API:
  * `record(tool_name, risk_level, success, user_corrected=False)` — log a sample
  * `confidence(tool_name, window=20)` — float 0..1 = recent success rate
  * `recommended_threshold(tool_name, base_risk)` — int 0..3, possibly higher than `base_risk`
  * `summary(window=50)` — overview for /audit-style display

Heuristic:
  - confidence >= 0.85 → use base risk as-is
  - 0.5 <= confidence < 0.85 → bump risk by 1
  - confidence < 0.5 → bump risk by 2 (capped at 3)
  - any user_corrected=True in the last 5 → bump by 1 minimum
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from kee.core import db


_MAX_RISK = 3


def wilson_lower_bound(successes: int, total: int, *, z: float = 1.96) -> float:
    """One-sided Wilson lower bound on the success proportion at ~95% CI.

    Why not raw `successes/total`? At low N the point estimate is wildly
    over-confident — 1/1=1.0 means "100% reliable" only if you ignore the
    sample size. Wilson lower bound penalises low N (1/1 → 0.21, 9/10 → 0.60,
    50/50 → 0.93) so the autonomy threshold doesn't trust new tools too fast.

    See: Edwin B. Wilson, JASA 1927; Reddit / Hacker News rank by lower bound.
    """
    if total <= 0:
        return 0.0
    phat = successes / total
    z2 = z * z
    denom = 1.0 + z2 / total
    center = (phat + z2 / (2 * total)) / denom
    margin = (z * math.sqrt((phat * (1 - phat) + z2 / (4 * total)) / total)) / denom
    return max(0.0, min(1.0, center - margin))


def record(
    tool_name: str,
    risk_level: int,
    success: bool,
    user_corrected: bool = False,
) -> int:
    with db.cursor() as cur:
        cur.execute(
            "INSERT INTO confidence_log (tool_name, risk_level, success, user_corrected) "
            "VALUES (?, ?, ?, ?)",
            (tool_name, int(risk_level), int(bool(success)), int(bool(user_corrected))),
        )
        return cur.lastrowid


def confidence(tool_name: str, window: int = 20) -> dict[str, Any]:
    """Return success ratio + corrections over the last `window` calls.

    Includes Wilson-CI lower bound (`trust_score`) so callers can rank tools
    by trust — not just raw success rate, which over-weights tiny samples.
    """
    with db.cursor() as cur:
        cur.execute(
            "SELECT success, user_corrected FROM confidence_log "
            "WHERE tool_name = ? ORDER BY id DESC LIMIT ?",
            (tool_name, window),
        )
        rows = cur.fetchall()
    if not rows:
        return {"tool_name": tool_name, "samples": 0, "success_rate": None,
                "trust_score": None, "recent_corrections": 0}
    successes = sum(int(r["success"]) for r in rows)
    corrections = sum(int(r["user_corrected"]) for r in rows[:5])
    n = len(rows)
    return {
        "tool_name": tool_name,
        "samples": n,
        "success_rate": round(successes / n, 3),
        "trust_score": round(wilson_lower_bound(successes, n), 3),
        "recent_corrections": corrections,
    }


def recommended_threshold(tool_name: str, base_risk: int) -> dict[str, Any]:
    """Decide whether to use `base_risk` or escalate based on recent history.

    Now keyed off Wilson `trust_score` instead of raw success_rate so a tool
    with 1/1=1.0 (small sample) doesn't get the same trust as 50/50=1.0.
    """
    base_risk = max(0, min(_MAX_RISK, int(base_risk)))
    info = confidence(tool_name)
    samples = info["samples"]
    rate = info["success_rate"]
    trust = info.get("trust_score")
    corrections = info["recent_corrections"]

    bump = 0
    reason = "default"
    if samples == 0 or trust is None:
        bump = 0
        reason = "no history yet"
    elif trust >= 0.75:
        bump = 0
        reason = f"trusted (trust={trust}, rate={rate}, n={samples})"
    elif trust >= 0.40:
        bump = 1
        reason = f"shaky (trust={trust}, rate={rate}, n={samples})"
    else:
        bump = 2
        reason = f"untrusted (trust={trust}, rate={rate}, n={samples})"

    if corrections > 0:
        bump = max(bump, 1)
        reason = f"recent user correction (count={corrections})"

    final_risk = min(_MAX_RISK, base_risk + bump)
    requires_confirmation = final_risk >= 3
    return {
        "tool_name": tool_name,
        "base_risk": base_risk,
        "final_risk": final_risk,
        "bumped_by": bump,
        "reason": reason,
        "samples": samples,
        "success_rate": rate,
        "trust_score": trust,
        "requires_confirmation": requires_confirmation,
    }


def summary(window: int = 50) -> dict[str, Any]:
    """Per-tool snapshot for the dashboard."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT tool_name FROM confidence_log "
            "ORDER BY tool_name ASC"
        )
        names = [r["tool_name"] for r in cur.fetchall()]
    return {
        "tool_count": len(names),
        "tools": [confidence(n, window) for n in names],
    }
