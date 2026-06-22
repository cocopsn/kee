"""Tool: recap_week — 7-day aggregated narrative.

Calls `narrate_day` for each of the last 7 days and rolls them up into
a single markdown report with weekly totals + per-day mini-summaries.

Use cases:
  - Sunday-evening review
  - "qué hice esta semana"
  - Weekly Telegram digest
  - Sleep Cycle's weekly meta-report (if wired)

Risk: 0 — pure aggregation over `narrate_day` (which is itself $0).
"""

from __future__ import annotations

import time as _time
from collections import Counter
from datetime import date, timedelta
from typing import Any

from kee.tools.base import Tool


def _format_recap(week_data: list[dict]) -> str:
    """Compose the weekly markdown."""
    today = date.today()
    week_start = today - timedelta(days=6)
    lines = [
        f"# Semana {week_start.isoformat()} → {today.isoformat()}",
        "",
    ]

    # Weekly totals
    totals: Counter = Counter()
    days_with_activity = 0
    for d in week_data:
        any_activity = False
        for k, v in (d.get("counts") or {}).items():
            if v:
                totals[k] += v
                any_activity = True
        if any_activity:
            days_with_activity += 1

    if not totals:
        lines.append("_Nada registrado en los últimos 7 días._")
        return "\n".join(lines)

    lines.append("## Totales semanales")
    for k, v in totals.most_common():
        lines.append(f"- **{k}**: {v}")
    lines.append(f"- _{days_with_activity}/7 días con actividad_")
    lines.append("")

    # Per-day mini-line
    lines.append("## Por día")
    for d in week_data:
        date_iso = d.get("date", "?")
        counts = d.get("counts") or {}
        non_zero = {k: v for k, v in counts.items() if v}
        if non_zero:
            line = ", ".join(f"{k}={v}" for k, v in non_zero.items())
            lines.append(f"- `{date_iso}` — {line}")
        else:
            lines.append(f"- `{date_iso}` — _sin actividad_")
    lines.append("")

    # Top commits across the week (if any)
    all_commits: list[dict] = []
    for d in week_data:
        all_commits.extend((d.get("raw") or {}).get("commits") or [])
    if all_commits:
        # Already SHA-deduped within each day; cross-day still possible
        seen = {}
        for c in all_commits:
            seen.setdefault(c["sha"], c)
        commits = sorted(seen.values(), key=lambda c: c["ts"], reverse=True)
        lines.append(f"## Commits ({len(commits)})")
        for c in commits[:10]:
            t_short = c["ts"][:16] if len(c["ts"]) >= 16 else c["ts"]
            lines.append(
                f"- `{t_short}` `{c['sha']}` `{c['repo']}` — {c['subject']}"
            )
        if len(commits) > 10:
            lines.append(f"- … _{len(commits) - 10} más_")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


class RecapWeekTool(Tool):
    name = "recap_week"
    description = (
        "Resumen agregado de los últimos 7 días — totales semanales + "
        "una línea por día + top 10 commits cross-day. Llama `narrate_day` "
        "internamente para cada fecha; cero costo LLM. Útil para review "
        "dominical, weekly Telegram digest, o reflexión semanal antes de "
        "Sleep Cycle.\n"
        "Devuelve `{markdown, raw, week_totals}`. Optional save_to_vault "
        "escribe a `vault/_kee/daily/<today>-week.md`."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "save_to_vault": {"type": "boolean", "default": False},
        },
    }

    async def execute(self, save_to_vault: bool = False) -> dict[str, Any]:
        from kee.tools.narrate_day import tool as nd
        today = date.today()
        days = [(today - timedelta(days=i)).isoformat()
                for i in range(7)][::-1]  # oldest → newest
        t0 = _time.time()
        week_data: list[dict[str, Any]] = []
        for d in days:
            res = await nd.execute(date=d)
            week_data.append(res)
        elapsed_ms = int((_time.time() - t0) * 1000)

        markdown = _format_recap(week_data)
        totals: Counter = Counter()
        for d in week_data:
            for k, v in (d.get("counts") or {}).items():
                totals[k] += v

        out: dict[str, Any] = {
            "ok": True,
            "week_start": days[0],
            "week_end": days[-1],
            "elapsed_ms": elapsed_ms,
            "week_totals": dict(totals),
            "markdown": markdown,
        }

        if save_to_vault:
            try:
                from kee.config import settings
                out_dir = settings.vault_dir / "_kee" / "daily"
                out_dir.mkdir(parents=True, exist_ok=True)
                p = out_dir / f"{today.isoformat()}-week.md"
                p.write_text(markdown, encoding="utf-8")
                out["saved_to"] = str(p)
            except Exception as e:
                out["save_error"] = str(e)
        return out


tool = RecapWeekTool()
