# KEE — Sovereign Digital Ecosystem
## Architecture Blueprint v1.0 | Mayo 2026
### "From scratch. Ours and no one else's."

---

## 0. Design Philosophy

Kee is not a chatbot with tools. Kee is an **autonomous cognitive layer** that wraps around Armando's entire digital life — always listening, always watching, always thinking. It runs across two physical nodes connected via encrypted mesh, perceives the world through microphone, screen, and notifications, acts through shell, browser, and APIs, remembers through a semantic knowledge graph, and speaks with a voice that is uniquely its own.

**Core principles:**
- **Sovereignty**: Zero external dependencies for core operation. No cloud APIs required. No frameworks owned by others.
- **Full Jarvis autonomy**: Kee acts first, logs everything, and Armando reviews when he wants to. No confirmation dialogs unless the action is irreversible.
- **Voice-first**: The primary interface is speech. Everything else is secondary.
- **Multi-surface**: Voice, terminal, web dashboard, and messaging are simultaneous — never mutually exclusive.
- **Self-evolving**: Kee can write, test, register, and use its own tools. It grows more capable over time.

---

## 1. Hardware Topology

```
┌─────────────────────────────────────┐     Tailscale Mesh     ┌─────────────────────────────────────┐
│  ALIENWARE 16 (Primary / Kee Core)  │◄══════════════════════►│  AUCTORUM PC (Worker / Memory)      │
│                                     │    SSH + HTTP APIs      │                                     │
│  CPU: Intel Core 7 240H (10C/16T)   │                        │  CPU: Intel i3-7100 (2C/4T)         │
│  GPU: RTX 5050 8GB GDDR7            │                        │  GPU: GTX 1070 8GB GDDR5            │
│  RAM: 16GB DDR5 5600MHz             │                        │  RAM: 16GB DDR4                     │
│  SSD: 2.5TB NVMe                    │                        │  SSD: 1TB                           │
│  OS:  Ubuntu 24.04 (primary)        │                        │  OS:  Windows 10 Pro + WSL2          │
│       Windows 11 (dual boot)        │                        │                                     │
│                                     │                        │                                     │
│  RUNS:                              │                        │  RUNS:                              │
│  • Kee Agent Core (Python)          │                        │  • ChromaDB (vector memory)         │
│  • Ollama (Qwen3.5 9B abliterated)  │                        │  • Embedding service (Ollama)       │
│  • openWakeWord ("Kee" detection)   │                        │  • Background indexer               │
│  • faster-whisper (STT)             │                        │  • Fine-tuning jobs (QLoRA)         │
│  • Piper TTS                        │                        │  • Syncthing (vault sync)           │
│  • Web Dashboard (SvelteKit)        │                        │  • Ollama backup instance           │
│  • PaddleOCR (on-demand)            │                        │                                     │
│  • Perception daemons               │                        │                                     │
└─────────────────────────────────────┘                        └─────────────────────────────────────┘
```

### VRAM Budget — Alienware RTX 5050 (8GB GDDR7)

| Component | VRAM | When |
|-----------|------|------|
| Qwen3.5 9B abliterated Q4_K_M | ~6.6GB | Always loaded |
| faster-whisper medium | ~1.5GB | On-demand (swaps with LLM KV cache) |
| PaddleOCR | ~0.5GB | On-demand, CPU fallback available |
| **Total peak** | **~7.1GB** | LLM + whisper simultaneous |

Strategy: Ollama's `OLLAMA_KEEP_ALIVE` manages model loading. LLM stays resident. STT loads on wake word detection, processes, unloads. OCR runs on CPU by default, GPU only if explicitly requested.

```bash
# Optimize VRAM usage
export OLLAMA_FLASH_ATTENTION=1
export OLLAMA_KV_CACHE_TYPE=q8_0  # Saves ~1GB on context
export OLLAMA_KEEP_ALIVE=24h       # Keep model hot
```

### VRAM Budget — Auctorum PC GTX 1070 (8GB GDDR5)

| Component | VRAM | When |
|-----------|------|------|
| nomic-embed-text (embeddings) | ~0.3GB | On indexing jobs |
| QLoRA fine-tuning (Unsloth) | ~7GB | Scheduled training runs |
| Dolphin 3.0 backup | ~4.7GB | Failover only |

---

## 2. Technology Stack — Best Tool for Each Job

### Core Language: **Python 3.12+**

Not because it's comfortable — because it's correct. The AI/ML ecosystem is Python-native: Ollama SDK, faster-whisper, openWakeWord, PaddleOCR, ChromaDB, Unsloth, pydantic, asyncio. Building the agent core in Node.js would mean wrapping every ML library through subprocess calls or HTTP — unnecessary indirection. The agent loop, tool registry, memory layer, and perception pipeline are all Python.

