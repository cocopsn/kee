"""Tool: windows — enumerate running applications and the foreground window.

Gives Kee context about what the user is doing right now without taking
a screenshot. Used by:
  - heartbeat (auto-tailor proactive offers based on active app)
  - the agent ("¿qué tengo abierto?", "cierra todo lo del navegador")
  - sleep_cycle (track which apps Coco uses most)

Risk: 0 (read-only) for list/active; 1 for focus (steals focus).
"""

from __future__ import annotations

from typing import Any

from kee.tools.base import Tool


def _list_running() -> list[dict]:
    """Cross-platform: pygetwindow on Windows, psutil everywhere."""
    apps: dict[str, dict] = {}
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "exe", "memory_info", "create_time"]):
            try:
                name = (p.info.get("name") or "").lower().replace(".exe", "")
                if not name or name in ("system", "registry", "idle"):
                    continue
                # Aggregate by executable
                key = name
                if key not in apps:
                    apps[key] = {
                        "name": name,
                        "pids": [],
                        "rss_mb": 0.0,
                        "started_at": p.info.get("create_time", 0),
                    }
                apps[key]["pids"].append(p.info["pid"])
                mem = p.info.get("memory_info")
                if mem:
                    apps[key]["rss_mb"] += round(mem.rss / (1024 * 1024), 1)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except Exception:
        return []
    return sorted(apps.values(), key=lambda x: x["rss_mb"], reverse=True)


def _frontmost() -> dict:
    """Active foreground window: title, app name, position."""
    try:
        import pygetwindow as gw
        w = gw.getActiveWindow()
        if not w:
            return {"ok": False, "error": "no active window"}
        return {
            "ok": True,
            "title": w.title,
            "left": w.left, "top": w.top,
            "width": w.width, "height": w.height,
            "minimized": w.isMinimized,
            "maximized": w.isMaximized,
        }
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _list_titles() -> list[str]:
    try:
        import pygetwindow as gw
        return [t for t in gw.getAllTitles() if t.strip()]
    except Exception:
        return []


def _focus(title_substr: str) -> dict:
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title_substr.lower() in w.title.lower()]
        if not matches:
            return {"ok": False, "error": f"no window matching {title_substr!r}"}
        w = matches[0]
        if w.isMinimized:
            w.restore()
        try:
            w.activate()
        except Exception:
            # Some windows don't activate cleanly; leave restored
            pass
        return {"ok": True, "focused": w.title}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def _close(title_substr: str) -> dict:
    try:
        import pygetwindow as gw
        matches = [w for w in gw.getAllWindows() if title_substr.lower() in w.title.lower()]
        if not matches:
            return {"ok": False, "error": f"no window matching {title_substr!r}"}
        closed = []
        for w in matches:
            try:
                w.close()
                closed.append(w.title)
            except Exception:
                pass
        return {"ok": bool(closed), "closed": closed}
    except Exception as e:
        return {"ok": False, "error": str(e)}


class WindowsTool(Tool):
    name = "windows"
    description = (
        "Enumerate running applications + the foreground window. Use to "
        "give the agent context about what Coco has open ('está en VSCode "
        "ahora'), and to focus or close specific windows by title.\n"
        "Actions:\n"
        "  - 'list':     all running apps with RSS + pid count\n"
        "  - 'active':   foreground window (title, geometry, state)\n"
        "  - 'titles':   all window titles\n"
        "  - 'focus':    bring a window to the front by title substring\n"
        "  - 'close':    close windows whose title contains the substring"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["list", "active", "titles", "focus", "close"],
                "default": "active",
            },
            "title": {"type": "string", "description": "Substring for focus/close"},
            "limit": {"type": "integer", "default": 30},
        },
    }

    async def execute(self, action: str = "active", title: str = "", limit: int = 30) -> dict[str, Any]:
        if action == "active":
            return _frontmost()
        if action == "list":
            apps = _list_running()[:limit]
            return {"ok": True, "count": len(apps), "apps": apps}
        if action == "titles":
            return {"ok": True, "titles": _list_titles()[:limit]}
        if action == "focus":
            if not title:
                return {"ok": False, "error": "title required"}
            return _focus(title)
        if action == "close":
            if not title:
                return {"ok": False, "error": "title required"}
            return _close(title)
        return {"ok": False, "error": f"unknown action '{action}'"}


tool = WindowsTool()
