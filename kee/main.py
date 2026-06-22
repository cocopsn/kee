"""Kee entry point.

Usage:
    python -m kee.main                       → terminal REPL (default)
    python -m kee.main terminal              → terminal REPL
    python -m kee.main check                 → run diagnostics and exit
    python -m kee.main gc                    → run a single Tool GC sweep
    python -m kee.main index                 → re-index the entire vault
    python -m kee.main watch                 → run the vault watcher in foreground
    python -m kee.main heartbeat             → run the heartbeat daemon in foreground

    Add `--watch` to terminal mode to spin up the watcher alongside the REPL.
    Add `--heartbeat` to terminal mode to spin up the heartbeat alongside.
    Add `--heartbeat-interval N` to change the beat interval (default 300s).

Future surfaces (voice, dashboard, telegram) plug in here.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys

from kee.config import settings, setup_logging
from kee.core import db
from kee.core.agent import KeeAgent
from kee.core.ollama_client import OllamaUnavailable
from kee.core.tool_gc import ToolGarbageCollector

logger = logging.getLogger(__name__)


# ── Diagnostics ───────────────────────────────────────────────────────────
async def _check() -> int:
    from rich.console import Console
    from rich.table import Table

    from kee.distributed.chroma_client import ChromaClient
    from kee.distributed.embedder import Embedder

    console = Console()
    settings.ensure_dirs()

    table = Table(title="Kee diagnostics", show_header=True, header_style="bold cyan")
    table.add_column("Check")
    table.add_column("Status")
    table.add_column("Details")

    # SQLite
    try:
        db.get_connection()
        table.add_row("SQLite", "[green]OK[/]", str(settings.db_path))
    except Exception as e:
        table.add_row("SQLite", "[red]FAIL[/]", str(e))

    # Identity files
    for label, path in (
        ("identity.md", settings.identity_path),
        ("soul.md", settings.soul_path),
        ("user.md", settings.user_path),
    ):
        if path.exists():
            table.add_row(label, "[green]OK[/]", str(path))
        else:
            table.add_row(label, "[yellow]missing[/]", str(path))

    # Ollama
    agent = KeeAgent()
    try:
        await asyncio.wait_for(agent.llm.wait_for_ready(timeout_s=3), timeout=4)
        ollama_ok = await agent.llm.health()
    except (asyncio.TimeoutError, OllamaUnavailable):
        ollama_ok = False
    table.add_row(
        "Ollama",
        "[green]OK[/]" if ollama_ok else "[yellow]missing or unreachable[/]",
        f"{agent.llm.host} — model={agent.llm.model}",
    )

    # Tool registry
    agent.bootstrap()
    table.add_row("Tools", "[green]OK[/]", f"{len(agent.registry.tools)} registered")

    # Scheduler
    table.add_row("Scheduler", "[green]OK[/]", "locks: llm, vram, memory, fs")

    # VRAM arbiter (live nvidia-smi snapshot + tenant accounting)
    from kee.core.vram_arbiter import get_default as get_vram
    vram_state = get_vram().measured()
    if vram_state.total_mb is None:
        table.add_row(
            "VRAM",
            "[yellow]no nvidia-smi[/]",
            f"tenants={vram_state.active_tenants}",
        )
    else:
        colour = {"ok": "green", "tight": "yellow", "critical": "red"}.get(
            vram_state.status, "yellow",
        )
        table.add_row(
            "VRAM",
            f"[{colour}]{vram_state.status}[/]",
            f"free={vram_state.free_mb}MB / used={vram_state.used_mb}MB / "
            f"total={vram_state.total_mb}MB · tenants={vram_state.active_tenants}",
        )

    # Embedder + ChromaDB (Phase 1 — informational, not required to run)
    embedder_health = await Embedder().health()
    table.add_row(
        "Embedder",
        "[green]OK[/]" if embedder_health["ok"] else "[yellow]offline[/]",
        f"host={embedder_health.get('host')} model={embedder_health.get('model', '?')}",
    )

    chroma_ok = await ChromaClient().health()
    table.add_row(
        "ChromaDB",
        "[green]OK[/]" if chroma_ok else "[yellow]offline[/]",
        settings.chromadb_host,
    )

    console.print(table)
    return 0 if ollama_ok else 1


# ── Tool GC one-shot ──────────────────────────────────────────────────────
async def _gc_once() -> int:
    settings.ensure_dirs()
    db.get_connection()
    agent = KeeAgent()
    agent.bootstrap()
    gc = ToolGarbageCollector(agent.registry)
    summary = gc.sweep_once()
    print(f"GC sweep: archived={summary['archived']}, flagged={summary['flagged']}")
    return 0


# ── Vault index ───────────────────────────────────────────────────────────
async def _index_vault() -> int:
    from kee.distributed.indexer import VaultIndexer
    settings.ensure_dirs()
    indexer = VaultIndexer()
    summary = await indexer.index_vault()
    print(
        f"Index sweep: indexed={summary['indexed']} skipped={summary['skipped']} "
        f"offline={summary.get('offline')}"
    )
    return 0 if not summary.get("offline") else 1


# ── Vault watcher (foreground) ────────────────────────────────────────────
async def _watch_vault() -> int:
    from kee.perception.filesystem import VaultWatcher
    settings.ensure_dirs()
    db.get_connection()
    watcher = VaultWatcher()
    loop = asyncio.get_running_loop()
    watcher.start(loop)
    print(f"Watching {settings.vault_dir} for .md changes. Ctrl-C to stop.")
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        watcher.stop()
    return 0


# ── Telegram surface (foreground) ─────────────────────────────────────────
# ── FastAPI backend (foreground) ─────────────────────────────────────────
def _api(host: str = "127.0.0.1", port: int = 7330, reload: bool = False) -> int:
    """Run the FastAPI backend that all UIs consume.

    Default bind: 127.0.0.1:7330 (local-only). For LAN access (e.g. mobile
    browser hitting Kee on the desktop) pass --api-host 0.0.0.0 explicitly.
    """
    import uvicorn
    settings.ensure_dirs()
    print(f"Kee API listening on http://{host}:{port}  (docs at /docs)")
    uvicorn.run("kee.surfaces.api:app", host=host, port=port,
                reload=reload, log_level="info")
    return 0


async def _telegram() -> int:
    from kee.surfaces.telegram import run as telegram_run

    settings.ensure_dirs()
    db.get_connection()
    agent = KeeAgent()
    agent.bootstrap()
    try:
        await telegram_run(agent)
    except (KeyboardInterrupt, asyncio.CancelledError):
        pass
    return 0


# ── Google OAuth one-shot (interactive browser flow) ─────────────────────
def _google_auth() -> int:
    """Run the Google OAuth flow once, cache the token. Browser opens."""
    from kee.distributed.google_oauth import get_credentials, status as gstat
    print("Pre-flight:", gstat())
    try:
        creds = get_credentials([
            "https://www.googleapis.com/auth/calendar",
            "https://www.googleapis.com/auth/gmail.readonly",
        ], interactive=True)
    except Exception as e:
        print(f"Auth failed: {e}")
        return 1
    print(f"OK. Token cached. Scopes: {creds.scopes}")
    return 0


# ── Voice surface (foreground) ───────────────────────────────────────────
async def _voice() -> int:
    from kee.perception.voice import VoicePipeline

    settings.ensure_dirs()
    db.get_connection()
    agent = KeeAgent()
    agent.bootstrap()
    pipe = VoicePipeline(agent)
    print("Voice pipeline starting. Say the wake word, then speak. Ctrl-C to stop.")
    try:
        await pipe.run()
    except KeyboardInterrupt:
        pipe.stop()
    return 0


# ── Live mic test ─────────────────────────────────────────────────────
def _mic_test() -> int:
    """Show 10 seconds of live mic RMS + speech detection.

    Use to verify your mic is actually capturing. If RMS stays under 30
    while you talk normally, your mic is muted or gain is too low —
    fix it in Windows Sound Settings → Input → Device Properties → Levels.
    """
    import os as _os, time as _time
    try:
        import sounddevice as sd, numpy as np
    except Exception as e:
        print(f"missing dep: {e}")
        return 1

    device = _os.environ.get("KEE_MIC_DEVICE")
    if device is not None:
        try: device = int(device)
        except ValueError: device = None

    info = sd.query_devices(device, kind='input') if device is not None else sd.query_devices(kind='input')
    sr = 16000
    try:
        stream = sd.InputStream(device=device, samplerate=sr, channels=1, dtype='int16', blocksize=1280)
        stream.start()
    except Exception:
        sr = int(info.get('default_samplerate', 44100) or 44100)
        stream = sd.InputStream(device=device, samplerate=sr, channels=1, dtype='int16', blocksize=int(sr*0.1))
        stream.start()

    print(f"\nMic test on device='{info.get('name','?')}' @ {sr}Hz")
    print("Talk for 10 seconds. RMS bar shows what the mic is hearing:\n")
    print("  rms     bar (each █ = 50 rms)")
    print("  ───────────────────────────────────────────")

    t0 = _time.time()
    max_rms = 0
    while _time.time() - t0 < 10:
        d, _o = stream.read(stream.blocksize)
        rms = float(np.abs(d).mean())
        max_rms = max(max_rms, rms)
        bar_len = int(rms / 50)
        bar = "█" * min(bar_len, 60)
        marker = "  ← VOICE" if rms > 100 else ("  ← whisper" if rms > 30 else "")
        print(f"  {rms:>5.0f}   {bar:<60}{marker}")
        _time.sleep(0.05)

    stream.stop(); stream.close()
    print(f"\n  Max RMS over 10s: {max_rms:.0f}")
    if max_rms < 30:
        print("\n  ✗ MIC IS DEAD OR MUTED.")
        print("  Fix: Windows Settings → System → Sound → Input → Device Properties → Levels")
        print("       Set the mic level to 80-100, unmute it.")
        print("  Or: change KEE_MIC_DEVICE in D:/Kee/.env to a different index.")
    elif max_rms < 200:
        print("  ⚠ Mic working but low gain. Boost it in Windows Sound Settings for better STT.")
    else:
        print("  ✓ Mic levels look good for wake-word + STT.")
    return 0


# ── Stack health check (post-launch verification) ───────────────────────
async def _check_stack() -> int:
    """Probe every supervised surface and the supervisor itself.

    Run after `python -m kee.main all` (give it ~10 s to spin up first).
    Returns 0 if the core stack (supervisor + api + dashboard mount) is
    alive. Other surfaces are reported but don't fail the check.
    """
    import json
    import time as _time
    from rich.console import Console
    from rich.table import Table
    import httpx

    # Force UTF-8 + no-legacy-Windows path so Rich doesn't choke on the
    # arrows / dots / box-drawing characters the table uses.
    console = Console(force_terminal=True, legacy_windows=False)
    settings.ensure_dirs()
    table = Table(title="Kee stack health", show_header=True,
                  header_style="bold cyan")
    table.add_column("Component"); table.add_column("Status"); table.add_column("Details")

    # Supervisor state file
    state_path = settings.data_dir / "supervisor_state.json"
    sup_ok = False
    if state_path.exists():
        try:
            data = json.loads(state_path.read_text(encoding="utf-8"))
            age = _time.time() - float(data.get("updated_at", 0))
            sup_ok = age < 10
            surfaces = data.get("surfaces", [])
            alive = sum(1 for s in surfaces if s.get("alive"))
            enabled = sum(1 for s in surfaces if s.get("enabled"))
            table.add_row(
                "supervisor",
                "[green]OK[/]" if sup_ok else f"[yellow]stale {age:.0f}s[/]",
                f"pid={data.get('supervisor_pid')} · {alive}/{enabled} surfaces alive",
            )
            for s in surfaces:
                if not s.get("enabled"):
                    table.add_row(
                        f"  - {s['name']}", "[dim]disabled[/]",
                        s.get("description", "")[:60],
                    )
                    continue
                if s.get("alive"):
                    table.add_row(
                        f"  - {s['name']}", "[green]alive[/]",
                        f"pid={s['pid']} up={int(s['uptime_s'])}s "
                        f"restarts={s['restarts']}",
                    )
                else:
                    table.add_row(
                        f"  - {s['name']}", "[red]down[/]",
                        f"last_exit={s.get('last_exit_code')} "
                        f"backoff={int(s.get('backoff_s', 0))}s",
                    )
        except Exception as e:
            table.add_row("supervisor", "[red]parse error[/]", str(e))
    else:
        table.add_row("supervisor", "[red]not running[/]",
                      "run `python -m kee.main all`")

    # API health
    api_ok = False
    api_url = "http://127.0.0.1:7330"
    try:
        async with httpx.AsyncClient(timeout=2.0) as c:
            r = await c.get(f"{api_url}/health")
            api_ok = r.status_code == 200
            data = r.json() if api_ok else {}
            table.add_row(
                "api",
                "[green]OK[/]" if api_ok else f"[red]{r.status_code}[/]",
                f"{api_url} · model={data.get('model')} · "
                f"uptime={int(data.get('uptime_s', 0))}s",
            )
    except Exception as e:
        table.add_row("api", "[red]unreachable[/]", str(e))

    # Dashboard mount (the SPA shell)
    if api_ok:
        try:
            async with httpx.AsyncClient(timeout=2.0) as c:
                r = await c.get(f"{api_url}/app/")
                dash_ok = r.status_code == 200 and "<html" in r.text.lower()
                table.add_row(
                    "dashboard /app",
                    "[green]OK[/]" if dash_ok else "[yellow]not built[/]",
                    "served by api · run `cd dashboard && npm run build` if missing",
                )
        except Exception as e:
            table.add_row("dashboard /app", "[red]error[/]", str(e))

    # Desktop signal pipe (just verify path is writable)
    try:
        from kee.desktop.app import write_signal, read_and_clear_signal
        write_signal("noop", reason="stack-check")
        sig = read_and_clear_signal()
        table.add_row(
            "desktop signal pipe",
            "[green]OK[/]" if sig else "[yellow]stale[/]",
            str(settings.data_dir / "desktop_signal.json"),
        )
    except Exception as e:
        table.add_row("desktop signal pipe", "[red]error[/]", str(e))

    # Voice readiness (model files on disk)
    wake = settings.models_dir / "wakeword" / "kee.onnx"
    table.add_row(
        "wake-word model",
        "[green]OK[/]" if wake.exists() else "[yellow]missing[/]",
        f"{wake} ({wake.stat().st_size if wake.exists() else 0}B)",
    )
    piper_count = len(list((settings.models_dir / "piper").glob("*.onnx"))) \
        if (settings.models_dir / "piper").exists() else 0
    table.add_row(
        "piper voices",
        "[green]OK[/]" if piper_count else "[yellow]none[/]",
        f"{piper_count} voice(s) installed",
    )

    console.print(table)
    return 0 if (sup_ok and api_ok) else 1


# ── Sleep Cycle one-shot ──────────────────────────────────────────────────
async def _sleep_cycle_once() -> int:
    from kee.cognition.sleep_cycle import SleepCycleDaemon
    settings.ensure_dirs()
    db.get_connection()
    agent = KeeAgent()
    agent.bootstrap()
    sc = SleepCycleDaemon(memory=agent.memory, llm=agent.llm, audit=agent.audit)
    report = await sc.run_once()
    print("Sleep Cycle done.")
    print(f"  summarized:    {report.summarized}")
    print(f"  axioms:        {len(report.axioms)}")
    for a in report.axioms:
        print(f"    - {a}")
    print(f"  proposal:      {report.proposal_path or '—'}")
    print(f"  digest:        {report.digest_path or '—'}")
    print(f"  pruned:        {report.pruned_rows}")
    if report.errors:
        print(f"  errors:        {report.errors}")
    return 0 if not report.errors else 1


# ── Heartbeat daemon (foreground) ─────────────────────────────────────────
async def _heartbeat(interval_s: int = 300) -> int:
    from kee.perception.heartbeat import HeartbeatDaemon

    settings.ensure_dirs()
    db.get_connection()
    agent = KeeAgent()
    agent.bootstrap()

    hb = HeartbeatDaemon(agent, interval_s=interval_s)
    print(
        f"Heartbeat running (interval={interval_s}s). Ctrl-C to stop.\n"
        f"Snapshots stream to audit_log; actionables fire the agent (also audited)."
    )
    loop = asyncio.get_running_loop()
    hb.start(loop)
    try:
        while True:
            await asyncio.sleep(3600)
    except (KeyboardInterrupt, asyncio.CancelledError):
        hb.stop()
    return 0


# ── Terminal REPL ─────────────────────────────────────────────────────────
async def _run_terminal(
    watch: bool = False,
    heartbeat: bool = False,
    heartbeat_interval: int = 300,
    sleep_cycle: bool = False,
) -> None:
    from kee.surfaces.terminal import run as terminal_run

    settings.ensure_dirs()
    db.get_connection()

    agent = KeeAgent()
    watcher = None
    hb = None
    sc = None

    if watch:
        from kee.perception.filesystem import VaultWatcher
        watcher = VaultWatcher(indexer=agent.memory.indexer)
        watcher.start(asyncio.get_running_loop())

    if heartbeat:
        from kee.perception.heartbeat import HeartbeatDaemon
        hb = HeartbeatDaemon(agent, interval_s=heartbeat_interval)
        hb.start(asyncio.get_running_loop())

    if sleep_cycle:
        from kee.cognition.sleep_cycle import SleepCycleDaemon
        sc = SleepCycleDaemon(memory=agent.memory, llm=agent.llm, audit=agent.audit)
        sc.start(asyncio.get_running_loop())

    # Make the daemons discoverable to the terminal slash commands.
    agent.heartbeat = hb       # type: ignore[attr-defined]
    agent.sleep_cycle = sc     # type: ignore[attr-defined]

    try:
        await terminal_run(agent)
    finally:
        if watcher is not None:
            watcher.stop()
        if hb is not None:
            hb.stop()
        if sc is not None:
            sc.stop()
        db.close()


# ── CLI ───────────────────────────────────────────────────────────────────
def run() -> None:
    parser = argparse.ArgumentParser(prog="kee")
    parser.add_argument(
        "surface",
        nargs="?",
        default="terminal",
        choices=["terminal", "check", "gc", "index", "watch", "heartbeat",
                 "sleep-cycle", "voice", "telegram", "google-auth", "api", "notif-bridge",
                 "spotify-auth", "all", "daemon", "tray", "install-autostart",
                 "uninstall-autostart", "desktop", "stack", "mic-test",
                 "backup-now"],
        help="Surface or one-shot command to run (default: terminal). "
             "Use 'all'/'daemon' to spawn the full Kee stack as supervised "
             "background processes.",
    )
    parser.add_argument(
        "--only",
        default=None,
        help="(all/daemon) comma-separated list of surfaces to enable, "
             "overriding KEE_DAEMON_* env flags.",
    )
    parser.add_argument(
        "--watch",
        action="store_true",
        help="In terminal mode: also start the vault watcher in the background.",
    )
    parser.add_argument(
        "--heartbeat",
        action="store_true",
        help="In terminal mode: also start the heartbeat daemon in the background.",
    )
    parser.add_argument(
        "--heartbeat-interval",
        type=int,
        default=300,
        help="Heartbeat interval in seconds (default 300, i.e. 5 minutes).",
    )
    parser.add_argument(
        "--sleep-cycle",
        action="store_true",
        help="In terminal mode: also arm the Sleep Cycle daemon (fires at 04:00 local).",
    )
    parser.add_argument(
        "--mode",
        default="hud",
        choices=["hud", "full"],
        help="(desktop) initial window mode — hud (compact corner) or full (dashboard).",
    )
    parser.add_argument(
        "--url",
        default=None,
        help="(desktop) override dashboard URL (default http://localhost:5173).",
    )
    parser.add_argument(
        "--api-host",
        default="127.0.0.1",
        help="Bind host for the FastAPI backend (default 127.0.0.1, local only).",
    )
    parser.add_argument(
        "--api-port",
        type=int,
        default=7330,
        help="Bind port for the FastAPI backend (default 7330).",
    )
    parser.add_argument(
        "--api-reload",
        action="store_true",
        help="Enable uvicorn auto-reload (dev only).",
    )
    args = parser.parse_args()

    # In interactive terminal mode, keep the REPL clean by default. DEBUG is
    # the explicit escape hatch when a noisy troubleshooting session is wanted.
    setup_logging()
    if args.surface == "terminal" and os.environ.get("KEE_LOG_LEVEL", "").upper() != "DEBUG":
        import logging as _logging
        _logging.getLogger().setLevel(_logging.WARNING)

    if args.surface == "check":
        sys.exit(asyncio.run(_check()))
    if args.surface == "gc":
        sys.exit(asyncio.run(_gc_once()))
    if args.surface == "index":
        sys.exit(asyncio.run(_index_vault()))
    if args.surface == "watch":
        sys.exit(asyncio.run(_watch_vault()))
    if args.surface == "heartbeat":
        sys.exit(asyncio.run(_heartbeat(interval_s=args.heartbeat_interval)))
    if args.surface == "sleep-cycle":
        sys.exit(asyncio.run(_sleep_cycle_once()))
    if args.surface == "backup-now":
        from kee.cognition.backup import run_backups
        import json as _json
        res = run_backups()
        print(_json.dumps(res, indent=2))
        sys.exit(0 if res.get("sqlite", {}).get("ok") else 1)
    if args.surface == "voice":
        sys.exit(asyncio.run(_voice()))
    if args.surface == "telegram":
        sys.exit(asyncio.run(_telegram()))
    if args.surface == "google-auth":
        sys.exit(_google_auth())
    if args.surface == "api":
        sys.exit(_api(host=args.api_host, port=args.api_port, reload=args.api_reload))
    if args.surface == "notif-bridge":
        from kee.perception.notif_bridge_windows import run as notif_run
        sys.exit(asyncio.run(notif_run()))
    if args.surface == "spotify-auth":
        from kee.distributed.spotify_oauth import run_oauth
        sys.exit(run_oauth())
    if args.surface in ("all", "daemon"):
        from kee.daemon.supervisor import run_supervisor
        only = [s.strip() for s in args.only.split(",")] if args.only else None
        sys.exit(asyncio.run(run_supervisor(only=only)))
    if args.surface == "tray":
        from kee.daemon.tray import run_tray
        sys.exit(run_tray())
    if args.surface == "install-autostart":
        from kee.daemon.autostart import install_windows_autostart
        sys.exit(install_windows_autostart())
    if args.surface == "uninstall-autostart":
        from kee.daemon.autostart import uninstall_windows_autostart
        sys.exit(uninstall_windows_autostart())
    if args.surface == "desktop":
        from kee.desktop.app import run_desktop, DEFAULT_URL
        sys.exit(run_desktop(mode=args.mode, url=args.url or DEFAULT_URL))
    if args.surface == "stack":
        sys.exit(asyncio.run(_check_stack()))
    if args.surface == "mic-test":
        sys.exit(_mic_test())

    asyncio.run(_run_terminal(
        watch=args.watch,
        heartbeat=args.heartbeat,
        heartbeat_interval=args.heartbeat_interval,
        sleep_cycle=args.sleep_cycle,
    ))


if __name__ == "__main__":
    run()
