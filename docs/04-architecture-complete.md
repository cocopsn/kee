# Kee — Architecture Complete

> **Documento canónico** — describe Kee tal como existe el 2026-05-04 y
> hacia dónde va. Si te encuentras leyendo esto meses después y todo se
> siente raro, empieza por `STATUS.md` (snapshot público) y
> `STATUS.local.md` (bitácora personal). El roadmap de fondo vive en
> `docs/03-technical-roadmap-v2.md`.

---

## 1. TL;DR

Kee es un agente personal soberano, voice-first y multi-surface,
construido para correr 24/7 en la laptop de Coco (Alienware m18 con RTX
5050 8GB) con un nodo worker en Ubuntu (Auctorum, GTX 1070) sincronizado
por Tailscale. Habla, escucha, percibe la pantalla, ejecuta tools sobre
el sistema, recuerda todo en SQLite + ChromaDB, se evalúa a sí mismo
cada noche en un Sleep Cycle de 14 fases y propone parches a su propio
código que Coco revisa y aplica. El objetivo no es un asistente: es un
proceso residente que envejece con su dueño.

---

## 2. Filosofía y diseño

### 2.1 Soberanía total para la operación core

Kee debe seguir siendo útil sin internet, sin Anthropic, sin OpenAI.
El default es Ollama local (`qwen3:8b` en Alienware, `qwen3.5:9b` en
Auctorum). Los providers de pago son **opt-in y ordenables** — no
obligatorios. Cuando se cae internet o el cap de costo dispara el
kill switch, Kee sigue conversando y ejecutando tools.

### 2.2 Voice-first, multi-surface

El usuario espera hablarle desde audífonos mientras maneja, escribirle
desde Telegram en el camión, mirar la dashboard en el monitor grande
y verlo aparecer en el HUD pywebview cuando dice "Kee". Cada surface
es un **proceso independiente** orquestado por un supervisor — si la
voz revienta no se cae la API.

### 2.3 Self-evolving sin self-deploying

Kee escribe propuestas: rewrites de descripciones de tools, parches
al código, nuevas tools probationary. **Nada se aplica sin revisión
humana excepto correcciones triviales con rollback automático**
(`apply_rewrite` corre `kee.main check` post-aplicar y revierte si
falla). El daemon `tool_evolution` propone cuando una tool acumula
≥3 hallucinations en 7 días. `plan_commit_linker` cierra ciclos
solo cuando el commit subject solapa tokens con la tarea.

### 2.4 Disciplina de costo

`KEE_DAILY_COST_CAP_USD=2` por default. El `cost_tracker` mide cada
call paga, y al llegar al cap fuerza Ollama hasta medianoche. La
suite de regresión (35 tests) corre **$0** — `_FakeLLM` stubs
reemplazan cualquier `llm.chat()`. Ningún test paga.

### 2.5 Soberanía + safety = dos bucles encadenados

La soberanía sin safety es vandalismo accidental. Cada write o exec
tool pasa por el **verification loop** (`kee/core/verify.py`): captura
estado pre, ejecuta, captura estado post, detecta anomalías. Las
anomalías se persisten en `anomalies` y disparan rollback flags.
`create_tool` corre cada nueva tool en un subprocess sandbox antes
de aceptarla. `tool_gc` archiva probationary tools no usadas en 7 días.

---

## 3. Arquitectura general

```
                          ┌────────────────────────────────────────┐
                          │   SUPERVISOR  (kee/daemon/supervisor)  │
                          │   PID 1 — backoff, autostart, ollama   │
                          └──┬──────┬──────┬──────┬──────┬──────┬──┘
                             │      │      │      │      │      │
        ┌────────────────────┘      │      │      │      │      └────────┐
        │       ┌───────────────────┘      │      │      └─────────┐     │
        v       v                          v      v                v     v
   ┌────────┐ ┌──────────┐ ┌─────────┐ ┌───────┐ ┌──────────────┐ ┌──────────┐
   │  api   │ │ telegram │ │ notif-  │ │ voice │ │  heartbeat   │ │ desktop  │
   │ (FAPI) │ │ (aiogram)│ │ bridge  │ │  STT/ │ │  (5min loop) │ │  (HUD)   │
   │        │ │          │ │ (winrt) │ │  TTS  │ │              │ │ pywebview│
   └────┬───┘ └────┬─────┘ └────┬────┘ └───┬───┘ └──────┬───────┘ └────┬─────┘
        │          │            │          │            │              │
        └──────────┴────────────┴────┬─────┴────────────┴──────────────┘
                                     v
                          ┌─────────────────────┐
                          │     KeeAgent        │  reasoning loop
                          │  kee/core/agent.py  │  + verification
                          └──────────┬──────────┘
                                     │
                ┌────────────────────┼────────────────────┐
                v                    v                    v
        ┌──────────────┐    ┌─────────────────┐    ┌──────────────┐
        │ Tool Registry│    │     Router      │    │   Memory     │
        │  (64 tools)  │───▶│ vault/config/   │───▶│  SQLite +    │
        │              │    │   router.md     │    │  ChromaDB    │
        └──────┬───────┘    │ 5-tier classify │    └──────────────┘
               │            └────────┬────────┘
               │                     │
               v                     v
        ┌──────────────┐    ┌─────────────────────────────────────┐
        │  verify.py   │    │   LLM Chain (kee/core/llm/chain)    │
        │ pre/post     │    │  Claude Sonnet → Haiku → GPT-4o-mini│
        │ snapshot     │    │  → Ollama-local → Ollama-remote     │
        └──────┬───────┘    └────────┬────────────────────────────┘
               │                     │
               v                     v
        ┌──────────────┐    ┌─────────────────┐
        │  audit_log   │◀───│  cost_tracker   │
        │  + anomalies │    │  $2/day kill sw │
        └──────────────┘    └─────────────────┘
                  │
                  │  (poll 1s)
                  v
        ┌──────────────────┐    WS /stream    ┌──────────────────┐
        │  _audit_tailer   │ ────────────────▶│  Dashboard 16pp  │
        │  (in api proc)   │                  │  + Voice HUD     │
        └──────────────────┘                  │  + Telegram echo │
                                              └──────────────────┘
```

