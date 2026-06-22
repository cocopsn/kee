"""Tool: plan — surface MultiPathPlanner to the agent + persist history.

The agent calls this when a request is open-ended ("ship this", "optimize
that") and committing to one approach in advance pays off. Returns a
selected plan + alternatives, optionally annotated with world-model impact.

Every plan is now persisted to `plan_history` so:
  - the agent can recall prior plans for similar tasks (`recall` action)
  - Sleep Cycle can measure the "planned vs executed" gap
  - the dashboard can render a planner timeline
  - the user can mark a plan as carried out (`mark_executed` action)

Risk: 0 — `propose` is pure inference; `mark_executed` only mutates this
tool's own table.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from kee.cognition.planner import MultiPathPlanner
from kee.core import db
from kee.core.ollama_client import OllamaClient
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _persist_plan(
    *,
    task: str,
    context: str | None,
    selected: dict[str, Any] | None,
    alternatives: list[dict[str, Any]] | None,
    world_entity: str | None,
    world_impact: float | None,
) -> int | None:
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO plan_history "
                "(task, context, selected_json, alternatives_json, "
                " world_entity, world_impact) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    task,
                    context,
                    json.dumps(selected, ensure_ascii=False) if selected else None,
                    json.dumps(alternatives or [], ensure_ascii=False),
                    world_entity,
                    float(world_impact) if world_impact is not None else None,
                ),
            )
            return cur.lastrowid
    except Exception as e:
        logger.warning("plan persistence failed: %s", e)
        return None


def _row_to_dict(row) -> dict[str, Any]:
    keys = ["id", "timestamp", "task", "context", "selected_json",
            "alternatives_json", "world_entity", "world_impact",
            "executed", "executed_at", "outcome"]
    d: dict[str, Any] = {k: row[i] for i, k in enumerate(keys)}
    # Decode JSON fields lazily; if a row is malformed we surface the raw text.
    for k in ("selected_json", "alternatives_json"):
        v = d.pop(k)
        try:
            d[k.replace("_json", "")] = json.loads(v) if v else None
        except (TypeError, json.JSONDecodeError):
            d[k.replace("_json", "")] = v
    d["executed"] = bool(d["executed"])
    return d


def _list_history(limit: int = 20, executed: bool | None = None) -> list[dict]:
    sql = ("SELECT id, timestamp, task, context, selected_json, "
           "alternatives_json, world_entity, world_impact, "
           "executed, executed_at, outcome FROM plan_history")
    params: list[Any] = []
    if executed is True:
        sql += " WHERE executed = 1"
    elif executed is False:
        sql += " WHERE executed = 0"
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(int(limit))
    con = db.get_connection()
    rows = con.execute(sql, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def _recall(query: str, limit: int = 5) -> list[dict]:
    """Substring search over `task` for similar prior plans."""
    if not query:
        return []
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, timestamp, task, context, selected_json, "
        "alternatives_json, world_entity, world_impact, "
        "executed, executed_at, outcome FROM plan_history "
        "WHERE task LIKE ? ORDER BY id DESC LIMIT ?",
        (f"%{query}%", int(limit)),
    ).fetchall()
    return [_row_to_dict(r) for r in rows]


def _mark_executed(plan_id: int, outcome: str | None = None) -> dict:
    try:
        with db.cursor() as cur:
            cur.execute(
                "UPDATE plan_history "
                "SET executed = 1, "
                "    executed_at = CURRENT_TIMESTAMP, "
                "    outcome = COALESCE(?, outcome) "
                "WHERE id = ?",
                (outcome, int(plan_id)),
            )
            if cur.rowcount == 0:
                return {"ok": False, "error": f"plan {plan_id} not found"}
            return {"ok": True, "id": int(plan_id)}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class PlanTool(Tool):
    name = "plan"
    description = (
        "Generate, persist, and recall execution plans.\n"
        "Actions:\n"
        "  - 'propose'       (default): generate 2-5 distinct plans for "
        "                      `task`, score them quality - risk - time, "
        "                      return winner + alternatives. Plan is "
        "                      auto-persisted; reply includes `plan_id` "
        "                      so the agent can mark it executed later.\n"
        "  - 'history'       : list the most recent N plans (default 20).\n"
        "  - 'recall'        : substring search over prior plan tasks "
        "                      (`query` required).\n"
        "  - 'mark_executed' : flag a `plan_id` as carried out, with "
        "                      optional `outcome` note.\n\n"
        "Use 'propose' for open-ended directives ('optimize AUCTORUM'), "
        "'recall' BEFORE 'propose' to avoid replanning the same thing, "
        "and 'mark_executed' once the plan actually shipped so Sleep Cycle "
        "sees the planned-vs-executed ratio.\n"
        "NOT accepted: query (use `task` for propose, `query` only for recall)."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["propose", "history", "recall",
                          "mark_executed", "link_commits"],
                "default": "propose",
            },
            "task": {"type": "string", "description": "What needs to be done (propose only)."},
            "query": {"type": "string", "description": "Substring to search past plan tasks (recall only)."},
            "context": {
                "type": "string",
                "description": (
                    "Optional grounding for propose (recent conversation, "
                    "relevant facts). Keep under ~1500 chars."
                ),
            },
            "n_alternatives": {
                "type": "integer",
                "default": 3,
                "description": "How many alternatives to generate (2-5).",
            },
            "world_entity": {
                "type": "string",
                "description": (
                    "Optional world_model entity id; annotates winner with "
                    "downstream impact_score."
                ),
            },
            "plan_id": {
                "type": "integer",
                "description": "Plan id (mark_executed only).",
            },
            "outcome": {
                "type": "string",
                "description": "Free-form note saved on mark_executed.",
            },
            "limit": {
                "type": "integer",
                "default": 20,
                "description": "Result cap for history/recall.",
            },
            "executed_only": {
                "type": "boolean",
                "description": "history: filter to executed plans only.",
            },
            "pending_only": {
                "type": "boolean",
                "description": "history: filter to pending plans only.",
            },
        },
    }

    async def execute(
        self,
        action: str = "propose",
        task: str | None = None,
        query: str | None = None,
        context: str | None = None,
        n_alternatives: int = 3,
        world_entity: str | None = None,
        plan_id: int | None = None,
        outcome: str | None = None,
        limit: int = 20,
        executed_only: bool = False,
        pending_only: bool = False,
    ) -> dict[str, Any]:
        if action == "history":
            executed: bool | None = None
            if executed_only:
                executed = True
            elif pending_only:
                executed = False
            return {"ok": True, "plans": _list_history(limit, executed)}

        if action == "recall":
            if not query:
                return {"ok": False, "error": "query required for recall"}
            plans = _recall(query, limit=limit)
            return {"ok": True, "query": query, "count": len(plans),
                    "plans": plans}

        if action == "mark_executed":
            if plan_id is None:
                return {"ok": False, "error": "plan_id required"}
            return _mark_executed(int(plan_id), outcome)

        if action == "link_commits":
            from kee.cognition.plan_commit_linker import propose_plan_links
            return propose_plan_links(
                window_days=int(limit) if limit > 7 else 14,
                apply=False,  # tool always proposes; never auto-applies
            )

        # Default: propose
        if not task:
            return {"ok": False, "error": "task required for propose"}

        # Auto-recall: surface up-to-3 similar past plans so the planner LLM
        # can lean on prior work instead of starting blank. Uses the longest
        # significant word as the search key (cheap, no LLM, no embedding).
        prior: list[dict[str, Any]] = []
        try:
            words = [w.strip(".,;:¡!¿?'\"()[]") for w in task.split()
                     if len(w) > 4]
            words.sort(key=len, reverse=True)
            for key in words[:3]:
                hits = _recall(key, limit=2)
                for h in hits:
                    if not any(p["id"] == h["id"] for p in prior):
                        prior.append(h)
                if len(prior) >= 3:
                    break
            prior = prior[:3]
        except Exception:
            prior = []
        # If we found prior plans, fold a tiny synopsis into context so the
        # LLM benefits without inflating the prompt.
        if prior:
            synopsis_lines = []
            for p in prior:
                sel = (p.get("selected") or {}).get("name") or "?"
                exec_tag = ("[ejecutado]" if p["executed"]
                            else "[pendiente]")
                synopsis_lines.append(
                    f"- (id={p['id']}) {p['task']} → {sel} {exec_tag}"
                )
            prior_block = ("\n## Planes previos sobre temas similares\n"
                           + "\n".join(synopsis_lines)
                           + "\nÚsalos para evitar replanificar lo ya hecho.\n")
            context = (context or "") + prior_block

        engine = MultiPathPlanner(llm=OllamaClient())
        result = await engine.plan(
            task=task,
            context=context,
            n_alternatives=n_alternatives,
            world_entity=world_entity,
        )
        # Surface the prior-plan ids in the response so the caller can act
        # on them (e.g. mark_executed if the new plan reuses one).
        if prior:
            result["prior_plan_ids"] = [p["id"] for p in prior]
        # Persist regardless of error so we know the LLM was asked.
        plan_id = _persist_plan(
            task=task,
            context=context,
            selected=result.get("selected"),
            alternatives=result.get("alternatives") or [],
            world_entity=world_entity,
            world_impact=(result.get("selected") or {}).get("world_impact"),
        )
        if plan_id is not None:
            result["plan_id"] = plan_id
        return result


tool = PlanTool()
