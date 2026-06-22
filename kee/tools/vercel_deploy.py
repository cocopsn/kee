"""Vercel deployment + project management tool.

Wraps the `vercel` CLI. Authentication is one-time interactive (`vercel login`)
— Kee can't do that for the user, but every action below assumes it's done.

Actions:
  * 'whoami'  — show authed user
  * 'deploy'  — deploy a directory (preview by default; `production=True` for
                prod). Returns the deployment URL parsed from CLI output.
  * 'list'    — list deployments for the current scope
  * 'logs'    — fetch logs for a deployment URL
  * 'inspect' — `vercel inspect <url>` for build status / metadata

Risk: 2 (deployments are externally visible). Production deploys especially
require the model to be sure.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_URL_RE = re.compile(r"https?://[a-zA-Z0-9._\-]+\.vercel\.app[A-Za-z0-9/_\-?=&%.]*")


def _extract_url(text: str) -> str | None:
    """Pull the first vercel.app URL out of `text`.

    The character class is restricted (no `"`, `,`, `}`, `]`) so JSON output
    from `vercel deploy --output json` doesn't bleed delimiters into the URL.
    """
    m = _URL_RE.search(text)
    return m.group(0).rstrip(".,;:") if m else None


# Heuristic markers that say "this directory is a deployable web project".
# We refuse to deploy a directory missing all of these — saves the user from
# the model accidentally pointing vercel at the parent `workspaces/` folder
# (it deploys, but the URL serves a 401/empty shell because vercel can't
# detect the framework).
_PROJECT_MARKERS = (
    "package.json", "index.html", "next.config.js", "next.config.ts",
    "next.config.mjs", "vercel.json", "astro.config.mjs", "vite.config.js",
    "vite.config.ts",
)


def _augmented_env() -> dict[str, str]:
    """Return os.environ extended with the npm-global bin directories.

    On Windows we prepend BOTH the legacy `%APPDATA%/npm` and the project's
    own `D:/Kee/node-globals` (the `npm config set prefix` we use to dodge
    Microsoft Store Python's filesystem virtualization, which hides
    `%APPDATA%/npm` from subprocesses). New shells will pick the same paths
    up via the persisted user `Path` env var.
    """
    env = dict(os.environ)
    if sys.platform == "win32":
        path = env.get("PATH", "") or env.get("Path", "")
        extras = [
            r"D:\Kee\node-globals",  # current npm prefix (real, on D:)
            os.path.join(env.get("APPDATA", ""), "npm"),  # legacy
        ]
        for npm_bin in extras:
            if npm_bin and npm_bin not in path:
                path = (path + ";" + npm_bin) if path else npm_bin
        env["PATH"] = path
    return env


def _cmd_quote(s: str) -> str:
    """Minimal cmd.exe quoting: wrap in double quotes if the arg has spaces
    or shell-metacharacters, escape embedded quotes."""
    if not s:
        return '""'
    needs = any(c in s for c in ' \t&|<>^()"%')
    if not needs:
        return s
    return '"' + s.replace('"', r'\"') + '"'


def _find_vercel_bin() -> str | None:
    """Locate the vercel binary, with Windows-aware fallbacks.

    `shutil.which("vercel")` misses npm-global installs on Windows when the
    `%APPDATA%/npm` directory isn't in the current process PATH. Try the
    common locations directly.
    """
    for name in ("vercel", "vercel.cmd", "vercel.exe"):
        found = shutil.which(name)
        if found:
            return found
    if sys.platform == "win32":
        # Even when the path Python can't `is_file()` due to Store-Python
        # sandbox issues, we can still invoke through cmd.exe. Return the
        # bare name so the shell resolves PATH+PATHEXT itself.
        candidates = [
            Path(os.environ.get("APPDATA", "")) / "npm" / "vercel.cmd",
            Path(os.environ.get("APPDATA", "")) / "npm" / "vercel.exe",
            Path(os.environ.get("LOCALAPPDATA", "")) / "npm" / "vercel.cmd",
        ]
        for c in candidates:
            try:
                if c.is_file():
                    return str(c)
            except OSError:
                continue
        # Last resort: trust that cmd.exe will find it via PATH (the user
        # added %APPDATA%/npm to their User PATH; new shells will see it).
        return "vercel"
    return None


class VercelDeployTool(Tool):
    name = "vercel_deploy"
    description = (
        "Deploy a directory to Vercel or query Vercel state. Wraps the `vercel` "
        "CLI; the user must have run `vercel login` once. **Production deploys "
        "are externally visible — only run with `production=True` when the user "
        "explicitly asks to ship to prod.**\n"
        "Actions:\n"
        "  - 'whoami'  → check auth\n"
        "  - 'deploy'  → deploy `directory` (preview unless production=True)\n"
        "  - 'list'    → list recent deployments\n"
        "  - 'logs'    → tail logs for `deployment_url`\n"
        "  - 'inspect' → metadata + build status for `deployment_url`"
    )
    risk_level = 2
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["whoami", "deploy", "list", "logs", "inspect"],
            },
            "directory": {
                "type": "string",
                "description": "Project directory (action='deploy'). Absolute path.",
            },
            "production": {
                "type": "boolean",
                "default": False,
                "description": "If true, deploy to production (--prod).",
            },
            "deployment_url": {
                "type": "string",
                "description": "Required for action='logs' or 'inspect'.",
            },
            "timeout_s": {
                "type": "integer",
                "default": 300,
                "description": "Subprocess timeout (default 300s for deploys).",
            },
        },
        "required": ["action"],
    }

    async def execute(
        self,
        action: str,
        directory: str | None = None,
        production: bool = False,
        deployment_url: str | None = None,
        timeout_s: int = 300,
    ) -> dict[str, Any]:
        bin_path = _find_vercel_bin()
        if bin_path is None:
            return {
                "status": "missing_dependency",
                "error": (
                    "`vercel` CLI not found. Install with `npm i -g vercel` "
                    "and ensure `%APPDATA%/npm` is in your PATH."
                ),
            }

        if action == "whoami":
            return await self._run([bin_path, "whoami"], cwd=None, timeout_s=15)

        if action == "deploy":
            if not directory:
                return {"error": "deploy requires `directory`"}
            # Sanity-check the directory is actually a deployable project.
            try:
                entries = {p.name for p in Path(directory).iterdir()}
            except (FileNotFoundError, NotADirectoryError, PermissionError) as e:
                return {"status": "bad_directory", "error": str(e)}
            if not entries & set(_PROJECT_MARKERS):
                return {
                    "status": "rejected_no_project",
                    "error": (
                        f"`{directory}` has none of {list(_PROJECT_MARKERS)}. "
                        "Vercel would deploy but serve an empty shell. "
                        "Point at the actual project directory (the one with "
                        "package.json or index.html)."
                    ),
                    "directory_contents": sorted(entries)[:20],
                }
            cmd = [bin_path, "--yes"]
            if production:
                cmd.append("--prod")
            result = await self._run(cmd, cwd=directory, timeout_s=timeout_s)
            url = _extract_url(result.get("stdout", "") + result.get("stderr", ""))
            if url:
                result["deployment_url"] = url
                result["production"] = production
            return result

        if action == "list":
            return await self._run([bin_path, "ls"], cwd=directory, timeout_s=30)

        if action == "logs":
            if not deployment_url:
                return {"error": "logs requires `deployment_url`"}
            return await self._run(
                [bin_path, "logs", deployment_url], cwd=None, timeout_s=30,
            )

        if action == "inspect":
            if not deployment_url:
                return {"error": "inspect requires `deployment_url`"}
            return await self._run(
                [bin_path, "inspect", deployment_url], cwd=None, timeout_s=30,
            )

        return {"error": f"unknown action: {action}"}

    @staticmethod
    async def _run(
        cmd: list[str],
        cwd: str | None,
        timeout_s: int,
    ) -> dict[str, Any]:
        # On Windows, the WindowsApps (Microsoft Store) Python sandbox
        # sometimes can't `os.exec` an npm-global .cmd shim at its real
        # path. Going through the shell (cmd.exe) bypasses that — cmd.exe
        # resolves the binary via PATH+PATHEXT and runs it for us.
        try:
            env = _augmented_env()
            if sys.platform == "win32":
                quoted = " ".join(_cmd_quote(a) for a in cmd)
                proc = await asyncio.create_subprocess_shell(
                    quoted,
                    cwd=cwd, env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    cwd=cwd, env=env,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
        except (FileNotFoundError, NotADirectoryError, OSError) as e:
            return {"status": "spawn_failed", "error": str(e)}
        try:
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"status": "timeout", "elapsed_s": timeout_s}

        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        # Detect classic auth failure so we can surface the fix to the model.
        auth_failed = (
            "not valid" in (out + err).lower()
            or "vercel login" in (out + err).lower()
        )
        return {
            "status": "ok" if proc.returncode == 0 else "nonzero_exit",
            "exit_code": proc.returncode,
            "stdout": out[-2000:],
            "stderr": err[-1000:],
            "auth_failed": auth_failed,
            "fix_hint": (
                "Run `vercel login` in a terminal once to authenticate."
                if auth_failed else None
            ),
        }


tool = VercelDeployTool()
