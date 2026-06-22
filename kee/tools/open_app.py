"""App orchestrator — open and focus desktop applications.

The everyday voice command is "abre VS Code" / "open Spotify". This tool
makes that work:

  1. If `focus_if_open=True` (default), try to find an already-running
     window for the app and bring it to the foreground first.
  2. Otherwise spawn a detached subprocess so Kee doesn't block on the
     child's lifetime.

The app registry maps short logical names (`vscode`, `firefox`, …) to a
list of candidate commands; the first one that resolves wins. Unknown
app names fall through to the literal value the LLM gave us — so
`open_app(app="custom_thing.exe")` also works without registration.

Risk: 1 (launches a new process; not destructive but visible).
"""

from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


# ── App registry ─────────────────────────────────────────────────────────
# Each entry: (commands_to_try, window_title_substrings_for_focus_match)
# Commands try `which`/`shutil.which` first; if missing, fall back to the
# explicit file paths listed below. Keep aliases generous — the model
# might say "code", "vscode", "visual studio code".
_APPS_WIN: dict[str, dict[str, Any]] = {
    "vscode": {
        "commands": ["code.cmd", "code.exe", "code"],
        "paths": [
            r"%LOCALAPPDATA%\Programs\Microsoft VS Code\Code.exe",
            r"%PROGRAMFILES%\Microsoft VS Code\Code.exe",
        ],
        "title_match": ["Visual Studio Code"],
        "aliases": ["code", "visual studio code", "vs code"],
    },
    "firefox": {
        "commands": ["firefox.exe", "firefox"],
        "paths": [r"%PROGRAMFILES%\Mozilla Firefox\firefox.exe"],
        "title_match": ["Mozilla Firefox", "Firefox"],
        "aliases": ["mozilla"],
    },
    "chrome": {
        "commands": ["chrome.exe", "chrome"],
        "paths": [
            r"%PROGRAMFILES%\Google\Chrome\Application\chrome.exe",
            r"%PROGRAMFILES(X86)%\Google\Chrome\Application\chrome.exe",
        ],
        "title_match": ["Google Chrome"],
        "aliases": ["google chrome"],
    },
    "obsidian": {
        "commands": ["obsidian.exe", "obsidian"],
        "paths": [r"%LOCALAPPDATA%\Programs\Obsidian\Obsidian.exe"],
        "title_match": ["Obsidian"],
        "aliases": [],
    },
    "spotify": {
        "commands": ["spotify.exe", "spotify"],
        "paths": [r"%APPDATA%\Spotify\Spotify.exe"],
        "title_match": ["Spotify"],
        "aliases": [],
    },
    "discord": {
        "commands": ["discord.exe"],
        "paths": [r"%LOCALAPPDATA%\Discord\Update.exe"],
        "path_args": {"discord": ["--processStart", "Discord.exe"]},
        "title_match": ["Discord"],
        "aliases": [],
    },
    "explorer": {
        "commands": ["explorer.exe"],
        "paths": [r"%WINDIR%\explorer.exe"],
        "title_match": ["File Explorer"],
        "aliases": ["files", "file explorer", "archivos"],
    },
    "notepad": {
        "commands": ["notepad.exe"],
        "paths": [],
        "title_match": ["Notepad", "Bloc de notas"],
        "aliases": ["bloc de notas"],
    },
    "calc": {
        "commands": ["calc.exe"],
        "paths": [],
        "title_match": ["Calculator", "Calculadora"],
        "aliases": ["calculator", "calculadora"],
    },
    "terminal": {
        "commands": ["wt.exe", "wt", "powershell.exe", "powershell"],
        "paths": [],
        "title_match": ["Windows Terminal", "PowerShell"],
        "aliases": ["windows terminal", "wt", "powershell", "cmd"],
    },
}


_APPS_LINUX: dict[str, dict[str, Any]] = {
    "vscode": {
        "commands": ["code"],
        "title_match": ["Visual Studio Code"],
        "aliases": ["code", "visual studio code", "vs code"],
    },
    "firefox": {
        "commands": ["firefox"],
        "title_match": ["Mozilla Firefox", "Firefox"],
        "aliases": ["mozilla"],
    },
    "chrome": {
        "commands": ["google-chrome", "chromium"],
        "title_match": ["Google Chrome", "Chromium"],
        "aliases": ["chromium", "google chrome"],
    },
    "obsidian": {
        "commands": ["obsidian"],
        "title_match": ["Obsidian"],
        "aliases": [],
    },
    "spotify": {
        "commands": ["spotify"],
        "title_match": ["Spotify"],
        "aliases": [],
    },
    "discord": {
        "commands": ["discord"],
        "title_match": ["Discord"],
        "aliases": [],
    },
    "files": {
        "commands": ["nautilus", "dolphin", "thunar"],
        "title_match": ["Files", "Nautilus", "Dolphin"],
        "aliases": ["explorer", "file explorer", "archivos"],
    },
    "terminal": {
        "commands": ["gnome-terminal", "konsole", "xterm"],
        "title_match": ["Terminal", "Konsole"],
        "aliases": ["console"],
    },
}


