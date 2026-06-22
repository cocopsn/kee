"""Tool: world_model — query the causal graph + run impact assessment.

The agent reaches for this BEFORE risky operations to ask "what would
acting on X cascade into?". Read-only: writes go through `seed_default_world`
or future `kee/cognition/world_evolve.py` (not yet built — Phase 5).

Risk: 0 (pure SQL reads).
"""

from __future__ import annotations

from typing import Any

from kee.cognition import world_model as wm
from kee.tools.base import Tool


class WorldModelTool(Tool):
    name = "world_model"
    description = (
        "Query the world model — the causal graph of Coco's projects, "
        "infrastructure, and external services. Use BEFORE risky actions to "
        "understand what they cascade into. Six actions:\n"
        "  - 'list': all entities (filter by `type`: project|system|service|metric|tool|person|external)\n"
        "  - 'entity': fetch one entity by id (with state + criticality)\n"
        "  - 'downstream': what `entity_id` AFFECTS (max_depth default 3)\n"
        "  - 'upstream': what `entity_id` DEPENDS ON\n"
        "  - 'impact_score': downstream criticality-weighted score 0..N "
        "with a recommendation (proceed | proceed_with_logging | "
        "require_confirmation | block_and_alert)\n"
        "  - 'seed': populate the graph with Coco's known projects + infra "
        "(idempotent — safe to call any time, just refreshes the seed set)"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "entity", "downstream", "upstream",
                         "impact_score", "seed"],
            },
            "entity_id": {"type": "string"},
            "type": {"type": "string"},
            "max_depth": {"type": "integer", "default": 3},
        },
        "required": ["action"],
    }

    async def execute(
        self,
        action: str,
        entity_id: str | None = None,
        type: str | None = None,
        max_depth: int = 3,
    ) -> dict[str, Any]:
        if action == "list":
            ents = wm.list_entities(type=type)
            return {
                "count": len(ents),
                "entities": [e.to_dict() for e in ents],
            }
        if action == "entity":
            if not entity_id:
                return {"error": "entity action requires `entity_id`"}
            e = wm.entity(entity_id)
            return e.to_dict() if e else {"error": f"no entity with id {entity_id!r}"}
        if action == "downstream":
            if not entity_id:
                return {"error": "downstream action requires `entity_id`"}
            return {
                "entity_id": entity_id,
                "max_depth": max_depth,
                "downstream": wm.downstream(entity_id, max_depth=max_depth),
            }
        if action == "upstream":
            if not entity_id:
                return {"error": "upstream action requires `entity_id`"}
            return {
                "entity_id": entity_id,
                "max_depth": max_depth,
                "upstream": wm.upstream(entity_id, max_depth=max_depth),
            }
        if action == "impact_score":
            if not entity_id:
                return {"error": "impact_score action requires `entity_id`"}
            return wm.impact_score(entity_id, max_depth=max_depth)
        if action == "seed":
            return {"status": "seeded", "counts": wm.seed_default_world()}
        return {"error": f"unknown action {action!r}"}


tool = WorldModelTool()
