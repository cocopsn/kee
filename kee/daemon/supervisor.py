"""Multi-surface supervisor.

A single async process that owns the full Kee stack. Each surface runs as a
child process (``python -m kee.main <surface>``), with stdout+stderr appended
to the dedicated log file the Health page already knows how to read.

Why subprocess and not threads / async tasks?
- Each surface has its own VRAM/CPU profile and import cost. Crashing the
  voice pipeline must not take the API down.
- Uvicorn / openWakeWord / aiogram each install their own signal handlers
  and assume they own the process. Sharing a single Python interpreter is a
  reliability minefield.
- Subprocesses give us a hard kill button when something hangs.

Restart policy is per-surface:
- ``always`` (default): respawn forever with exponential backoff
  (``2s, 4s, 8s, 16s, 32s, 60s``) until the surface stays up for ``stable_window``
  seconds, after which the backoff resets.
- ``once``: run, log exit, do not restart (used for one-shot probes).

Shutdown handling: SIGINT / SIGTERM (and Windows CTRL_BREAK_EVENT via the
console) trigger a graceful drain — each child is sent the platform's
"please exit" signal, given ``shutdown_grace`` seconds, then SIGKILL'd.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from kee.config import settings

logger = logging.getLogger(__name__)


# ── Surface registry ──────────────────────────────────────────────────────
@dataclass
class SurfaceSpec:
    """Declarative description of a single supervised surface."""

    name: str
    args: list[str]                       # extra args after `python -m kee.main`
    log_filename: str                     # written under settings.data_dir
    enabled_default: bool = True
    restart: str = "always"               # "always" | "once"
    grace_s: float = 5.0                  # how long to wait for clean exit
    description: str = ""

    @property
    def log_path(self) -> Path:
        return settings.data_dir / self.log_filename

    @property
    def env_flag(self) -> str:
        return f"KEE_DAEMON_{self.name.upper().replace('-', '_')}"


# Single source of truth. The Health page log viewer already reads these
# filenames, so reusing them gives free integration.
SURFACES: list[SurfaceSpec] = [
    SurfaceSpec(
        name="api",
        args=["api"],
        log_filename="api.err",
        description="FastAPI backend (chat, dashboard, WS, REST endpoints).",
    ),
    SurfaceSpec(
        name="telegram",
        args=["telegram"],
        log_filename="telegram_bot.err",
        description="Telegram bot — long-poll, multi-turn ConversationState.",
    ),
    SurfaceSpec(
        name="notif-bridge",
        args=["notif-bridge"],
        log_filename="notif_bridge.err",
        description="Windows UserNotificationListener → /notifications/inbound.",
    ),
    SurfaceSpec(
        name="voice",
        args=["voice"],
        log_filename="voice.log",
        description="Always-listening wake-word (kee.onnx) → STT → agent → TTS.",
        # Voice is the heaviest surface: openWakeWord + faster-whisper +
        # Piper. Keep it in the default lineup now that kee.onnx ships, but
        # respect KEE_DAEMON_VOICE=0 to disable for headless smoke tests.
    ),
    SurfaceSpec(
        name="heartbeat",
        args=["heartbeat"],
        log_filename="heartbeat.log",
        description="5-min perception loop (window, calendar, goals, anomalies).",
    ),
    SurfaceSpec(
        name="sleep-cycle",
        args=[],          # no foreground subcommand; runs inside terminal --sleep-cycle
        log_filename="sleep_cycle.log",
        enabled_default=False,
        description="04:00 daily cognition pass — runs inside terminal mode.",
    ),
    SurfaceSpec(
        name="desktop",
        args=["desktop", "--mode", "hud"],
        log_filename="desktop.log",
        # Default ON — Coco asked for "Kee viviendo en la pantalla". Toggle
        # off with KEE_DAEMON_DESKTOP=0 if you're running headless / SSH.
        # The window starts visible in the corner; user can hide via the
        # `_` button (it goes to the system tray and the wake-word brings
        # it back).
        enabled_default=True,
        description="Native HUD window (pywebview over /app/hud, served by the API).",
    ),
]


# ── Per-surface runtime state ────────────────────────────────────────────
@dataclass
class SurfaceState:
    spec: SurfaceSpec
    proc: Optional[subprocess.Popen] = None
    started_at: float = 0.0
    restarts: int = 0
    last_exit_code: Optional[int] = None
    last_exit_at: float = 0.0
    backoff_s: float = 0.0
    enabled: bool = True
    log_handle: Optional[object] = field(default=None, repr=False)

    @property
    def alive(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    @property
    def pid(self) -> Optional[int]:
        return self.proc.pid if self.proc else None

    def to_dict(self) -> dict:
        return {
            "name": self.spec.name,
            "enabled": self.enabled,
            "alive": self.alive,
            "pid": self.pid,
            "started_at": self.started_at,
            "uptime_s": (time.time() - self.started_at) if self.alive else 0,
            "restarts": self.restarts,
            "last_exit_code": self.last_exit_code,
            "last_exit_at": self.last_exit_at,
            "backoff_s": self.backoff_s,
            "log_path": str(self.spec.log_path),
            "description": self.spec.description,
        }


# ── Supervisor ───────────────────────────────────────────────────────────
class Supervisor:
    """Owns every Kee surface and keeps them running."""

    state_filename = "supervisor_state.json"
    poll_interval_s = 1.0
    stable_window_s = 60.0          # uptime after which backoff resets
    backoff_ladder = [2, 4, 8, 16, 32, 60]
    shutdown_grace_s = 8.0

    def __init__(self, only: Optional[list[str]] = None) -> None:
        settings.ensure_dirs()
        self.states: dict[str, SurfaceState] = {}
        for spec in SURFACES:
            # Allow disabling per-surface via env (KEE_DAEMON_VOICE=0 etc.)
            env_val = os.environ.get(spec.env_flag)
            if env_val is not None:
                enabled = env_val.lower() not in ("0", "false", "no", "off")
            else:
                enabled = spec.enabled_default
            if only is not None:
                enabled = spec.name in only
            self.states[spec.name] = SurfaceState(spec=spec, enabled=enabled)

        self._stop_evt = asyncio.Event()
        # python.exe path: uvicorn etc. run inside the same venv as the
        # supervisor — guarantees we don't accidentally fall back to system
        # Python that is missing kee's deps.
        self._python = sys.executable

    # ── lifecycle ────────────────────────────────────────────────────
    def request_stop(self) -> None:
        if not self._stop_evt.is_set():
            logger.info("Supervisor stop requested")
            self._stop_evt.set()

    async def run(self) -> int:
        self._install_signal_handlers()
        logger.info("Kee supervisor starting (python=%s)", self._python)
        # Bulletproof: ensure local Ollama is up BEFORE spawning surfaces.
        # If the user reboots their laptop in a network where Auctorum is
        # unreachable (Tec wifi blocking outbound, plane mode, café
        # firewall), the agent must still have a local LLM. We don't
        # supervise ollama as a SurfaceSpec because its installer already
        # registers a Windows startup entry — we just refuse to leave it
        # dead at boot.
        try:
            self._ensure_ollama_alive()
        except Exception as e:
            logger.warning("ensure_ollama failed: %s — continuing anyway", e)
        for st in self.states.values():
            if st.enabled:
                self._spawn(st)
            else:
                logger.info("Surface %s disabled (skipping)", st.spec.name)
        self._write_state()

        try:
            while not self._stop_evt.is_set():
                self._reap_and_restart()
                self._write_state()
                try:
                    await asyncio.wait_for(self._stop_evt.wait(), timeout=self.poll_interval_s)
                except asyncio.TimeoutError:
                    continue
        finally:
            await self._shutdown_all()
            self._write_state()
        return 0

    # ── spawning ─────────────────────────────────────────────────────
    def _spawn(self, st: SurfaceState) -> None:
        spec = st.spec
        if not spec.args:
            logger.info("Surface %s has no foreground command; skipping spawn", spec.name)
            st.enabled = False
            return
        cmd = [self._python, "-m", "kee.main", *spec.args]
        # Make sure stdout isn't buffered, so logs land in the file
        # while a long surface is still running.
        env = os.environ.copy()
        env.setdefault("PYTHONUNBUFFERED", "1")
        # Mark child env so subprocesses know they're under the supervisor
        # (useful for muting interactive REPL prompts, etc.).
        env["KEE_SUPERVISED"] = "1"

        # Open log in append mode; supervisor never truncates user history.
        log = spec.log_path.open("a", encoding="utf-8", buffering=1)
        ts = time.strftime("%Y-%m-%dT%H:%M:%S")
        log.write(f"\n──── supervisor spawn {ts} (cmd={' '.join(cmd)}) ────\n")

        # Windows: CREATE_NEW_PROCESS_GROUP lets us send CTRL_BREAK_EVENT
        # for graceful shutdown without taking down the supervisor itself.
        creationflags = 0
        if sys.platform == "win32":
            creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                env=env,
                cwd=str(settings.project_root),
                creationflags=creationflags,
            )
        except Exception as e:
            logger.exception("Failed to spawn %s: %s", spec.name, e)
            try:
                log.write(f"[supervisor] spawn failed: {e}\n")
            finally:
                log.close()
            st.proc = None
            st.last_exit_code = -1
            st.last_exit_at = time.time()
            return

        st.proc = proc
        st.log_handle = log
        st.started_at = time.time()
        logger.info("Spawned %s pid=%s", spec.name, proc.pid)

    def _reap_and_restart(self) -> None:
        now = time.time()
        for st in self.states.values():
            if not st.enabled:
                continue
            if st.proc is None:
                # Cooling down? Honor backoff.
                if st.backoff_s > 0 and (now - st.last_exit_at) < st.backoff_s:
                    continue
                self._spawn(st)
                continue
            rc = st.proc.poll()
            if rc is None:
                # Still alive — see if we can clear the backoff after a stable run.
                if st.backoff_s and (now - st.started_at) >= self.stable_window_s:
                    logger.info("%s stable %ds — backoff cleared", st.spec.name, int(self.stable_window_s))
                    st.backoff_s = 0
                continue

            # Child exited.
            st.last_exit_code = rc
            st.last_exit_at = now
            if st.log_handle is not None:
                try:
                    st.log_handle.write(f"[supervisor] exited rc={rc} after {int(now - st.started_at)}s\n")
                    st.log_handle.flush()
                    st.log_handle.close()
                except Exception:
                    pass
                st.log_handle = None
            st.proc = None
            logger.warning("%s exited rc=%s", st.spec.name, rc)

            if st.spec.restart != "always":
                st.enabled = False
                continue

            # Bump backoff. Index = min(restarts, len-1).
            idx = min(st.restarts, len(self.backoff_ladder) - 1)
            st.backoff_s = float(self.backoff_ladder[idx])
            st.restarts += 1
            logger.info(
                "%s will respawn in %ds (restart #%d)",
                st.spec.name, int(st.backoff_s), st.restarts,
            )

    # ── shutdown ─────────────────────────────────────────────────────
    async def _shutdown_all(self) -> None:
        logger.info("Shutting down all surfaces…")
        # 1) ask politely
        for st in self.states.values():
            if st.proc and st.proc.poll() is None:
                self._signal(st, graceful=True)
        # 2) wait
        deadline = time.time() + self.shutdown_grace_s
        while time.time() < deadline and any(
            st.proc and st.proc.poll() is None for st in self.states.values()
        ):
            await asyncio.sleep(0.2)
        # 3) hard kill
        for st in self.states.values():
            if st.proc and st.proc.poll() is None:
                logger.warning("Force-killing %s pid=%s", st.spec.name, st.pid)
                try:
                    st.proc.kill()
                except Exception:
                    pass
            if st.log_handle is not None:
                try:
                    st.log_handle.close()
                except Exception:
                    pass
                st.log_handle = None

    def _signal(self, st: SurfaceState, graceful: bool) -> None:
        if not st.proc:
            return
        try:
            if sys.platform == "win32":
                # CTRL_BREAK_EVENT is the only signal that reliably reaches
                # children spawned with CREATE_NEW_PROCESS_GROUP on Windows.
                st.proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                st.proc.send_signal(signal.SIGTERM if graceful else signal.SIGKILL)
        except Exception as e:
            logger.warning("Signal to %s failed: %s", st.spec.name, e)

    # ── ollama bring-up ──────────────────────────────────────────────
    def _ensure_ollama_alive(self) -> None:
        """Make sure local Ollama is responding on settings.ollama_host.

        If it isn't, locate the ollama binary and spawn `ollama serve` as a
        detached process. We do NOT track it in `self.states` — Ollama
        owns its own lifecycle and its installer registers a Windows
        startup entry. This is purely a "the user just rebooted in a café
        and forgot to start Ollama" safety net.
        """
        import shutil
        import urllib.request

        host = settings.ollama_host or "http://localhost:11434"
        # Quick liveness probe (200ms is enough for localhost)
        try:
            with urllib.request.urlopen(f"{host}/api/tags", timeout=0.5) as r:  # noqa: S310
                if r.status == 200:
                    logger.info("Local Ollama already alive at %s", host)
                    return
        except Exception:
            pass  # not alive — try to spawn it

        # Locate the ollama binary
        ollama_bin = shutil.which("ollama")
        if not ollama_bin and sys.platform == "win32":
            # Common install locations on Windows
            candidates = [
                Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe",
                Path("C:/Program Files/Ollama/ollama.exe"),
            ]
            for c in candidates:
                if c.exists():
                    ollama_bin = str(c)
                    break

        if not ollama_bin:
            logger.warning(
                "Ollama not running and binary not found in PATH or "
                "%LOCALAPPDATA%/Programs/Ollama. Install from ollama.com."
            )
            return

        log_path = settings.data_dir / "ollama.log"
        log = log_path.open("a", encoding="utf-8", buffering=1)
        log.write(f"\n──── supervisor spawn ollama at {time.strftime('%Y-%m-%dT%H:%M:%S')} ────\n")
        creationflags = 0
        if sys.platform == "win32":
            # DETACHED_PROCESS so Ollama survives if the supervisor exits;
            # CREATE_NEW_PROCESS_GROUP so its console signals don't reach us.
            creationflags = (
                subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
                | 0x00000008  # DETACHED_PROCESS
            )
        try:
            subprocess.Popen(  # noqa: S603 — trusted binary path
                [ollama_bin, "serve"],
                stdout=log,
                stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                creationflags=creationflags,
            )
        except Exception as e:
            log.write(f"[supervisor] ollama spawn failed: {e}\n")
            logger.warning("Failed to spawn ollama serve: %s", e)
            return

        # Give it ~5s to come up under flash attention before probing.
        for _ in range(10):
            time.sleep(0.5)
            try:
                with urllib.request.urlopen(f"{host}/api/tags", timeout=0.5) as r:  # noqa: S310
                    if r.status == 200:
                        logger.info("Spawned local Ollama at %s", host)
                        return
            except Exception:
                continue
        logger.warning(
            "Spawned ollama but it didn't respond within 5s. "
            "Check %s for details.", log_path,
        )

    # ── signal plumbing ──────────────────────────────────────────────
    def _install_signal_handlers(self) -> None:
        loop = asyncio.get_running_loop()
        # POSIX: add_signal_handler. Windows: fall back to signal.signal —
        # add_signal_handler isn't supported there.
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                loop.add_signal_handler(sig, self.request_stop)
            except (NotImplementedError, RuntimeError):
                signal.signal(sig, lambda *_: self.request_stop())
        if sys.platform == "win32":
            try:
                signal.signal(signal.SIGBREAK, lambda *_: self.request_stop())  # type: ignore[attr-defined]
            except Exception:
                pass

    # ── status persistence ───────────────────────────────────────────
    @property
    def state_path(self) -> Path:
        return settings.data_dir / self.state_filename

    def _write_state(self) -> None:
        payload = {
            "updated_at": time.time(),
            "supervisor_pid": os.getpid(),
            "surfaces": [st.to_dict() for st in self.states.values()],
        }
        try:
            tmp = self.state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            tmp.replace(self.state_path)
        except Exception as e:
            logger.warning("Failed to write supervisor state: %s", e)


# ── public entrypoint ────────────────────────────────────────────────────
async def run_supervisor(only: Optional[list[str]] = None) -> int:
    sup = Supervisor(only=only)
    return await sup.run()


def read_state() -> dict:
    """Read the latest supervisor snapshot from disk (used by /system/supervisor)."""
    path = settings.data_dir / Supervisor.state_filename
    if not path.exists():
        return {"running": False, "surfaces": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        # Mark stale if not refreshed in the last 10s — the supervisor pings
        # every second so anything older means it crashed or wasn't started.
        age = time.time() - float(data.get("updated_at", 0))
        data["stale_s"] = round(age, 1)
        data["running"] = age < 10
        return data
    except Exception as e:
        return {"running": False, "error": str(e), "surfaces": []}
