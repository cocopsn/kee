"""Terminal surface — Rich-based interactive REPL.

The primary dev/debug surface for Kee. Voice (Phase 2) and the web dashboard
(Phase 4) layer on later; this is the always-available fallback.

Slash commands:
  /exit, /quit       quit (and summarize the last conversation if it exists)
  /help              command listing
  /status            system + Ollama + Embedder + ChromaDB health
  /tools             list registered tools
  /audit [n]         show last n audit entries (default 10)
  /reload            re-load custom tools from vault/_kee/tools/
  /index             re-index the entire vault (uses VaultIndexer)
  /summarize         summarize the most recent conversation
  /history [n]       list the n most recent conversations (default 10)
"""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table

from kee.core.agent import KeeAgent
from kee.distributed.chroma_client import ChromaClient
from kee.distributed.embedder import Embedder

logger = logging.getLogger(__name__)
console = Console()


def _quiet_terminal_logs() -> None:
    """Keep recoverable model/router noise out of the interactive prompt."""
    for name in (
        "kee.core.router",
        "kee.core.ollama_client",
        "httpx",
        "httpcore",
        "ollama",
    ):
        logging.getLogger(name).setLevel(logging.ERROR)


def _model_status_text(agent: KeeAgent) -> str:
    llm = getattr(agent, "llm", None)
    registry = getattr(agent, "registry", None)
    tools = getattr(registry, "tools", {}) or {}
    model = getattr(llm, "model", "?")
    host = getattr(llm, "host", "?")
    return (
        "Estoy corriendo como Kee sobre este LLM local:\n\n"
        f"- Modelo principal: `{model}`\n"
        f"- Ollama host: `{host}`\n"
        f"- Tools registradas: `{len(tools)}`"
    )


def _local_model_answer(agent: KeeAgent, line: str) -> str | None:
    text = re.sub(r"[¿?¡!.,:;]+", " ", (line or "").lower())
    if not re.search(r"\b(modelo|model|llm)\b", text):
        return None
    if not re.search(r"\b(actual|usas|usa|eres|soy|running|current|principal)\b", text):
        return None
    return _model_status_text(agent)


# ── Banner / help ─────────────────────────────────────────────────────────
def _print_banner() -> None:
    console.print(
        Panel.fit(
            "[bold cyan]KEE[/]  [dim]v0.1.0 — sovereign agent[/]\n"
            "[dim]Type your message. /help for commands, /exit to quit.[/]",
            border_style="cyan",
        )
    )


def _print_help() -> None:
    table = Table(title="Slash commands", show_header=True, header_style="bold cyan")
    table.add_column("Command")
    table.add_column("Description")
    table.add_row("/exit, /quit", "Quit the REPL (summarizes the conversation first)")
    table.add_row("/help", "Show this help")
    table.add_row("/status", "Print system + Ollama + Embedder + ChromaDB status")
    table.add_row("/model", "Show the active local model without calling the LLM")
    table.add_row("/tools", "List registered tools")
    table.add_row("/audit [n]", "Show last n audit entries (default 10)")
    table.add_row("/reload", "Reload custom tools from vault/_kee/tools/")
    table.add_row("/index", "Re-index the entire vault (chunk → embed → store)")
    table.add_row("/summarize", "Summarize the most recent conversation")
    table.add_row("/reset", "Drop the current conversation; the next turn starts fresh")
    table.add_row("/history [n]", "List the n most recent conversations (default 10)")
    table.add_row("/heartbeat [n]", "Show the last n heartbeat snapshots (default 5)")
    table.add_row("/digest", "Read today's morning digest (Sleep Cycle output)")
    table.add_row("/proposals", "List pending identity-update proposals from Sleep Cycle")
    table.add_row("/sleep", "Run a Sleep Cycle pass right now (don't wait for 4 AM)")
    console.print(table)


# ── Status ────────────────────────────────────────────────────────────────
async def _cmd_status(agent: KeeAgent) -> None:
    healthy = await agent.llm.health()
    embedder_health = await Embedder().health()
    chroma_ok = await ChromaClient().health()

    table = Table(show_header=False, box=None)
    table.add_row("Ollama host", agent.llm.host)
    table.add_row("Model", agent.llm.model)
    table.add_row("Model ready", "[green]yes[/]" if healthy else "[red]no[/]")
    table.add_row("Tools registered", str(len(agent.registry.tools)))
    table.add_row(
        "Embedder",
        f"[green]{embedder_health['host']}[/]" if embedder_health["ok"]
        else "[yellow]offline[/]",
    )
    table.add_row("ChromaDB", "[green]online[/]" if chroma_ok else "[yellow]offline[/]")
    console.print(Panel(table, title="Kee status", border_style="cyan"))


def _cmd_tools(agent: KeeAgent) -> None:
    table = Table(title="Registered tools", show_header=True, header_style="bold cyan")
    table.add_column("Name")
    table.add_column("Risk", justify="center")
    table.add_column("Description")
    for t in agent.registry.tools.values():
        first_line = t.description.strip().split("\n")[0]
        table.add_row(t.name, str(t.risk_level), first_line[:80])
    console.print(table)


