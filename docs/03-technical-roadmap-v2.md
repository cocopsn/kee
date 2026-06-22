# KEE — Technical Roadmap v2.0
## From Blueprint to Jarvis: The Complete Execution Plan
### Integrating Hardware Reality, Cognitive Gaps, and Advanced Extensions
#### Mayo 2026

> **This document supersedes [`01-architecture-blueprint.md`](01-architecture-blueprint.md) and [`02-jarvis-addendum.md`](02-jarvis-addendum.md) as the canonical roadmap.** The earlier documents remain checked in for historical context — v2 absorbs and resolves every issue raised in them, plus the hardware viability analysis, the 12 Jarvis cognitive gaps, and the 5 + 5 advanced extensions.

---

## EXECUTIVE SUMMARY

Every section below includes: the problem, the solution, the specific implementation, the hardware cost, the phase assignment, and the dependencies.

---

## PART I — HARDWARE REALITY AND RESOURCE MANAGEMENT

### 1.1 The VRAM Truth Table

The RTX 5050 has 8GB GDDR7. This is the absolute ceiling. Everything else flows from this constraint.

```
┌─────────────────────────────────────────────────────────────────┐
│                    RTX 5050 VRAM BUDGET (8GB)                   │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Windows 11 Desktop Compositor (DWM)          ~0.5–1.0 GB       │
│  ─────────────────────────────────────────────────────────      │
│  Available for Kee:                           ~7.0–7.5 GB       │
│                                                                 │
│  Qwen3.5 9B Q4_K_M (weights only)             ~5.8 GB           │
│  KV Cache (num_ctx=4096)                      ~0.5–0.7 GB       │
│  ─────────────────────────────────────────────────────────      │
│  LLM Total:                                   ~6.3–6.5 GB       │
│                                                                 │
│  Remaining headroom:                          ~0.5–1.2 GB       │
│                                                                 │
│  faster-whisper (GPU):                        ~1.5 GB ❌        │
│  faster-whisper (CPU):                        0 GB VRAM ✅       │
│  PaddleOCR (CPU):                             0 GB VRAM ✅       │
│                                                                 │
│  VERDICT: LLM is the sole VRAM tenant on the primary node.      │
│  Everything else runs on CPU or on the worker node.             │
└─────────────────────────────────────────────────────────────────┘
```

### 1.2 Critical VRAM Optimizations (Mandatory for Phase 0)

These are not optional. Without them, Kee OOMs on first boot.

#### Optimization 1: Context Window Reduction

```
# Modelfile
PARAMETER num_ctx 4096    # NOT 8192
```

**Why:** KV cache grows linearly with context length. At 8192, Qwen3.5 9B consumes ~1.5GB for KV alone. At 4096, it's ~0.7GB. This frees ~800MB — the difference between running and crashing.

**Impact on Kee:** Kee holds ~3000 tokens of conversation + ~1000 tokens of RAG context per turn. With surgical RAG (see 1.4), this is sufficient. Long conversations are summarized and compressed, not carried verbatim.

#### Optimization 2: Aggressive KV Cache Quantization

```bash
export OLLAMA_KV_CACHE_TYPE=q4_0    # Not q8_0
export OLLAMA_FLASH_ATTENTION=1
```

**Why:** q4_0 KV cache uses ~4× less memory than f16. Flash Attention reduces memory from O(n²) to O(n). Combined, these save ~500MB.

**Trade-off:** Marginal quality loss in very long reasoning chains. Acceptable for a RAG-augmented system where context is injected, not accumulated.

#### Optimization 3: Controlled Layer Offloading

```
# If VRAM is still tight, offload N layers to DDR5
# Alienware DDR5 5600MHz gives ~15-20 tokens/s with partial offload
# vs ~35-45 tokens/s full GPU

# In Ollama, set via num_gpu parameter in Modelfile:
PARAMETER num_gpu 28    # Out of ~32 total layers for 9B model
                        # Keeps 4 layers in RAM, rest on GPU
```

**Why:** DDR5 at 5600MHz is fast enough for partial offloading. The latency hit (~40% slower generation) is acceptable because Kee is voice-first — a 2-second pause before speaking sounds natural, not broken.

**When to use:** Only if OOM occurs despite optimizations 1 and 2. This is the failsafe, not the default.

#### Optimization 4: Ollama Startup Wait Script

