"""Tool: work_session — persistent `claude -p` subprocess per project.

Jarvis's biggest cost-saver pattern. The existing `claude_code` tool
spins up a fresh `claude -p` process for every call — each one cold-
starts the conversation, re-reads the workspace, re-loads context
(~$0.05-0.20 each time on Pro/Max). `work_session` keeps a long-lived
subprocess per project keyed in a JSON file under
`data/work_sessions/<slug>.json` and reuses it via `claude --continue`.

Workflow:
    > "kee, abre sesión en auctorum-systems"
        → work_session(action='open', project='auctorum-systems',
                       cwd='D:/Codigo/Auctorum/auctorum-systems')
    > "kee, agrega un endpoint para ..."
        → work_session(action='ask', message='agrega un endpoint…')
        → re-uses the existing claude session, no re-init
    > "kee, cierra sesión"
        → work_session(action='close')

State file format (`data/work_sessions/<slug>.json`):
    {
      "project": "auctorum-systems",
      "cwd": "D:/Codigo/Auctorum/auctorum-systems",
      "opened_at": "2026-05-04T10:30:00",
      "last_used_at": "2026-05-04T10:32:14",
      "turns": 5,
      "total_cost_usd": 0.034
    }

Risk: 1 (writes/runs subprocesses, but every action is reversible —
worst case the session crashes and we re-open).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import subprocess
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from kee.config import settings
from kee.tools.base import Tool

logger = logging.getLogger(__name__)


SESSIONS_DIR = settings.data_dir / "work_sessions"


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "session"


def _state_path(project: str) -> Path:
    SESSIONS_DIR.mkdir(parents=True, exist_ok=True)
    return SESSIONS_DIR / f"{_slug(project)}.json"


def _read_state(project: str) -> Optional[dict]:
    p = _state_path(project)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


def _write_state(state: dict) -> None:
    p = _state_path(state["project"])
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
    tmp.replace(p)


def _list_sessions() -> list[dict]:
    if not SESSIONS_DIR.exists():
        return []
    out = []
    for p in sorted(SESSIONS_DIR.glob("*.json"), key=lambda x: x.stat().st_mtime, reverse=True):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            out.append({
                "project": d.get("project"),
                "cwd": d.get("cwd"),
                "opened_at": d.get("opened_at"),
                "last_used_at": d.get("last_used_at"),
                "turns": d.get("turns", 0),
                "total_cost_usd": d.get("total_cost_usd", 0),
            })
        except Exception:
            continue
    return out


def _claude_bin() -> Optional[str]:
    """Find the claude CLI (Coco's Pro/Max subscription)."""
    import shutil
    return shutil.which("claude")


async def _run_claude_async(
    cwd: Path, message: str, continue_flag: bool, timeout_s: int = 600,
) -> dict:
    """Spawn a single claude -p call. Returns parsed JSON-ish result."""
    bin = _claude_bin()
    if not bin:
        return {"ok": False, "error": "claude CLI not found in PATH"}
    cmd = [bin, "-p"]
    if continue_flag:
        cmd.append("--continue")
    cmd += ["--output-format", "json", message]
    env = os.environ.copy()
    # Strip the nested-session guard like the existing claude_code tool does
    env.pop("CLAUDECODE", None)

    t0 = time.monotonic()
    try:
        proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=str(cwd),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            env=env,
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
        elapsed_ms = int((time.monotonic() - t0) * 1000)
        out = stdout.decode("utf-8", "replace") if stdout else ""
        err = stderr.decode("utf-8", "replace") if stderr else ""
        if proc.returncode != 0:
            return {"ok": False, "error": err[:500] or f"exit={proc.returncode}",
                    "elapsed_ms": elapsed_ms}
        # Try JSON parse
        try:
            data = json.loads(out)
            data["elapsed_ms"] = elapsed_ms
            data["ok"] = True
            return data
        except Exception:
            return {"ok": True, "result": out[:4000], "elapsed_ms": elapsed_ms}
    except asyncio.TimeoutError:
        return {"ok": False, "error": f"timeout after {timeout_s}s"}


class WorkSessionTool(Tool):
    name = "work_session"
    description = (
        "Persistent claude -p sessions per project. Cheaper than calling "
        "`claude_code` for each query because re-uses the existing "
        "conversation via `--continue`. Use when the user wants to "
        "iterate on the same project (auctorum-systems, kee, nahual, "
        "etc.) without re-explaining context every turn.\n"
        "Actions:\n"
        "  - 'open':    start/resume session for a project (project + cwd required)\n"
        "  - 'ask':     send a message to the active session (message required)\n"
        "  - 'close':   end session (project required)\n"
        "  - 'list':    show all sessions\n"
        "  - 'status':  show one session's metadata (project required)\n"
        "  - 'summary': lifetime totals across all sessions ($spend, turn count)\n"
        "  - 'prune':   close sessions idle longer than `idle_days` (default 30)"
    )
    risk_level = 1
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["open", "ask", "close", "list", "status",
                         "summary", "prune"],
                "default": "list",
            },
            "project": {"type": "string"},
            "cwd": {"type": "string", "description": "Absolute path to project root (required on open)"},
            "message": {"type": "string", "description": "What to ask claude in this session"},
            "timeout_s": {"type": "integer", "default": 600},
            "idle_days": {"type": "integer", "default": 30,
                          "description": "For 'prune': close sessions idle longer than this."},
        },
    }

    async def execute(
        self,
        action: str = "list",
        project: str = "",
        cwd: str = "",
        message: str = "",
        timeout_s: int = 600,
        idle_days: int = 30,
    ) -> dict[str, Any]:
        if action == "list":
            return {"ok": True, "sessions": _list_sessions()}

        if action == "status":
            if not project:
                return {"ok": False, "error": "project required"}
            st = _read_state(project)
            if not st:
                return {"ok": False, "error": f"no session for {project!r}"}
            return {"ok": True, **st}

        if action == "open":
            if not project or not cwd:
                return {"ok": False, "error": "project + cwd required"}
            cwd_p = Path(cwd).expanduser().resolve()
            if not cwd_p.exists():
                return {"ok": False, "error": f"cwd not found: {cwd_p}"}
            existing = _read_state(project) or {}
            state = {
                "project": project,
                "cwd": str(cwd_p),
                "opened_at": existing.get("opened_at") or datetime.now().isoformat(timespec="seconds"),
                "last_used_at": datetime.now().isoformat(timespec="seconds"),
                "turns": existing.get("turns", 0),
                "total_cost_usd": existing.get("total_cost_usd", 0),
            }
            _write_state(state)
            return {"ok": True, "opened": project, "cwd": str(cwd_p),
                    "resumed": bool(existing)}

        if action == "ask":
            if not project or not message:
                return {"ok": False, "error": "project + message required"}
            st = _read_state(project)
            if not st:
                return {"ok": False, "error": f"no session for {project!r}; open it first"}
            cwd_p = Path(st["cwd"])
            if not cwd_p.exists():
                return {"ok": False, "error": f"cwd missing: {cwd_p}"}
            # Use --continue if turns > 0 (this isn't the first message)
            continue_flag = (st.get("turns", 0) > 0)
            r = await _run_claude_async(cwd_p, message, continue_flag, timeout_s)
            if r.get("ok"):
                cost = float(r.get("total_cost_usd") or 0)
                st["turns"] += 1
                st["last_used_at"] = datetime.now().isoformat(timespec="seconds")
                st["total_cost_usd"] = round(st.get("total_cost_usd", 0) + cost, 4)
                _write_state(st)
                # Log to audit_log via the existing economy plumbing
                try:
                    from kee.cognition.economy import record_cost
                    record_cost(tool="work_session", model=r.get("model", "?"),
                                cost_usd=cost,
                                duration_ms=r.get("elapsed_ms"),
                                tokens_in=(r.get("usage") or {}).get("input_tokens"),
                                tokens_out=(r.get("usage") or {}).get("output_tokens"))
                except Exception:
                    pass
            return {"project": project, "turns_total": st["turns"],
                    "cost_this_turn_usd": float(r.get("total_cost_usd") or 0),
                    "cost_session_usd": st["total_cost_usd"], **r}

        if action == "close":
            if not project:
                return {"ok": False, "error": "project required"}
            p = _state_path(project)
            if not p.exists():
                return {"ok": False, "error": f"no session for {project!r}"}
            try:
                p.unlink()
                return {"ok": True, "closed": project}
            except Exception as e:
                return {"ok": False, "error": str(e)}

        if action == "summary":
            sessions = _list_sessions()
            total_cost = round(sum(s.get("total_cost_usd", 0) or 0
                                   for s in sessions), 4)
            total_turns = sum(int(s.get("turns", 0) or 0) for s in sessions)
            return {
                "ok": True,
                "sessions": len(sessions),
                "total_turns": total_turns,
                "total_cost_usd": total_cost,
                "by_project": [
                    {"project": s.get("project"),
                     "turns": s.get("turns", 0),
                     "cost_usd": s.get("total_cost_usd", 0),
                     "last_used_at": s.get("last_used_at")}
                    for s in sessions
                ],
            }

        if action == "prune":
            from datetime import timedelta
            cutoff = datetime.now() - timedelta(days=int(idle_days))
            closed: list[str] = []
            for s in _list_sessions():
                last = s.get("last_used_at")
                if not last:
                    continue
                try:
                    when = datetime.fromisoformat(last)
                except ValueError:
                    continue
                if when < cutoff:
                    p = _state_path(s.get("project", ""))
                    try:
                        p.unlink()
                        closed.append(s.get("project", ""))
                    except Exception:
                        pass
            return {"ok": True, "pruned": closed,
                    "idle_days": int(idle_days)}

        return {"ok": False, "error": f"unknown action '{action}'"}


tool = WorkSessionTool()
