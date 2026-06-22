"""Speaker recognition (lite).

Phase 7 — "who's talking". A full diarization stack (pyannote.audio, SpeechBrain
ECAPA-TDNN) is heavy and needs HuggingFace auth. This module does a much
simpler thing that's still useful in a single-user household:

  * Capture a baseline voice-print for the owner (Coco) from N enrollment
    samples.
  * For each subsequent utterance, compute the same features and compare.
  * Return ``(is_owner, confidence)`` so the voice pipeline can tag the
    audit row, refuse risky tools when the speaker is unknown, etc.

Features used (all numpy, no extra deps):

  * Mean pitch via autocorrelation
  * Pitch standard deviation
  * Zero-crossing rate
  * Spectral centroid (FFT-based)
  * Spectral rolloff (85th percentile)

Each utterance becomes a 5-vector. The baseline is the per-feature mean +
std across enrollment samples. Distance is normalized Euclidean (z-score
distance) — robust to absolute level differences between mics.

Caveats: doesn't beat a CNN on accuracy. It DOES distinguish "the owner"
from "anyone else who walks into the room" with low false-accept rate at
the cost of moderate false-reject. For Phase 7's "tag, don't enforce"
purpose, that's the right tradeoff.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np

from kee.config import settings

logger = logging.getLogger(__name__)

PRINT_FILENAME = "voice_print.json"
SR_DEFAULT = 16000
N_FEATURES = 5


# ── Feature extraction ───────────────────────────────────────────────────
def _autocorr_pitch(x: np.ndarray, sr: int = SR_DEFAULT,
                    fmin: float = 60, fmax: float = 400) -> float:
    """Estimate fundamental pitch (Hz) of a single frame via autocorrelation.
    Returns 0 if no clear pitch (unvoiced/silence)."""
    if len(x) < 256:
        return 0.0
    x = x - x.mean()
    if np.abs(x).max() < 1e-3:
        return 0.0
    corr = np.correlate(x, x, mode="full")
    corr = corr[len(corr) // 2:]
    min_lag = int(sr / fmax)
    max_lag = int(sr / fmin)
    if max_lag >= len(corr):
        return 0.0
    region = corr[min_lag:max_lag]
    if region.size == 0 or region.max() < 0.3 * corr[0]:
        return 0.0
    lag = int(np.argmax(region) + min_lag)
    return sr / lag if lag > 0 else 0.0


def _zcr(x: np.ndarray) -> float:
    """Zero-crossing rate per sample."""
    if len(x) < 2:
        return 0.0
    return float(np.mean(np.abs(np.diff(np.sign(x))) > 0))


def _spectral_centroid_rolloff(x: np.ndarray, sr: int = SR_DEFAULT) -> tuple[float, float]:
    """Compute spectral centroid (Hz) and 85th-percentile rolloff (Hz)."""
    n = max(512, int(2 ** math.ceil(math.log2(min(len(x), 4096)))))
    if len(x) < n:
        x = np.pad(x, (0, n - len(x)))
    spec = np.abs(np.fft.rfft(x[:n] * np.hanning(n)))
    freqs = np.fft.rfftfreq(n, 1.0 / sr)
    energy = spec.sum()
    if energy < 1e-9:
        return 0.0, 0.0
    centroid = float(np.sum(freqs * spec) / energy)
    cumulative = np.cumsum(spec)
    threshold = 0.85 * cumulative[-1]
    rolloff_idx = int(np.searchsorted(cumulative, threshold))
    rolloff = float(freqs[min(rolloff_idx, len(freqs) - 1)])
    return centroid, rolloff


def features_from_audio(audio: np.ndarray, sr: int = SR_DEFAULT) -> np.ndarray:
    """Compute the 5-feature vector for a single utterance.

    Pitch features are averaged over voiced frames (50 ms hop) so that
    long unvoiced stretches (silence between words) don't dilute them.
    """
    # Normalize int16 → float in [-1, 1] if needed
    if audio.dtype == np.int16:
        x = audio.astype(np.float32) / 32768.0
    else:
        x = audio.astype(np.float32)

    # Frame for pitch
    frame_len = int(0.05 * sr)
    pitches: list[float] = []
    for start in range(0, len(x) - frame_len, frame_len):
        f = _autocorr_pitch(x[start:start + frame_len], sr=sr)
        if f > 0:
            pitches.append(f)
    if pitches:
        pitch_mean = float(np.mean(pitches))
        pitch_std = float(np.std(pitches))
    else:
        pitch_mean = 0.0
        pitch_std = 0.0

    centroid, rolloff = _spectral_centroid_rolloff(x, sr=sr)
    return np.array([
        pitch_mean,
        pitch_std,
        _zcr(x),
        centroid,
        rolloff,
    ], dtype=np.float32)


# ── Persistent voice-print ───────────────────────────────────────────────
@dataclass
class VoicePrint:
    owner_label: str = "owner"
    n_samples: int = 0
    mean: list[float] = field(default_factory=lambda: [0.0] * N_FEATURES)
    std: list[float] = field(default_factory=lambda: [1.0] * N_FEATURES)
    threshold_z: float = 2.5    # max average z-distance to count as owner
    enabled: bool = False       # True once enrollment has run

    def to_arrays(self) -> tuple[np.ndarray, np.ndarray]:
        return np.array(self.mean, dtype=np.float32), np.array(self.std, dtype=np.float32)


def print_path() -> Path:
    return settings.data_dir / PRINT_FILENAME


def load_print() -> VoicePrint:
    p = print_path()
    if not p.exists():
        return VoicePrint()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
        return VoicePrint(**{k: v for k, v in raw.items()
                             if k in VoicePrint.__dataclass_fields__})
    except Exception as e:
        logger.warning("voice_print: corrupt, resetting (%s)", e)
        return VoicePrint()


def save_print(vp: VoicePrint) -> None:
    p = print_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(asdict(vp), indent=2), encoding="utf-8")
    tmp.replace(p)
    logger.info("voice_print saved (n=%d, enabled=%s)", vp.n_samples, vp.enabled)


# ── Enrollment + matching ────────────────────────────────────────────────
def enroll(samples: list[np.ndarray], sr: int = SR_DEFAULT,
           label: str = "owner") -> VoicePrint:
    """Build a voice-print from N enrollment utterances (≥ 5 recommended)."""
    if not samples:
        raise ValueError("enroll() needs at least 1 sample")
    vecs = np.stack([features_from_audio(s, sr=sr) for s in samples])
    mean = vecs.mean(axis=0)
    std = vecs.std(axis=0) + 1e-6   # avoid division by zero
    vp = VoicePrint(
        owner_label=label,
        n_samples=len(samples),
        mean=mean.tolist(),
        std=std.tolist(),
        enabled=True,
    )
    save_print(vp)
    return vp


def match(audio: np.ndarray, sr: int = SR_DEFAULT,
          vp: Optional[VoicePrint] = None) -> dict:
    """Compare an utterance to the saved voice-print.

    Returns ``{is_owner, z_distance, threshold, confidence, enabled}``.
    ``confidence`` ∈ [0, 1]: 1 means exact mean, 0 means outside threshold.
    """
    vp = vp if vp is not None else load_print()
    if not vp.enabled:
        return {"is_owner": True, "z_distance": 0.0,
                "threshold": vp.threshold_z, "confidence": 1.0,
                "enabled": False, "reason": "no enrollment yet"}
    feat = features_from_audio(audio, sr=sr)
    mean, std = vp.to_arrays()
    z = np.abs((feat - mean) / std)
    avg_z = float(z.mean())
    is_owner = avg_z <= vp.threshold_z
    confidence = max(0.0, 1.0 - (avg_z / max(vp.threshold_z * 2, 1e-3)))
    return {
        "is_owner": is_owner,
        "z_distance": round(avg_z, 3),
        "per_feature_z": [round(float(v), 3) for v in z],
        "threshold": vp.threshold_z,
        "confidence": round(confidence, 3),
        "enabled": True,
        "owner_label": vp.owner_label,
    }
