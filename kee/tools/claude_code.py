"""Claude Code orchestrator.

Delegates complex coding tasks to a headless `claude -p` subprocess. The
local Qwen3.5 9B agent decides WHAT and WHY; Claude Code (Sonnet/Opus via
the user's Pro/Max subscription) handles the HOW. Per v2 §IV / addendum §2.

Camino A: this tool shells out to the `claude` CLI installed on the host.
Authentication and rate-limiting come from the user's logged-in Pro/Max
plan — no `ANTHROPIC_API_KEY` required.

Workspace policy:
  * Each task runs in an isolated directory under `D:/Kee/workspaces/`.
  * If the caller doesn't supply `working_directory`, one is minted from a
    sanitized task slug and the current timestamp.
  * The directory persists after the call so Kee (or Coco) can inspect or
    promote what Claude built.

Permission mode is `acceptEdits` — Claude can read/write/run within the
workspace without prompting. We never pass `--dangerously-skip-permissions`.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import shutil
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str, max_len: int = 32) -> str:
    base = _SLUG_RE.sub("-", text.lower()).strip("-") or "task"
    return base[:max_len].rstrip("-") or "task"


def _list_files(root: Path, limit: int = 80) -> list[str]:
    out: list[str] = []
    for p in root.rglob("*"):
        if p.is_file() and ".git" not in p.parts and "node_modules" not in p.parts:
            try:
                out.append(str(p.relative_to(root)).replace("\\", "/"))
            except ValueError:
                out.append(str(p))
        if len(out) >= limit:
            break
    return out


class ClaudeCodeTool(Tool):
    name = "claude_code"
    description = (
        "Delegate a coding task to Claude Code (Sonnet/Opus via Coco's "
        "Pro/Max subscription, headless). **If the user explicitly tells "
        "you to use claude_code, USE THIS TOOL — even if the task looks "
        "trivial. Do not substitute `files.write` or `execute_shell` "
        "for it.** The user often wants the delegation for testing, "
        "logging, or audit purposes regardless of complexity.\n\n"
        "Otherwise, reach for this when: building a small project from "
        "scratch (landing page, script, API), refactoring across multiple "
        "files, or any coding work that exceeds the local 9B model's range.\n\n"
        "Provide a SELF-CONTAINED prompt as if briefing a senior engineer who "
        "has never seen the codebase: what to build, file paths, constraints, "
        "definition of done. Claude runs in an isolated workspace under "
        "D:/Kee/workspaces/ and may write/edit/run within it freely.\n\n"
        "Returns: the final text from Claude, the workspace path, and a list "
        "of files created. The workspace persists for review.\n\n"
        "Cost: counts against Coco's Pro/Max rate limits, not a paid API."
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "Complete brief for Claude. Include all context.",
            },
            "task_name": {
                "type": "string",
                "description": (
                    "Short slug used to name the workspace dir (e.g. "
                    "'fat-dogs-landing'). Optional — derived from the prompt "
                    "if missing."
                ),
            },
            "working_directory": {
                "type": "string",
                "description": (
                    "Absolute path to use as the workspace. Optional — when "
                    "omitted, a new dir is created under D:/Kee/workspaces/."
                ),
            },
            "allowed_tools": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Built-in Claude tools to enable. Default: "
                    "['Read', 'Edit', 'Write', 'Bash']. Use 'default' for all "
                    "or '' for none."
                ),
            },
            "add_dir": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Extra absolute paths Claude is allowed to read (e.g. a "
                    "reference project elsewhere on disk). Optional."
                ),
            },
            "model": {
                "type": "string",
                "description": (
                    "Model alias ('sonnet', 'opus', 'haiku') or a full model "
                    "ID. Optional — Claude picks its default."
                ),
            },
            "effort": {
                "type": "string",
                "enum": ["low", "medium", "high"],
                "description": "Reasoning effort. Optional.",
            },
            "timeout_s": {
                "type": "integer",
                "description": "Subprocess timeout in seconds. Default 600.",
                "default": 600,
            },
        },
        "required": ["prompt"],
    }

    async def execute(
        self,
        prompt: str,
        task_name: str | None = None,
        working_directory: str | None = None,
        allowed_tools: list[str] | None = None,
        add_dir: list[str] | None = None,
        model: str | None = None,
        effort: str | None = None,
        timeout_s: int = 600,
    ) -> dict[str, Any]:
        # ── 0. Pre-flight ─────────────────────────────────────────────────
        if shutil.which("claude") is None:
            return {
                "status": "missing_dependency",
                "error": (
                    "`claude` CLI not found in PATH. Install with "
                    "`npm install -g @anthropic-ai/claude-code` and run "
                    "`claude auth` once."
                ),
            }

        # ── 1. Resolve workspace ──────────────────────────────────────────
        workspaces_root = settings.project_root / "workspaces"
        workspaces_root.mkdir(parents=True, exist_ok=True)

        if working_directory:
            workdir = Path(working_directory).expanduser().resolve()
            workdir.mkdir(parents=True, exist_ok=True)
        else:
            slug = _slugify(task_name or prompt.split("\n", 1)[0])
            stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
            workdir = workspaces_root / f"{stamp}-{slug}"
            workdir.mkdir(parents=True, exist_ok=False)

        # ── 2. Build the command ──────────────────────────────────────────
        if allowed_tools is None:
            allowed_tools = ["Read", "Edit", "Write", "Bash"]

        cmd: list[str] = [
            "claude",
            "-p", prompt,
            "--output-format", "json",
            "--permission-mode", "acceptEdits",
        ]

        if allowed_tools == [""] or allowed_tools == "":
            cmd += ["--allowedTools", ""]
        elif allowed_tools:
            cmd += ["--allowedTools", *allowed_tools]

        for d in add_dir or []:
            cmd += ["--add-dir", d]
        if model:
            cmd += ["--model", model]
        if effort:
            cmd += ["--effort", effort]

        # ── 3. Run it ─────────────────────────────────────────────────────
        logger.info(
            "claude_code: spawning in %s (timeout=%ds, allowed=%s)",
            workdir, timeout_s, allowed_tools,
        )
        started = time.time()

        # Strip the `CLAUDECODE` marker from the subprocess env so a Kee
        # instance launched from inside an interactive Claude Code session
        # (e.g. during development) doesn't trip the "nested session" guard.
        # In production (Kee running standalone on the Alienware) this env
        # var isn't set anyway, so the strip is a no-op.
        child_env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                cwd=str(workdir),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=child_env,
            )
        except (FileNotFoundError, NotADirectoryError, OSError) as e:
            return {"status": "spawn_failed", "error": str(e), "workdir": str(workdir)}

        try:
            stdout_b, stderr_b = await asyncio.wait_for(
                proc.communicate(), timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {
                "status": "timeout",
                "workdir": str(workdir),
                "elapsed_s": time.time() - started,
                "files_created": _list_files(workdir),
            }

        elapsed = time.time() - started
        stdout = stdout_b.decode("utf-8", errors="replace")
        stderr = stderr_b.decode("utf-8", errors="replace")

        # ── 4. Parse the JSON output ──────────────────────────────────────
        result_text = ""
        parsed: dict[str, Any] | None = None
        try:
            parsed = json.loads(stdout)
            # Claude Code's json output may use 'result' or 'response' or
            # nest the assistant message — be defensive.
            if isinstance(parsed, dict):
                result_text = (
                    parsed.get("result")
                    or parsed.get("response")
                    or parsed.get("text")
                    or ""
                )
        except json.JSONDecodeError:
            # Fallback: take the last non-empty stdout line.
            for line in reversed(stdout.splitlines()):
                if line.strip():
                    result_text = line.strip()
                    break

        # ── 5. Inventory + return ─────────────────────────────────────────
        files = _list_files(workdir)

        # Bubble up the fields Internal Economy cares about, so the
        # downstream tracker doesn't have to re-parse the raw JSON.
        cost_usd = None
        if isinstance(parsed, dict):
            cost_usd = parsed.get("total_cost_usd")
        out: dict[str, Any] = {
            "status": "ok" if proc.returncode == 0 else "nonzero_exit",
            "exit_code": proc.returncode,
            "elapsed_s": round(elapsed, 1),
            "workdir": str(workdir),
            "result": result_text[:4000],
            "files_created": files,
            "stderr": stderr[-800:] if stderr else "",
            "raw_keys": sorted(parsed.keys()) if isinstance(parsed, dict) else None,
            "total_cost_usd": cost_usd,
            "duration_api_ms": parsed.get("duration_api_ms") if isinstance(parsed, dict) else None,
            "usage": parsed.get("usage") if isinstance(parsed, dict) else None,
            "modelUsage": parsed.get("modelUsage") if isinstance(parsed, dict) else None,
        }

        # Record cost in the Internal Economy ledger (best-effort, never raises).
        try:
            from kee.cognition.economy import from_claude_code_result
            task_summary = (prompt[:120] + ("…" if len(prompt) > 120 else "")).strip()
            from_claude_code_result(out, task_summary=task_summary)
        except Exception:
            logger.debug("cost ledger write skipped", exc_info=True)

        return out


tool = ClaudeCodeTool()