```python
# kee/core/startup.py — MUST run before any LLM call

import httpx
import asyncio
import subprocess

async def ensure_ollama_ready(model: str, timeout: int = 120) -> bool:
    """Wait for Ollama to be running and model to be loaded.

    Ollama model loading is NOT instant:
    1. Ollama service must be running
    2. Model must be pulled (downloaded)
    3. Model must be loaded into VRAM (takes 10-30s for 9B)
    4. KV cache must be initialized
    """

    # Step 1: Wait for Ollama service
    for i in range(timeout):
        try:
            async with httpx.AsyncClient() as client:
                r = await client.get("http://localhost:11434/api/tags", timeout=2)
                if r.status_code == 200:
                    break
        except (httpx.ConnectError, httpx.TimeoutException):
            if i == 0:
                subprocess.Popen(
                    ["ollama", "serve"],
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                )
            await asyncio.sleep(1)
    else:
        raise RuntimeError(f"Ollama not available after {timeout}s")

    # Step 2: Check model is pulled
    async with httpx.AsyncClient() as client:
        r = await client.get("http://localhost:11434/api/tags")
        models = [m["name"] for m in r.json().get("models", [])]
        if model not in models and model.split(":")[0] not in [m.split(":")[0] for m in models]:
            raise RuntimeError(f"Model {model} not pulled. Run: ollama pull {model}")

    # Step 3: Warm up model (force load into VRAM)
    async with httpx.AsyncClient() as client:
        try:
            await client.post(
                "http://localhost:11434/api/chat",
                json={
                    "model": model,
                    "messages": [{"role": "user", "content": "ping"}],
                    "stream": False,
                    "options": {"num_ctx": 4096, "num_predict": 1},
                },
                timeout=60,
            )
        except httpx.TimeoutException:
            raise RuntimeError("Model loading timed out. Check VRAM availability.")

    return True
```

### 1.3 RAM Budget (16GB Alienware)

```
┌─────────────────────────────────────────────────────────────────┐
│                    RAM BUDGET (16GB DDR5)                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Windows 11 + Explorer + basic services       ~4.0–5.0 GB       │
│  VS Code (if open)                            ~0.5–1.5 GB       │
│  Browser (5-10 tabs)                          ~1.0–2.0 GB       │
│  ─────────────────────────────────────────────────────────      │
│  OS + Apps baseline:                          ~5.5–8.5 GB       │
│                                                                 │
│  Kee Python core + all daemons                ~0.5–1.0 GB       │
│  SQLite (in-process, not server)              ~0.1 GB           │
│  Node.js (WhatsApp bridge)                    ~0.2–0.5 GB       │
│  SvelteKit dashboard                          ~0.2–0.3 GB       │
│  faster-whisper (CPU mode, on-demand)         ~0.5–1.0 GB       │
│  ─────────────────────────────────────────────────────────      │
│  Kee total:                                   ~1.5–3.0 GB       │
│                                                                 │
│  ═══════════════════════════════════════════════════════════    │
│  TOTAL:                                       ~7.0–11.5 GB      │
│  HEADROOM:                                    ~4.5–9.0 GB       │
│                                                                 │
│  STRESS SCENARIO (Claude Code npm build):     +1.5–3.0 GB       │
│  TOTAL UNDER STRESS:                          ~10.0–14.5 GB     │
│                                                                 │
│  VERDICT: Tight but viable. Close VS Code and heavy browser     │
│  tabs during Claude Code builds if RAM pressure appears.        │
└─────────────────────────────────────────────────────────────────┘
```

### 1.4 Surgical RAG Pipeline (Solving the Context Window Constraint)

With `num_ctx=4096`, every token matters. The RAG pipeline must be surgical.

```
User query
    │
    ▼
Embedding (nomic-embed-text on Auctorum PC)
    │
    ▼
ChromaDB vector search (top_k=10 candidates)
    │
    ▼
Reranker (bge-reranker-base on Auctorum CPU)  ← NEW COMPONENT
    │  Scores each candidate for relevance to query
    │  Selects top 3 most relevant chunks
    ▼
Context compression (remove boilerplate, keep facts)
    │
    ▼
Final injection: ~300-500 tokens of surgical context
    │  (not 2000 tokens of noise)
    ▼
LLM receives: system_prompt (~800 tokens)
             + compressed_memory (~400 tokens)
             + user_message (~100 tokens)
             + tool_schemas (~500 tokens)
             ─────────────────────────────
             TOTAL: ~1800 tokens input
             REMAINING for generation: ~2200 tokens
```

**Reference implementation outline:**

```python
# kee/core/memory.py — Surgical RAG

class MemoryManager:
    """Three-tier memory: conversation (short), semantic (medium), structural (long)."""

    async def retrieve(self, query: str, top_k: int = 3) -> str:
        # Step 1: Vector search (broad recall)
        raw_results = self.collection.query(query_texts=[query], n_results=10)
        candidates = raw_results["documents"][0]
        metadatas = raw_results["metadatas"][0]

        # Step 2: Rerank (precision filter) — runs on Auctorum CPU
        reranked = await self._rerank(query, candidates)

        # Step 3: Select top 3 and compress
        compressed = self._compress_chunks(reranked[:top_k])

        # Step 4: Format for injection
        return "\n".join(
            f"[{meta.get('source','unknown')}]: {chunk}"
            for chunk, meta in zip(compressed, metadatas[:top_k])
        )

    async def _rerank(self, query: str, documents: list) -> list:
        """Rerank documents by relevance using bge-reranker-base."""
        try:
            async with httpx.AsyncClient() as client:
                r = await client.post(
                    self.reranker_url,
                    json={"query": query, "documents": documents},
                    timeout=5,
                )
                scored = sorted(r.json(), key=lambda x: x["score"], reverse=True)
                return [s["document"] for s in scored]
        except Exception:
            return documents  # fall back to vector ordering

    def _compress_chunks(self, chunks: list) -> list:
        """Remove boilerplate, keep factual content."""
        out = []
        for chunk in chunks:
            clean = chunk.replace("##", "").replace("**", "").strip()
            words = clean.split()
            if len(words) > 200:
                clean = " ".join(words[:200]) + "..."
            out.append(clean)
        return out
```

