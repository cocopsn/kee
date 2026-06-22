"""Daily cost tracker + kill switch.

Sums every paid provider call from `audit_log.cost_usd` for the current
local day. When daily total reaches the configured cap, `kill_switch_active`
returns True and the chain forces a downgrade to the local-only provider
(Ollama) until midnight rolls over.

Cap is $2/day by default — configurable via KEE_DAILY_COST_CAP_USD.
Soft warning at 80% of cap (returns `near_cap=True` for UI to surface).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any

from kee.core import db

logger = logging.getLogger(__name__)


def _cap_usd() -> float:
    try:
        return float(os.environ.get("KEE_DAILY_COST_CAP_USD", "2.0"))
    except ValueError:
        return 2.0


def _day_start_str(now: datetime | None = None) -> str:
    """SQLite stores `CURRENT_TIMESTAMP` as 'YYYY-MM-DD HH:MM:SS' (space
    separator, not ISO 'T'). Match that format for valid range comparison."""
    now = now or datetime.now()
    return now.strftime("%Y-%m-%d 00:00:00")


def daily_total_usd(now: datetime | None = None) -> float:
    """Sum of cost_usd across all audit_log rows from local-day-start."""
    try:
        con = db.get_connection()
        row = con.execute(
            "SELECT COALESCE(SUM(cost_usd), 0) FROM audit_log "
            "WHERE timestamp >= ? AND cost_usd IS NOT NULL",
            (_day_start_str(now),),
        ).fetchone()
        return float(row[0]) if row else 0.0
    except Exception as e:
        logger.warning("daily_total_usd failed: %s", e)
        return 0.0


def kill_switch_active() -> bool:
    return daily_total_usd() >= _cap_usd()


def status() -> dict[str, Any]:
    today = daily_total_usd()
    cap = _cap_usd()
    return {
        "today_usd": round(today, 4),
        "cap_usd": cap,
        "pct_of_cap": round((today / cap) * 100, 1) if cap > 0 else 0,
        "near_cap": today >= cap * 0.8,
        "kill_active": today >= cap,
    }


def by_provider_today() -> dict[str, dict[str, Any]]:
    """Per-provider breakdown for today (calls + cost)."""
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT provider, COUNT(*), COALESCE(SUM(cost_usd), 0), "
            "       COALESCE(SUM(tokens_in), 0), COALESCE(SUM(tokens_out), 0) "
            "FROM audit_log WHERE timestamp >= ? AND provider IS NOT NULL "
            "GROUP BY provider",
            (_day_start_str(),),
        ).fetchall()
    except Exception as e:
        logger.warning("by_provider_today failed: %s", e)
        return {}
    return {
        r[0]: {"calls": r[1], "cost_usd": round(float(r[2]), 4),
               "tokens_in": int(r[3]), "tokens_out": int(r[4])}
        for r in rows
    }
