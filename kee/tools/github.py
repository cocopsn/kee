"""GitHub operations via the `gh` CLI.

Authentication is one-time (`gh auth login`). Every action assumes auth
is in place; the tool reports clean errors when it isn't.

Actions:
  * 'status'       — `gh auth status` (cheap auth probe)
  * 'repo_view'    — view a repo's metadata
  * 'repo_create'  — create a new repo (private by default)
  * 'pr_list'      — list open PRs in `repo`
  * 'pr_create'    — create a PR (must be in a checked-out repo)
  * 'pr_view'      — view PR details
  * 'issue_list'   — list issues
  * 'issue_create' — create an issue
  * 'workflow_run' — trigger a workflow

Risk: 1 for read-only views; 2 for create/comment; 3 (CONFIRM) for force-push,
delete, or anything that hits production CI. The model should treat any action
that fans out to other humans as risk-3 in spirit.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_READ_ACTIONS = {"status", "repo_view", "pr_list", "pr_view", "issue_list"}


class GitHubTool(Tool):
    name = "github"
    description = (
        "Interact with GitHub via the `gh` CLI (assumes prior `gh auth login`). "
        "Use for: viewing repos, listing/creating PRs and issues, triggering "
        "workflows. **Anything that creates a PR, issue, or comment is "
        "externally visible — only do it when explicitly asked.**\n"
        "Actions: status, repo_view, repo_create, pr_list, pr_create, pr_view, "
        "issue_list, issue_create, workflow_run."
    )
    risk_level = 2
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status", "repo_view", "repo_create",
                    "pr_list", "pr_create", "pr_view",
                    "issue_list", "issue_create", "workflow_run",
                ],
            },
            "repo": {
                "type": "string",
                "description": "owner/name (e.g. `octocat/hello-world`). Required for most actions.",
            },
            "title": {"type": "string", "description": "PR / issue title."},
            "body": {"type": "string", "description": "PR / issue body."},
            "base": {
                "type": "string",
                "default": "main",
                "description": "Base branch for pr_create.",
            },
            "head": {
                "type": "string",
                "description": "Head branch for pr_create. Defaults to current branch.",
            },
            "directory": {
                "type": "string",
                "description": "Local clone directory (some actions need cwd).",
            },
            "private": {
                "type": "boolean",
                "default": True,
                "description": "Visibility for repo_create.",
            },
            "number": {
                "type": "integer",
                "description": "PR or issue number for *_view actions.",
            },
            "workflow": {
                "type": "string",
                "description": "Workflow file or ID for workflow_run.",
            },
            "json_fields": {
                "type": "string",
                "description": "Comma-separated fields for `--json` (list/view).",
            },
            "timeout_s": {"type": "integer", "default": 60},
        },
        "required": ["action"],
    }

    async def execute(self, action: str, **kw) -> dict[str, Any]:
        if shutil.which("gh") is None:
            return {
                "status": "missing_dependency",
                "error": "`gh` CLI not in PATH. Install from https://cli.github.com/.",
            }

        timeout = kw.get("timeout_s", 60)
        cwd = kw.get("directory")

        if action == "status":
            return await self._run(["gh", "auth", "status"], cwd, timeout)

        if action == "repo_view":
            if not kw.get("repo"):
                return {"error": "repo_view needs `repo`"}
            cmd = ["gh", "repo", "view", kw["repo"]]
            if kw.get("json_fields"):
                cmd += ["--json", kw["json_fields"]]
            return await self._run(cmd, cwd, timeout)

        if action == "repo_create":
            if not kw.get("repo"):
                return {"error": "repo_create needs `repo`"}
            visibility = "--private" if kw.get("private", True) else "--public"
            cmd = ["gh", "repo", "create", kw["repo"], visibility]
            if kw.get("directory"):
                cmd += ["--source", kw["directory"]]
            return await self._run(cmd, cwd, timeout)

        if action == "pr_list":
            if not kw.get("repo"):
                return {"error": "pr_list needs `repo`"}
            cmd = ["gh", "pr", "list", "--repo", kw["repo"]]
            if kw.get("json_fields"):
                cmd += ["--json", kw["json_fields"]]
            else:
                cmd += ["--json", "number,title,author,state,url,createdAt"]
            return await self._run(cmd, cwd, timeout)

        if action == "pr_create":
            if not (kw.get("title") and kw.get("body")):
                return {"error": "pr_create needs `title` and `body`"}
            cmd = ["gh", "pr", "create",
                   "--title", kw["title"],
                   "--body", kw["body"],
                   "--base", kw.get("base", "main")]
            if kw.get("head"):
                cmd += ["--head", kw["head"]]
            return await self._run(cmd, cwd, timeout)

        if action == "pr_view":
            if not kw.get("number") or not kw.get("repo"):
                return {"error": "pr_view needs `repo` and `number`"}
            cmd = ["gh", "pr", "view", str(kw["number"]), "--repo", kw["repo"]]
            if kw.get("json_fields"):
                cmd += ["--json", kw["json_fields"]]
            return await self._run(cmd, cwd, timeout)

        if action == "issue_list":
            if not kw.get("repo"):
                return {"error": "issue_list needs `repo`"}
            cmd = ["gh", "issue", "list", "--repo", kw["repo"],
                   "--json", kw.get("json_fields") or "number,title,author,state,url,createdAt"]
            return await self._run(cmd, cwd, timeout)

        if action == "issue_create":
            if not (kw.get("title") and kw.get("repo")):
                return {"error": "issue_create needs `repo` and `title`"}
            cmd = ["gh", "issue", "create",
                   "--repo", kw["repo"],
                   "--title", kw["title"],
                   "--body", kw.get("body") or ""]
            return await self._run(cmd, cwd, timeout)

        if action == "workflow_run":
            if not (kw.get("repo") and kw.get("workflow")):
                return {"error": "workflow_run needs `repo` and `workflow`"}
            cmd = ["gh", "workflow", "run", kw["workflow"], "--repo", kw["repo"]]
            return await self._run(cmd, cwd, timeout)

        return {"error": f"unknown action: {action}"}

    @staticmethod
    async def _run(cmd: list[str], cwd: str | None, timeout: int) -> dict[str, Any]:
        # Same Windows-shell workaround as vercel_deploy: route through
        # cmd.exe so PATH+PATHEXT resolution works inside the Store-Python
        # sandbox.
        import sys
        from kee.tools.vercel_deploy import _augmented_env, _cmd_quote
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
            out_b, err_b = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"status": "timeout", "elapsed_s": timeout}
        out = out_b.decode("utf-8", errors="replace")
        err = err_b.decode("utf-8", errors="replace")
        auth_failed = (
            "not authenticated" in (out + err).lower()
            or "gh auth login" in (out + err).lower()
        )
        return {
            "status": "ok" if proc.returncode == 0 else "nonzero_exit",
            "exit_code": proc.returncode,
            "stdout": out[-3000:],
            "stderr": err[-1000:],
            "auth_failed": auth_failed,
            "fix_hint": "Run `gh auth login` once to authenticate." if auth_failed else None,
        }


tool = GitHubTool()
