"""Audit logger.

Every tool execution and assistant response is recorded to the `audit_log`
table. Tool calls also capture pre/post state and the verification verdict
(see `kee.core.verify`). Anomalies (verification failures or rollbacks)
get an extra row in the `anomalies` table cross-referencing the audit row.

Public surface:
  * `log_action(...)` — record intent + outcome of a tool call in one row.
  * `log_response(...)` — record the assistant's final response.
  * `log_event(...)` — generic system event (heartbeat, perception).
  * `log_anomaly(...)` — record a verification failure (links to audit row).
  * `recent(limit)` / `recent_anomalies(limit)` — read accessors.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kee.core import db

logger = logging.getLogger(__name__)


def _json(obj: Any) -> str | None:
    if obj is None:
        return None
    try:
        return json.dumps(obj, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(obj)


class AuditLogger:
    # ── Tool execution ────────────────────────────────────────────────────
    def log_action(
        self,
        tool_name: str,
        parameters: dict[str, Any] | None = None,
        result: Any = None,
        risk_level: int = 0,
        success: bool = True,
        error: str | None = None,
        pre_state: dict[str, Any] | None = None,
        post_state: dict[str, Any] | None = None,
        verification: dict[str, Any] | None = None,
    ) -> int:
        """Record a tool call. Returns the audit row id."""
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                    (action, tool_name, parameters, result, risk_level, success,
                     error, pre_state, post_state, verification)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "tool_call",
                    tool_name,
                    _json(parameters or {}),
                    _json(result) if result is not None else None,
                    risk_level,
                    int(success),
                    error,
                    _json(pre_state) if pre_state else None,
                    _json(post_state) if post_state else None,
                    _json(verification) if verification else None,
                ),
            )
            return cur.lastrowid

    # ── Assistant response ────────────────────────────────────────────────
    def log_response(self, conversation_id: str, response: str) -> None:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log (action, tool_name, parameters, result, risk_level, success)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                ("response", "agent", conversation_id, response[:4000], 0, 1),
            )

    def log_llm_call(
        self,
        conversation_id: str,
        provider: str,
        model_name: str,
        tier: str,
        latency_ms: int | None,
        tokens_in: int | None,
        tokens_out: int | None,
        cost_usd: float | None,
        success: bool = True,
        error: str | None = None,
    ) -> None:
        """Record one LLM provider call (provenance + cost). One row per
        completion — used by the cost ticker and provider health UI."""
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log
                  (action, tool_name, parameters, success, error,
                   provider, model_name, tier, latency_ms,
                   tokens_in, tokens_out, cost_usd)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "llm_call", "llm.chat", conversation_id,
                    1 if success else 0, error,
                    provider, model_name, tier, latency_ms,
                    tokens_in, tokens_out, cost_usd,
                ),
            )

    # ── System events ────────────────────────────────────────────────────
    def log_event(self, action: str, payload: dict[str, Any] | None = None) -> None:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO audit_log (action, tool_name, parameters, risk_level, success)
                VALUES (?, ?, ?, ?, ?)
                """,
                (action, "_system", _json(payload or {}), 0, 1),
            )

    # ── Anomalies ─────────────────────────────────────────────────────────
    def log_anomaly(
        self,
        tool_name: str,
        verification: dict[str, Any],
        audit_id: int | None = None,
        kind: str = "unexpected_change",
        severity: int = 1,
    ) -> int:
        with db.cursor() as cur:
            cur.execute(
                """
                INSERT INTO anomalies (audit_id, tool_name, kind, detail, severity)
                VALUES (?, ?, ?, ?, ?)
                """,
                (audit_id, tool_name, kind, _json(verification), severity),
            )
            row_id = cur.lastrowid
        logger.warning(
            "ANOMALY tool=%s kind=%s severity=%d detail=%s",
            tool_name, kind, severity, verification.get("anomalies"),
        )
        return row_id

    # ── Read accessors ───────────────────────────────────────────────────
    def recent(self, limit: int = 20) -> list[dict[str, Any]]:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]

    def recent_anomalies(self, limit: int = 20) -> list[dict[str, Any]]:
        with db.cursor() as cur:
            cur.execute("SELECT * FROM anomalies ORDER BY id DESC LIMIT ?", (limit,))
            return [dict(row) for row in cur.fetchall()]
