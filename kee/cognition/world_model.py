"""World Model — causal knowledge graph in SQLite.

Coco's projects don't live in isolation. AUCTORUM depends on the Auctorum
PC being up; that PC affects revenue; deploys to production touch
clients. The World Model captures these relationships as a directed graph
so the agent can answer "what does X affect?" / "what does Y depend on?"
before executing risky actions.

v2 §III Gap 2 (causal knowledge graph) + Gap 8 (impact assessment).

Schema (in `kee/core/db.py`):
  * `world_entities(id, name, type, state, criticality, notes, updated_at)`
  * `world_relations(source_id → target_id, relation, weight, description)`

Public API (sync — these are tiny SQL queries, no need for asyncio):
  * `upsert_entity(...)` — add or update an entity
  * `upsert_relation(...)` — add or update an edge
  * `entity(id)` — fetch one
  * `list_entities(type=None)` — filter by type
  * `downstream(id, max_depth=3)` — what does this entity AFFECT?
  * `upstream(id, max_depth=3)` — what does this entity DEPEND ON?
  * `impact_score(id)` — sum of (weight × criticality) over downstream — 0..N
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from kee.core import db

logger = logging.getLogger(__name__)


VALID_TYPES = {"project", "person", "system", "service", "metric", "tool", "external"}
VALID_RELATIONS = {"depends_on", "affects", "generates", "blocks", "owns", "uses"}
# Edges considered "downstream" — i.e. the source has influence over the target.
_DOWNSTREAM_RELATIONS = {"affects", "generates", "blocks", "owns"}
_UPSTREAM_RELATIONS = {"depends_on", "uses"}


@dataclass
class Entity:
    id: str
    name: str
    type: str
    state: dict[str, Any] = field(default_factory=dict)
    criticality: int = 5
    notes: str | None = None
    updated_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id, "name": self.name, "type": self.type,
            "state": self.state, "criticality": self.criticality,
            "notes": self.notes, "updated_at": self.updated_at,
        }


@dataclass
class Edge:
    source_id: str
    target_id: str
    relation: str
    weight: float = 1.0
    description: str | None = None


# ── Persistence ──────────────────────────────────────────────────────────
def upsert_entity(
    id: str,
    name: str,
    type: str,
    state: dict[str, Any] | None = None,
    criticality: int = 5,
    notes: str | None = None,
) -> None:
    if type not in VALID_TYPES:
        raise ValueError(f"invalid type {type!r}; pick one of {sorted(VALID_TYPES)}")
    criticality = max(1, min(10, int(criticality)))
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO world_entities (id, name, type, state, criticality, notes, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                type = excluded.type,
                state = excluded.state,
                criticality = excluded.criticality,
                notes = excluded.notes,
                updated_at = excluded.updated_at
            """,
            (id, name, type, json.dumps(state or {}, ensure_ascii=False),
             criticality, notes, datetime.utcnow()),
        )


def upsert_relation(
    source_id: str,
    target_id: str,
    relation: str,
    weight: float = 1.0,
    description: str | None = None,
) -> None:
    if relation not in VALID_RELATIONS:
        raise ValueError(f"invalid relation {relation!r}; pick one of {sorted(VALID_RELATIONS)}")
    weight = max(0.0, min(1.0, float(weight)))
    with db.cursor() as cur:
        cur.execute(
            """
            INSERT INTO world_relations
                (source_id, target_id, relation, weight, description, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(source_id, target_id, relation) DO UPDATE SET
                weight = excluded.weight,
                description = excluded.description,
                updated_at = excluded.updated_at
            """,
            (source_id, target_id, relation, weight, description, datetime.utcnow()),
        )


def remove_entity(id: str) -> None:
    with db.cursor() as cur:
        cur.execute("DELETE FROM world_entities WHERE id = ?", (id,))


def remove_relation(source_id: str, target_id: str, relation: str | None = None) -> int:
    with db.cursor() as cur:
        if relation:
            cur.execute(
                "DELETE FROM world_relations WHERE source_id = ? AND target_id = ? AND relation = ?",
                (source_id, target_id, relation),
            )
        else:
            cur.execute(
                "DELETE FROM world_relations WHERE source_id = ? AND target_id = ?",
                (source_id, target_id),
            )
        return cur.rowcount or 0


# ── Read API ─────────────────────────────────────────────────────────────
def entity(id: str) -> Entity | None:
    with db.cursor() as cur:
        cur.execute("SELECT * FROM world_entities WHERE id = ?", (id,))
        row = cur.fetchone()
    if not row:
        return None
    state = json.loads(row["state"]) if row["state"] else {}
    return Entity(
        id=row["id"], name=row["name"], type=row["type"],
        state=state, criticality=row["criticality"], notes=row["notes"],
        updated_at=str(row["updated_at"]) if row["updated_at"] else None,
    )


def list_entities(type: str | None = None) -> list[Entity]:
    sql = "SELECT * FROM world_entities"
    args: tuple = ()
    if type:
        sql += " WHERE type = ?"
        args = (type,)
    sql += " ORDER BY name ASC"
    out: list[Entity] = []
    with db.cursor() as cur:
        cur.execute(sql, args)
        for row in cur.fetchall():
            state = json.loads(row["state"]) if row["state"] else {}
            out.append(Entity(
                id=row["id"], name=row["name"], type=row["type"],
                state=state, criticality=row["criticality"], notes=row["notes"],
                updated_at=str(row["updated_at"]) if row["updated_at"] else None,
            ))
    return out


