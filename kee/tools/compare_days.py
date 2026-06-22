"""Tool: compare_days — diff two days' counts, side by side.

Calls `narrate_day` for both dates, computes per-category deltas, and
returns a markdown table. Useful for "qué cambió entre lunes y martes",
"este miércoles vs el anterior", "ayer vs hoy hasta las 10".

Risk: 0 — pure aggregation over `narrate_day` (which is itself $0).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from kee.tools.base import Tool


def _parse_date(spec: str) -> date:
    if spec == "today":
        return date.today()
    if spec == "yesterday":
        return date.today() - timedelta(days=1)
    if spec.startswith("-") and spec[1:].isdigit():
        return date.today() - timedelta(days=int(spec[1:]))
    from datetime import datetime
    return datetime.strptime(spec, "%Y-%m-%d").date()


def _format_diff(a_date: str, b_date: str,
                 a_counts: dict, b_counts: dict) -> str:
    keys = sorted(set(a_counts) | set(b_counts))
    lines = [
        f"# {a_date} vs {b_date}",
        "",
        f"| Category | {a_date} | {b_date} | Δ |",
        "|---|---:|---:|---:|",
    ]
    for k in keys:
        a = a_counts.get(k, 0)
        b = b_counts.get(k, 0)
        delta = b - a
        if delta == 0 and a == 0 and b == 0:
            continue
        sign = "+" if delta > 0 else ("" if delta == 0 else "−")
        delta_str = f"{sign}{abs(delta)}"
        lines.append(f"| {k} | {a} | {b} | {delta_str} |")
    return "\n".join(lines) + "\n"


class CompareDaysTool(Tool):
    name = "compare_days"
    description = (
        "Diff dos días — counts side-by-side de commits, dispatches, "
        "planes, focus, notificaciones, perception, conversaciones. "
        "Acepta 'today' | 'yesterday' | 'YYYY-MM-DD' | '-N' (N días "
        "atrás). Cero costo LLM. Útil para 'lunes vs martes', "
        "'esta semana vs la pasada' (con week_offset future).\n"
        "Inputs: date_a (default yesterday), date_b (default today)."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "date_a": {"type": "string", "default": "yesterday"},
            "date_b": {"type": "string", "default": "today"},
        },
    }

    async def execute(
        self,
        date_a: str = "yesterday",
        date_b: str = "today",
    ) -> dict[str, Any]:
        try:
            a = _parse_date(date_a)
            b = _parse_date(date_b)
        except Exception as e:
            return {"ok": False, "error": f"bad date: {e}"}

        from kee.tools.narrate_day import tool as nd
        ra = await nd.execute(date=a.isoformat())
        rb = await nd.execute(date=b.isoformat())
        a_counts = ra.get("counts") or {}
        b_counts = rb.get("counts") or {}
        markdown = _format_diff(a.isoformat(), b.isoformat(),
                                 a_counts, b_counts)
        delta = {
            k: (b_counts.get(k, 0) - a_counts.get(k, 0))
            for k in set(a_counts) | set(b_counts)
        }
        return {
            "ok": True,
            "date_a": a.isoformat(),
            "date_b": b.isoformat(),
            "counts_a": a_counts,
            "counts_b": b_counts,
            "delta": delta,
            "markdown": markdown,
        }


tool = CompareDaysTool()