### 3.1 SQLite — single source of truth

Todo el estado durable de Kee vive en `data/kee.db` (WAL,
foreign_keys=ON, busy_timeout=5s). Tablas:

| Tabla | Para qué |
|---|---|
| `conversations` | id, source, started_at, summary, token_count |
| `messages` | role, content, tool_name, tool_result, created_at |
| `audit_log` | acción, tool, params, result, success, **pre_state**, **post_state**, **verification** |
| `notifications` | inbound + outbound, urgency, handled |
| `anomalies` | verification fails, rollback events, sandbox fails |
| `task_ledger` | tareas asíncronas legacy |
| `tool_registry` | duplica el registro en memoria, sobrevive restarts |
| `goals` | con deadline, milestones JSON, progress_pct |
| `world_entities` + `world_relations` | grafo causal del world model |
| `cost_ledger` | provider, model, tokens, cost_usd por call |
| `confidence_log` | puntuación de Wilson por tool |
| `plan_history` | propuestas del planner, executed_at vinculado a commit |
| `focus_sessions` | pomodoros + work sessions |
| `scheduled_callbacks` | "recordame X mañana 10am" |
| `learnings` | axiomas extraídos por Sleep Cycle |

Migraciones son **aditivas** (`_ADDITIVE_MIGRATIONS` en `db.py`) —
nunca rename ni drop. La DB se comparte entre versiones.

### 3.2 Two-node setup

- **Alienware** (Windows 11, RTX 5050 8GB) — supervisor + 6 surfaces +
  Ollama local + voz + dashboard web. Es donde vive el usuario.
- **Auctorum** (Ubuntu 24.04, GTX 1070 8GB) — worker headless por
  Tailscale CGNAT (`100.64.0.0/10`). Corre ChromaDB, Ollama remoto
  (`qwen3.5:9b` + `nomic-embed-text`), reranker (flashrank
  ms-marco-MiniLM), vision (llava-phi3:3.8b) y un health aggregator
  que expone `/health`. Todo bajo systemd con ufw scoped.

### 3.3 Cross-process WS reactivity

`kee/surfaces/api.py::_audit_tailer` corre como background task dentro
del proceso API: poll cada 1s al `audit_log` y broadcast a todos los
clientes WS de `/stream`. Esto es lo que permite que el bot de Telegram
ejecute una tool y la NeuralCanvas en la dashboard reaccione, aunque
sean procesos separados. Los clientes envían
`{type:"client_register", wants_audio:true}` para recibir
`voice_audio_*` chunks; los demás sólo reciben los eventos textuales.

---

## 4. The 4-provider LLM chain + 5-tier router

### 4.1 Providers (ABC en `kee/core/llm/base.py`)

| Provider | Modelo | Costo | Notas |
|---|---|---|---|
| `ClaudeProvider` | claude-sonnet-4-6 | paid | tier `heavy` |
| `ClaudeHaikuProvider` | claude-haiku-4-5 | paid (cheap) | tier `medium`, subclase de Claude |
| `OpenAIProvider` | gpt-4o-mini | paid (cheap) | tier `conversational` |
| `OllamaProvider` | qwen3:8b local | $0 | tier `simple`, default fallback |
| `OllamaProvider` (remote) | qwen3.5:9b@auctorum | $0 | activado con `AUCTORUM_OLLAMA` env |

