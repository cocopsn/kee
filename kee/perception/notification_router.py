"""Smart routing for outbound notifications.

`notify_user()` historically fans out to every wired channel. That's right
for critical events but creates notification fatigue for low-urgency
ones — and waking Coco at 3 AM with a desktop toast about a low-priority
event is worse than missing the event.

This module decides WHICH channels to use given:
  - `urgency`  ('low' | 'normal' | 'critical')
  - the current local hour
  - active focus session (if any)
  - whether the desktop is locked / idle (Windows-only signal, optional)

Returns a `RoutingDecision` the caller passes to `notify_user(channels=…)`.

Quiet hours: 00:00 - 07:00 local. Override via `KEE_QUIET_HOURS` env
(e.g. `22:00-08:00`).

Decision matrix:

  urgency=critical  → every channel always (waking Coco for real fires is
                      the whole point)
  urgency=normal    → desktop + telegram during 7-22; telegram only
                      otherwise; if focus session active and the message
                      is not about that project, downgrade to telegram-only
                      so the desktop doesn't break flow
  urgency=low       → desktop only during 7-22; nothing during quiet hours
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

logger = logging.getLogger(__name__)


_QUIET_DEFAULT = (0, 7)   # midnight to 7am

# Substring patterns (lowercased) for "do not disturb me right now" signals
# from the active window title — games, fullscreen video, deep-focus apps.
_DND_WINDOW_PATTERNS = (
    "cities skylines", "gta", "grand theft", "rockstar",
    "steam", "epic games", "battle.net", "uplay", "ubisoft",
    "valorant", "minecraft", "fortnite", "league of legends", "lol client",
    "destiny", "elden ring", "dark souls", "rdr2", "bf2042",
    "overwatch", "csgo", "counter-strike", "dota",
    "twitch", "youtube fullscreen", "netflix", "disney+", "primevideo",
    "zoom meeting", "teams meeting", "google meet", "discord call",
)


def _parse_quiet_env() -> tuple[int, int]:
    raw = os.environ.get("KEE_QUIET_HOURS", "").strip()
    if not raw:
        return _QUIET_DEFAULT
    m = re.match(r"^(\d{1,2}):?\d*\s*-\s*(\d{1,2}):?\d*$", raw)
    if not m:
        return _QUIET_DEFAULT
    try:
        return (int(m.group(1)) % 24, int(m.group(2)) % 24)
    except ValueError:
        return _QUIET_DEFAULT


def _in_quiet_window(hour: int, q: tuple[int, int]) -> bool:
    start, end = q
    if start <= end:
        return start <= hour < end
    # Wraps midnight (e.g. 22-8): in window if hour >= start OR hour < end
    return hour >= start or hour < end


def _active_window_dnd() -> tuple[bool, str | None]:
    """True if the active window matches a do-not-disturb pattern.
    Returns (matched, matched_pattern_or_None)."""
    try:
        import pygetwindow as gw  # type: ignore
        w = gw.getActiveWindow()
        if w is None:
            return False, None
        title = (getattr(w, "title", "") or "").lower()
        for pat in _DND_WINDOW_PATTERNS:
            if pat in title:
                return True, pat
    except Exception:
        pass
    return False, None


@dataclass
class RoutingDecision:
    desktop: bool
    telegram: bool
    reason: str
    quiet: bool
    hour: int
    dnd_window: bool = False


def decide(
    urgency: str = "normal",
    *,
    project_hint: str | None = None,
    now: datetime | None = None,
) -> RoutingDecision:
    """Decide which channels to use for a notification.

    `project_hint` lets the caller pass the project this notification is
    about; we cross-check against `focus_sessions.current()` to decide
    whether to interrupt the desktop while Coco is in flow.
    """
    now = now or datetime.now()
    hour = now.hour
    quiet_window = _parse_quiet_env()
    quiet = _in_quiet_window(hour, quiet_window)
    dnd_match, dnd_pattern = _active_window_dnd()

    # Critical always escalates everywhere — even gaming.
    if urgency == "critical":
        return RoutingDecision(
            desktop=True, telegram=True,
            reason="urgency=critical → all channels",
            quiet=quiet, hour=hour, dnd_window=dnd_match,
        )

    # Active gaming / DND window: drop desktop for low+normal so Coco
    # doesn't get yanked out of game flow. Telegram still goes through
    # for normal urgency (he can check it after).
    if dnd_match and urgency in ("low", "normal"):
        return RoutingDecision(
            desktop=False,
            telegram=(urgency == "normal"),
            reason=(f"DND window active ('{dnd_pattern}') → "
                    f"{'telegram only' if urgency == 'normal' else 'silent'}"),
            quiet=quiet, hour=hour, dnd_window=True,
        )

    # Low + quiet = silent.
    if urgency == "low" and quiet:
        return RoutingDecision(
            desktop=False, telegram=False,
            reason=f"urgency=low + quiet hours ({quiet_window}) → silent",
            quiet=quiet, hour=hour,
        )

    # Low + awake = desktop only.
    if urgency == "low":
        return RoutingDecision(
            desktop=True, telegram=False,
            reason="urgency=low → desktop only",
            quiet=quiet, hour=hour,
        )

    # Normal urgency
    if quiet:
        return RoutingDecision(
            desktop=False, telegram=True,
            reason=f"urgency=normal + quiet hours → telegram only",
            quiet=quiet, hour=hour,
        )

    # Active focus session and the notification is off-topic? Downgrade
    # so the desktop doesn't interrupt deep work.
    try:
        from kee.tools.focus import _current as focus_current
        f = focus_current()
        if f and project_hint:
            proj = (f.get("project") or "").lower()
            if proj and proj not in (project_hint or "").lower():
                return RoutingDecision(
                    desktop=False, telegram=True,
                    reason=(f"normal urgency, focus on {f['project']!r} "
                            f"and message is about {project_hint!r} → "
                            f"telegram only"),
                    quiet=quiet, hour=hour,
                )
    except Exception:
        pass

    # Default: both channels.
    return RoutingDecision(
        desktop=True, telegram=True,
        reason="urgency=normal, no quiet/focus override → both channels",
        quiet=quiet, hour=hour,
    )


async def notify_smart(
    title: str,
    body: str,
    *,
    urgency: str = "normal",
    kind: str = "info",
    project_hint: str | None = None,
) -> dict[str, Any]:
    """Convenience wrapper: decide → notify_user with the right channels."""
    from kee.perception.notifications import notify_user
    decision = decide(urgency, project_hint=project_hint)
    out = await notify_user(
        title=title, body=body, kind=kind, urgency=urgency,
        desktop=decision.desktop, telegram=decision.telegram,
    )
    out["routing"] = {
        "desktop": decision.desktop,
        "telegram": decision.telegram,
        "reason": decision.reason,
        "quiet": decision.quiet,
        "hour": decision.hour,
    }
    return out
