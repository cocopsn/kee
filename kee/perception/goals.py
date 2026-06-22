"""Parse `vault/config/goals.md` into structured records.

The vault file is human-first: Markdown headings as goal titles, bullet
lines for metadata. We don't enforce a rigid schema — we accept anything the
human writes — but we expect at least:

    ## Goal title
    - **Status**: active|paused|completed|abandoned
    - **Deadline**: YYYY-MM-DD
    - **Project**: kee | auctorum | ...
    - free-form bullets describing milestones, notes, etc.

Any unrecognised key/value bullet is preserved as `extras` so we can grow
the schema without breaking the parser.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator

from kee.config import settings

logger = logging.getLogger(__name__)


_BULLET_RE = re.compile(
    r"^\s*[-*]\s+\*\*(?P<key>[A-Za-z][A-Za-z _-]*)\*\*\s*[:：]\s*(?P<value>.+?)\s*$"
)
_FREE_BULLET_RE = re.compile(r"^\s*[-*]\s+(?P<text>.+?)\s*$")
_HEADING_RE = re.compile(r"^##\s+(?P<title>.+?)\s*$")


@dataclass
class Goal:
    title: str
    status: str = "active"
    deadline: date | None = None
    project: str | None = None
    progress_pct: int | None = None
    notes: list[str] = field(default_factory=list)
    extras: dict[str, str] = field(default_factory=dict)

    def days_to_deadline(self, today: date | None = None) -> int | None:
        if self.deadline is None:
            return None
        return (self.deadline - (today or date.today())).days

    def is_active(self) -> bool:
        return self.status.lower() in {"active", "in_progress", "in progress"}


def _parse_value(key: str, raw: str) -> tuple[str, object]:
    k = key.strip().lower().replace(" ", "_")
    v: object = raw.strip()
    if k == "deadline":
        for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                v = datetime.strptime(str(v), fmt).date()
                break
            except ValueError:
                continue
        else:
            v = None
    elif k == "progress" or k == "progress_pct":
        m = re.match(r"(\d{1,3})", str(v))
        v = int(m.group(1)) if m else None
        k = "progress_pct"
    return k, v


def _iter_blocks(text: str) -> Iterator[tuple[str, list[str]]]:
    """Yield (heading, body_lines) per `## Heading` block."""
    title: str | None = None
    body: list[str] = []
    for line in text.splitlines():
        m = _HEADING_RE.match(line)
        if m:
            if title is not None:
                yield title, body
            title = m.group("title").strip()
            body = []
        elif title is not None:
            body.append(line)
    if title is not None:
        yield title, body


def parse_goals(text: str) -> list[Goal]:
    out: list[Goal] = []
    for title, body in _iter_blocks(text):
        goal = Goal(title=title)
        for line in body:
            m = _BULLET_RE.match(line)
            if m:
                k, v = _parse_value(m.group("key"), m.group("value"))
                if k == "status" and isinstance(v, str):
                    goal.status = v.strip().lower()
                elif k == "deadline":
                    goal.deadline = v if isinstance(v, date) else None
                elif k == "project" and isinstance(v, str):
                    goal.project = v.strip()
                elif k == "progress_pct":
                    goal.progress_pct = v if isinstance(v, int) else None
                else:
                    if v is not None:
                        goal.extras[k] = str(v)
                continue
            m = _FREE_BULLET_RE.match(line)
            if m:
                goal.notes.append(m.group("text"))
        out.append(goal)
    return out


def load_goals(path: Path | None = None) -> list[Goal]:
    p = path or (settings.vault_dir / "config" / "goals.md")
    if not p.exists():
        return []
    try:
        return parse_goals(p.read_text(encoding="utf-8"))
    except Exception:
        logger.exception("goals.md parse failed")
        return []


def upcoming_deadlines(
    horizon_days: int = 7,
    today: date | None = None,
    path: Path | None = None,
) -> list[Goal]:
    """Active goals whose deadline lands within the next `horizon_days`."""
    today = today or date.today()
    horizon = today + timedelta(days=horizon_days)
    out = []
    for g in load_goals(path):
        if not g.is_active() or g.deadline is None:
            continue
        if today <= g.deadline <= horizon:
            out.append(g)
    return out
