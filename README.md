# Kee

A sovereign personal AI agent. Voice-first, multi-surface, with persistent
memory, perception, and the ability to act on your machine. Runs on your
hardware (Ollama for free local inference) and falls back to Anthropic /
OpenAI when you opt in.

> **Status:** **65 tools**, **37 regression suites** (all $0), Sleep Cycle with
> **14 daily phases**, smart notification routing, plan/focus/learn/recall
> persistence, cross-process WS reactivity. See [`STATUS.md`](STATUS.md)
> for the running build log and
> [`docs/05-full-inventory.md`](docs/05-full-inventory.md) for the tool inventory
> (measured 2026-08-27: `kee/tools/` holds 65 modules).

## What Kee can do

- **Talk back to you.** Wake-word triggers (silero VAD + Whisper STT),
  Piper / ElevenLabs TTS, full-duplex barge-in, ambient sound
  classification, multi-language (Spanish + English).
- **Run on you.** 65 tools covering filesystem, shell, web search,
  browser automation (Playwright + Chromium), git, GitHub CLI, Vercel
  deploy, Obsidian vault, calendar, gmail, whatsapp, telegram, weather,
  news, screen capture, Spotify, Home Assistant, Wake-on-LAN.
- **Remember you.** SQLite-backed memory across sessions: messages,
  conversations, plans, focus sessions, scheduled callbacks, learnings,
  dispatches, audit log. Cross-conversation summaries auto-generated
  every 10 min. Vault search (substring + ChromaDB semantic when
  available).
- **Improve itself.** Sleep Cycle runs nightly at 04:00: summarises
  conversations, derives axioms, proposes identity updates, drafts
  tool-description rewrites when the LLM keeps misusing a tool, links
  pending plans to real commits, archives stale plans. Heartbeat fires
  cognitive-health alerts when reply quality drops.
- **Stay out of your way.** Smart notification router respects quiet
  hours (00–07 by default), focus sessions (notification about project
  A while you're focused on B → telegram only), and DND windows (gaming /
  meeting active → no desktop toasts).
- **Manage cost.** Multi-provider LLM chain (Claude Sonnet + Haiku +
  GPT-4o-mini + Ollama) with a 5-tier router (`direct/simple/
  conversational/medium/heavy`) and a `$2/day` kill switch that forces
  Ollama for the rest of the day.

## Install (Windows / Linux)

### Prerequisites

