"""Tool: pomodoro — focus session + scheduled break callback.

Wraps `focus.start` + `schedule_self.start` so a single call sets up the
canonical 25/5 cycle: open a focus session for `work_min` minutes, schedule
a callback to remind Coco to take a `break_min` break, and another after
the break to suggest resuming.

Risk: 1 — only mutates focus_sessions + scheduled_callbacks (own tables).
"""

from __future__ import annotations

import logging
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


class PomodoroTool(Tool):
    name = "pomodoro"
    description = (
        "Inicia un ciclo Pomodoro: abre `focus` por work_min minutos y "
        "programa dos callbacks vía `schedule_self` — uno al final del "
        "trabajo (recordar tomar break) y otro al final del break (sugerir "
        "retomar). Defaults: work_min=25, break_min=5. Coco dice 'arranca "
        "un pomodoro de auctorum' y tú llamas `pomodoro start "
        "project=auctorum`. Usa `pomodoro stop` para abortar.\n"
        "Acciones: 'start' | 'stop' | 'status'"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "stop", "status"],
                "default": "start",
            },
            "project": {"type": "string"},
            "intent": {"type": "string"},
            "work_min": {"type": "integer", "default": 25},
            "break_min": {"type": "integer", "default": 5},
        },
    }

    async def execute(
        self,
        action: str = "start",
        project: str | None = None,
        intent: str | None = None,
        work_min: int = 25,
        break_min: int = 5,
    ) -> dict[str, Any]:
        from kee.tools.focus import tool as focus_tool
        from kee.tools.schedule_self import tool as sched_tool

        if action == "status":
            cur = await focus_tool.execute(action="current")
            pending = await sched_tool.execute(action="list")
            return {
                "ok": True,
                "active_focus": cur.get("active"),
                "pending_callbacks": pending.get("pending", []),
            }

        if action == "stop":
            ended = await focus_tool.execute(
                action="end", outcome="pomodoro stopped",
            )
            # Also cancel any pending pomodoro callbacks
            pending = await sched_tool.execute(action="list")
            cancelled: list[int] = []
            for cb in pending.get("pending", []):
                if cb.get("kind") == "pomodoro":
                    res = await sched_tool.execute(
                        action="cancel", id=cb["id"],
                    )
                    if res.get("ok"):
                        cancelled.append(cb["id"])
            return {"ok": True, "ended_focus": ended.get("ok"),
                    "cancelled_callbacks": cancelled}

        # default: start
        if not project:
            return {"ok": False, "error": "project required"}
        if work_min < 1 or break_min < 0:
            return {"ok": False, "error": "work_min ≥ 1, break_min ≥ 0"}

        # Open the focus session
        focus_res = await focus_tool.execute(
            action="start",
            project=project,
            intent=intent or f"pomodoro {work_min}/{break_min}",
            duration_min=work_min,
        )
        # Schedule break-start callback at work_min
        cb1 = await sched_tool.execute(
            action="start",
            when_min=work_min,
            kind="pomodoro",
            message=(f"⏰ Pomodoro on {project}: trabajo de {work_min}min "
                     f"completado. Take a {break_min}min break."),
        )
        # Schedule resume callback at work_min + break_min
        cb2 = await sched_tool.execute(
            action="start",
            when_min=work_min + break_min,
            kind="pomodoro",
            message=(f"☕ Break done. Resume {project} or "
                     f"start a new pomodoro?"),
        )
        return {
            "ok": True,
            "project": project,
            "work_min": work_min,
            "break_min": break_min,
            "focus_id": focus_res.get("id"),
            "break_callback_id": cb1.get("id"),
            "resume_callback_id": cb2.get("id"),
        }


tool = PomodoroTool()
