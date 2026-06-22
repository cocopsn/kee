"""FastAPI backend — the substrate every Kee UI consumes.

Three classes of endpoint:

  * **REST reads** — `/health`, `/tools`, `/audit`, `/heartbeat`,
    `/conversations`, `/goals`, `/world-model`, `/economy`, `/digest`.
    Stable, paginated where it matters, JSON.
  * **POST /chat** — alternative to terminal/voice/telegram. Same agent
    pipeline, per-`session_id` `ConversationState`. Streams tokens
    inside the response over Server-Sent Events when `stream=true`.
  * **WS /stream** — live tail of audit_log and heartbeat snapshots.
    The dashboard subscribes here for the nervous-system view.

CORS is wide-open for `localhost:*` so any local UI (Vite dev server,
Tauri webview, Electron, plain HTML) can talk to us. NOT exposed to
the network — uvicorn binds 127.0.0.1 by default.

The agent and its dependencies (db, scheduler, registry) are
instantiated once per process and shared. Connection-style state per
chat lives in `_CONV_BY_SESSION`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any, AsyncIterator

from fastapi import (
    FastAPI, File, HTTPException, Query, UploadFile, WebSocket, WebSocketDisconnect,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field, field_validator as _field_validator

from kee.cognition import autonomy, economy
from kee.cognition import world_model as wm
from kee.config import settings
from kee.core import db
from kee.core.agent import KeeAgent
from kee.core.memory import ConversationState
from kee.perception.goals import load_goals

logger = logging.getLogger(__name__)


# ── Shared state ─────────────────────────────────────────────────────────
class _State:
    agent: KeeAgent | None = None
    started_at: datetime = datetime.utcnow()


_STATE = _State()
_CONV_BY_SESSION: dict[str, ConversationState] = {}

# Live event queue for /stream. Each entry is (queue, capabilities) so we
# can route audio events only to clients that asked for them. Capabilities
# default to {wants_audio: False, wants_animation: True} matching the
# Jarvis-pattern smart-routing.
_STREAM_QUEUES: list[dict[str, Any]] = []


def _broadcast(event: dict[str, Any], audio_only: bool | None = None) -> None:
    """Fan-out to every active subscriber.

    `audio_only=True`  → routes only to clients with `wants_audio=True`.
    `audio_only=None`  (default) → auto-classifies: any event whose `type`
                        starts with ``voice_audio_`` is treated as audio-only.
                        Belt-and-braces so a caller never has to remember the
                        flag for streaming chunks.
    Drops to /dev/null if no matching subscribers; full queues are silently
    dropped (see `tests/test_audio_routing.py`).
    """
    if audio_only is None:
        et = event.get("type") if isinstance(event, dict) else None
        audio_only = bool(et and isinstance(et, str)
                          and et.startswith("voice_audio_"))
    for entry in list(_STREAM_QUEUES):
        q = entry["queue"]
        if audio_only and not entry.get("wants_audio"):
            continue
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _auto_summarizer() -> None:
    """Every 10 minutes, summarize conversations idle for 15+ min so they
    feed cross-conversation memory on the next chat. Uses local Ollama
    (free), bounded to 5 conversations per cycle to avoid bursts.
    """
    while True:
        try:
            await asyncio.sleep(600)  # 10 min
            agent = _STATE.agent
            if agent is None:
                continue
            ids = agent.memory.stale_conversations(idle_minutes=15, limit=5)
            for cid in ids:
                try:
                    await agent.memory.summarize_conversation(cid, llm=agent.llm)
                except Exception as e:
                    logger.debug("auto_summarizer %s skipped: %s", cid, e)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.warning("auto_summarizer cycle failed: %s", e)


async def _audit_tailer() -> None:
    """Tail the audit_log table and broadcast new rows on the WS stream.

    This is what lets the dashboard react to events from ANY surface —
    Telegram, voice, terminal — even when those surfaces are running in
    separate processes. We hold the highest-seen audit id and poll for
    anything beyond it. SQLite is local + the table is tiny, so 1s is
    fine and zero-config.
    """
    last_id = 0
    try:
        con = db.get_connection()
        row = con.execute("SELECT MAX(id) FROM audit_log").fetchone()
        last_id = (row[0] if row and row[0] is not None else 0)
    except Exception:
        pass
    while True:
        try:
            con = db.get_connection()
            cur = con.execute(
                "SELECT id, timestamp, action, tool_name, success "
                "FROM audit_log WHERE id > ? ORDER BY id ASC LIMIT 50",
                (last_id,),
            )
            rows = cur.fetchall()
            for r in rows:
                last_id = r[0]
                # SQLite returns timestamps as datetime when the column
                # has TIMESTAMP affinity. JSON-serialise them as ISO.
                ts = r[1]
                if hasattr(ts, "isoformat"):
                    ts = ts.isoformat()
                _broadcast({
                    "type": r[2] or "audit",
                    "tool": r[3],
                    "ok": bool(r[4]),
                    "audit_id": r[0],
                    "ts": ts,
                })
        except Exception as e:
            logger.warning("audit_tailer cycle failed: %s", e)
        await asyncio.sleep(1.0)


# ── Lifespan: bootstrap the agent once ───────────────────────────────────
@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings.ensure_dirs()
    db.get_connection()
    _STATE.agent = KeeAgent()
    _STATE.agent.bootstrap()
    logger.info("FastAPI ready: %d tools, model=%s",
                len(_STATE.agent.registry.tools), _STATE.agent.llm.model)
    tail_task = asyncio.create_task(_audit_tailer())
    summ_task = asyncio.create_task(_auto_summarizer())
    try:
        yield
    finally:
        for t in (tail_task, summ_task):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        db.close()


app = FastAPI(
    title="Kee API",
    version="0.1.0",
    description="Local control plane for Kee. The dashboard + future UIs read from here.",
    lifespan=_lifespan,
)

# ── Mount the built dashboard so the API serves the entire UI on one port ─
# This is what makes the system "converge": one process tree, one URL,
# one tray icon. The desktop window, browser, and any mobile client all
# point at http://127.0.0.1:7330/app.
_DASHBOARD_BUILD = settings.project_root / "dashboard" / "build"
_DASHBOARD_AVAILABLE = (
    _DASHBOARD_BUILD.exists() and (_DASHBOARD_BUILD / "index.html").exists()
)
try:
    from fastapi.staticfiles import StaticFiles
    if _DASHBOARD_AVAILABLE:
        # Real assets (the _app/* hashed bundles, favicon, etc.) live on disk.
        app.mount(
            "/app/_app",
            StaticFiles(directory=str(_DASHBOARD_BUILD / "_app")),
            name="dashboard_assets",
        )
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning("dashboard mount skipped: %s", _e)
    _DASHBOARD_AVAILABLE = False


app.add_middleware(
    CORSMiddleware,
    # localhost UIs (Vite at :5173, Tauri, Electron) + Termux on the same
    # LAN reaching us via Tailscale or a local IP. Set
    # `KEE_CORS_ALLOWED_ORIGINS=https://my-host` (comma-separated) to add
    # explicit hosts for production. Wildcard pattern below covers
    # 192.168.*.*, 10.*.*.*, 100.*.*.* (Tailscale CGNAT range).
    allow_origin_regex=(
        r"^https?://("
        r"localhost|127\.0\.0\.1"
        r"|192\.168\.\d{1,3}\.\d{1,3}"
        r"|10\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r"|100\.\d{1,3}\.\d{1,3}\.\d{1,3}"
        r")(:\d+)?$"
    ),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Optional bearer-token gate for non-loopback clients (Termux/mobile) ──
@app.middleware("http")
async def _bearer_gate(request, call_next):
    """When ``KEE_API_TOKEN`` is set in the env, every request from a
    non-loopback client must present ``Authorization: Bearer <token>``.
    Loopback (127.0.0.1, ::1) is always exempt so the dashboard / local
    surfaces don't need to know the token. Phase 7 §"Termux mobile edge".
    """
    import os as _os
    token = _os.environ.get("KEE_API_TOKEN", "").strip()
    if not token:
        return await call_next(request)
    client_host = request.client.host if request.client else ""
    if client_host in ("127.0.0.1", "::1", "localhost"):
        return await call_next(request)
    auth = request.headers.get("authorization", "")
    if not auth.startswith("Bearer "):
        return JSONResponse({"detail": "missing bearer"}, status_code=401)
    if auth.removeprefix("Bearer ").strip() != token:
        return JSONResponse({"detail": "invalid bearer"}, status_code=401)
    return await call_next(request)


def _agent() -> KeeAgent:
    if _STATE.agent is None:
        raise HTTPException(503, "Agent not bootstrapped yet.")
    return _STATE.agent


# ── REST: meta ───────────────────────────────────────────────────────────
@app.get("/health")
async def health() -> dict[str, Any]:
    a = _agent()
    return {
        "status": "ok",
        "model": a.llm.model,
        "tools": len(a.registry.tools),
        "started_at": _STATE.started_at.isoformat(timespec="seconds"),
        "uptime_s": int((datetime.utcnow() - _STATE.started_at).total_seconds()),
    }


@app.get("/tools")
async def tools_index() -> dict[str, Any]:
    a = _agent()
    return {
        "count": len(a.registry.tools),
        "tools": [
            {
                "name": t.name,
                "risk_level": t.risk_level,
                "source": t.source,
                "description": t.description.strip().split("\n")[0],
                "parameters_schema": t.parameters_schema,
            }
            for t in a.registry.tools.values()
        ],
    }


# ── REST: audit / heartbeat / conversations ──────────────────────────────
@app.get("/audit")
async def audit(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"rows": _agent().audit.recent(limit=limit)}


@app.get("/anomalies")
async def anomalies(limit: int = Query(50, ge=1, le=500)) -> dict[str, Any]:
    return {"rows": _agent().audit.recent_anomalies(limit=limit)}


@app.get("/heartbeat/recent")
async def heartbeat_recent(n: int = Query(10, ge=1, le=100)) -> dict[str, Any]:
    """Recent heartbeat snapshots. Pulled from audit_log because the
    in-memory buffer only exists when the heartbeat daemon is running
    inside the same process."""
    with db.cursor() as cur:
        cur.execute(
            "SELECT id, timestamp, parameters FROM audit_log "
            "WHERE action = 'heartbeat' ORDER BY id DESC LIMIT ?",
            (n,),
        )
        rows = []
        for r in cur.fetchall():
            try:
                payload = json.loads(r["parameters"]) if r["parameters"] else {}
            except json.JSONDecodeError:
                payload = {}
            rows.append({
                "id": r["id"], "timestamp": str(r["timestamp"]),
                "mode": payload.get("mode"),
                "checks": payload.get("checks"),
            })
    return {"count": len(rows), "rows": rows}


@app.get("/conversations")
async def conversations_list(limit: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"rows": _agent().memory.recent_conversations(limit=limit)}


@app.get("/conversations/{conversation_id}")
async def conversation_detail(conversation_id: str) -> dict[str, Any]:
    msgs = _agent().memory.get_messages(conversation_id)
    if not msgs:
        raise HTTPException(404, f"No messages for {conversation_id}")
    return {"id": conversation_id, "messages": msgs}


# ── REST: goals + world model + economy + autonomy ───────────────────────
@app.get("/goals")
async def goals_index() -> dict[str, Any]:
    return {"goals": [
        {
            "title": g.title, "status": g.status,
            "deadline": g.deadline.isoformat() if g.deadline else None,
            "days_left": g.days_to_deadline(),
            "project": g.project, "progress_pct": g.progress_pct,
            "notes": g.notes, "extras": g.extras,
        }
        for g in load_goals()
    ]}


@app.get("/world-model/entities")
async def world_entities(type: str | None = None) -> dict[str, Any]:
    return {"entities": [e.to_dict() for e in wm.list_entities(type=type)]}


class GoalsBody(BaseModel):
    markdown: str


@app.get("/goals/raw")
async def goals_raw() -> dict[str, Any]:
    """Return the raw markdown of vault/config/goals.md for inline editing."""
    p = settings.vault_dir / "config" / "goals.md"
    if not p.exists():
        return {"path": str(p), "exists": False, "markdown": ""}
    return {"path": str(p), "exists": True, "markdown": p.read_text(encoding="utf-8")}


@app.put("/goals/raw")
async def goals_raw_put(body: GoalsBody) -> dict[str, Any]:
    """Overwrite vault/config/goals.md. Goals parser re-reads on every
    /goals call so changes are immediately visible."""
    p = settings.vault_dir / "config" / "goals.md"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body.markdown, encoding="utf-8")
    return {"ok": True, "bytes": len(body.markdown.encode("utf-8"))}


@app.get("/vault/list")
async def vault_list(subdir: str = "") -> dict[str, Any]:
    """List markdown files in the vault. `subdir` is relative to vault_dir.
    Returns a list of {path, name, bytes, mtime} for each .md file recursively."""
    from pathlib import Path as _P
    base = settings.vault_dir
    if subdir:
        # Prevent path traversal
        safe = (base / subdir).resolve()
        if not str(safe).startswith(str(base.resolve())):
            raise HTTPException(400, "subdir escapes vault")
        base = safe
    if not base.exists():
        return {"items": []}
    items = []
    for f in base.rglob("*.md"):
        try:
            st = f.stat()
            rel = f.relative_to(settings.vault_dir)
            items.append({
                "path": str(rel).replace("\\", "/"),
                "name": f.name,
                "bytes": st.st_size,
                "mtime": st.st_mtime,
            })
        except Exception:
            continue
    items.sort(key=lambda i: i["mtime"], reverse=True)
    return {"items": items[:200]}


class VaultWriteBody(BaseModel):
    content: str


@app.put("/vault/write")
async def vault_write(path: str, body: VaultWriteBody) -> dict[str, Any]:
    """Write a markdown file in the vault. Path is relative to vault_dir.
    Refuses paths outside vault. Refuses non-.md extensions to avoid
    accidentally clobbering binary files. Vault watcher picks the change
    up and reindexes."""
    base = settings.vault_dir
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(400, "path escapes vault")
    if not target.suffix == ".md":
        raise HTTPException(400, "only .md files allowed")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body.content, encoding="utf-8")
    return {"ok": True, "path": path, "bytes": len(body.content.encode("utf-8"))}


@app.get("/vault/read")
async def vault_read(path: str) -> dict[str, Any]:
    """Return the contents of a vault markdown file. `path` is relative to vault_dir."""
    from pathlib import Path as _P
    base = settings.vault_dir
    target = (base / path).resolve()
    if not str(target).startswith(str(base.resolve())):
        raise HTTPException(400, "path escapes vault")
    if not target.exists() or not target.is_file():
        raise HTTPException(404, "not found")
    try:
        text = target.read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"read failed: {e}")
    return {
        "path": path,
        "bytes": len(text.encode("utf-8")),
        "lines": text.count("\n") + 1,
        "content": text,
    }


@app.get("/spotify/now_playing")
async def spotify_now_playing() -> dict[str, Any]:
    """Quick wrapper for the dashboard music chip."""
    try:
        from kee.tools.spotify import tool as _sp
        return await _sp.execute(action="now_playing")
    except Exception as e:
        return {"status": "error", "reason": str(e)[:200]}


@app.get("/voice/state")
async def voice_state() -> dict[str, Any]:
    """Wake-word + STT + TTS file existence + last voice activity."""
    from pathlib import Path as _P
    wake = settings.models_dir / "wakeword" / "kee.onnx"
    piper = settings.models_dir / "piper" / "es_MX-claude-high.onnx"
    samples_dir = settings.models_dir / "wakeword" / "samples" / "positive"
    sample_count = len(list(samples_dir.glob("*.wav"))) if samples_dir.exists() else 0
    # Wake training log tail (last error or progress)
    log_path = settings.data_dir / "wake_train.log"
    last_log = ""
    if log_path.exists():
        try:
            with log_path.open("r", encoding="utf-8", errors="replace") as f:
                # Tail with \r handling for tqdm progress bars
                tail = f.read()[-4000:]
                lines = [ln for ln in tail.replace("\r", "\n").split("\n") if ln.strip()]
                last_log = "\n".join(lines[-15:])
        except Exception:
            pass
    return {
        "wake_word": {
            "path": str(wake),
            "exists": wake.exists(),
            "bytes": wake.stat().st_size if wake.exists() else 0,
            "fallback": "hey_jarvis_v0.1 (bundled in openwakeword)",
        },
        "tts": {
            "path": str(piper),
            "exists": piper.exists(),
            "bytes": piper.stat().st_size if piper.exists() else 0,
            "voice": "es_MX-claude-high",
        },
        "stt": {
            "model": "faster-whisper small (CT2 format, lazy-downloaded)",
            "languages": ["es", "en"],
        },
        "training": {
            "samples_recorded": sample_count,
            "log_tail": last_log,
        },
    }


# ── Voice configuration & catalog ────────────────────────────────────────
@app.get("/voice/config")
async def voice_config_get() -> dict[str, Any]:
    """Current persistent voice preferences (voice stem + speed + speak toggle)."""
    from kee.core import voice_config as vcfg
    prefs = vcfg.load()
    from dataclasses import asdict as _asdict
    return _asdict(prefs)


class VoiceConfigBody(BaseModel):
    voice: str | None = None
    length_scale: float | None = None
    noise_scale: float | None = None
    noise_w: float | None = None
    speak_responses: bool | None = None
    sentence_silence_s: float | None = None
    voice_per_lang: dict[str, str] | None = None
    auto_detect_language: bool | None = None
    stt_language: str | None = None


@app.post("/voice/config")
async def voice_config_set(body: VoiceConfigBody) -> dict[str, Any]:
    """Update voice preferences. Persisted to ``data/voice_config.json``;
    voice pipeline picks up the change on its next utterance — no restart."""
    from kee.core import voice_config as vcfg
    prefs = vcfg.load()
    if body.voice is not None:
        # Validate against installed voices
        installed = {v["name"] for v in vcfg.installed_voices()}
        if body.voice not in installed:
            raise HTTPException(404, f"voice '{body.voice}' not installed. "
                                     f"installed: {sorted(installed)}")
        prefs.voice = body.voice
    if body.length_scale is not None:
        prefs.length_scale = max(0.5, min(2.5, float(body.length_scale)))
    if body.noise_scale is not None:
        prefs.noise_scale = max(0.0, min(1.5, float(body.noise_scale)))
    if body.noise_w is not None:
        prefs.noise_w = max(0.0, min(1.5, float(body.noise_w)))
    if body.speak_responses is not None:
        prefs.speak_responses = bool(body.speak_responses)
    if body.sentence_silence_s is not None:
        prefs.sentence_silence_s = max(0.0, min(2.0, float(body.sentence_silence_s)))
    if body.voice_per_lang is not None:
        # Validate every mapped voice exists; drop unknown entries silently
        installed = {v["name"] for v in vcfg.installed_voices()}
        cleaned = {lang.lower(): name for lang, name in body.voice_per_lang.items()
                   if name in installed and len(lang) <= 8}
        prefs.voice_per_lang = cleaned
    if body.auto_detect_language is not None:
        prefs.auto_detect_language = bool(body.auto_detect_language)
    if body.stt_language is not None:
        prefs.stt_language = body.stt_language[:8].lower()
    vcfg.save(prefs)
    from dataclasses import asdict as _asdict
    return {"ok": True, "config": _asdict(prefs)}


@app.get("/voice/voices")
async def voice_voices() -> dict[str, Any]:
    """List Piper voices currently installed under ``models/piper/``."""
    from kee.core import voice_config as vcfg
    return {"voices": vcfg.installed_voices()}


@app.get("/voice/catalog")
async def voice_catalog() -> dict[str, Any]:
    """Curated list of downloadable Piper voices. Each entry annotated with
    ``installed`` so the UI can render install / uninstall buttons."""
    from kee.distributed import piper_catalog as pc
    items = []
    for v in pc.CATALOG:
        items.append({
            "stem": v.stem,
            "locale": v.locale,
            "name": v.name,
            "quality": v.quality,
            "description": v.description,
            "approx_mb": v.approx_mb,
            "installed": pc.is_installed(v.stem),
        })
    return {"voices": items}


class VoiceInstallBody(BaseModel):
    stems: list[str] = Field(default_factory=list)


@app.post("/voice/install")
async def voice_install(body: VoiceInstallBody) -> dict[str, Any]:
    """Download one or more Piper voices from the rhasspy/piper-voices HF repo.

    Idempotent. Bytes go to ``models/piper/<stem>.onnx`` + ``.onnx.json``.
    Runs synchronously (the dashboard waits with a spinner) — Piper voices
    are 13-60 MB so this is usually under 10s on a decent connection.
    """
    if not body.stems:
        raise HTTPException(400, "no stems provided")
    from kee.distributed import piper_catalog as pc
    # Run blocking downloads in a worker thread so the API stays responsive
    import asyncio as _asyncio
    results = await _asyncio.get_running_loop().run_in_executor(
        None, pc.install_many, body.stems,
    )
    return {"results": results}


@app.post("/voice/voices/{stem}/uninstall")
async def voice_uninstall(stem: str) -> dict[str, Any]:
    from kee.distributed import piper_catalog as pc
    return pc.remove(stem)


class VoiceSpeakBody(BaseModel):
    text: str
    voice: str | None = None     # override the active voice for this sample
    play: bool = True            # play through speakers (False = return wav bytes only)


@app.post("/voice/speak")
async def voice_speak(body: VoiceSpeakBody) -> dict[str, Any]:
    """Synthesize a sample with the selected voice. Used by the Settings
    page "Test" button. Plays through the system's default audio output
    when ``play=True`` (the audio comes out of the same machine running
    the API — Coco's laptop)."""
    from kee.core import voice_config as vcfg
    from kee.perception.voice import _find_piper as _find_piper_bin
    import subprocess as _sp
    import tempfile as _tf
    import wave as _wave
    import time as _time
    import shutil as _shutil

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")

    prefs = vcfg.load()
    stem = body.voice or prefs.voice
    voice_path = vcfg.voice_file_for(stem)
    if not voice_path.exists():
        raise HTTPException(404, f"voice '{stem}' not installed")
    piper_bin = _find_piper_bin()
    if piper_bin is None:
        raise HTTPException(500, "piper executable not found in PATH or venv")

    with _tf.NamedTemporaryFile(suffix=".wav", delete=False) as wav:
        wav_path = wav.name
    t0 = _time.monotonic()
    try:
        cmd = [piper_bin, *prefs.to_piper_args(voice_path), "--output_file", wav_path]
        proc = _sp.run(cmd, input=text.encode("utf-8"),
                       capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise HTTPException(
                500,
                f"piper failed: {proc.stderr.decode('utf-8', 'replace')[:300]}",
            )
        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        wav_size = (_shutil.os.stat(wav_path).st_size if _shutil.os.path.exists(wav_path) else 0)
        if body.play:
            try:
                import sounddevice as _sd
                import numpy as _np
                with _wave.open(wav_path, "rb") as wf:
                    sr = wf.getframerate()
                    raw = wf.readframes(wf.getnframes())
                arr = _np.frombuffer(raw, dtype=_np.int16)
                _sd.play(arr, samplerate=sr, blocking=False)
            except Exception as e:
                return {"ok": True, "voice": stem, "wav_path": wav_path,
                        "elapsed_ms": elapsed_ms, "wav_bytes": wav_size,
                        "play_error": str(e)}
        return {"ok": True, "voice": stem, "wav_path": wav_path,
                "elapsed_ms": elapsed_ms, "wav_bytes": wav_size}
    except _sp.TimeoutExpired:
        raise HTTPException(500, "piper timed out (>60s)")
    finally:
        # Keep wav for one minute then sweep — sounddevice may still be
        # reading from it asynchronously, so we don't unlink immediately.
        pass


class VoiceStreamBody(BaseModel):
    text: str
    voice: str | None = None
    chunk_seconds: float = Field(default=0.8, ge=0.1, le=5.0)


@app.post("/voice/stream")
async def voice_stream(body: VoiceStreamBody) -> dict[str, Any]:
    """Synthesize text and broadcast audio chunks over the /stream WS so any
    client that registered with `wants_audio:true` can play them in lock-step
    with the orb's amplitude envelope.

    Each chunk event has shape::

        {
          "type": "voice_audio_chunk",
          "audio_b64": "<base64-encoded int16 PCM mono>",
          "sample_rate": 22050,
          "index": 0,
          "is_last": false,
          "rms": 0.12,
          "duration_ms": 800
        }

    Useful so a remote dashboard tab (no local Piper) can still hear Kee.
    Returns metadata about the run; clients consume audio over the WS.
    """
    from kee.core import voice_config as vcfg
    from kee.perception.voice import _find_piper as _find_piper_bin
    import base64 as _b64
    import math as _math
    import subprocess as _sp
    import tempfile as _tf
    import time as _time
    import wave as _wave

    text = (body.text or "").strip()
    if not text:
        raise HTTPException(400, "empty text")
    prefs = vcfg.load()
    stem = body.voice or prefs.voice
    voice_path = vcfg.voice_file_for(stem)
    if not voice_path.exists():
        raise HTTPException(404, f"voice '{stem}' not installed")
    piper_bin = _find_piper_bin()
    if piper_bin is None:
        raise HTTPException(500, "piper executable not found")

    with _tf.NamedTemporaryFile(suffix=".wav", delete=False) as wav:
        wav_path = wav.name
    t0 = _time.monotonic()
    try:
        cmd = [piper_bin, *prefs.to_piper_args(voice_path),
               "--output_file", wav_path]
        proc = _sp.run(cmd, input=text.encode("utf-8"),
                       capture_output=True, timeout=60)
        if proc.returncode != 0:
            raise HTTPException(
                500,
                f"piper failed: {proc.stderr.decode('utf-8', 'replace')[:300]}",
            )

        with _wave.open(wav_path, "rb") as wf:
            sr = wf.getframerate()
            nchan = wf.getnchannels()
            sw = wf.getsampwidth()
            total_frames = wf.getnframes()
            raw = wf.readframes(total_frames)

        if sw != 2:
            raise HTTPException(500, f"unsupported sample width {sw}")

        chunk_frames = max(1, int(sr * body.chunk_seconds))
        # Bytes-per-frame for int16 mono/stereo.
        bpf = sw * nchan
        total_bytes = total_frames * bpf
        chunk_bytes = chunk_frames * bpf

        # Compute RMS over int16 PCM in pure Python (avoid numpy import here).
        def _rms_int16(buf: bytes) -> float:
            if not buf:
                return 0.0
            n = len(buf) // 2
            if n == 0:
                return 0.0
            # Sum squares without numpy: use array module.
            import array as _arr
            samples = _arr.array("h"); samples.frombytes(buf)
            acc = 0
            for s in samples:
                acc += s * s
            mean = acc / n
            return min(1.0, _math.sqrt(mean) / 32768.0)

        index = 0
        offset = 0
        broadcast_count = 0
        while offset < total_bytes:
            sl = raw[offset:offset + chunk_bytes]
            offset += chunk_bytes
            is_last = offset >= total_bytes
            rms = _rms_int16(sl)
            duration_ms = int(1000 * (len(sl) / bpf) / sr) if sr else 0
            _broadcast({
                "type": "voice_audio_chunk",
                "audio_b64": _b64.b64encode(sl).decode("ascii"),
                "sample_rate": sr,
                "channels": nchan,
                "index": index,
                "is_last": is_last,
                "rms": round(rms, 4),
                "duration_ms": duration_ms,
            }, audio_only=True)
            broadcast_count += 1
            index += 1
            # Yield to the loop so other tasks (HUD orb, dashboard updates)
            # don't starve while we shovel audio.
            await asyncio.sleep(0)

        elapsed_ms = int((_time.monotonic() - t0) * 1000)
        return {
            "ok": True,
            "voice": stem,
            "chunks": broadcast_count,
            "sample_rate": sr,
            "channels": nchan,
            "duration_ms": int(1000 * total_frames / sr) if sr else 0,
            "elapsed_ms": elapsed_ms,
            "audio_subscribers": sum(
                1 for e in _STREAM_QUEUES if e.get("wants_audio")
            ),
        }
    except _sp.TimeoutExpired:
        raise HTTPException(500, "piper timed out (>60s)")


@app.get("/quality/snapshot")
async def quality_snapshot() -> dict[str, Any]:
    """Rolling response-quality snapshot (Jarvis-pattern, no LLM cost).

    Returns the last 20 agent-reply samples + average score + trend.
    Powers the dashboard's voice-quality sparkline."""
    from kee.cognition.conversation_monitor import snapshot
    return snapshot()


@app.get("/quality/lifetime")
async def quality_lifetime(window_days: int = 7) -> dict[str, Any]:
    """Cross-process quality history backed by `audit_log`.

    The in-memory `/quality/snapshot` is per-process — voice and chat live
    in separate processes (under the supervisor) and don't share state.
    This endpoint reads `conversation_qa` audit rows so the dashboard can
    render a unified history across surfaces.
    """
    from kee.tools.quality_snapshot import _lifetime_snapshot
    return _lifetime_snapshot(window_days=window_days)


@app.get("/voice/last_event")
async def voice_last_event() -> dict[str, Any]:
    """Return the most recent voice event (wake / STT / agent reply) so
    the HUD can show real-time feedback. Driven by the audit_log: pulls
    the last few rows where action ∈ {wake_word, voice}."""
    conn = db.get_connection()
    cur = conn.cursor()
    try:
        cur.execute("""
            SELECT id, timestamp, action, result
            FROM audit_log
            WHERE action IN ('wake_word', 'voice')
            ORDER BY id DESC
            LIMIT 5
        """)
        rows = []
        for row in cur.fetchall():
            payload: dict[str, Any] = {}
            try:
                payload = json.loads(row[3]) if row[3] else {}
            except Exception:
                payload = {}
            rows.append({
                "id": row[0], "timestamp": row[1],
                "action": row[2], "payload": payload,
            })
        return {"events": rows}
    except Exception as e:
        return {"events": [], "error": str(e)}


@app.get("/voice/ambient")
async def voice_ambient(limit: int = 100, since_minutes: int | None = None) -> dict[str, Any]:
    """Recent ambient sound events captured by the voice surface (Phase 7)."""
    from kee.perception.ambient_sound import recent_events
    return {"rows": recent_events(limit=limit, since_minutes=since_minutes)}


@app.get("/voice/speaker")
async def voice_speaker_state() -> dict[str, Any]:
    """Current voice-print enrollment state."""
    from kee.perception import speaker_id
    vp = speaker_id.load_print()
    from dataclasses import asdict as _asdict
    return _asdict(vp)


class SpeakerEnrollBody(BaseModel):
    label: str = "owner"
    sample_paths: list[str] = Field(default_factory=list)


@app.post("/voice/speaker/enroll")
async def voice_speaker_enroll(body: SpeakerEnrollBody) -> dict[str, Any]:
    """Build/refresh the owner voice-print from a list of WAV paths.

    The wake-word recorder at ``scripts/record_wake_word.py`` already drops
    16 kHz mono WAVs at ``models/wakeword/samples/positive/``. With no
    sample_paths provided, all those WAVs are used (great default — the
    wake-word recordings double as voice-print enrollment)."""
    from kee.perception import speaker_id
    import wave as _wave
    import numpy as _np

    paths = body.sample_paths or [
        str(p) for p in (settings.models_dir / "wakeword" / "samples" / "positive").glob("*.wav")
    ]
    if not paths:
        raise HTTPException(400, "no sample WAVs provided and none found at "
                                 "models/wakeword/samples/positive/")
    samples: list[_np.ndarray] = []
    for p in paths:
        try:
            with _wave.open(p, "rb") as wf:
                if wf.getframerate() != 16000:
                    continue
                raw = wf.readframes(wf.getnframes())
                samples.append(_np.frombuffer(raw, dtype=_np.int16))
        except Exception:
            continue
    if not samples:
        raise HTTPException(400, "no usable 16 kHz mono WAVs in supplied paths")
    vp = speaker_id.enroll(samples, label=body.label)
    from dataclasses import asdict as _asdict
    return {"ok": True, "enrolled_from": len(samples), "voice_print": _asdict(vp)}


@app.get("/cycle/state")
async def cycle_state() -> dict[str, Any]:
    """Read the latest Sleep Cycle output (vault/config/user_behavior.json)."""
    import json as _json
    p = settings.vault_dir / "config" / "user_behavior.json"
    if not p.exists():
        return {"exists": False}
    try:
        data = _json.loads(p.read_text(encoding="utf-8"))
        return {"exists": True, **data}
    except (OSError, _json.JSONDecodeError) as e:
        return {"exists": True, "error": str(e)}


@app.get("/identity/history")
async def identity_history(limit: int = 30) -> dict[str, Any]:
    """Return git-tracked changes to identity files (soul.md, identity.md,
    user.md, router.md). Phase 7 self-evolution audit trail.

    Each entry is a commit affecting any of those files: SHA, author,
    timestamp, summary, files touched. Pulled live from `git log` so the
    output reflects the current repo state."""
    import subprocess
    from pathlib import Path as _P
    files_rel = [
        "vault/config/soul.md",
        "vault/config/identity.md",
        "vault/config/user.md",
        "vault/config/router.md",
        "vault/config/goals.md",
    ]
    cwd = str(settings.project_root)
    out: list[dict[str, Any]] = []
    try:
        # %H sha · %ai date · %an author · %s subject
        cmd = [
            "git", "-C", cwd, "log",
            f"--max-count={max(1, min(limit, 200))}",
            "--pretty=format:%H|%ai|%an|%s",
            "--name-only",
            "--",
        ] + files_rel
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
        if r.returncode != 0:
            return {"error": r.stderr[:300] or "git log failed", "rows": []}
        # Parse: each entry separated by \n\n; first line is meta, then files
        for entry in r.stdout.split("\n\n"):
            entry = entry.strip()
            if not entry:
                continue
            lines = entry.split("\n")
            meta = lines[0]
            try:
                sha, date, author, subject = meta.split("|", 3)
            except ValueError:
                continue
            files_touched = [l for l in lines[1:] if l.strip()]
            out.append({
                "sha": sha[:10],
                "date": date,
                "author": author,
                "subject": subject,
                "files": files_touched,
            })
        return {"rows": out, "count": len(out)}
    except FileNotFoundError:
        return {"error": "git binary not on PATH", "rows": []}
    except subprocess.TimeoutExpired:
        return {"error": "git log timeout", "rows": []}


@app.get("/identity/diff/{sha}")
async def identity_diff(sha: str) -> dict[str, Any]:
    """Return the unified diff for a specific commit (sanity-checked: only
    accepts hex SHAs)."""
    import subprocess, re as _re
    if not _re.match(r"^[0-9a-fA-F]{4,40}$", sha):
        raise HTTPException(400, "invalid sha")
    cwd = str(settings.project_root)
    try:
        r = subprocess.run(
            ["git", "-C", cwd, "show", "--stat", "--patch", sha,
             "--", "vault/config/"],
            capture_output=True, text=True, timeout=10,
        )
        if r.returncode != 0:
            raise HTTPException(404, r.stderr[:200] or "commit not found")
        return {"sha": sha, "diff": r.stdout}
    except FileNotFoundError:
        raise HTTPException(500, "git not available")


@app.get("/cycle/proposals")
async def cycle_proposals() -> dict[str, Any]:
    """List all generated identity proposals (applied + pending)."""
    from kee.cognition.sleep_cycle import SleepCycleDaemon
    return {"proposals": SleepCycleDaemon.list_proposals()}


@app.get("/plans/recent")
async def plans_recent(
    limit: int = 20,
    pending_only: bool = False,
) -> dict[str, Any]:
    """List recent plans from `plan_history` (winner of each propose call).

    Powers the dashboard's planner timeline and lets Coco see what Kee has
    been thinking about lately.
    """
    from kee.tools.planner import _list_history
    return {"plans": _list_history(
        limit=limit, executed=(False if pending_only else None),
    )}


@app.post("/plans/{plan_id}/mark-executed")
async def plans_mark_executed(
    plan_id: int,
    outcome: str | None = None,
) -> dict[str, Any]:
    """Flip a plan to executed = 1 with optional `outcome` note. Used by
    the dashboard's planner timeline and by the agent itself via the
    `plan` tool's `mark_executed` action."""
    from kee.tools.planner import _mark_executed
    res = _mark_executed(int(plan_id), outcome)
    if not res.get("ok"):
        raise HTTPException(404, res.get("error", "not found"))
    return res


@app.get("/cycle/pending")
async def cycle_pending() -> dict[str, Any]:
    """One-stop "review queue" endpoint — every artefact Sleep Cycle has
    generated that's still waiting for human review.

    Powers a dashboard widget and the morning Telegram digest. Aggregates:
      - identity proposals (vault/_kee/identity_proposals/)
      - tool rewrite proposals (vault/_kee/tool_rewrites/)
      - self-evolution code proposals (vault/_kee/code_proposals/)
      - pending plans older than 7 days but newer than 30
        (the 30+ ones are auto-archived by Sleep Cycle Phase 11)
    """
    out: dict[str, Any] = {
        "identity_proposals": [],
        "tool_rewrites": [],
        "code_proposals": [],
        "stale_pending_plans": [],
    }

    base = settings.vault_dir / "_kee"

    def _list_md(subdir: str) -> list[dict[str, Any]]:
        d = base / subdir
        if not d.exists():
            return []
        rows = []
        for p in sorted(d.glob("*.md"), reverse=True):
            try:
                st = p.stat()
                rows.append({
                    "name": p.stem, "path": str(p),
                    "bytes": st.st_size,
                    "modified": datetime.utcfromtimestamp(st.st_mtime)
                                        .isoformat() + "Z",
                })
            except Exception:
                continue
        return rows

    out["identity_proposals"] = _list_md("identity_proposals")
    out["tool_rewrites"] = _list_md("tool_rewrites")
    out["code_proposals"] = _list_md("code_proposals")

    # Pending plans 7-30d old (older than 30d are auto-archived).
    try:
        con = db.get_connection()
        rows = con.execute(
            "SELECT id, timestamp, task FROM plan_history "
            "WHERE executed = 0 "
            "AND timestamp <= datetime('now', '-7 days') "
            "AND timestamp >= datetime('now', '-30 days') "
            "ORDER BY timestamp ASC"
        ).fetchall()
        out["stale_pending_plans"] = [
            {"id": r[0], "ts": str(r[1]), "task": (r[2] or "")[:120]}
            for r in rows
        ]
    except Exception:
        pass

    out["total"] = (
        len(out["identity_proposals"])
        + len(out["tool_rewrites"])
        + len(out["code_proposals"])
        + len(out["stale_pending_plans"])
    )
    return out


@app.get("/cycle/tool-rewrites")
async def cycle_tool_rewrites() -> dict[str, Any]:
    """List every tool-rewrite proposal Sleep Cycle has drafted.

    These live in `vault/_kee/tool_rewrites/<date>-<tool>.md` and contain
    a side-by-side of the current vs. proposed `description` text. Apply
    by hand: open the file, copy the proposed block into the tool's `.py`
    source, run `python -m kee.main check`, commit.
    """
    out = []
    base = settings.vault_dir / "_kee" / "tool_rewrites"
    if base.exists():
        for p in sorted(base.glob("*.md"), reverse=True):
            try:
                stat = p.stat()
                # Filename is `<date>-<tool>.md` — split on first dash
                # AFTER YYYY-MM-DD so multi-word tool names survive.
                stem = p.stem
                if len(stem) > 11 and stem[10] == "-":
                    date, tool = stem[:10], stem[11:]
                else:
                    date, tool = "?", stem
                out.append({
                    "date": date,
                    "tool": tool,
                    "path": str(p),
                    "bytes": stat.st_size,
                    "modified": datetime.utcfromtimestamp(stat.st_mtime)
                                        .isoformat() + "Z",
                })
            except Exception:
                continue
    return {"proposals": out}


@app.get("/cycle/tool-rewrites/{date}/{tool}")
async def cycle_tool_rewrite_get(date: str, tool: str) -> dict[str, Any]:
    """Return the markdown body of a single tool-rewrite proposal."""
    p = (settings.vault_dir / "_kee" / "tool_rewrites" /
         f"{date}-{tool}.md")
    if not p.exists():
        raise HTTPException(404, f"no proposal {date}/{tool}")
    return {"date": date, "tool": tool, "path": str(p),
            "body": p.read_text(encoding="utf-8")}


@app.post("/cycle/proposals/{proposal_date}/apply")
async def cycle_proposals_apply(proposal_date: str) -> dict[str, Any]:
    """Apply an identity proposal: appends the PROPUESTA block to soul.md
    with a date-stamped marker, marks the proposal file as APPLIED, and
    commits to git. Reversible via git revert. Idempotent (refuses if marker
    already present in soul.md)."""
    from kee.cognition.sleep_cycle import SleepCycleDaemon
    result = SleepCycleDaemon.apply_proposal(proposal_date)
    if not result.get("ok"):
        raise HTTPException(400, result.get("error", "unknown"))
    # Notify Coco that his identity just evolved
    try:
        from kee.perception.notifications import notify_user
        await notify_user(
            title="🧬 Identity proposal applied",
            body=(f"Proposal {proposal_date} merged into soul.md. "
                  f"+{result['soul_bytes_added']}b. "
                  f"git={'✓' if result['git_committed'] else 'skipped'}"),
            kind="identity_evolution",
        )
    except Exception:
        pass
    return result


@app.post("/cycle/run")
async def cycle_run() -> dict[str, Any]:
    """Trigger a one-shot Sleep Cycle. Free (uses local Ollama)."""
    try:
        from kee.cognition.sleep_cycle import SleepCycleDaemon
        agent = _agent()
        daemon = SleepCycleDaemon(llm=agent.llm)
        await daemon.run_once()
        return {"ok": True}
    except Exception as e:
        logger.exception("cycle/run failed")
        raise HTTPException(500, f"sleep cycle failed: {e}")


@app.get("/world-model/relations")
async def world_relations() -> dict[str, Any]:
    """Return all directed edges of the causal graph."""
    con = db.get_connection()
    rows = con.execute(
        "SELECT source_id, target_id, relation, weight, description "
        "FROM world_relations ORDER BY weight DESC"
    ).fetchall()
    return {
        "edges": [
            {"source": r[0], "target": r[1], "relation": r[2],
             "weight": float(r[3] or 0), "description": r[4]}
            for r in rows
        ]
    }


@app.get("/world-model/impact/{entity_id}")
async def world_impact(entity_id: str, max_depth: int = 3) -> dict[str, Any]:
    return wm.impact_score(entity_id, max_depth=max_depth)


@app.get("/economy/summary")
async def economy_summary(window_days: int | None = None) -> dict[str, Any]:
    return economy.summary(window_days=window_days)


@app.get("/economy/recent")
async def economy_recent(n: int = Query(20, ge=1, le=200)) -> dict[str, Any]:
    return {"entries": [e.to_dict() for e in economy.recent(n=n)]}


@app.get("/autonomy/summary")
async def autonomy_summary(window: int = 50) -> dict[str, Any]:
    return autonomy.summary(window=window)


# ── REST: digests + identity proposals ──────────────────────────────────
@app.get("/digest/today")
async def digest_today() -> dict[str, Any]:
    from datetime import date
    p = settings.vault_dir / "_kee" / "daily" / f"{date.today().isoformat()}.md"
    if not p.exists():
        raise HTTPException(404, "No digest for today. Run /sleep or wait for 04:00.")
    return {"date": date.today().isoformat(), "markdown": p.read_text(encoding="utf-8")}


@app.get("/brief")
async def brief_endpoint(
    include_inbox: bool = False,
    include_calendar: bool = False,
    save: bool = False,
) -> dict[str, Any]:
    """Composable markdown brief — wraps the `brief` tool. Use cases:
    Telegram /brief command, dashboard "current state" widget, or "save
    a snapshot to the vault" with `save=true`.
    """
    from kee.tools.brief import tool as brief_tool
    return await brief_tool.execute(
        include_inbox=include_inbox,
        include_calendar=include_calendar,
        save_to_vault=save,
    )


@app.get("/digest/snapshot")
async def digest_snapshot(
    window_days: int = 7,
    include_commits: bool = True,
    include_inbox: bool = False,
) -> dict[str, Any]:
    """On-demand `reflect` snapshot — same data Sleep Cycle uses for the
    morning brief, but available any time without waiting for 04:00.

    Powers a "what's the status" widget on the dashboard. Includes commits
    by default (free, just reads git log); inbox is opt-in because it
    requires Gmail auth.
    """
    from kee.tools.reflect import tool as reflect_tool
    return await reflect_tool.execute(
        window_days=window_days,
        include_commits=include_commits,
        include_inbox=include_inbox,
    )


@app.get("/proposals")
async def proposals_index() -> dict[str, Any]:
    d = settings.vault_dir / "_kee" / "identity_proposals"
    if not d.exists():
        return {"proposals": []}
    return {"proposals": [
        {"date": p.stem, "path": str(p), "size": p.stat().st_size}
        for p in sorted(d.glob("*.md"), reverse=True)
    ]}


@app.get("/proposals/{date}")
async def proposal_detail(date: str) -> dict[str, Any]:
    p = settings.vault_dir / "_kee" / "identity_proposals" / f"{date}.md"
    if not p.exists():
        raise HTTPException(404, f"No proposal for {date}.")
    return {"date": date, "markdown": p.read_text(encoding="utf-8")}


# ── POST /chat — synchronous turn ───────────────────────────────────────
class ChatRequest(BaseModel):
    message: str = Field(..., min_length=1)
    session_id: str = Field("default", description="Stable id to keep multi-turn context.")


class ChatResponse(BaseModel):
    response: str
    conversation_id: str
    iteration: int


@app.post("/chat", response_model=ChatResponse)
async def chat(req: ChatRequest) -> ChatResponse:
    a = _agent()
    state = _CONV_BY_SESSION.get(req.session_id)
    text, conv = await a.process(req.message, source="api", state=state)
    _CONV_BY_SESSION[req.session_id] = conv
    _broadcast({
        "type": "chat",
        "session_id": req.session_id,
        "user": req.message,
        "assistant": text,
        "conversation_id": conv.id,
        "ts": datetime.utcnow().isoformat(),
    })
    return ChatResponse(response=text, conversation_id=conv.id, iteration=conv.iteration)


@app.post("/chat/stream")
async def chat_stream_endpoint(req: ChatRequest):
    """Streaming chat — Server-Sent Events. Yields token chunks as they
    arrive from the active LLM provider. Skips tool-calling path (uses
    direct provider stream rather than the full agent loop) so the dashboard
    gets fast first-token + word-by-word reveal.

    Each SSE event is JSON: {"type": "delta", "text": "..."} or
    {"type": "done", "conversation_id": "...", "ts": "..."}.
    """
    a = _agent()

    async def event_stream():
        # Build minimal context for stream — system prompt + last user msg
        from kee.core.memory import ConversationState
        state = _CONV_BY_SESSION.get(req.session_id) or a.memory.start_conversation(source="api")
        # Refresh system prompt
        capabilities = a._build_capabilities_block()
        sys_prompt = a.identity.build_system_prompt(capabilities=capabilities, source="api")
        if state.messages and state.messages[0].get("role") == "system":
            state.messages[0] = {"role": "system", "content": sys_prompt}
        else:
            state.messages.insert(0, {"role": "system", "content": sys_prompt})
        state.messages.append({"role": "user", "content": req.message})
        a.memory.store_message(state.id, "user", req.message)
        _CONV_BY_SESSION[req.session_id] = state

        # Route via the router (so we still pick the right tier/provider)
        try:
            decision = await a.router.route(req.message, source="api")
        except Exception:
            decision = None
        force = (decision.provider_target if decision and decision.provider_target in
                 ("ollama", "claude", "haiku", "openai") else None)

        # Direct-answer fast path
        if decision and decision.tier == "direct" and decision.direct_reply:
            text = decision.direct_reply
            yield f"data: {json.dumps({'type': 'delta', 'text': text})}\n\n"
            state.messages.append({"role": "assistant", "content": text})
            a.memory.store_message(state.id, "assistant", text)
            yield f"data: {json.dumps({'type': 'done', 'conversation_id': state.id})}\n\n"
            return

        full = []
        try:
            async for chunk in a.chain.chat_stream(
                messages=state.messages, force_provider=force,
            ):
                full.append(chunk)
                yield f"data: {json.dumps({'type': 'delta', 'text': chunk})}\n\n"
        except Exception as e:
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)[:200]})}\n\n"
            return

        text = "".join(full)
        # Apply post-strip even on streaming (only stripped chunk-by-chunk
        # would be jittery — we let the client live-render then send a
        # final "replace" event with the cleaned text).
        from kee.core.agent import _strip_followup_offers
        cleaned = _strip_followup_offers(text)
        if cleaned != text:
            yield f"data: {json.dumps({'type': 'replace', 'text': cleaned})}\n\n"
            text = cleaned

        state.messages.append({"role": "assistant", "content": text})
        a.memory.store_message(state.id, "assistant", text)
        a.audit.log_response(state.id, text)
        _broadcast({
            "type": "chat", "session_id": req.session_id,
            "user": req.message, "assistant": text,
            "conversation_id": state.id,
            "ts": datetime.utcnow().isoformat(),
        })
        yield f"data: {json.dumps({'type': 'done', 'conversation_id': state.id})}\n\n"

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post("/chat/{session_id}/reset")
async def chat_reset(session_id: str) -> dict[str, Any]:
    existed = _CONV_BY_SESSION.pop(session_id, None) is not None
    return {"session_id": session_id, "had_state": existed, "status": "reset"}