def _cmd_audit(agent: KeeAgent, n: int = 10) -> None:
    rows = agent.audit.recent(limit=n)
    table = Table(title=f"Last {n} audit entries", show_header=True, header_style="bold cyan")
    table.add_column("ID", justify="right")
    table.add_column("Time")
    table.add_column("Action")
    table.add_column("Tool")
    table.add_column("OK", justify="center")
    for row in rows:
        ok = "✓" if row.get("success") else "✗"
        table.add_row(
            str(row["id"]),
            str(row["timestamp"])[:19],
            row["action"],
            row["tool_name"],
            ok,
        )
    console.print(table)


def _cmd_reload(agent: KeeAgent) -> None:
    agent.registry.load_custom()
    console.print(f"[green]Reloaded.[/] {len(agent.registry.tools)} tools active.")


async def _cmd_index(agent: KeeAgent) -> None:
    with console.status("[dim]indexing vault…[/]", spinner="dots"):
        summary = await agent.memory.indexer.index_vault()
    if summary.get("offline"):
        console.print("[yellow]Indexer offline (Embedder or ChromaDB missing).[/]")
    else:
        console.print(
            f"[green]Indexed[/] {summary['indexed']} files, "
            f"skipped {summary['skipped']}."
        )


async def _cmd_summarize(agent: KeeAgent, state: dict[str, Any]) -> None:
    conv = state.get("conversation")
    if conv is None:
        console.print("[yellow]No conversation in this session yet.[/]")
        return
    with console.status("[dim]summarizing…[/]", spinner="dots"):
        summary = await agent.memory.summarize_conversation(
            conv.id, agent.llm, force=True,
        )
    if summary:
        console.print(Panel(Markdown(summary), title="summary", border_style="cyan"))
    else:
        console.print("[yellow]Nothing to summarize.[/]")


def _cmd_reset(state: dict[str, Any]) -> None:
    state["conversation"] = None
    console.print("[dim]Conversation reset. The next message starts fresh.[/]")


def _cmd_digest(agent: KeeAgent) -> None:
    from datetime import date
    from kee.config import settings as _s
    p = _s.vault_dir / "_kee" / "daily" / f"{date.today().isoformat()}.md"
    if not p.exists():
        console.print(
            f"[yellow]No digest for today yet.[/] Run [bold]/sleep[/] to generate one, "
            f"or wait for the 04:00 daemon."
        )
        return
    console.print(Panel(Markdown(p.read_text(encoding="utf-8")),
                        border_style="cyan", title=str(p.name)))


def _cmd_proposals(agent: KeeAgent) -> None:
    from kee.config import settings as _s
    d = _s.vault_dir / "_kee" / "identity_proposals"
    if not d.exists():
        console.print("[dim]No proposals directory yet.[/]")
        return
    files = sorted(d.glob("*.md"))
    if not files:
        console.print("[dim]No proposals on file.[/]")
        return
    table = Table(title="Identity proposals", show_header=True, header_style="bold cyan")
    table.add_column("Date")
    table.add_column("Path")
    table.add_column("Size")
    for f in files[-10:]:
        table.add_row(f.stem, str(f), f"{f.stat().st_size}B")
    console.print(table)
    if files:
        console.print(
            f"\n[dim]Read with: cat \"{files[-1]}\" — proposals are NEVER auto-applied.[/]"
        )


async def _cmd_sleep(agent: KeeAgent) -> None:
    from kee.cognition.sleep_cycle import SleepCycleDaemon
    sc = getattr(agent, "sleep_cycle", None) or SleepCycleDaemon(
        memory=agent.memory, llm=agent.llm, audit=agent.audit,
    )
    with console.status("[dim]running sleep cycle (~30-90s)…[/]", spinner="dots"):
        report = await sc.run_once()
    table = Table(show_header=False, box=None)
    table.add_row("summarized", str(report.summarized))
    table.add_row("axioms", str(len(report.axioms)))
    table.add_row("proposal", report.proposal_path or "—")
    table.add_row("digest", report.digest_path or "—")
    table.add_row("pruned", str(report.pruned_rows))
    if report.errors:
        table.add_row("errors", "; ".join(report.errors))
    console.print(Panel(table, title="sleep cycle", border_style="cyan"))


