"""Apply a tool-rewrite proposal — explicit confirm required, auto-revert on fail.

The flow:
  1. Read `vault/_kee/tool_rewrites/<date>-<tool>.md` and extract the
     "Proposed rewrite" code block.
  2. Locate the tool's .py source via the live registry.
  3. Replace the `description = (…)` class attribute with a triple-quoted
     literal containing the proposed text.
  4. Run `python -m kee.main check` — if non-zero, revert via git.
  5. If clean, return success WITHOUT committing — the human reviews
     `git diff` and commits manually.

Safety:
  - Refuses if `confirm=True` not passed
  - Refuses if proposed text < 20 chars (likely placeholder)
  - Refuses if the .py file isn't tracked by git (no rollback path)
  - Always returns the diff so the agent can read it back to the user
"""

from __future__ import annotations

import logging
import re
import subprocess
from pathlib import Path
from typing import Any

from kee.config import settings

logger = logging.getLogger(__name__)


_PROPOSED_BLOCK_RE = re.compile(
    r"##\s*Proposed rewrite\s*\n+```\s*\n(?P<body>.+?)\n```",
    re.S,
)


def parse_proposal(md_path: Path) -> dict[str, Any]:
    """Extract the proposed description from the markdown."""
    if not md_path.exists():
        return {"ok": False, "error": f"no proposal at {md_path}"}
    text = md_path.read_text(encoding="utf-8")
    m = _PROPOSED_BLOCK_RE.search(text)
    if not m:
        return {"ok": False,
                "error": "could not find ## Proposed rewrite code block"}
    proposed = m.group("body").strip()
    if len(proposed) < 20:
        return {"ok": False,
                "error": f"proposed text too short ({len(proposed)} chars) "
                         "— likely placeholder, not applying"}
    # Tool name is in the filename: <date>-<tool>.md
    stem = md_path.stem
    if len(stem) <= 11 or stem[10] != "-":
        return {"ok": False, "error": f"can't parse tool from {stem!r}"}
    tool_name = stem[11:]
    return {"ok": True, "tool": tool_name,
            "proposed": proposed, "proposed_len": len(proposed)}


def find_tool_source(tool_name: str) -> Path | None:
    """Locate the tool's .py file via the registry."""
    try:
        from kee.core.tool_registry import ToolRegistry
        r = ToolRegistry()
        r.load_builtins()
    except Exception as e:
        logger.warning("registry load failed: %s", e)
        return None
    t = r.tools.get(tool_name)
    if not t:
        return None
    fp = getattr(t, "file_path", None)
    if fp:
        return Path(fp)
    # Builtins don't expose file_path — guess from kee/tools/<name>.py
    candidate = Path(__file__).resolve().parent.parent / "tools" / f"{tool_name}.py"
    return candidate if candidate.exists() else None


# Match `description = ("…")` with single, double, or triple quotes,
# possibly multi-line, on a class attribute. We anchor on the leading
# whitespace + `description = ` to stay inside the class scope.
_DESC_PATTERNS = [
    # Triple-quoted string (most likely)
    re.compile(
        r'^(?P<indent>\s+)description\s*=\s*"""(?P<body>.+?)"""',
        re.S | re.M,
    ),
    re.compile(
        r"^(?P<indent>\s+)description\s*=\s*'''(?P<body>.+?)'''",
        re.S | re.M,
    ),
    # Parenthesised concatenation: description = ("a" "b" ...)
    re.compile(
        r'^(?P<indent>\s+)description\s*=\s*\(\s*(?P<body>(?:[ru]?(?:"[^"]*"|\'[^\']*\')\s*)+)\)',
        re.S | re.M,
    ),
    # Single-line literal
    re.compile(
        r'^(?P<indent>\s+)description\s*=\s*"(?P<body>[^"]+)"',
        re.M,
    ),
]