- **Python 3.12+** (3.13 confirmed working)
- **[Ollama](https://ollama.com)** (required for free local inference)
- **git**, **node** (only if you want the SvelteKit dashboard)

### 1. Clone + venv

```bash
git clone https://github.com/cocopsn/kee.git
cd kee
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux/Mac:
source .venv/bin/activate
pip install -e .
```

### 2. Pull a local model

```bash
# Primary brain — pull the HF weights, then re-wrap them with the Qwen3
# chat template so Ollama's tool parser can read the model's responses.
# (The raw HF release ships with an empty TEMPLATE that breaks tool use.)
ollama pull hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M
ollama create kee-uncensored:latest -f scripts/kee_uncensored.Modelfile

ollama pull nomic-embed-text   # for embeddings (Phase 3)
ollama pull llama3.2:1b        # for the router (5-tier, ~250ms)
```

### 3. Customise your identity

```bash
python scripts/setup_personal.py
```

Then open `vault/config/{user,identity,soul,goals}.md` and fill them
in — they're gitignored so your personal content never leaks.

### 4. Configure secrets (optional)

Copy `.env.example` to `.env` and uncomment the providers you want:

- `KEE_LLM_PRIMARY=ollama` (free, local) or `claude` / `openai` etc.
- `ANTHROPIC_API_KEY`, `OPENAI_API_KEY` if you opt in to paid providers
- `KEE_TELEGRAM_TOKEN` + `KEE_TELEGRAM_ALLOWED_USERS` for the bot
- `RESEND_API_KEY` for email
- `WHATSAPP_TOKEN` + `WHATSAPP_PHONE_NUMBER_ID` for WhatsApp Cloud API
- `KEE_DAILY_COST_CAP_USD=2` (kill switch, defaults to $2)

### 5. Smoke test

```bash
python -m kee.main check
```

Should report your tool count, Ollama health, identity files OK.

### 6. Run a surface

```bash
python -m kee.main             # terminal REPL
python -m kee.main api         # FastAPI on 127.0.0.1:7330
python -m kee.main voice       # voice loop (needs mic + Piper)
python -m kee.main telegram    # Telegram bot
python -m kee.main all         # everything under the supervisor
```

Once the API is up, useful URLs:

- `http://127.0.0.1:7330/app/`             — full SvelteKit dashboard
- `http://127.0.0.1:7330/worker/dashboard` — standalone HTML worker viewer
- `http://127.0.0.1:7330/health`           — JSON system health
- `http://127.0.0.1:7330/fleet`            — multi-node probe

### 7. (Optional) Bring up the worker node

If you have a second machine on Tailscale (the v2 design — primary +
worker split for ChromaDB / reranker / vision), the bring-up is a
single script:

```bash
ssh user@worker
git clone https://github.com/cocopsn/kee.git ~/kee
bash ~/kee/scripts/auctorum/provision.sh
```

The script is idempotent: installs deps, creates venvs for ChromaDB +
reranker + vision + health aggregator, deploys systemd units, opens
ufw for the Tailscale CGNAT, and runs smoke tests. Then on the
primary, set `AUCTORUM_HOST=<worker-tailscale-ip>` in your `.env`.

Or install autostart on Windows:

```bash
python -m kee.main install-autostart
```

## Architecture in one screen

```
                       ┌─────────────────┐
       ┌───────────────┤  Surfaces       │
       │ terminal      │  api(FastAPI)   │
       │ voice         │  telegram       │
       │ chat (SSE)    │  notif-bridge   │
       └───────────────┴─────────────────┘
                          │
                          ▼
       ┌──────────────────────────────────┐
       │  KeeAgent (kee/core/agent.py)    │
       │  • reasoning loop                │
       │  • verify-and-retry on voice     │
       │  • QA observe → audit_log        │
       │  • auto-dispatch breadcrumbs     │
       └──────────────────────────────────┘
                          │
       ┌──────────────────┼──────────────────┐
       ▼                  ▼                  ▼
   ┌─────────┐      ┌─────────────┐   ┌──────────────┐
   │ Tools   │      │ LLM chain   │   │ Cognition    │
   │ (55+)   │      │ Claude+Haiku│   │ planner,     │
   │ schema  │      │ +OpenAI+    │   │ sleep cycle, │
   │ validated│     │ Ollama      │   │ world model, │
   └─────────┘      └─────────────┘   │ self-heal,   │
                          │            │ tool_evol,   │
                          │            │ plan_link    │
                          ▼            └──────────────┘
                  ┌──────────────┐            │
                  │ SQLite memory│ ◄──────────┘
                  │ + audit_log  │
                  │ + plans      │
                  │ + focus      │
                  │ + learnings  │
                  │ + dispatches │
                  │ + callbacks  │
                  └──────────────┘
```

The full v2 roadmap is in [`docs/03-technical-roadmap-v2.md`](docs/03-technical-roadmap-v2.md).

## Project layout

```
kee/                Python package
├── core/           Agent, db, memory, identity, scheduler, LLM chain
├── tools/          The 55+ tools (each is a self-contained .py file)
├── cognition/      Sleep Cycle, planner, world model, self-evolution,
│                   tool_evolution, plan_commit_linker, autonomy (Wilson)
├── perception/     Voice, heartbeat, notifications, biometric,
│                   notification_router, ambient_sound, speaker_id
├── surfaces/       terminal, api, telegram
├── distributed/    Optional worker (ChromaDB, vision, embeddings)
├── daemon/         Supervisor + autostart (Windows Task Scheduler)
└── desktop/        Optional pywebview HUD with Three.js orb

vault/              Obsidian vault — personal content (gitignored)
├── config/         identity / soul / user / goals (templates committed)
├── projects/       Per-project notes (gitignored)
└── _kee/           Sleep Cycle outputs, learnings, decisions (gitignored)

dashboard/          SvelteKit 2 + Svelte 5 + Tailwind 4 dashboard
                    (12 pages, NeuralCanvas, served at /app/)

browser_extension/  Manifest v3, intercepts WhatsApp / Slack / Discord /
                    Telegram Web notifications → /notifications/inbound

tests/              All $0, no LLM in CI
                    Run: python tests/run_all.py

scripts/            setup_personal, setup_obsidian, setup_chromadb_local,
                    import_chat_exports, import_project_docs, etc.
```

## Tests

```bash
python tests/run_all.py       # 25 suites, all $0
```

## What "sovereign" means

- **Local-first.** Every core function works without paid APIs (Ollama
  for inference, faster-whisper for STT, Piper for TTS, ChromaDB for
  RAG, all SQLite for state).
- **Your data, your machine.** No cloud dependencies in the default
  install. Personal vault content is gitignored. The dashboard talks to
  127.0.0.1 only.
- **Auditable.** Every tool call is logged with risk level, latency,
  cost, and verification state. Sleep Cycle generates daily review
  artefacts you approve before they touch your identity files.
- **Reversible.** Self-evolution proposals never auto-apply to soul.md.
  Tool-rewrite proposals never modify .py source. Plan auto-link only
  fires on strong matches (≥3 commits or ≥3 token overlap).

## Hard rules for contributors

See [`CLAUDE.md`](CLAUDE.md) for the full rule set this repo enforces on
agentic coding sessions. Highlights:

- VRAM is the hard ceiling (RTX 5050 8GB → `KEE_NUM_CTX=4096`,
  `OLLAMA_KV_CACHE_TYPE=q4_0`).
- The `claude_code` tool uses Camino A (subprocess `claude -p`, free
  within the Pro/Max subscription).
- Don't add cloud dependencies for *core* operation. Optional opt-ins
  are fine.
- Schema migrations are additive (`_ADDITIVE_MIGRATIONS` in
  `kee/core/db.py`); don't drop or rename columns.

## License

MIT. See [`LICENSE`](LICENSE).

## Origin

Built as a personal sovereign agent inspired by the Jarvis pattern.
Public release maintains the framework; the personal vault content
(identity, soul, user, projects, daily briefs) stays out of git via the
standard `scripts/setup_personal.py` flow.
