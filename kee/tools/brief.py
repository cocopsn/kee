"""Tool: brief — composable markdown brief of Kee's day so far.

Aggregates commits + dispatches + reflect snapshot + calendar + inbox
into a single markdown string suitable for:
  - sharing on Telegram (`telegram_send`)
  - saving to `vault/_kee/daily/<date>-on-demand.md`
  - reading aloud via voice
  - displaying on the dashboard

This is what Sleep Cycle's 04:00 brief does, minus the LLM rewrite — pure
deterministic composition from the data we've been building. Useful when
Coco wants the morning brief at 2 PM instead of waiting for tomorrow.

Risk: 0 — pure reads.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date, datetime
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _format_md(payload: dict[str, Any]) -> str:
    """Compose a markdown brief from the gathered sub-payloads."""
    today = date.today().isoformat()
    now = datetime.now().strftime("%H:%M")
    lines: list[str] = [
        f"# Kee brief — {today} {now}",
        "",
    ]

    # Commits
    commits = payload.get("commits") or {}
    today_c = commits.get("today") or {}
    week_c = commits.get("week") or {}
    if today_c.get("count") is not None or week_c.get("count") is not None:
        lines.append("## Commits")
        lines.append(
            f"- **Hoy:** {today_c.get('count', 0)} commit(s) "
            + (", ".join(f"{repo}={n}" for repo, n in
                         (today_c.get('by_repo') or {}).items())
               or "_(ninguno)_")
        )
        lines.append(
            f"- **Última semana:** {week_c.get('count', 0)} commit(s) "
            + (", ".join(f"{repo}={n}" for repo, n in
                         list((week_c.get('by_repo') or {}).items())[:5])
               or "_(ninguno)_")
        )
        lines.append("")

    # Dispatch / active projects
    projects = payload.get("projects") or []
    if projects:
        lines.append("## Proyectos vivos (últimos 7d)")
        for p in projects[:5]:
            lines.append(
                f"- **{p['project']}** — {p['touches']} touches "
                f"(último: {p.get('last_seen', '?')})"
            )
        lines.append("")

    # Reflect summary
    qa = payload.get("qa") or {}
    if qa.get("samples"):
        lines.append("## Calidad de respuesta (Kee, últimos 7d)")
        lines.append(
            f"- Promedio: **{qa['avg_score']}** ({qa['samples']} muestras)"
        )
        for src, info in (qa.get("by_source") or {}).items():
            lines.append(f"  - {src}: {info['avg']} (n={info['n']})")
        lines.append("")

    # Plans
    plans = payload.get("plans") or {}
    if plans.get("total"):
        lines.append("## Planes")
        lines.append(
            f"- {plans['executed']}/{plans['total']} ejecutados "
            f"({int((plans.get('execution_rate') or 0) * 100)}%)"
        )
        for p in (plans.get("recent") or [])[:3]:
            mark = "[x]" if p["executed"] else "[ ]"
            lines.append(f"  - {mark} {p['task'][:90]}")
        lines.append("")

    # Inbox
    inbox = payload.get("inbox") or {}
    totals = inbox.get("totals") or {}
    if totals:
        lines.append("## Inbox (no leídos)")
        for cat, n in sorted(totals.items(), key=lambda kv: -kv[1])[:6]:
            lines.append(f"- {cat}: {n}")
        lines.append("")

    # Calendar (upcoming)
    cal = payload.get("calendar") or {}
    events = cal.get("events") or []
    if events:
        lines.append("## Calendario (próximas horas)")
        for e in events[:3]:
            t = e.get("start") or e.get("starts_at") or "?"
            lines.append(f"- {t} — {(e.get('summary') or '?')[:80]}")
        lines.append("")

    # Hallucinations / tool degradation
    halluc = payload.get("hallucinations") or {}
    if halluc.get("total"):
        worst = halluc.get("by_tool", [])[:1]
        if worst:
            lines.append("## Salud de tools")
            w = worst[0]
            top = ", ".join(f"{k}({n})" for k, n in (w.get("top_kwargs") or [])[:3])
            lines.append(
                f"- {halluc['total']} kwargs alucinados; peor offender: "
                f"`{w['tool']}` ({w['count']} hits, top: {top})"
            )
            lines.append("")

    # Closing one-liner
    summary = payload.get("summary")
    if summary:
        lines.append("---")
        lines.append(f"_{summary}_")

    return "\n".join(lines).rstrip() + "\n"


class BriefTool(Tool):
    name = "brief"
    description = (
        "Compose a markdown daily brief of Kee's state: commits, projects, "
        "QA score, planes, inbox totals, calendario próximo, alucinaciones. "
        "Pure read — no LLM, no network beyond what `inbox_triage` and "
        "`calendar` already require. Use cuando Coco pide 'qué llevamos' o "
        "antes de mandarse un resumen a Telegram. Devuelve `{markdown, "
        "raw}` donde `raw` es el snapshot estructurado y `markdown` es "
        "el texto formateado para compartir."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "include_inbox": {
                "type": "boolean", "default": False,
                "description": "Include Gmail unread totals (requires auth).",
            },
            "include_calendar": {
                "type": "boolean", "default": False,
                "description": "Include next 3 calendar events (requires auth).",
            },
            "save_to_vault": {
                "type": "boolean", "default": False,
                "description": "Also write to "
                               "`vault/_kee/daily/<date>-on-demand-<HHMM>.md`.",
            },
        },
    }

    async def execute(
        self,
        include_inbox: bool = False,
        include_calendar: bool = False,
        save_to_vault: bool = False,
    ) -> dict[str, Any]:
        from kee.tools.reflect import tool as reflect_tool
        snap = await reflect_tool.execute(
            window_days=7,
            include_commits=True,
            include_inbox=include_inbox,
        )

        # Optional calendar fold-in.
        if include_calendar:
            try:
                from kee.tools.calendar_tool import tool as cal_tool
                snap["calendar"] = await cal_tool.execute(
                    action="upcoming", hours=12, max_results=5,
                )
            except Exception:
                snap["calendar"] = {"error": "calendar tool unavailable"}

        markdown = _format_md(snap)
        out: dict[str, Any] = {"markdown": markdown, "raw": snap}

        if save_to_vault:
            try:
                from kee.config import settings
                out_dir = settings.vault_dir / "_kee" / "daily"
                out_dir.mkdir(parents=True, exist_ok=True)
                stamp = datetime.now().strftime("%H%M")
                p = out_dir / f"{date.today().isoformat()}-on-demand-{stamp}.md"
                p.write_text(markdown, encoding="utf-8")
                out["saved_to"] = str(p)
            except Exception as e:
                out["save_error"] = str(e)
        return out


tool = BriefTool()