@app.get("/chat/{session_id}/active")
async def chat_active(session_id: str) -> dict[str, Any]:
    """Return the active conversation for a session: conversation_id +
    full message history. Used by the dashboard on page load to restore
    the chat after a refresh / close — so a long task left running on
    the server is visible when the user comes back."""
    a = _agent()
    state = _CONV_BY_SESSION.get(session_id)
    # If no in-memory state, look up the most recent conversation for
    # this session from SQLite. That handles api process restart too.
    if state is None:
        with db.cursor() as cur:
            row = cur.execute(
                "SELECT id FROM conversations WHERE source IN ('api','dashboard') "
                "ORDER BY last_active DESC LIMIT 1"
            ).fetchone()
            cid = row[0] if row else None
        if not cid:
            return {"session_id": session_id, "conversation_id": None,
                    "messages": [], "active_in_memory": False}
        msgs = a.memory.get_messages(cid)
        return {"session_id": session_id, "conversation_id": cid,
                "messages": msgs, "active_in_memory": False}
    msgs = a.memory.get_messages(state.id)
    return {"session_id": session_id, "conversation_id": state.id,
            "messages": msgs, "active_in_memory": True}


# ── File attachments per session ─────────────────────────────────────────
def _attachments_dir(session_id: str):
    """Resolve and create the attachment dir for a chat session."""
    safe = "".join(c for c in session_id if c.isalnum() or c in "._-")[:64] or "default"
    p = settings.data_dir / "attachments" / safe
    p.mkdir(parents=True, exist_ok=True)
    return p


