"""Tool: commits — git activity across Coco's project tree.

Walks a configurable set of project roots (D:/projects, D:/Codigo,
D:/auctorum-systems, D:/nahual, D:/Kee, …) and aggregates `git log`
across all of them in a single window. Lets the agent answer:

  - "¿qué commiteé hoy?" (today)
  - "¿qué commits llevo esta semana?" (last 7d)
  - "¿qué proyectos toqué en el último mes?" (project-level summary)

Pure git-CLI calls; no remote network. If git isn't on PATH or a root
doesn't exist, it's silently skipped — never fails the whole call.

Risk: 0 — read-only over the local filesystem.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

from kee.tools.base import Tool

logger = logging.getLogger(__name__)


# Default roots to scan. Each root is searched 2 levels deep for `.git`
# directories. Coco can override at call time via `roots=`.
_DEFAULT_ROOTS = [
    "D:/Kee",
    "D:/projects",
    "D:/Codigo",
    "D:/auctorum-systems",
    "D:/nahual",
    "D:/auctorumsis-backup",
]


def _git_bin() -> str | None:
    return shutil.which("git")


def _find_repos(roots: list[str], max_depth: int = 2) -> list[Path]:
    """Find every git repo in `roots` up to `max_depth` levels deep."""
    found: list[Path] = []
    for root in roots:
        rp = Path(root).expanduser()
        if not rp.exists():
            continue
        # Self-check: is `rp` already a repo?
        if (rp / ".git").exists():
            found.append(rp)
            continue
        # Walk children up to max_depth
        for depth in range(max_depth):
            for child in list(rp.glob("/".join(["*"] * (depth + 1)))):
                if child.is_dir() and (child / ".git").exists():
                    found.append(child)
    # Dedup preserving order
    seen: set[str] = set()
    out: list[Path] = []
    for p in found:
        s = str(p.resolve())
        if s not in seen:
            seen.add(s)
            out.append(p)
    return out


def _git_log(repo: Path, since: str, author: str | None = None) -> list[dict]:
    """Run `git log` in `repo`. Returns parsed list of commits.

    `since` is a git --since string ('1 day ago', '1 week ago', '2026-01-01').
    """
    git = _git_bin()
    if not git:
        return []
    cmd = [
        git, "-C", str(repo), "log",
        f"--since={since}",
        "--pretty=format:%H%x09%ai%x09%an%x09%s",
        "--no-merges",
    ]
    if author:
        cmd.append(f"--author={author}")
    try:
        proc = subprocess.run(
            cmd, capture_output=True, text=True, timeout=15,
            encoding="utf-8", errors="replace",
        )
    except Exception as e:
        logger.debug("git log failed for %s: %s", repo, e)
        return []
    if proc.returncode != 0:
        return []
    out: list[dict] = []
    for line in proc.stdout.splitlines():
        parts = line.split("\t", 3)
        if len(parts) < 4:
            continue
        sha, ts, author_name, subject = parts
        out.append({
            "sha": sha[:8],
            "ts": ts,
            "author": author_name,
            "subject": subject[:160],
            "repo": repo.name,
        })
    return out


def _summarize(commits: list[dict], window_label: str) -> dict[str, Any]:
    by_repo = Counter(c["repo"] for c in commits)
    by_author = Counter(c["author"] for c in commits)
    return {
        "window": window_label,
        "count": len(commits),
        "by_repo": dict(by_repo.most_common()),
        "by_author": dict(by_author.most_common(5)),
    }


class CommitsTool(Tool):
    name = "commits"
    description = (
        "Aggregate git activity across Coco's project tree (D:/Kee, "
        "D:/projects, D:/Codigo, D:/auctorum-systems, D:/nahual, …). "
        "Use to answer '¿qué commiteé hoy?' / '¿qué proyectos toqué esta "
        "semana?' / '¿cuántos commits llevo en X repo?'.\n"
        "Actions:\n"
        "  - 'today'   (default): commits since midnight local\n"
        "  - 'week'    : last 7 days\n"
        "  - 'window'  : custom `since` string (git syntax)\n"
        "  - 'summary' : per-repo + per-author counts only (no full list)\n"
        "  - 'repos'   : list discovered git repos under the roots"
    )
    risk_level = 0
    parameters_schema = {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["today", "week", "window", "summary", "repos"],
                "default": "today",
            },
            "since": {
                "type": "string",
                "description": "Git --since string (window only), e.g. "
                               "'2 hours ago' or '2026-05-01'.",
            },
            "author": {
                "type": "string",
                "description": "Optional --author filter.",
            },
            "roots": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Override the default scan roots. Useful "
                               "for one-off audits.",
            },
            "limit": {"type": "integer", "default": 50,
                      "description": "Cap on commits returned (sort: newest first)."},
            "summary_only": {
                "type": "boolean", "default": False,
                "description": "Return only per-repo/per-author counts, "
                               "not the full commit list.",
            },
        },
    }

    async def execute(
        self,
        action: str = "today",
        since: str | None = None,
        author: str | None = None,
        roots: list[str] | None = None,
        limit: int = 50,
        summary_only: bool = False,
    ) -> dict[str, Any]:
        if not _git_bin():
            return {"ok": False, "error": "git not found on PATH"}

        scan_roots = roots or _DEFAULT_ROOTS
        repos = _find_repos(scan_roots, max_depth=2)
        if action == "repos":
            return {"ok": True, "count": len(repos),
                    "repos": [str(r) for r in repos]}

        if action == "today":
            since_str = "midnight"
            window_label = "today (since midnight)"
        elif action == "week":
            since_str = "7 days ago"
            window_label = "last 7 days"
        elif action == "window":
            if not since:
                return {"ok": False,
                        "error": "window action requires `since`"}
            since_str = since
            window_label = f"since {since}"
        elif action == "summary":
            since_str = since or "7 days ago"
            window_label = f"summary since {since_str}"
            summary_only = True
        else:
            return {"ok": False, "error": f"unknown action {action!r}"}

        all_commits: list[dict] = []
        for r in repos:
            all_commits.extend(_git_log(r, since=since_str, author=author))

        # Dedup commits by SHA prefix across repo copies (e.g.
        # `auctorum-systems` and its `auctorumsis-backup/repo` mirror
        # produce identical SHAs — count once, label both repos).
        by_sha: dict[str, dict] = {}
        for c in all_commits:
            sha = c["sha"]
            if sha in by_sha:
                # Append the alternate repo name if not already there.
                seen = by_sha[sha]
                if c["repo"] not in seen.get("repos_seen_in", []):
                    seen.setdefault("repos_seen_in", [seen["repo"]]).append(c["repo"])
            else:
                by_sha[sha] = c
        all_commits = list(by_sha.values())

        # Newest first
        all_commits.sort(key=lambda c: c["ts"], reverse=True)

        out = _summarize(all_commits, window_label)
        out["roots_scanned"] = len(repos)
        if not summary_only:
            out["commits"] = all_commits[:int(limit)]
        return out


tool = CommitsTool()
