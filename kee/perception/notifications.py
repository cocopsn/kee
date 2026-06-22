"""Cross-platform notification delivery — v2 §VI Phase 3.

Send-only for now (Kee → user). Receiving / intercepting incoming
notifications (D-Bus listener on Linux, UserNotificationListener on
Windows) is materially harder and lives in Phase 6 once the dashboard
exists. The Sleep Cycle daily digest, heartbeat actionables, and any
Phase 5 messaging surface call into here.

Backends:
  * Windows: `winotify` — native Toast notifications.
  * Linux:   `notify-send` (libnotify) via subprocess.
  * macOS / unknown: writes to `vault/_kee/notifications.log` as fallback
    so nothing is silently lost.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import sys
from datetime import datetime
from typing import Any

from kee.config import settings

logger = logging.getLogger(__name__)


_FALLBACK_LOG = settings.vault_dir / "_kee" / "notifications.log"


def _log_fallback(title: str, message: str, urgency: str) -> dict[str, Any]:
    _FALLBACK_LOG.parent.mkdir(parents=True, exist_ok=True)
    line = (
        f"[{datetime.now().isoformat(timespec='seconds')}] "
        f"({urgency}) {title}: {message}\n"
    )
    with _FALLBACK_LOG.open("a", encoding="utf-8") as f:
        f.write(line)
    return {"status": "fallback_log_only", "log_path": str(_FALLBACK_LOG)}


def _send_windows(
    title: str,
    message: str,
    urgency: str,
    icon_path: str | None,
    duration_s: int,
) -> dict[str, Any]:
    try:
        from winotify import Notification, audio  # type: ignore[import-not-found]
    except ImportError:
        return {"status": "missing_dependency", "error": "winotify not installed"}

    toast = Notification(
        app_id="Kee",
        title=title,
        msg=message,
        icon=icon_path or "",
        duration="long" if duration_s > 8 else "short",
    )
    if urgency == "critical":
        toast.set_audio(audio.LoopingAlarm, loop=False)
    toast.show()
    return {"status": "sent", "platform": "windows"}


def _send_linux(
    title: str,
    message: str,
    urgency: str,
    icon_path: str | None,
    duration_s: int,
) -> dict[str, Any]:
    if shutil.which("notify-send") is None:
        return {"status": "missing_dependency", "error": "notify-send not in PATH"}
    cmd = [
        "notify-send",
        "--app-name=Kee",
        f"--urgency={urgency}",
        f"--expire-time={duration_s * 1000}",
    ]
    if icon_path:
        cmd.append(f"--icon={icon_path}")
    cmd += [title, message]
    try:
        subprocess.run(cmd, check=True, capture_output=True, timeout=5)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        return {"status": "send_failed", "error": str(e)}
    return {"status": "sent", "platform": "linux"}


def send_notification(
    title: str,
    message: str,
    urgency: str = "normal",
    icon_path: str | None = None,
    duration_s: int = 5,
) -> dict[str, Any]:
    """Send a desktop notification. Always returns a dict — never raises.

    `urgency` is "low" | "normal" | "critical". It maps to platform
    semantics where possible (Linux libnotify supports it natively;
    Windows toast uses critical to play the looping alarm sound).
    """
    if urgency not in ("low", "normal", "critical"):
        urgency = "normal"
    try:
        if sys.platform == "win32":
            result = _send_windows(title, message, urgency, icon_path, duration_s)
        elif sys.platform.startswith("linux"):
            result = _send_linux(title, message, urgency, icon_path, duration_s)
        else:
            return _log_fallback(title, message, urgency)

        if result.get("status") not in ("sent",):
            # Always echo to fallback log so nothing is silently lost.
            _log_fallback(title, f"{message} | {result}", urgency)
        return result
    except Exception as e:
        logger.exception("notification send raised")
        _log_fallback(title, f"{message} | exception={e}", urgency)
        return {"status": "exception", "error": str(e)}


# ── Multi-channel async fan-out ──────────────────────────────────────────
async def notify_user(
    title: str,
    body: str,
    kind: str = "info",
    urgency: str = "normal",
    telegram: bool = True,
    desktop: bool = True,
) -> dict[str, Any]:
    """High-level user-facing notification. Fans out to:
      • Desktop toast (winotify on Windows, notify-send on Linux)
      • Telegram bot (sendMessage to allowed user IDs in env)

    `kind` is a free-form tag for audit/log filtering ("tool_created",
    "anomaly", "task_done", etc.). Each channel is best-effort — a failure
    on one doesn't stop the others.
    """
    import os
    out: dict[str, Any] = {"kind": kind, "channels": {}}
    if desktop:
        try:
            r = send_notification(title=title, message=body, urgency=urgency)
            out["channels"]["desktop"] = r.get("status", "?")
        except Exception as e:
            out["channels"]["desktop"] = f"err: {e}"
    if telegram:
        token = os.environ.get("KEE_TELEGRAM_TOKEN", "").strip()
        raw_users = os.environ.get("KEE_TELEGRAM_ALLOWED_USERS", "").strip()
        chat_ids = [u.strip() for u in raw_users.split(",")
                    if u.strip().isdigit()]
        if not (token and chat_ids):
            out["channels"]["telegram"] = "skipped (no token or numeric ids)"
        else:
            try:
                import httpx
                text = f"*{title}*\n\n{body}"
                sent = 0
                async with httpx.AsyncClient(timeout=5.0) as client:
                    for cid in chat_ids:
                        r = await client.post(
                            f"https://api.telegram.org/bot{token}/sendMessage",
                            json={"chat_id": cid, "text": text,
                                  "parse_mode": "Markdown"},
                        )
                        if r.status_code == 200:
                            sent += 1
                out["channels"]["telegram"] = f"sent to {sent}/{len(chat_ids)}"
            except Exception as e:
                out["channels"]["telegram"] = f"err: {e}"
    # Persist to notifications table for the dashboard inbox
    try:
        record_notification(
            direction="outbound",
            source=kind,
            title=title,
            body=body,
            urgency={"low": 0, "normal": 1, "critical": 2}.get(urgency, 1),
        )
    except Exception:
        pass
    return out


def record_notification(
    direction: str,
    source: str,
    body: str,
    title: str | None = None,
    urgency: int = 1,
    metadata: dict[str, Any] | None = None,
) -> int | None:
    """Persist a notification row. Returns the new row id, or None on error."""
    import json as _json
    from kee.core import db
    try:
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO notifications (direction, source, title, body, urgency, metadata) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (direction, source, title, body, urgency,
                 _json.dumps(metadata) if metadata else None),
            )
            return cur.lastrowid
    except Exception as e:
        logger.warning("record_notification failed: %s", e)
        return None
