"""Build docs/05-full-inventory.md — the exhaustive reference of every
module, function, class, tool, endpoint, page, table, script and test
that ships in Kee right now.

The doc is generated mechanically from:
  - data/_tools_inventory.json (introspected from the live ToolRegistry)
  - data/_modules_inventory.json (AST walk of kee/**.py)
  - kee/surfaces/api.py (regex-grep of @app.{verb})
  - kee/core/db.py + the live SQLite schema
  - dashboard/src/routes (filesystem walk)
  - tests/, scripts/, docs/ (filesystem walk)

Re-run after adding tools / files. It's intentionally generated rather
than hand-written so the inventory cannot drift.

Usage::

    .venv\\Scripts\\python.exe scripts\\build_inventory_md.py
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_PATH = ROOT / "docs" / "05-full-inventory.md"


def _read_json(p: Path) -> dict:
    return json.loads(p.read_text(encoding="utf-8"))


def _api_endpoints() -> list[tuple[str, str]]:
    """Return [(verb, path)] for every @app decorator in surfaces/api.py."""
    src = (ROOT / "kee" / "surfaces" / "api.py").read_text(encoding="utf-8")
    pat = re.compile(r'^@app\.(get|post|put|delete|patch|websocket)\("([^"]+)"', re.M)
    return [(m.group(1).upper(), m.group(2)) for m in pat.finditer(src)]


def _db_tables() -> list[tuple[str, int]]:
    """[(table_name, column_count)] for every user table."""
    db = ROOT / "data" / "kee.db"
    if not db.exists():
        return []
    out = []
    with sqlite3.connect(str(db)) as c:
        rows = c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY name"
        ).fetchall()
        for (name,) in rows:
            cols = c.execute(f"PRAGMA table_info({name})").fetchall()
            out.append((name, len(cols)))
    return out


def _dashboard_pages() -> list[str]:
    """List of route paths."""
    routes_dir = ROOT / "dashboard" / "src" / "routes"
    out = []
    for p in sorted(routes_dir.rglob("+page.svelte")):
        rel = p.parent.relative_to(routes_dir).as_posix()
        out.append("/" if rel == "." else f"/{rel}")
    return out


def _components() -> list[str]:
    cdir = ROOT / "dashboard" / "src" / "lib" / "components"
    if not cdir.exists():
        return []
    return [p.stem for p in sorted(cdir.glob("*.svelte"))]


def _tests() -> list[str]:
    return [p.name for p in sorted((ROOT / "tests").glob("test_*.py"))]


def _scripts() -> list[str]:
    out = []
    sd = ROOT / "scripts"
    for p in sorted(sd.iterdir()):
        if p.is_file():
            out.append(p.name)
        elif p.is_dir():
            for q in sorted(p.iterdir()):
                if q.is_file():
                    out.append(f"{p.name}/{q.name}")
    return out


def _docs() -> list[str]:
    return [p.name for p in sorted((ROOT / "docs").glob("*.md"))]


# ── Group tools by category for readability ──────────────────────────────
TOOL_CATEGORIES: dict[str, list[str]] = {
    "Filesystem & Shell": [
        "files", "execute_shell", "system_status", "clipboard",
        "windows", "screen", "desktop_control", "open_app",
        "system_control",
    ],
    "Web & Search": [
        "web_search", "fetch_url", "smart_search", "research",
        "news", "weather",
    ],
    "Memory & Recall": [
        "memory_search", "recall", "vault_search", "episodic",
        "narrate_day", "recap_week", "compare_days", "notes",
    ],
    "Cognition & Planning": [
        "plan", "infer_goal", "goals", "reflect", "dispatch",
        "quality_snapshot", "context",
    ],
    "Productivity": [
        "work_session", "focus", "pomodoro", "learn", "projects",
        "brief",
    ],
    "Communication": [
        "notify", "emit", "email_send", "gmail", "whatsapp_send",
        "calendar",
    ],
    "System Integrations": [
        "spotify", "home_assistant", "browser_control", "wol",
        "market",
    ],
    "Code & DevOps": [
        "claude_code", "github", "vercel_deploy", "scaffold",
        "commits",
    ],
    "Self-Evolution": [
        "create_tool", "tool_reliability", "apply_rewrite",
        "schedule_self", "user_patterns", "perf_stats",
    ],
    "Worker / Vision / RAG": [
        "worker_health", "vision", "describe_screen",
    ],
    "Observability": [
        "inbox_triage", "economy", "world_model",
    ],
}


# ── The actual builder ───────────────────────────────────────────────────
def main() -> None:
    tools = _read_json(ROOT / "data" / "_tools_inventory.json")
    modules = _read_json(ROOT / "data" / "_modules_inventory.json")
    endpoints = _api_endpoints()
    tables = _db_tables()
    pages = _dashboard_pages()
    components = _components()
    tests = _tests()
    scripts_list = _scripts()
    docs_list = _docs()

    by_name = {t["name"]: t for t in tools}
    categorized = {n for v in TOOL_CATEGORIES.values() for n in v}
    uncategorized = [t["name"] for t in tools if t["name"] not in categorized]
    if uncategorized:
        TOOL_CATEGORIES["Uncategorized"] = uncategorized

    # Group module entries by top-level subdirectory
    by_subdir: dict[str, list[tuple[str, dict]]] = {}
    for path, info in sorted(modules.items()):
        parts = path.split("/")
        if len(parts) < 2:
            continue
        subdir = parts[1] if parts[0] == "kee" and len(parts) >= 3 else "(top)"
        by_subdir.setdefault(subdir, []).append((path, info))

    L: list[str] = []
    L.append("# Kee — Full Inventory")
    L.append("")
    L.append(f"> Generated mechanically by `scripts/build_inventory_md.py` "
             f"on {date.today().isoformat()}. Re-run after adding tools or "
             f"modules — this file should never drift from the source.")
    L.append("")
    L.append("**Topline counts:**")
    L.append("")
    L.append(f"- **{len(tools)} tools** in the live registry")
    total_funcs = sum(len(v.get("functions", [])) for v in modules.values())
    total_classes = sum(len(v.get("classes", [])) for v in modules.values())
    L.append(f"- **{len(modules)} Python modules** under `kee/` "
             f"(excluding `__init__.py` and `__pycache__`)")
    L.append(f"- **{total_classes} public classes**, **{total_funcs} public functions**")
    L.append(f"- **{len(endpoints)} HTTP endpoints** + 1 WebSocket on the API surface")
    L.append(f"- **{len(tables)} SQLite tables**")
    L.append(f"- **{len(pages)} dashboard pages**, {len(components)} reusable components")
    L.append(f"- **{len(tests)} regression suites** (all $0)")
    L.append(f"- **{len(scripts_list)} provisioning + maintenance scripts**")
    L.append(f"- **{len(docs_list)} documentation files**")
    L.append("")
    L.append("---")
    L.append("")

    L.append("## Table of contents")
    L.append("")
    L.append("1. [Tools (live registry)](#1-tools-live-registry)")
    L.append("2. [Python modules by subsystem](#2-python-modules-by-subsystem)")
    L.append("3. [HTTP API endpoints](#3-http-api-endpoints)")
    L.append("4. [SQLite schema](#4-sqlite-schema)")
    L.append("5. [Dashboard pages + components](#5-dashboard-pages--components)")
    L.append("6. [Tests](#6-tests)")
    L.append("7. [Scripts](#7-scripts)")
    L.append("8. [Documentation](#8-documentation)")
    L.append("9. [Vault layout](#9-vault-layout)")
    L.append("")
    L.append("---")
    L.append("")

    # ── 1. Tools ─────────────────────────────────────────────────────────
    L.append(f"## 1. Tools (live registry)")
    L.append("")
    L.append(f"All {len(tools)} tools currently exposed to the agent. Risk "
             f"levels: **0** = read-only, **1** = local writes, **2** = code/"
             f"infra changes, **3** = externally visible / irreversible.")
    L.append("")
    for cat, names in TOOL_CATEGORIES.items():
        present = [n for n in names if n in by_name]
        if not present:
            continue
        L.append(f"### {cat} ({len(present)})")
        L.append("")
        L.append("| Tool | Risk | Module | Purpose |")
        L.append("|---|:-:|---|---|")
        for n in present:
            t = by_name[n]
            desc = t["desc"].replace("|", "\\|")
            mod = t["module"]
            L.append(f"| `{t['name']}` | {t['risk']} | `{mod}` | {desc} |")
        L.append("")
    L.append("---")
    L.append("")

    # ── 2. Modules ───────────────────────────────────────────────────────
    L.append("## 2. Python modules by subsystem")
    L.append("")
    L.append(f"AST walk of `kee/**.py`. Each module shows its 1-line "
             f"docstring (top of file) plus public classes and functions "
             f"(names not starting with `_`).")
    L.append("")
    subdir_order = ["core", "tools", "cognition", "perception",
                    "surfaces", "distributed", "daemon", "desktop", "(top)"]
    for sd in subdir_order:
        if sd not in by_subdir:
            continue
        entries = by_subdir[sd]
        L.append(f"### `kee/{sd}/`  —  {len(entries)} modules")
        L.append("")
        for path, info in entries:
            L.append(f"#### `{path}`")
            doc = info.get("doc") or "_(no module docstring)_"
            L.append(f"> {doc}")
            L.append("")
            classes = info.get("classes") or []
            funcs = info.get("functions") or []
            if classes:
                L.append(f"**Classes:** " +
                         ", ".join(f"`{c}`" for c in classes))
            if funcs:
                L.append(f"**Functions:** " +
                         ", ".join(f"`{f}`" for f in funcs))
            if not classes and not funcs:
                L.append("_(no public symbols at module level)_")
            L.append("")
        L.append("---")
        L.append("")

    # ── 3. API endpoints ─────────────────────────────────────────────────
    L.append("## 3. HTTP API endpoints")
    L.append("")
    L.append(f"All routes registered on the FastAPI app in "
             f"`kee/surfaces/api.py`. The dashboard, voice surface, "
             f"telegram bot, browser extension, mobile edge node and "
             f"the Termux client all consume this same surface.")
    L.append("")
    L.append("| Verb | Path |")
    L.append("|---|---|")
    for verb, path in endpoints:
        L.append(f"| `{verb}` | `{path}` |")
    L.append("")
    L.append("Plus:")
    L.append("")
    L.append("- WebSocket `/stream` — push channel for cross-process audit "
             "events (consumed by the dashboard's NeuralCanvas + voice HUD).")
    L.append("- Static mount `/app/*` — pre-built SvelteKit dashboard "
             "served from `dashboard/build/`.")
    L.append("")
    L.append("---")
    L.append("")

    # ── 4. SQLite schema ─────────────────────────────────────────────────
    L.append("## 4. SQLite schema")
    L.append("")
    L.append(f"Tables in `data/kee.db` — Kee's single source of persistent "
             f"truth. Schema is forward-only: only `_ADDITIVE_MIGRATIONS` "
             f"in `kee/core/db.py` may add columns; never rename or drop.")
    L.append("")
    L.append("| Table | Columns |")
    L.append("|---|:-:|")
    for name, ncols in tables:
        L.append(f"| `{name}` | {ncols} |")
    L.append("")
    L.append("---")
    L.append("")

    # ── 5. Dashboard ─────────────────────────────────────────────────────
    L.append("## 5. Dashboard pages + components")
    L.append("")
    L.append(f"SvelteKit 2 + Svelte 5 runes + Tailwind 4. Built into "
             f"`dashboard/build/` and served at `/app/*`. Every page is "
             f"reactive against the WebSocket `/stream`.")
    L.append("")
    L.append(f"### Pages ({len(pages)})")
    L.append("")
    for p in pages:
        url = "/app" if p == "/" else f"/app{p}"
        L.append(f"- `{p}`  →  http://localhost:7330{url}")
    L.append("")
    L.append(f"### Reusable components ({len(components)})")
    L.append("")
    for c in components:
        L.append(f"- `{c}`")
    L.append("")
    L.append("---")
    L.append("")

    # ── 6. Tests ─────────────────────────────────────────────────────────
    L.append("## 6. Tests")
    L.append("")
    L.append(f"All {len(tests)} suites under `tests/`. The runner is "
             f"`tests/run_all.py`. **No paid LLM is allowed** — `_FakeLLM` "
             f"stubs cover every place `llm.chat()` is called. The whole "
             f"suite runs offline in ~30s.")
    L.append("")
    for t in tests:
        L.append(f"- `tests/{t}`")
    L.append("")
    L.append("---")
    L.append("")

    # ── 7. Scripts ───────────────────────────────────────────────────────
    L.append("## 7. Scripts")
    L.append("")
    L.append(f"Provisioning + maintenance helpers under `scripts/`. "
             f"`scripts/auctorum/` ships everything needed to bring up the "
             f"worker on a fresh Ubuntu 24.04 box.")
    L.append("")
    for s in scripts_list:
        L.append(f"- `scripts/{s}`")
    L.append("")
    L.append("---")
    L.append("")

    # ── 8. Docs ──────────────────────────────────────────────────────────
    L.append("## 8. Documentation")
    L.append("")
    for d in docs_list:
        L.append(f"- `docs/{d}`")
    L.append("")
    L.append("Plus repo-root: `README.md`, `STATUS.md`, `CLAUDE.md`.")
    L.append("")
    L.append("---")
    L.append("")

    # ── 9. Vault ─────────────────────────────────────────────────────────
    L.append("## 9. Vault layout")
    L.append("")
    L.append("The vault is a real Obsidian vault, gitignored except for "
             "templates. It's where Kee writes day narratives, weekly "
             "recaps, tool-rewrite proposals, and where the user keeps "
             "notes/projects/identity.")
    L.append("")
    L.append("```")
    L.append("vault/")
    L.append("├── .obsidian/                  (Obsidian config — committed)")
    L.append("├── README.md                   (committed)")
    L.append("├── config/")
    L.append("│   ├── identity.md             (gitignored — who Kee is)")
    L.append("│   ├── soul.md                 (gitignored — Kee's voice)")
    L.append("│   ├── user.md                 (gitignored — about Coco)")
    L.append("│   ├── goals.md                (gitignored — active goals)")
    L.append("│   ├── router.md               (committed — 5-tier rules)")
    L.append("│   └── *.template.md           (committed — seed templates)")
    L.append("├── notes/                      (gitignored — Coco's notes)")
    L.append("├── projects/                   (gitignored — per-project markdown)")
    L.append("└── _kee/                       (gitignored — Kee's own writes)")
    L.append("    ├── daily/<date>.md         (narrate_day output)")
    L.append("    ├── daily/<date>-week.md    (recap_week output)")
    L.append("    ├── tools/                  (create_tool output, archived versions)")
    L.append("    ├── tool_rewrites/          (Sleep Cycle Phase 9 proposals)")
    L.append("    └── digests/                (nightly digest grounding)")
    L.append("```")
    L.append("")
    L.append("---")
    L.append("")
    L.append(f"_End of inventory. To regenerate: "
             f"`.venv\\Scripts\\python.exe scripts\\build_inventory_md.py`._")
    L.append("")

    OUT_PATH.write_text("\n".join(L), encoding="utf-8")
    print(f"wrote {OUT_PATH} ({sum(len(line) for line in L):,} chars, "
          f"{len(L):,} lines)")


if __name__ == "__main__":
    main()
