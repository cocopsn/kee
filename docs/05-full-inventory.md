# Kee — Full Inventory

> Generated mechanically by `scripts/build_inventory_md.py` on 2026-06-07. Re-run after adding tools or modules — this file should never drift from the source.

**Topline counts:**

- **65 tools** in the live registry
- **134 Python modules** under `kee/` (excluding `__init__.py` and `__pycache__`)
- **153 public classes**, **247 public functions**
- **100 HTTP endpoints** + 1 WebSocket on the API surface
- **20 SQLite tables**
- **18 dashboard pages**, 8 reusable components
- **37 regression suites** (all $0)
- **20 provisioning + maintenance scripts**
- **6 documentation files**

---

## Table of contents

1. [Tools (live registry)](#1-tools-live-registry)
2. [Python modules by subsystem](#2-python-modules-by-subsystem)
3. [HTTP API endpoints](#3-http-api-endpoints)
4. [SQLite schema](#4-sqlite-schema)
5. [Dashboard pages + components](#5-dashboard-pages--components)
6. [Tests](#6-tests)
7. [Scripts](#7-scripts)
8. [Documentation](#8-documentation)
9. [Vault layout](#9-vault-layout)

---

## 1. Tools (live registry)

All 65 tools currently exposed to the agent. Risk levels: **0** = read-only, **1** = local writes, **2** = code/infra changes, **3** = externally visible / irreversible.

### Filesystem & Shell (9)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `files` | 1 | `kee.tools.files` | Read, write or list files on the host. **USE THIS — not execute_shell — for any filesystem listing, file read, or file write operation.** Cross-platform: works the same on Windows and Linux. action='read' returns file co... |
| `execute_shell` | 1 | `kee.tools.shell` | Execute a single shell command on the host. Returns stdout, stderr and exit code. **Prefer the `files` tool for ls/cat/read/write — they're cross-platform; shell commands like `ls` fail on Windows.** Use this for things ... |
| `system_status` | 0 | `kee.tools.system` | Report the host machine's CPU, memory, disk, uptime and network reachability of the Auctorum worker node. Use to answer questions about system health or before starting heavy work. |
| `clipboard` | 1 | `kee.tools.clipboard` | Read or write the OS clipboard. Useful when the user says 'copia esto', 'pégalo en X', 'qué tengo copiado'. Cross-platform via pyperclip with a Windows-native fallback. Actions: 'get' (read), 'set' (write text), 'clear' ... |
| `windows` | 1 | `kee.tools.windows` | Enumerate running applications + the foreground window. Use to give the agent context about what Coco has open ('está en VSCode ahora'), and to focus or close specific windows by title. Actions:   - 'list':     all runni... |
| `screen` | 2 | `kee.tools.screen` | Direct OS-level screen + cursor + keyboard control. Actions: screenshot (full or region), mouse_move(x,y), mouse_click(x,y,button), type_text(text), find_text(query) → returns the (x,y) center of the first OCR match. All... |
| `desktop_control` | 1 | `kee.tools.desktop_control` | Show, hide, or change the Kee hologram overlay on the user's screen. Use when the user asks Kee to disappear, come back, watch the screen, or switch between modes. The hologram is a transparent overlay with the neural ca... |
| `open_app` | 1 | `kee.tools.open_app` | Open a desktop application or focus it if already running. Use for voice commands like 'abre VS Code' / 'open Spotify' / 'enfoca el browser'. Known apps (Windows): vscode, firefox, chrome, obsidian, spotify, discord, exp... |
| `system_control` | 1 | `kee.tools.system_control` | Control physical hardware: volume, brightness, sleep, lock. Voice ergonomics — when Coco says 'súbele al volumen', 'bájale el brillo', 'duerme la compu', 'bloquea la pantalla'. Actions:   - 'volume': get or set system vo... |

### Web & Search (6)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `web_search` | 0 | `kee.tools.web` | Search the web for a query. Returns the DuckDuckGo instant answer and related topics. Use for factual lookups; for in-depth content follow up with fetch_url on a specific result. |
| `fetch_url` | 0 | `kee.tools.web` | HTTP GET a URL and return the response body. Use after web_search to read the actual content of a result. Truncated to 8000 chars. |
| `smart_search` | 0 | `kee.tools.smart_search` | Búsqueda unificada sobre todo lo que Kee almacena: messages, plan_history, dispatches, notifications, y opcionalmente tool_calls del audit y notas del vault. Devuelve una lista plana rankeada con `source` etiquetado para... |
| `research` | 0 | `kee.tools.research` | One-shot research: web search + scrape the top result's full readable text in a single call. Use instead of chaining web_search → fetch_url manually. Cuts answer latency in half for explainer-style queries. |
| `news` | 0 | `kee.tools.news` | Top news headlines for a topic via DuckDuckGo News. Returns title, source, timestamp, snippet for the top N results. Free, no API key needed. Use for daily briefings (sleep_cycle), user queries about current events, mark... |
| `weather` | 0 | `kee.tools.weather` | Get current weather + 3-day forecast for a city. Free Open-Meteo API, no key needed. Default city auto-detected from vault/config/user.md (looks for a 'ciudad:' or 'city:' line). |

### Memory & Recall (8)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `memory_search` | 0 | `kee.tools.memory_tool` | Semantic search the Obsidian vault for context relevant to a query. Use for: looking up project history, prior decisions, knowledge base entries, or anything Armando has written down. Returns top-K matching passages from... |
| `recall` | 0 | `kee.tools.recall` | Search Kee's own past conversations (the messages table) for prior context — what was said, by whom, when. Use BEFORE asking the user to repeat himself, BEFORE reaching for memory_search (which queries the vault, not cha... |
| `vault_search` | 0 | `kee.tools.vault_search` | Substring search literal sobre todo el vault (.md files). Diferente de:   - `memory_search` (semantic, requiere ChromaDB)   - `projects search` (sólo vault/projects/)   - `notes search` (sólo vault/notes/)   - `recall` (... |
| `episodic` | 0 | `kee.tools.episodic` | Búsqueda semántica unificada sobre TODO lo que pasó: conversaciones, dispatches, planes, focus sessions, learnings, notificaciones y perception events. Diferente de `recall` (solo messages), `memory_search` (solo vault),... |
| `narrate_day` | 0 | `kee.tools.narrate_day` | Línea-de-tiempo en markdown de TODO lo que pasó un día específico: commits, dispatches, planes, focus sessions, notificaciones, perception events, y conversaciones. Determinístico, sin LLM (zero-cost). Ideal para 'qué hi... |
| `recap_week` | 0 | `kee.tools.recap_week` | Resumen agregado de los últimos 7 días — totales semanales + una línea por día + top 10 commits cross-day. Llama `narrate_day` internamente para cada fecha; cero costo LLM. Útil para review dominical, weekly Telegram dig... |
| `compare_days` | 0 | `kee.tools.compare_days` | Diff dos días — counts side-by-side de commits, dispatches, planes, focus, notificaciones, perception, conversaciones. Acepta 'today' \| 'yesterday' \| 'YYYY-MM-DD' \| '-N' (N días atrás). Cero costo LLM. Útil para 'lunes v... |
| `notes` | 1 | `kee.tools.notes` | Read, search, list, create, or append notes in the Obsidian vault at vault/. Notes default to vault/notes/. For semantic search across the entire knowledge base use memory_search (ChromaDB RAG); this tool is the fast sub... |

### Cognition & Planning (7)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `plan` | 0 | `kee.tools.planner` | Generate, persist, and recall execution plans. Actions:   - 'propose'       (default): generate 2-5 distinct plans for                       `task`, score them quality - risk - time,                       return winner +... |
| `infer_goal` | 0 | `kee.tools.infer_goal` | Map a high-level / casual directive from Armando into a strategic/tactical/operational hierarchy before acting. Use this when the command is vague or ambitious (e.g. 'optimize AUCTORUM', 'help me ship Kee v1', 'arregla e... |
| `goals` | 0 | `kee.tools.goals` | Query Armando's active goals from the vault (vault/config/goals.md). Use this for ANY question about pending work, deadlines, what's due this week, status of a project, etc. Do NOT read goals.md with the files tool — use... |
| `reflect` | 0 | `kee.tools.reflect` | Snapshot estructurado del estado reciente de Kee — combina QA score por surface, planes ejecutados vs pendientes, alucinaciones de kwargs, tools menos confiables (Wilson), y proyectos activos. Úsalo cuando Coco pregunte ... |
| `dispatch` | 1 | `kee.tools.dispatch` | Inspect or record dispatch_registry events (project-level work tracking). Use BEFORE asking the user 'what project?' (the answer is often in `active`), and AFTER significant project work to leave a breadcrumb future Kee ... |
| `quality_snapshot` | 0 | `kee.tools.quality_snapshot` | Inspect Kee's own recent reply quality (last 20 turns, in-memory). Returns avg score 0-1, 5-vs-prev-5 trend, per-surface breakdown, and the most recent issues per sample. Use BEFORE deciding whether to escalate to a heav... |
| `context` | 0 | `kee.tools.context` | Snapshot del estado ambiente AHORA: hora local, ventana activa, sesión de foco abierta, último dispatch, último commit, gasto LLM del día, próximos callbacks programados. (Opcional: clima y calendario.) Úsalo al INICIO d... |

### Productivity (6)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `work_session` | 1 | `kee.tools.work_session` | Persistent claude -p sessions per project. Cheaper than calling `claude_code` for each query because re-uses the existing conversation via `--continue`. Use when the user wants to iterate on the same project (auctorum-sy... |
| `focus` | 1 | `kee.tools.focus` | Declarar y seguir la sesión de foco actual. Coco dice 'voy a trabajar en AUCTORUM 90 min', tú llamas `focus start project=auctorum duration_min=90`. El heartbeat después monitorea si la ventana activa empata con `project... |
| `pomodoro` | 1 | `kee.tools.pomodoro` | Inicia un ciclo Pomodoro: abre `focus` por work_min minutos y programa dos callbacks vía `schedule_self` — uno al final del trabajo (recordar tomar break) y otro al final del break (sugerir retomar). Defaults: work_min=2... |
| `learn` | 1 | `kee.tools.learn` | Pin a durable knowledge nugget Kee should remember across sessions. Use cuando Coco te corrige ('NO uses Haiku para code review') o cuando descubres un dato útil ('siempre `D:/Kee/node-globals` para npm'). NO uses para c... |
| `projects` | 1 | `kee.tools.projects` | Lee y anota los notes de proyectos en `vault/projects/<slug>.md` (generados por `scripts/import_project_docs.py`). Úsalo cuando Coco pregunta '¿qué tiene auctorum-systems?' o cuando quieres dejar una nota fechada en un p... |
| `brief` | 0 | `kee.tools.brief` | Compose a markdown daily brief of Kee's state: commits, projects, QA score, planes, inbox totals, calendario próximo, alucinaciones. Pure read — no LLM, no network beyond what `inbox_triage` and `calendar` already requir... |

### Communication (6)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `notify` | 1 | `kee.tools.notify` | Send a desktop notification (Windows toast / Linux libnotify) to Armando. Use sparingly — only when (a) the user is likely AFK / in another app and (b) the information is time-sensitive enough that an inline chat reply c... |
| `emit` | 2 | `kee.tools.emit` | Empuja una notificación a Coco fuera de banda (Telegram + desktop, según el smart router). Úsalo cuando algo asíncrono termina y necesita llegar aunque Coco haya cambiado de surface (ej. 'deploy listo, URL: …'). NO uses ... |
| `email_send` | 3 | `kee.tools.email_send` | Send an email via Resend. **Risk 3 — externally visible, irreversible.** Only call when Armando explicitly told you to send THIS specific email; never auto-send drafts. Body can be plain text (default) or HTML. Requires ... |
| `gmail` | 0 | `kee.tools.gmail_tool` | Read Gmail inbox. Read-only — sending email goes through `email_send` (Resend). Actions:   - 'search': search threads by `query` (Gmail search syntax). Returns thread IDs + snippets.   - 'read_thread': fetch full content... |
| `whatsapp_send` | 3 | `kee.tools.whatsapp_send` | Send a WhatsApp text or template message via Meta Cloud API. **Risk 3 — externally visible.** Use only when Coco said 'manda WhatsApp a X' or similar IN THIS TURN. Never auto-send. Two modes:   - `text` (default): freefo... |
| `calendar` | 1 | `kee.tools.calendar_tool` | Query / write Google Calendar. Actions:   - 'upcoming': events in the next `hours` hours (default 24). Filter by `calendar_id` (default 'primary').   - 'today': all events today (local time).   - 'list_calendars': show a... |

### System Integrations (5)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `spotify` | 1 | `kee.tools.spotify` | Spotify control: now_playing, play, pause, next, previous, volume(level 0-100), search(query, kind), play_uri(uri). Token cached in data/spotify_token.json — must be auth'd once via `python -m kee.main spotify-auth`. |
| `home_assistant` | 0 | `kee.tools.home_assistant` | Talk to a Home Assistant instance — read sensor states, turn lights on/off, fire scripts and scenes. Configure once with `KEE_HASS_URL` + `KEE_HASS_TOKEN` (long-lived access token) in .env. Without those env vars, status... |
| `browser_control` | 1 | `kee.tools.browser_control` | Drive a real Chromium browser via Playwright. Use for: verifying a Vercel deploy actually serves content, scraping a page, filling a form, taking a screenshot of a dashboard, monitoring a CI run page. The browser is shar... |
| `wol` | 1 | `kee.tools.wol` | Send a Wake-on-LAN magic packet to a machine on the LAN. Useful for waking the Auctorum worker before kicking off a heavy task (reranker, ChromaDB, Gemma vision). Configure once with `KEE_WORKER_MAC=aa:bb:cc:dd:ee:ff`, t... |
| `market` | 0 | `kee.tools.market` | Crypto + stock + FX market data and price alerts. Read-only, no broker keys needed (CoinGecko + Yahoo Finance public APIs). Alerts evaluate against your watchlist at vault/config/watchlist.json and fire desktop+Telegram ... |

### Code & DevOps (5)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `claude_code` | 1 | `kee.tools.claude_code` | Delegate a coding task to Claude Code (Sonnet/Opus via Coco's Pro/Max subscription, headless). **If the user explicitly tells you to use claude_code, USE THIS TOOL — even if the task looks trivial. Do not substitute `fil... |
| `github` | 2 | `kee.tools.github` | Interact with GitHub via the `gh` CLI (assumes prior `gh auth login`). Use for: viewing repos, listing/creating PRs and issues, triggering workflows. **Anything that creates a PR, issue, or comment is externally visible ... |
| `vercel_deploy` | 2 | `kee.tools.vercel_deploy` | Deploy a directory to Vercel or query Vercel state. Wraps the `vercel` CLI; the user must have run `vercel login` once. **Production deploys are externally visible — only run with `production=True` when the user explicit... |
| `scaffold` | 1 | `kee.tools.scaffold` | Bootstrap a new project skeleton in D:/Kee/workspaces/<slug>/. Six templates: svelte, next, python_cli, python_api, landing, docs. Refuses to overwrite an existing directory. After scaffolding, you can hand off the works... |
| `commits` | 0 | `kee.tools.commits` | Aggregate git activity across Coco's project tree (D:/Kee, D:/projects, D:/Codigo, D:/auctorum-systems, D:/nahual, …). Use to answer '¿qué commiteé hoy?' / '¿qué proyectos toqué esta semana?' / '¿cuántos commits llevo en... |

### Self-Evolution (6)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `create_tool` | 2 | `kee.tools.create_tool` | Write a new Python tool, sandbox-test it, archive any prior version, and register it for immediate use. Use when no existing tool fits the task. Provide complete, working code for the `execute` body — not the wrapping cl... |
| `tool_reliability` | 0 | `kee.tools.tool_reliability` | Query per-tool historical success rates from audit_log. Use to see which tools have been failing (e.g. 'files: 5/10 last week, mostly NotADirectoryError'), to debug, and to inform routing decisions (low-reliability tools... |
| `apply_rewrite` | 2 | `kee.tools.apply_rewrite` | Aplica una propuesta de tool-rewrite (Sleep Cycle Phase 9) al source code del tool. SAFE: requiere `confirm=True`, exige que el .py esté git-clean, corre `python -m kee.main check` después y revierte automáticamente si f... |
| `schedule_self` | 1 | `kee.tools.schedule_self` | Programa un callback futuro para Kee. Coco dice 'recuérdame en X' y tú llamas `schedule_self start when_min=X message=…`. La fila queda en `scheduled_callbacks`; el heartbeat la dispara cuando `fire_at <= now()`. Accione... |
| `user_patterns` | 0 | `kee.tools.user_patterns` | Behavioural intelligence about Coco — peak activity hours, most-used tools, surface distribution (chat/voice/telegram), daily cost trend, and Sleep Cycle's accumulated axioms. Use this BEFORE deciding to interrupt the us... |
| `perf_stats` | 0 | `kee.tools.perf_stats` | Pipeline introspection over audit_log. Use to debug latency, cost, success rates by layer (LLM / tool / surface). Same data the dashboard renders, exposed as a tool so the agent can reason about its own performance. Acti... |

### Worker / Vision / RAG (3)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `worker_health` | 0 | `kee.tools.worker_health` | Probe el worker node (Auctorum) y devuelve estado de cada subsistema: chroma, ollama, reranker, vision, gpu, disk, load. Usa esto ANTES de decidir si vale la pena llamar memory_search (requiere chroma+reranker), o si la ... |
| `vision` | 1 | `kee.tools.vision` | Describe lo que aparece en una imagen usando el endpoint vision del worker (Auctorum). Útil cuando Coco te pasa un screenshot, o cuando combinas con `screen` para ver qué hay en pantalla y razonar sobre eso. Modelo backe... |
| `describe_screen` | 1 | `kee.tools.describe_screen` | Captura un screenshot y se lo manda al endpoint vision para que describa qué hay en pantalla. Útil cuando Coco pregunta '¿qué tengo abierto?' o '¿qué dice ese mensaje?' o necesita razonar sobre una UI sin describirla. Wr... |

### Observability (3)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `inbox_triage` | 0 | `kee.tools.inbox_triage` | Categoriza el inbox de Gmail (no leído) por heurística — sin LLM, sin costo. Buckets: urgent, billing, auth_codes, calendar, github, work, marketing, social, school, noreply, other. Útil cuando Coco pregunta '¿algo impor... |
| `economy` | 0 | `kee.tools.economy` | Query Kee's spend on paid tools. Right now `claude_code` calls are tracked (Coco's Pro/Max subscription is rate-limited so the raw cost lands here even though no per-call invoice is issued). Future paid integrations land... |
| `world_model` | 0 | `kee.tools.world` | Query the world model — the causal graph of Coco's projects, infrastructure, and external services. Use BEFORE risky actions to understand what they cascade into. Six actions:   - 'list': all entities (filter by `type`: ... |

### Uncategorized (1)

| Tool | Risk | Module | Purpose |
|---|:-:|---|---|
| `keecode` | 1 | `kee.tools.keecode` | Launch and control KeeCode, Kee's clean-room coding-agent surface backed by OpenCode and the current local Ollama model. Use this when the user wants a Claude-Code-like workflow without proprietary or leaked source: open... |

---

## 2. Python modules by subsystem

AST walk of `kee/**.py`. Each module shows its 1-line docstring (top of file) plus public classes and functions (names not starting with `_`).

### `kee/core/`  —  21 modules

#### `kee/core/agent.py`
> Kee agent core — ReAct loop with native Ollama tool calling.

**Classes:** `KeeAgent`
**Functions:** `salvage_tool_calls`

#### `kee/core/audit.py`
> Audit logger.

**Classes:** `AuditLogger`

#### `kee/core/db.py`
> SQLite layer.

**Functions:** `get_connection`, `cursor`, `close`

#### `kee/core/identity.py`
> Identity loader.

**Classes:** `IdentityLoader`

#### `kee/core/llm/base.py`
> LLM provider abstraction.

**Classes:** `ChatResponse`, `ProviderUnavailable`, `ProviderHardFail`, `LLMProvider`

#### `kee/core/llm/chain.py`
> LLMChain — orchestrates primary provider + fallbacks.

**Classes:** `LLMChain`
**Functions:** `build_default_chain`

#### `kee/core/llm/claude.py`
> Anthropic Claude provider.

**Classes:** `ClaudeProvider`, `ClaudeHaikuProvider`

#### `kee/core/llm/cost_tracker.py`
> Daily cost tracker + kill switch.

**Functions:** `daily_total_usd`, `kill_switch_active`, `status`, `by_provider_today`

#### `kee/core/llm/ollama_p.py`
> Ollama provider — local fallback. Wraps the existing OllamaClient

**Classes:** `OllamaProvider`

#### `kee/core/llm/ollama_remote.py`
> Remote Ollama provider — points at the Auctorum worker's Ollama

**Classes:** `OllamaRemoteProvider`

#### `kee/core/llm/openai_p.py`
> OpenAI provider — fallback after Claude.

**Classes:** `OpenAIProvider`

#### `kee/core/memory.py`
> Memory manager.

**Classes:** `ConversationState`, `MemoryManager`

#### `kee/core/ollama_client.py`
> Async wrapper around the Ollama API.

**Classes:** `OllamaUnavailable`, `ChatResponse`, `OllamaClient`

#### `kee/core/router.py`
> Router — classify each user turn into a tier and route to the right LLM.

**Classes:** `RouterDecision`, `Router`

#### `kee/core/scheduler.py`
> Central scheduler — concurrency control for Kee.

**Classes:** `Priority`, `PriorityLock`, `KeeScheduler`
**Functions:** `get_default`

#### `kee/core/services.py`
> Shared service registry — a tiny DI container.

**Functions:** `bind`

#### `kee/core/tool_gc.py`
> Tool garbage collector.

**Classes:** `ToolGarbageCollector`

#### `kee/core/tool_registry.py`
> Dynamic tool registry.

**Classes:** `ToolRegistry`

#### `kee/core/verify.py`
> Verification loop helpers.

**Functions:** `capture_state`, `verify`, `serialize_state`

#### `kee/core/voice_config.py`
> Persistent voice configuration.

**Classes:** `VoicePreferences`
**Functions:** `config_path`, `load`, `save`, `voice_file_for`, `installed_voices`

#### `kee/core/vram_arbiter.py`
> VRAM Arbiter — final guard against OOM on the 8GB RTX 5050.

**Classes:** `VRAMTenant`, `VRAMState`, `VRAMOvercommit`, `VRAMArbiter`
**Functions:** `get_default`

---

### `kee/tools/`  —  65 modules

#### `kee/tools/apply_rewrite.py`
> Tool: apply_rewrite — apply a tool-rewrite proposal from Sleep Cycle.

**Classes:** `ApplyRewriteTool`

#### `kee/tools/base.py`
> Base class for Kee tools.

**Classes:** `Tool`

#### `kee/tools/brief.py`
> Tool: brief — composable markdown brief of Kee's day so far.

**Classes:** `BriefTool`

#### `kee/tools/browser_control.py`
> Browser automation tool — Playwright on Chromium.

**Classes:** `BrowserControlTool`

#### `kee/tools/calendar_tool.py`
> Google Calendar tool — read upcoming events + create events.

**Classes:** `CalendarTool`

#### `kee/tools/claude_code.py`
> Claude Code orchestrator.

**Classes:** `ClaudeCodeTool`

#### `kee/tools/clipboard.py`
> Tool: clipboard — read + write the OS clipboard.

**Classes:** `ClipboardTool`

#### `kee/tools/commits.py`
> Tool: commits — git activity across Coco's project tree.

**Classes:** `CommitsTool`

#### `kee/tools/compare_days.py`
> Tool: compare_days — diff two days' counts, side by side.

**Classes:** `CompareDaysTool`

#### `kee/tools/context.py`
> Tool: context — "right now" ambient state in one call.

**Classes:** `ContextTool`

#### `kee/tools/create_tool.py`
> Meta-tool: Kee writes its own tools.

**Classes:** `CreateToolTool`

#### `kee/tools/describe_screen.py`
> Tool: describe_screen — screenshot + vision in one call.

**Classes:** `DescribeScreenTool`

#### `kee/tools/desktop_control.py`
> Tool: desktop_control — voice-driven control of the hologram window.

**Classes:** `DesktopControlTool`

#### `kee/tools/dispatch.py`
> Tool: dispatch — read + write the dispatch_registry.

**Classes:** `DispatchTool`

#### `kee/tools/economy.py`
> Tool: economy — query the Internal Economy ledger.

**Classes:** `EconomyTool`

#### `kee/tools/email_send.py`
> Email-send tool — uses Resend (https://resend.com) to ship outbound mail.

**Classes:** `EmailSendTool`

#### `kee/tools/emit.py`
> Tool: emit — push a notification to Coco through the smart router.

**Classes:** `EmitTool`

#### `kee/tools/episodic.py`
> Tool: episodic — semantic recall across EVERYTHING that happened.

**Classes:** `EpisodicTool`

#### `kee/tools/files.py`
> File I/O tool.

**Classes:** `FilesTool`

#### `kee/tools/focus.py`
> Tool: focus — declare + track current work focus.

**Classes:** `FocusTool`

#### `kee/tools/github.py`
> GitHub operations via the `gh` CLI.

**Classes:** `GitHubTool`

#### `kee/tools/gmail_tool.py`
> Gmail tool — read-only inbox triage (search + read threads).

**Classes:** `GmailTool`

#### `kee/tools/goals.py`
> Goals tool — typed access to vault/config/goals.md.

**Classes:** `GoalsTool`

#### `kee/tools/home_assistant.py`
> Tool: home_assistant — Home Assistant REST API wrapper.

**Classes:** `HomeAssistantTool`

#### `kee/tools/inbox_triage.py`
> Tool: inbox_triage — heuristic Gmail unread classification.

**Classes:** `InboxTriageTool`

#### `kee/tools/infer_goal.py`
> Tool: infer_goal — surface the Goal Inference Engine to the agent.

**Classes:** `InferGoalTool`

#### `kee/tools/keecode.py`
> Tool: keecode - Kee's OpenCode-backed coding-agent bridge.

**Classes:** `KeeCodeTool`

#### `kee/tools/learn.py`
> Tool: learn — durable knowledge nuggets the agent explicitly remembers.

**Classes:** `LearnTool`

#### `kee/tools/market.py`
> Tool: market — crypto + stock prices and alerts.

**Classes:** `MarketTool`
**Functions:** `watchlist_path`, `load_watchlist`, `save_watchlist`, `quote`, `history`, `check_alerts`

#### `kee/tools/memory_tool.py`
> Memory tool — semantic search over the vault.

**Classes:** `MemorySearchTool`

#### `kee/tools/narrate_day.py`
> Tool: narrate_day — chronological narrative of a specific day.

**Classes:** `NarrateDayTool`

#### `kee/tools/news.py`
> Tool: news — top news headlines via DuckDuckGo News.

**Classes:** `NewsTool`

#### `kee/tools/notes.py`
> Tool: notes — read/search/create notes in the Obsidian vault.

**Classes:** `NotesTool`
**Functions:** `notes_dir`

#### `kee/tools/notify.py`
> Tool: notify — desktop toast notifications (Kee → Coco).

**Classes:** `NotifyTool`

#### `kee/tools/open_app.py`
> App orchestrator — open and focus desktop applications.

**Classes:** `OpenAppTool`

#### `kee/tools/perf_stats.py`
> Tool: perf_stats — pipeline introspection over audit_log.

**Classes:** `PerfStatsTool`

#### `kee/tools/planner.py`
> Tool: plan — surface MultiPathPlanner to the agent + persist history.

**Classes:** `PlanTool`

#### `kee/tools/pomodoro.py`
> Tool: pomodoro — focus session + scheduled break callback.

**Classes:** `PomodoroTool`

#### `kee/tools/projects.py`
> Tool: projects — read + annotate Coco's project notes in vault/projects/.

**Classes:** `ProjectsTool`

#### `kee/tools/quality_snapshot.py`
> Tool: quality_snapshot — agent-introspection over its own response quality.

**Classes:** `QualitySnapshotTool`

#### `kee/tools/recall.py`
> Tool: recall — search Kee's own past conversations.

**Classes:** `RecallTool`

#### `kee/tools/recap_week.py`
> Tool: recap_week — 7-day aggregated narrative.

**Classes:** `RecapWeekTool`

#### `kee/tools/reflect.py`
> Tool: reflect — agent-callable mid-day reflection.

**Classes:** `ReflectTool`

#### `kee/tools/research.py`
> Tool: research — one-shot search + scrape top result.

**Classes:** `ResearchTool`

#### `kee/tools/scaffold.py`
> Tool: scaffold — bootstrap project skeletons in `D:/Kee/workspaces/<slug>/`.

**Classes:** `ScaffoldTool`

#### `kee/tools/schedule_self.py`
> Tool: schedule_self — lightweight future-time callbacks.

**Classes:** `ScheduleSelfTool`
**Functions:** `fire_due_callbacks`

#### `kee/tools/screen.py`
> Computer Use stub — screen + cursor I/O without vision LLM.

**Classes:** `ScreenTool`

#### `kee/tools/shell.py`
> Shell execution tool — cross-platform.

**Classes:** `ShellTool`

#### `kee/tools/smart_search.py`
> Tool: smart_search — unified search across Kee's data surfaces.

**Classes:** `SmartSearchTool`

#### `kee/tools/spotify.py`
> Spotify tool — currently-playing awareness + control.

**Classes:** `SpotifyTool`

#### `kee/tools/system.py`
> System status tool.

**Classes:** `SystemStatusTool`

#### `kee/tools/system_control.py`
> Tool: system_control — volume + brightness + sleep on Windows.

**Classes:** `SystemControlTool`

#### `kee/tools/tool_reliability.py`
> Tool: tool_reliability — query historical tool success rates.

**Classes:** `ToolReliabilityTool`

#### `kee/tools/user_patterns.py`
> Tool: user_patterns — query Sleep Cycle's behavioural model.

**Classes:** `UserPatternsTool`

#### `kee/tools/vault_search.py`
> Tool: vault_search — substring search across the whole vault.

**Classes:** `VaultSearchTool`

#### `kee/tools/vercel_deploy.py`
> Vercel deployment + project management tool.

**Classes:** `VercelDeployTool`

#### `kee/tools/vision.py`
> Tool: vision — describe images via the Auctorum vision endpoint.

**Classes:** `VisionTool`

#### `kee/tools/weather.py`
> Tool: weather — current + forecast via Open-Meteo (free, no API key).

**Classes:** `WeatherTool`

#### `kee/tools/web.py`
> Web tool — search and fetch.

**Classes:** `WebSearchTool`, `FetchUrlTool`

#### `kee/tools/whatsapp_send.py`
> WhatsApp send tool — outbound via Meta Cloud API.

**Classes:** `WhatsAppSendTool`

#### `kee/tools/windows.py`
> Tool: windows — enumerate running applications and the foreground window.

**Classes:** `WindowsTool`

#### `kee/tools/wol.py`
> Tool: wol — Wake-on-LAN packet sender.

**Classes:** `WakeOnLanTool`

#### `kee/tools/work_session.py`
> Tool: work_session — persistent `claude -p` subprocess per project.

**Classes:** `WorkSessionTool`

#### `kee/tools/worker_health.py`
> Tool: worker_health — probe the Auctorum worker stack.

**Classes:** `WorkerHealthTool`

#### `kee/tools/world.py`
> Tool: world_model — query the causal graph + run impact assessment.

**Classes:** `WorldModelTool`

---

### `kee/cognition/`  —  18 modules

#### `kee/cognition/autonomy.py`
> Dynamic Autonomy Threshold — v2 §III Gap 11.

**Functions:** `wilson_lower_bound`, `record`, `confidence`, `recommended_threshold`, `summary`

#### `kee/cognition/backup.py`
> Backup story — daily snapshots of the things that hurt to lose.

**Functions:** `backup_sqlite`, `backup_vault`, `backup_worker_chroma`, `rotate`, `run_backups`

#### `kee/cognition/conversation_monitor.py`
> Rolling conversation quality monitor — Jarvis-pattern, zero LLM.

**Classes:** `ConversationMonitor`
**Functions:** `observe`, `snapshot`

#### `kee/cognition/dispatch_registry.py`
> Dispatch registry — cross-session project + task awareness.

**Functions:** `ensure_schema`, `record_dispatch`, `log_task`, `active_projects`, `recent_dispatches`, `project_task_summary`, `format_for_prompt`

#### `kee/cognition/economy.py`
> Internal Economy — per-call cost tracking.

**Classes:** `CostEntry`
**Functions:** `record`, `from_claude_code_result`, `summary`, `recent`

#### `kee/cognition/episodic_indexer.py`
> Episodic memory — semantic recall over EVERYTHING that happened.

**Classes:** `EpisodicIndexer`

#### `kee/cognition/goal_inference.py`
> Goal Inference Engine — v2 §III Gap 1.

**Classes:** `InferredGoal`, `GoalInferenceEngine`

#### `kee/cognition/plan_commit_linker.py`
> Plan ↔ commit linker — proposes plan executions from real git activity.

**Functions:** `propose_plan_links`

#### `kee/cognition/planner.py`
> Multi-step Planner — generate, score, pick the best execution plan.

**Classes:** `Plan`, `MultiPathPlanner`

#### `kee/cognition/response_qa.py`
> Post-response quality checker — Jarvis-pattern QA without the LLM cost.

**Classes:** `QAVerdict`
**Functions:** `check`, `summary_for_retry`

#### `kee/cognition/self_evolution.py`
> Self-editing codebase daemon.

**Functions:** `proposals_dir`, `analyze_recent_runtime`, `draft_proposal`, `apply_via_claude_code`, `list_proposals`, `read_proposal`

#### `kee/cognition/self_healing.py`
> Self-healing infrastructure — v2 §IV Extension 7.

**Classes:** `RecoveryReport`, `SelfHealing`

#### `kee/cognition/sleep_cycle.py`
> Sleep Cycle daemon — Kee's REM phase.

**Classes:** `SleepReport`, `SleepCycleDaemon`

#### `kee/cognition/temporal.py`
> Temporal intelligence — when to act, when to wait, when to interrupt.

**Classes:** `Mode`, `TemporalIntelligence`

#### `kee/cognition/tool_evolution.py`
> Tool description rewrite proposals — closes the kwarg-hallucination loop.

**Functions:** `draft_rewrite_proposals`

#### `kee/cognition/tool_rewrite_apply.py`
> Apply a tool-rewrite proposal — explicit confirm required, auto-revert on fail.

**Functions:** `parse_proposal`, `find_tool_source`, `replace_description`, `apply_proposal`

#### `kee/cognition/worker_reindex.py`
> Worker reindex — Sleep Cycle Phase 12.

**Functions:** `maybe_reindex`

#### `kee/cognition/world_model.py`
> World Model — causal knowledge graph in SQLite.

**Classes:** `Entity`, `Edge`
**Functions:** `upsert_entity`, `upsert_relation`, `remove_entity`, `remove_relation`, `entity`, `list_entities`, `downstream`, `upstream`, `impact_score`, `seed_default_world`

---

### `kee/perception/`  —  12 modules

#### `kee/perception/ambient_sound.py`
> Ambient sound event detector.

**Classes:** `AmbientSoundDetector`
**Functions:** `ensure_schema`, `log_event`, `recent_events`

#### `kee/perception/biometric.py`
> Biometric telemetry ingest + analysis.

**Functions:** `ensure_schema`, `insert`, `insert_many`, `recent`, `latest_by_kind`, `score_recent_state`

#### `kee/perception/filesystem.py`
> Vault file watcher.

**Classes:** `VaultWatcher`

#### `kee/perception/goals.py`
> Parse `vault/config/goals.md` into structured records.

**Classes:** `Goal`
**Functions:** `parse_goals`, `load_goals`, `upcoming_deadlines`

#### `kee/perception/heartbeat.py`
> Heartbeat daemon — Kee's autonomic nervous system.

**Classes:** `Actionable`, `HeartbeatSnapshot`, `HeartbeatDaemon`

#### `kee/perception/notif_bridge_windows.py`
> Windows UserNotificationListener bridge.

**Functions:** `run`

#### `kee/perception/notification_router.py`
> Smart routing for outbound notifications.

**Classes:** `RoutingDecision`
**Functions:** `decide`, `notify_smart`

#### `kee/perception/notifications.py`
> Cross-platform notification delivery — v2 §VI Phase 3.

**Functions:** `send_notification`, `notify_user`, `record_notification`

#### `kee/perception/speaker_id.py`
> Speaker recognition (lite).

**Classes:** `VoicePrint`
**Functions:** `features_from_audio`, `print_path`, `load_print`, `save_print`, `enroll`, `match`

#### `kee/perception/tts_elevenlabs.py`
> ElevenLabs TTS provider — optional fallback for higher quality voice.

**Functions:** `is_configured`, `selected_provider`, `synthesize`, `play_mp3`

#### `kee/perception/voice.py`
> Voice pipeline — Phase 2.

**Classes:** `VoiceConfig`, `VoicePipeline`

#### `kee/perception/window.py`
> Cross-platform active-window inspection.

**Functions:** `get_active_window`

---

### `kee/surfaces/`  —  3 modules

#### `kee/surfaces/api.py`
> FastAPI backend — the substrate every Kee UI consumes.

**Classes:** `GoalsBody`, `VaultWriteBody`, `VoiceConfigBody`, `VoiceInstallBody`, `VoiceSpeakBody`, `VoiceStreamBody`, `SpeakerEnrollBody`, `ChatRequest`, `ChatResponse`, `SettingsUpdate`, `KeeCodeContextBody`, `KeeCodeLaunchBody`, `ToolExecBody`, `RouterConfigPatch`, `InboundNotification`
**Functions:** `health`, `tools_index`, `audit`, `anomalies`, `heartbeat_recent`, `conversations_list`, `conversation_detail`, `goals_index`, `world_entities`, `goals_raw`, `goals_raw_put`, `vault_list`, `vault_write`, `vault_read`, `spotify_now_playing`, `voice_state`, `voice_config_get`, `voice_config_set`, `voice_voices`, `voice_catalog`, `voice_install`, `voice_uninstall`, `voice_speak`, `voice_stream`, `quality_snapshot`, `quality_lifetime`, `voice_last_event`, `voice_ambient`, `voice_speaker_state`, `voice_speaker_enroll`, `cycle_state`, `identity_history`, `identity_diff`, `cycle_proposals`, `plans_recent`, `plans_mark_executed`, `cycle_pending`, `cycle_tool_rewrites`, `cycle_tool_rewrite_get`, `cycle_proposals_apply`, `cycle_run`, `world_relations`, `world_impact`, `economy_summary`, `economy_recent`, `autonomy_summary`, `digest_today`, `brief_endpoint`, `digest_snapshot`, `proposals_index`, `proposal_detail`, `chat`, `chat_stream_endpoint`, `chat_reset`, `chat_active`, `chat_attach`, `chat_attachments`, `chat_attachment_delete`, `memory_recent_summaries`, `memory_summarize_stale`, `llm_providers`, `llm_cost`, `llm_recent`, `router_config`, `keecode_status`, `keecode_context`, `keecode_launch`, `update_settings`, `agent_rebuild`, `system_hallucinations`, `system_daemons`, `self_evolution_proposals`, `self_evolution_proposal`, `self_evolution_draft`, `self_evolution_apply`, `desktop_signal`, `biometric_sample`, `biometric_recent`, `biometric_state`, `edge_ask`, `fleet_state`, `episodic_query`, `episodic_reindex`, `narrate_day_endpoint`, `system_version`, `worker_dashboard`, `worker_reindex_now`, `system_supervisor`, `system_logs`, `tools_execute`, `tools_source`, `llm_test_provider`, `router_config_put`, `notifications_inbound`, `notifications_list`, `notifications_unread_count`, `notifications_mark_handled`, `notifications_handle_all`, `memory_summarize_one`, `stream`

#### `kee/surfaces/telegram.py`
> Telegram bot surface — chat with Kee from your phone.

**Functions:** `run`

#### `kee/surfaces/terminal.py`
> Terminal surface — Rich-based interactive REPL.

**Functions:** `run`

---

### `kee/distributed/`  —  8 modules

#### `kee/distributed/chroma_client.py`
> Tiny ChromaDB v2 HTTP client.

**Classes:** `ChromaUnavailable`, `ChromaClient`

#### `kee/distributed/embedder.py`
> Embedding service.

**Classes:** `EmbedderUnavailable`, `Embedder`

#### `kee/distributed/fleet.py`
> Multi-node fleet manager.

**Classes:** `FleetNode`
**Functions:** `fleet_config_path`, `load_fleet`, `save_fleet`, `probe_node`, `probe_fleet`, `find_node`

#### `kee/distributed/google_oauth.py`
> Google OAuth token manager.

**Functions:** `get_credentials`, `status`

#### `kee/distributed/indexer.py`
> Vault indexer.

**Classes:** `Chunk`, `VaultIndexer`

#### `kee/distributed/piper_catalog.py`
> Piper voice catalog & installer.

**Classes:** `CatalogVoice`
**Functions:** `find`, `voice_dir`, `is_installed`, `install`, `install_many`, `remove`

#### `kee/distributed/reranker.py`
> Reranker — Phase 3 surgical-RAG precision filter.

**Classes:** `Reranker`

#### `kee/distributed/spotify_oauth.py`
> Spotify OAuth — Authorization Code with PKCE.

**Functions:** `run_oauth`

---

### `kee/daemon/`  —  3 modules

#### `kee/daemon/autostart.py`
> Register Kee to start automatically.

**Functions:** `install_windows_autostart`, `uninstall_windows_autostart`

#### `kee/daemon/supervisor.py`
> Multi-surface supervisor.

**Classes:** `SurfaceSpec`, `SurfaceState`, `Supervisor`
**Functions:** `run_supervisor`, `read_state`

#### `kee/daemon/tray.py`
> System-tray icon for Kee.

**Functions:** `run_tray`

---

### `kee/desktop/`  —  1 modules

#### `kee/desktop/app.py`
> Kee desktop window — pywebview shell over the SvelteKit dashboard.

**Classes:** `DesktopBridge`, `DesktopApp`
**Functions:** `signal_path`, `write_signal`, `read_and_clear_signal`, `run_desktop`

---

### `kee/(top)/`  —  2 modules

#### `kee/config.py`
> Runtime configuration for Kee.

**Classes:** `Settings`
**Functions:** `setup_logging`

#### `kee/main.py`
> Kee entry point.

**Functions:** `run`

---

## 3. HTTP API endpoints

All routes registered on the FastAPI app in `kee/surfaces/api.py`. The dashboard, voice surface, telegram bot, browser extension, mobile edge node and the Termux client all consume this same surface.

| Verb | Path |
|---|---|
| `GET` | `/health` |
| `GET` | `/tools` |
| `GET` | `/audit` |
| `GET` | `/anomalies` |
| `GET` | `/heartbeat/recent` |
| `GET` | `/conversations` |
| `GET` | `/conversations/{conversation_id}` |
| `GET` | `/goals` |
| `GET` | `/world-model/entities` |
| `GET` | `/goals/raw` |
| `PUT` | `/goals/raw` |
| `GET` | `/vault/list` |
| `PUT` | `/vault/write` |
| `GET` | `/vault/read` |
| `GET` | `/spotify/now_playing` |
| `GET` | `/voice/state` |
| `GET` | `/voice/config` |
| `POST` | `/voice/config` |
| `GET` | `/voice/voices` |
| `GET` | `/voice/catalog` |
| `POST` | `/voice/install` |
| `POST` | `/voice/voices/{stem}/uninstall` |
| `POST` | `/voice/speak` |
| `POST` | `/voice/stream` |
| `GET` | `/quality/snapshot` |
| `GET` | `/quality/lifetime` |
| `GET` | `/voice/last_event` |
| `GET` | `/voice/ambient` |
| `GET` | `/voice/speaker` |
| `POST` | `/voice/speaker/enroll` |
| `GET` | `/cycle/state` |
| `GET` | `/identity/history` |
| `GET` | `/identity/diff/{sha}` |
| `GET` | `/cycle/proposals` |
| `GET` | `/plans/recent` |
| `POST` | `/plans/{plan_id}/mark-executed` |
| `GET` | `/cycle/pending` |
| `GET` | `/cycle/tool-rewrites` |
| `GET` | `/cycle/tool-rewrites/{date}/{tool}` |
| `POST` | `/cycle/proposals/{proposal_date}/apply` |
| `POST` | `/cycle/run` |
| `GET` | `/world-model/relations` |
| `GET` | `/world-model/impact/{entity_id}` |
| `GET` | `/economy/summary` |
| `GET` | `/economy/recent` |
| `GET` | `/autonomy/summary` |
| `GET` | `/digest/today` |
| `GET` | `/brief` |
| `GET` | `/digest/snapshot` |
| `GET` | `/proposals` |
| `GET` | `/proposals/{date}` |
| `POST` | `/chat` |
| `POST` | `/chat/stream` |
| `POST` | `/chat/{session_id}/reset` |
| `GET` | `/chat/{session_id}/active` |
| `POST` | `/chat/{session_id}/attach` |
| `GET` | `/chat/{session_id}/attachments` |
| `DELETE` | `/chat/{session_id}/attachments/{filename}` |
| `GET` | `/memory/recent_summaries` |
| `POST` | `/memory/summarize_stale` |
| `GET` | `/llm/providers` |
| `GET` | `/llm/cost` |
| `GET` | `/llm/recent` |
| `GET` | `/router/config` |
| `GET` | `/keecode/status` |
| `POST` | `/keecode/context` |
| `POST` | `/keecode/launch` |
| `POST` | `/settings` |
| `POST` | `/agent/rebuild` |
| `GET` | `/system/hallucinations` |
| `GET` | `/system/daemons` |
| `GET` | `/self_evolution/proposals` |
| `GET` | `/self_evolution/proposals/{proposal_date}` |
| `POST` | `/self_evolution/draft` |
| `POST` | `/self_evolution/proposals/{proposal_date}/apply` |
| `POST` | `/desktop/signal` |
| `POST` | `/biometric/sample` |
| `GET` | `/biometric/recent` |
| `GET` | `/biometric/state` |
| `POST` | `/edge/ask` |
| `GET` | `/fleet` |
| `GET` | `/episodic/query` |
| `POST` | `/episodic/reindex` |
| `GET` | `/narrate/{day}` |
| `GET` | `/system/version` |
| `GET` | `/worker/dashboard` |
| `POST` | `/worker/reindex` |
| `GET` | `/system/supervisor` |
| `GET` | `/system/logs/{name}` |
| `POST` | `/tools/{name}/execute` |
| `GET` | `/tools/{name}/source` |
| `POST` | `/llm/test_provider/{name}` |
| `PUT` | `/router/config` |
| `POST` | `/notifications/inbound` |
| `GET` | `/notifications` |
| `GET` | `/notifications/unread_count` |
| `POST` | `/notifications/{nid}/handled` |
| `POST` | `/notifications/handle_all` |
| `POST` | `/memory/summarize/{conversation_id}` |
| `WEBSOCKET` | `/stream` |

Plus:

- WebSocket `/stream` — push channel for cross-process audit events (consumed by the dashboard's NeuralCanvas + voice HUD).
- Static mount `/app/*` — pre-built SvelteKit dashboard served from `dashboard/build/`.

---

## 4. SQLite schema

Tables in `data/kee.db` — Kee's single source of persistent truth. Schema is forward-only: only `_ADDITIVE_MIGRATIONS` in `kee/core/db.py` may add columns; never rename or drop.

| Table | Columns |
|---|:-:|
| `ambient_events` | 8 |
| `anomalies` | 7 |
| `audit_log` | 19 |
| `biometric_samples` | 7 |
| `confidence_log` | 6 |
| `conversations` | 6 |
| `cost_ledger` | 10 |
| `dispatches` | 6 |
| `focus_sessions` | 8 |
| `goals` | 10 |
| `learnings` | 8 |
| `messages` | 7 |
| `notifications` | 9 |
| `plan_history` | 11 |
| `scheduled_callbacks` | 8 |
| `task_ledger` | 9 |
| `task_log` | 7 |
| `tool_registry` | 10 |
| `world_entities` | 7 |
| `world_relations` | 7 |

---

## 5. Dashboard pages + components

SvelteKit 2 + Svelte 5 runes + Tailwind 4. Built into `dashboard/build/` and served at `/app/*`. Every page is reactive against the WebSocket `/stream`.

### Pages (18)

- `/`  →  http://localhost:7330/app
- `/audit`  →  http://localhost:7330/app/audit
- `/conversations`  →  http://localhost:7330/app/conversations
- `/cost`  →  http://localhost:7330/app/cost
- `/cycle`  →  http://localhost:7330/app/cycle
- `/diary`  →  http://localhost:7330/app/diary
- `/episodic`  →  http://localhost:7330/app/episodic
- `/goals`  →  http://localhost:7330/app/goals
- `/health`  →  http://localhost:7330/app/health
- `/hud`  →  http://localhost:7330/app/hud
- `/nervous-system`  →  http://localhost:7330/app/nervous-system
- `/notifications`  →  http://localhost:7330/app/notifications
- `/settings`  →  http://localhost:7330/app/settings
- `/tools`  →  http://localhost:7330/app/tools
- `/vault`  →  http://localhost:7330/app/vault
- `/voice`  →  http://localhost:7330/app/voice
- `/worker`  →  http://localhost:7330/app/worker
- `/world`  →  http://localhost:7330/app/world

### Reusable components (8)

- `Brand`
- `Glass`
- `JarvisOrb`
- `NeuralCanvas`
- `PulseDot`
- `Sparkline`
- `Stat`
- `Toast`

---

## 6. Tests

All 37 suites under `tests/`. The runner is `tests/run_all.py`. **No paid LLM is allowed** — `_FakeLLM` stubs cover every place `llm.chat()` is called. The whole suite runs offline in ~30s.

- `tests/test_apply_rewrite.py`
- `tests/test_audio_routing.py`
- `tests/test_backup.py`
- `tests/test_chain_ordering.py`
- `tests/test_cognitive_heartbeat.py`
- `tests/test_commits.py`
- `tests/test_compare_days.py`
- `tests/test_cost_tracker.py`
- `tests/test_episodic_indexer.py`
- `tests/test_focus.py`
- `tests/test_hallucination_loop.py`
- `tests/test_inbox_triage.py`
- `tests/test_keecode.py`
- `tests/test_learn.py`
- `tests/test_missing_required.py`
- `tests/test_narrate_day.py`
- `tests/test_notification_router.py`
- `tests/test_plan_history.py`
- `tests/test_plan_linker.py`
- `tests/test_projects.py`
- `tests/test_quality_snapshot.py`
- `tests/test_real_rag.py`
- `tests/test_recall.py`
- `tests/test_recap_week.py`
- `tests/test_reflect.py`
- `tests/test_response_qa.py`
- `tests/test_router_parser.py`
- `tests/test_schedule_self.py`
- `tests/test_self_correction.py`
- `tests/test_strip_helpers.py`
- `tests/test_terminal_helpers.py`
- `tests/test_tool_evolution.py`
- `tests/test_tool_schemas.py`
- `tests/test_user_patterns.py`
- `tests/test_voice_streaming.py`
- `tests/test_wilson.py`
- `tests/test_worker_health.py`

---

## 7. Scripts

Provisioning + maintenance helpers under `scripts/`. `scripts/auctorum/` ships everything needed to bring up the worker on a fresh Ubuntu 24.04 box.

- `scripts/auctorum/health_server.py`
- `scripts/auctorum/logrotate.conf`
- `scripts/auctorum/provision.sh`
- `scripts/auctorum/reranker_server.py`
- `scripts/auctorum/syncthing_pair.md`
- `scripts/auctorum/vision_server.py`
- `scripts/auctorum/warm_cron.sh`
- `scripts/build_inventory_md.py`
- `scripts/import_chat_exports.py`
- `scripts/import_keys.py`
- `scripts/import_project_docs.py`
- `scripts/record_wake_word.py`
- `scripts/release_public.sh`
- `scripts/setup_chromadb_local.py`
- `scripts/setup_obsidian.py`
- `scripts/setup_personal.py`
- `scripts/start-kee-chat.ps1`
- `scripts/train_wake_word.md`
- `scripts/train_wake_word_colab.md`
- `scripts/wsl_train_kee.sh`

---

## 8. Documentation

- `docs/01-architecture-blueprint.md`
- `docs/02-jarvis-addendum.md`
- `docs/03-technical-roadmap-v2.md`
- `docs/04-architecture-complete.md`
- `docs/05-full-inventory.md`
- `docs/termux_setup.md`

Plus repo-root: `README.md`, `STATUS.md`, `CLAUDE.md`.

---

## 9. Vault layout

The vault is a real Obsidian vault, gitignored except for templates. It's where Kee writes day narratives, weekly recaps, tool-rewrite proposals, and where the user keeps notes/projects/identity.

```
vault/
├── .obsidian/                  (Obsidian config — committed)
├── README.md                   (committed)
├── config/
│   ├── identity.md             (gitignored — who Kee is)
│   ├── soul.md                 (gitignored — Kee's voice)
│   ├── user.md                 (gitignored — about Coco)
│   ├── goals.md                (gitignored — active goals)
│   ├── router.md               (committed — 5-tier rules)
│   └── *.template.md           (committed — seed templates)
├── notes/                      (gitignored — Coco's notes)
├── projects/                   (gitignored — per-project markdown)
└── _kee/                       (gitignored — Kee's own writes)
    ├── daily/<date>.md         (narrate_day output)
    ├── daily/<date>-week.md    (recap_week output)
    ├── tools/                  (create_tool output, archived versions)
    ├── tool_rewrites/          (Sleep Cycle Phase 9 proposals)
    └── digests/                (nightly digest grounding)
```

---

_End of inventory. To regenerate: `.venv\Scripts\python.exe scripts\build_inventory_md.py`._