### 1.5 Worker Node (Auctorum PC) — Realistic Assignment

```
┌─────────────────────────────────────────────────────────────────┐
│              AUCTORUM PC — REALISTIC ROLE                       │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ✅ VIABLE:                                                     │
│  ├─ ChromaDB server (HTTP API on Tailscale)                     │
│  ├─ nomic-embed-text embeddings (~0.3GB VRAM)                   │
│  ├─ bge-reranker-base (CPU only, ~0.5GB RAM)                    │
│  ├─ Gemma 4 E4B vision (~3.5GB VRAM, on-demand)                 │
│  ├─ Syncthing vault sync                                        │
│  ├─ Background vault indexing                                   │
│  └─ Ollama backup instance (failover for inference)             │
│                                                                 │
│  ⚠️ MARGINAL:                                                   │
│  └─ QLoRA fine-tuning 9B:                                       │
│     • GTX 1070: No Tensor Cores, no Flash Attention native      │
│     • batch_size=1, seq_len=512, gradient_checkpointing=True    │
│     • Estimated time: 12-48 hours per epoch                     │
│     • VIABLE but painfully slow                                 │
│     • Alternative: Use cloud GPU for training (~$1-3/run        │
│       on RunPod/Lambda), keep 1070 for inference only           │
│                                                                 │
│  ❌ NOT VIABLE:                                                 │
│  └─ Simultaneous vision + embeddings + fine-tuning              │
│     Only ONE GPU-heavy task at a time                           │
│                                                                 │
│  CPU (i3-7100): The real bottleneck.                            │
│  ├─ 2 cores = ONE task at a time with acceptable latency        │
│  ├─ Tailscale encryption + ChromaDB + reranker = ~100% CPU      │
│  └─ Solution: prioritize tasks, queue heavy operations          │
└─────────────────────────────────────────────────────────────────┘
```

---

## PART II — THE SCHEDULER (The Nervous System)

Without this, Kee dies. The scheduler is to Kee what the autonomic nervous system is to a human body — it keeps everything alive without conscious intervention.

### 2.1 Architecture

```
                    ┌─────────────────────────────┐
                    │     KeeScheduler            │
                    │                             │
                    │  Priority Queue (heapq)     │
                    │  ┌─────────────────────┐    │
                    │  │ P0: CRITICAL (voice)│    │
                    │  │ P1: HIGH (user text)│    │
                    │  │ P2: NORMAL (heartbt)│    │
                    │  │ P3: LOW (indexing)  │    │
                    │  │ P4: IDLE (cleanup)  │    │
                    │  └─────────────────────┘    │
                    │                             │
                    │  Resource Locks:            │
                    │  ├─ llm_lock (Mutex)        │
                    │  ├─ vram_lock (Mutex)       │
                    │  ├─ memory_write_lock       │
                    │  └─ worker_gpu_lock         │
                    │                             │
                    │  Preemption:                │
                    │  P0 task → cancels P2+ tasks│
                    │  currently holding llm_lock │
                    │                             │
                    │  VRAM Arbiter:              │
                    │  Tracks what's loaded in GPU│
                    │  Prevents OOM by blocking   │
                    │  concurrent GPU loads       │
                    └─────────────────────────────┘
```

### 2.2 VRAM Arbiter (Critical New Component)

The scheduler alone isn't enough. You need a VRAM-aware resource manager that knows exactly what's loaded in the GPU at any moment and prevents OOM.

```python
# kee/core/vram_arbiter.py

import asyncio
import subprocess
from enum import Enum
from dataclasses import dataclass

class VRAMTenant(Enum):
    LLM = "llm"                 # Qwen3.5 9B — ~6.5GB, always resident
    WHISPER = "whisper"         # faster-whisper — ~1.5GB, on-demand
    VISION = "vision"           # Gemma 4 E4B — ~3.5GB (on worker only)
    EMBEDDINGS = "embeddings"   # nomic-embed — ~0.3GB (on worker only)

@dataclass
class VRAMState:
    total_mb: int
    used_mb: int
    free_mb: int
    active_tenants: list

class VRAMArbiter:
    """Prevents VRAM overcommitment. The final guard against OOM.

    Rules:
    1. LLM is always resident. Never unload.
    2. Whisper runs on CPU. Period. No GPU contention.
    3. Vision runs on worker GPU, never on primary.
    4. If worker GPU is busy with vision, embeddings queue.
    5. Fine-tuning on worker preempts everything else on worker GPU.
    """

    def __init__(self, node: str = "primary"):
        self.node = node
        self.active = set()
        self._lock = asyncio.Lock()

        if node == "primary":
            self.budget_mb = 7500
            self.tenant_costs = {
                VRAMTenant.LLM: 6500,
                VRAMTenant.WHISPER: 0,  # FORCED TO CPU
            }
        elif node == "worker":
            self.budget_mb = 7500
            self.tenant_costs = {
                VRAMTenant.VISION: 3500,
                VRAMTenant.EMBEDDINGS: 300,
            }

    async def can_load(self, tenant: VRAMTenant) -> bool:
        async with self._lock:
            current = sum(self.tenant_costs.get(t, 0) for t in self.active)
            return (current + self.tenant_costs.get(tenant, 0)) <= self.budget_mb

    async def register(self, tenant: VRAMTenant):
        if not await self.can_load(tenant):
            raise MemoryError(
                f"Cannot load {tenant.value}: would exceed VRAM budget. "
                f"Active: {[t.value for t in self.active]}"
            )
        self.active.add(tenant)

    async def release(self, tenant: VRAMTenant):
        self.active.discard(tenant)
```

