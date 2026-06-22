"""Google Calendar tool — read upcoming events + create events.

Required scopes: `https://www.googleapis.com/auth/calendar` (read+write)
or `…/calendar.readonly` if you only want read.

First call triggers `python -m kee.main google-auth` flow if no token yet.
After that, the agent can call this transparently.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from kee.distributed.google_oauth import get_credentials
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_SCOPES_RW = ["https://www.googleapis.com/auth/calendar"]
_SCOPES_RO = ["https://www.googleapis.com/auth/calendar.readonly"]


def _service(scopes):
    from googleapiclient.discovery import build
    creds = get_credentials(scopes, interactive=False)
    return build("calendar", "v3", credentials=creds, cache_discovery=False)


class CalendarTool(Tool):
    name = "calendar"
    description = (
        "Query / write Google Calendar. Actions:\n"
        "  - 'upcoming': events in the next `hours` hours (default 24). "
        "Filter by `calendar_id` (default 'primary').\n"
        "  - 'today': all events today (local time).\n"
        "  - 'list_calendars': show available calendar IDs/names.\n"
        "  - 'create': create event with `summary`, `start_iso`, `end_iso` "
        "(ISO 8601 with timezone), optional `description`, `location`, "
        "`attendees` (list of emails). RISK 2 — creates real entries.\n"
        "Setup: needs Google OAuth client. Run `python -m kee.main "
        "google-auth` once if not yet authorized."
    )
    risk_level = 1  # bumps to 2 in `execute` when action='create'
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["upcoming", "today", "list_calendars", "create"],
                "default": "upcoming",
            },
            "calendar_id": {"type": "string", "default": "primary"},
            "hours": {"type": "integer", "default": 24},
            "max_results": {"type": "integer", "default": 25},
            # for action='create'
            "summary": {"type": "string"},
            "description": {"type": "string"},
            "location": {"type": "string"},
            "start_iso": {"type": "string"},
            "end_iso": {"type": "string"},
            "attendees": {"type": "array", "items": {"type": "string"}},
        },
    }

    async def execute(
        self,
        action: str = "upcoming",
        calendar_id: str = "primary",
        hours: int = 24,
        max_results: int = 25,
        summary: str | None = None,
        description: str | None = None,
        location: str | None = None,
        start_iso: str | None = None,
        end_iso: str | None = None,
        attendees: list[str] | None = None,
    ) -> dict[str, Any]:
        try:
            svc = _service(_SCOPES_RW if action == "create" else _SCOPES_RO)
        except RuntimeError as e:
            return {"status": "auth_required", "error": str(e)}

        try:
            if action == "list_calendars":
                resp = svc.calendarList().list().execute()
                items = resp.get("items", [])
                return {
                    "status": "ok",
                    "calendars": [
                        {"id": c["id"], "summary": c.get("summary"),
                         "primary": c.get("primary", False),
                         "tz": c.get("timeZone")}
                        for c in items
                    ],
                }

            if action in ("upcoming", "today"):
                now = datetime.now(timezone.utc)
                if action == "today":
                    start_local = datetime.now().astimezone().replace(
                        hour=0, minute=0, second=0, microsecond=0,
                    )
                    end_local = start_local + timedelta(days=1)
                    time_min = start_local.astimezone(timezone.utc).isoformat()
                    time_max = end_local.astimezone(timezone.utc).isoformat()
                else:
                    time_min = now.isoformat()
                    time_max = (now + timedelta(hours=hours)).isoformat()
                resp = svc.events().list(
                    calendarId=calendar_id,
                    timeMin=time_min, timeMax=time_max,
                    maxResults=max_results, singleEvents=True,
                    orderBy="startTime",
                ).execute()
                events = resp.get("items", [])
                out = []
                for e in events:
                    start = e["start"].get("dateTime") or e["start"].get("date")
                    end = e["end"].get("dateTime") or e["end"].get("date")
                    out.append({
                        "id": e["id"],
                        "summary": e.get("summary", "(no title)"),
                        "start": start, "end": end,
                        "location": e.get("location"),
                        "attendees": [a.get("email") for a in e.get("attendees", [])],
                        "html_link": e.get("htmlLink"),
                    })
                return {"status": "ok", "count": len(out), "events": out}

            if action == "create":
                if not (summary and start_iso and end_iso):
                    return {"error": "create needs summary + start_iso + end_iso"}
                body = {
                    "summary": summary,
                    "start": {"dateTime": start_iso},
                    "end": {"dateTime": end_iso},
                }
                if description: body["description"] = description
                if location: body["location"] = location
                if attendees:
                    body["attendees"] = [{"email": a} for a in attendees]
                created = svc.events().insert(
                    calendarId=calendar_id, body=body, sendUpdates="all",
                ).execute()
                return {
                    "status": "created",
                    "event_id": created["id"],
                    "html_link": created.get("htmlLink"),
                    "summary": created.get("summary"),
                }

            return {"error": f"unknown action {action!r}"}

        except Exception as e:
            return {"status": "error", "error": f"{type(e).__name__}: {str(e)[:300]}"}


tool = CalendarTool()
