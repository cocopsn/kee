"""Tool: home_assistant — Home Assistant REST API wrapper.

Phase 8 §"IoT / Home Assistant". Lets Kee read state from and call
services on a running Home Assistant instance — turn lights on, check
sensors, fire scripts/scenes, query history.

Configuration (env, both required for any non-status action):
  KEE_HASS_URL=http://homeassistant.local:8123
  KEE_HASS_TOKEN=<long-lived access token>
    (HA UI: Profile → Long-Lived Access Tokens → Create Token)

Without those env vars the tool degrades gracefully — `status` returns
"unconfigured" and other actions error out with a helpful pointer.

Risk:
  - read actions ('status', 'states', 'state', 'history'): 0
  - write actions ('call_service', 'fire_event'):           2
"""

from __future__ import annotations

import logging
import os
from typing import Any, Optional

import httpx

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


def _config() -> tuple[Optional[str], Optional[str]]:
    url = os.environ.get("KEE_HASS_URL", "").strip().rstrip("/")
    token = os.environ.get("KEE_HASS_TOKEN", "").strip()
    return (url or None, token or None)


def _headers(token: str) -> dict:
    return {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }


async def _get(path: str, timeout: float = 6.0) -> tuple[int, Any]:
    url, token = _config()
    if not url or not token:
        return 0, {"error": "unconfigured. Set KEE_HASS_URL and KEE_HASS_TOKEN."}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.get(url + path, headers=_headers(token))
            try:
                body = r.json()
            except Exception:
                body = r.text
            return r.status_code, body
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}


async def _post(path: str, payload: dict, timeout: float = 8.0) -> tuple[int, Any]:
    url, token = _config()
    if not url or not token:
        return 0, {"error": "unconfigured. Set KEE_HASS_URL and KEE_HASS_TOKEN."}
    try:
        async with httpx.AsyncClient(timeout=timeout) as c:
            r = await c.post(url + path, headers=_headers(token), json=payload)
            try:
                body = r.json()
            except Exception:
                body = r.text
            return r.status_code, body
    except Exception as e:
        return -1, {"error": f"{type(e).__name__}: {e}"}


class HomeAssistantTool(Tool):
    name = "home_assistant"
    description = (
        "Talk to a Home Assistant instance — read sensor states, turn "
        "lights on/off, fire scripts and scenes. Configure once with "
        "`KEE_HASS_URL` + `KEE_HASS_TOKEN` (long-lived access token) "
        "in .env. Without those env vars, status returns 'unconfigured' "
        "and other actions error out cleanly.\n"
        "Actions:\n"
        "  - 'status':       check connectivity + version (read, risk 0)\n"
        "  - 'states':       list all entity states (read, risk 0)\n"
        "  - 'state':        get one entity by id (read, risk 0)\n"
        "  - 'call_service': domain.service with optional target/data (write, risk 2)\n"
        "  - 'history':      last N hours of state changes for entity (read, risk 0)"
    )
    # Read by default; the agent's autonomy threshold escalates write
    # actions via `recommended_threshold` when needed.
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["status", "states", "state", "call_service", "history"],
                "default": "status",
            },
            "entity_id": {"type": "string", "description": "e.g. 'light.kitchen'"},
            "domain": {"type": "string", "description": "e.g. 'light' (for call_service)"},
            "service": {"type": "string", "description": "e.g. 'turn_on' (for call_service)"},
            "target": {"type": "object", "description": "{entity_id, area_id, device_id}"},
            "data": {"type": "object", "description": "service data payload"},
            "hours": {"type": "integer", "default": 24, "description": "history window"},
            "filter": {"type": "string", "description": "substring filter on entity_id (states)"},
        },
    }

    async def execute(
        self,
        action: str = "status",
        entity_id: Optional[str] = None,
        domain: Optional[str] = None,
        service: Optional[str] = None,
        target: Optional[dict] = None,
        data: Optional[dict] = None,
        hours: int = 24,
        filter: Optional[str] = None,
    ) -> dict[str, Any]:
        if action == "status":
            url, token = _config()
            if not url or not token:
                return {
                    "ok": False, "configured": False,
                    "hint": "Set KEE_HASS_URL + KEE_HASS_TOKEN in .env. "
                            "Get token via HA Profile → Long-Lived Access Tokens.",
                }
            code, body = await _get("/api/")
            return {
                "ok": code == 200, "configured": True, "url": url,
                "status_code": code, "message": body if isinstance(body, dict) else str(body)[:200],
            }

        if action == "states":
            code, body = await _get("/api/states")
            if code != 200:
                return {"ok": False, "status_code": code, "error": body}
            rows = body if isinstance(body, list) else []
            if filter:
                rows = [r for r in rows if filter.lower() in r.get("entity_id", "").lower()]
            slim = [
                {
                    "entity_id": r.get("entity_id"),
                    "state": r.get("state"),
                    "friendly_name": r.get("attributes", {}).get("friendly_name"),
                    "last_changed": r.get("last_changed"),
                }
                for r in rows
            ]
            return {"ok": True, "count": len(slim), "states": slim[:200]}

        if action == "state":
            if not entity_id:
                return {"ok": False, "error": "entity_id required"}
            code, body = await _get(f"/api/states/{entity_id}")
            return {"ok": code == 200, "status_code": code, "entity": body}

        if action == "call_service":
            if not domain or not service:
                return {"ok": False, "error": "domain + service required"}
            payload: dict = {}
            if data:
                payload.update(data)
            if target:
                # HA accepts target keys mixed into the payload.
                payload.update(target)
            if entity_id and "entity_id" not in payload:
                payload["entity_id"] = entity_id
            code, body = await _post(f"/api/services/{domain}/{service}", payload)
            return {
                "ok": code in (200, 201),
                "status_code": code,
                "domain": domain, "service": service,
                "payload": payload,
                "result": body if isinstance(body, list) else (body if isinstance(body, dict) else str(body)[:200]),
            }

        if action == "history":
            if not entity_id:
                return {"ok": False, "error": "entity_id required"}
            from datetime import datetime, timedelta, timezone
            since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
            code, body = await _get(
                f"/api/history/period/{since}?filter_entity_id={entity_id}",
                timeout=10.0,
            )
            if code != 200 or not isinstance(body, list) or not body:
                return {"ok": code == 200, "status_code": code, "history": []}
            entries = body[0]
            slim = [
                {"state": e.get("state"), "last_changed": e.get("last_changed")}
                for e in entries
            ]
            return {
                "ok": True, "entity_id": entity_id,
                "hours": hours, "count": len(slim), "history": slim,
            }

        return {"ok": False, "error": f"unknown action '{action}'"}


tool = HomeAssistantTool()
