"""Shared service registry — a tiny DI container.

Tools and other components that need access to the agent's collaborators
(memory manager, audit logger, tool registry) read them from this module.
The `KeeAgent` calls `bind(...)` once at startup, after which the services
are available globally.

This keeps tool implementations free of constructor plumbing while still
making the dependencies explicit and easy to swap in tests.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from kee.core.audit import AuditLogger
    from kee.core.memory import MemoryManager
    from kee.core.tool_registry import ToolRegistry


memory: "MemoryManager | None" = None
audit: "AuditLogger | None" = None
registry: "ToolRegistry | None" = None


def bind(
    memory_: "MemoryManager",
    audit_: "AuditLogger",
    registry_: "ToolRegistry",
) -> None:
    global memory, audit, registry
    memory = memory_
    audit = audit_
    registry = registry_