def replace_description(source: str, new_text: str) -> tuple[str, bool]:
    """Find + replace the `description` class attribute. Returns
    (new_source, replaced)."""
    for pat in _DESC_PATTERNS:
        m = pat.search(source)
        if not m:
            continue
        indent = m.group("indent")
        # Use a triple-quoted literal so we don't have to escape internal
        # newlines or quotes in the proposed text.
        replacement = (
            f'{indent}description = """{new_text}"""'
        )
        return source[:m.start()] + replacement + source[m.end():], True
    return source, False


def _git_status_clean(file_path: Path) -> bool:
    """True if `file_path` has no uncommitted changes."""
    try:
        r = subprocess.run(
            ["git", "-C", str(settings.project_root if hasattr(settings, "project_root") else "."),
             "status", "--porcelain", str(file_path)],
            capture_output=True, text=True, timeout=5,
        )
        return r.returncode == 0 and not r.stdout.strip()
    except Exception:
        return False


def _git_revert(file_path: Path) -> bool:
    try:
        r = subprocess.run(
            ["git", "-C", str(settings.project_root if hasattr(settings, "project_root") else "."),
             "checkout", "--", str(file_path)],
            capture_output=True, text=True, timeout=10,
        )
        return r.returncode == 0
    except Exception:
        return False


def _run_check() -> tuple[bool, str]:
    """Run `python -m kee.main check` to confirm registry still loads."""
    try:
        r = subprocess.run(
            ["python", "-m", "kee.main", "check"],
            capture_output=True, text=True, timeout=30,
            cwd=str(settings.project_root if hasattr(settings, "project_root") else "."),
        )
        return r.returncode == 0, (r.stdout + r.stderr)[-400:]
    except Exception as e:
        return False, str(e)[:200]


def apply_proposal(date: str, tool_name: str,
                    confirm: bool = False) -> dict[str, Any]:
    """Apply a tool rewrite proposal. SAFE:
      - confirm=True required
      - .py file must be git-clean (no other uncommitted edits to it)
      - auto-reverts if `python -m kee.main check` fails after edit
      - never auto-commits — leaves git diff for human review
    """
    if not confirm:
        return {"ok": False,
                "error": "confirm=True required (this modifies tool source)"}

    md_path = settings.vault_dir / "_kee" / "tool_rewrites" / f"{date}-{tool_name}.md"
    parsed = parse_proposal(md_path)
    if not parsed.get("ok"):
        return parsed

    source_path = find_tool_source(tool_name)
    if not source_path or not source_path.exists():
        return {"ok": False,
                "error": f"no source file for tool {tool_name!r}"}

    if not _git_status_clean(source_path):
        return {"ok": False,
                "error": (f"{source_path} has uncommitted changes — "
                          "commit or stash first")}

    original = source_path.read_text(encoding="utf-8")
    new_source, replaced = replace_description(
        original, parsed["proposed"],
    )
    if not replaced:
        return {"ok": False,
                "error": ("could not find a `description = (...)` "
                          "class attribute to replace in "
                          f"{source_path}")}

    source_path.write_text(new_source, encoding="utf-8")

    check_ok, check_output = _run_check()
    if not check_ok:
        # Revert — registry can't load with the rewrite.
        reverted = _git_revert(source_path)
        return {
            "ok": False,
            "error": "kee.main check FAILED after rewrite — auto-reverted",
            "reverted": reverted,
            "check_tail": check_output,
        }

    # Compute a small diff summary
    diff_summary = subprocess.run(
        ["git", "-C", str(settings.project_root if hasattr(settings, "project_root") else "."),
         "diff", "--stat", str(source_path)],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()

    return {
        "ok": True,
        "tool": tool_name,
        "source_path": str(source_path),
        "proposed_chars": parsed["proposed_len"],
        "diff_stat": diff_summary,
        "next_step": (
            f"Review the diff: git diff {source_path} — "
            "then commit when satisfied."
        ),
    }