`LLMChain` (`kee/core/llm/chain.py`) **siempre incluye los 5**, sólo
reordena según `KEE_LLM_PRIMARY`. Cada call escribe a `cost_ledger`:
provider, model, tier, tokens prompt/completion, latency_ms, cost_usd.

### 4.2 Router de 5 tiers

`kee/core/router.py` clasifica cada turn del usuario usando
`llama3.2:1b` (literalmente $0 y <100ms). Reglas en
`vault/config/router.md` — un markdown editable que el usuario
modifica para cambiar el comportamiento sin tocar código.

| Tier | Provider | Caso | Costo aprox |
|---|---|---|---|
| `direct` | ninguno | match regex en router.md, respuesta plantilla | $0 |
| `simple` | Ollama local | "qué hora es", "abre spotify" | $0 |
| `conversational` | gpt-4o-mini | charla casual, tool calls simples | $0.0001 |
| `medium` | Haiku | razonamiento moderado, planning corto | $0.001 |
| `heavy` | Sonnet | código, debugging, write-ups largos | $0.01 |

### 4.3 Cost tracker + kill switch

`kee/core/llm/cost_tracker.py` lee `cost_ledger`, suma el día actual,
y cuando alcanza `KEE_DAILY_COST_CAP_USD` (default `2.00`) fuerza
todos los providers a `OllamaProvider` hasta medianoche local. Este
hard kill se ve en la dashboard `/cost` con el switch en rojo.

### 4.4 Re-probe on failure

El embedder remoto puede caerse silenciosamente (Auctorum reboot, Tec
wifi bloqueando outbound). En vez de cachear el host indefinidamente,
`kee/distributed/indexer.py` re-prueba la conexión por cada batch de
embeddings, y al fallar cae al embedder local (también
`nomic-embed-text` vía Ollama). El agente nunca crashea por esto.

---

## 5. Surfaces

Todas son procesos independientes bajo el supervisor.

### 5.1 `terminal`
REPL clásico — `python -m kee.main`. Útil para debugging y para correr
`sleep-cycle` o `gc` one-shot.

### 5.2 `voice` (`kee/perception/voice.py`)
La superficie más pesada. Pipeline:

```
mic → silero VAD (h, c separados!) → openWakeWord (kee.onnx)
    → faster-whisper (CPU, hard-coded) → KeeAgent.process()
    → Piper local TTS  ó  ElevenLabs (opt-in)
    → audio chunks 0.8s broadcast por /stream con `rms` envelope
```

