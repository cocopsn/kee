"""Shell execution tool — cross-platform.

Runs a single command via the system shell with a timeout. On Windows the
shell is the system default (`cmd.exe` via `subprocess`); on POSIX systems
it's `/bin/sh`. Returns stdout/stderr/exit code.

Risk: 1 — writes to the local filesystem, can install packages, etc.
"""

from __future__ import annotations

import asyncio
from typing import Any

from kee.tools.base import Tool


class ShellTool(Tool):
    name = "execute_shell"
    description = (
        "Execute a single shell command on the host. Returns stdout, stderr "
        "and exit code. **Prefer the `files` tool for ls/cat/read/write — "
        "they're cross-platform; shell commands like `ls` fail on Windows.** "
        "Use this for things `files` can't do: package installs, running "
        "scripts, git, npm, python invocations, etc. The shell is cmd.exe "
        "on Windows and /bin/sh on POSIX."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": "The shell command to execute.",
            },
            "cwd": {
                "type": "string",
                "description": "Working directory. Defaults to the project root.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Timeout in seconds. Default 60.",
                "default": 60,
            },
        },
        "required": ["command"],
    }

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout_s: int = 60,
    ) -> dict[str, Any]:
        # Validate cwd up front. Without this, asyncio raises
        # NotADirectoryError / FileNotFoundError from deep inside
        # _winapi.CreateProcess and the traceback pollutes the terminal.
        if cwd is not None:
            from pathlib import Path
            cwd_path = Path(cwd).expanduser()
            if not cwd_path.exists():
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"cwd does not exist: {cwd_path}",
                    "timed_out": False,
                }
            if not cwd_path.is_dir():
                return {
                    "exit_code": -1,
                    "stdout": "",
                    "stderr": f"cwd is not a directory: {cwd_path}",
                    "timed_out": False,
                }
            cwd = str(cwd_path)

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=cwd,
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as e:
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"could not spawn shell: {e}",
                "timed_out": False,
            }
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "exit_code": -1,
                "stdout": "",
                "stderr": f"Command timed out after {timeout_s}s",
                "timed_out": True,
            }

        # Truncate output to keep tool results from blowing the LLM context.
        out = stdout.decode("utf-8", errors="replace")[-4000:]
        err = stderr.decode("utf-8", errors="replace")[-2000:]
        return {
            "exit_code": proc.returncode,
            "stdout": out,
            "stderr": err,
            "timed_out": False,
        }


tool = ShellTool()
