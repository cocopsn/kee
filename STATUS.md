# Kee — Build Status

> The personal install's running log lives in `STATUS.local.md`
> (gitignored). This file is a high-level snapshot of what the public
> framework includes.

Updated: **2026-05-04**

## Current state

- **65 tools** registered (`kee/tools/` holds 65 modules; verified 2026-08-27).
  Inventory + descriptions: see `python -m kee.main check`, `GET /tools` from
  the API, or [`docs/05-full-inventory.md`](docs/05-full-inventory.md).
- **37 regression suites** under `tests/`, all $0 (no LLM in CI).
  Runner: `python tests/run_all.py`. Plus a 4-case integration suite
  (`test_real_rag.py`) gated by `KEE_TEST_REAL_RAG=1` for live worker
  verification.
- **8 phases** of the v2 roadmap shipped or substantially complete,
  including the worker-side surgical RAG pipeline + continuous passive
  perception (Gap 6).
  Phase definitions in [`docs/03-technical-roadmap-v2.md`](docs/03-technical-roadmap-v2.md).
- **Sleep Cycle** runs nightly at 04:00 with **14 phases**:
  summarize → stats → axioms → behavior → propose → cleanup → digest
  (grounded on `narrate_day` of yesterday's real events) →
  self-evolution → tool-evolution → plan-commit-link → stale-archival
  → backup (WAL-safe SQLite + vault tar.gz, 30-day rotation) →
  worker-reindex → episodic-index.
- **Episodic memory** — single ChromaDB collection (`episodic`) over
  conversations + dispatches + plans + focus_sessions + learnings +
  notifications + perception events. Semantic recall via the new
  `episodic` tool or `GET /episodic/query?q=…&kinds=…`.
- **Multi-provider LLM chain** (Claude Sonnet + Haiku + GPT-4o-mini +
  Ollama qwen3:8b + Ollama-remote qwen3.5:9b on Auctorum) with 5-tier
  router and $2/day kill switch. Remote Ollama auto-included when
  `AUCTORUM_OLLAMA` is set.
- **15 heartbeat checks**: system_health, ollama_status, pending_tasks,
  active_window, goal_deadlines, calendar, market_alerts, biometric_state,
  cognitive_health, morning_brief, focus_drift, scheduled_callbacks,
  worker_status, **passive_perception** (opt-in via
  `KEE_PASSIVE_PERCEPTION=1`, captures + describes the active window
  via the vision endpoint when it changes meaningfully), and
  **opportunity_scan** (flags plans stalled >3d and proposals untouched
  >7d, 6h cooldown).
- **Cross-process WS reactivity**: `_audit_tailer` in API broadcasts
  audit_log changes to every dashboard tab, voice HUD, telegram bot.
- **Smart notification routing**: quiet hours + focus drift + DND window
  + critical override.
- **Distributed worker (Auctorum)** — single `bash scripts/auctorum/
  provision.sh` brings up ChromaDB + reranker (flashrank) + vision
  (llava-phi3 / Gemma) + health aggregator with systemd units, ufw
  rules for the Tailscale CGNAT, and idempotent re-runs. Surgical RAG
  works end-to-end against the worker; the agent gracefully falls back
  to local inference when it's offline.

## Subsystems shipped

### Core
- Multi-surface agent (terminal / voice / chat / api / telegram /
  notif-bridge) under one `KeeAgent`.
- SQLite schema: conversations, messages, audit_log, notifications,
  anomalies, world_entities, world_relations, tool_registry, goals,
  cost_ledger, confidence_log, plan_history, focus_sessions,
  scheduled_callbacks, learnings.
- Resident-identity supervisor with backoff + autostart on Windows.
- KeeScheduler (priority-aware locks) + VRAMArbiter (8 GB budget).

### Cognition
- MultiPathPlanner with persistence + auto-recall of prior plans.
- World Model (causal graph, impact scoring with thresholds).
- Sleep Cycle (11 phases including tool_evolution + plan_commit_linker).
- Self-evolution daemon (proposes code patches Coco reviews).
- Wilson confidence intervals for autonomy threshold.
- Conversation QA monitor + retry-on-low-score for voice.

### Perception
- Voice pipeline: silero VAD + faster-whisper + Piper / ElevenLabs.
- Wake-word: openWakeWord + custom training pipeline.
- Heartbeat: 15 checks, 4-hour cooldown for advice events.
- Biometric: coarse energy-level read from samples.
- Speaker recognition lite.
- Notification router with DND auto-detection.

### Tools (64)
Filesystem, shell, web, memory_search, system, create_tool, goals,
claude_code, vercel_deploy, github, infer_goal, notify, open_app,
browser_control, world, planner, economy, calendar, gmail, email_send,
whatsapp_send, screen, spotify, wol, market, home_assistant,
desktop_control, clipboard, windows, weather, news, research,
system_control, tool_reliability, notes, work_session, perf_stats,
scaffold, user_patterns, quality_snapshot, recall, dispatch, reflect,
inbox_triage, commits, focus, brief, smart_search, schedule_self,
context, emit, pomodoro, learn, projects, vault_search,
worker_health, vision, describe_screen,
**episodic** (semantic recall over the full event log),
**narrate_day** (chronological markdown timeline),
**recap_week** (7-day rollup with weekly totals + per-day mini-summaries),
**compare_days** (side-by-side diff of any two days, markdown Δ table),
**apply_rewrite** (apply a tool-rewrite proposal — confirm + git-clean +
auto-revert if `kee.main check` fails).

### Distributed
- **Auctorum worker live** (Ubuntu 24.04 + GTX 1070, Tailscale): ChromaDB
  + Ollama remote (nomic-embed + qwen3.5 stack) + reranker (flashrank
  ms-marco-MiniLM) + vision (llava-phi3:3.8b) + health aggregator
  (single `/health` endpoint). All under systemd, ufw scoped to
  100.64.0.0/10. End-to-end surgical RAG verified live: 20 vault files
  indexed, semantic queries return correct snippets in ~600 ms warm.
- `scripts/auctorum/provision.sh` — idempotent bring-up (apt deps,
  venvs, systemd units, ufw rules, smoke tests).
- `scripts/auctorum/{reranker,vision,health}_server.py` — the three
  FastAPI services that ship with the worker.
- `scripts/auctorum/syncthing_pair.md` — vault sync setup guide.
- Fleet probe (`/fleet`) reports per-node ping + per-service health.
- Worker re-index on demand: `POST /worker/reindex` or
  `worker_health action=reindex`.
- Sleep Cycle Phase 12 auto re-indexes if the vault changed AND the
  worker is reachable — catches "edited 5 notes on a flight, indexer
  catches up at 04:00".
- Termux mobile edge node (`POST /edge/ask` + bearer token).

### UI
- SvelteKit 2 + Svelte 5 + Tailwind 4 dashboard, 16 pages (chat,
  conversations, nervous-system, world, cycle, voice, vault, tools,
  goals, notifications, episodic, diary, worker, health, settings,
  **cost** — dedicated $-per-day breakdown with kill-switch state and
  recent paid calls table).
- NeuralCanvas v5 (Canvas2D, ~1500 particles, 5 state palettes).
- Browser extension (manifest v3) for WhatsApp / Slack / Discord /
  Telegram Web notification capture.
- Optional pywebview HUD with Three.js orb.

### Resilience
- **Bulletproof offline mode**: agent boots cleanly without Auctorum.
  LLM chain falls back to local Ollama. Embedder re-probes on failure
  (no permanent host caching). Episodic queries return graceful
  "worker unreachable" instead of crashing.
- **Supervisor-managed Ollama**: if `ollama serve` isn't responding on
  startup, the supervisor spawns it. No manual `ollama serve` needed.
- **Nightly backups** (Sleep Cycle Phase 12): WAL-safe SQLite snapshot
  via second read-only connection + vault tar.gz + manifest.json,
  30-day rotation. Optional worker Chroma snapshot via SSH stream.

## How to refresh this file

This is the public-facing snapshot. Don't put personal context here —
that goes in `STATUS.local.md` (gitignored). When a new subsystem
ships, add a bullet under the relevant section and bump the date.
