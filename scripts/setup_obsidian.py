"""Configure D:/Kee/vault/ as a real Obsidian vault.

Doesn't install Obsidian itself — that's a Coco-side install from
https://obsidian.md/download (or `winget install Obsidian.Obsidian`).
This script ensures the vault is *recognized* as one and seeds sane
defaults that play well with Kee's writes:

  - .obsidian/app.json          (workspace settings)
  - .obsidian/appearance.json   (dark theme, monospace lines)
  - .obsidian/core-plugins.json (enable file-explorer, search, graph,
                                  daily-notes, templates, sync)
  - .obsidian/community-plugins.json (recommended community plugins doc)

Run once:
    python -m scripts.setup_obsidian
"""

from __future__ import annotations

import json
from pathlib import Path

VAULT = Path(r"D:/Kee/vault")
OBSIDIAN_DIR = VAULT / ".obsidian"


CORE_PLUGINS = [
    "file-explorer", "global-search", "switcher", "graph",
    "backlink", "outgoing-link", "tag-pane", "page-preview",
    "daily-notes", "templates", "note-composer", "command-palette",
    "markdown-importer", "outline", "word-count", "file-recovery",
    "publish",
]

# Minimal app.json — match Kee's font + dark mode
APP_JSON = {
    "showLineNumber": True,
    "spellcheck": False,
    "useTab": False,
    "tabSize": 2,
    "newFileLocation": "folder",
    "newFileFolderPath": "notes",
    "attachmentFolderPath": "_attachments",
    "promptDelete": True,
}

APPEARANCE_JSON = {
    "baseFontSize": 16,
    "monospaceFontFamily": "JetBrains Mono",
    "interfaceFontFamily": "Inter",
    "textFontFamily": "Inter",
    "theme": "obsidian",  # dark
}

WORKSPACE_JSON = {
    "main": {
        "id": "main", "type": "split", "children": [
            {"type": "tabs", "children": [
                {"type": "leaf", "state": {"type": "empty", "title": "Kee vault"}}
            ]}
        ],
    },
    "left": {"id": "left", "type": "split", "children": [], "direction": "horizontal", "width": 280},
    "right": {"id": "right", "type": "split", "children": [], "direction": "horizontal", "width": 280},
}


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            existing = json.loads(path.read_text(encoding="utf-8"))
            if existing == data:
                return  # idempotent
        except Exception:
            pass
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    print(f"  wrote {path.relative_to(VAULT)}")


def main() -> None:
    if not VAULT.exists():
        print(f"✗ Vault not found: {VAULT}")
        return
    print(f"Configuring Obsidian for vault at {VAULT}")
    OBSIDIAN_DIR.mkdir(exist_ok=True)
    write_json(OBSIDIAN_DIR / "app.json", APP_JSON)
    write_json(OBSIDIAN_DIR / "appearance.json", APPEARANCE_JSON)
    write_json(OBSIDIAN_DIR / "workspace.json", WORKSPACE_JSON)
    write_json(OBSIDIAN_DIR / "core-plugins.json", CORE_PLUGINS)
    write_json(OBSIDIAN_DIR / "community-plugins.json", [])

    # Make sure standard folders exist so Obsidian's file explorer renders
    for sub in ("notes", "_attachments", "config", "_kee", "projects",
                "people", "knowledge", "logs"):
        d = VAULT / sub
        d.mkdir(parents=True, exist_ok=True)

    # README inside the vault that opens by default
    readme = VAULT / "README.md"
    if not readme.exists():
        readme.write_text(
            "# Kee Vault\n\n"
            "This is Coco's Obsidian vault, written-to by Kee (the agent) "
            "and Coco himself.\n\n"
            "## Layout\n"
            "- `notes/` — free-form notes (what `notes` tool writes to)\n"
            "- `config/` — identity.md, soul.md, user.md, goals.md, "
            "router.md (LIVE — Kee reads on every turn)\n"
            "- `_kee/` — Kee's own scratch space (proposals, daily digests, "
            "archived tools, code proposals)\n"
            "- `projects/` — one .md per project (imported via "
            "`scripts/import_project_docs.py`)\n"
            "- `people/` — one .md per person Kee has interacted with\n"
            "- `knowledge/` — long-form reference material\n"
            "- `logs/` — append-only event log\n\n"
            "## Recommended Obsidian community plugins\n"
            "Install via Settings → Community plugins:\n"
            "- **Templater** — dynamic templates with date/time/Kee context\n"
            "- **Dataview** — query notes like a database\n"
            "- **Kanban** — board view for goals.md\n"
            "- **Calendar** — daily-notes navigator\n"
            "- **Periodic Notes** — daily/weekly/monthly note scaffolding\n"
            "- **Tag Wrangler** — tag refactoring\n"
            "- **Excalidraw** — sketches that live in the vault\n\n"
            "## Sync (optional, for multi-device)\n"
            "- Drop the vault on Syncthing / iCloud / OneDrive — Kee "
            "writes are atomic (.tmp + replace) so this is safe.\n"
            "- Or use Obsidian Sync ($) for end-to-end encrypted multi-device.\n",
            encoding="utf-8",
        )
        print(f"  wrote README.md")
    print("\n✓ Obsidian-ready. Open Obsidian → 'Open another vault' → "
          f"point at {VAULT} → done.")


if __name__ == "__main__":
    main()
