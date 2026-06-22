"""File I/O tool.

Three sub-actions in one tool: read, write, list. Combining them under a
single LLM-facing tool reduces the number of tool slots the model has to
juggle while keeping each call unambiguous.

Risk: 1 (write/list create or expose data).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from kee.tools.base import Tool


class FilesTool(Tool):
    name = "files"
    description = (
        "Read, write or list files on the host. **USE THIS — not "
        "execute_shell — for any filesystem listing, file read, or file "
        "write operation.** Cross-platform: works the same on Windows and "
        "Linux. action='read' returns file contents; action='write' writes "
        "content (creates parent dirs); action='list' lists entries in a "
        "directory. Use absolute paths or paths relative to the project root."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["read", "write", "list"],
            },
            "path": {
                "type": "string",
                "description": "File or directory path.",
            },
            "content": {
                "type": "string",
                "description": "Content to write (action='write' only).",
            },
            "max_chars": {
                "type": "integer",
                "description": "Truncate read output to this many characters. Default 8000.",
                "default": 8000,
            },
        },
        "required": ["action", "path"],
    }

    async def execute(
        self,
        action: str,
        path: str,
        content: str | None = None,
        max_chars: int = 8000,
    ) -> dict[str, Any]:
        p = Path(path).expanduser()

        if action == "read":
            if not p.exists():
                return {"error": f"File not found: {p}"}
            if not p.is_file():
                return {"error": f"Not a file: {p}"}
            data = p.read_text(encoding="utf-8", errors="replace")
            truncated = len(data) > max_chars
            return {
                "path": str(p),
                "content": data[:max_chars],
                "truncated": truncated,
                "size_bytes": p.stat().st_size,
            }

        if action == "write":
            if content is None:
                return {"error": "Missing 'content' for write action."}
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(content, encoding="utf-8")
            return {
                "path": str(p),
                "bytes_written": len(content.encode("utf-8")),
            }

        if action == "list":
            if not p.exists():
                return {"error": f"Path not found: {p}"}
            if not p.is_dir():
                return {"error": f"Not a directory: {p}"}
            entries = []
            for child in sorted(p.iterdir()):
                entries.append({
                    "name": child.name,
                    "type": "dir" if child.is_dir() else "file",
                    "size": child.stat().st_size if child.is_file() else None,
                })
            return {"path": str(p), "entries": entries[:200]}

        return {"error": f"Unknown action: {action}"}


tool = FilesTool()
