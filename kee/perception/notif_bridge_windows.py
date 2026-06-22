"""Windows UserNotificationListener bridge.

Auto-ingests every toast notification that appears on the Windows shell
(WhatsApp Desktop, Slack, Discord, Outlook, browser web push, etc.) and
forwards them to Kee's `/notifications/inbound` endpoint so they appear
in the dashboard inbox + bell icon.

How it works:
  1. Request access to the UserNotificationListener (Windows shows a
     permission prompt the first time — Coco must say yes once, then the
     app remains in the "All apps notifications" allow-list forever).
  2. Poll `get_notifications_async` every 1 s for the user-visible feed.
  3. Diff against last-seen IDs; for each NEW one, extract title + body
     from the toast template and POST to /notifications/inbound.
  4. Loop forever.

Usage:  python -m kee.perception.notif_bridge_windows

Permission management:
  Windows Settings → System → Notifications → Additional settings →
  scroll to "All apps notifications" → enable for "Python" (the venv
  interpreter that runs Kee).
"""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from typing import Any

import httpx

logger = logging.getLogger(__name__)

# How often to poll (Windows pushes are not always observable as pubsub
# from this binding — polling is reliable and 1s is plenty for human-
# perceived "new toast" events).
POLL_INTERVAL_S = 1.0
KEE_API_BASE = os.environ.get("KEE_API_BASE", "http://127.0.0.1:7330")


async def _request_access() -> bool:
    """Returns True if the listener can read user notifications."""
    try:
        from winrt.windows.ui.notifications.management import (
            UserNotificationListener,
            UserNotificationListenerAccessStatus,
        )
    except ImportError as e:
        logger.error("winrt notification bindings missing: %s", e)
        return False
    listener = UserNotificationListener.current
    status = await listener.request_access_async()
    if status != UserNotificationListenerAccessStatus.ALLOWED:
        logger.error(
            "Notification access NOT granted (status=%s). "
            "Open Windows Settings → System → Notifications → Additional "
            "settings → 'All apps notifications' → toggle Python ON.",
            status,
        )
        return False
    return True


def _extract_text(notif) -> tuple[str | None, str]:
    """Pull (title, body) out of a UserNotification's toast XML."""
    title: str | None = None
    body_parts: list[str] = []
    try:
        toast_binding = notif.notification.visual.get_binding("ToastGeneric")
        if toast_binding is None:
            return None, ""
        for i, txt in enumerate(toast_binding.get_text_elements()):
            t = (txt.text or "").strip()
            if not t:
                continue
            if i == 0 and not title:
                title = t
            else:
                body_parts.append(t)
    except Exception as e:
        logger.debug("text extract failed: %s", e)
    return title, "\n".join(body_parts)


def _app_id(notif) -> str:
    try:
        return (notif.app_info.display_info.display_name or "windows").lower()
    except Exception:
        return "windows"


async def run() -> int:
    if not sys.platform.startswith("win"):
        logger.error("notif_bridge_windows is Windows-only")
        return 2
    ok = await _request_access()
    if not ok:
        return 3

    from winrt.windows.ui.notifications import NotificationKinds
    from winrt.windows.ui.notifications.management import UserNotificationListener
    listener = UserNotificationListener.current

    seen: set[int] = set()
    # Seed seen with whatever's already in the queue so we don't fire a
    # firehose of historical toasts on boot.
    initial = await listener.get_notifications_async(NotificationKinds.TOAST)
    for n in initial:
        seen.add(n.id)
    logger.info("notif_bridge ready: seeded %d existing toasts", len(seen))

    async with httpx.AsyncClient(timeout=4.0) as http:
        while True:
            try:
                current = await listener.get_notifications_async(NotificationKinds.TOAST)
                cur_ids = set()
                for n in current:
                    cur_ids.add(n.id)
                    if n.id in seen:
                        continue
                    title, body = _extract_text(n)
                    if not body and not title:
                        continue
                    source = _app_id(n)
                    payload: dict[str, Any] = {
                        "source": source,
                        "title": title,
                        "body": body or (title or ""),
                        "urgency": 1,
                        "metadata": {"native_id": n.id},
                    }
                    try:
                        r = await http.post(f"{KEE_API_BASE}/notifications/inbound", json=payload)
                        if r.status_code == 200:
                            logger.info("ingested: [%s] %s", source, (title or body)[:60])
                        else:
                            logger.warning("inbound POST %s: %s", r.status_code, r.text[:200])
                    except Exception as e:
                        logger.warning("POST failed: %s", e)
                # Forget IDs that left the system queue (Windows clears them)
                seen = (seen & cur_ids) | (cur_ids - seen)
            except Exception as e:
                logger.warning("poll cycle failed: %s", e)
            await asyncio.sleep(POLL_INTERVAL_S)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
