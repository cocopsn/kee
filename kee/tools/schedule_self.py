"""Tool: schedule_self — lightweight future-time callbacks.

Coco says "recuérdame en 30 min revisar el deployment" and the agent
calls `schedule_self start when_min=30 message=…`. The row lands in
`scheduled_callbacks` and the heartbeat's `_check_scheduled_callbacks`
fires it when `fire_at <= now()`.

Two ways to express WHEN:
  - `when_min`: integer minutes from now (most common)
  - `at`:       explicit "YYYY-MM-DD HH:MM" string in local time

Risk: 1 — own table only, but mutations are user-visible (creates a
notification when fired).
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from typing import Any

from kee.core import db
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _row_to_dict(row) -> dict[str, Any]:
    cols = ["id", "created_at", "fire_at", "kind", "payload", "fired",
            "fired_at", "cancelled"]
    d = {c: row[i] for i, c in enumerate(cols)}
    if isinstance(d.get("payload"), str):
        try:
            d["payload"] = json.loads(d["payload"])
        except Exception:
            pass
    d["fired"] = bool(d["fired"])
    d["cancelled"] = bool(d["cancelled"])
    return d


def _resolve_fire_at(when_min: int | None, at: str | None) -> str | None:
    """Return UTC string (YYYY-MM-DD HH:MM:SS).

    SQLite's `datetime('now')` returns UTC, and the heartbeat compares
    `fire_at <= datetime('now')`. So we must persist UTC; when the user
    says "30 min from now" they mean 30 min wall-clock, which is the
    same delta in UTC.
    """
    if at:
        # Accept "YYYY-MM-DD HH:MM" or "HH:MM" — interpret as LOCAL,
        # then convert to UTC for storage.
        try:
            if " " in at:
                dt_local = datetime.strptime(at, "%Y-%m-%d %H:%M")
            else:
                today = datetime.now().date()
                t = datetime.strptime(at, "%H:%M").time()
                dt_local = datetime.combine(today, t)
                if dt_local < datetime.now():
                    dt_local = dt_local + timedelta(days=1)
            # Local → UTC offset
            offset = datetime.now() - datetime.utcnow()
            dt_utc = dt_local - offset
            return dt_utc.strftime("%Y-%m-%d %H:%M:%S")
        except ValueError:
            return None
    if when_min is not None:
        # `when_min` is wall-clock minutes; same delta in UTC.
        dt_utc = datetime.utcnow() + timedelta(minutes=int(when_min))
        return dt_utc.strftime("%Y-%m-%d %H:%M:%S")
    return None


class ScheduleSelfTool(Tool):
    name = "schedule_self"
    description = (
        "Programa un callback futuro para Kee. Coco dice 'recuérdame en "
        "X' y tú llamas `schedule_self start when_min=X message=…`. La fila "
        "queda en `scheduled_callbacks`; el heartbeat la dispara cuando "
        "`fire_at <= now()`.\n"
        "Acciones:\n"
        "  - 'start':  programa nuevo (when_min XOR at, message required)\n"
        "  - 'list':   próximos callbacks pendientes\n"
        "  - 'cancel': cancela por id\n"
        "  - 'history': últimos N callbacks (incluye disparados)"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "list", "cancel", "history"],
                "default": "list",
            },
            "when_min": {
                "type": "integer",
                "description": "Minutos desde ahora (start; XOR con `at`).",
            },
            "at": {
                "type": "string",
                "description": "Hora local 'YYYY-MM-DD HH:MM' o 'HH:MM' "
                               "(start; XOR con `when_min`).",
            },
            "message": {
                "type": "string",
                "description": "Texto del recordatorio (start).",
            },
            "kind": {
                "type": "string", "default": "reminder",
                "description": "Tag libre: reminder | check_deploy | "
                               "follow_up | morning_brief.",
            },
            "id": {"type": "integer",
                   "description": "ID del callback (cancel)."},
            "limit": {"type": "integer", "default": 10},
        },
    }

    async def execute(
        self,
        action: str = "list",
        when_min: int | None = None,
        at: str | None = None,
        message: str | None = None,
        kind: str = "reminder",
        id: int | None = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if action == "start":
            if not (when_min or at):
                return {"ok": False, "error": "when_min or at required"}
            if not message:
                return {"ok": False, "error": "message required"}
            fire_at = _resolve_fire_at(when_min, at)
            if not fire_at:
                return {"ok": False, "error": "could not parse `at`"}
            payload = json.dumps({"message": message,
                                  "set_by": "agent"}, ensure_ascii=False)
            with db.cursor() as cur:
                cur.execute(
                    "INSERT INTO scheduled_callbacks "
                    "(fire_at, kind, payload) VALUES (?, ?, ?)",
                    (fire_at, kind, payload),
                )
                new_id = cur.lastrowid
            return {"ok": True, "id": new_id, "fire_at": fire_at,
                    "kind": kind, "message": message}

        if action == "cancel":
            if id is None:
                return {"ok": False, "error": "id required"}
            with db.cursor() as cur:
                cur.execute(
                    "UPDATE scheduled_callbacks SET cancelled = 1 "
                    "WHERE id = ? AND fired = 0",
                    (int(id),),
                )
                if cur.rowcount == 0:
                    return {"ok": False,
                            "error": f"callback {id} not found or already fired"}
            return {"ok": True, "cancelled_id": int(id)}

        if action == "list":
            con = db.get_connection()
            rows = con.execute(
                "SELECT id, created_at, fire_at, kind, payload, fired, "
                "fired_at, cancelled FROM scheduled_callbacks "
                "WHERE fired = 0 AND cancelled = 0 "
                "ORDER BY fire_at ASC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return {"ok": True, "pending": [_row_to_dict(r) for r in rows]}

        if action == "history":
            con = db.get_connection()
            rows = con.execute(
                "SELECT id, created_at, fire_at, kind, payload, fired, "
                "fired_at, cancelled FROM scheduled_callbacks "
                "ORDER BY id DESC LIMIT ?",
                (int(limit),),
            ).fetchall()
            return {"ok": True, "callbacks": [_row_to_dict(r) for r in rows]}

        return {"ok": False, "error": f"unknown action {action!r}"}


tool = ScheduleSelfTool()


# ── Helper used by heartbeat ──────────────────────────────────────────────
def fire_due_callbacks() -> list[dict[str, Any]]:
    """Return + flip rows whose `fire_at <= now()`. Caller is the
    heartbeat — it dispatches each to the agent / notification fan-out."""
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, fire_at, kind, payload FROM scheduled_callbacks "
        "WHERE fired = 0 AND cancelled = 0 "
        "AND fire_at <= datetime('now')"
    ).fetchall()
    fired: list[dict[str, Any]] = []
    if not rows:
        return fired
    ids = [r[0] for r in rows]
    with db.cursor() as cur:
        cur.execute(
            f"UPDATE scheduled_callbacks "
            f"SET fired = 1, fired_at = datetime('now') "
            f"WHERE id IN ({','.join('?' * len(ids))})",
            ids,
        )
    for r in rows:
        try:
            payload = json.loads(r[3] or "{}")
        except Exception:
            payload = {"raw": r[3]}
        fired.append({
            "id": r[0], "fire_at": r[1], "kind": r[2],
            "message": payload.get("message"), "payload": payload,
        })
    return fired
