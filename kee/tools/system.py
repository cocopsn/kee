"""System status tool.

Returns CPU, memory, disk, uptime, and a quick check on whether the Auctorum
worker node is reachable. Cross-platform via `psutil`.

Risk: 0 (read-only).
"""

from __future__ import annotations

import platform
import socket
import time
from typing import Any

import httpx
import psutil

from kee.config import settings
from kee.tools.base import Tool

_BOOT_TIME = psutil.boot_time()


class SystemStatusTool(Tool):
    name = "system_status"
    description = (
        "Report the host machine's CPU, memory, disk, uptime and network "
        "reachability of the Auctorum worker node. Use to answer questions "
        "about system health or before starting heavy work."
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "include_auctorum": {
                "type": "boolean",
                "description": "Probe the Auctorum worker for reachability.",
                "default": True,
            },
        },
    }

    async def execute(self, include_auctorum: bool = True) -> dict[str, Any]:
        vm = psutil.virtual_memory()
        disk_target = str(settings.project_root)
        disk = psutil.disk_usage(disk_target)
        cpu_pct = psutil.cpu_percent(interval=0.2)
        uptime_s = int(time.time() - _BOOT_TIME)

        out: dict[str, Any] = {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "cpu": {
                "count": psutil.cpu_count(),
                "usage_pct": cpu_pct,
            },
            "memory": {
                "total_gb": round(vm.total / 1024**3, 2),
                "available_gb": round(vm.available / 1024**3, 2),
                "used_pct": vm.percent,
            },
            "disk": {
                "path": disk_target,
                "total_gb": round(disk.total / 1024**3, 2),
                "free_gb": round(disk.free / 1024**3, 2),
                "used_pct": disk.percent,
            },
            "uptime_s": uptime_s,
            "ollama_host": settings.ollama_host,
        }

        if include_auctorum:
            out["auctorum"] = await self._probe_auctorum()

        return out

    async def _probe_auctorum(self) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=2.0) as client:
                r = await client.get(f"{settings.auctorum_ollama}/api/tags")
            return {"reachable": r.status_code == 200, "status": r.status_code}
        except (httpx.ConnectError, httpx.TimeoutException):
            return {"reachable": False, "status": None}


tool = SystemStatusTool()