### 2.3 Ollama Model Swap Queue

Ollama doesn't swap models instantly. Loading a model takes 10–30 seconds. The KV cache doesn't always free cleanly. This needs explicit management.

```python
# kee/core/model_manager.py

class OllamaModelManager:
    """Manages model loading/unloading for Ollama.

    On 8GB VRAM only ONE model can be loaded at a time. Model swaps are
    expensive (10-30s). Minimize them. Strategy:
    - Primary model (Qwen3.5 9B) stays loaded 24/7
    - Whisper runs on CPU (no model swap needed)
    - Vision runs on worker's Ollama instance (separate GPU)
    - If user explicitly requests a swap (e.g., to Dolphin), swap, process,
      swap back.
    """

    def __init__(self, primary_model: str, host: str = "http://localhost:11434"):
        self.primary = primary_model
        self.host = host
        self.current_model = None
        self._swap_lock = asyncio.Lock()

    async def ensure_primary(self):
        if self.current_model != self.primary:
            await self._load_model(self.primary)

    async def swap_to(self, model: str):
        async with self._swap_lock:
            await self._unload_current()
            await self._load_model(model)
        self.current_model = model

    async def swap_back(self):
        async with self._swap_lock:
            await self._unload_current()
            await self._load_model(self.primary)

    async def _unload_current(self):
        if self.current_model:
            async with httpx.AsyncClient() as client:
                await client.post(
                    f"{self.host}/api/chat",
                    json={
                        "model": self.current_model,
                        "messages": [{"role": "user", "content": ""}],
                        "stream": False,
                        "keep_alive": 0,
                    },
                    timeout=10,
                )
            self.current_model = None
            await asyncio.sleep(2)
```

---

## PART III — THE 12 JARVIS COGNITIVE GAPS (Phased Solutions)

### Gap 1: Goal Inference Engine

**Problem:** Kee executes instructions literally. "Optimize AUCTORUM" → refactors code. But you meant: get more revenue.

**Solution:** A goal hierarchy system that maps commands to strategic intent.

```python
# kee/cognition/goal_inference.py

class GoalInferenceEngine:
    """Maps user commands to strategic objectives.

    Hierarchy:
    Strategic (why) → Tactical (what) → Operational (how)
    """
    def __init__(self, goals_file: str = "~/kee-vault/config/goals.md"):
        self.goals = self._load_goals(goals_file)

    async def infer_intent(self, command: str, context: dict) -> dict:
        prompt = f"""Given this command from Armando: "{command}"

Active goals:
{self._format_goals()}

Recent context:
{json.dumps(context, indent=2)}

Determine:
1. What strategic goal does this serve?
2. What is the most impactful tactical action?
3. What specific operations should I execute?

Respond in JSON: {{"strategic": "...", "tactical": "...", "operations": [...]}}"""
        response = await self.llm.chat([
            {"role": "system", "content": "You are Kee's goal inference module."},
            {"role": "user", "content": prompt},
        ])
        return json.loads(response.content)
```

**Phase:** 3 (after core agent loop is stable). **VRAM cost:** 0. **Dependency:** `goals.md` must exist.

### Gap 2: World Model (Causal Knowledge Graph)

**Problem:** Kee doesn't understand cause-effect relationships. It doesn't know that deploying broken code to AUCTORUM affects client revenue.

**Solution:** A lightweight causal graph stored in SQLite, queryable by the agent.

```sql
CREATE TABLE world_entities (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    type TEXT NOT NULL,     -- 'project', 'person', 'system', 'service'
    state TEXT,             -- JSON: current state
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE world_relations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_id TEXT REFERENCES world_entities(id),
    target_id TEXT REFERENCES world_entities(id),
    relation TEXT NOT NULL,     -- 'depends_on', 'affects', 'generates', 'blocks'
    weight REAL DEFAULT 1.0,
    description TEXT
);
```

**Tool:** `query_world_model(entity, action='impact_analysis'|'dependencies')` — traverses up/down stream from an entity.

**Phase:** 4. **VRAM cost:** 0.

### Gap 3: Multi-Step Planner (Beyond ReAct)

**Problem:** ReAct is step-by-step reactive. Jarvis plans 3–5 steps ahead before acting.

**Solution:** A planning module that generates and scores multiple execution paths before committing.

