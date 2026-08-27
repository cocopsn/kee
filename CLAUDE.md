# CLAUDE.md — instructions for AI coding sessions in this repo

This file is consumed automatically by Claude Code (and similar agents)
when they operate inside the Kee repo. Read it before doing anything.

## What is Kee?

A sovereign personal AI agent — voice-first, multi-surface, with
persistent memory, perception, and the ability to act on the user's
machine. The canonical spec is in [`docs/03-technical-roadmap-v2.md`](docs/03-technical-roadmap-v2.md);
the running build status is in [`STATUS.md`](STATUS.md). The personal
vault content (`vault/config/{user,identity,soul,goals}.md`,
`vault/projects/`, `vault/_kee/`) is **gitignored** — never check it
in. Templates for the personal files live next to them as
`*.template.md` and are seeded by `scripts/setup_personal.py`.

## Architecture in one paragraph

Surfaces (terminal / voice / chat / api / telegram / notif-bridge) call
`KeeAgent.process()`. The agent runs a reasoning loop, calls tools from
the central registry (65 tools, schema-validated), and routes LLM calls
through a 4-provider chain (Claude Sonnet + Haiku + GPT-4o-mini + Ollama)
with a 5-tier router and a `$/day` kill switch. Memory + audit + plans
+ focus + learnings + dispatches + scheduled callbacks all live in one
SQLite db. Sleep Cycle runs nightly (11 phases) — summarises, derives
axioms, proposes identity / tool-rewrite / code patches, links pending
plans to real commits. Heartbeat (12 checks) fires actionables on
hardware OR cognitive degradation.

## Resident-identity supervisor

Kee runs as a single supervised tree, not as a collection of REPLs:

- `python -m kee.main all` (alias `daemon`) → spawns
  `kee/daemon/supervisor.py` as parent. It owns: `api`, `telegram`,
  `notif-bridge`, `voice`, `heartbeat` (and `sleep-cycle` opt-in via
  `KEE_DAEMON_SLEEP_CYCLE=1`). Each surface is a subprocess with
  stdout/stderr appended to `data/<name>.{err|log}`. Exponential backoff
  on crash (`2,4,8,16,32,60s`); backoff resets after 60s of stable
  uptime. Graceful shutdown sends `CTRL_BREAK_EVENT` on Windows /
  `SIGTERM` elsewhere with 8s grace before SIGKILL.
- `--only api,heartbeat` or `KEE_DAEMON_VOICE=0` env flags — disable
  surfaces selectively.
- `python -m kee.main install-autostart` — registers Windows Task
  Scheduler entry "Kee" that launches the supervisor hidden at user
  logon. Uninstall: `uninstall-autostart`.
- `GET /system/supervisor` returns `data/supervisor_state.json`
  (refreshed every 1s by the supervisor). Dashboard Health page renders
  the surface strip from this. **State file lags up to 1s and is marked
  `running=false` when older than 10s** — even if individual children
  are still alive.

When adding a new long-running surface, register it in `SURFACES`
inside `kee/daemon/supervisor.py` AND add its log filename to the
whitelist in `/system/logs/{name}` so the Health page can show its tail.

## Multi-provider LLM chain + router

- **`kee/core/llm/`**: provider abstraction (ABC `LLMProvider`) with 4
  implementations:
  - `claude.py` → `ClaudeProvider` (Sonnet) + `ClaudeHaikuProvider`
    (Haiku, subclass)
  - `openai_p.py` → `OpenAIProvider` (gpt-4o-mini)
  - `ollama_p.py` → `OllamaProvider` (local, free)
  - `chain.py` → `LLMChain` orchestrates fallback. **Always includes all
    4 providers**, just reorders by `KEE_LLM_PRIMARY`. Per-call cost
    tracked into `audit_log` with provider/model/tier/tokens/latency/
    cost_usd.
- **`kee/core/router.py`** + **`vault/config/router.md`**: classifies
  every user turn into 5 tiers using `llama3.2:1b`:
  - `direct` → router template, $0 (regex match in router.md)
  - `simple` → ollama, $0
  - `conversational` → openai (gpt-4o-mini)
  - `medium` → haiku
  - `heavy` → claude sonnet
- **`kee/core/llm/cost_tracker.py`**: `KEE_DAILY_COST_CAP_USD` (default
  `$2/day`) → kill switch forces ollama until midnight when reached.

## Cross-process WS reactivity

`kee/surfaces/api.py::_audit_tailer` is a background task in the API
process polling `audit_log` and broadcasting via WS `/stream`. **This is
what lets cross-surface events (telegram bot, voice, etc.) update the
dashboard's neural canvas in real time** even though they're separate
processes. Smart audio routing means `voice_audio_*` events auto-route
only to clients that registered with `wants_audio:true`.

## Cross-cutting safety systems

Everything else depends on these. **Route new components through them —
don't bypass.**

- `kee/core/scheduler.py` — `KeeScheduler` with priority-aware locks
  (`llm`, `vram`, `memory`, `fs`). Every LLM call routes through the
  LLM lock so heartbeat / voice / user input cannot pile up.
- `kee/core/vram_arbiter.py` — `VRAMArbiter` tracks GPU tenants
  (`LLM`, `WHISPER`, `VISION`, `EMBEDDINGS`) per node and refuses any
  registration that would push past the budget. Whisper is hard-coded
  to CPU.
