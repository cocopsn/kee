"""Central scheduler — concurrency control for Kee.

Solves the four problems that bite once Kee has more than one input source:

  1. **LLM is single-threaded.** Ollama serves one request at a time per model.
     Two callers hitting it concurrently queue at the OS level with no
     priority — a heartbeat task can starve a user voice command.
  2. **VRAM conflicts.** Whisper STT, Piper TTS, and the main LLM all want
     the same 8 GB. Running two at once thrashes.
  3. **Race conditions** on shared state — ChromaDB writes, the audit log,
     filesystem operations.
  4. **Priority inversion.** When the user says something, every background
     task should yield.

The scheduler exposes named `asyncio.Lock` objects for each shared resource.
A *priority queue* sits on top so that a Priority.CRITICAL submitter can
preempt a Priority.LOW one waiting on the same lock — by signaling its
cancel-event so the holder can release early.

Phase 0 only has one consumer (the terminal REPL) so contention is zero in
practice, but everything that touches the LLM or the filesystem already
goes through this layer — no retrofit needed when voice + heartbeat land
in Phase 2/3.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from enum import IntEnum
from typing import AsyncIterator

logger = logging.getLogger(__name__)


class Priority(IntEnum):
    CRITICAL = 0  # User voice command — preempt everything
    HIGH = 1      # User text command
    NORMAL = 2    # Heartbeat actions, scheduled tasks
    LOW = 3       # Background indexing, file watching
    IDLE = 4      # Maintenance, cleanup


@dataclass
class _Holder:
    priority: int
    name: str
    cancel: asyncio.Event


class PriorityLock:
    """An async lock with priority-based preemption hints.

    Callers acquire with a `Priority`. While the lock is held, any caller
    arriving with strictly higher priority (lower numeric value) signals
    the holder's `cancel` event. The holder is responsible for checking
    that event between cancellation-safe points — we never force-cancel a
    coroutine, because that can leave shared state inconsistent.
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self._mutex = asyncio.Lock()
        self._holder: _Holder | None = None

    @asynccontextmanager
    async def acquire(
        self,
        owner: str,
        priority: Priority = Priority.NORMAL,
    ) -> AsyncIterator[asyncio.Event]:
        # Tap on the shoulder of the current holder if we outrank them.
        if self._holder and priority < self._holder.priority:
            logger.debug(
                "Lock '%s': preemption hint from %s (p=%d) over %s (p=%d)",
                self.name, owner, priority.value,
                self._holder.name, self._holder.priority,
            )
            self._holder.cancel.set()

        await self._mutex.acquire()
        cancel = asyncio.Event()
        self._holder = _Holder(priority=priority.value, name=owner, cancel=cancel)
        try:
            yield cancel
        finally:
            self._holder = None
            self._mutex.release()


class KeeScheduler:
    """Holds the named locks for the four shared resources."""

    def __init__(self) -> None:
        self.llm = PriorityLock("llm")
        self.vram = PriorityLock("vram")
        self.memory = PriorityLock("memory")
        self.fs = PriorityLock("fs")

    @asynccontextmanager
    async def llm_call(
        self,
        owner: str = "agent",
        priority: Priority = Priority.HIGH,
    ) -> AsyncIterator[asyncio.Event]:
        async with self.llm.acquire(owner, priority) as cancel:
            yield cancel


# Module-level default scheduler. The KeeAgent constructs one and binds it
# to the Ollama client; tests and alternate surfaces can swap in their own.
_default: KeeScheduler | None = None


def get_default() -> KeeScheduler:
    global _default
    if _default is None:
        _default = KeeScheduler()
    return _default