```python
class MultiPathPlanner:
    """Generate 3 approaches, score by quality - risk - time, pick the best."""

    async def plan(self, task: str, context: dict) -> dict:
        prompt = f"""Task: {task}
Context: {json.dumps(context)}

Generate exactly 3 different approaches. For each:
- Steps
- Time estimate (minutes)
- Risk (low/medium/high)
- Quality score (1-10)
- Failure modes

JSON: {{"plans": [{{"name","steps","time_minutes","risk","quality_score","failure_modes"}}]}}"""
        response = await self.llm.chat([
            {"role": "system", "content": "Strategic planning module. Concrete, realistic."},
            {"role": "user", "content": prompt},
        ])
        plans = json.loads(response.content)["plans"]
        best = max(plans, key=lambda p: (
            p["quality_score"] * 2
            - {"low": 0, "medium": 1, "high": 3}[p["risk"]]
            - p["time_minutes"] / 30
        ))
        return {"selected_plan": best, "alternatives": plans}
```

**Phase:** 4. **VRAM cost:** 0 (one extra inference per complex task). **When to use:** only for tasks marked "complex" by the agent.

### Gap 4: Temporal Intelligence

**Problem:** Kee doesn't know WHEN to act, interrupt, or stay silent.

**Solution:** A context-aware timing engine integrated with the heartbeat.

```python
class TemporalIntelligence:
    """Knows when to act, when to wait, when to interrupt."""

    def __init__(self):
        self.user_patterns = {
            "deep_work_hours": [(22, 2)],   # 10pm - 2am
            "study_hours": [(8, 14)],        # 8am - 2pm (Tec classes)
            "low_energy_hours": [(14, 16)],  # Post-lunch
        }
        self.interrupt_thresholds = {
            "deep_work": Priority.CRITICAL,
            "study": Priority.HIGH,
            "normal": Priority.NORMAL,
            "idle": Priority.LOW,
        }

    def current_mode(self) -> str:
        hour = datetime.now().hour
        for mode, ranges in self.user_patterns.items():
            for start, end in ranges:
                if start <= hour or hour < end:
                    return mode
        return "normal"

    def should_interrupt(self, priority: Priority) -> bool:
        return priority <= self.interrupt_thresholds.get(self.current_mode(), Priority.NORMAL)

    def optimal_delivery_time(self, task_type: str) -> datetime:
        if task_type == "daily_digest":
            return self._next_occurrence(hour=8, minute=30)
        elif task_type == "goal_reminder":
            return self._next_occurrence(hour=22, minute=0)
        return datetime.now()
```

**Phase:** 3. **VRAM cost:** 0.

### Gap 5: Dynamic Identity Evolution

**Problem:** `identity.md` and `soul.md` are static. Kee can't evolve its personality coherently.

**Solution:** Versioned identity files with controlled mutation.

```python
class IdentityEvolution:
    """Controlled evolution of personality/behavior.

    Rules:
    1. Identity changes are NEVER automatic. Always logged, always reviewable.
    2. Versioned with git. Armando can rollback any time.
    3. Hard constraints in soul.md are immutable (marked with [IMMUTABLE]).
    4. Soft preferences can be updated by the Sleep Cycle.
    5. Maximum one change per day to prevent drift.
    """

    async def propose_identity_update(self, observation: str, evidence: list) -> dict:
        current_user = Path("~/kee-vault/config/user.md").read_text()
        prompt = f"""Based on this observation:
{observation}

Evidence from recent interactions:
{json.dumps(evidence)}

Current user.md:
{current_user}

Propose a MINIMAL, SPECIFIC update to user.md. Add or modify ONE fact.
Do not remove existing facts.
Respond with: {{"section": "...", "change": "...", "reason": "..."}}"""
        proposal = await self.llm.chat([
            {"role": "system", "content": "Identity evolution. Conservative. Small changes only."},
            {"role": "user", "content": prompt},
        ])
        self._git_snapshot("Pre-evolution snapshot")
        return json.loads(proposal.content)
```

**Phase:** 5. **VRAM cost:** 0.

### Gap 6: Continuous Passive Perception

**Problem:** Kee is event-driven (wake word → act). Jarvis is always aware.

**Solution:** A lightweight perception stream that runs continuously, detecting only CHANGES worth noting.

```python
class PassiveAwareness:
    """Lightweight continuous perception. NOT full OCR every second.

    What it does:
    1. Every 10s: Check active window title (nearly zero CPU)
    2. On window change: Log the transition
    3. Every 5min: Compare current activity pattern to baseline
    4. On anomaly: Flag for heartbeat consideration

    What it does NOT do:
    - Full OCR constantly (too expensive)
    - Full screenshot analysis (only on-demand)
    - Audio analysis when not triggered (privacy + CPU)
    """

    async def perception_loop(self):
        while True:
            new_window = await self._get_active_window()
            if new_window != self.current_window:
                self.activity_log.append({
                    "timestamp": datetime.now().isoformat(),
                    "from": self.current_window,
                    "to": new_window,
                    "duration_on_previous": self._time_on_current(),
                })
                self.current_window = new_window
                if self._detect_context_switch_overload():
                    await self.agent.process_event("perception", {
                        "type": "context_switch_overload",
                        "message": "You've switched apps 15 times in 10 minutes. Deep work might help.",
                    })
            await asyncio.sleep(10)

    async def _get_active_window(self) -> dict:
        try:
            import pygetwindow as gw  # Windows
            win = gw.getActiveWindow()
            if win:
                return {"title": win.title, "app": win.title.split(" - ")[-1]}
        except ImportError:
            result = subprocess.run(
                ["xdotool", "getactivewindow", "getwindowname"],
                capture_output=True, text=True,
            )
            return {"title": result.stdout.strip()}
        return {"title": "unknown"}
```

