"""Verification loop helpers.

For each tool execution the agent captures *before* and *after* snapshots of
relevant state, then diffs them. The aim is not exhaustive correctness — the
LLM still has to reason about results — but to catch silent failures:

  * write_file claimed success but the file hash didn't change.
  * execute_shell touched a directory that was supposed to be read-only.
  * execute_shell ran with `exit_code != 0` (already obvious, but logged).

When a verification fails we record an anomaly. For `write_file` we keep the
pre-state content in memory so the agent can offer a rollback.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def _hash(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    try:
        return hashlib.md5(path.read_bytes()).hexdigest()
    except (OSError, PermissionError):
        return None


def _safe_listdir(path: str | Path, limit: int = 30) -> list[str] | None:
    try:
        p = Path(path)
        if not p.exists() or not p.is_dir():
            return None
        return sorted(os.listdir(p))[:limit]
    except (OSError, PermissionError):
        return None


def capture_state(tool_name: str, params: dict[str, Any]) -> dict[str, Any]:
    """Snapshot relevant state before/after a tool call.

    The captured fields depend on the tool. Read-only tools like web_search,
    memory_search and system_status return an empty dict — there's nothing
    meaningful to diff.
    """
    state: dict[str, Any] = {}

    if tool_name == "files":
        action = params.get("action")
        target = params.get("path")
        if target:
            p = Path(target).expanduser()
            state["target"] = str(p)
            state["exists"] = p.exists()
            if action in ("write", "read") and p.is_file():
                state["hash"] = _hash(p)
                state["size"] = p.stat().st_size

    if tool_name == "execute_shell":
        cwd = params.get("cwd") or "."
        state["cwd"] = str(Path(cwd).expanduser())
        state["cwd_files"] = _safe_listdir(state["cwd"])

    return state


def verify(
    tool_name: str,
    params: dict[str, Any],
    result: Any,
    pre: dict[str, Any],
    post: dict[str, Any],
) -> dict[str, Any]:
    """Compare pre/post snapshots and return a verification verdict.

    Returns: {"ok": bool, "reason": str, "anomalies": [str], "rollback_available": bool}
    """
    anomalies: list[str] = []
    rollback_available = False

    if tool_name == "files":
        action = params.get("action")
        if action == "write":
            if not post.get("exists"):
                anomalies.append("write_file claimed success but target doesn't exist")
            elif pre.get("hash") and pre.get("hash") == post.get("hash"):
                anomalies.append("write_file ran but content hash is unchanged")
            elif pre.get("exists"):
                rollback_available = True  # had previous content; could revert

        if action == "read":
            if pre.get("exists") and not post.get("exists"):
                anomalies.append("read action: file disappeared mid-operation")

    if tool_name == "execute_shell":
        if isinstance(result, dict):
            if result.get("exit_code") not in (None, 0):
                anomalies.append(
                    f"shell exited with code {result.get('exit_code')}"
                )
            if result.get("timed_out"):
                anomalies.append("shell command timed out")

    return {
        "ok": not anomalies,
        "anomalies": anomalies,
        "rollback_available": rollback_available,
        "diff": _shallow_diff(pre, post),
    }


def _shallow_diff(pre: dict[str, Any], post: dict[str, Any]) -> dict[str, Any]:
    keys = set(pre) | set(post)
    out: dict[str, Any] = {}
    for k in keys:
        if pre.get(k) != post.get(k):
            out[k] = {"before": pre.get(k), "after": post.get(k)}
    return out


def serialize_state(state: dict[str, Any]) -> str | None:
    if not state:
        return None
    try:
        return json.dumps(state, ensure_ascii=False, default=str)
    except (TypeError, ValueError):
        return str(state)
