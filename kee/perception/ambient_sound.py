"""Ambient sound event detector.

Phase 7 §"Advanced sound detection". The roadmap specifies YAMNet, but
YAMNet is a TensorFlow model and we deliberately uninstalled TF during
the wake-word training patches. This module gives Kee the same
*behavior* (passive ambient awareness, time-stamped events for the
heartbeat / sleep-cycle to reason over) using two on-disk assets we
already have:

* **silero VAD** ONNX (bundled with openWakeWord) — gates "is this
  speech?" cheaply, no TF.
* **RMS energy + spectral centroid** — distinguishes loud non-speech
  events (door slam, dog bark, dropped plate) from background noise.

Each detected event lands in the SQLite ``ambient_events`` table:
    (id, timestamp, kind, rms, centroid_hz, duration_ms, source, note)

Where ``kind`` ∈
    {``speech_burst``, ``loud_event``, ``quiet_minute``, ``baseline_drift``}.

The voice surface can stream chunks into ``AmbientSoundDetector.feed()``
during its idle (between wake-words) state. A second consumer is the
heartbeat: it can ask "what happened in the last 15 min?" and weave
that into context ("kitchen activity at 14:32 — coffee maker?").

Drop-in: zero new pip deps. ONNX runtime + numpy are already installed.
"""

from __future__ import annotations

import json
import logging
import math
import time
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from kee.config import settings
from kee.core import db

logger = logging.getLogger(__name__)


SR = 16000
CHUNK = 1280              # 80 ms @ 16 kHz — same as wake-word
SILERO_INPUT = 512        # silero VAD wants 32 ms @ 16 kHz frames

# Thresholds (heuristic, room for tuning per-environment via env)
RMS_QUIET = 80.0          # below = silence
RMS_LOUD = 1500.0         # above = loud event candidate
SPEECH_PROB = 0.5         # silero threshold
QUIET_WINDOW_S = 60       # log a quiet_minute every N seconds of pure silence


