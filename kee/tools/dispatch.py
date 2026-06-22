"""Tool: dispatch — read + write the dispatch_registry.

The dispatch_registry tracks "Kee, work on X" events at a project level.
Already auto-injected into every system prompt so the model knows what
Coco's been working on. This tool surfaces it as a first-class action so
the agent can:

  - record a fresh dispatch when the user pivots to a new project
  - look up project-level task history before reaching for memory_search
  - inspect per-project success rates / tool mixes
  - flag completion of a dispatch

Risk: 0 — own table, additive writes only.
"""

from __future__ import annotations

from typing import Any

from kee.cognition import dispatch_registry as dr
from kee.tools.base import Tool


class DispatchTool(Tool):
    name = "dispatch"
    description = (
        "Inspect or record dispatch_registry events (project-level work "
        "tracking). Use BEFORE asking the user 'what project?' (the answer "
        "is often in `active`), and AFTER significant project work to leave "
        "a breadcrumb future Kee can recall.\n"
        "Actions:\n"
        "  - 'active' (default): top N projects touched in last `days`\n"
        "  - 'recent'          : last N dispatch events across all projects\n"
        "  - 'project'         : per-project stats (tools, success rate)\n"
        "  - 'record'          : log a new dispatch (project + kind + summary)\n"
        "  - 'log_task'        : log a single tool call against a project"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["active", "recent", "project", "record", "log_task"],
                "default": "active",
            },
            "project": {"type": "string",
                        "description": "Project slug (required for project / "
                                       "record / log_task)."},
            "kind": {"type": "string", "default": "work",
                     "description": "Dispatch kind (record only): work | "
                                    "review | followup | note | done."},
            "summary": {"type": "string",
                        "description": "Free-form description of the "
                                       "dispatch (record only)."},
            "tool_name": {"type": "string",
                          "description": "Tool that was called (log_task only)."},
            "success": {"type": "boolean", "default": True,
                        "description": "Did the tool succeed (log_task only)."},
            "elapsed_ms": {"type": "integer",
                           "description": "Wall time of the call (log_task only)."},
            "note": {"type": "string",
                     "description": "Free-form note (log_task only)."},
            "limit": {"type": "integer", "default": 10},
            "days": {"type": "integer", "default": 7},
        },
    }

    async def execute(
        self,
        action: str = "active",
        project: str | None = None,
        kind: str = "work",
        summary: str = "",
        tool_name: str | None = None,
        success: bool = True,
        elapsed_ms: int | None = None,
        note: str | None = None,
        limit: int = 10,
        days: int = 7,
    ) -> dict[str, Any]:
        if action == "active":
            return {"ok": True,
                    "projects": dr.active_projects(limit=limit, days=days)}
        if action == "recent":
            return {"ok": True,
                    "dispatches": dr.recent_dispatches(limit=limit)}
        if action == "project":
            if not project:
                return {"ok": False, "error": "project required"}
            return {"ok": True,
                    **dr.project_task_summary(project, days=days)}
        if action == "record":
            if not project:
                return {"ok": False, "error": "project required"}
            rid = dr.record_dispatch(project, kind=kind, summary=summary)
            return {"ok": True, "id": rid, "project": project,
                    "kind": kind, "summary": summary}
        if action == "log_task":
            if not project or not tool_name:
                return {"ok": False,
                        "error": "project + tool_name required"}
            rid = dr.log_task(project, tool_name, success=success,
                              elapsed_ms=elapsed_ms, note=note)
            return {"ok": True, "id": rid}
        return {"ok": False, "error": f"unknown action {action!r}"}


tool = DispatchTool()