**Phase:** 3. **VRAM:** 0. **RAM:** ~10MB. **CPU:** <1%.

### Gap 7: User Behavior Model

**Problem:** Kee knows facts about you but doesn't model HOW you think and decide.

**Solution:** A behavioral profile that learns from your patterns. Stored in `~/kee-vault/config/user_behavior.json`. Updated by the Sleep Cycle daemon nightly.

Tracks:
- Suggestion accept/reject ratio
- Stress vs calm response patterns
- Productive hours
- Topics that get engagement vs dismissal

Provides `get_communication_hint()` so the agent knows whether to be terse, verbose, ask questions, or stay quiet *right now*.

**Phase:** 5. **VRAM cost:** 0.

### Gap 8: Impact Assessment (Anti-Drift System)

**Problem:** Autonomous actions can accumulate into suboptimal system state over weeks.

**Solution:** Pre-action impact scoring combined with post-action verification (already in place from the verify loop).

```python
class ImpactAssessment:
    """Evaluate potential consequences before critical actions."""

    async def assess(self, action: str, params: dict) -> dict:
        affected = await self.world_model.impact_analysis(entity=self._extract_entity(params))
        risk_score = sum(e["weight"] * self._entity_criticality(e) for e in affected)
        return {
            "action": action,
            "risk_score": risk_score,           # 0-10
            "affected_entities": affected,
            "recommendation": (
                "proceed"               if risk_score < 3 else
                "proceed_with_logging"  if risk_score < 6 else
                "require_confirmation"  if risk_score < 8 else
                "block_and_alert"
            ),
            "rollback_available": self._has_rollback(action),
        }
```

**Phase:** 4. **VRAM cost:** 0.

### Gaps 9–12: Summary Table

| Gap | Solution | Phase | VRAM | Complexity |
|-----|----------|-------|------|------------|
| **9. Omnipresence** | Termux + Tailscale edge client | 6 | 0 | Medium |
| **10. Internal Economy** | Cost/value scoring per task | 5 | 0 | Low |
| **11. True Initiative** | Dynamic autonomy threshold based on confidence history | 5 | 0 | Medium |
| **12. Self-Model** | Performance metrics tracking + failure rate per tool | 4 | 0 | Low |

---

## PART IV — ADVANCED EXTENSIONS (Phases 6+)

### Extension 1: Self-Editing Codebase (Recursive Self-Improvement)

```python
class SelfEditDaemon:
    """Weekly: Kee profiles its own performance and optimizes its code.

    Flow:
    1. Run cProfile on agent loop for 1 hour
    2. Identify bottlenecks (slowest functions, most memory)
    3. Generate optimization prompt with own source code
    4. Use claude_code tool to implement the fix
    5. Run test suite (must exist!)
    6. If tests pass: commit + hot-reload module
    7. If tests fail: rollback, log failure

    SAFETY:
    - Only modifies files in kee/ directory
    - Never touches identity files without proposal
    - Test suite MUST pass before commit
    - Git commit for every change (rollback always possible)
    - Maximum 3 self-edits per week
    """
```

**Phase:** 7 (requires stable test suite and proven agent loop).

### Extension 2: Omnipresence via Termux (Mobile Edge Node)

Android phone runs a thin Termux client that joins the Tailscale mesh. Captures audio → UDP stream → Alienware. Receives TTS audio stream back. No LLM, no processing — pure I/O node. Falls back to Telegram if Tailscale drops.

**Phase:** 7.

### Extension 3: AEGIS Terminal Integration

```python
class AEGISIntegrationTool:
    name = "aegis_market_check"
    risk_level = 0  # Reading only. Execution is Level 3.

    async def execute(self, action: str, **kwargs):
        if action == "scan":
            # Fetch market data, compare against AEGIS strategy params, return signals
            ...
        elif action == "execute_trade":
            # LEVEL 3: send Telegram "BTC at $X, RSI oversold. Execute $500 buy?"
            # Wait for explicit "Yes"
            ...
```

**Phase:** 8.

### Extension 4: AUCTORUM Fleet Manager

`auctorum_fleet(action)` — `health_check`, `restart_agent`, `generate_invoice`, `margin_report`. SSH into Auctorum PC, manage the per-client Docker containers running each WhatsApp agent.

**Phase:** 6.

### Extension 5: Sleep Cycle (Memory Consolidation)

