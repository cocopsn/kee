"""Tool: wol — Wake-on-LAN packet sender.

Phase 8 (Extended Ecosystem). Wakes up the Auctorum worker (or any other
machine on the LAN) by sending a magic packet to its NIC's MAC address.

Risk: 1 (network-side effect — wakes up a remote machine, but reversible
the same way it always was: turn the machine off again).

Configuration: pass MAC explicitly OR set ``KEE_WORKER_MAC`` in the env
to allow `wol(action='wake_worker')` to use it without args.
"""

from __future__ import annotations

import os
import socket
from typing import Any

from kee.tools.base import Tool


def _send_magic_packet(mac: str, broadcast: str = "255.255.255.255",
                       port: int = 9) -> bool:
    """Build + send a Wake-on-LAN magic packet.

    Magic packet = 6 bytes of 0xFF + 16 repetitions of the target MAC.
    Sent UDP to the LAN broadcast address on port 7 or 9 (both standard).
    """
    cleaned = mac.replace(":", "").replace("-", "").replace(".", "")
    if len(cleaned) != 12:
        return False
    try:
        mac_bytes = bytes.fromhex(cleaned)
    except ValueError:
        return False
    packet = b"\xff" * 6 + mac_bytes * 16
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        s.sendto(packet, (broadcast, port))
    return True


class WakeOnLanTool(Tool):
    name = "wol"
    description = (
        "Send a Wake-on-LAN magic packet to a machine on the LAN. Useful "
        "for waking the Auctorum worker before kicking off a heavy task "
        "(reranker, ChromaDB, Gemma vision). Configure once with "
        "`KEE_WORKER_MAC=aa:bb:cc:dd:ee:ff`, then call "
        "`wol(action='wake_worker')` — or pass `mac=...` explicitly.\n"
        "Actions:\n"
        "  - 'wake_worker': use KEE_WORKER_MAC if set, error otherwise\n"
        "  - 'wake':        explicit MAC + optional broadcast/port\n"
        "  - 'status':      report what's configured"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["wake_worker", "wake", "status"],
                "default": "wake_worker",
            },
            "mac": {
                "type": "string",
                "description": "MAC address (aa:bb:cc:dd:ee:ff). Required for 'wake'.",
            },
            "broadcast": {
                "type": "string",
                "default": "255.255.255.255",
                "description": "LAN broadcast address. Subnet broadcast (e.g. 192.168.1.255) is more reliable across switches.",
            },
            "port": {"type": "integer", "default": 9},
            "ports": {
                "type": "array",
                "items": {"type": "integer"},
                "description": "Send to multiple ports (default [7, 9]).",
            },
        },
    }

    async def execute(
        self,
        action: str = "wake_worker",
        mac: str | None = None,
        broadcast: str = "255.255.255.255",
        port: int = 9,
        ports: list[int] | None = None,
    ) -> dict[str, Any]:
        if action == "status":
            cfg_mac = os.environ.get("KEE_WORKER_MAC", "").strip()
            cfg_bcast = os.environ.get("KEE_WORKER_BROADCAST", "").strip()
            return {
                "ok": True,
                "configured_mac": cfg_mac or None,
                "configured_broadcast": cfg_bcast or None,
                "hostname": socket.gethostname(),
            }

        if action == "wake_worker":
            mac = (mac or os.environ.get("KEE_WORKER_MAC", "")).strip()
            broadcast = broadcast or os.environ.get(
                "KEE_WORKER_BROADCAST", "255.255.255.255",
            )
            if not mac:
                return {
                    "ok": False,
                    "error": "no MAC configured. Set KEE_WORKER_MAC=aa:bb:cc:dd:ee:ff in .env or pass mac=...",
                }

        if not mac:
            return {"ok": False, "error": "mac is required for action='wake'"}

        send_ports = ports if ports else [port, 7]
        results: list[dict] = []
        for p in send_ports:
            sent = _send_magic_packet(mac, broadcast, p)
            results.append({"port": p, "sent": sent})

        any_ok = any(r["sent"] for r in results)
        return {
            "ok": any_ok,
            "mac": mac,
            "broadcast": broadcast,
            "ports": send_ports,
            "results": results,
            "note": "Magic packet dispatched. Target may take 30-90s to fully boot.",
        }


tool = WakeOnLanTool()
