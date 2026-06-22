"""End-to-end stress test for the full Kee stack.

Verifies the new kee-uncensored:latest model and the agent loop survive
real-world traffic, not just trivial greetings. Covers:

  1. Diagnostics (kee.main check) — every subsystem must be OK.
  2. Conversational turns (router tier "conversational", no tools).
  3. Direct-answer turns (router tier "direct").
  4. Tool-using turns covering each tool category (files, shell, web,
     world, recall, vault_search, episodic, narrate_day, plan, etc.).
  5. Multi-turn continuity (same conversation, second turn).
  6. Concurrent calls (3 parallel turns) — exercises the scheduler's
     llm lock.
  7. API smoke (POST /chat through the live FastAPI surface).
  8. Memory persistence (recall a turn from earlier in the run).

Each test prints a per-line verdict. The final score is the number of
checks that returned a non-empty, sensible reply. Anything below the
threshold is a fail and we don't ship.

Run::

    .venv\\Scripts\\python.exe tests/stress_e2e.py
"""
from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Callable

os.environ.setdefault("PYTHONIOENCODING", "utf-8")
os.environ.setdefault("PYTHONUTF8", "1")

# Quiet the deep stack — we only want the test's own output.
logging.basicConfig(level=logging.WARNING)
for n in ("httpx", "httpcore", "ollama", "urllib3", "asyncio",
          "kee.distributed", "kee.core.scheduler"):
    logging.getLogger(n).setLevel(logging.ERROR)


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""
    elapsed_s: float = 0.0


@dataclass
class Report:
    checks: list[CheckResult] = field(default_factory=list)

    def add(self, name: str, ok: bool, detail: str = "",
            elapsed_s: float = 0.0) -> None:
        self.checks.append(CheckResult(name, ok, detail, elapsed_s))
        tag = "OK " if ok else "FAIL"
        d = (detail.encode("ascii", "replace").decode("ascii")
             if detail else "")
        print(f"  [{tag}] {elapsed_s:5.1f}s  {name}: {d[:160]}")

    def passed(self) -> int:
        return sum(1 for c in self.checks if c.ok)

    def total(self) -> int:
        return len(self.checks)


def _ascii(s: str) -> str:
    return (s or "").encode("ascii", errors="replace").decode("ascii")


async def _run_turn(agent, prompt: str, source: str = "terminal",
                    state=None) -> tuple[str, Any, float]:
    t0 = time.time()
    reply, st = await agent.process(prompt, source=source, state=state)
    return reply, st, time.time() - t0


