# Kee Vault

This is Coco's Obsidian vault, written-to by Kee (the agent) and Coco himself.

## Layout
- `notes/` — free-form notes (what `notes` tool writes to)
- `config/` — identity.md, soul.md, user.md, goals.md, router.md (LIVE — Kee reads on every turn)
- `_kee/` — Kee's own scratch space (proposals, daily digests, archived tools, code proposals)
- `projects/` — one .md per project (imported via `scripts/import_project_docs.py`)
- `people/` — one .md per person Kee has interacted with
- `knowledge/` — long-form reference material
- `logs/` — append-only event log

## Recommended Obsidian community plugins
Install via Settings → Community plugins:
- **Templater** — dynamic templates with date/time/Kee context
- **Dataview** — query notes like a database
- **Kanban** — board view for goals.md
- **Calendar** — daily-notes navigator
- **Periodic Notes** — daily/weekly/monthly note scaffolding
- **Tag Wrangler** — tag refactoring
- **Excalidraw** — sketches that live in the vault

## Sync (optional, for multi-device)
- Drop the vault on Syncthing / iCloud / OneDrive — Kee writes are atomic (.tmp + replace) so this is safe.
- Or use Obsidian Sync ($) for end-to-end encrypted multi-device.
