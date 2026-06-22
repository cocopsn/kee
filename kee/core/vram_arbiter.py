"""VRAM Arbiter — final guard against OOM on the 8GB RTX 5050.

The scheduler's `vram_lock` serializes GPU access. The arbiter goes further:
it tracks *what is actually loaded* in VRAM right now and refuses any
registration that would push past the budget.

Rules (v2 roadmap §2.2):
  1. LLM is always resident. Never unload.
  2. Whisper runs on CPU. Period. No GPU contention with the LLM.
  3. Vision runs on the worker GPU, never on the primary.
  4. If the worker GPU is busy with vision, embeddings queue.
  5. Fine-tuning on the worker preempts everything else on the worker GPU.

Each Kee node (primary = Alienware, worker = Auctorum) instantiates its own
arbiter with its own budget and tenant cost table.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import subprocess
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class VRAMTenant(Enum):
    LLM = "llm"                 # Qwen3.5 9B Q4_K_M — ~6.5 GB, always resident
    WHISPER = "whisper"         # faster-whisper — would be ~1.5GB on GPU; FORCED to CPU
    VISION = "vision"           # Gemma 4 E4B (worker only) — ~3.5 GB
    EMBEDDINGS = "embeddings"   # nomic-embed-text (worker only) — ~0.3 GB


@dataclass
class VRAMState:
    total_mb: int | None
    used_mb: int | None
    free_mb: int | None
    active_tenants: list[str]
    status: str  # 'ok' | 'tight' | 'critical' | 'unknown'


_PRIMARY_BUDGET_MB = 7500     # 8 GB GPU minus ~500 MB DWM/OS
_WORKER_BUDGET_MB = 7500      # GTX 1070 8 GB

_PRIMARY_COSTS = {
    VRAMTenant.LLM: 6500,
    VRAMTenant.WHISPER: 0,    # CPU-bound by policy
}
_WORKER_COSTS = {
    VRAMTenant.VISION: 3500,
    VRAMTenant.EMBEDDINGS: 300,
}


class VRAMOvercommit(MemoryError):
    """Raised when a register() would push the budget past its ceiling."""


class VRAMArbiter:
    def __init__(self, node: str = "primary") -> None:
        self.node = node
        self.active: set[VRAMTenant] = set()
        self._lock = asyncio.Lock()

        if node == "primary":
            self.budget_mb = _PRIMARY_BUDGET_MB
            self.tenant_costs = dict(_PRIMARY_COSTS)
        elif node == "worker":
            self.budget_mb = _WORKER_BUDGET_MB
            self.tenant_costs = dict(_WORKER_COSTS)
        else:
            raise ValueError(f"Unknown node: {node!r}")

    # ── Accounting ────────────────────────────────────────────────────────
    def _accounted_mb(self) -> int:
        return sum(self.tenant_costs.get(t, 0) for t in self.active)

    async def can_load(self, tenant: VRAMTenant) -> bool:
        async with self._lock:
            cost = self.tenant_costs.get(tenant, 0)
            return (self._accounted_mb() + cost) <= self.budget_mb

    async def register(self, tenant: VRAMTenant) -> None:
        if not await self.can_load(tenant):
            raise VRAMOvercommit(
                f"Cannot load {tenant.value}: would exceed VRAM budget. "
                f"Active: {[t.value for t in self.active]}, "
                f"accounted={self._accounted_mb()}MB, budget={self.budget_mb}MB"
            )
        async with self._lock:
            self.active.add(tenant)
            logger.info(
                "VRAM register: +%s (active=%s, accounted=%dMB/%dMB)",
                tenant.value, [t.value for t in self.active],
                self._accounted_mb(), self.budget_mb,
            )

    async def release(self, tenant: VRAMTenant) -> None:
        async with self._lock:
            self.active.discard(tenant)
            logger.info(
                "VRAM release: -%s (active=%s)",
                tenant.value, [t.value for t in self.active],
            )

    # ── Live query (nvidia-smi) ───────────────────────────────────────────
    def measured(self) -> VRAMState:
        """Snapshot from nvidia-smi. Falls back to accounting-only when the
        tool isn't installed."""
        if shutil.which("nvidia-smi") is None:
            return VRAMState(
                total_mb=None, used_mb=None, free_mb=None,
                active_tenants=[t.value for t in self.active],
                status="unknown",
            )
        try:
            r = subprocess.run(
                ["nvidia-smi",
                 "--query-gpu=memory.total,memory.used,memory.free",
                 "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=3,
            )
            total, used, free = (int(x.strip()) for x in r.stdout.strip().split(",")[:3])
            status = "critical" if free < 200 else "tight" if free < 500 else "ok"
            return VRAMState(
                total_mb=total, used_mb=used, free_mb=free,
                active_tenants=[t.value for t in self.active],
                status=status,
            )
        except Exception as e:
            logger.debug("nvidia-smi probe failed: %s", e)
            return VRAMState(
                total_mb=None, used_mb=None, free_mb=None,
                active_tenants=[t.value for t in self.active],
                status="unknown",
            )

    def report(self) -> dict[str, Any]:
        m = self.measured()
        return {
            "node": self.node,
            "budget_mb": self.budget_mb,
            "accounted_mb": self._accounted_mb(),
            "active_tenants": [t.value for t in self.active],
            "measured": {
                "total_mb": m.total_mb, "used_mb": m.used_mb,
                "free_mb": m.free_mb, "status": m.status,
            },
        }


# Module-level singleton — most callers are on the primary node.
_default: VRAMArbiter | None = None


def get_default(node: str = "primary") -> VRAMArbiter:
    global _default
    if _default is None:
        _default = VRAMArbiter(node=node)
        # The LLM is always resident on the primary node — register at startup.
        if node == "primary":
            _default.active.add(VRAMTenant.LLM)
    return _default
