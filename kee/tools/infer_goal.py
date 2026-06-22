"""Tool: infer_goal — surface the Goal Inference Engine to the agent.

The agent loop calls this when Armando issues a high-level / casual
directive. Output goes back as a tool result the agent can reason about
("ok, the strategic goal is X, the tactical move is Y, so I'll use these
tools in this order").

Risk: 0 — pure inference, no side effects.
"""

from __future__ import annotations

from typing import Any

from kee.cognition.goal_inference import GoalInferenceEngine
from kee.core import services
from kee.tools.base import Tool


class InferGoalTool(Tool):
    name = "infer_goal"
    description = (
        "Map a high-level / casual directive from Armando into a strategic/"
        "tactical/operational hierarchy before acting. Use this when the "
        "command is vague or ambitious (e.g. 'optimize AUCTORUM', 'help me "
        "ship Kee v1', 'arregla esto'). Returns:\n"
        "  - strategic: the underlying objective\n"
        "  - tactical:  the highest-impact move\n"
        "  - operational: concrete steps (≤3)\n"
        "  - uncertainty: low/medium/high — high means: ask Armando first.\n"
        "Skip this tool for direct, concrete requests ('list files', "
        "'what time is it'). Use it when YOU need to plan."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The user's directive verbatim.",
            },
            "context": {
                "type": "string",
                "description": (
                    "Optional recent conversation snippet to ground inference. "
                    "Keep under 800 chars."
                ),
            },
        },
        "required": ["command"],
    }

    async def execute(
        self,
        command: str,
        context: str | None = None,
    ) -> dict[str, Any]:
        # Pull the LLM via services so we share the same scheduler-bound client.
        from kee.core.ollama_client import OllamaClient
        # services.registry guarantees we're in an agent-bound context, but
        # the LLM itself isn't on services — instantiate one tied to the
        # default scheduler.
        engine = GoalInferenceEngine(llm=OllamaClient())
        result = await engine.infer(command, recent_context=context)
        return result.to_dict()


tool = InferGoalTool()
