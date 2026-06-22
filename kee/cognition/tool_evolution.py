"""Tool description rewrite proposals — closes the kwarg-hallucination loop.

When the LLM keeps inventing kwargs that a tool doesn't accept, the right
fix is usually to rewrite the tool's `description` so the LLM stops
guessing. This module:

  1. Reads `audit_log` for `kwarg_hallucination` rows in a window.
  2. For tools above a hit threshold, asks the local LLM (Ollama, free)
     to draft a new description that explicitly forbids the imagined
     kwargs.
  3. Writes the proposal to `vault/_kee/tool_rewrites/<date>-<tool>.md`
     for human review (parallel to `identity_proposals/`).

**No auto-apply.** Editing tool source is risky; this module only proposes.
The human reads the markdown, decides, and either edits the tool by hand
or instructs `claude_code` to do it.

Run-once entry: `await draft_rewrite_proposals(llm=...)`. Returns the list
of paths written.
"""

from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.core import db

logger = logging.getLogger(__name__)


# Minimum hallucination count over the window before we bother proposing
# a rewrite. Tunable via env in case Coco wants more aggressive proposals.
_MIN_HITS_DEFAULT = 3


def _hallucinations_by_tool(window_days: int = 7) -> dict[str, Counter]:
    """Aggregate kwarg_hallucination rows: which kwargs each tool keeps
    being called with that it doesn't accept."""
    out: dict[str, Counter] = {}
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT tool_name, parameters FROM audit_log "
            "WHERE action='kwarg_hallucination' "
            "AND timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
    except Exception as e:
        logger.warning("tool_evolution: audit_log read failed: %s", e)
        return out
    for tool_name, raw in rows:
        if not tool_name or not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        c = out.setdefault(tool_name, Counter())
        for u in (payload.get("unknown") or []):
            c[u] += 1
    return out


def _current_tool_metadata(name: str) -> dict[str, Any] | None:
    """Pull the live tool's name + description + accepted-kwarg list +
    source file. Used to give the LLM context for the rewrite."""
    try:
        from kee.core.tool_registry import ToolRegistry
        r = ToolRegistry()
        r.load_builtins()
    except Exception as e:
        logger.warning("tool_evolution: registry load failed: %s", e)
        return None
    tool = r.tools.get(name)
    if not tool:
        return None
    sch = getattr(tool, "parameters_schema", {}) or {}
    accepted = sorted((sch.get("properties") or {}).keys())
    return {
        "name": tool.name,
        "description": tool.description or "",
        "accepted_kwargs": accepted,
        "risk_level": getattr(tool, "risk_level", 1),
        "source": getattr(tool, "source", "?"),
        "file_path": getattr(tool, "file_path", None),
    }


async def _propose_one(llm, name: str, halluc: Counter, meta: dict) -> str:
    """Ask the LLM to draft a new description for `name` that explicitly
    rules out the hallucinated kwargs. Returns the proposed description
    text (or empty string on failure)."""
    top = halluc.most_common(8)
    prompt = (
        f"Eres el módulo Tool Evolution de Kee. La LLM ha estado llamando "
        f"al tool `{name}` con kwargs inventados que NO existen en su schema.\n\n"
        f"Descripción actual:\n```\n{meta['description'].strip()}\n```\n\n"
        f"Kwargs que SÍ acepta: {meta['accepted_kwargs']}\n"
        f"Kwargs ALUCINADOS (top 8 con frecuencia): {top}\n\n"
        "Reescribe la descripción para que sea menos ambigua y la LLM "
        "deje de inventar esos kwargs. Reglas:\n"
        "  - Mantén el primer párrafo (qué hace el tool).\n"
        "  - Agrega una línea explícita: 'NOT accepted: <lista>' con los "
        "    nombres alucinados más comunes.\n"
        "  - Si los kwargs alucinados sugieren una funcionalidad que el "
        "    usuario quería pero el tool no tiene, anótalo al final como "
        "    'TODO: consider adding ...'.\n"
        "  - No inventes parámetros nuevos.\n"
        "  - Máximo 600 caracteres en total.\n\n"
        "Responde SOLO con el texto de la nueva descripción, sin code "
        "fence, sin prosa adicional."
    )
    try:
        response = await llm.chat(
            messages=[
                {"role": "system",
                 "content": "Eres conciso y técnico. Solo texto plano."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            owner="tool_evolution",
        )
    except Exception as e:
        logger.warning("tool_evolution: LLM call failed for %s: %s", name, e)
        return ""
    txt = (response.content or "").strip()
    # Strip code-fence wrapper if the model added one despite the rule.
    if txt.startswith("```"):
        txt = txt.strip("`").strip()
        if txt.lower().startswith("text\n"):
            txt = txt.split("\n", 1)[1]
    return txt[:1000]


def _write_proposal(
    name: str, meta: dict, halluc: Counter, proposed: str,
) -> Path:
    """Write a markdown file with side-by-side comparison + raw stats so
    Coco can review before applying."""
    out_dir = settings.vault_dir / "_kee" / "tool_rewrites"
    out_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now().strftime("%Y-%m-%d")
    path = out_dir / f"{date}-{name}.md"
    body = (
        f"# Tool rewrite proposal — `{name}`\n\n"
        f"Generated: {datetime.now().isoformat(timespec='seconds')}\n\n"
        f"- **Source:** {meta.get('source')}\n"
        f"- **File:** `{meta.get('file_path') or 'built-in'}`\n"
        f"- **Risk level:** {meta.get('risk_level')}\n"
        f"- **Accepted kwargs:** {meta.get('accepted_kwargs')}\n"
        f"- **Hallucinated kwargs (top 8):** "
        f"{halluc.most_common(8)}\n\n"
        "## Current description\n\n```\n"
        f"{meta.get('description','').strip()}\n```\n\n"
        "## Proposed rewrite\n\n```\n"
        f"{proposed.strip()}\n```\n\n"
        "## How to apply\n\n"
        "Edit the tool's `.py` file and replace the `description` class "
        "attribute with the proposed text. Run `python -m kee.main check` "
        "to verify the registry still loads, then commit.\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


async def draft_rewrite_proposals(
    llm,
    *,
    window_days: int = 7,
    min_hits: int | None = None,
) -> list[dict[str, Any]]:
    """Main entry — surveys hallucinations, drafts rewrites, writes to vault.

    Returns a list of `{tool, path, hits}` for tools that got a proposal.
    Empty list when nothing meets the threshold (most days).
    """
    threshold = min_hits if min_hits is not None else _MIN_HITS_DEFAULT
    by_tool = _hallucinations_by_tool(window_days=window_days)
    if not by_tool:
        logger.info("tool_evolution: no hallucinations in last %dd",
                    window_days)
        return []

    out: list[dict[str, Any]] = []
    for name, counter in by_tool.items():
        hits = sum(counter.values())
        if hits < threshold:
            continue
        meta = _current_tool_metadata(name)
        if not meta:
            logger.debug("tool_evolution: no live metadata for %r — skipping",
                         name)
            continue
        proposed = await _propose_one(llm, name, counter, meta)
        if not proposed:
            continue
        path = _write_proposal(name, meta, counter, proposed)
        out.append({
            "tool": name, "path": str(path),
            "hits": hits,
            "kwargs": counter.most_common(5),
        })
        logger.info("tool_evolution: wrote proposal %s (hits=%d)", path, hits)
    return out