```python
class SleepCycleDaemon:
    """Runs at 4:00 AM. Kee's REM sleep.

    1. Analyze today's audit log for patterns
    2. Summarize conversations into episodic memory
    3. Detect new axioms about user behavior
    4. Update user_behavior.json
    5. Propose identity/soul updates (if warranted)
    6. Clean up temporary files and stale data
    7. Generate daily digest for morning delivery (8:30 AM)
    """
```

**Phase:** 5.

### Extension 6: Full-Duplex Audio + Interruption

Kee can be interrupted while speaking. TTS output runs in a separate thread; wake-word detection continues during playback. On detection during playback: stop TTS immediately, capture new audio, process the interruption.

```python
class FullDuplexAudio:
    def __init__(self):
        self.is_speaking = False
        self.tts_thread = None
        self.cancel_speech = threading.Event()

    async def speak(self, text: str):
        self.is_speaking = True
        self.cancel_speech.clear()
        audio = await self.piper.synthesize(text)
        self.tts_thread = threading.Thread(target=self._play_audio, args=(audio,))
        self.tts_thread.start()

    def interrupt(self):
        self.cancel_speech.set()
        self.is_speaking = False

    def _play_audio(self, audio):
        stream = sd.OutputStream(samplerate=22050, channels=1)
        stream.start()
        chunk = 2205  # 100ms at 22050Hz
        for i in range(0, len(audio), chunk):
            if self.cancel_speech.is_set():
                break
            stream.write(audio[i:i+chunk])
        stream.stop()
        self.is_speaking = False
```

**Phase:** 6.

### Extension 7: Self-Healing Infrastructure

```python
class SelfHealing:
    """Runs every 60s. Monitors:
    - Auctorum PC connectivity (Tailscale)
    - ChromaDB availability
    - Ollama health
    - Disk space
    - System temperature

    Actions: ping → SSH restart → Wake-on-LAN. ChromaDB unreachable →
    failover to local SQLite FTS. Disk low → notify the agent.
    """
```

**Phase:** 5.

### Extension 8: IoT / Physical Agency (Home Assistant)

`home_control(action)` — `set_lights`, `wake_on_lan`, `server_power`. Talks to Home Assistant's REST API or MQTT broker. Adjusts lighting based on time/fatigue, wakes sleeping PCs, controls smart plugs.

**Phase:** 8.

---

## PART V — `create_tool` SAFETY FRAMEWORK

This is the complete hardened lifecycle of a Kee-created tool. (The Phase 0 build already implements this — sandbox + version + probation + audit.)

```
Kee decides it needs a new tool
    │
    ▼
Code Generation (LLM writes Python)
    │
    ▼
Sandbox Test (subprocess, /tmp, 10s timeout)
    │
    ├── FAIL → Reject, log error, try once more
    │
    └── PASS ↓
    │
    ▼
Version Control (git commit in kee-vault/_kee/tools/)
    │
    ▼
Register as PROBATIONARY (7-day trial)
    │
    ▼
WhatsApp/Telegram Report to Armando
    │
    ▼
Usage Tracking (use_count, last_used, error_rate)
    │
    ├── Used 3+ times, error_rate < 10% → PROMOTE to permanent
    ├── Unused for 7 days → ARCHIVE (move to archive/, unregister)
    └── Error rate > 30% → QUARANTINE (disable, alert Armando)
    │
    ▼
Garbage Collection (weekly by Sleep Cycle)
    └── Remove archived tools older than 30 days
```

---

## PART VI — COMPLETE PHASED ROADMAP

### Phase 0 — Foundation (Week 1)
Platform: Windows 11, `D:\Kee`

- Project structure, `pyproject.toml`, git init
- Ollama startup wait script
- `OllamaClient` with tool calling (Qwen3.5 9B)
- Basic agent loop (ReAct, no tools yet)
- Terminal surface (Rich library)
- SQLite schema (all tables)
- Scheduler with priority queue + locks
- VRAM Arbiter
- Identity files: `identity.md`, `soul.md`, `user.md`
- VRAM optimizations: `num_ctx=4096`, `q4_0` KV cache, flash attention

### Phase 1 — Core Agent + Tools (Week 2)
- Tool registry with dynamic loading
- 5 builtin tools: shell, files, web_search, memory_search, system_status
- Post-action verification loop (pre/post state capture)
- Audit logger with full context
- Vault structure (`~/kee-vault/`) created
- File system watcher (watchdog)
- Basic conversation history in SQLite

### Phase 2 — Voice Pipeline (Week 3)
- openWakeWord: train "Kee" wake word (~50 recordings)
- faster-whisper: CPU-only mode, medium model
- Piper TTS: Spanish MX voice
- Full pipeline: wake → listen → think → speak
- Voice runs as background daemon
- Whisper force-CPU configuration (no VRAM contention)

### Phase 3 — Memory + Perception + Timing (Week 4–5)
- ChromaDB on Auctorum PC (Docker container)
- Embedding pipeline: vault → chunk → embed → store
- Reranker (`bge-reranker-base`) on Auctorum CPU
- Surgical RAG in agent loop
- Passive awareness daemon (window tracking)
- Temporal intelligence (time-aware behavior)
- Goal inference engine
- Heartbeat daemon (every 5 min)
- Notification interceptor (D-Bus on Linux / toast on Windows)
- Syncthing between Alienware ↔ Auctorum