- `kee/core/verify.py` + verification loop in `kee/core/agent.py` —
  pre/post state capture for `files` and `execute_shell` tools, anomaly
  detection, rollback flag. Failed verifications produce both an
  `audit_log` row and an `anomalies` row.
- `kee/tools/create_tool.py` — meta-tool with sandbox subprocess test,
  versioning (archives previous file), probationary flag, audit trail.
- `kee/core/tool_gc.py` — background coroutine that archives
  probationary tools unused for 7 days; flags low-use ones after 30.

## Introspection feedback loop

The piece that makes Kee self-improving:

- `_filter_kwargs` in the registry writes `kwarg_hallucination` audit
  rows when the LLM invents kwargs the tool doesn't accept;
  `_missing_required` writes `kwarg_missing_required` rows when it omits
  required ones.
- `kee/cognition/conversation_monitor.observe()` writes
  `conversation_qa` audit rows per turn (score, issues, word_count).
- `tool_reliability` tool ranks tools by Wilson lower bound
  (`trust_score`) instead of naïve success rate — so 1/1 doesn't
  outrank 49/50.
- `kee/cognition/tool_evolution.py` (Sleep Cycle Phase 9) drafts
  description-rewrite proposals when a tool collects ≥3 hallucination
  hits in 7 days.
- `kee/cognition/plan_commit_linker.py` (Phase 10) auto-marks plans
  executed when commit subjects token-overlap with plan tasks.
- Phase 11 archives plans pending >30d.
- New `cognitive_health` heartbeat check fires when QA avg drops, on
  hallucination bursts, or on untrusted-tool calls.

## Hard rules for working in this repo

1. **Personal content stays out of git.**
   - `vault/config/{user,identity,soul,goals}.md` are gitignored.
   - `vault/projects/`, `vault/_kee/` are gitignored.
   - Edit the `*.template.md` files instead if the schema needs to
     change. Then run `scripts/setup_personal.py` again to refresh.
2. **Work autonomously, report at milestones.** Don't pause for
   confirmation between obvious steps. Verify prerequisites yourself,
   install what's safely installable, document what needs the user's
   hand. Reserve interruptions for genuinely irreversible / externally
   visible actions.
3. **VRAM is the hard ceiling.** Default install assumes 8 GB GPU.
   v2 §1.2 mandates these settings — they are *not* optional:
   - `KEE_NUM_CTX=4096` (NOT 8192 — KV cache would OOM)
   - `OLLAMA_KV_CACHE_TYPE=q4_0` (NOT q8_0 — saves ~500 MB)
   - `OLLAMA_FLASH_ATTENTION=1`
   - `OLLAMA_KEEP_ALIVE=24h`
   The `VRAMArbiter` is the runtime guard — every component that loads
   VRAM must register/release through it.
4. **`claude_code` tool uses Camino A.** Invoke `claude -p` headless via
   subprocess — **NOT** the Anthropic SDK with an API key. It's free
   within the user's Pro/Max subscription.
5. **Schema migrations are additive.** Add new columns via
   `_ADDITIVE_MIGRATIONS` in `kee/core/db.py`. Never rename or drop —
   the DB is shared across versions and must always migrate forward.
6. **Tests are $0.** Every new feature gets a regression test in
   `tests/test_<feature>.py` and a line in `tests/run_all.py`. The
   suite must never call a paid LLM. Use `_FakeLLM` stubs when the
   feature wraps `llm.chat()`.

## Don't do

- Don't move files outside the project root without checking with the
  user.
- Don't introduce cloud-API dependencies (OpenAI, Anthropic-direct, etc.)
  for Kee's *core* operation. The whole point is sovereignty. Optional
  opt-ins are fine.
- Don't add notification surfaces (WhatsApp/Telegram) without proper
  user-allowlist gating.
- Don't skip the verification loop when adding a write/exec tool.
  Update `verify.py` to capture state for it.
- Don't commit anything under `vault/config/` (except templates),
  `vault/_kee/`, `vault/projects/`, `data/`, `models/`, or files
  matching `AUDIT-*`, `REPORT-*`, `PRIVATE-*`, `*.local.md`. The
  `.gitignore` enforces this — don't override it.

## Common commands

```bash
# Run diagnostics
python -m kee.main check

# Run the terminal REPL
python -m kee.main

# Run the full daemon tree
python -m kee.main all

# One-shot Sleep Cycle now
python -m kee.main sleep-cycle

# Run the full $0 regression gate
python tests/run_all.py
```

## Setup checklist for a fresh machine

1. **Python 3.12+** (3.13 confirmed working).
2. **Ollama**: `curl -fsSL https://ollama.com/install.sh | sh` (Linux)
   or installer from ollama.com (Windows). Then:
   ```bash
   # Primary model — pull the HF weights, then re-wrap them with the
   # Qwen3 chat template so Ollama's tool parser works (the raw HF
   # release ships with an empty TEMPLATE that breaks every tool turn).
   ollama pull hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M
   ollama create kee-uncensored:latest -f scripts/kee_uncensored.Modelfile

   ollama pull nomic-embed-text
   ollama pull llama3.2:1b
   ```
3. Create venv: `python -m venv .venv` and activate it.
4. Install: `pip install -e .` (editable picks up changes).
5. `python scripts/setup_personal.py` (creates personal vault files
   from templates).
6. `python -m kee.main check` — should report all green except
   possibly Ollama if you haven't pulled the model yet.
