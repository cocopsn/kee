"""Rolling conversation quality monitor — Jarvis-pattern, zero LLM.

Tracks the last N agent replies and computes a 0..1 quality score
based on: average length, character-break frequency, markdown count,
filler-phrase rate, language consistency. The dashboard's Health page
can render this as a sparkline ("Kee's voice quality: 0.82, trending
up").

Usage:
    monitor.observe(reply, source='voice', expected_lang='es')
    state = monitor.snapshot()  # → {avg_score, trend, recent[]}
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import asdict, dataclass
from typing import Any, Deque

from kee.cognition.response_qa import check as qa_check


@dataclass
class _Sample:
    ts: float
    source: str
    score: float
    issues: list[str]
    word_count: int


class ConversationMonitor:
    """Singleton-style. Keeps the last N samples in-memory; no DB writes."""

    def __init__(self, window: int = 20):
        self.window = window
        self._samples: Deque[_Sample] = deque(maxlen=window)

    def observe(self, reply: str, source: str = "chat",
                expected_lang: str = "es") -> dict:
        v = qa_check(reply, source=source, expected_lang=expected_lang)
        s = _Sample(
            ts=time.time(),
            source=source,
            score=v.score,
            issues=v.issues,
            word_count=v.meta.get("word_count", 0),
        )
        self._samples.append(s)
        # Persist to audit_log so other processes (dashboard, sleep cycle,
        # cross-process /quality endpoints) see this turn's score even though
        # the in-memory deque is per-process. Fail-soft: telemetry must
        # never break a turn.
        try:
            import json as _json
            from kee.core import db as _db
            with _db.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log "
                    "(action, tool_name, success, parameters, result) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        "conversation_qa", source, 1 if v.ok else 0,
                        _json.dumps({"score": v.score,
                                     "issues": v.issues,
                                     "word_count": v.meta.get("word_count", 0)},
                                    ensure_ascii=False),
                        # Truncate the reply preview so long replies don't
                        # bloat the DB. Just enough to spot patterns.
                        (reply or "")[:240],
                    ),
                )
        except Exception:
            pass
        return {"score": v.score, "issues": v.issues, "ok": v.ok}

    def snapshot(self) -> dict:
        n = len(self._samples)
        if n == 0:
            return {"count": 0, "avg_score": None, "trend": 0,
                    "recent": [], "by_source": {}}
        scores = [s.score for s in self._samples]
        avg = sum(scores) / n
        # Trend = mean of last 5 minus mean of previous 5
        trend = 0.0
        if n >= 6:
            recent = scores[-5:]
            prev = scores[-10:-5] if n >= 10 else scores[:-5]
            if prev:
                trend = (sum(recent) / len(recent)) - (sum(prev) / len(prev))
        # Per-source breakdown
        by_source: dict[str, dict] = {}
        for s in self._samples:
            d = by_source.setdefault(s.source, {"n": 0, "sum": 0.0})
            d["n"] += 1; d["sum"] += s.score
        for src in by_source:
            by_source[src] = {"count": by_source[src]["n"],
                              "avg_score": round(by_source[src]["sum"] / by_source[src]["n"], 2)}
        return {
            "count": n,
            "avg_score": round(avg, 2),
            "trend": round(trend, 2),
            "by_source": by_source,
            "recent": [asdict(s) for s in list(self._samples)[-10:]],
        }


# Module-level singleton — shared across surfaces in the same process.
_MONITOR = ConversationMonitor(window=20)


def observe(reply: str, source: str = "chat", expected_lang: str = "es") -> dict:
    return _MONITOR.observe(reply, source=source, expected_lang=expected_lang)


def snapshot() -> dict:
    return _MONITOR.snapshot()