Filtro anti-hallucination de Whisper (silencio → "Subtítulos por
Amara.org" se descarta). Streaming TTS por chunks con
`KEE_VOICE_STREAMING=1`. Wake-word custom-trained con
`scripts/wakeword/`.

### 5.3 `chat` / `api` (`kee/surfaces/api.py`)
FastAPI. Sirve la dashboard SvelteKit, el HUD pywebview, y endpoints
REST para todas las tools internas (`/tools`, `/system/supervisor`,
`/quality/lifetime`, `/cycle/tool-rewrites`, `/episodic/query`,
`/voice/stream`, `/edge/ask` con bearer, etc.). Aloja
`_audit_tailer`.

### 5.4 `telegram` (`kee/surfaces/telegram.py`)
Bot aiogram con `ConversationState` multi-turn. Allowlist de chat IDs
(crítico — sin esto cualquier desconocido le da prompts a Kee). Cada
mensaje pasa por el mismo `KeeAgent.process()` que la voz; las
respuestas se escriben al audit_log y la dashboard las ve en tiempo
real vía WS.

### 5.5 `notif-bridge` (`kee/perception/notif_bridge_windows.py`)
Daemon winrt usando `UserNotificationListener`. Poll cada 1s el shell
de Windows, dedup por `(app, title, body)`, POST a
`/notifications/inbound`. Es lo que permite que Kee vea las notifs de
Discord, Slack, WhatsApp Desktop, calendar, etc.

### 5.6 `heartbeat` (ver §9)
5-min loop con 15 checks.

### 5.7 `desktop` (HUD)
Ventana pywebview chiquita en una esquina, sirviendo `/app/hud` desde
la API. Tres.js orb que pulsa con el estado del agente. Toggleable
con `KEE_DAEMON_DESKTOP=0`.

### 5.8 Supervisor (`kee/daemon/supervisor.py`)

`python -m kee.main all` (alias `daemon`) lanza el supervisor.
Detalles importantes:

- **Backoff exponencial**: `2,4,8,16,32,60s`. Se resetea tras 60s de
  uptime estable.
- **Graceful shutdown**: `CTRL_BREAK_EVENT` en Windows / `SIGTERM`
  Linux, 8s de gracia, luego SIGKILL.
- **`--only api,heartbeat`** o `KEE_DAEMON_VOICE=0` — disable
  selectivo.
- **`install-autostart`** — registra Task Scheduler entry "Kee" hidden
  at user logon. `uninstall-autostart` lo quita.
- **`_ensure_ollama_alive`** — al boot, hace probe a
  `localhost:11434/api/tags`; si no responde, busca el binario de
  ollama y lo spawnea como `DETACHED_PROCESS`. Esto cubre el escenario
  "rebooté en un café y no arranqué Ollama manualmente".
- **`/system/supervisor`** lee `data/supervisor_state.json` (refrescado
  cada 1s). Marca `running:false` si el archivo tiene >10s de antigüedad
  aunque los hijos sigan vivos.

### 5.9 Browser extension
Manifest v3 en `browser_extension/`. Carga: `chrome://extensions` →
developer mode → load unpacked. Intercepta el `Notification` API en
WhatsApp Web, Slack, Discord, Telegram Web y forwardea a
`/notifications/inbound`. También context-menu para selección de texto.

---

## 6. Tool registry — las 64 tools

Source de verdad: `kee/core/tool_registry.py::load_builtins()` +
`vault/_kee/tools/*.py` para custom probationary. Cada tool extiende
`Tool` (`kee/tools/base.py`) con `name`, `description`,
`parameters_schema`, `risk_level` (0..3), y un `async execute(**kw)`.

### 6.1 Filesystem & shell
`files`, `shell`, `system`, `system_control`, `clipboard`, `windows`,
`screen`, `desktop_control`, `open_app`

### 6.2 Web & search
`web`, `smart_search`, `research`, `news`, `weather`

### 6.3 Memory & recall
`memory_search`, `recall`, `vault_search`, `episodic`, `narrate_day`,
`recap_week`, `compare_days`

### 6.4 Cognición y planning
`planner`, `plan` (history/recall/mark_executed), `infer_goal`,
`goals`, `reflect`, `dispatch`, `quality_snapshot`

### 6.5 Productividad
`work_session` (start/stop/summary/prune), `focus`, `pomodoro`,
`learn`, `projects`, `notes`, `brief`

### 6.6 Comunicación
`notify`, `email_send`, `gmail`, `whatsapp_send`, `telegram`,
`calendar`

### 6.7 Integraciones de sistema
`spotify`, `home_assistant`, `browser_control`, `wol`, `market`,
`economy`

### 6.8 Code & devops
`claude_code` (Camino A — `claude -p` headless, **no** Anthropic SDK),
`github`, `vercel_deploy`, `scaffold`, `commits`

### 6.9 Self-evolution
`create_tool` (sandbox + versionado + probationary),
`tool_reliability` (Wilson lower bound), `apply_rewrite` (aplica
proposal + `kee.main check` + auto-revert), `schedule_self`,
`user_patterns`

### 6.10 Worker / RAG
`worker_health` (ping + reindex), `vision`, `describe_screen`

### 6.11 Observabilidad
`perf_stats`, `context`, `emit` (broadcast WS arbitrario),
`inbox_triage`

### 6.12 Salvaguardas en cada call

Antes de invocar:
1. `_filter_kwargs` quita kwargs alucinados → audit row
   `kwarg_hallucination`
2. `_missing_required` verifica required del schema → audit row
   `kwarg_missing_required` + early return con hint
3. Para tools con `risk_level >= 2`, el verification loop captura
   estado pre

Tras invocar:
4. Bump `use_count`, `last_used` en memoria y SQLite
5. Verification post; si anomalía, escribe `anomalies` row + flag
   rollback

---

## 7. Memory architecture

### 7.1 Capa SQLite
Ya enumerada en §3.1 — 16 tablas. Todo lo "estructurado" vive aquí.

### 7.2 Capa semántica — ChromaDB en Auctorum

Una sola collection `episodic` con embeddings `nomic-embed-text` (768d).
Indexa todo lo "narrativo":

- `conversations` (turn por turn)
- `dispatches` (cuando Kee envía algo a alguien)
- `plan_history` rows
- `focus_sessions` con sus notas
- `learnings` (axiomas del Sleep Cycle)
- `notifications` inbound + outbound
- `perception` events (window switches, biometric reads)

Query vía `episodic` tool o `GET /episodic/query?q=…&kinds=…`. La
re-rank pasa por el reranker flashrank en el worker (ms-marco-MiniLM,
~30ms). End-to-end de pregunta vaga a snippet correcto: ~600ms warm.

### 7.3 Capa documental — vault Obsidian

`vault/` es un Obsidian vault real (`.obsidian/` configurado por
`scripts/setup_obsidian.py`):

- `vault/config/` — `user.md`, `identity.md`, `soul.md`, `goals.md`,
  `router.md`, `user_behavior.json`. **Gitignored**, los `*.template.md`
  están versionados.
- `vault/projects/` — un md por proyecto del usuario.
- `vault/_kee/` — todo lo que Kee escribe sobre sí mismo:
  - `_kee/tools/` — tools custom (probationary by default)
  - `_kee/identity_proposals/<date>.md` — propuestas para que Coco
    apruebe a mano
  - `_kee/daily/<date>.md` — digest del Sleep Cycle
  - `_kee/rewrites/` — proposals de tool_evolution

### 7.4 Backups (Sleep Cycle Phase 12)

WAL-safe SQLite snapshot vía segunda conexión read-only +
`vault.tar.gz` + `manifest.json`. Rotación a 30 días en
`data/backups/`. Opcional: Chroma snapshot por SSH stream desde
Auctorum. Manual: `python -m kee.main backup-now`.

---

## 8. Cognition layer (`kee/cognition/*`)

### 8.1 Sleep Cycle — 14 fases

Corre 04:00 local cada noche. Cada fase es independiente y resumable.

| # | Fase | Qué hace |
|---|---|---|
| 1 | `summarize` | Resume cada conversación de las últimas 24h |
| 2 | `stats` | Cuenta tools, success rate, peak hour |
| 3 | `axioms` | LLM extrae observaciones cualitativas |
| 4 | `update_behavior` | Merge stats+axioms a `user_behavior.json` |
| 5 | `propose_identity` | Markdown con cambios sugeridos a identity.md |
| 6 | `cleanup` | Prune heartbeat >30d, messages >90d |
| 7 | `digest` | `narrate_day` del día anterior con eventos reales |
| 8 | `self_evolution` | Daemon de propuestas de patches al código |
| 9 | `tool_evolution` | Rewrites de descripciones para tools con ≥3 hallucinations en 7d |
| 10 | `plan_commit_link` | Marca plans como executed cuando subject de commit solapa tokens |
| 11 | `stale_archival` | Archiva plans pendientes >30d |
| 12 | `backup` | WAL-safe SQLite + vault tar.gz, 30-day rotation |
| 13 | `worker_reindex` | Si vault cambió Y worker reachable → reindex |
| 14 | `episodic_index` | Embedea el día anterior al collection episódico |

### 8.2 Self-evolution loop

```
Sleep Cycle → propose patch (markdown) → vault/_kee/proposals/
   → human review (terminal o dashboard /cycle)
   → apply_rewrite tool ejecuta git apply
   → corre `python -m kee.main check`
   → si rojo: git revert; si verde: commit
```

Esto es el diseño **deliberadamente conservador**: nada se aplica
silenciosamente. Coco siempre da el visto.

### 8.3 World Model (`world_model.py`)

Grafo causal en `world_entities` + `world_relations`. Cuando
`KeeAgent` está por ejecutar una tool de risk ≥ 2, hace Impact
Assessment: ¿qué entidades dependen de la que voy a tocar? ¿son
critical? Bloquea o pide confirmación según thresholds.

### 8.4 Wilson confidence intervals

`kee/cognition/autonomy.wilson_lower_bound(successes, trials)`
reemplaza el success-rate ingenuo. 1/1 = 0.21, no 1.0. Una tool
nueva no salta a "trusted" hasta acumular evidencia suficiente.
`recommended_threshold` consume `trust_score` para decidir si una
tool puede correr sin confirmación.

### 8.5 Conversation QA monitor

`conversation_monitor.observe()` evalúa cada turn y escribe
`conversation_qa` audit rows (cross-process — el bot de Telegram, la
voz y la dashboard escriben al mismo log). Score < 0.55 dispara el
heartbeat `cognitive_health`. La voz tiene retry-on-low-score.

### 8.6 Tool evolution

`kee/cognition/tool_evolution.py` corre en Sleep Cycle Phase 9. Lee
`audit_log` por `kwarg_hallucination` agrupado por tool. Si hay
≥3 en 7 días, dropdown LLM call genera una rewrite del
`description` de la tool y la escribe a `vault/_kee/rewrites/`. Coco
revisa, `apply_rewrite` la aplica.

### 8.7 Plan ↔ commit linker

`kee/cognition/plan_commit_linker.py` — Phase 10. Lee `plan_history`
WHERE `executed_at IS NULL`. Para cada plan, intersecta tokens del
plan con commit subjects de las últimas 48h. ≥2 tokens en común →
marca `executed_at` con el commit hash. Esto cierra el ciclo
"propuse plan → escribí código" sin pedirle nada al usuario.

---

## 9. Perception layer (`kee/perception/*`)

### 9.1 Heartbeat — 15 checks

`kee/perception/heartbeat.py`. Cada 5 min (configurable), 30-min
cooldown por check.

| Check | Dispara cuando |
|---|---|
| `system_health` | CPU >85% sostenido, RAM >90%, disk D: <5GB |
| `ollama_status` | proc no responde a `/api/tags` |
| `pending_tasks` | task_ledger con scheduled_for vencido |
| `active_window` | switch tracking, detección de focus drift |
| `goal_deadlines` | goal con deadline <7 días sin progress |
| `calendar` | siguiente evento <30min |
| `market_alerts` | precios que cruzan thresholds |
| `biometric_state` | si hay datos del smartwatch (futuro) |
| `cognitive_health` | QA avg <0.55, hallucination burst, untrusted tool call |
| `morning_brief` | a las 8:00, summary del día |
| `focus_drift` | >12 window switches en 10 min |
| `scheduled_callbacks` | recordatorios manuales |
| `worker_status` | ping a Auctorum `/health` |
| `passive_perception` | active window cambia → vision describe (opt-in `KEE_PASSIVE_PERCEPTION=1`) |
| `opportunity_scan` | plans estancados >3d, proposals sin tocar >7d (6h cooldown) |

Cada actionable pasa por `TemporalIntelligence.allow_interruption(mode)`
antes de llegar al agente. Modes: `focus`, `meeting`, `sleep`, `free`.

### 9.2 Voice pipeline

Detallado en §5.2. Stack: silero VAD → openWakeWord → faster-whisper →
KeeAgent → Piper/ElevenLabs. VRAM: 0 (whisper en CPU, Piper en CPU,
openWakeWord es ONNX <100MB). Streaming TTS broadcastea chunks de
0.8s con envelope RMS para que la NeuralCanvas pulse.

### 9.3 Wake-word

`kee.onnx` custom-trained con openWakeWord. Pipeline de training en
`scripts/wakeword/` (pos samples grabados con la voz de Coco, neg
del mismo background, augment con ruido de Tec/café/calle).

### 9.4 Notification router

`kee/perception/notifications.py::notify_user` — fan-out asíncrono a
desktop toast + Telegram + persist en `notifications`. Reglas:

- DND window (`KEE_DND_HOURS=22-7`)
- Focus drift activo → suprime non-critical
- Critical override (`urgency=2`) atraviesa todo
- Quiet hours dump al inbox para morning brief

### 9.5 Passive perception (Gap 6 cerrado)

`_check_passive_perception` captura screenshot del active window
cuando cambia "meaningfully" (no swap entre VS Code y terminal del
mismo proyecto), lo manda al endpoint vision del worker
(`llava-phi3:3.8b`), y persiste el caption a `audit_log`. Esto le
da a Kee contexto del día sin que el usuario tenga que contarle.

### 9.6 `notif_bridge_windows`

Detallado en §5.5. Único componente que importa `winrt` —
fail-soft si no está disponible (Linux dev environment).

---

## 10. Distributed architecture

### 10.1 Auctorum worker

Ubuntu 24.04 + GTX 1070 8GB + Tailscale CGNAT. Provisioning **en un
solo bash**: `bash scripts/auctorum/provision.sh`. Idempotente —
re-run safe. Instala apt deps, crea venvs en `/opt/auctorum/`, escribe
las systemd units, configura ufw scoped a `100.64.0.0/10`.

### 10.2 Servicios

| Service | Puerto Tailscale | Stack |
|---|---|---|
| ChromaDB | 8001 | chromadb HTTP |
| Ollama remote | 11434 | qwen3.5:9b + nomic-embed-text |
| Reranker | 8002 | flashrank ms-marco-MiniLM-L-12-v2 |
| Vision | 8003 | llava-phi3:3.8b vía Ollama |
| Health aggregator | 8000 | FastAPI con `/health` único |

### 10.3 Surgical RAG

```
query → embed (nomic-embed via Ollama) → Chroma top-50
     → reranker top-8 → snippets a Sonnet/Haiku/Ollama
```

20 vault files indexados, queries semánticas devuelven snippets en
~600ms warm. Verificado live.

### 10.4 Termux mobile edge node

`POST /edge/ask` con bearer token (`KEE_EDGE_TOKEN`). El móvil de
Coco puede preguntarle a Kee desde fuera de Tailscale; Kee responde
con el provider más barato disponible.

### 10.5 Fallback bulletproof

Sin Auctorum, Kee:
- LLM cae a Ollama local (qwen3:8b)
- Embedder re-prueba en cada batch, falla a local
- `episodic` queries devuelven `{"ok": false, "reason": "worker
  unreachable"}` en vez de crashear
- Vision/describe_screen reportan tool unavailable

---

## 11. Safety systems (no se bypassean — punto)

| Sistema | Archivo | Función |
|---|---|---|
| KeeScheduler | `kee/core/scheduler.py` | locks priority-aware: `llm`, `vram`, `memory`, `fs`. Voz/heartbeat/usuario no se pisan |
| VRAMArbiter | `kee/core/vram_arbiter.py` | budget 8GB por nodo, tenants `LLM/WHISPER/VISION/EMBEDDINGS`. Whisper hard-coded CPU |
| verify.py | `kee/core/verify.py` | pre/post snapshot para `files` y `execute_shell`, anomaly detection, rollback flag |
| create_tool sandbox | `kee/tools/create_tool.py` | subprocess test, versionado (archiva previa), probationary, audit |
| tool_gc | `kee/core/tool_gc.py` | archiva probationary >7d sin uso, flag low-use >30d |
| Cost kill switch | `kee/core/llm/cost_tracker.py` | $2/day → fuerza ollama hasta medianoche |
| Allowlist Telegram | `kee/surfaces/telegram.py` | chat_id allowlist explícita |
| Verification loop en agente | `kee/core/agent.py` | wraps tool execute para risk ≥2 |

---

## 12. Dashboard (SvelteKit 2 + Svelte 5 runes + Tailwind 4)

16 páginas en `dashboard/src/routes/`:

| Ruta | Para qué |
|---|---|
| `/` | landing, health glance |
| `/chat` | conversación principal |
| `/conversations` | historial filtrable |
| `/nervous-system` | NeuralCanvas v5 fullscreen |
| `/world` | grafo causal del world model |
| `/cycle` | reportes Sleep Cycle + propuestas |
| `/voice` | VU meter + transcripción live |
| `/vault` | browser estilo Obsidian read-only |
| `/tools` | inventario, trust scores, hallucinations |
| `/goals` | progress, deadlines |
| `/notifications` | inbox unified |
| `/episodic` | semantic recall UI |
| `/diary` | daily digests del Sleep Cycle |
| `/worker` | status Auctorum, reindex button |
| `/health` | supervisor surfaces strip + log tails |
| `/settings` | env, providers, router rules editor |
| `/cost` | $/día con kill-switch state + tabla de paid calls |
| `/audit` | raw audit_log |
| `/hud`, `/hologram` | endpoints para pywebview, no listados arriba |

**NeuralCanvas v5** — Canvas2D (no WebGL), ~1500 partículas, 5 paletas
de estado (idle, listening, thinking, executing, alarmed). Render
target separado del bloom para evitar feedback visual.

---

## 13. Test strategy

- **35 regression suites** en `tests/`, todas $0. `_FakeLLM` stubs
  reemplazan cualquier `llm.chat()`. Runner: `python tests/run_all.py`
  — fuerza UTF-8 stdio para que las suites con `✓/✗` no exploten en
  cp1252 Windows console.
- **Integration suite**: `tests/test_real_rag.py` (4 casos) gated por
  `KEE_TEST_REAL_RAG=1`. Sólo se corre cuando Auctorum está vivo.
- **Smoke**: `python -m kee.main check` reporta SQLite OK, identity
  files presentes, Ollama health, tool count, supervisor state.

Política dura: **CI nunca paga**. Si una feature llama LLM, su test
stuba `LLMChain.chat()` y verifica el `messages` payload, no la
respuesta del modelo real.

---

## 14. Operations

| Comando | Qué hace |
|---|---|
| `python -m kee.main` | terminal REPL |
| `python -m kee.main all` (alias `daemon`) | supervisor + 6 surfaces |
| `python -m kee.main install-autostart` | Task Scheduler entry "Kee" hidden at logon |
| `python -m kee.main uninstall-autostart` | quita la entry |
| `python -m kee.main check` | diagnostics |
| `python -m kee.main sleep-cycle` | one-shot, todas las 14 fases |
| `python -m kee.main backup-now` | manual backup |
| `python -m kee.main gc` | tool_gc sweep |
| `python -m kee.main notif-bridge` | sólo el bridge winrt |
| `python -m kee.main tray` | pystray icon (opcional) |
| `bash scripts/auctorum/provision.sh` | provisioning del worker |
| `bash scripts/release_public.sh` | orphan-squash al repo público |

**Cadencia diaria**:
- Supervisor poll: 1s
- Heartbeat: 5min
- Sleep Cycle: 04:00 local
- `/system/supervisor` write: 1s
- `_audit_tailer` poll: 1s

---

## 15. Roadmap forward

### Phase 6 (en curso)
- Full-duplex audio — interrumpir a Kee mientras habla (echo cancellation)
- QLoRA fine-tuning local en Auctorum (LoRA personality train con
  conversaciones del usuario)
- BLE presence detection — wake-word más estricto cuando el AirTag/iPhone
  no está cerca

### Phase 7
- Self-editing codebase daemon — `apply_rewrite` invocado **automáticamente**
  por una nueva router rule cuando la propuesta tiene confidence alta
  + tests verdes
- Speaker diarization full (no sólo lite) — distinguir voces múltiples
  en una sala
- YAMNet sound detection — perro ladra, vidrio roto, alarma de horno

### Phase 8
- AEGIS Terminal market integration (conexión con el proyecto de
  trading personal del usuario)
- Multi-language voice — switching es/en/de a media frase con detección
- Biometric telemetry — pulso/sleep/HRV del smartwatch alimentando
  `_check_biometric_state` con datos reales

### Pendientes menores
- Telegram media — fotos pasan por vision, audios por whisper,
  documentos por research
- Cross-conversation linking — "el plan que mencionamos ayer en voz"
  enlaza a la sesión correcta
- Tests para opportunity_scan + self_correction edge paths
- Mini-charts en `/cost` (sparkline 30d)

---

## 16. Anti-features (deliberadamente NO en Kee)

- **No cloud-only en core**. Cualquier dependencia de pago es opt-in.
- **No silent updates**. Toda propuesta pasa por revisión humana.
- **No data exfiltration**. Todo evento se queda local. El único
  outbound es a providers LLM seleccionados por el usuario.
- **No paid LLM en tests**. Punto.
- **No `pip install` desde el agente en producción**. `create_tool`
  corre subprocess sandbox, no instala paquetes.
- **No autonomía en acciones irreversibles**. Borrar, publicar,
  enviar dinero, mandar email a desconocidos — siempre confirmación.
- **No multi-tenant**. Kee es de Coco. Single-user es feature, no bug.

---

## Quick reference

### Variables de entorno claves
```bash
# LLM
KEE_LLM_PRIMARY=ollama|openai|claude|haiku
KEE_DAILY_COST_CAP_USD=2.00
ANTHROPIC_API_KEY=...
OPENAI_API_KEY=...

# Ollama
OLLAMA_HOST=http://localhost:11434
OLLAMA_MODELS=D:\Ollama\models       # Windows — keep off C:
OLLAMA_KV_CACHE_TYPE=q4_0
OLLAMA_FLASH_ATTENTION=1
OLLAMA_KEEP_ALIVE=24h
KEE_NUM_CTX=4096                     # NO 8192 — KV cache OOM

# Worker
AUCTORUM_OLLAMA=http://100.64.x.x:11434
AUCTORUM_CHROMA=http://100.64.x.x:8001
AUCTORUM_RERANKER=http://100.64.x.x:8002
AUCTORUM_VISION=http://100.64.x.x:8003

# Daemon toggles
KEE_DAEMON_VOICE=0                   # disable voice
KEE_DAEMON_DESKTOP=0                 # disable HUD
KEE_DAEMON_SLEEP_CYCLE=1             # opt-in sleep cycle inside daemon
KEE_PASSIVE_PERCEPTION=1             # opt-in passive vision

# Voice
KEE_VOICE_STREAMING=1
ELEVENLABS_API_KEY=...               # opt-in TTS pago

# Edge
KEE_EDGE_TOKEN=...                   # bearer para POST /edge/ask
```

### Comandos top-10
```bash
python -m kee.main check               # diagnostics
python -m kee.main all                 # full daemon tree
python -m kee.main sleep-cycle         # one-shot cognition pass
python -m kee.main backup-now          # manual backup
python -m kee.main install-autostart   # Windows logon entry
python tests/run_all.py                # $0 regression gate
bash scripts/auctorum/provision.sh     # bring up worker
schtasks /Run /TN Kee                  # start without reboot
curl localhost:8000/system/supervisor  # surface health
curl localhost:8000/quality/lifetime   # QA score over time
```

### File paths críticos
```
kee/core/agent.py                  # reasoning loop
kee/core/tool_registry.py          # 64 tools, hallucination filters
kee/core/db.py                     # SQLite schema + migrations
kee/core/llm/chain.py              # provider fallback
kee/core/router.py                 # 5-tier classifier
kee/core/scheduler.py              # priority locks
kee/core/vram_arbiter.py           # 8GB budget enforcement
kee/core/verify.py                 # pre/post state safety
kee/core/llm/cost_tracker.py       # $2/day kill switch
kee/daemon/supervisor.py           # PID 1, backoff, ensure_ollama
kee/cognition/sleep_cycle.py       # 14-phase nightly pass
kee/cognition/tool_evolution.py    # description rewrites
kee/cognition/plan_commit_linker.py # token-overlap auto-mark
kee/perception/heartbeat.py        # 15 checks
kee/perception/voice.py            # silero+whisper+piper pipeline
kee/perception/notif_bridge_windows.py # winrt listener
kee/surfaces/api.py                # FastAPI + _audit_tailer
kee/surfaces/telegram.py           # aiogram bot
vault/config/router.md             # tier rules (markdown!)
vault/config/identity.md           # who Kee is (gitignored)
data/kee.db                        # SQLite WAL
data/supervisor_state.json         # 1s heartbeat
scripts/auctorum/provision.sh      # one-shot worker bring-up
docs/03-technical-roadmap-v2.md    # canonical roadmap
STATUS.md                          # public snapshot
STATUS.local.md                    # personal log (gitignored)
```

---

*Última actualización: 2026-05-04. Si rompe la realidad, vuelve a
`STATUS.md` primero.*
