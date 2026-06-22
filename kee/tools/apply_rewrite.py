"""Tool: apply_rewrite — apply a tool-rewrite proposal from Sleep Cycle.

Wraps `kee/cognition/tool_rewrite_apply.py`. Lets the agent (or Coco
via the dashboard / chat) apply a proposed description rewrite from
`vault/_kee/tool_rewrites/<date>-<tool>.md` after reviewing it.

Safety machinery (in the underlying module):
  - confirm=True required (refuses without)
  - target .py file must be git-clean
  - auto-runs `python -m kee.main check` after edit; auto-reverts on
    non-zero exit
  - never auto-commits; leaves the diff for human review

Risk: 2 — modifies a tool's source file. The hard guardrails above
mitigate; the human still does the final `git add` + commit.
"""

from __future__ import annotations

from typing import Any

from kee.tools.base import Tool


class ApplyRewriteTool(Tool):
    name = "apply_rewrite"
    description = (
        "Aplica una propuesta de tool-rewrite (Sleep Cycle Phase 9) al "
        "source code del tool. SAFE: requiere `confirm=True`, exige "
        "que el .py esté git-clean, corre `python -m kee.main check` "
        "después y revierte automáticamente si falla. NO commitea solo "
        "— deja `git diff` listo para que el humano revise + commitee.\n"
        "Acciones:\n"
        "  - 'list' (default): lista propuestas pendientes\n"
        "  - 'show':  muestra el contenido de una propuesta (date+tool)\n"
        "  - 'apply': aplica una propuesta (requires date+tool+confirm)"
    )
    risk_level = 2
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "show", "apply"],
                "default": "list",
            },
            "date": {"type": "string",
                     "description": "Date stem of the proposal (YYYY-MM-DD)."},
            "tool": {"type": "string",
                     "description": "Tool name (filename suffix after date)."},
            "confirm": {
                "type": "boolean", "default": False,
                "description": "Required for 'apply'. Without it, refuses.",
            },
        },
    }

    async def execute(
        self,
        action: str = "list",
        date: str | None = None,
        tool: str | None = None,
        confirm: bool = False,
    ) -> dict[str, Any]:
        from kee.config import settings

        if action == "list":
            d = settings.vault_dir / "_kee" / "tool_rewrites"
            if not d.exists():
                return {"ok": True, "proposals": []}
            rows = []
            for p in sorted(d.glob("*.md"), reverse=True):
                stem = p.stem
                if len(stem) > 11 and stem[10] == "-":
                    rows.append({
                        "date": stem[:10],
                        "tool": stem[11:],
                        "path": str(p),
                        "bytes": p.stat().st_size,
                    })
            return {"ok": True, "proposals": rows}

        if action == "show":
            if not date or not tool:
                return {"ok": False, "error": "date + tool required"}
            from kee.cognition.tool_rewrite_apply import parse_proposal
            from pathlib import Path
            p = settings.vault_dir / "_kee" / "tool_rewrites" / f"{date}-{tool}.md"
            return parse_proposal(p)

        if action == "apply":
            if not date or not tool:
                return {"ok": False, "error": "date + tool required"}
            from kee.cognition.tool_rewrite_apply import apply_proposal
            return apply_proposal(date, tool, confirm=bool(confirm))

        return {"ok": False, "error": f"unknown action {action!r}"}


tool = ApplyRewriteTool()