# ── DB schema ────────────────────────────────────────────────────────────
def ensure_schema() -> None:
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ambient_events (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp   TEXT NOT NULL,
            kind        TEXT NOT NULL,
            rms         REAL,
            centroid_hz REAL,
            duration_ms INTEGER,
            source      TEXT,
            note        TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ambient_ts ON ambient_events(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_ambient_kind ON ambient_events(kind)")
    conn.commit()


def log_event(kind: str, rms: float, centroid: float | None = None,
              duration_ms: int = 0, source: str = "voice", note: str = "") -> int:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO ambient_events (timestamp, kind, rms, centroid_hz, duration_ms, source, note)
        VALUES (datetime('now'), ?, ?, ?, ?, ?, ?)
    """, (kind, float(rms), centroid, int(duration_ms), source, note))
    conn.commit()
    return cur.lastrowid


def recent_events(limit: int = 50, since_minutes: int | None = None) -> list[dict]:
    ensure_schema()
    conn = db.get_connection()
    cur = conn.cursor()
    if since_minutes is not None:
        cur.execute(
            "SELECT id, timestamp, kind, rms, centroid_hz, duration_ms, source, note "
            "FROM ambient_events "
            "WHERE timestamp >= datetime('now', ? || ' minutes') "
            "ORDER BY id DESC LIMIT ?",
            (f"-{since_minutes}", limit),
        )
    else:
        cur.execute(
            "SELECT id, timestamp, kind, rms, centroid_hz, duration_ms, source, note "
            "FROM ambient_events ORDER BY id DESC LIMIT ?",
            (limit,),
        )
    cols = [c[0] for c in cur.description]
    return [dict(zip(cols, row)) for row in cur.fetchall()]


# ── Spectral centroid (numpy) ────────────────────────────────────────────
def _spectral_centroid(x: np.ndarray, sr: int = SR) -> float:
    n = max(512, int(2 ** math.ceil(math.log2(len(x)))))
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    spec = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    energy = spec.sum()
    if energy < 1e-9:
        return 0.0
    return float(np.sum(freqs * spec) / energy)


# ── Silero VAD wrapper ───────────────────────────────────────────────────
class _SileroVAD:
    """Thin wrapper over silero_vad.onnx (bundled with openwakeword)."""

    def __init__(self):
        self._sess = None
        # silero needs h + c LSTM states as separate inputs (not a single
        # 'state' — that input doesn't exist in the model schema).
        self._h = np.zeros((2, 1, 64), dtype=np.float32)
        self._c = np.zeros((2, 1, 64), dtype=np.float32)

    def _ensure(self):
        if self._sess is not None:
            return
        try:
            import onnxruntime as ort
            import openwakeword as _oww
            base = Path(_oww.__file__).parent
            # openwakeword 0.6+ bundles models under resources/models/
            candidates = list(base.glob("**/silero_vad*.onnx"))
            if not candidates:
                logger.warning("silero VAD model not found in openwakeword bundle (%s)", base)
                return
            silero_path = candidates[0]
            self._sess = ort.InferenceSession(
                str(silero_path), providers=["CPUExecutionProvider"],
            )
            logger.info("silero VAD loaded from %s", silero_path)
        except Exception as e:
            logger.warning("silero VAD load failed: %s", e)

    def speech_prob(self, frame: np.ndarray) -> float:
        """Frame is 32 ms (512 samples) @ 16 kHz, int16 or float32."""
        self._ensure()
        if self._sess is None:
            return 0.0
        if frame.dtype == np.int16:
            f = frame.astype(np.float32) / 32768.0
        else:
            f = frame.astype(np.float32)
        if len(f) < SILERO_INPUT:
            f = np.pad(f, (0, SILERO_INPUT - len(f)))
        f = f[:SILERO_INPUT].reshape(1, -1)
        try:
            out, self._h, self._c = self._sess.run(
                None,
                {
                    "input": f,
                    "sr": np.array(SR, dtype=np.int64),
                    "h": self._h,
                    "c": self._c,
                },
            )
            return float(out[0][0])
        except Exception:
            return 0.0


# ── Detector core ────────────────────────────────────────────────────────
@dataclass
class _SpeechBurst:
    started_at: float
    samples: int = 0
    peak_rms: float = 0.0


class AmbientSoundDetector:
    """Stream-feed audio chunks; emits events asynchronously to the DB.

    Usage from voice.py (in idle loop, between wake words):
        detector.feed(chunk)
    """

    def __init__(self, source: str = "voice"):
        self.source = source
        self._vad = _SileroVAD()
        self._rms_history: deque[float] = deque(maxlen=20)
        self._burst: Optional[_SpeechBurst] = None
        self._last_quiet_log = time.time()
        self._loud_count = 0
        ensure_schema()

    def feed(self, chunk: np.ndarray) -> None:
        """Process one ~80 ms chunk of int16 audio."""
        if chunk.size == 0:
            return
        rms = float(np.abs(chunk).mean())
        self._rms_history.append(rms)
        now = time.time()

        # Loud non-speech event
        if rms >= RMS_LOUD:
            self._loud_count += 1
            if self._loud_count >= 2:
                centroid = _spectral_centroid(chunk.astype(np.float32))
                # Crude speech-vs-event heuristic: speech has centroid 500-3000Hz,
                # bangs / claps / glass have either very low or very high centroid.
                kind = "speech_burst" if 500 <= centroid <= 3500 else "loud_event"
                log_event(kind, rms=rms, centroid=centroid,
                          duration_ms=int(self._loud_count * 80),
                          source=self.source,
                          note=f"sustained ≥{RMS_LOUD:.0f} RMS")
                self._loud_count = 0
            return
        self._loud_count = 0

        # Speech burst tracking via silero
        prob = self._vad.speech_prob(chunk[:SILERO_INPUT])
        if prob >= SPEECH_PROB:
            if self._burst is None:
                self._burst = _SpeechBurst(started_at=now, peak_rms=rms)
            self._burst.samples += len(chunk)
            self._burst.peak_rms = max(self._burst.peak_rms, rms)
        elif self._burst is not None:
            duration_ms = int((self._burst.samples / SR) * 1000)
            if duration_ms >= 600:
                # ≥ 0.6s of speech detected — log it
                log_event("speech_burst",
                          rms=self._burst.peak_rms,
                          centroid=None,
                          duration_ms=duration_ms,
                          source=self.source,
                          note="silero_vad")
            self._burst = None

        # Quiet minute log (avoid silence-as-data-loss in the timeline)
        if (now - self._last_quiet_log) >= QUIET_WINDOW_S:
            avg_rms = float(np.mean(list(self._rms_history))) if self._rms_history else 0.0
            if avg_rms <= RMS_QUIET:
                log_event("quiet_minute", rms=avg_rms, centroid=None,
                          duration_ms=int(QUIET_WINDOW_S * 1000),
                          source=self.source,
                          note=f"avg_rms={avg_rms:.0f}")
                self._last_quiet_log = now
            else:
                # Reset clock so we only log truly-quiet windows
                self._last_quiet_log = now
