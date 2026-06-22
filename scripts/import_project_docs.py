"""Scan local project directories and import their docs into Kee's vault.

Walks roots like `D:/projects`, `D:/Codigo`, `D:/auctorum-systems`,
`D:/nahual` and for each project (= a directory containing a marker
like .git / package.json / pyproject.toml / Cargo.toml), produces:

    vault/projects/<project_name>.md

The .md contains:
  - Project name + path + last-modified date
  - README.md content (verbatim, capped at 12000 chars)
  - package.json description / pyproject metadata
  - .env.example or sample config (sensitive bits redacted)
  - Top 10 source files by line count
  - Recent git log (last 10 commits, if .git/)

Useful so Kee's `memory_search` (RAG) can pull project context into
conversations without Coco having to re-explain what each project is.

Usage:
    python -m scripts.import_project_docs           # default roots
    python -m scripts.import_project_docs D:/work   # custom root
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Iterable

VAULT = Path(r"D:/Kee/vault")
PROJECTS_DIR = VAULT / "projects"
DEFAULT_ROOTS = [
    Path(r"D:/projects"),
    Path(r"D:/Codigo"),
    Path(r"D:/auctorum-systems"),
    Path(r"D:/nahual"),
    Path(r"D:/auctorumsis-backup"),
]
MARKERS = (".git", "package.json", "pyproject.toml", "Cargo.toml",
           "go.mod", "Gemfile", "build.gradle", "pom.xml")
SKIP_DIRS = {"node_modules", ".venv", "venv", "__pycache__", ".next",
             "dist", "build", ".turbo", ".cache", "target", ".git"}
SKIP_FILE_PATTERNS = ("package-lock.json", "yarn.lock", "pnpm-lock.yaml",
                       "Cargo.lock", ".min.js", ".min.css")


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    return re.sub(r"[\s_-]+", "-", s)[:60] or "project"


def _is_project(p: Path) -> bool:
    return any((p / m).exists() for m in MARKERS)


def find_projects(roots: list[Path]) -> Iterable[Path]:
    for root in roots:
        if not root.exists():
            continue
        # Top-level: any subdir with a marker
        for child in root.iterdir():
            if not child.is_dir():
                continue
            if child.name in SKIP_DIRS:
                continue
            if _is_project(child):
                yield child
            else:
                # Look one level deeper for monorepos
                try:
                    for grand in child.iterdir():
                        if grand.is_dir() and grand.name not in SKIP_DIRS and _is_project(grand):
                            yield grand
                except (OSError, PermissionError):
                    continue


def _read_capped(p: Path, max_chars: int = 12000) -> str:
    try:
        return p.read_text(encoding="utf-8", errors="replace")[:max_chars]
    except Exception:
        return ""


def _redact(s: str) -> str:
    """Strip obvious secrets from .env-style content."""
    s = re.sub(r'(["\']?\b(?:api[_-]?key|secret|token|password)\b["\']?\s*[:=]\s*)["\']?[^"\'\n,;]+',
               r'\1[REDACTED]', s, flags=re.IGNORECASE)
    s = re.sub(r"sk-[A-Za-z0-9]{20,}", "[REDACTED-API-KEY]", s)
    return s


def _git_log(p: Path, n: int = 10) -> str:
    if not (p / ".git").exists():
        return ""
    try:
        r = subprocess.run(
            ["git", "log", f"-n{n}", "--pretty=format:- %h %ad %s", "--date=short"],
            cwd=str(p), capture_output=True, text=True, timeout=10,
        )
        return r.stdout.strip() if r.returncode == 0 else ""
    except Exception:
        return ""


def _top_source_files(p: Path, top: int = 10) -> list[tuple[int, str]]:
    counts: list[tuple[int, str]] = []
    for f in p.rglob("*"):
        if not f.is_file():
            continue
        if any(d in f.parts for d in SKIP_DIRS):
            continue
        if any(f.name.endswith(s) for s in SKIP_FILE_PATTERNS):
            continue
        if f.suffix not in (".py", ".ts", ".tsx", ".js", ".jsx", ".svelte",
                            ".go", ".rs", ".java", ".rb", ".cs", ".kt"):
            continue
        try:
            n = sum(1 for _ in f.open("r", encoding="utf-8", errors="replace"))
            counts.append((n, str(f.relative_to(p)).replace("\\", "/")))
        except Exception:
            continue
    counts.sort(reverse=True)
    return counts[:top]


def project_to_md(p: Path) -> str:
    name = p.name
    parts = [f"# {name}", "", f"`{p}`", "",
             f"*Imported {datetime.now().isoformat(timespec='minutes')}*", ""]

    readme = None
    for cand in ("README.md", "Readme.md", "readme.md", "README", "README.rst"):
        if (p / cand).exists():
            readme = (p / cand); break
    if readme:
        parts += ["## README", "", _read_capped(readme), ""]

    pkg = p / "package.json"
    if pkg.exists():
        try:
            import json
            d = json.loads(pkg.read_text(encoding="utf-8"))
            parts += ["## package.json", "",
                      f"- name: `{d.get('name','')}`",
                      f"- version: `{d.get('version','')}`",
                      f"- description: {d.get('description','—')}",
                      f"- scripts: {', '.join((d.get('scripts') or {}).keys())}",
                      f"- deps: {', '.join((d.get('dependencies') or {}).keys())[:300]}",
                      ""]
        except Exception: pass

    pp = p / "pyproject.toml"
    if pp.exists():
        parts += ["## pyproject.toml", "", "```toml",
                  _read_capped(pp, 1500), "```", ""]

    # Sample env (.env.example or .env.sample)
    for cand in (".env.example", ".env.sample", ".env.dist"):
        if (p / cand).exists():
            parts += [f"## {cand}", "", "```env",
                      _redact(_read_capped(p / cand, 2000)), "```", ""]
            break

    top = _top_source_files(p)
    if top:
        parts += ["## Top source files (by line count)", ""]
        for n, fn in top:
            parts += [f"- `{fn}` — {n} lines"]
        parts += [""]

    log = _git_log(p)
    if log:
        parts += ["## Recent commits", "", log, ""]

    return "\n".join(parts)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("roots", nargs="*", help="extra roots to scan")
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    roots = DEFAULT_ROOTS + [Path(r) for r in args.roots]
    print(f"Scanning {len(roots)} roots: {[str(r) for r in roots if r.exists()]}")
    PROJECTS_DIR.mkdir(parents=True, exist_ok=True)
    written = 0
    seen: set[str] = set()
    for proj in find_projects(roots):
        if str(proj) in seen:
            continue
        seen.add(str(proj))
        if written >= args.limit:
            break
        out = PROJECTS_DIR / f"{_slug(proj.name)}.md"
        try:
            out.write_text(project_to_md(proj), encoding="utf-8")
            written += 1
            print(f"  ✓ {proj.name} → {out.relative_to(VAULT)}")
        except Exception as e:
            print(f"  ✗ {proj.name}: {e}")
    print(f"\nWrote {written} project docs to {PROJECTS_DIR}")
    print("Run `python -m kee.main index` to feed them into RAG (when ChromaDB is up).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
