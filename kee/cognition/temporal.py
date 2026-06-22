"""Temporal intelligence — when to act, when to wait, when to interrupt.

A small, deterministic layer the heartbeat (and other proactive components)
consult before pushing anything into the agent's queue. Without it, Kee will
talk over Coco's deep work for trivial things.

The patterns below are seeded from `user.md` and v2 §III Gap 4. They can be
tuned by the Phase 5 user-behavior model later — for now they're hand-picked.

Hours are in 24h local time. Ranges that wrap midnight are written as
`(start, end)` with `end <= start`, e.g. `(22, 2)` means 22:00 to 02:00.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, time as dtime
from enum import IntEnum

from kee.core.scheduler import Priority


class Mode(str):
    DEEP_WORK = "deep_work"   # Hard focus — only CRITICAL interrupts
    STUDY = "study"           # Class hours — HIGH and above
    LOW_ENERGY = "low_energy" # Post-lunch dip — NORMAL and above
    NORMAL = "normal"         # Default
    IDLE = "idle"             # Off-hours / low activity


@dataclass(frozen=True)
class _Window:
    start_hour: int  # 0-23
    end_hour: int    # 0-23, may be <= start_hour for wrap-midnight ranges
    mode: str

    def contains(self, h: int) -> bool:
        if self.start_hour <= self.end_hour:
            return self.start_hour <= h < self.end_hour
        return h >= self.start_hour or h < self.end_hour


@dataclass
class TemporalIntelligence:
    """Lightweight time-aware gating.

    Public surface:
      * `current_mode()` → str (one of Mode.*)
      * `should_interrupt(priority)` → bool
      * `optimal_delivery_time(task_type)` → datetime
    """

    windows: list[_Window] = field(default_factory=lambda: [
        _Window(22, 2, Mode.DEEP_WORK),    # 22:00 → 02:00
        _Window(8, 14, Mode.STUDY),         # Tec class hours
        _Window(14, 16, Mode.LOW_ENERGY),   # Post-lunch
        _Window(2, 8, Mode.IDLE),           # Asleep / off-hours
    ])

    # Minimum priority required to interrupt in each mode.
    # Anything *strictly higher* (numerically lower) than the threshold passes.
    interrupt_thresholds: dict[str, IntEnum] = field(default_factory=lambda: {
        Mode.DEEP_WORK: Priority.CRITICAL,
        Mode.STUDY: Priority.HIGH,
        Mode.LOW_ENERGY: Priority.NORMAL,
        Mode.NORMAL: Priority.NORMAL,
        Mode.IDLE: Priority.LOW,
    })

    def current_mode(self, at: datetime | None = None) -> str:
        h = (at or datetime.now()).hour
        for w in self.windows:
            if w.contains(h):
                return w.mode
        return Mode.NORMAL

    def should_interrupt(
        self,
        priority: Priority,
        at: datetime | None = None,
    ) -> bool:
        """True iff `priority` is high enough to break into the current mode."""
        threshold = self.interrupt_thresholds.get(self.current_mode(at), Priority.NORMAL)
        return priority.value <= threshold.value

    def optimal_delivery_time(
        self,
        task_type: str,
        now: datetime | None = None,
    ) -> datetime:
        """Suggest when a non-urgent message should be delivered.

        - 'daily_digest' → next 08:30 local
        - 'goal_reminder' → next 22:00 local (start of deep work)
        - everything else → now
        """
        now = now or datetime.now()
        if task_type == "daily_digest":
            return self._next_at(now, dtime(hour=8, minute=30))
        if task_type == "goal_reminder":
            return self._next_at(now, dtime(hour=22, minute=0))
        return now

    @staticmethod
    def _next_at(now: datetime, target: dtime) -> datetime:
        candidate = now.replace(
            hour=target.hour, minute=target.minute, second=0, microsecond=0,
        )
        if candidate <= now:
            from datetime import timedelta
            candidate = candidate + timedelta(days=1)
        return candidate
