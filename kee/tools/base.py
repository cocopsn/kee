"""Base class for Kee tools.

Every tool exposes the same interface so the registry can discover it
uniformly and Ollama can be given a consistent function-calling schema.
"""

from __future__ import annotations

from typing import Any, ClassVar


class Tool:
    """Subclass and override `execute`. Set the four class attributes."""

    # ── Required class attributes ─────────────────────────────────────────
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    parameters_schema: ClassVar[dict[str, Any]] = {
        "type": "object", "properties": {}, "required": []
    }

    # 0 = read-only, 1 = local write, 2 = system mutation, 3 = external/irreversible
    risk_level: ClassVar[int] = 0

    # 'builtin' | 'custom' | 'kee_generated'
    source: ClassVar[str] = "builtin"

    async def execute(self, **kwargs: Any) -> Any:
        """Perform the tool's work. Return JSON-serializable data."""
        raise NotImplementedError

    def to_schema(self) -> dict[str, Any]:
        """OpenAI/Ollama function-calling schema."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters_schema,
            },
        }
