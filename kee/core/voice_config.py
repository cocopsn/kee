"""Persistent voice configuration.

A small JSON file at ``data/voice_config.json`` is the single source of
truth for which Piper voice Kee speaks with, and how fast. The voice
pipeline reads it on every TTS call so the setting can be changed live
from the dashboard without restarting the daemon.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import asdict, dataclass
from pathlib import Path

from kee.config import settings

logger = logging.getLogger(__name__)

CONFIG_FILENAME = "voice_config.json"
DEFAULT_VOICE = "es_MX-claude-high"


@dataclass
class VoicePreferences:
    voice: str = DEFAULT_VOICE          # filename stem (no .onnx) — DEFAULT
    length_scale: float = 1.0           # >1 = slower, <1 = faster
    noise_scale: float = 0.667          # variability
    noise_w: float = 0.8                # phoneme-length variability
    speak_responses: bool = True
    sentence_silence_s: float = 0.18    # pause between sentences
    # Phase 8: per-language voice. Empty dict = always use `voice` regardless
    # of detected language. With entries, the voice pipeline auto-detects
    # the language of each utterance (Whisper) and picks the matching voice;
    # falls back to `voice` if the detected language has no mapping.
    voice_per_lang: dict = None         # type: ignore[assignment]
    # If True, Whisper auto-detects language per utterance (slower by ~50ms
    # but enables code-mixed Spanish/English chats). If False, locks to
    # ``stt_language`` for max speed.
    auto_detect_language: bool = False
    stt_language: str = "es"            # default STT language when auto-detect off

    def __post_init__(self):
        if self.voice_per_lang is None:
            object.__setattr__(self, "voice_per_lang", {})

    def voice_for_lang(self, lang: str | None) -> str:
        """Pick the voice for a detected language code (e.g. 'es', 'en')."""
        if lang and self.voice_per_lang:
            mapped = self.voice_per_lang.get(lang)
            if mapped:
                return mapped
        return self.voice

    def to_piper_args(self, voice_path: Path) -> list[str]:
        """Translate to piper CLI flags. ``voice_path`` is the .onnx file."""
        return [
            "--model", str(voice_path),
            "--length_scale", f"{self.length_scale:.3f}",
            "--noise_scale", f"{self.noise_scale:.3f}",
            "--noise_w", f"{self.noise_w:.3f}",
            "--sentence_silence", f"{self.sentence_silence_s:.3f}",
        ]


_lock = threading.Lock()


def config_path() -> Path:
    return settings.data_dir / CONFIG_FILENAME


def load() -> VoicePreferences:
    p = config_path()
    if not p.exists():
        return VoicePreferences()
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        logger.warning("voice_config: corrupt file, falling back to defaults (%s)", e)
        return VoicePreferences()
    # Tolerate unknown keys / older versions
    fields = {f for f in VoicePreferences.__dataclass_fields__}
    cleaned = {k: v for k, v in raw.items() if k in fields}
    return VoicePreferences(**cleaned)


def save(prefs: VoicePreferences) -> None:
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    with _lock:
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(asdict(prefs), indent=2), encoding="utf-8")
        tmp.replace(p)
    logger.info("voice_config saved: voice=%s length=%.2f", prefs.voice, prefs.length_scale)


def voice_file_for(name: str) -> Path:
    """Return the ``.onnx`` path for a given voice stem (e.g. ``es_MX-claude-high``)."""
    if name.endswith(".onnx"):
        name = name[:-5]
    return settings.models_dir / "piper" / f"{name}.onnx"


def installed_voices() -> list[dict]:
    """Enumerate every Piper voice present in ``models/piper/``."""
    base = settings.models_dir / "piper"
    if not base.exists():
        return []
    out: list[dict] = []
    for onnx in sorted(base.glob("*.onnx")):
        meta_path = onnx.with_suffix(".onnx.json")
        meta: dict = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except Exception:
                pass
        sample_rate = meta.get("audio", {}).get("sample_rate")
        language = meta.get("language", {}).get("code") or onnx.stem.split("-")[0]
        out.append({
            "name": onnx.stem,
            "path": str(onnx),
            "size_mb": round(onnx.stat().st_size / (1024 * 1024), 2),
            "language": language,
            "sample_rate": sample_rate,
            "has_metadata": meta_path.exists(),
        })
    return out