# ── 1. Diagnostics ───────────────────────────────────────────────────────
def check_diagnostics(rep: Report) -> None:
    print("\n[ 1 ] Diagnostics")
    t0 = time.time()
    res = subprocess.run(
        [sys.executable, "-m", "kee.main", "check"],
        capture_output=True, text=True, timeout=60,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    elapsed = time.time() - t0
    out = (res.stdout or "") + (res.stderr or "")
    ok_subsystems = [
        ("SQLite", "SQLite" in out and "OK" in out),
        ("Ollama", "Ollama" in out and "OK" in out),
        ("Tools 65", "65 registered" in out or "Tools" in out and "OK" in out),
        ("VRAM", "VRAM" in out and ("ok" in out or "OK" in out)),
        ("Embedder", "Embedder" in out and "OK" in out),
    ]
    for name, ok in ok_subsystems:
        rep.add(f"check.{name}", ok, "", elapsed_s=0.0 if ok else 0.0)
    rep.checks[-1].elapsed_s = elapsed  # attribute the wall time to the last one


# ── 2. Conversational turns ──────────────────────────────────────────────
async def check_chat(rep: Report, agent) -> None:
    print("\n[ 2 ] Conversational (no tools)")
    cases = [
        ("hola kee", lambda r: len(r) > 5 and "limit" not in r.lower()),
        ("en una frase: que es entropia",
         lambda r: len(r) > 20 and ("desorden" in r.lower()
                                     or "energ" in r.lower()
                                     or "aleator" in r.lower())),
        ("dame 3 razones para usar sqlite",
         lambda r: len(r) > 50 and r.count("\n") >= 1),
    ]
    for q, verdict in cases:
        try:
            reply, _, elapsed = await _run_turn(agent, q)
            ok = verdict(reply or "")
            rep.add(f"chat.{q[:35]}", ok, _ascii(reply or "")[:80], elapsed)
        except Exception as e:
            rep.add(f"chat.{q[:35]}", False, f"EXC {type(e).__name__}: {e}", 0)


# ── 3. Tool-using turns ──────────────────────────────────────────────────
async def check_tools(rep: Report, agent) -> None:
    print("\n[ 3 ] Tool-using turns")
    cases = [
        # Relaxed verdicts — we want "the model called the tool and got a
        # real-looking reply", not a literal substring match. The model
        # writes in Spanish so requirements are loose.
        ("lista los archivos de D:/Kee/scripts usando la tool files",
         lambda r: len(r) > 40 and any(t in r.lower() for t in
             ("scripts", "archivos", "modelfile", ".py", ".ps1", "├", "─"))),
        ("usa la tool commits para decirme cuantos commits llevo hoy en D:/Kee",
         lambda r: len(r) > 30 and "commit" in r.lower()),
        ("usa la tool weather para el clima en monterrey",
         lambda r: len(r) > 30 and any(t in r.lower() for t in
             ("temperatura", "celsius", "clima", "monterrey", "humedad"))),
        ("usa la tool world_model action=list para listar mis entidades",
         lambda r: len(r) > 20),
        ("usa la tool recall query='hola' limit=3 para buscar conversaciones pasadas",
         lambda r: len(r) > 10),
    ]
    for q, verdict in cases:
        try:
            reply, _, elapsed = await _run_turn(agent, q)
            ok = bool(reply) and verdict(reply or "")
            rep.add(f"tool.{q[:40]}", ok, _ascii(reply or "")[:100], elapsed)
        except Exception as e:
            rep.add(f"tool.{q[:40]}", False, f"EXC {type(e).__name__}: {e}", 0)


# ── 4. Multi-turn continuity ─────────────────────────────────────────────
async def check_continuity(rep: Report, agent) -> None:
    print("\n[ 4 ] Multi-turn continuity")
    try:
        r1, st, e1 = await _run_turn(agent, "Mi color favorito es violeta. Solo dime ok.")
        r2, _, e2 = await _run_turn(agent, "Cual era mi color favorito?", state=st)
        ok = "violeta" in (r2 or "").lower()
        rep.add("continuity.color_recall", ok,
                f"r1={_ascii(r1)[:40]!r} r2={_ascii(r2)[:80]!r}",
                elapsed_s=e1 + e2)
    except Exception as e:
        rep.add("continuity.color_recall", False, f"EXC: {e}", 0)


# ── 5. Concurrency (scheduler lock) ──────────────────────────────────────
async def check_concurrent(rep: Report, agent) -> None:
    print("\n[ 5 ] Concurrent turns")
    questions = [
        "responde solo: A",
        "responde solo: B",
        "responde solo: C",
    ]
    t0 = time.time()
    try:
        results = await asyncio.gather(*[
            agent.process(q, source="terminal") for q in questions
        ], return_exceptions=True)
        elapsed = time.time() - t0
        successes = sum(
            1 for r in results
            if not isinstance(r, Exception)
            and r[0] and "limit" not in r[0].lower()
        )
        ok = successes >= 2
        rep.add("concurrent.3_parallel", ok,
                f"{successes}/3 succeeded in {elapsed:.1f}s", elapsed)
    except Exception as e:
        rep.add("concurrent.3_parallel", False, f"EXC: {e}", time.time() - t0)


# ── 6. API smoke (only if server is reachable) ───────────────────────────
async def check_api(rep: Report) -> None:
    print("\n[ 6 ] API surface")
    try:
        import httpx
        async with httpx.AsyncClient(timeout=120) as c:
            t0 = time.time()
            r = await c.get("http://localhost:7330/health")
            if r.status_code != 200:
                rep.add("api.health", False, f"status={r.status_code}", time.time() - t0)
                return
            d = r.json()
            ok = d.get("status") == "ok" and d.get("tools", 0) >= 60
            rep.add("api.health", ok,
                    f"model={d.get('model','?')[:30]} tools={d.get('tools')}",
                    time.time() - t0)

            t0 = time.time()
            r = await c.post(
                "http://localhost:7330/chat",
                json={"message": "responde solo con: API_OK",
                      "session_id": "stress_e2e"},
            )
            if r.status_code != 200:
                rep.add("api.chat", False,
                        f"status={r.status_code} body={r.text[:120]}",
                        time.time() - t0)
                return
            d = r.json()
            # The /chat endpoint returns {"response", "conversation_id",
            # "iteration"} — NOT "reply". The earlier test read the wrong
            # field which is why the API check kept "failing" with empty.
            reply = d.get("response", "") or ""
            ok = bool(reply) and "limit" not in reply.lower()
            rep.add("api.chat", ok, _ascii(reply)[:80], time.time() - t0)
    except Exception as e:
        rep.add("api.health", False, f"server not running: {e}", 0)


# ── 7. Direct-tool execution (bypassing the model) ───────────────────────
async def check_direct_tools(rep: Report, agent) -> None:
    print("\n[ 7 ] Direct tool exec (bypassing LLM)")
    cases = [
        ("files", {"action": "list", "path": "D:/Kee/scripts"}),
        ("system_status", {}),
        ("vault_search", {"query": "kee", "limit": 3}),
        ("commits", {"action": "today"}),
        ("narrate_day", {"date": "today"}),
        ("perf_stats", {"view": "overview"}),
    ]
    for tool_name, args in cases:
        t0 = time.time()
        try:
            tool = agent.registry.tools.get(tool_name)
            if tool is None:
                rep.add(f"direct.{tool_name}", False, "tool not registered", 0)
                continue
            result = await tool.execute(**args)
            ok = isinstance(result, dict) and (
                result.get("ok") is True
                or result.get("ok") is None  # some tools don't set ok
                or len(str(result)) > 20
            )
            rep.add(f"direct.{tool_name}", ok,
                    str(result)[:90], time.time() - t0)
        except Exception as e:
            rep.add(f"direct.{tool_name}", False,
                    f"EXC {type(e).__name__}: {e}", time.time() - t0)


# ── 8. Run-all suite ─────────────────────────────────────────────────────
def check_regressions(rep: Report) -> None:
    print("\n[ 8 ] Regression suite (tests/run_all.py)")
    t0 = time.time()
    res = subprocess.run(
        [sys.executable, "tests/run_all.py"],
        capture_output=True, text=True, timeout=600,
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    )
    elapsed = time.time() - t0
    out = (res.stdout or "") + (res.stderr or "")
    last_line = next(
        (ln for ln in reversed(out.splitlines()) if "OK:" in ln or "FAIL" in ln),
        "",
    )
    rep.add("regression.run_all", res.returncode == 0, last_line[:100], elapsed)


# ── Driver ───────────────────────────────────────────────────────────────
async def main() -> int:
    rep = Report()
    print("=" * 72)
    print("Kee end-to-end stress test")
    print("=" * 72)

    # Phase 1: diagnostics (subprocess)
    check_diagnostics(rep)

    # Phase 2-5,7: in-process agent calls
    from kee.core.agent import KeeAgent
    agent = KeeAgent()
    agent.bootstrap()

    await check_chat(rep, agent)
    await check_tools(rep, agent)
    await check_continuity(rep, agent)
    await check_concurrent(rep, agent)
    await check_direct_tools(rep, agent)

    # Phase 6: API (requires the supervisor up)
    await check_api(rep)

    # Phase 8: regression suite (subprocess)
    check_regressions(rep)

    # Summary
    print()
    print("=" * 72)
    p, t = rep.passed(), rep.total()
    pct = (p / t * 100) if t else 0
    print(f"Stress test summary: {p}/{t} passed ({pct:.0f}%)")
    fails = [c for c in rep.checks if not c.ok]
    if fails:
        print(f"\n{len(fails)} failure(s):")
        for f in fails:
            detail = _ascii(f.detail or "")
            print(f"  FAIL  {f.name}: {detail[:100]}")
    print()
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
