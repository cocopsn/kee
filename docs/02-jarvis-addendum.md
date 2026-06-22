# KEE — Addendum: Full Jarvis Capabilities
## Orchestración de Alto Nivel + Computer Use + Self-Awareness
### Lo que falta para que Kee sea verdaderamente Jarvis

---

## 1. El Escenario Objetivo

```
Tú (por voz): "Kee, hazme una landing page para Fat Dogs, shipeala en Vercel"

Kee (internamente):
  1. Busca contexto sobre Fat Dogs en el vault → equipo de flag football, 5v5
  2. Crea un directorio temporal ~/kee-workspace/fat-dogs-landing
  3. Ejecuta Claude Code en headless mode con el prompt completo
  4. Claude Code genera el proyecto Next.js completo
  5. Verifica que funcione con `npm run build`
  6. Despliega a Vercel via CLI
  7. Obtiene la URL del deployment

Kee (por voz): "Listo. fat-dogs.vercel.app está arriba. Es una landing con
               el roster, los 21 plays, y un formulario de contacto. ¿Quieres
               que te la muestre?"
```

Este flujo requiere tres capacidades que no están en el blueprint base:
1. **Orquestación de agentes externos** (Claude Code, Codex)
2. **Computer Use real** (ver pantalla, entender qué pasa, actuar)
3. **Self-awareness del estado del mundo** (saber qué proyectos tiene, qué deployments existen)

---

## 2. Herramienta: Claude Code Orchestrator

Claude Code tiene un modo headless (`-p` flag) que lo convierte en un agente programable. Kee lo usa como una **herramienta de ejecución de alto nivel** — el cerebro local (Qwen3.5 9B) decide QUÉ hacer, y delega el HOW a Claude Code cuando la tarea requiere capacidad que excede al modelo local.

Tool: `claude_code(prompt, working_directory, allowed_tools, deploy_to_vercel, max_turns)`. Spawns a `claude -p ...` subprocess in `acceptEdits` permission mode, returns the JSON output.

---

## 3. Herramienta: Computer Use (Screen Agent)

Pipeline: screenshot → vision LLM (Gemma 4 E4B, ~3.5GB, on Auctorum PC's GPU to avoid VRAM conflict with main Qwen3.5 on Alienware) → structured understanding → action.

Tool: `computer_use(action, query, x, y, text, key, scroll_direction, scroll_amount)` — actions: screenshot, click, type, scroll, key.

Vision delegated to Auctorum: `POST http://auctorum:11434/api/chat` with `huihui_ai/gemma-4-abliterated` and base64 image.

---

## 4. Herramienta: Browser Automation (Playwright)

Tool: `browser_control(action, url, selector, text, wait_for)` — actions: navigate, click, type, screenshot, get_text, close. Uses Playwright Chromium (non-headless during dev for visibility).

---

## 5. Herramienta: App Orchestrator

Tool: `open_app(app, args, focus_if_open)` — opens or focuses system apps (vscode, terminal, firefox, obsidian, spotify, files, discord). Tries `xdotool windowactivate` first (Linux) before launching.

---

## 6. Herramienta: Vercel Deployment Manager

Tool: `vercel_deploy(action, directory, production, deployment_url)` — actions: deploy, status, list, logs. Wraps the `vercel` CLI.

---

## 7. Herramienta: GitHub Operations

Tool: `github(action, repo_name, title, body, directory, private, branch)` — actions: create_repo, push, create_pr, list_issues, create_issue, check_ci, clone. Wraps `gh` CLI.

---

## 8. Complete Tool Inventory for Jarvis-Level Kee

### Core (Phase 0-1)
- `execute_shell` (risk 1) — bash commands
- `read_file` (risk 0)
- `write_file` (risk 1)
- `web_search` (risk 0) — SearXNG / DuckDuckGo
- `browse_url` (risk 0)
- `memory_search` (risk 0) — semantic via ChromaDB
- `memory_store` (risk 1)
- `system_status` (risk 0)
- `project_status` (risk 0)
- `goal_update` (risk 1)

### Voice + Perception (Phase 2-3)
- `ocr_screen` (risk 0)
- `computer_use` (risk 1)
- `open_app` (risk 0)
- `clipboard_read` (risk 0)
- `clipboard_write` (risk 1)
- `notification_send` (risk 1)

### Development (Phase 4)
- `claude_code` (risk 1)
- `vercel_deploy` (risk 2)
- `github` (risk 1)
- `browser_control` (risk 1)
- `docker_manage` (risk 2)

### Communication (Phase 4)
- `send_telegram` (risk 3)
- `send_whatsapp` (risk 3)
- `gmail_read` (risk 0)
- `gmail_draft` (risk 2)
- `gmail_send` (risk 3)

### Calendar + Productivity (Phase 5)
- `calendar_check` (risk 0)
- `calendar_create` (risk 2)
- `spotify_control` (risk 0)
- `schedule_task` (risk 1)

### Meta (Phase 5)
- `create_tool` (risk 2)
- `list_tools` (risk 0)
- `tool_docs` (risk 0)

---

## 9. Self-Awareness System

`SelfAwareness.generate_capabilities_prompt()` produces a dynamic block injected into the system prompt:
- Tools available (count + name + short description)
- Perception status (mic / screen / notifications / file watcher: ON/OFF)
- Hardware (free VRAM, CPU load, Auctorum PC online?)
- Limitations (context window, model VRAM, paid actions)

Re-rendered each turn so Kee always knows what it can and cannot do *right now*.

---

## 10. The Complete Flow: Voice → Think → Act → Verify → Speak

1. **PERCEIVE** — wake word → STT → text
2. **REMEMBER** — `memory_search` for context
3. **PLAN** — Qwen3.5 reasons about approach
4. **ACT** — call appropriate tools (claude_code, browser, shell, etc.)
5. **VERIFY** — `computer_use` screenshot or `browser_control` to confirm result
6. **RESPOND** — synthesize answer, TTS speaks it
7. **LOG** — audit logger records the entire chain

---

## 11. Prerequisites for Jarvis-Level Kee

### Software (Alienware Ubuntu)

```bash
npm install -g @anthropic-ai/claude-code
npm install -g vercel
sudo apt install gh xdotool scrot xclip
pip install playwright && playwright install chromium
```

### API Keys

| Service | Var | Purpose |
|---------|-----|---------|
| Anthropic | `ANTHROPIC_API_KEY` | Claude Code headless |
| Vercel | `VERCEL_TOKEN` | Deployments |
| GitHub | `gh auth login` | Repo management |
| Google | OAuth | Calendar, Gmail |
| Telegram | Bot token | Messaging |

Only the Anthropic API costs money — and only when Kee chooses to delegate to Claude Code. Everything else is free.

---

*This addendum completes the Jarvis vision. Kee is not just a voice assistant that answers questions — it's an autonomous agent that can see your screen, understand your context, orchestrate professional-grade coding tools, deploy to production, and verify the results — all from a voice command while you grab coffee.*
