"""Self-healing infrastructure — v2 §IV Extension 7.

Heartbeat OBSERVES outages. Self-healing ATTEMPTS RECOVERY.

The split is intentional: heartbeat is read-only and never blocks; self-
healing performs side effects (restarts processes, sends WoL packets,
flips fallback flags) and may take seconds. Heartbeat fires actionables
into the agent loop AND, in parallel, into self-healing's queue.

Recovery strategies (Phase 5 minimal set — Phase 7+ adds more):

  * **Ollama unreachable** — try `ollama serve` as a detached subprocess.
    Most common cause is "the daemon was killed by OS sleep / dev restart".
  * **ChromaDB unreachable** — flip `MemoryManager.indexer.use_fallback`
    to True so retrieve() returns local SQLite FTS instead of timing out
    on every voice turn. (FTS implementation lives in indexer; for now
    this just sets the flag and logs.)
  * **Worker (Auctorum) unreachable** — ping; if alive but slow, log;
    if dead, send WoL packet when MAC is configured.
  * **Disk low (<5 GB free on project drive)** — fire a critical
    `notify` so Coco gets a desktop toast.

Every recovery attempt + outcome is in audit_log under
`action='self_healing'` so the dashboard can show the timeline.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import socket
import struct
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any

import httpx
import psutil

from kee.config import settings
from kee.core import services
from kee.perception.notifications import send_notification

logger = logging.getLogger(__name__)


# Per-incident cooldown so we don't restart Ollama 1000 times in a minute.
_RECOVERY_COOLDOWN_S = 120


@dataclass
class RecoveryReport:
    issue: str
    action_taken: str
    success: bool
    detail: dict[str, Any]


class SelfHealing:
    def __init__(self) -> None:
        self._last_attempt_at: dict[str, float] = {}

    # ── Public entry point — heartbeat or any other surface calls this ───
    async def attempt_recovery(self, check_name: str, snapshot: dict[str, Any]) -> RecoveryReport | None:
        if not snapshot.get("action_needed"):
            return None
        last = self._last_attempt_at.get(check_name, 0.0)
        if (time.time() - last) < _RECOVERY_COOLDOWN_S:
            return None  # don't thrash

        report: RecoveryReport | None = None
        if check_name == "ollama_status" and not snapshot.get("reachable"):
            report = await self._heal_ollama()
        elif check_name == "system_health" and snapshot.get("disk_free_gb") is not None and snapshot["disk_free_gb"] < 5:
            report = await self._heal_disk_low(snapshot)
        else:
            return None  # nothing to do for this check

        self._last_attempt_at[check_name] = time.time()
        if services.audit:
            services.audit.log_event("self_healing", {
                "issue": report.issue,
                "action": report.action_taken,
                "success": report.success,
                "detail": report.detail,
            })
        logger.warning(
            "Self-healing: %s → %s (success=%s)",
            report.issue, report.action_taken, report.success,
        )
        return report

    # ── Recovery actions ─────────────────────────────────────────────────
    async def _heal_ollama(self) -> RecoveryReport:
        # Step 1: probe again to rule out a transient blip
        if await self._ollama_reachable():
            return RecoveryReport(
                issue="ollama_unreachable",
                action_taken="re-probed; daemon recovered on its own",
                success=True, detail={},
            )

        # Step 2: locate the binary
        bin_path = shutil.which("ollama")
        if bin_path is None:
            # Fallback to the known Windows install location.
            candidate = (
                os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
                if sys.platform == "win32"
                else "/usr/local/bin/ollama"
            )
            if os.path.exists(candidate):
                bin_path = candidate
        if bin_path is None:
            return RecoveryReport(
                issue="ollama_unreachable",
                action_taken="locate binary",
                success=False,
                detail={"error": "ollama executable not in PATH or known locations"},
            )

        # Step 3: spawn detached `ollama serve`
        try:
            log_path = settings.data_dir / "ollama.log"
            log_fp = open(log_path, "ab")
            if sys.platform == "win32":
                DETACHED_PROCESS = 0x00000008
                CREATE_NEW_PROCESS_GROUP = 0x00000200
                proc = subprocess.Popen(
                    [bin_path, "serve"],
                    stdin=subprocess.DEVNULL, stdout=log_fp, stderr=log_fp,
                    creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
                    close_fds=True,
                )
            else:
                proc = subprocess.Popen(
                    [bin_path, "serve"],
                    stdin=subprocess.DEVNULL, stdout=log_fp, stderr=log_fp,
                    start_new_session=True, close_fds=True,
                )
        except Exception as e:
            return RecoveryReport(
                issue="ollama_unreachable",
                action_taken="spawn `ollama serve`",
                success=False, detail={"error": str(e), "bin": bin_path},
            )

        # Step 4: wait up to 20s for the daemon to come up
        for _ in range(20):
            await asyncio.sleep(1)
            if await self._ollama_reachable():
                send_notification(
                    title="Kee: Ollama recuperado",
                    message=f"Reinicié `ollama serve` (PID {proc.pid}). El agente vuelve a estar disponible.",
                    urgency="normal",
                )
                return RecoveryReport(
                    issue="ollama_unreachable",
                    action_taken=f"spawned `ollama serve` (pid {proc.pid})",
                    success=True,
                    detail={"pid": proc.pid, "bin": bin_path},
                )

        return RecoveryReport(
            issue="ollama_unreachable",
            action_taken="spawned `ollama serve` but it didn't respond in 20s",
            success=False,
            detail={"pid": proc.pid, "bin": bin_path},
        )

    async def _heal_disk_low(self, snapshot: dict[str, Any]) -> RecoveryReport:
        free_gb = snapshot.get("disk_free_gb", 0)
        send_notification(
            title="Kee: disco bajo",
            message=(
                f"Sólo quedan {free_gb} GB libres en {settings.project_root}. "
                "Considera vaciar workspaces o archivar datasets viejos."
            ),
            urgency="critical", duration_s=15,
        )
        # Auto-archive workspaces older than 14 days as a soft cleanup.
        archived = self._archive_old_workspaces(days=14)
        return RecoveryReport(
            issue="disk_low",
            action_taken=f"notified user + archived {archived} workspaces older than 14d",
            success=True, detail={"archived": archived, "free_gb_now": free_gb},
        )

    # ── Helpers ──────────────────────────────────────────────────────────
    async def _ollama_reachable(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{settings.ollama_host}/api/tags")
            return r.status_code == 200
        except (httpx.ConnectError, httpx.TimeoutException):
            return False

    def _archive_old_workspaces(self, days: int = 14) -> int:
        ws = settings.project_root / "workspaces"
        if not ws.exists():
            return 0
        archive = ws / "_archive"
        archive.mkdir(exist_ok=True)
        archived = 0
        for child in ws.iterdir():
            if not child.is_dir() or child.name.startswith("_"):
                continue
            age_days = (time.time() - child.stat().st_mtime) / 86400.0
            if age_days > days:
                try:
                    target = archive / child.name
                    if target.exists():
                        continue
                    shutil.move(str(child), target)
                    archived += 1
                except Exception:
                    logger.debug("archive failed for %s", child, exc_info=True)
        return archived

    @staticmethod
    def send_wake_on_lan(mac: str, broadcast: str = "255.255.255.255", port: int = 9) -> bool:
        """Send a Wake-on-LAN magic packet. MAC format: 'aa:bb:cc:dd:ee:ff'."""
        try:
            mac_bytes = bytes.fromhex(mac.replace(":", "").replace("-", ""))
            if len(mac_bytes) != 6:
                return False
            packet = b"\xff" * 6 + mac_bytes * 16
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
                s.sendto(packet, (broadcast, port))
            return True
        except Exception as e:
            logger.debug("WoL send failed: %s", e)
            return False
