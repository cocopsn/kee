# Kee Dashboard

SvelteKit + Tailwind 4 + TypeScript. The first of the three UIs Coco wants
(per `~/.claude/projects/D--Kee/memory/project_kee.md` UI vision).

This is the **nervous-system view**: live audit log, heartbeat snapshots,
tool registry, goals, economy, and a chat surface. Designed to ship as a
real desktop app via Tauri later — the SvelteKit build is configured for
the static adapter so a Tauri wrap is a one-step add.

## Dev

```powershell
# 1. Backend (one terminal)
cd D:\Kee
.\.venv\Scripts\python.exe -m kee.main api
# Listens on http://127.0.0.1:7330

# 2. Frontend (another terminal)
cd D:\Kee\dashboard
npm install     # first time
npm run dev
# Listens on http://localhost:5173
```

Open http://localhost:5173 in your browser.

## Routes

| Path | Purpose |
|------|---------|
| `/` | Chat surface — POST /chat with conversation continuity |
| `/nervous-system` | 3-pane live view: audit log + heartbeat + WebSocket stream |
| `/tools` | Tool registry with risk + per-tool autonomy stats |
| `/goals` | Goals tracker + economy lifetime |

## Build for production / Tauri wrap

```powershell
npm run build
# Output in `build/` — static SPA. Drop into a Tauri project as the
# `frontendDist` path and you have a real desktop app.
```

## Configuration

Set `VITE_KEE_API` to point at a different backend:

```bash
VITE_KEE_API=http://192.168.1.20:7330 npm run dev
```

Default: `http://127.0.0.1:7330` (matches `python -m kee.main api`).
