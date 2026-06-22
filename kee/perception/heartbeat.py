"""Heartbeat daemon — Kee's autonomic nervous system.

Runs every `interval_s` (default 5 minutes). Each beat:

  1. Collects a snapshot of the world (system health, Ollama status, pending
     tasks, active window, goal proximity).
  2. Logs the snapshot to the audit log (always — this is the historical
     record of what Kee saw).
  3. For every snapshot field flagged `action_needed`, computes a priority
     and asks `TemporalIntelligence` whether interruption is allowed in the
     current mode.
  4. Surviving actionables are queued for the agent (`agent.process` with
     `source="heartbeat"`). The agent decides what to do — usually that
     means logging a note; eventually it will mean Telegram / TTS.

Dedup: each check has its own cooldown so a single persistent condition
(disk low for hours, worker offline all night) doesn't fire the agent on
every beat. Defaults to 30 minutes per condition.

Runs entirely in the asyncio event loop. The CPU cost per beat is dominated
by `nvidia-smi` (~50ms) and the goals.md parse (~1ms). Idle CPU < 0.1%.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Awaitable, Callable

import httpx
import psutil

from kee.cognition.self_healing import SelfHealing
from kee.cognition.temporal import Mode, TemporalIntelligence
from kee.config import settings
from kee.core import db
from kee.core.scheduler import Priority
from kee.core.vram_arbiter import get_default as get_vram
from kee.perception.goals import upcoming_deadlines
from kee.perception.window import get_active_window

logger = logging.getLogger(__name__)


@dataclass
class Actionable:
    """Something the heartbeat thinks the agent should know about."""
    check: str
    priority: Priority
    summary: str
    detail: dict[str, Any] = field(default_factory=dict)


@dataclass
class HeartbeatSnapshot:
    timestamp: str
    mode: str
    checks: dict[str, dict[str, Any]] = field(default_factory=dict)
    actionables_fired: list[str] = field(default_factory=list)
    actionables_suppressed: list[str] = field(default_factory=list)


CheckFn = Callable[[], Awaitable[dict[str, Any]]]


class HeartbeatDaemon:
    def __init__(
        self,
        agent,
        interval_s: int = 300,
        dedup_seconds: int = 1800,  # 30 min between repeats of the same check
    ) -> None:
        self.agent = agent
        self.interval_s = interval_s
        self.dedup_seconds = dedup_seconds
        self.temporal = TemporalIntelligence()
        self.healer = SelfHealing()

        # name -> last-time-we-fired-this
        self._last_fired: dict[str, float] = {}
        # Window-change detection
        self._last_window: dict[str, Any] | None = None
        self._window_switches: list[float] = []  # unix ts, last 10 min

        # Buffer of recent beats — surfaces (terminal, future dashboard) read
        # this for /heartbeat-style commands.
        self._recent: list[HeartbeatSnapshot] = []
        self._recent_max = 50

        self._stop = asyncio.Event()
        self._task: asyncio.Task | None = None

    # ── Lifecycle ─────────────────────────────────────────────────────────
    def start(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        loop = loop or asyncio.get_event_loop()
        if self._task is None or self._task.done():
            self._task = loop.create_task(self.run_forever())
            logger.info("Heartbeat daemon started (interval=%ds)", self.interval_s)

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        # Fire one beat immediately so the user can confirm it's alive.
        await self._safe_beat()
        while not self._stop.is_set():
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                await self._safe_beat()
        logger.info("Heartbeat daemon stopped")

    async def _safe_beat(self) -> None:
        try:
            await self.beat()
        except Exception:
            logger.exception("Heartbeat beat failed")

    # ── A single beat ─────────────────────────────────────────────────────
    async def beat(self) -> HeartbeatSnapshot:
        now = datetime.now()
        snap = HeartbeatSnapshot(
            timestamp=now.isoformat(timespec="seconds"),
            mode=self.temporal.current_mode(now),
        )

        checks: list[tuple[str, CheckFn]] = [
            ("system_health", self._check_system_health),
            ("ollama_status", self._check_ollama),
            ("pending_tasks", self._check_pending_tasks),
            ("active_window", self._check_active_window),
            ("goal_deadlines", self._check_goal_deadlines),
            ("calendar", self._check_calendar),
            ("market_alerts", self._check_market_alerts),
            ("biometric_state", self._check_biometric_state),
            ("cognitive_health", self._check_cognitive_health),
            ("morning_brief", self._check_morning_brief),
            ("focus_drift", self._check_focus_drift),
            ("scheduled_callbacks", self._check_scheduled_callbacks),
            ("worker_status", self._check_worker_status),
            ("passive_perception", self._check_passive_perception),
            ("opportunity_scan", self._check_opportunity_scan),
        ]

        actionables: list[Actionable] = []
        for name, fn in checks:
            try:
                result = await fn()
            except Exception as e:
                logger.exception("Heartbeat check %s failed", name)
                result = {"error": f"{type(e).__name__}: {e}"}
            snap.checks[name] = result
            if result.get("action_needed"):
                actionables.append(Actionable(
                    check=name,
                    priority=result.get("priority", Priority.NORMAL),
                    summary=result.get("summary", name),
                    detail=result,
                ))

        # Always log the snapshot to audit.
        self.agent.audit.log_event("heartbeat", {
            "mode": snap.mode,
            "checks": snap.checks,
        })

        # Filter actionables by temporal mode + per-check cooldown.
        for a in actionables:
            if not self._should_fire(a, snap.mode):
                snap.actionables_suppressed.append(a.check)
                continue
            snap.actionables_fired.append(a.check)
            self._last_fired[a.check] = time.time()
            asyncio.create_task(self._dispatch_to_agent(a))
            # In parallel, ask the self-healer if there's an automatic
            # recovery for this. Recovery is independent of agent dispatch
            # — Kee can both notify Coco AND restart Ollama.
            asyncio.create_task(
                self.healer.attempt_recovery(a.check, snap.checks.get(a.check, {}))
            )

        # Buffer for surfaces
        self._recent.append(snap)
        if len(self._recent) > self._recent_max:
            self._recent = self._recent[-self._recent_max:]

        return snap

    def recent(self, n: int = 10) -> list[HeartbeatSnapshot]:
        return list(self._recent[-n:])

    # ── Action dispatching ────────────────────────────────────────────────
    def _should_fire(self, a: Actionable, mode: str) -> bool:
        if not self.temporal.should_interrupt(a.priority):
            return False
        last = self._last_fired.get(a.check, 0.0)
        if (time.time() - last) < self.dedup_seconds:
            return False
        return True

    async def _dispatch_to_agent(self, a: Actionable) -> None:
        prompt = (
            f"[heartbeat:{a.check}] {a.summary}\n\n"
            "Decide if this needs an action. "
            "If yes, take it (preferably one tool call) and reply briefly. "
            "If no, reply in one sentence with what you observed and why "
            "no action is needed."
        )
        try:
            await self.agent.process(prompt, source="heartbeat")
        except Exception:
            logger.exception("Heartbeat → agent dispatch failed for %s", a.check)

    # ── Individual checks ─────────────────────────────────────────────────
    async def _check_system_health(self) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        disk = psutil.disk_usage(str(settings.project_root))
        cpu = psutil.cpu_percent(interval=0.1)
        vram = get_vram().measured()

        out: dict[str, Any] = {
            "cpu_pct": cpu,
            "ram_used_pct": vm.percent,
            "ram_free_gb": round(vm.available / 1024**3, 2),
            "disk_free_gb": round(disk.free / 1024**3, 2),
            "vram": {
                "free_mb": vram.free_mb,
                "used_mb": vram.used_mb,
                "status": vram.status,
            },
        }

        # Action triggers per v2 §VIII health table.
        if disk.free < 10 * 1024**3:
            out["action_needed"] = True
            out["priority"] = Priority.HIGH
            out["summary"] = (
                f"Disk free is {out['disk_free_gb']} GB on the project drive "
                f"— under 10 GB threshold."
            )
        elif vram.status == "critical":
            out["action_needed"] = True
            out["priority"] = Priority.HIGH
            out["summary"] = f"VRAM critical: {vram.free_mb} MB free."
        elif cpu > 85 and vm.percent > 90:
            out["action_needed"] = True
            out["priority"] = Priority.NORMAL
            out["summary"] = (
                f"Sustained system load: CPU {cpu}%, RAM {vm.percent}%."
            )
        return out

    async def _check_ollama(self) -> dict[str, Any]:
        out: dict[str, Any] = {"host": settings.ollama_host}
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{settings.ollama_host}/api/tags")
            out["reachable"] = r.status_code == 200
            if r.status_code == 200:
                models = [m.get("name", "") for m in r.json().get("models", [])]
                out["model_present"] = any(settings.model in m for m in models)
                if not out["model_present"]:
                    out["action_needed"] = True
                    out["priority"] = Priority.HIGH
                    out["summary"] = (
                        f"Ollama is up but the configured model "
                        f"`{settings.model}` is no longer in the list."
                    )
        except (httpx.ConnectError, httpx.TimeoutException):
            out["reachable"] = False
            out["action_needed"] = True
            out["priority"] = Priority.HIGH
            out["summary"] = (
                f"Ollama at {settings.ollama_host} did not respond on this "
                "beat. Cannot serve LLM calls."
            )
        return out

    async def _check_pending_tasks(self) -> dict[str, Any]:
        with db.cursor() as cur:
            cur.execute(
                "SELECT id, type, command, scheduled_for FROM task_ledger "
                "WHERE status = 'pending' AND "
                "(scheduled_for IS NULL OR scheduled_for <= ?) "
                "ORDER BY scheduled_for ASC LIMIT 20",
                (datetime.utcnow(),),
            )
            rows = [dict(r) for r in cur.fetchall()]
        out: dict[str, Any] = {"due_count": len(rows)}
        if rows:
            out["due"] = [
                {"id": r["id"], "type": r["type"], "command": r["command"][:120]}
                for r in rows
            ]
            out["action_needed"] = True
            out["priority"] = Priority.NORMAL
            out["summary"] = f"{len(rows)} task(s) in the ledger are due."
        return out

    async def _check_active_window(self) -> dict[str, Any]:
        win = get_active_window()
        out: dict[str, Any] = {"window": win}

        if self._last_window is not None:
            same = (
                self._last_window.get("title") == win.get("title")
                and self._last_window.get("title") is not None
            )
            if not same:
                self._window_switches.append(time.time())
        self._last_window = win

        # Trim to last 10 minutes.
        cutoff = time.time() - 600
        self._window_switches = [t for t in self._window_switches if t >= cutoff]
        out["switches_last_10min"] = len(self._window_switches)

        if len(self._window_switches) >= 15:
            out["action_needed"] = True
            out["priority"] = Priority.LOW
            out["summary"] = (
                f"{len(self._window_switches)} window switches in the last "
                "10 minutes — context-switch overload."
            )
        return out

    async def _check_calendar(self) -> dict[str, Any]:
        """Soft check — if Google Calendar is wired, report next event."""
        try:
            from kee.tools.calendar_tool import tool as cal
            res = await cal.execute(action="upcoming", hours=2, max_results=3)
        except Exception:
            return {}
        if res.get("status") != "ok":
            return {}  # auth not done or transient error — quiet skip
        events = res.get("events") or []
        out: dict[str, Any] = {
            "next_count": len(events),
            "next": [
                {"summary": e["summary"], "start": e["start"]} for e in events[:3]
            ],
        }
        if events:
            # Closest event in the next 30 min → HIGH; under 2h → NORMAL
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            try:
                first_start_str = events[0]["start"]
                # Calendar API returns ISO 8601 with offset like +00:00
                first_start = datetime.fromisoformat(first_start_str.replace("Z", "+00:00"))
                minutes_to_event = (first_start - now).total_seconds() / 60.0
                out["minutes_to_first"] = round(minutes_to_event, 1)
                if 0 < minutes_to_event <= 30:
                    out["action_needed"] = True
                    out["priority"] = Priority.HIGH
                    out["summary"] = (
                        f"Calendar: '{events[0]['summary']}' starts in "
                        f"{int(minutes_to_event)} min."
                    )
            except (ValueError, KeyError, TypeError):
                pass
        return out

    async def _check_goal_deadlines(self) -> dict[str, Any]:
        upcoming = upcoming_deadlines(horizon_days=7)
        out: dict[str, Any] = {
            "upcoming_count": len(upcoming),
            "items": [
                {"title": g.title, "deadline": g.deadline.isoformat() if g.deadline else None,
                 "days_left": g.days_to_deadline()}
                for g in upcoming
            ],
        }
        if not upcoming:
            return out

        # Highest urgency wins.
        soonest = min((g.days_to_deadline() or 999) for g in upcoming)
        if soonest <= 1:
            out["action_needed"] = True
            out["priority"] = Priority.HIGH
            out["summary"] = (
                f"Goal deadline in {soonest} day(s): "
                f"{', '.join(g.title for g in upcoming if (g.days_to_deadline() or 999) <= 1)}"
            )
        elif soonest <= 3:
            out["action_needed"] = True
            out["priority"] = Priority.NORMAL
            out["summary"] = (
                f"Goal deadline in {soonest} day(s): "
                f"{', '.join(g.title for g in upcoming if (g.days_to_deadline() or 999) <= 3)}"
            )
        return out

    async def _check_market_alerts(self) -> dict[str, Any]:
        """Phase 8: walk the market watchlist + fire any breached threshold.

        Skips silently if the watchlist is empty (no symbols → no work).
        Notifications are deduped per-day inside `market.check_alerts`,
        so this can run every heartbeat without spam.
        """
        try:
            from kee.tools.market import check_alerts, load_watchlist
            wl = load_watchlist()
            if not wl:
                return {"watchlist_empty": True}
            r = await check_alerts(notify=True)
            out: dict[str, Any] = {
                "checked": r.get("checked", 0),
                "fired": r.get("fired", 0),
                "alerts": r.get("alerts", []),
            }
            if r.get("fired"):
                out["action_needed"] = True
                out["priority"] = Priority.HIGH
                out["summary"] = (
                    f"{r['fired']} market alert(s): " +
                    ", ".join(f"{a['symbol']} {a['trigger']}" for a in r["alerts"])
                )
            return out
        except Exception as e:
            return {"error": str(e)}

    async def _check_opportunity_scan(self) -> dict[str, Any]:
        """Proactive nudge over the persistence layer.

        Surfaces three categories the user usually forgets:
          - **Stalled plans** — pending >3 days but ≤30d (older are
            auto-archived by Sleep Cycle Phase 11).
          - **Unreviewed proposals** — identity_proposals + tool_rewrites
            in vault/_kee/ that are >7 days old.
          - **Goal deadlines closing** — already covered by goal_deadlines
            check, this one ignores those.

        Per-category cooldown: 6h. Fires LOW priority. Pure read.
        """
        try:
            import os as _os
            from kee.config import settings as _settings
            from kee.core import db as _db
            now = time.time()
            cd = getattr(self, "_opportunity_cooldown", {})
            self._opportunity_cooldown = cd

            stalled_plans: list[tuple[int, str]] = []
            try:
                con = _db.get_connection()
                rows = con.execute(
                    "SELECT id, task FROM plan_history "
                    "WHERE executed = 0 "
                    "AND timestamp <= datetime('now', '-3 days') "
                    "AND timestamp >= datetime('now', '-30 days') "
                    "ORDER BY timestamp ASC LIMIT 5"
                ).fetchall()
                stalled_plans = [(r[0], (r[1] or "")[:60]) for r in rows]
            except Exception:
                pass

            stale_proposals: list[str] = []
            try:
                from datetime import datetime as _dt, timedelta as _td
                cutoff = _dt.now() - _td(days=7)
                for sub in ("identity_proposals", "tool_rewrites"):
                    d = _settings.vault_dir / "_kee" / sub
                    if not d.exists():
                        continue
                    for p in d.glob("*.md"):
                        try:
                            if _dt.fromtimestamp(p.stat().st_mtime) < cutoff:
                                stale_proposals.append(f"{sub}/{p.name}")
                        except OSError:
                            continue
            except Exception:
                pass

            triggers: list[str] = []
            if stalled_plans:
                triggers.append(f"{len(stalled_plans)} planes pendientes >3d")
            if stale_proposals:
                triggers.append(f"{len(stale_proposals)} propuestas sin revisar >7d")

            out: dict[str, Any] = {
                "stalled_plans": stalled_plans,
                "stale_proposals": stale_proposals[:8],
            }
            if not triggers:
                return out

            # Cooldown by trigger fingerprint
            key = "|".join(triggers)
            last = cd.get(key, 0)
            if (now - last) < 6 * 3600:
                out["cooldown_active"] = True
                return out
            cd[key] = now

            out["action_needed"] = True
            out["priority"] = Priority.LOW
            parts = []
            if stalled_plans:
                top_id, top_task = stalled_plans[0]
                parts.append(
                    f"Plan #{top_id} '{top_task}' lleva >3d sin ejecutar "
                    f"(+ {len(stalled_plans) - 1} más)"
                    if len(stalled_plans) > 1 else
                    f"Plan #{top_id} '{top_task}' lleva >3d sin ejecutar"
                )
            if stale_proposals:
                parts.append(
                    f"{len(stale_proposals)} propuestas sin revisar "
                    f"(GET /cycle/pending)"
                )
            out["summary"] = "Oportunidades: " + " · ".join(parts)
            return out
        except Exception as e:
            return {"error": str(e)}

    async def _check_passive_perception(self) -> dict[str, Any]:
        """Continuous passive perception (v2 §III Gap 6).

        Opt-in via `KEE_PASSIVE_PERCEPTION=1` (default OFF for privacy).
        Captures a screenshot when:
          1. The active window TITLE has meaningfully changed since the
             last capture (≥40% Levenshtein-style char delta).
          2. At least `min_interval_min` (default 8) minutes have passed
             since the last capture.
          3. The window is NOT in the DND list (games / video / meeting).

        Sends to vision endpoint, stores description as
        `audit_log.action='perception_screenshot'` with
        `parameters={window_title, description, ts}`. The episodic
        indexer (Sleep Cycle Phase 13) picks these up automatically.

        No-op when:
          - `KEE_PASSIVE_PERCEPTION` env not set/false
          - vision endpoint unreachable
          - active window doesn't qualify for capture
        """
        import os as _os
        if _os.environ.get("KEE_PASSIVE_PERCEPTION", "0") not in ("1", "true", "on"):
            return {"enabled": False}

        min_interval_min = int(_os.environ.get(
            "KEE_PERCEPTION_MIN_INTERVAL_MIN", "8"))

        # 1. Need active window
        win = self._last_window or {}
        title = (win.get("title") or "").strip()
        if not title:
            return {"enabled": True, "skipped": "no active window"}

        # 2. DND gate (reuse the notification router's pattern list)
        try:
            from kee.perception.notification_router import _DND_WINDOW_PATTERNS
            tlow = title.lower()
            for pat in _DND_WINDOW_PATTERNS:
                if pat in tlow:
                    return {"enabled": True, "skipped": f"DND match: {pat}"}
        except Exception:
            pass

        # 3. Cooldown + change detection
        now = time.time()
        last = getattr(self, "_perception_last", None)
        if last:
            since_min = (now - last["ts"]) / 60
            if since_min < min_interval_min:
                # Same title and within cooldown → skip
                if last["title"] == title:
                    return {"enabled": True,
                            "skipped": f"cooldown ({since_min:.1f}m)"}
                # Significant title change overrides cooldown only if
                # we waited at least 1/3 of the interval.
                if since_min < min_interval_min / 3:
                    return {"enabled": True,
                            "skipped": f"title changed but min_interval/3 not met"}
                # Char-delta gate
                old = last["title"]
                if len(set(title) & set(old)) / max(1, len(set(old) | set(title))) > 0.7:
                    return {"enabled": True,
                            "skipped": "title change too small"}

        # 4. Capture
        try:
            from kee.tools.describe_screen import tool as ds
            t0 = time.time()
            res = await ds.execute(
                monitor=0,
                prompt="Describe brevemente qué app, ventana y contenido principal aparece. Una frase.",
                save_screenshot=False,
            )
            elapsed_ms = int((time.time() - t0) * 1000)
        except Exception as e:
            return {"enabled": True, "error": str(e)[:120]}

        if not res.get("ok"):
            return {"enabled": True, "vision_error": res.get("reason"),
                    "vision_detail": (res.get("error") or "")[:80]}

        desc = res.get("description", "").strip()
        if not desc:
            return {"enabled": True, "skipped": "vision returned empty"}

        # 5. Persist as audit row for the episodic indexer to pick up
        try:
            import json as _json
            from kee.core import db as _db
            with _db.cursor() as cur:
                cur.execute(
                    "INSERT INTO audit_log "
                    "(action, tool_name, success, parameters) "
                    "VALUES (?, ?, ?, ?)",
                    ("perception_screenshot", "passive_perception", 1,
                     _json.dumps({
                         "window_title": title,
                         "description": desc[:600],
                         "vision_ms": elapsed_ms,
                     }, ensure_ascii=False)),
                )
        except Exception as e:
            return {"enabled": True, "audit_error": str(e)[:120]}

        # Track for cooldown
        self._perception_last = {"ts": now, "title": title}

        return {
            "enabled": True,
            "captured": True,
            "window_title": title[:80],
            "description_preview": desc[:120],
            "elapsed_ms": elapsed_ms,
        }

    async def _check_worker_status(self) -> dict[str, Any]:
        """Detect Auctorum worker availability transitions.

        Fires LOW priority on each transition (offline → online or
        online → offline). Edge-triggered, so a stable up or stable
        down state stays silent. Lets the agent reset its memory
        retrieval expectations without polling the worker on every turn.
        """
        try:
            import os as _os
            url = _os.environ.get(
                "KEE_WORKER_HEALTH_URL",
                f"http://{_os.environ.get('AUCTORUM_HOST','auctorum')}:8080",
            ) + "/health"
            import httpx as _httpx
            try:
                async with _httpx.AsyncClient(timeout=4.0) as client:
                    r = await client.get(url)
                online = (r.status_code == 200
                          and bool((r.json() or {}).get("ok")))
            except Exception:
                online = False

            prev = getattr(self, "_worker_status_last", None)
            self._worker_status_last = online
            out: dict[str, Any] = {
                "online": online, "previous": prev, "url": url,
            }
            # Edge: only fire on transition (or first known up after init).
            if prev is None or prev == online:
                return out
            out["action_needed"] = True
            out["priority"] = Priority.LOW
            if online:
                out["summary"] = (
                    "Worker (Auctorum) volvió online — semantic memory "
                    "available again."
                )
            else:
                out["summary"] = (
                    "Worker (Auctorum) cayó — memory_search hace fallback, "
                    "vision y reranker indisponibles."
                )
            return out
        except Exception as e:
            return {"error": str(e)}

    async def _check_scheduled_callbacks(self) -> dict[str, Any]:
        """Fire any `scheduled_callbacks` row whose `fire_at <= now()`. One
        actionable per fired row so the agent can dispatch each individually
        (instead of bundling them into one prompt). The fan-out happens
        through the regular actionable pipeline downstream."""
        try:
            from kee.tools.schedule_self import fire_due_callbacks
            fired = fire_due_callbacks()
            if not fired:
                return {"due": 0}
            # The standard actionable mechanism only allows ONE
            # action_needed per check; bundle multiple fired callbacks into
            # one summary so all of them surface this tick.
            lines = [
                f"⏰ {f.get('message') or '(no message)'} "
                f"[{f['kind']} · id={f['id']}]"
                for f in fired
            ]
            return {
                "due": len(fired),
                "fired": fired,
                "action_needed": True,
                "priority": Priority.NORMAL,
                "summary": "Recordatorios listos:\n" + "\n".join(lines),
            }
        except Exception as e:
            return {"error": str(e)}

    async def _check_focus_drift(self) -> dict[str, Any]:
        """If a focus session is open, check whether the active window's
        title mentions the session's `project`. If not, bump `drift_count`
        and (after ≥3 consecutive drift bumps) fire an actionable so the
        agent can gently nudge Coco. Per-session 5-min cooldown so we
        don't over-bump."""
        try:
            from kee.tools.focus import _current as focus_current
            from kee.tools.focus import _bump_drift as focus_drift
            f = focus_current()
            if not f:
                return {"active_focus": None}

            # Cooldown
            now = time.time()
            cd = getattr(self, "_focus_drift_cooldown", {})
            self._focus_drift_cooldown = cd
            last = cd.get(f["id"], 0)
            if (now - last) < 5 * 60:
                return {"active_focus": f["project"], "cooldown_active": True}

            win = self._last_window or {}
            title = (win.get("title") or "").lower()
            project = (f["project"] or "").lower()
            on_topic = bool(project and project in title)
            out: dict[str, Any] = {
                "active_focus": f["project"],
                "active_window_title": win.get("title"),
                "on_topic": on_topic,
                "drift_count_before": f["drift_count"],
            }
            if on_topic:
                return out
            # Off topic — bump and possibly fire.
            res = focus_drift(reason=f"window={win.get('title')}")
            cd[f["id"]] = now
            out["drift_count_after"] = res.get("drift_count")
            # Fire only after 3 cumulative drifts in this session so a
            # quick context-switch doesn't spam.
            if (res.get("drift_count") or 0) >= 3:
                out["action_needed"] = True
                out["priority"] = Priority.LOW
                out["summary"] = (
                    f"Focus drift: trabajando '{f['project']}' pero la "
                    f"ventana es '{win.get('title')[:60] if win.get('title') else '?'}'. "
                    f"({out['drift_count_after']} drifts esta sesión)"
                )
            return out
        except Exception as e:
            return {"error": str(e)}

    async def _check_morning_brief(self) -> dict[str, Any]:
        """Fire once per day in the 7-10 AM local window with a one-line
        digest of yesterday + today's plan. Cheap — calls `reflect` (zero
        LLM) under the hood. Cooldown: per calendar day, persisted to an
        in-memory `_morning_brief_last_date` so a heartbeat restart can
        re-fire if Kee was offline at the trigger time.
        """
        try:
            now_local = datetime.now()
            hour = now_local.hour
            today_iso = now_local.date().isoformat()
            last_date = getattr(self, "_morning_brief_last_date", None)
            out: dict[str, Any] = {
                "hour": hour, "today": today_iso, "last_date": last_date,
            }
            # Window: 7..10 AM local, single fire per day
            if hour < 7 or hour >= 10:
                return out
            if last_date == today_iso:
                out["already_fired_today"] = True
                return out

            # Pull the snapshot synchronously inside the heartbeat tick.
            from kee.tools.reflect import tool as reflect_tool
            snap = await reflect_tool.execute(
                window_days=1, include_commits=True, include_inbox=False,
            )
            self._morning_brief_last_date = today_iso
            out["snap_summary"] = snap.get("summary")
            out["action_needed"] = True
            out["priority"] = Priority.NORMAL
            out["summary"] = (
                "Morning brief: " + (snap.get("summary") or "Nada nuevo.")
            )
            return out
        except Exception as e:
            return {"error": str(e)}

    async def _check_cognitive_health(self) -> dict[str, Any]:
        """Fires when Kee's *own* recent quality drops or when she's been
        leaning on tools the LLM keeps misusing. Pure SQL — no LLM cost.

        Triggers (any of):
          - rolling QA avg in the last 4h falls below 0.55 with ≥4 samples
          - ≥6 `kwarg_hallucination` rows in the last 1h on any tool
          - lifetime tool with `trust_score` < 0.30 was called in the last 1h

        Per-trigger cooldown: 30 min (in-memory) so we don't spam Coco.
        """
        import json as _json
        from kee.core import db as _db
        try:
            now = time.time()
            cd = getattr(self, "_cognitive_cooldown", {})
            self._cognitive_cooldown = cd

            con = _db.get_connection()

            # 1. QA avg over last 4h
            qa_rows = con.execute(
                "SELECT parameters FROM audit_log "
                "WHERE action='conversation_qa' "
                "AND timestamp >= datetime('now', '-4 hours')"
            ).fetchall()
            qa_avg = None
            qa_n = len(qa_rows)
            if qa_n:
                try:
                    scores = []
                    for r in qa_rows:
                        try:
                            scores.append(float(
                                _json.loads(r[0] or "{}").get("score") or 0
                            ))
                        except Exception:
                            continue
                    if scores:
                        qa_avg = sum(scores) / len(scores)
                except Exception:
                    pass

            # 2. Hallucination burst over last 1h
            halluc_count = con.execute(
                "SELECT COUNT(*) FROM audit_log "
                "WHERE action='kwarg_hallucination' "
                "AND timestamp >= datetime('now', '-1 hour')"
            ).fetchone()[0] or 0

            # 3. Wilson-low tool used in last 1h
            from kee.cognition.autonomy import wilson_lower_bound as _wlb
            recent_calls = con.execute(
                "SELECT tool_name, success FROM audit_log "
                "WHERE action='tool_call' AND tool_name IS NOT NULL "
                "AND timestamp >= datetime('now', '-1 hour')"
            ).fetchall()
            recent_names = {r[0] for r in recent_calls}
            untrusted: list[str] = []
            for name in recent_names:
                hist = con.execute(
                    "SELECT success FROM audit_log "
                    "WHERE action='tool_call' AND tool_name = ? "
                    "ORDER BY id DESC LIMIT 30",
                    (name,),
                ).fetchall()
                ok = sum(1 for r in hist if r[0])
                if hist and _wlb(ok, len(hist)) < 0.30:
                    untrusted.append(name)

            out: dict[str, Any] = {
                "qa_samples_4h": qa_n,
                "qa_avg_4h": (round(qa_avg, 3) if qa_avg is not None else None),
                "hallucinations_1h": halluc_count,
                "untrusted_tools_called_1h": untrusted,
            }

            triggers: list[str] = []
            if qa_avg is not None and qa_n >= 4 and qa_avg < 0.55:
                triggers.append(f"qa_avg={round(qa_avg,2)} (n={qa_n})")
            if halluc_count >= 6:
                triggers.append(f"halluc_burst={halluc_count}")
            if untrusted:
                triggers.append(
                    f"untrusted={untrusted[:3]}"
                )

            if not triggers:
                return out

            # Cooldown: dedup the EXACT trigger set within 30 min so a
            # constant low-QA condition doesn't spam.
            key = "|".join(sorted(triggers))
            last = cd.get(key, 0)
            if (now - last) < 30 * 60:
                out["cooldown_active"] = True
                return out
            cd[key] = now

            out["action_needed"] = True
            out["priority"] = Priority.NORMAL
            out["summary"] = (
                "Cognitive heartbeat: " + " · ".join(triggers)
            )
            return out
        except Exception as e:
            return {"error": str(e)}

    async def _check_biometric_state(self) -> dict[str, Any]:
        """Phase 8: coarse energy-level read from recent biometric samples.

        Silent when no biometric samples exist. Heavy-handed dedup: a
        critical/low actionable fires AT MOST once per 4 h regardless of
        heartbeat frequency, since these are advice events, not real
        emergencies — spamming "rest now" is worse than missing one.
        """
        try:
            from kee.perception import biometric as bio
            state = bio.score_recent_state(window_hours=12)
            out: dict[str, Any] = {**state}
            # No samples at all? Stay silent.
            if not state.get("samples_used"):
                return out
            level = state.get("energy_level")
            # 4-hour per-level cooldown (in-memory, resets on heartbeat
            # restart — that's intentional, a daemon restart should be
            # allowed to re-warn on the same condition).
            now = time.time()
            cd = getattr(self, "_biometric_cooldown", {})
            if level in ("critical", "low"):
                last = cd.get(level, 0)
                if (now - last) < 4 * 3600:
                    out["cooldown_active"] = True
                    return out
                cd[level] = now
                self._biometric_cooldown = cd
                out["action_needed"] = True
                out["priority"] = Priority.HIGH if level == "critical" else Priority.NORMAL
                out["summary"] = (
                    f"Biometric: {level} energy. "
                    + "; ".join(state.get("notes", []))
                )
            return out
        except Exception as e:
            return {"error": str(e)}