def _outgoing(id: str, relations: set[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(relations))
    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT r.target_id, r.relation, r.weight, r.description, e.name, e.type, e.criticality
            FROM world_relations r
            JOIN world_entities e ON e.id = r.target_id
            WHERE r.source_id = ? AND r.relation IN ({placeholders})
            """,
            (id, *relations),
        )
        return [dict(row) for row in cur.fetchall()]


def _incoming(id: str, relations: set[str]) -> list[dict[str, Any]]:
    placeholders = ",".join("?" * len(relations))
    with db.cursor() as cur:
        cur.execute(
            f"""
            SELECT r.source_id, r.relation, r.weight, r.description, e.name, e.type, e.criticality
            FROM world_relations r
            JOIN world_entities e ON e.id = r.source_id
            WHERE r.target_id = ? AND r.relation IN ({placeholders})
            """,
            (id, *relations),
        )
        return [dict(row) for row in cur.fetchall()]


def downstream(id: str, max_depth: int = 3) -> list[dict[str, Any]]:
    """Everything `id` flows out to, up to `max_depth` hops.

    Returns a list of `{entity_id, name, type, depth, weight_path, relation_path}`.
    Each entity is reported once with its STRONGEST cumulative weight.
    """
    return _traverse(id, _DOWNSTREAM_RELATIONS, max_depth, direction="out")


def upstream(id: str, max_depth: int = 3) -> list[dict[str, Any]]:
    """Everything `id` flows in from (its dependencies), up to max_depth."""
    return _traverse(id, _UPSTREAM_RELATIONS, max_depth, direction="in")


def _traverse(
    start_id: str,
    relations: set[str],
    max_depth: int,
    direction: str,
) -> list[dict[str, Any]]:
    """BFS over the allowed edges. Cumulative weight is the product along
    the path; if the same entity is reached via multiple paths we keep the
    max product."""
    fetcher = _outgoing if direction == "out" else _incoming
    other_id_field = "target_id" if direction == "out" else "source_id"

    best: dict[str, dict[str, Any]] = {}
    queue: list[tuple[str, int, float, list[str], list[str]]] = [
        (start_id, 0, 1.0, [], [])
    ]
    seen_at: dict[str, int] = {}

    while queue:
        cur, depth, weight_acc, rel_path, name_path = queue.pop(0)
        if depth >= max_depth:
            continue
        for edge in fetcher(cur, relations):
            other = edge[other_id_field]
            if other == start_id:
                continue  # don't loop back to ourselves
            new_weight = weight_acc * float(edge["weight"])
            new_path = rel_path + [edge["relation"]]
            new_names = name_path + [edge["name"]]
            existing = best.get(other)
            if existing is None or new_weight > existing["weight_path"]:
                best[other] = {
                    "entity_id": other,
                    "name": edge["name"],
                    "type": edge["type"],
                    "criticality": edge["criticality"],
                    "depth": depth + 1,
                    "weight_path": round(new_weight, 4),
                    "relation_path": new_path,
                    "name_path": new_names,
                }
            # Continue BFS unless we've already seen this node at a shallower depth.
            if seen_at.get(other, max_depth + 1) > depth + 1:
                seen_at[other] = depth + 1
                queue.append((other, depth + 1, new_weight, new_path, new_names))

    return sorted(best.values(), key=lambda x: x["weight_path"], reverse=True)


def impact_score(id: str, max_depth: int = 3) -> dict[str, Any]:
    """Aggregate downstream impact of acting on `id`.

    score = Σ (weight_path × downstream.criticality) over all reachable nodes.
    Recommendation thresholds (matched to v2 §III Gap 8):
      score < 3   → 'proceed'
      3..6        → 'proceed_with_logging'
      6..8        → 'require_confirmation'
      > 8         → 'block_and_alert'
    """
    affected = downstream(id, max_depth=max_depth)
    score = sum(a["weight_path"] * a["criticality"] for a in affected)
    score = round(score, 2)
    if score < 3:
        rec = "proceed"
    elif score < 6:
        rec = "proceed_with_logging"
    elif score < 8:
        rec = "require_confirmation"
    else:
        rec = "block_and_alert"
    return {
        "entity_id": id,
        "score": score,
        "recommendation": rec,
        "affected_count": len(affected),
        "affected": affected[:20],
    }


# ── Convenience: seed with a generic world skeleton ──────────────────────
def seed_default_world() -> dict[str, int]:
    """Populate the graph with the minimum infrastructure entities every
    Kee install has. Idempotent — safe to re-run.

    Customise this by adding your own project / client / metric entities.
    See the call site in `kee/tools/world.py` action='seed'."""
    counts = {"entities": 0, "relations": 0}

    # Generic infrastructure entities
    seed_entities = [
        ("primary_host", "Primary host (where Kee runs)",
         "system", {"role": "kee_core"}, 9),
        ("ollama", "Ollama daemon", "service",
         {
             "model": (
                 "hf.co/HauhauCS/"
                 "Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M"
             )
         }, 8),
        ("kee", "Kee", "project", {"phase": "active"}, 9),
        ("vault", "Obsidian vault", "service",
         {"path": "./vault"}, 8),
    ]
    for e in seed_entities:
        upsert_entity(*e)
        counts["entities"] += 1

    seed_edges = [
        ("kee", "primary_host", "depends_on", 1.0, "Kee runs here"),
        ("kee", "ollama", "depends_on", 1.0, "LLM brain"),
        ("kee", "vault", "uses", 0.9, "identity files + memory"),
        ("primary_host", "kee", "affects", 1.0,
         "Kee can't run if host is down"),
        ("ollama", "kee", "affects", 1.0, "no LLM = no agent"),
    ]
    for e in seed_edges:
        upsert_relation(*e)
        counts["relations"] += 1

    return counts