### Web Dashboard: **SvelteKit**

Not Next.js. SvelteKit compiles to vanilla JS with zero runtime overhead, ships smaller bundles, and has native WebSocket support for real-time streaming of Kee's consciousness (logs, heartbeat, active perception). The dashboard is a window into Kee's nervous system — it needs to be fast, reactive, and always connected. SvelteKit delivers this with less code and better performance than React/Next.js.

### Messaging Gateway: **Python (Baileys via subprocess for WhatsApp, python-telegram-bot for Telegram)**

WhatsApp uses your existing AUCTORUM Baileys stack — a thin Node.js bridge that forwards messages to Kee's Python core via Unix socket. Telegram is native Python. Both are channels, not the brain.

### Database: **SQLite (sessions, audit log, task ledger) + ChromaDB (semantic memory)**

No Postgres. No Redis. SQLite handles structured data with zero ops overhead, runs embedded, and survives crashes. ChromaDB handles vector search for semantic retrieval against the Obsidian vault.

### Communication: **Unix sockets (local) + Tailscale SSH (remote) + FastAPI (internal APIs)**

FastAPI exposes Kee's internal services (memory query, tool execution, status) as HTTP endpoints on the Tailscale network. The Auctorum PC consumes and provides these APIs.

---

## 3. System Architecture — The Five Layers of Kee

```
┌──────────────────────────────────────────────────────────────────────┐
│                        LAYER 5: SURFACES                            │
│  Voice (primary) │ Terminal/CLI │ Web Dashboard │ WhatsApp/Telegram  │
├──────────────────────────────────────────────────────────────────────┤
│                        LAYER 4: IDENTITY                            │
│  identity.md │ soul.md │ user.md │ Modelfile │ LoRA personality     │
├──────────────────────────────────────────────────────────────────────┤
│                        LAYER 3: COGNITION                           │
│  Agent Loop (ReAct) │ Tool Registry │ Planner │ Autonomy Engine     │
├──────────────────────────────────────────────────────────────────────┤
│                        LAYER 2: MEMORY                              │
│  ChromaDB (semantic) │ SQLite (structured) │ Obsidian (human-readable) │
│  Conversation history │ Knowledge graph │ Audit log                 │
├──────────────────────────────────────────────────────────────────────┤
│                        LAYER 1: PERCEPTION                          │
│  openWakeWord │ faster-whisper │ PaddleOCR │ Window monitor         │
│  D-Bus notifications │ Clipboard │ Calendar sync │ File watchers    │
└──────────────────────────────────────────────────────────────────────┘
```

---

## 4. Layer 1 — Perception (Always Active)

### 4.1 Voice Pipeline

```
Microphone (always on)
    │
    ▼
openWakeWord daemon ─── listens for "Kee" ──► [not detected: continue listening]
    │
    [detected]
    ▼
silero-vad (Voice Activity Detection) ─── records until 1.5s silence
    │
    ▼
faster-whisper (medium model) ─── transcribes audio to text
    │
    ▼
Kee Agent Core ─── processes command
    │
    ▼
Piper TTS ─── generates audio response
    │
    ▼
Speaker output
```

### 4.2 Screen Perception

PaddleOCR for on-demand OCR. xdotool / window monitor for active window awareness. Linux-only (Phase 2-3).

### 4.3 Notification Interceptor

D-Bus watcher for system notifications (Linux-only).

### 4.4 File System Watcher

watchdog observes the Obsidian vault for changes and triggers re-indexing on changes to `.md` files.

---

## 5. Layer 2 — Memory (Persistent Knowledge)

### Architecture

- **Obsidian Vault** (`~/kee-vault/` — on Windows dev: `D:\Kee\vault\`) — human-readable .md files, synced via Syncthing.
- **ChromaDB** on Auctorum PC — semantic search via Tailscale HTTP API.
- **SQLite** on Alienware (`D:\Kee\data\kee.db`) — conversations, audit_log, task_ledger, tool_registry, goals.

### Vault Structure

```
vault/
├── _kee/                      # Kee's own memory space
│   ├── daily/                 # Daily digests
│   ├── decisions/             # Decisions log
│   ├── learnings/             # Things Kee learned
│   └── tools/                 # Custom tool docs / generated tools
├── projects/                  # Per-project notes
├── people/
├── knowledge/
├── config/
│   ├── identity.md            # WHO Kee is
│   ├── soul.md                # HOW Kee thinks
│   ├── user.md                # WHO Armando is
│   ├── goals.md               # Active goals
│   └── tools.md               # Available tools catalog
└── logs/
    ├── audit/
    └── heartbeat/
```

### SQLite Schema

```sql
CREATE TABLE conversations (
    id TEXT PRIMARY KEY,
    source TEXT NOT NULL,
    started_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,
    summary TEXT,
    token_count INTEGER DEFAULT 0
);

CREATE TABLE messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id TEXT REFERENCES conversations(id),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    tool_name TEXT,
    tool_result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    action TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    parameters TEXT,
    result TEXT,
    risk_level INTEGER DEFAULT 0,
    success BOOLEAN DEFAULT TRUE,
    error TEXT
);

