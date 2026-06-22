"""Tool: clipboard — read + write the OS clipboard.

Universal bridge between apps. Voice flow:
  "Kee, copia esto: ..."          → clipboard(action='set', text='...')
  "Kee, qué tengo en el clipboard" → clipboard(action='get')
  "Kee, pega esto en el doc"      → another tool reads via 'get'

Risk:
  - get: 1 (could exfiltrate sensitive data — bound to user intent)
  - set: 0
  - clear: 0
"""

from __future__ import annotations

from typing import Any

from kee.tools.base import Tool


def _get_clipboard() -> tuple[bool, str]:
    """Try multiple backends. Returns (ok, text|error)."""
    # 1. pyperclip — pip-installable, cross-platform
    try:
        import pyperclip
        return True, pyperclip.paste() or ""
    except Exception:
        pass
    # 2. Windows: pywin32 native
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            data = win32clipboard.GetClipboardData(win32clipboard.CF_UNICODETEXT)
            return True, data or ""
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        return False, str(e)


def _set_clipboard(text: str) -> tuple[bool, str]:
    try:
        import pyperclip
        pyperclip.copy(text)
        return True, f"copied {len(text)} chars"
    except Exception:
        pass
    try:
        import win32clipboard
        win32clipboard.OpenClipboard()
        try:
            win32clipboard.EmptyClipboard()
            win32clipboard.SetClipboardText(text)
            return True, f"copied {len(text)} chars"
        finally:
            win32clipboard.CloseClipboard()
    except Exception as e:
        return False, str(e)


class ClipboardTool(Tool):
    name = "clipboard"
    description = (
        "Read or write the OS clipboard. Useful when the user says 'copia "
        "esto', 'pégalo en X', 'qué tengo copiado'. Cross-platform via "
        "pyperclip with a Windows-native fallback.\n"
        "Actions: 'get' (read), 'set' (write text), 'clear' (empty)."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["get", "set", "clear"],
                "default": "get",
            },
            "text": {"type": "string", "description": "Text to set (when action='set')"},
        },
    }

    async def execute(self, action: str = "get", text: str = "") -> dict[str, Any]:
        if action == "get":
            ok, val = _get_clipboard()
            return {"ok": ok, "text": val if ok else None,
                    "length": len(val) if ok else 0,
                    "error": None if ok else val}
        if action == "set":
            if not text:
                return {"ok": False, "error": "text is required for action='set'"}
            ok, msg = _set_clipboard(text)
            return {"ok": ok, "message": msg if ok else None,
                    "error": None if ok else msg}
        if action == "clear":
            ok, msg = _set_clipboard("")
            return {"ok": ok, "message": "clipboard cleared" if ok else None,
                    "error": None if ok else msg}
        return {"ok": False, "error": f"unknown action '{action}'"}


tool = ClipboardTool()