def _registry() -> dict[str, dict[str, Any]]:
    return _APPS_WIN if sys.platform == "win32" else _APPS_LINUX


def _resolve_app(name: str) -> tuple[str, dict[str, Any]] | None:
    """Look up an app by canonical name or alias. Returns (canonical, entry)."""
    name = name.strip().lower()
    reg = _registry()
    if name in reg:
        return name, reg[name]
    for canonical, entry in reg.items():
        if name in entry.get("aliases", []):
            return canonical, entry
    return None


def _find_executable(entry: dict[str, Any]) -> str | None:
    for cmd in entry.get("commands", []):
        found = shutil.which(cmd)
        if found:
            return found
    for raw in entry.get("paths", []):
        expanded = os.path.expandvars(raw)
        if Path(expanded).exists():
            return expanded
    return None


def _focus_windows(title_substrings: list[str]) -> bool:
    if not title_substrings:
        return False
    try:
        import pygetwindow as gw  # type: ignore[import-not-found]
    except ImportError:
        return False
    try:
        for needle in title_substrings:
            wins = [w for w in gw.getAllWindows() if needle.lower() in w.title.lower()]
            for w in wins:
                if not w.title.strip():
                    continue
                try:
                    if w.isMinimized:
                        w.restore()
                    w.activate()
                    return True
                except Exception:
                    continue
    except Exception as e:  # pygetwindow flaky around UWP windows
        logger.debug("pygetwindow focus failed: %s", e)
    return False


def _focus_linux(title_substrings: list[str]) -> bool:
    if not title_substrings or shutil.which("xdotool") is None:
        return False
    for needle in title_substrings:
        try:
            res = subprocess.run(
                ["xdotool", "search", "--name", needle, "windowactivate"],
                capture_output=True, text=True, timeout=3,
            )
            if res.returncode == 0:
                return True
        except subprocess.TimeoutExpired:
            continue
    return False


def _spawn_detached(cmd: list[str], env: dict[str, str] | None = None) -> int:
    """Launch a subprocess that survives Kee's lifetime."""
    if sys.platform == "win32":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP,
            close_fds=True,
            env=env,
        )
    else:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            env=env,
        )
    return proc.pid


class OpenAppTool(Tool):
    name = "open_app"
    description = (
        "Open a desktop application or focus it if already running. Use for "
        "voice commands like 'abre VS Code' / 'open Spotify' / 'enfoca el "
        "browser'. Known apps (Windows): vscode, firefox, chrome, obsidian, "
        "spotify, discord, explorer, notepad, calc, terminal. Aliases like "
        "'code', 'mozilla', 'archivos', 'calculadora' are recognized. If "
        "the app isn't in the registry, the literal name is tried as a "
        "shell command — useful for one-off launches."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "app": {
                "type": "string",
                "description": "Canonical app name or alias (e.g. 'vscode', 'code', 'firefox', 'spotify').",
            },
            "args": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Optional arguments to pass to the app (e.g. file path for vscode).",
            },
            "focus_if_open": {
                "type": "boolean",
                "default": True,
                "description": "If true (default), bring an existing window to the foreground rather than launching a new instance.",
            },
        },
        "required": ["app"],
    }

    async def execute(
        self,
        app: str,
        args: list[str] | None = None,
        focus_if_open: bool = True,
    ) -> dict[str, Any]:
        args = args or []

        # Step 1: try focus
        if focus_if_open:
            resolved = _resolve_app(app)
            title_match = resolved[1].get("title_match", []) if resolved else [app]
            focused = (
                _focus_windows(title_match)
                if sys.platform == "win32"
                else _focus_linux(title_match)
            )
            if focused:
                return {
                    "status": "focused",
                    "app": resolved[0] if resolved else app,
                }

        # Step 2: launch
        resolved = _resolve_app(app)
        if resolved is not None:
            canonical, entry = resolved
            executable = _find_executable(entry)
            if executable is None:
                return {
                    "status": "not_installed",
                    "app": canonical,
                    "tried_commands": entry.get("commands", []),
                    "tried_paths": [
                        os.path.expandvars(p) for p in entry.get("paths", [])
                    ],
                    "fix_hint": (
                        f"Install {canonical} or add it to PATH; "
                        f"alternatively pass the explicit .exe path as `app`."
                    ),
                }
            extra_args = entry.get("path_args", {}).get(canonical, [])
            cmd = [executable, *extra_args, *args]
        else:
            # Unknown app: try the bare string as a shell command via PATH
            found = shutil.which(app)
            if not found:
                return {
                    "status": "unknown",
                    "app": app,
                    "fix_hint": (
                        "App not in registry and not in PATH. Either pass a "
                        "full executable path as `app`, or use a known alias "
                        "(vscode/firefox/chrome/obsidian/spotify/discord/"
                        "explorer/notepad/calc/terminal)."
                    ),
                }
            cmd = [found, *args]

        try:
            pid = _spawn_detached(cmd)
        except Exception as e:
            return {"status": "spawn_failed", "error": str(e), "cmd": cmd}

        return {
            "status": "launched",
            "app": resolved[0] if resolved else app,
            "pid": pid,
            "cmd": cmd,
        }


tool = OpenAppTool()