def _cmd_heartbeat(agent: KeeAgent, n: int = 5) -> None:
    hb = getattr(agent, "heartbeat", None)
    if hb is None:
        console.print(
            "[yellow]No heartbeat daemon attached. "
            "Restart with --heartbeat to enable.[/]"
        )
        return
    snaps = hb.recent(n)
    if not snaps:
        console.print("[dim]No beats recorded yet.[/]")
        return
    table = Table(title=f"Last {len(snaps)} heartbeat beats", show_header=True,
                  header_style="bold cyan")
    table.add_column("Time")
    table.add_column("Mode")
    table.add_column("Fired")
    table.add_column("Suppressed")
    table.add_column("Summary")
    for s in snaps:
        # Pull the most interesting summary from the snapshot.
        summary_bits = []
        sysh = s.checks.get("system_health", {})
        if "vram" in sysh:
            v = sysh["vram"]
            summary_bits.append(
                f"VRAM {v.get('free_mb','?')}MB/{v.get('status','?')}"
            )
        if sysh:
            summary_bits.append(f"CPU {sysh.get('cpu_pct','?')}%")
            summary_bits.append(f"disk {sysh.get('disk_free_gb','?')}GB")
        win = s.checks.get("active_window", {}).get("window", {})
        if win.get("title"):
            summary_bits.append(f"win:{win['title'][:24]}")
        table.add_row(
            s.timestamp.split("T")[1] if "T" in s.timestamp else s.timestamp,
            s.mode,
            ",".join(s.actionables_fired) or "—",
            ",".join(s.actionables_suppressed) or "—",
            " · ".join(summary_bits)[:80],
        )
    console.print(table)


def _cmd_history(agent: KeeAgent, n: int = 10) -> None:
    rows = agent.memory.recent_conversations(limit=n)
    table = Table(title=f"Last {n} conversations", show_header=True, header_style="bold cyan")
    table.add_column("Started")
    table.add_column("Source")
    table.add_column("Summary")
    for r in rows:
        table.add_row(
            str(r["started_at"])[:19],
            r["source"],
            (r.get("summary") or "—")[:90],
        )
    console.print(table)


# ── Command dispatcher ────────────────────────────────────────────────────
async def _handle_command(
    agent: KeeAgent,
    line: str,
    state: dict[str, Any],
) -> bool:
    parts = line.strip().split()
    if not parts or not parts[0].startswith("/"):
        return False
    cmd = parts[0].lower()

    if cmd in ("/exit", "/quit"):
        raise SystemExit(0)
    if cmd == "/help":
        _print_help()
        return True
    if cmd == "/status":
        await _cmd_status(agent)
        return True
    if cmd == "/model":
        console.print(Panel(Markdown(_model_status_text(agent)), border_style="cyan", title="kee"))
        return True
    if cmd == "/tools":
        _cmd_tools(agent)
        return True
    if cmd == "/audit":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        _cmd_audit(agent, n)
        return True
    if cmd == "/reload":
        _cmd_reload(agent)
        return True
    if cmd == "/index":
        await _cmd_index(agent)
        return True
    if cmd == "/summarize":
        await _cmd_summarize(agent, state)
        return True
    if cmd == "/reset":
        _cmd_reset(state)
        return True
    if cmd == "/history":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        _cmd_history(agent, n)
        return True
    if cmd == "/heartbeat":
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
        _cmd_heartbeat(agent, n)
        return True
    if cmd == "/digest":
        _cmd_digest(agent)
        return True
    if cmd == "/proposals":
        _cmd_proposals(agent)
        return True
    if cmd == "/sleep":
        await _cmd_sleep(agent)
        return True

    console.print(f"[yellow]Unknown command:[/] {cmd}")
    return True


# ── Main loop ─────────────────────────────────────────────────────────────
async def run(agent: KeeAgent) -> None:
    _quiet_terminal_logs()
    agent.bootstrap()
    _print_banner()
    await _cmd_status(agent)

    state: dict[str, Any] = {"conversation": None}
    loop = asyncio.get_event_loop()

    try:
        while True:
            try:
                line = await loop.run_in_executor(
                    None, console.input, "[bold green]coco[/] ❯ ",
                )
            except (KeyboardInterrupt, EOFError):
                break

            line = line.strip()
            if not line:
                continue

            try:
                if await _handle_command(agent, line, state):
                    continue
            except SystemExit:
                break

            local_answer = _local_model_answer(agent, line)
            if local_answer is not None:
                console.print(Panel(Markdown(local_answer), border_style="cyan", title="kee"))
                continue

            try:
                with console.status("[dim]thinking…[/]", spinner="dots"):
                    response, conv = await agent.process(
                        line, source="terminal", state=state["conversation"],
                    )
                    state["conversation"] = conv
            except Exception as e:
                # Agent-loop failures must NEVER kill the REPL. Surface a
                # one-line summary; the full traceback lives in the log.
                logger.exception("Agent.process raised")
                console.print(Panel(
                    f"[red]Agent error:[/] {type(e).__name__}: {e}\n"
                    "[dim]Conversation state preserved. Try again or /reset.[/]",
                    border_style="red", title="kee",
                ))
                continue

            console.print(Panel(Markdown(response), border_style="cyan", title="kee"))

    finally:
        # Best-effort summarize on exit so the conversations table doesn't
        # accumulate raw transcripts with no summary.
        conv = state.get("conversation")
        if conv is not None:
            try:
                await asyncio.wait_for(
                    agent.memory.summarize_conversation(conv.id, agent.llm),
                    timeout=20,
                )
            except (asyncio.TimeoutError, Exception) as e:  # noqa: BLE001
                logger.debug("Exit-summarize skipped: %s", e)
        console.print("[dim]bye[/]")