@app.post("/chat/{session_id}/attach")
async def chat_attach(
    session_id: str,
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a file into the session's attachments folder. Path is added
    to the active ConversationState so the next agent turn sees it as an
    available document.
    """
    safe_name = "".join(c for c in (file.filename or "file") if c.isalnum() or c in "._- ").strip()
    if not safe_name:
        safe_name = "upload"
    dest_dir = _attachments_dir(session_id)
    dest = dest_dir / safe_name
    # Stream to disk, cap at 25 MB so a runaway upload doesn't fill the disk
    MAX_BYTES = 25 * 1024 * 1024
    written = 0
    with dest.open("wb") as out:
        while True:
            chunk = await file.read(64 * 1024)
            if not chunk:
                break
            written += len(chunk)
            if written > MAX_BYTES:
                out.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, f"File exceeds {MAX_BYTES // (1024*1024)} MB cap")
            out.write(chunk)
    # Wire into the active conversation state, if any
    state = _CONV_BY_SESSION.get(session_id)
    if state and str(dest) not in state.attached_files:
        state.attached_files.append(str(dest))
    _broadcast({
        "type": "attachment",
        "session_id": session_id,
        "filename": safe_name,
        "bytes": written,
        "ts": datetime.utcnow().isoformat(),
    })
    return {
        "ok": True,
        "filename": safe_name,
        "path": str(dest),
        "bytes": written,
        "session_attached_count": len(state.attached_files) if state else 1,
    }


@app.get("/chat/{session_id}/attachments")
async def chat_attachments(session_id: str) -> dict[str, Any]:
    """List files attached in this session (from disk + state)."""
    state = _CONV_BY_SESSION.get(session_id)
    state_files = list(state.attached_files) if state else []
    p = _attachments_dir(session_id)
    on_disk = sorted(str(f) for f in p.iterdir() if f.is_file()) if p.exists() else []
    # Union (dedup, keep on-disk listing as source of truth for sizes)
    items = []
    seen: set[str] = set()
    for path_str in state_files + on_disk:
        if path_str in seen:
            continue
        seen.add(path_str)
        from pathlib import Path as _P
        f = _P(path_str)
        if not f.exists():
            continue
        items.append({
            "path": str(f),
            "name": f.name,
            "bytes": f.stat().st_size,
        })
    return {"session_id": session_id, "items": items}


@app.delete("/chat/{session_id}/attachments/{filename}")
async def chat_attachment_delete(session_id: str, filename: str) -> dict[str, Any]:
    safe_name = "".join(c for c in filename if c.isalnum() or c in "._- ")
    p = _attachments_dir(session_id) / safe_name
    if not p.exists():
        raise HTTPException(404, "not found")
    p.unlink()
    state = _CONV_BY_SESSION.get(session_id)
    if state:
        state.attached_files = [f for f in state.attached_files if f != str(p)]
    return {"ok": True, "deleted": str(p)}


# ── Cross-conversation memory inspection ─────────────────────────────────
@app.get("/memory/recent_summaries")
async def memory_recent_summaries(limit: int = 10) -> dict[str, Any]:
    a = _agent()
    rows = a.memory.recent_summaries(limit=max(1, min(limit, 50)))
    return {"rows": rows}


@app.post("/memory/summarize_stale")
async def memory_summarize_stale(idle_minutes: int = 15) -> dict[str, Any]:
    """Find conversations idle for N+ minutes without a summary, and
    summarize them via the local Ollama provider (free). Returns IDs done.
    """
    a = _agent()
    ids = a.memory.stale_conversations(idle_minutes=idle_minutes, limit=20)
    done: list[str] = []
    for cid in ids:
        try:
            s = await a.memory.summarize_conversation(cid, llm=a.llm)
            if s:
                done.append(cid)
        except Exception as e:
            logger.warning("summarize_conversation %s failed: %s", cid, e)
    return {"summarized": done, "count": len(done)}


# ── Provider chain + cost tracking endpoints ─────────────────────────────
@app.get("/llm/providers")
async def llm_providers() -> dict[str, Any]:
    """Live state of the multi-provider chain."""
    a = _agent()
    chain = getattr(a, "chain", None)
    if not chain:
        return {"providers": [], "primary": None}
    health = await chain.health_all()
    return {
        "primary": chain.primary.name,
        "providers": [
            {
                "name": p.name,
                "model": p.model_name,
                "cost_in_per_mtok": p.cost_in_per_mtok,
                "cost_out_per_mtok": p.cost_out_per_mtok,
                "healthy": health.get(p.name, False),
                "is_primary": p.name == chain.primary.name,
            }
            for p in chain.providers
        ],
    }


@app.get("/llm/cost")
async def llm_cost() -> dict[str, Any]:
    """Today's cost ticker + per-provider breakdown."""
    from kee.core.llm.cost_tracker import status, by_provider_today
    return {
        "today": status(),
        "by_provider": by_provider_today(),
    }


@app.get("/llm/recent")
async def llm_recent(limit: int = 30) -> dict[str, Any]:
    """Most-recent LLM calls with provenance for a live ticker."""
    con = db.get_connection()
    rows = con.execute(
        "SELECT id, timestamp, provider, model_name, tier, latency_ms, "
        "       tokens_in, tokens_out, cost_usd, success "
        "FROM audit_log WHERE action='llm_call' "
        "ORDER BY id DESC LIMIT ?",
        (max(1, min(limit, 200)),),
    ).fetchall()
    return {
        "rows": [
            {
                "id": r[0], "timestamp": r[1],
                "provider": r[2], "model": r[3], "tier": r[4],
                "latency_ms": r[5], "tokens_in": r[6], "tokens_out": r[7],
                "cost_usd": r[8], "success": bool(r[9]),
            }
            for r in rows
        ]
    }


@app.get("/router/config")
async def router_config() -> dict[str, Any]:
    """Return the parsed router.md (direct rules + tier hints) for the
    settings UI."""
    a = _agent()
    r = getattr(a, "router", None)
    if not r:
        return {"direct_rules": [], "tier_hints": {}}
    r._load_config()
    return {
        "direct_rules": [
            {"pattern": pat.pattern, "reply": rep}
            for pat, rep in r._direct_rules
        ],
        "tier_hints": {
            tier: [pat.pattern for pat in pats]
            for tier, pats in r._tier_hints.items()
        },
    }


class SettingsUpdate(BaseModel):
    daily_cap_usd: float | None = None
    primary: str | None = None  # claude | openai | ollama
    model: str | None = None
    code_agent: str | None = None  # keecode | claude_code | opencode
    code_agent_model: str | None = None
    opencode_command: str | None = None
    opencode_repo: str | None = None


class KeeCodeContextBody(BaseModel):
    notes: str = ""
    session_id: str = "dashboard"


class KeeCodeLaunchBody(BaseModel):
    prompt: str = ""
    workdir: str | None = None
    model: str | None = None


@app.get("/keecode/status")
async def keecode_status() -> dict[str, Any]:
    from kee.integrations import keecode
    try:
        keecode.write_opencode_config()
    except Exception:
        logger.debug("keecode config write skipped", exc_info=True)
    return keecode.status()


@app.post("/keecode/context")
async def keecode_context(body: KeeCodeContextBody) -> dict[str, Any]:
    from kee.integrations import keecode
    path = keecode.write_context_bridge(
        notes=body.notes,
        session_id=body.session_id,
    )
    return {"ok": True, "context_path": str(path), **keecode.status()}


@app.post("/keecode/launch")
async def keecode_launch(body: KeeCodeLaunchBody) -> dict[str, Any]:
    from kee.integrations import keecode
    return keecode.launch_terminal(
        workdir=body.workdir,
        prompt=body.prompt,
        model=body.model,
    )


@app.post("/settings")
async def update_settings(req: SettingsUpdate) -> dict[str, Any]:
    """Live-update env settings (writes to .env). When the LLM provider
    primary changes, ALSO rebuild the agent's chain in-place so the
    change takes effect immediately without a process restart."""
    import os, re
    env_path = settings.project_root / ".env"
    if not env_path.exists():
        return {"ok": False, "error": ".env missing"}
    text = env_path.read_text(encoding="utf-8")
    changed = []
    rebuild_chain = False

    def set_env_value(key: str, value: str, label: str) -> None:
        nonlocal text
        if re.search(rf"^{re.escape(key)}=", text, re.MULTILINE):
            text = re.sub(
                rf"^{re.escape(key)}=.*$",
                f"{key}={value}",
                text,
                flags=re.MULTILINE,
            )
        else:
            text = text.rstrip() + f"\n{key}={value}\n"
        os.environ[key] = value
        changed.append(label)

    if req.daily_cap_usd is not None:
        # cost_tracker reads env each call — no rebuild needed
        if re.search(r"^KEE_DAILY_COST_CAP_USD=", text, re.MULTILINE):
            text = re.sub(r"^KEE_DAILY_COST_CAP_USD=.*$",
                          f"KEE_DAILY_COST_CAP_USD={req.daily_cap_usd}",
                          text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + f"\nKEE_DAILY_COST_CAP_USD={req.daily_cap_usd}\n"
        os.environ["KEE_DAILY_COST_CAP_USD"] = str(req.daily_cap_usd)
        changed.append("daily_cap_usd")
    if req.primary in ("claude", "haiku", "openai", "ollama"):
        if re.search(r"^KEE_LLM_PRIMARY=", text, re.MULTILINE):
            text = re.sub(r"^KEE_LLM_PRIMARY=.*$",
                          f"KEE_LLM_PRIMARY={req.primary}",
                          text, flags=re.MULTILINE)
        else:
            text = text.rstrip() + f"\nKEE_LLM_PRIMARY={req.primary}\n"
        os.environ["KEE_LLM_PRIMARY"] = req.primary
        changed.append("primary")
        rebuild_chain = True
    if req.model is not None and req.model.strip():
        model = req.model.strip()
        set_env_value("KEE_MODEL", model, "model")
        settings.model = model
        if _STATE.agent is not None:
            _STATE.agent.llm.model = model
        rebuild_chain = True
    if req.code_agent in ("keecode", "opencode", "claude_code"):
        set_env_value("KEE_CODE_AGENT", req.code_agent, "code_agent")
    if req.code_agent_model is not None and req.code_agent_model.strip():
        set_env_value("KEE_CODE_AGENT_MODEL", req.code_agent_model.strip(), "code_agent_model")
    if req.opencode_command is not None and req.opencode_command.strip():
        set_env_value("KEE_OPENCODE_COMMAND", req.opencode_command.strip(), "opencode_command")
    if req.opencode_repo is not None and req.opencode_repo.strip():
        set_env_value("KEE_OPENCODE_REPO", req.opencode_repo.strip(), "opencode_repo")
    env_path.write_text(text, encoding="utf-8")
    rebuilt = False
    if rebuild_chain and _STATE.agent is not None:
        try:
            from kee.core.llm.chain import build_default_chain
            _STATE.agent.chain = build_default_chain()
            rebuilt = True
        except Exception as e:
            logger.warning("chain rebuild failed: %s", e)
    return {"ok": True, "changed": changed, "chain_rebuilt": rebuilt}


@app.post("/agent/rebuild")
async def agent_rebuild() -> dict[str, Any]:
    """Force-rebuild the LLM chain (re-reads env for provider order +
    keys) and re-instantiate the router. Useful after editing .env or
    router.md from outside the dashboard."""
    a = _agent()
    from kee.core.llm.chain import build_default_chain
    from kee.core.router import Router
    a.chain = build_default_chain()
    a.router = Router()
    return {
        "ok": True,
        "chain_providers": [p.name for p in a.chain.providers],
        "primary": a.chain.primary.name,
    }


@app.get("/system/hallucinations")
async def system_hallucinations(window_days: int = 7) -> dict[str, Any]:
    """Roll-up of `kwarg_hallucination` audit rows by tool.

    The dashboard Tools page renders this as a "tools the LLM keeps misusing"
    leaderboard. Sleep Cycle's nightly axiom phase reads it too. Each entry::

        {tool: 'weather', count: 12, top_kwargs: [['query', 8], ['city', 4]]}

    Returns the top-20 sorted by count desc.
    """
    from collections import Counter as _Counter
    con = db.get_connection()
    try:
        rows = con.execute(
            "SELECT tool_name, parameters FROM audit_log "
            "WHERE action='kwarg_hallucination' "
            "AND timestamp >= datetime('now', ? || ' days')",
            (f"-{int(window_days)}",),
        ).fetchall()
    except Exception as e:
        return {"window_days": window_days, "tools": [], "error": str(e)}
    by_tool: dict[str, _Counter] = {}
    for tool_name, raw in rows:
        if not tool_name or not raw:
            continue
        try:
            payload = json.loads(raw)
        except Exception:
            continue
        c = by_tool.setdefault(tool_name, _Counter())
        for u in (payload.get("unknown") or []):
            c[u] += 1
    out = [
        {"tool": name, "count": sum(c.values()),
         "top_kwargs": c.most_common(5)}
        for name, c in by_tool.items()
    ]
    out.sort(key=lambda r: r["count"], reverse=True)
    return {"window_days": window_days, "tools": out[:20],
            "total": sum(r["count"] for r in out)}


@app.get("/system/daemons")
async def system_daemons() -> dict[str, Any]:
    """Discover Kee processes currently running on this host. Cross-platform
    via psutil — reports api, telegram, voice, notif-bridge, terminal,
    sleep-cycle. Used by the dashboard's Health page.
    """
    out: list[dict[str, Any]] = []
    try:
        import psutil
        for p in psutil.process_iter(["pid", "name", "cmdline", "create_time", "cpu_percent", "memory_info"]):
            try:
                cmd = " ".join(p.info.get("cmdline") or [])
                if "kee.main" not in cmd:
                    continue
                # Identify surface from cmdline tokens
                surface = "?"
                for token in ["telegram", "voice", "api", "watch", "heartbeat",
                              "sleep-cycle", "notif-bridge", "terminal"]:
                    if token in cmd.split():
                        surface = token
                        break
                mem = p.info.get("memory_info")
                out.append({
                    "pid": p.info["pid"],
                    "surface": surface,
                    "started_at": p.info.get("create_time"),
                    "cpu_pct": p.info.get("cpu_percent") or 0.0,
                    "rss_mb": round((mem.rss / (1024 * 1024)) if mem else 0, 1),
                    "cmd": cmd[:120],
                })
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
    except ImportError:
        return {"error": "psutil not installed", "rows": []}
    return {"rows": sorted(out, key=lambda r: r["surface"])}


@app.get("/self_evolution/proposals")
async def self_evolution_proposals() -> dict[str, Any]:
    """List code-improvement proposals Kee has drafted (Phase 7)."""
    from kee.cognition import self_evolution as se
    return {"proposals": se.list_proposals()}


@app.get("/self_evolution/proposals/{proposal_date}")
async def self_evolution_proposal(proposal_date: str) -> dict[str, Any]:
    from kee.cognition import self_evolution as se
    out = se.read_proposal(proposal_date)
    if not out.get("exists"):
        raise HTTPException(404, f"no proposal for {proposal_date}")
    return out


@app.post("/self_evolution/draft")
async def self_evolution_draft(window_days: int = 7) -> dict[str, Any]:
    """Generate a fresh proposal from the last `window_days` of runtime data.
    Idempotent for the day (dedup by friction-hash). Free Ollama call."""
    from kee.cognition import self_evolution as se
    return await se.draft_proposal(window_days=window_days)


@app.post("/self_evolution/proposals/{proposal_date}/apply")
async def self_evolution_apply(proposal_date: str) -> dict[str, Any]:
    """Spawn claude_code against the proposal text in a new git branch.
    **Costs Claude Pro/Max time** — explicit opt-in only."""
    from kee.cognition import self_evolution as se
    return await se.apply_via_claude_code(proposal_date)


@app.post("/desktop/signal")
async def desktop_signal(body: dict[str, Any]) -> dict[str, Any]:
    """Drop a signal for the running Kee desktop window.

    Body: ``{"action": "show|hide|toggle|switch_mode", "mode": "hud|full",
              "reason": "free-form"}``

    The desktop process polls ``data/desktop_signal.json`` every 250 ms
    and consumes the file. If no desktop process is running the signal
    just sits there until one starts (or it's overwritten by the next
    signal).
    """
    from kee.desktop.app import write_signal
    action = (body.get("action") or "show").lower()
    mode = (body.get("mode") or "hud").lower()
    reason = (body.get("reason") or "manual")
    write_signal(action, mode=mode, reason=reason,
                 extra={k: v for k, v in body.items()
                        if k not in ("action", "mode", "reason")})
    return {"ok": True, "action": action, "mode": mode, "reason": reason}


@app.post("/biometric/sample")
async def biometric_sample(body: dict[str, Any]) -> dict[str, Any]:
    """Ingest one or many biometric samples.

    Single: ``{"kind": "hr_resting", "value": 62, "unit": "bpm", "source": "garmin"}``
    Bulk:   ``{"samples": [{"kind": ..., "value": ...}, ...]}``

    Phase 8 §"Biometric telemetry". The pipeline is source-agnostic so
    Health Connect on Android, an Apple Watch shortcut, a Garmin export
    script, or manual entry all work.
    """
    from kee.perception import biometric as bio
    if "samples" in body:
        n = bio.insert_many(body["samples"])
        return {"ok": True, "inserted": n}
    if "kind" not in body or "value" not in body:
        raise HTTPException(400, "need {kind, value} or {samples: [...]}")
    sid = bio.insert(
        kind=body["kind"],
        value=float(body["value"]),
        unit=body.get("unit", ""),
        source=body.get("source", "manual"),
        note=body.get("note", ""),
        timestamp=body.get("timestamp"),
    )
    return {"ok": True, "id": sid}


@app.get("/biometric/recent")
async def biometric_recent(
    limit: int = 50,
    kind: str | None = None,
    since_hours: int | None = None,
) -> dict[str, Any]:
    from kee.perception import biometric as bio
    return {"rows": bio.recent(limit=limit, kind=kind, since_hours=since_hours)}


@app.get("/biometric/state")
async def biometric_state(window_hours: int = 12) -> dict[str, Any]:
    """Current energy_level + which biometric signals contributed."""
    from kee.perception import biometric as bio
    return bio.score_recent_state(window_hours=window_hours)


@app.post("/edge/ask")
async def edge_ask(body: dict[str, Any]) -> dict[str, Any]:
    """Lean text-in / text-out endpoint for resource-constrained mobile
    clients (Termux on Android, embedded HUDs, etc.). One JSON in, one
    JSON out — no streaming, no SSE, no large payloads.

    Body: ``{"text": "...", "session": "optional-session-id"}``
    Returns: ``{"reply": "...", "tier": "...", "elapsed_ms": int, "session": "..."}``

    Phase 7 §"Termux mobile edge client". Auth is the same bearer-token
    gate as the rest of the API (KEE_API_TOKEN env when set).
    """
    text = (body.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "missing 'text'")
    session = body.get("session") or "edge-default"
    import time as _time
    t0 = _time.monotonic()
    a = _agent()
    state = _CONV_BY_SESSION.get(session)
    response, conv = await a.process(text, source="edge", state=state)
    _CONV_BY_SESSION[session] = conv
    return {
        "reply": (response or "").strip(),
        "session": session,
        "elapsed_ms": int((_time.monotonic() - t0) * 1000),
    }


@app.get("/fleet")
async def fleet_state() -> dict[str, Any]:
    """Snapshot of every configured node — primary + workers — with per-node
    reachability + service health (Ollama, ChromaDB, API).

    Edit ``vault/config/fleet.json`` to register more nodes. Phase 6 close.
    """
    from kee.distributed.fleet import probe_fleet
    return await probe_fleet()


@app.get("/episodic/query")
async def episodic_query(
    q: str,
    n: int = 5,
    kinds: str | None = None,
) -> dict[str, Any]:
    """Semantic recall over the unified episodic index. `kinds` is a
    comma-separated filter, e.g. `kinds=plan,dispatch,focus`."""
    from kee.cognition.episodic_indexer import EpisodicIndexer
    kinds_list = [k.strip() for k in kinds.split(",")] if kinds else None
    return await EpisodicIndexer().query(
        query=q, n_results=int(n), kinds=kinds_list,
    )


@app.post("/episodic/reindex")
async def episodic_reindex(window_days: int = 7) -> dict[str, Any]:
    """Force a rebuild of the episodic ChromaDB index over the last
    `window_days`. Same logic Sleep Cycle Phase 13 runs nightly."""
    from kee.cognition.episodic_indexer import EpisodicIndexer
    return await EpisodicIndexer().index_window(window_days=int(window_days))


@app.get("/narrate/{day}")
async def narrate_day_endpoint(day: str) -> dict[str, Any]:
    """Markdown timeline of a given day. Accepts `today`, `yesterday`,
    or `YYYY-MM-DD`. Used by the dashboard's daily-brief widget and by
    end-of-day journaling flows."""
    from kee.tools.narrate_day import tool as nd
    return await nd.execute(date=day)


@app.get("/system/version")
async def system_version() -> dict[str, Any]:
    """Build metadata: commit hash, branch, tool count, schema version,
    environment summary. Used to confirm two Kee installs are at the
    same revision (Alienware vs Auctorum, or after a deploy).
    """
    import subprocess
    info: dict[str, Any] = {
        "ts": datetime.utcnow().isoformat() + "Z",
        "python": __import__("sys").version.split()[0],
    }
    # Git introspection — graceful when repo missing (e.g. pip install)
    try:
        head = subprocess.run(
            ["git", "-C", str(settings.project_root if hasattr(settings, 'project_root') else '.'),
             "rev-parse", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if head.returncode == 0:
            info["commit"] = head.stdout.strip()[:12]
        branch = subprocess.run(
            ["git", "-C", str(settings.project_root if hasattr(settings, 'project_root') else '.'),
             "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if branch.returncode == 0:
            info["branch"] = branch.stdout.strip()
        count = subprocess.run(
            ["git", "-C", str(settings.project_root if hasattr(settings, 'project_root') else '.'),
             "rev-list", "--count", "HEAD"],
            capture_output=True, text=True, timeout=2,
        )
        if count.returncode == 0:
            info["commits"] = int(count.stdout.strip())
    except Exception:
        pass

    # Live runtime metrics
    try:
        a = _agent()
        info["tools"] = len(a.registry.tools)
    except Exception:
        info["tools"] = None

    # Worker config visibility (NOT credentials, just URLs)
    import os
    info["worker"] = {
        "host": os.environ.get("AUCTORUM_HOST"),
        "chroma_configured": bool(os.environ.get("CHROMADB_HOST")),
        "reranker_configured": bool(os.environ.get("KEE_RERANKER_URL")),
        "vision_configured": bool(os.environ.get("KEE_VISION_URL")),
    }
    return info


@app.get("/worker/dashboard", response_class=None)
async def worker_dashboard():
    """Standalone HTML page that shows the worker stack at a glance.

    No SvelteKit dependency — self-contained, served straight from the
    API. Auto-refreshes every 5s. Open at
    http://127.0.0.1:7330/worker/dashboard
    """
    import time
    from fastapi.responses import HTMLResponse
    from kee.cognition.worker_reindex import _load_state as _ri_state

    # Pull a live snapshot
    snap = {}
    try:
        from kee.tools.worker_health import tool as wh
        snap = await wh.execute(action="snapshot", timeout_s=4)
    except Exception as e:
        snap = {"ok": False, "error": str(e)}

    fleet_snap = {}
    try:
        from kee.distributed.fleet import probe_fleet
        fleet_snap = await probe_fleet()
    except Exception:
        pass

    reindex_state = _ri_state()

    body = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>Kee — Worker dashboard</title>
  <meta http-equiv="refresh" content="5">
  <style>
    body {{ font: 14px/1.5 -apple-system, Segoe UI, Roboto, sans-serif;
            background: #0a0e14; color: #c4d2e5; margin: 0; padding: 24px; }}
    h1 {{ font-size: 18px; color: #4cc9f0; margin: 0 0 8px; }}
    .ts {{ color: #5a7a9a; font-size: 12px; margin-bottom: 24px; }}
    .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(280px, 1fr)); gap: 16px; }}
    .card {{ background: #131b26; border: 1px solid #1f2b3a; border-radius: 8px;
             padding: 16px; }}
    .card h2 {{ font-size: 13px; color: #8fa5c0; text-transform: uppercase;
                letter-spacing: 0.05em; margin: 0 0 12px; }}
    .row {{ display: flex; justify-content: space-between; padding: 4px 0;
            border-bottom: 1px solid #1a2533; }}
    .row:last-child {{ border-bottom: none; }}
    .ok {{ color: #4ade80; }}
    .down {{ color: #f87171; }}
    .num {{ color: #fde047; font-variant-numeric: tabular-nums; }}
    pre {{ background: #0a0e14; padding: 8px; overflow-x: auto;
           font-size: 11px; color: #94a3b8; }}
  </style>
</head>
<body>
  <h1>Kee — Worker dashboard</h1>
  <div class="ts">auto-refresh 5s · {datetime.now().strftime("%Y-%m-%d %H:%M:%S")} ·
    POST /worker/reindex?force=true to manually re-index ·
    open in voice for live narration</div>

  <div class="grid">"""

    # Worker subsystems card
    subs = (snap.get("subsystems") or [])
    body += '<div class="card"><h2>Worker subsystems</h2>'
    if not subs:
        body += '<div class="row down">offline / unreachable</div>'
    for s in subs:
        ok = s.get("ok")
        cls = "ok" if ok else "down"
        symbol = "OK" if ok else "DOWN"
        line = f"{s.get('name', '?')}"
        if "elapsed_ms" in s:
            line += f"  <span class='num'>{s['elapsed_ms']} ms</span>"
        body += (f'<div class="row"><span class="{cls}">[{symbol}]</span>'
                 f'<span>{line}</span></div>')
    body += "</div>"

    # GPU + load card
    gpu = next((s for s in subs if s.get("name") == "gpu"), {})
    disk = next((s for s in subs if s.get("name") == "disk"), {})
    load = next((s for s in subs if s.get("name") == "load"), {})
    body += '<div class="card"><h2>Hardware</h2>'
    if gpu.get("ok"):
        body += (f'<div class="row"><span>GPU</span>'
                 f'<span>{gpu.get("model", "?")}</span></div>'
                 f'<div class="row"><span>VRAM</span>'
                 f'<span class="num">{gpu.get("mem_used_mb", 0)} / '
                 f'{gpu.get("mem_total_mb", 0)} MB '
                 f'({gpu.get("mem_used_pct", 0)}%)</span></div>'
                 f'<div class="row"><span>util</span>'
                 f'<span class="num">{gpu.get("util_pct", 0)}%</span></div>')
    if disk.get("ok"):
        body += (f'<div class="row"><span>Disk</span>'
                 f'<span class="num">{disk.get("free_gb", 0)} GB free '
                 f'({disk.get("percent", 0)}%)</span></div>')
    if load.get("ok"):
        body += (f'<div class="row"><span>CPU</span>'
                 f'<span class="num">{load.get("cpu_pct", 0)}%</span></div>'
                 f'<div class="row"><span>RAM</span>'
                 f'<span class="num">{load.get("ram_used_gb", 0)} / '
                 f'{load.get("ram_total_gb", 0)} GB '
                 f'({load.get("ram_pct", 0)}%)</span></div>')
    body += "</div>"

    # Last re-index card
    body += '<div class="card"><h2>Last re-index</h2>'
    if reindex_state.get("last_run_ts"):
        ago_s = int(time.time() - reindex_state["last_run_ts"])
        ago_h = ago_s / 3600
        body += (f'<div class="row"><span>indexed</span>'
                 f'<span class="num">{reindex_state.get("last_indexed", 0)}</span></div>'
                 f'<div class="row"><span>elapsed</span>'
                 f'<span class="num">{reindex_state.get("last_elapsed_s", 0)} s</span></div>'
                 f'<div class="row"><span>age</span>'
                 f'<span class="num">{ago_h:.1f} h</span></div>')
    else:
        body += '<div class="row"><span>never run</span><span>—</span></div>'
    body += "</div>"

    # Fleet card
    body += '<div class="card"><h2>Fleet</h2>'
    for n in (fleet_snap.get("nodes") or []):
        cls = "ok" if n.get("alive") else "down"
        sym = "OK" if n.get("alive") else "DOWN"
        body += (f'<div class="row"><span class="{cls}">[{sym}]</span>'
                 f'<span>{n.get("name", "?")} '
                 f'<span class="num">{n.get("ping_ms", 0)} ms</span></span></div>')
    body += "</div>"

    # Today's narrative + episodic stats card
    today_counts: dict[str, int] = {}
    try:
        from kee.tools.narrate_day import tool as nd
        nd_out = await nd.execute(date="today")
        today_counts = nd_out.get("counts", {})
    except Exception:
        pass
    body += '<div class="card"><h2>Today (so far)</h2>'
    if today_counts:
        for k, v in today_counts.items():
            if v == 0:
                continue
            body += (f'<div class="row"><span>{k}</span>'
                     f'<span class="num">{v}</span></div>')
        if all(v == 0 for v in today_counts.values()):
            body += '<div class="row"><span>nothing logged yet</span><span>—</span></div>'
    else:
        body += '<div class="row"><span>narrate_day error</span><span>—</span></div>'
    body += '<div class="row" style="margin-top:8px"><span>view full</span>'
    body += '<a href="/narrate/today" style="color:#4cc9f0">/narrate/today</a></div>'
    body += "</div>"

    # Episodic memory card
    body += '<div class="card"><h2>Episodic memory</h2>'
    try:
        con = db.get_connection()
        # Sample query metadata via direct SQL count of source rows
        n_conv = con.execute(
            "SELECT COUNT(*) FROM conversations WHERE summary IS NOT NULL"
        ).fetchone()[0]
        n_disp = con.execute("SELECT COUNT(*) FROM dispatches").fetchone()[0]
        n_plan = con.execute("SELECT COUNT(*) FROM plan_history").fetchone()[0]
        n_focus = con.execute("SELECT COUNT(*) FROM focus_sessions").fetchone()[0]
        n_learn = con.execute(
            "SELECT COUNT(*) FROM learnings WHERE forgotten = 0"
        ).fetchone()[0]
        n_perc = con.execute(
            "SELECT COUNT(*) FROM audit_log WHERE action='perception_screenshot'"
        ).fetchone()[0]
        body += (f'<div class="row"><span>conversations summarised</span>'
                 f'<span class="num">{n_conv}</span></div>'
                 f'<div class="row"><span>dispatches</span>'
                 f'<span class="num">{n_disp}</span></div>'
                 f'<div class="row"><span>plans</span>'
                 f'<span class="num">{n_plan}</span></div>'
                 f'<div class="row"><span>focus sessions</span>'
                 f'<span class="num">{n_focus}</span></div>'
                 f'<div class="row"><span>learnings (active)</span>'
                 f'<span class="num">{n_learn}</span></div>'
                 f'<div class="row"><span>perception events</span>'
                 f'<span class="num">{n_perc}</span></div>')
    except Exception as e:
        body += f'<div class="row"><span>error</span><span>{e}</span></div>'
    body += '<div class="row" style="margin-top:8px"><span>query</span>'
    body += '<a href="/episodic/query?q=auctorum" style="color:#4cc9f0">/episodic/query?q=…</a></div>'
    body += "</div>"

    body += "</div></body></html>"
    return HTMLResponse(content=body)


@app.post("/worker/reindex")
async def worker_reindex_now(force: bool = True) -> dict[str, Any]:
    """Trigger a vault re-index against the worker's ChromaDB on demand.

    Internally calls Sleep Cycle Phase 12's `maybe_reindex(force=…)`
    helper — same logic, but driven by Coco from the dashboard or by
    the agent on his behalf instead of waiting for 04:00.
    """
    from kee.cognition.worker_reindex import maybe_reindex
    return await maybe_reindex(force=bool(force))


@app.get("/system/supervisor")
async def system_supervisor() -> dict[str, Any]:
    """Read the latest supervisor snapshot.

    Returns ``{running: bool, supervisor_pid, surfaces: [...], stale_s}``
    where each surface dict carries ``alive``, ``pid``, ``uptime_s``,
    ``restarts``, ``last_exit_code``, ``backoff_s``, ``log_path``.

    The supervisor refreshes ``data/supervisor_state.json`` every second;
    ``running=False`` means the supervisor isn't up (or crashed). Daemons
    can still be alive without the supervisor — they just won't auto-restart.
    """
    from kee.daemon.supervisor import read_state
    return read_state()


@app.get("/system/logs/{name}")
async def system_logs(name: str, tail: int = 100) -> dict[str, Any]:
    """Return the tail of a known log file. Whitelisted by name to prevent
    arbitrary file reads."""
    safe = {
        "api": settings.data_dir / "api.err",
        "telegram": settings.data_dir / "telegram_bot.err",
        "voice": settings.data_dir / "voice.log",
        "notif_bridge": settings.data_dir / "notif_bridge.err",
        "wake_train": settings.data_dir / "wake_train.log",
        "vite": settings.data_dir / "vite.log",
    }
    if name not in safe:
        raise HTTPException(404, f"unknown log '{name}'. valid: {list(safe)}")
    path = safe[name]
    if not path.exists():
        return {"name": name, "path": str(path), "exists": False, "lines": []}
    try:
        # Read last N lines without slurping the whole file when huge
        with path.open("r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-max(1, min(tail, 500)):]
        return {
            "name": name, "path": str(path), "exists": True,
            "lines": [ln.rstrip("\n") for ln in lines],
            "total_returned": len(lines),
        }
    except Exception as e:
        raise HTTPException(500, f"read failed: {e}")


class ToolExecBody(BaseModel):
    arguments: dict[str, Any] = Field(default_factory=dict)


@app.post("/tools/{name}/execute")
async def tools_execute(name: str, body: ToolExecBody) -> dict[str, Any]:
    """Run a registered tool with explicit args. Restricted to risk_level <= 1
    (read-only / local-only) tools to avoid accidental dangerous actions
    via the dashboard. Audited like any other tool call."""
    a = _agent()
    tool = a.registry.tools.get(name)
    if not tool:
        raise HTTPException(404, f"tool '{name}' not registered")
    risk = getattr(tool, "risk_level", 0)
    if risk > 0:
        raise HTTPException(
            403,
            f"tool '{name}' has risk_level={risk}; dashboard tester is "
            "limited to risk_level==0 (read-only). For mutating tools "
            "use the agent (chat) — it goes through the verification loop."
        )
    import time as _time
    t0 = _time.monotonic()
    try:
        result = await tool.execute(**(body.arguments or {}))
        elapsed = int((_time.monotonic() - t0) * 1000)
        try:
            a.audit.log_action(
                conversation_id="dashboard-tester",
                tool_name=name,
                parameters=body.arguments,
                result=result,
                risk_level=risk,
                success=True,
            )
        except Exception:
            pass
        return {"ok": True, "result": result, "elapsed_ms": elapsed}
    except Exception as e:
        elapsed = int((_time.monotonic() - t0) * 1000)
        return {"ok": False, "error": str(e), "elapsed_ms": elapsed}


@app.get("/tools/{name}/source")
async def tools_source(name: str) -> dict[str, Any]:
    """Return the source code of a tool. Built-in tools live in
    `kee/tools/`, custom ones in `vault/_kee/tools/`. Resolved via the
    registered tool's `file_path` attribute when available, else by
    convention. Source is read-only (UI displays for inspection)."""
    a = _agent()
    tool = a.registry.tools.get(name)
    if not tool:
        raise HTTPException(404, f"tool '{name}' not registered")
    fp = getattr(tool, "file_path", None)
    if not fp:
        # fallback by convention
        from pathlib import Path as _P
        candidates = [
            settings.project_root / "kee" / "tools" / f"{name}.py",
            settings.vault_dir / "_kee" / "tools" / f"{name}.py",
        ]
        fp = next((str(c) for c in candidates if _P(c).exists()), None)
    if not fp:
        raise HTTPException(404, f"source file not found for '{name}'")
    try:
        from pathlib import Path as _P
        text = _P(fp).read_text(encoding="utf-8")
    except Exception as e:
        raise HTTPException(500, f"read failed: {e}")
    return {
        "name": name,
        "path": str(fp),
        "lines": text.count("\n") + 1,
        "bytes": len(text.encode("utf-8")),
        "source": text,
    }


@app.post("/llm/test_provider/{name}")
async def llm_test_provider(name: str) -> dict[str, Any]:
    """Run a 1-token health probe against a single provider in the chain."""
    a = _agent()
    chain = getattr(a, "chain", None)
    if not chain:
        return {"ok": False, "error": "no chain"}
    target = next((p for p in chain.providers if p.name == name), None)
    if not target:
        return {"ok": False, "error": f"provider '{name}' not in chain"}
    import time
    t0 = time.monotonic()
    try:
        ok = await target.health()
        return {
            "ok": True, "healthy": ok, "name": name,
            "latency_ms": int((time.monotonic() - t0) * 1000),
        }
    except Exception as e:
        return {"ok": False, "name": name, "error": str(e)[:200]}


# ── Router config edit (write back to vault/config/router.md) ────────────
class RouterConfigPatch(BaseModel):
    direct_rules: list[dict[str, str]] | None = None  # [{match, reply}]
    tier_hints: dict[str, list[str]] | None = None    # {tier: [pattern]}


@app.put("/router/config")
async def router_config_put(req: RouterConfigPatch) -> dict[str, Any]:
    """Rewrite the YAML blocks inside router.md with the user's edits.
    The router re-reads its config on every classify() so changes take
    effect on the very next user turn."""
    from pathlib import Path as _P
    p: _P = settings.vault_dir / "config" / "router.md"
    text = p.read_text(encoding="utf-8")
    out = text
    if req.direct_rules is not None:
        block_lines = []
        for rule in req.direct_rules:
            m = (rule.get("match") or "").replace("'", "\\'")
            r = (rule.get("reply") or "").replace("'", "\\'")
            if not m or not r:
                continue
            block_lines.append(f"- match: '{m}'")
            block_lines.append(f"  reply: '{r}'")
            block_lines.append("")
        new_block = "```yaml\n" + "\n".join(block_lines).rstrip() + "\n```"
        out = _replace_yaml_block(out, "## DIRECT_ANSWERS", new_block)
    if req.tier_hints is not None:
        block_lines = []
        for tier in ("simple", "medium", "heavy"):
            pats = req.tier_hints.get(tier) or []
            block_lines.append(f"{tier}:")
            for pat in pats:
                pat_clean = pat.replace("'", "\\'")
                block_lines.append(f"  - '{pat_clean}'")
            block_lines.append("")
        new_block = "```yaml\n" + "\n".join(block_lines).rstrip() + "\n```"
        out = _replace_yaml_block(out, "## TIER_HINTS", new_block)
    p.write_text(out, encoding="utf-8")
    return {"ok": True, "bytes": len(out)}


def _replace_yaml_block(text: str, marker: str, new_block: str) -> str:
    """Replace the first ```yaml ...``` block after `marker` with new_block."""
    import re
    idx = text.find(marker)
    if idx < 0:
        # marker not found — append at end
        return text.rstrip() + f"\n\n{marker}\n\n{new_block}\n"
    rest_start = idx + len(marker)
    m = re.search(r"```ya?ml\s*\n.*?```", text[rest_start:], re.DOTALL)
    if not m:
        return text[:rest_start] + "\n\n" + new_block + "\n" + text[rest_start:]
    abs_start = rest_start + m.start()
    abs_end = rest_start + m.end()
    return text[:abs_start] + new_block + text[abs_end:]


# ── Notifications ────────────────────────────────────────────────────────
class InboundNotification(BaseModel):
    source: str = Field(..., description="whatsapp | slack | system | webhook | etc.")
    body: str
    title: str | None = None
    urgency: int = 1   # 0 low, 1 normal, 2 critical (also accepts string aliases)
    metadata: dict[str, Any] | None = None

    @_field_validator('urgency', mode='before')
    @classmethod
    def _coerce_urgency(cls, v):
        # Accept "low"/"normal"/"critical" strings so external clients
        # don't need to memorize the integer codes.
        if isinstance(v, str):
            return {"low": 0, "normal": 1, "critical": 2}.get(v.lower(), 1)
        return v


@app.post("/notifications/inbound")
async def notifications_inbound(req: InboundNotification) -> dict[str, Any]:
    """Open endpoint for ANY source (webhook, browser extension, OS bridge)
    to push an incoming notification at Kee. We persist + WS-broadcast +
    audit. The agent doesn't act on it automatically — the dashboard's
    inbox is the surface."""
    from kee.perception.notifications import record_notification
    rid = record_notification(
        direction="inbound", source=req.source, body=req.body,
        title=req.title, urgency=req.urgency, metadata=req.metadata,
    )
    _broadcast({
        "type": "notification_inbound",
        "source": req.source, "title": req.title, "body": req.body[:200],
        "urgency": req.urgency, "id": rid,
        "ts": datetime.utcnow().isoformat(),
    })
    return {"ok": True, "id": rid}


@app.get("/notifications")
async def notifications_list(
    direction: str | None = None,
    source: str | None = None,
    handled: bool | None = None,
    limit: int = 50,
) -> dict[str, Any]:
    where = []
    args: list[Any] = []
    if direction:
        where.append("direction = ?"); args.append(direction)
    if source:
        where.append("source = ?"); args.append(source)
    if handled is not None:
        where.append("handled = ?"); args.append(1 if handled else 0)
    sql = "SELECT id, timestamp, direction, source, title, body, urgency, handled, metadata FROM notifications"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(max(1, min(limit, 200)))
    con = db.get_connection()
    rows = con.execute(sql, args).fetchall()
    return {"rows": [
        {"id": r[0], "timestamp": (r[1].isoformat() if hasattr(r[1], "isoformat") else r[1]),
         "direction": r[2], "source": r[3], "title": r[4], "body": r[5],
         "urgency": r[6], "handled": bool(r[7]), "metadata": r[8]}
        for r in rows
    ]}


@app.get("/notifications/unread_count")
async def notifications_unread_count() -> dict[str, Any]:
    con = db.get_connection()
    row = con.execute(
        "SELECT COUNT(*) FROM notifications WHERE direction='inbound' AND handled=0"
    ).fetchone()
    return {"count": int(row[0] or 0) if row else 0}


@app.post("/notifications/{nid}/handled")
async def notifications_mark_handled(nid: int) -> dict[str, Any]:
    with db.cursor() as cur:
        cur.execute("UPDATE notifications SET handled = 1 WHERE id = ?", (nid,))
    return {"ok": True, "id": nid}


@app.post("/notifications/handle_all")
async def notifications_handle_all() -> dict[str, Any]:
    with db.cursor() as cur:
        cur.execute("UPDATE notifications SET handled = 1 WHERE direction='inbound' AND handled = 0")
    return {"ok": True}


# ── Force summarize endpoint ──────────────────────────────────────────────
@app.post("/memory/summarize/{conversation_id}")
async def memory_summarize_one(conversation_id: str) -> dict[str, Any]:
    """Force-summarize a specific conversation now (uses local Ollama)."""
    a = _agent()
    s = await a.memory.summarize_conversation(conversation_id, llm=a.llm, force=True)
    return {"ok": s is not None, "summary": s, "conversation_id": conversation_id}


# ── WebSocket /stream — live tail ────────────────────────────────────────
@app.websocket("/stream")
async def stream(ws: WebSocket) -> None:
    """Bidirectional event stream.

    Outbound: every `_broadcast()` event lands here.
    Inbound:  clients can send `{"type":"client_register", ...}` to declare
              their capabilities (e.g. `wants_audio:true` for the voice HUD,
              `device_type:"browser"` for the dashboard). Audio-only events
              skip clients that didn't opt in — keeps voice chunks off the
              dashboard tab and animation events off the audio HUD.
    """
    await ws.accept()
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    entry: dict[str, Any] = {
        "queue": q,
        "wants_audio": False,
        "device_type": "browser",
        "ts": datetime.utcnow().isoformat(),
    }
    _STREAM_QUEUES.append(entry)
    logger.info("WebSocket connected (%d listeners)", len(_STREAM_QUEUES))

    async def _recv_loop() -> None:
        """Consume client_register messages without blocking the send side."""
        try:
            while True:
                msg = await ws.receive_json()
                if not isinstance(msg, dict):
                    continue
                if msg.get("type") == "client_register":
                    if "wants_audio" in msg:
                        entry["wants_audio"] = bool(msg["wants_audio"])
                    if "device_type" in msg:
                        entry["device_type"] = str(msg["device_type"])[:32]
                    logger.debug(
                        "ws client registered: device=%s wants_audio=%s",
                        entry["device_type"], entry["wants_audio"],
                    )
                # Other inbound message types (ping, etc.) silently ignored.
        except WebSocketDisconnect:
            pass
        except Exception:
            # receive_json raises if the socket dies — that's normal.
            pass

    recv_task = asyncio.create_task(_recv_loop())
    try:
        await ws.send_json({
            "type": "hello",
            "ts": datetime.utcnow().isoformat(),
            "capabilities_hint": {
                "register": "send {type:'client_register', wants_audio:bool, "
                            "device_type:'browser'|'hud'|'mobile'}",
            },
        })
        while True:
            event = await q.get()
            await ws.send_json(event)
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.exception("websocket loop error")
    finally:
        recv_task.cancel()
        try:
            await recv_task
        except Exception:
            pass
        if entry in _STREAM_QUEUES:
            _STREAM_QUEUES.remove(entry)
        logger.info("WebSocket disconnected (%d remaining)", len(_STREAM_QUEUES))


# ── SPA fallback for the built dashboard ─────────────────────────────────
# Must come AFTER every other route registration so REST endpoints win the
# match. Any /app or /app/<route> request that doesn't hit a static file
# (the _app/* assets are mounted above) returns the SvelteKit shell so
# client-side routing handles `/app/hud`, `/app/cycle`, etc.
if _DASHBOARD_AVAILABLE:
    from fastapi.responses import FileResponse, RedirectResponse

    @app.get("/", include_in_schema=False)
    async def _root_redirect():
        # Convenience: bare http://127.0.0.1:7330 lands on the dashboard.
        return RedirectResponse(url="/app/")

    @app.get("/app", include_in_schema=False)
    async def _app_root():
        return FileResponse(str(_DASHBOARD_BUILD / "index.html"))

    @app.get("/app/{spa_path:path}", include_in_schema=False)
    async def _spa_fallback(spa_path: str):
        # Try a real file under build/ first (favicon.png, robots.txt, …);
        # otherwise hand back the SPA shell.
        candidate = _DASHBOARD_BUILD / spa_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(_DASHBOARD_BUILD / "index.html"))
