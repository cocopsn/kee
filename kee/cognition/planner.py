"""Multi-step Planner — generate, score, pick the best execution plan.

The bare ReAct loop is *reactive*: each step is decided after the previous
result. Jarvis-grade Kee plans 3 alternatives, scores them, then commits
to the winner — saving the user from "well, that didn't work, let me try
something else…" cycles.

v2 §III Gap 3.

Use only for tasks the agent has tagged as "complex" — for "list files"
or "what time is it" the planning round is overhead.

Scoring formula matches the v2 spec:
    quality * 2 - {low:0, medium:1, high:3}[risk] - time_minutes / 30

Higher is better. Tied scores break by lower risk, then lower time.

The planner is offline-friendly: it returns a sentinel `inferred=False`
plan when the LLM call fails, so the caller doesn't have to special-case.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from kee.cognition.world_model import impact_score
from kee.core.ollama_client import OllamaClient

logger = logging.getLogger(__name__)


_RISK_PENALTY = {"low": 0, "medium": 1, "high": 3}


@dataclass
class Plan:
    name: str
    steps: list[str] = field(default_factory=list)
    time_minutes: float = 0
    risk: str = "medium"   # 'low' | 'medium' | 'high'
    quality_score: int = 5  # 1..10
    failure_modes: list[str] = field(default_factory=list)
    score: float = 0.0      # computed; see _score()
    impact: dict[str, Any] | None = None  # populated when world_entity is provided

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "steps": self.steps,
            "time_minutes": self.time_minutes,
            "risk": self.risk,
            "quality_score": self.quality_score,
            "failure_modes": self.failure_modes,
            "score": round(self.score, 2),
            "impact": self.impact,
        }


def _score(plan: Plan) -> float:
    risk_pen = _RISK_PENALTY.get(plan.risk.lower(), 1)
    return plan.quality_score * 2 - risk_pen - (plan.time_minutes / 30.0)


def _coerce_plan(raw: dict[str, Any], idx: int) -> Plan:
    name = str(raw.get("name") or f"plan_{idx + 1}")
    steps_raw = raw.get("steps") or []
    if isinstance(steps_raw, str):
        steps_raw = [steps_raw]
    steps = [str(s).strip() for s in steps_raw if str(s).strip()]
    try:
        tm = float(raw.get("time_minutes") or raw.get("time") or 5)
    except (TypeError, ValueError):
        tm = 5.0
    risk = str(raw.get("risk", "medium")).lower()
    if risk not in _RISK_PENALTY:
        risk = "medium"
    try:
        q = int(raw.get("quality_score") or raw.get("quality") or 5)
    except (TypeError, ValueError):
        q = 5
    q = max(1, min(10, q))
    fm = raw.get("failure_modes") or []
    if isinstance(fm, str):
        fm = [fm]
    failure_modes = [str(f).strip() for f in fm if str(f).strip()]
    return Plan(name=name, steps=steps, time_minutes=tm, risk=risk,
                quality_score=q, failure_modes=failure_modes)


class MultiPathPlanner:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    async def plan(
        self,
        task: str,
        context: str | None = None,
        n_alternatives: int = 3,
        world_entity: str | None = None,
    ) -> dict[str, Any]:
        """Return {selected, alternatives, world_impact?}.

        `world_entity` (optional): an id from the world_model. If given,
        compute the cascading impact and attach it to the result so the
        agent can decide whether to require confirmation.
        """
        n_alternatives = max(2, min(5, n_alternatives))

        prompt = (
            "Eres el Multi-Step Planner de Kee. Te llega una tarea y "
            "tu trabajo es generar **exactamente "
            f"{n_alternatives} approaches distintos** para resolverla. "
            "Cada approach debe ser SUSTANTIVAMENTE diferente, no "
            "variaciones cosméticas.\n\n"
            f"## Task\n{task.strip()}\n\n"
        )
        if context:
            prompt += f"## Context\n{context.strip()[:1500]}\n\n"
        prompt += (
            "Para cada approach devuelve:\n"
            "  - name: nombre corto y distintivo\n"
            "  - steps: lista de 2-6 pasos concretos (verbos en infinitivo)\n"
            "  - time_minutes: estimación honesta\n"
            "  - risk: 'low' | 'medium' | 'high'\n"
            "  - quality_score: 1..10 — qué tan bien resuelve la tarea\n"
            "  - failure_modes: 1-3 cosas que podrían fallar\n\n"
            'Responde SOLO con JSON: {"plans": [{...}, {...}, {...}]}\n'
            "Sin prosa adicional. Sin markdown fences."
        )

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": "Eres conciso. Solo JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.45,
                owner="planner",
            )
        except Exception as e:
            logger.warning("planner LLM call failed: %s", e)
            return {
                "selected": None,
                "alternatives": [],
                "error": f"llm: {type(e).__name__}: {e}",
            }

        content = (response.content or "").strip()
        if content.startswith("```"):
            # Strip ```json ... ```
            content = content.strip("`").lstrip("json").strip()

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            return {
                "selected": None,
                "alternatives": [],
                "error": "non_json_response",
                "raw": content[:500],
            }

        raw_plans = data.get("plans") or []
        if not isinstance(raw_plans, list) or not raw_plans:
            return {"selected": None, "alternatives": [], "error": "empty_plans"}

        plans = [_coerce_plan(p, i) for i, p in enumerate(raw_plans)]
        for p in plans:
            p.score = _score(p)

        plans.sort(
            key=lambda p: (p.score, -_RISK_PENALTY[p.risk], -p.time_minutes),
            reverse=True,
        )
        selected = plans[0]

        # Optional world-impact annotation on the WINNING plan
        if world_entity:
            try:
                selected.impact = impact_score(world_entity)
            except Exception as e:
                selected.impact = {"error": str(e)}

        return {
            "selected": selected.to_dict(),
            "alternatives": [p.to_dict() for p in plans[1:]],
            "n_returned": len(plans),
        }
