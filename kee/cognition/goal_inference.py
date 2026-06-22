"""Goal Inference Engine — v2 §III Gap 1.

Maps a raw user command to a strategic/tactical/operational hierarchy.
This is the first cognitive layer above the ReAct loop: instead of
"interpret instruction literally", Kee asks itself "what does Coco
actually want?" before acting.

Example:
  command = "optimize AUCTORUM"
  →
  {
    "strategic": "Increase AUCTORUM's monthly revenue and client retention.",
    "tactical":  "Reduce response latency on the WhatsApp bot to lift conversion.",
    "operational": [
      "Check current latency baseline via system_status on the worker.",
      "Profile the agent's longest hops with a debug run.",
      "Apply the top fix and redeploy."
    ],
    "uncertainty": "low"
  }

The engine is offline-friendly: it reads `vault/config/goals.md` and
the user-behavior axioms (when present) so the LLM has Coco's actual
project context to ground inference in. Falls back gracefully when
those files are missing.

Risk-wise this is read-only and stateless — running inference doesn't
mutate anything. The agent uses the result to decide which tools to
invoke; that part stays under the existing verification loop.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

from kee.config import settings
from kee.core.ollama_client import OllamaClient
from kee.perception.goals import load_goals

logger = logging.getLogger(__name__)


@dataclass
class InferredGoal:
    strategic: str = ""
    tactical: str = ""
    operational: list[str] = field(default_factory=list)
    uncertainty: str = "medium"   # low | medium | high
    raw: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategic": self.strategic,
            "tactical": self.tactical,
            "operational": self.operational,
            "uncertainty": self.uncertainty,
        }


class GoalInferenceEngine:
    def __init__(self, llm: OllamaClient) -> None:
        self.llm = llm

    def _format_goals_block(self) -> str:
        active = [g for g in load_goals() if g.is_active()]
        if not active:
            return "(no active goals on file)"
        lines = []
        for g in active[:10]:
            dl = g.deadline.isoformat() if g.deadline else "no deadline"
            project = f" [{g.project}]" if g.project else ""
            progress = f" {g.progress_pct}%" if g.progress_pct is not None else ""
            lines.append(f"- **{g.title}**{project} (due {dl}{progress})")
        return "\n".join(lines)

    def _format_axioms_block(self) -> str:
        path = settings.vault_dir / "config" / "user_behavior.json"
        if not path.exists():
            return ""
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return ""
        axioms = data.get("axioms_recent") or []
        if not axioms:
            return ""
        return "\n".join(f"- {a}" for a in axioms[:5])

    async def infer(
        self,
        command: str,
        recent_context: str | None = None,
    ) -> InferredGoal:
        goals_block = self._format_goals_block()
        axioms_block = self._format_axioms_block()

        prompt = (
            "Eres el Goal Inference Engine de Kee. Recibes un comando "
            "casual de Armando y debes mapearlo a una jerarquía:\n"
            "  - **strategic**: el objetivo de fondo (POR QUÉ — una oración).\n"
            "  - **tactical**: el siguiente movimiento de mayor impacto (QUÉ — una oración).\n"
            "  - **operational**: hasta 3 pasos concretos (CÓMO — verbos en infinitivo).\n"
            "  - **uncertainty**: 'low' si el comando + contexto son claros; "
            "'medium' si hay ambigüedad razonable; 'high' si necesitas preguntar.\n\n"
            f"## Comando\n\"{command.strip()}\"\n\n"
            f"## Goals activos de Armando\n{goals_block}\n\n"
        )
        if axioms_block:
            prompt += f"## Axiomas recientes (Sleep Cycle)\n{axioms_block}\n\n"
        if recent_context:
            prompt += f"## Contexto reciente\n{recent_context[:800]}\n\n"
        prompt += (
            "Responde SOLO con un objeto JSON:\n"
            '{"strategic": "...", "tactical": "...", "operational": ["...", "..."], "uncertainty": "low|medium|high"}\n'
            "Sin prosa adicional. Si el comando es trivial (saludo, "
            "broma) o totalmente ambiguo, marca uncertainty='high' "
            "y deja operational como lista vacía."
        )

        try:
            response = await self.llm.chat(
                messages=[
                    {"role": "system", "content": "Eres conciso. Solo JSON."},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.2,
                owner="goal_inference",
            )
        except Exception as e:
            logger.warning("goal inference LLM call failed: %s", e)
            return InferredGoal(uncertainty="high", raw={"error": str(e)})

        content = (response.content or "").strip()
        # Handle code-fence wrap.
        if content.startswith("```"):
            content = content.strip("`").lstrip("json").strip()

        try:
            parsed = json.loads(content)
        except json.JSONDecodeError:
            logger.debug("goal inference returned non-JSON: %s", content[:200])
            return InferredGoal(uncertainty="high", raw={"unparsed": content[:300]})

        ops = parsed.get("operational") or []
        if not isinstance(ops, list):
            ops = [str(ops)]
        return InferredGoal(
            strategic=str(parsed.get("strategic", "")).strip(),
            tactical=str(parsed.get("tactical", "")).strip(),
            operational=[str(x).strip() for x in ops][:3],
            uncertainty=str(parsed.get("uncertainty", "medium")).lower(),
            raw=parsed,
        )