CREATE TABLE task_ledger (
    id TEXT PRIMARY KEY,
    type TEXT NOT NULL,
    status TEXT DEFAULT 'pending',
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    scheduled_for TIMESTAMP,
    completed_at TIMESTAMP,
    command TEXT NOT NULL,
    result TEXT,
    error TEXT
);

CREATE TABLE tool_registry (
    name TEXT PRIMARY KEY,
    description TEXT NOT NULL,
    parameters_schema TEXT NOT NULL,
    source TEXT NOT NULL,
    file_path TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used TIMESTAMP,
    use_count INTEGER DEFAULT 0,
    risk_level INTEGER DEFAULT 0
);

CREATE TABLE goals (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    deadline TIMESTAMP,
    status TEXT DEFAULT 'active',
    progress_pct INTEGER DEFAULT 0,
    milestones TEXT,
    project TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP
);
```

---

## 6. Layer 3 — Cognition (The Brain)

### 6.1 Agent Loop (ReAct Pattern)

A pure Python implementation of the ReAct (Reasoning + Acting) loop with native Ollama tool calling. The agent:
1. Receives user input
2. Builds system prompt from identity + memory context
3. Calls Ollama with tools schema
4. If LLM emits tool calls — executes them, appends results, loops
5. If LLM emits final response — stores conversation, audits, returns

### 6.2 Tool Registry

Dynamic registry. Built-in tools are hardcoded; custom tools are loaded from `vault/_kee/tools/*.py` at startup. Kee can register new tools at runtime via the `create_tool` meta-tool.

### 6.3 The Meta-Tool: Kee Creates Its Own Tools

`create_tool` writes a Python file to `vault/_kee/tools/`, imports it, and registers it. After this, Kee's capability has grown.

### 6.4 Heartbeat Daemon

Runs every N minutes. Checks calendar, goals, system health, pending tasks, generates observations. Action items get fed back into the agent loop.

---

## 7. Layer 4 — Identity (The Soul)

Three markdown files in `vault/config/`:
- `identity.md` — WHO Kee is (core identity, voice, awareness, autonomy levels)
- `soul.md` — HOW Kee thinks and behaves (philosophy, decision framework, personality anchors)
- `user.md` — WHO Armando is (identity, projects, technical stack, communication preferences)

These are concatenated at runtime to form the system prompt. Editing them takes effect on the next conversation.

### Autonomy Levels

- **Level 0 (FREE)**: Read files, query memory, search web, check status, observe screen — no confirmation needed.
- **Level 1 (LOG)**: Write files, create scripts, git operations, open apps — execute freely, log everything.
- **Level 2 (LOG+NOTIFY)**: Delete files, install packages, modify system config — execute, log, notify.
- **Level 3 (CONFIRM)**: Send emails, paid API calls, push to production, anything involving external humans — ask before executing.

---

## 8. Layer 5 — Surfaces

- **Voice (primary)**: Wake Word → STT → Agent → TTS → Speaker.
- **Terminal/CLI**: Rich-based interactive REPL.
- **Web Dashboard**: SvelteKit app with WebSocket connection — chat, nervous-system view, vault browser, tool management, goals, config editor, audit log.
- **Messaging**: Telegram (python-telegram-bot), WhatsApp (Baileys bridge via Unix socket).

---

## 9. Distributed Processing

Tailscale mesh. Alienware delegates heavy work to Auctorum PC via SSH and HTTP APIs:
- Embedding batch jobs (nomic-embed-text on GTX 1070)
- ChromaDB queries (HTTP API exposed on Tailscale)
- QLoRA fine-tuning runs (Unsloth)

---

## 10. LLM Configuration

**Primary model**: `huihui_ai/qwen3.5-abliterated:9b` (Q4_K_M, ~6.6GB VRAM).

Modelfile parameters: temperature 0.7, top_p 0.9, num_ctx 8192, repeat_penalty 1.1.

System prompt is built dynamically from identity.md + soul.md + user.md at runtime.

**Phase 5 fine-tuning**: QLoRA via Unsloth on Auctorum PC GTX 1070, exported to GGUF Q4_K_M, loaded back into Ollama.

---

## 11. Systemd Services (Linux deployment)

- `kee-agent.service` — Agent core, depends on ollama.service
- `kee-wakeword.service` — Wake word daemon, depends on sound.target
- `kee-dashboard.service` — Web dashboard, depends on kee-agent.service

---

## 12. Project Structure

```
D:/Kee/
├── kee/                           # Python package — the brain
│   ├── __init__.py
│   ├── main.py                    # Entry point
│   ├── config.py                  # Settings
│   ├── core/
│   │   ├── agent.py               # ReAct loop
│   │   ├── ollama_client.py       # Ollama wrapper
│   │   ├── tool_registry.py
│   │   ├── memory.py              # SQLite + ChromaDB
│   │   ├── identity.py            # Loads .md files
│   │   ├── audit.py
│   │   ├── db.py                  # SQLite schema/init
│   │   └── heartbeat.py           # Phase 3
│   ├── perception/                # Phase 2-3 (Linux-only mostly)
│   ├── tools/                     # Built-in + Kee-generated
│   ├── surfaces/
│   │   ├── terminal.py            # Phase 0
│   │   ├── api.py                 # FastAPI (Phase 4)
│   │   └── telegram.py            # Phase 4
│   └── distributed/               # Phase 1+
├── vault/                         # Obsidian vault, synced via Syncthing
│   ├── _kee/
│   ├── config/
│   ├── projects/
│   └── ...
├── data/                          # kee.db, runtime state
├── models/                        # Wake word + TTS models
├── docs/                          # This blueprint + addendum
├── scripts/                       # Setup, maintenance
├── pyproject.toml
├── README.md
├── CLAUDE.md
└── .gitignore
```

---

## 13. Implementation Roadmap

### Phase 0 — Foundation (Week 1)
- Project structure with pyproject.toml
- Install Ollama + pull `huihui_ai/qwen3.5-abliterated:9b`
- Identity files (identity.md, soul.md, user.md)
- Basic OllamaClient with tool calling
- Basic agent loop (no tools, just conversation)
- Terminal surface with Rich
- Basic SQLite schema

### Phase 1 — Core Agent (Week 2)
- Tool registry with 5 builtin tools (shell, files, web_search, memory, system_status)
- ReAct loop with tool execution
- Audit logger
- ChromaDB on Auctorum PC
- Vault structure + file watcher
- Syncthing between machines

### Phase 2 — Voice (Week 3)
- Wake word "Kee" via openWakeWord
- faster-whisper STT
- Piper TTS
- Full voice pipeline
- Systemd services

### Phase 3 — Memory + Perception (Week 4)
- ChromaDB indexing pipeline for vault
- Semantic search in agent
- Screen perception, notifications, heartbeat

### Phase 4 — Dashboard + Messaging (Week 5-6)
- SvelteKit dashboard with WebSocket
- Telegram bot
- WhatsApp Baileys bridge

### Phase 5 — Personality + Self-Evolution (Week 7-8)
- QLoRA fine-tune
- Meta-tool create_tool
- Goal tracker
- Daily digest

### Phase 6 — External Integrations (Ongoing)
- Calendar, Gmail, GitHub, Spotify, Playwright

---

## 14. Dependencies (pyproject.toml)

```toml
[project]
name = "kee"
version = "0.1.0"
requires-python = ">=3.12"

dependencies = [
    "pydantic>=2.0",
    "ollama>=0.4",
    "httpx>=0.27",
    "openwakeword>=0.6",
    "faster-whisper>=1.1",
    "sounddevice>=0.5",
    "piper-tts>=1.2",
    "silero-vad>=5.1",
    "chromadb>=0.5",
    "paddleocr>=2.9",
    "watchdog>=4.0",
    "fastapi>=0.115",
    "uvicorn>=0.32",
    "websockets>=13",
    "rich>=13",
    "python-telegram-bot>=21",
    "paramiko>=3.5",
    "playwright>=1.49",
    "python-dotenv>=1.0",
]
```

---

*This document is the complete blueprint for Kee v1.0. Every architectural decision is intentional. Every component is chosen for technical merit, not familiarity. The system is designed to be built incrementally — Phase 0 produces a working agent in one week, and each subsequent phase adds a new dimension of capability.*

*From scratch. Ours and no one else's.*