### Phase 4 — Power Tools + World Model (Week 6–7)
- Claude Code orchestrator tool (headless `-p` mode via the user's Pro/Max subscription — Camino A)
- Vercel deployment tool
- GitHub tool (`gh` CLI)
- Browser automation (Playwright)
- App orchestrator (open/focus apps)
- Computer Use tool (screenshot → vision on worker)
- World Model (causal graph in SQLite)
- Multi-step planner
- Impact assessment system
- Self-performance metrics (tool error rates)
- `create_tool` with full safety framework

### Phase 5 — Cognitive Evolution (Week 8–10)
- Sleep Cycle daemon (4 AM consolidation)
- User behavior model (pattern learning)
- Dynamic identity evolution (versioned, controlled)
- Internal economy (cost/value scoring)
- Dynamic autonomy threshold
- Self-healing infrastructure
- Google Calendar integration
- Gmail integration (read-only)
- Telegram bot surface
- WhatsApp bridge via Baileys
- Tool garbage collector

### Phase 6 — Full Jarvis (Week 11–14)
- Full-duplex audio (interrupt Kee while speaking)
- SvelteKit dashboard (nervous system view)
- AUCTORUM fleet manager
- Spotify API integration
- BLE presence detection (optional)
- QLoRA fine-tuning pipeline on Auctorum PC (or cloud GPU)
- Training dataset creation (200+ examples)
- LoRA personality training

### Phase 7 — Self-Evolution (Week 15+)
- Self-editing codebase daemon
- Termux mobile edge client
- Speaker diarization (who's talking)
- Advanced sound detection (YAMNet)

### Phase 8 — Extended Ecosystem (Ongoing)
- AEGIS Terminal market integration
- IoT / Home Assistant
- Biometric telemetry (smartwatch data)
- Wake-on-LAN for infrastructure management
- Multi-language voice (Spanish/English/German switching)

---

## PART VII — DEPENDENCY GRAPH

```
Phase 0 (Foundation)
    │
    ├──→ Phase 1 (Tools)
    │       │
    │       ├──→ Phase 2 (Voice)
    │       │       │
    │       │       └──→ Phase 6 (Full-duplex, dashboard)
    │       │
    │       ├──→ Phase 3 (Memory + Perception)
    │       │       │
    │       │       ├──→ Phase 4 (Power Tools)
    │       │       │       │
    │       │       │       └──→ Phase 5 (Cognitive Evolution)
    │       │       │               │
    │       │       │               └──→ Phase 7 (Self-Evolution)
    │       │       │
    │       │       └──→ Phase 5 (Sleep Cycle needs memory)
    │       │
    │       └──→ Phase 8 (AEGIS, IoT — independent, needs tools)
    │
    └──→ Phase 6 (Fine-tuning — independent path, needs Phase 1)
```

---

## PART VIII — CRITICAL SUCCESS METRICS

How to know each phase is ACTUALLY working:

| Phase | Success Metric | Test |
|-------|---------------|------|
| 0 | Agent responds to text input via terminal | Type a question, get a coherent answer |
| 1 | Agent uses tools correctly | "What files are in ~/kee?" → `shell_exec ls` |
| 2 | Voice pipeline end-to-end | Say "Kee, what time is it?" → hear answer |
| 3 | RAG returns relevant context | Ask about a project → correct info from vault |
| 4 | Claude Code builds and deploys | "Build a hello world page on Vercel" → live URL |
| 5 | Sleep cycle generates useful digest | Morning message with yesterday's summary |
| 6 | Interrupt Kee mid-sentence | Say "wait" while Kee speaks → it stops |
| 7 | Kee optimizes its own code | Commit log shows self-improvement |
| 8 | Market alert received | Telegram notification about price movement |

### Hardware Health Metrics (Monitor Always):

| Metric | Healthy | Warning | Critical |
|--------|---------|---------|----------|
| VRAM free | >500MB | 200–500MB | <200MB |
| RAM free | >4GB | 2–4GB | <2GB |
| CPU avg | <60% | 60–85% | >85% sustained |
| Inference speed | >15 tok/s | 8–15 tok/s | <8 tok/s |
| Worker ping | <50ms | 50–200ms | >200ms or timeout |

---

## STATUS DELTA — WHAT'S BUILT vs v2 PLAN (as of 2026-05-02)

A separate file tracks the live delta between v2's roadmap and what is actually shipped. See [`STATUS.md`](../STATUS.md) at the repo root.

---

*This document is the complete, definitive technical roadmap for Kee v2.0. It incorporates every hardware constraint, every cognitive gap, every advanced extension, and every safety mechanism discussed across all prior documents. It is designed to be executed incrementally — each phase produces a working system, and each subsequent phase adds capability without breaking what came before.*

*Kee is not a powerful system. Kee is an efficiently limited system. And that is exactly what makes it viable.*

*From scratch. Ours and no one else's.*
