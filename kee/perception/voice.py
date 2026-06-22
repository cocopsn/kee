"""Voice pipeline — Phase 2.

Wake word → VAD-gated recording → STT → agent → TTS → speakers.

All audio runs on CPU per VRAM Arbiter rules: the LLM is the sole VRAM
tenant on the primary node. Whisper "small" on a modern CPU is ~3-5×
real-time on Spanish (1.5 s of speech → ~0.3-0.5 s transcription).

Defaults assume the openwakeword `hey_jarvis_v0.1` placeholder model
(bundled with the package, ~1.3 MB). When you've trained your own
`kee.onnx` from `D:\\Kee\\models\\wakeword\\samples\\positive\\*.wav`,
drop it at `D:\\Kee\\models\\wakeword\\kee.onnx` and set
`KEE_WAKE_WORD=D:\\Kee\\models\\wakeword\\kee.onnx`.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from kee.config import settings


# Replies that are pure acknowledgments — Kee just acted, no need to
# narrate the success out loud. Saves ~3-6s of TTS playback on imperatives
# like "abre vscode" / "borra ese archivo".
#
# Two patterns:
#   1. Bare interjection — "Listo", "Hecho", "Ok", "Done"
#   2. <gerund> [<object>] — "Abriendo VS Code", "Borrando ese archivo"
_ACK_BARE = re.compile(
    r"^(?:listo|hecho|ok|okay|claro|de acuerdo|enseguida|done|ack|✓)\s*[.!]?\s*$",
    re.IGNORECASE,
)
_ACK_GERUND = re.compile(
    r"^(?:abriendo|cerrando|borrando|guardando|enviando|ejecutando|copiando|"
    r"moviendo|creando|"
    r"opening|closing|deleting|saving|sending|running|copying|moving|creating)"
    r"(?:\s+\S+){0,4}\s*[.!]?\s*$",
    re.IGNORECASE,
)


def _is_silent_ack(text: str) -> bool:
    """Detect short acknowledgments not worth speaking out loud."""
    t = text.strip()
    if len(t) > 60:
        return False
    return bool(_ACK_BARE.match(t) or _ACK_GERUND.match(t))


def _find_piper() -> str | None:
    """Locate the piper executable. Falls back to the venv Scripts/bin
    directory because pip-installed scripts aren't always on PATH (and
    the WindowsApps Python sandbox makes that worse)."""
    found = shutil.which("piper")
    if found:
        return found
    venv_bin = Path(sys.executable).parent
    for name in ("piper.exe", "piper"):
        candidate = venv_bin / name
        if candidate.exists():
            return str(candidate)
    return None

logger = logging.getLogger(__name__)


# ── Audio constants (must match openwakeword + faster-whisper) ───────────
SR = 16000              # sample rate
WAKE_CHUNK = 1280       # 80 ms — what openWakeWord expects per .predict()
SILENCE_HANG_S = 1.5    # how long of silence ends the recording
MAX_UTTERANCE_S = 12    # hard cap so a stuck VAD doesn't record forever
WAKE_THRESHOLD = float(os.environ.get("KEE_WAKE_THRESHOLD", "0.5"))
# 0.20 is permissive — easier to false-trigger but the user reports
# saying "Kee" doesn't fire. If you get false positives, raise via
# KEE_WAKE_THRESHOLD env. The custom kee.onnx is small (13 KB) and was
# trained on synthetic samples, so its scores tend to cluster low.
WAKE_DEBUG = os.environ.get("KEE_WAKE_DEBUG", "0") == "1"
# When KEE_WAKE_DEBUG=1, every chunk where the wake score exceeds 0.05
# is logged so you can see what the model is hearing.


_HALLUCINATION_PATTERNS_EARLY = [
    r"^subt[íi]tulos?\b.*",                 # 'Subtítulos por Amara.org'
    r"^subtitled?\b.*by.*",                 # English variant
    r"^gracias por (ver|haber|estar|seguir)",
    r"^thanks? for (watching|listening)",
    r"^subscribe\b",
    r"^(amara\.org|community|youtube)",
    r"^y nos vemos\b.*",                    # 'y nos vemos en el próximo vídeo'
    r"^hasta (la próxima|el próximo|luego)",
    r"^\W*$",                               # only punctuation/whitespace
    r"^[,.!?¡¿\s]{3,}$",                    # ',,¿Qué? ,, ,, ,, ,, ,,'
    r"^.{1,2}$",                            # 1-2 char garbage (was 1-3, dropped 'kee')
]
_HALLUCINATION_RE_EARLY = [re.compile(p, re.I) for p in _HALLUCINATION_PATTERNS_EARLY]


def _clean_for_tts(text: str) -> str:
    """Strip markdown / shell syntax / URLs / weird punctuation that Piper
    mispronounces. Goal: spoken text reads like natural Spanish.

    Examples:
      "Ejecuta `python -m kee.main`"  →  "Ejecuta el comando"
      "**Listo**, mira [aquí](url)"   →  "Listo, mira aquí"
      "wa.me/+15551234567"             →  "el número de WhatsApp guardado"
      "myproject/.env.local"           →  "myproject env local"
    """
    if not text:
        return ""
    t = text
    # Markdown links [text](url) → just text
    t = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", t)
    # Code blocks → drop entirely
    t = re.sub(r"```[\s\S]*?```", " ", t)
    # Inline code → unwrap (keep the word, drop backticks)
    t = re.sub(r"`+([^`\n]+?)`+", r"\1", t)
    # Bold/italic markers
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)
    t = re.sub(r"\*([^*\n]+)\*", r"\1", t)
    t = re.sub(r"__([^_]+)__", r"\1", t)
    t = re.sub(r"_([^_\n]+)_", r"\1", t)
    # List markers
    t = re.sub(r"^[\s]*[-*•]\s+", "", t, flags=re.M)
    t = re.sub(r"^[\s]*\d+[.)]\s+", "", t, flags=re.M)
    # URLs / emails / phone numbers
    t = re.sub(r"https?://\S+", "el enlace", t)
    t = re.sub(r"\bwww\.\S+", "el sitio", t)
    t = re.sub(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b", "el correo", t)
    t = re.sub(r"\+?\d[\d\s\-]{8,}\d", "el número", t)
    # Shell module syntax
    t = re.sub(r"`?python\s+-m\s+\S+`?", "el comando", t)
    t = re.sub(r"`?\s*-{1,2}[\w-]+(?:=\S+)?`?", "", t)
    # File paths
    t = re.sub(r"[A-Z]:[\\/][\w.\\/-]+", "una ruta", t)
    t = re.sub(r"/[\w./-]{4,}", "una ruta", t)
    # Replace symbols Piper reads literally
    t = t.replace("→", " a ").replace("←", " desde ")
    t = t.replace("≈", " aproximadamente ").replace("≥", " mayor o igual a ")
    t = t.replace("≤", " menor o igual a ").replace("·", " ").replace("•", " ")
    t = t.replace("…", "...").replace("–", "-").replace("—", "-")
    # File extensions tend to read weirdly
    t = re.sub(r"\.(?:py|md|json|yaml|yml|txt|env|toml|js|ts|tsx|svelte|onnx|wav|mp3|csv)\b", "", t)
    # Drop emoji + other Unicode symbols
    t = re.sub(r"[\U0001F300-\U0001FAFF☀-➿]", "", t)
    # Collapse repeated punctuation
    t = re.sub(r"([.!?])\1{2,}", r"\1", t)
    t = re.sub(r"\.{4,}", "...", t)
    # Newlines → sentence pauses
    t = re.sub(r"\n{2,}", ". ", t)
    t = re.sub(r"\n", ". ", t)
    # Multiple spaces → single
    t = re.sub(r"\s+", " ", t).strip()
    # Cleanup: leading/trailing punctuation residue
    t = t.strip(" ,;:")
    return t


def _is_likely_hallucination(text: str) -> bool:
    """Detect Whisper hallucination patterns. Three-layer check:
      1. Regex bank for known templates ('Subtítulos por...', etc.)
      2. Punct-spam heuristic (>50% non-alnum + short = noise)
      3. Hard blocklist of phrases Whisper produces on silence/music
      4. N-gram repetition: any 1-3 word phrase repeated ≥3x covering
         ≥75% of tokens → drop. (Kokoro-style hallucination, common.)
    """
    t = (text or "").strip()
    if not t:
        return True
    if any(rx.match(t) for rx in _HALLUCINATION_RE_EARLY):
        return True
    # Punct-spam
    if len(t) < 40:
        non_alnum = sum(1 for c in t if not c.isalnum())
        if non_alnum / len(t) > 0.5:
            return True
    # Hard blocklist (Whisper's "silence-on-music/noise" outputs in EN+ES)
    tl = t.lower().strip(" ¡¿,.!?")
    if tl in _WHISPER_HALLUCINATION_EXACT:
        return True
    # N-gram repetition: any token bigram or trigram repeated ≥3x
    # AND those repetitions cover ≥75% of the total tokens → drop.
    tokens = re.findall(r"\w+", t.lower())
    n = len(tokens)
    if n >= 4:
        for size in (1, 2, 3):
            from collections import Counter
            grams = [" ".join(tokens[i:i+size]) for i in range(n - size + 1)]
            c = Counter(grams)
            for g, count in c.most_common(1):
                if count >= 3 and (count * size) / n >= 0.75:
                    return True
    return False


_WHISPER_HALLUCINATION_EXACT = {
    # ENGLISH (videos / streams)
    "thank you for watching", "thanks for watching",
    "subscribe", "like and subscribe",
    "please subscribe", "please like and subscribe",
    "see you next time", "see you in the next one",
    "bye", "bye bye", "goodbye",
    "amara.org community", "translation by amara.org",
    # SPANISH (videos / streams)
    "gracias por ver", "gracias por ver el video",
    "gracias por ver el vídeo", "gracias por ver este video",
    "suscríbete", "dale like", "no olvides suscribirte",
    "nos vemos en el próximo video", "nos vemos en el próximo vídeo",
    "y nos vemos en el próximo video",
    "hasta la próxima", "hasta luego", "adiós",
    "subtítulos por la comunidad de amara.org",
    "subtítulos por amara.org", "subtítulos realizados por la comunidad de amara.org",
    # MUSICAL / NOISE outputs
    "music", "música", "♪", "♫",
    "applause", "aplausos",
}


def _resolve_wake_word_model() -> tuple[str, str]:
    """Return (model_path_or_name, score_key_that_appears_in_predictions)."""
    explicit = os.environ.get("KEE_WAKE_WORD")
    if explicit:
        if Path(explicit).exists():
            stem = Path(explicit).stem
            return explicit, stem
        logger.warning("KEE_WAKE_WORD=%s not found; falling back to placeholder", explicit)
    # Look for a trained kee model in the canonical spot
    canonical = settings.models_dir / "wakeword" / "kee.onnx"
    if canonical.exists():
        return str(canonical), "kee"
    # Placeholder while training hasn't happened
    return "hey_jarvis_v0.1", "hey_jarvis_v0.1"


@dataclass
class VoiceConfig:
    wake_threshold: float = WAKE_THRESHOLD
    # `small` (~250MB, fast on CPU) is the default. Tested `medium` and
    # `large-v3` — they're 5-50x slower on this hardware (CPU-only per
    # VRAM Arbiter rules), making voice unusable. Stuck with small +
    # an aggressive regex-based trigger pattern that catches the
    # common Whisper mishearings of "Kee" (aquí, ki, key, qué, etc).
    whisper_model: str = os.environ.get("KEE_WHISPER_MODEL", "small")
    whisper_compute_type: str = "int8"
    whisper_language: str = "es"
    piper_voice: str = ""
    speak_responses: bool = True
    max_iterations: int = 5
    # Wake-word trigger pattern.
    # Matches EITHER:
    #  (a) PREFIX (hey/oye/hola/ok/ey/atención) + KEE-SOUND, OR
    #  (b) BARE KEE-SOUND alone in a SHORT utterance (≤25 chars total) —
    #      handles the case where user says only "Kee" and Whisper outputs
    #      just "¡Aquí!" / "aquí." without picking up the prefix.
    #
    # The bare-fallback is checked separately in code (see run loop) so we
    # can apply the length cap. This regex covers case (a).
    wake_triggers_pattern: str = (
        r"(?:^|\s|[¡¿,.!?])"
        r"(?:hey|ey|oye|hola|holi|holy|okey|okay|ok|atención|atencion)"
        r"[\s,]+"
        r"(?:kee+|key|kit|kid|ki|qui|qué|que|aquí|aqui|aki|jiki)"
        r"(?:\s|[,.!?¡¿]|$)"
    )
    # Bare wake (no prefix). Aggressive: ~100+ Whisper variants of "Kee".
    # False positives are recoverable; missed wakes feel broken.
    wake_bare_pattern: str = (
        r"^[¡¿\s,.!?]*"
        r"(?:"
        # Direct phonetic
        r"kee+|key|kii|qui|qu[ée]|que|"
        # "aquí" family (Spanish "here")
        r"aqu[ií]|aki|aqui+|"
        # K-prefixed short words
        r"ki|kid|kit|kin|kim|kis|kit+|kih|kik|kil|kir|kio|kiu|"
        # Whisper-typical short outputs
        r"laki|taki|jiki|heik|jeik|yiki|jik|nik|pik|tik|"
        r"qui+|quik|kios|cuiz|cuy|"
        # Greetings Whisper substitutes
        r"hai|hi+|ai|ay|ey|hey|jey|jay|guay|"
        # Fallback: any 2-4 char word starting with k/q
        r"[kq][a-z]{0,3}|"
        # 'ni'/'mi'/'pi'/'ti' family (1-2 syllable Whisper outputs)
        r"ni+|mi+|pi+|ti+|vi+|li+|si+"
        r")"
        r"[!?\s,.]*$"
    )
    wake_bare_max_len: int = 25
    # Active-listen window: after a successful command, the next
    # utterance(s) within this window are treated as commands without
    # requiring the wake word. Tightened to 8s based on production
    # Jarvis projects — 30s caused too many false-triggers in noisy rooms.
    active_listen_s: float = 8.0


class VoicePipeline:
    def __init__(self, agent, config: VoiceConfig | None = None) -> None:
        self.agent = agent
        self.cfg = config or VoiceConfig()
        self._wake = None       # lazy
        self._whisper = None    # lazy
        self._piper_voice = None
        self._stream = None
        self._stop = asyncio.Event()
        self._wake_path, self._wake_key = _resolve_wake_word_model()
        self._ambient = None
        self._ambient_enabled = os.environ.get("KEE_AMBIENT_LOG", "1") not in ("0", "false", "off")
        # Multi-turn voice conversation state — same ConversationState
        # instance is reused across utterances within a 5-min idle window
        # so "qué hora es" → "y mañana?" actually has context. Reset on
        # idle expiration or via explicit reset.
        from kee.core.memory import ConversationState
        self._conv_state: "ConversationState | None" = None
        self._conv_last_used: float = 0.0
        self._CONV_IDLE_S = 300.0     # 5 min

    # ── Wake detection: VAD + Whisper transcript matching ────────────────
    # We dropped openWakeWord entirely. Custom kee.onnx was undertrained
    # (max score 0.005) and hey_jarvis_v0.1 only fires on a 2-syllable
    # English phrase. The new approach:
    #
    #   1. silero VAD detects when the user starts speaking
    #   2. Record until VAD says silence resumed
    #   3. Whisper transcribes the recording
    #   4. Regex check for trigger phrases in the transcript
    #   5. If matched, treat the rest of the transcript as the command
    #
    # This is what Alexa / Siri / etc actually do under the hood when
    # you don't have a dedicated keyword-spotting model. ~200-500ms
    # extra latency vs a real KWS model, but it ACTUALLY WORKS.
    def _ensure_wake_model(self):
        """Load silero VAD (used for speech-segment detection)."""
        if self._wake is not None:
            return
        try:
            import onnxruntime as ort
            import openwakeword as _oww
            base = Path(_oww.__file__).parent
            silero = next(base.glob("**/silero_vad*.onnx"), None)
            if silero is None:
                raise RuntimeError("silero_vad.onnx not bundled with openwakeword")
            self._wake = ort.InferenceSession(
                str(silero), providers=["CPUExecutionProvider"],
            )
            # silero_vad expects h + c LSTM states as SEPARATE inputs.
            # We had been passing a single 'state' which silently no-op'd
            # → VAD always returned 0.0 → loop never fired. Verified
            # against silero ONNX schema: inputs = [input, sr, h, c].
            self._vad_h = np.zeros((2, 1, 64), dtype=np.float32)
            self._vad_c = np.zeros((2, 1, 64), dtype=np.float32)
            logger.info(
                "Wake detector: VAD+Whisper (triggers regex: %s)",
                self.cfg.wake_triggers_pattern,
            )
        except Exception as e:
            logger.exception("VAD load failed: %s", e)
            raise

    def _vad_speech_prob(self, frame: np.ndarray) -> float:
        """Run silero VAD on a 512-sample (32ms @ 16kHz) frame.
        Returns speech probability 0..1. Verified live: max prob ~0.99
        when speaking, ~0.0 in silence."""
        if frame.dtype == np.int16:
            f = frame.astype(np.float32) / 32768.0
        else:
            f = frame.astype(np.float32)
        if len(f) < 512:
            f = np.pad(f, (0, 512 - len(f)))
        f = f[:512].reshape(1, -1)
        try:
            out, self._vad_h, self._vad_c = self._wake.run(
                None, {
                    "input": f,
                    "sr": np.array(SR, dtype=np.int64),
                    "h": self._vad_h,
                    "c": self._vad_c,
                },
            )
            return float(out[0][0])
        except Exception as e:
            logger.warning("VAD inference failed: %s", e)
            return 0.0

    def _ensure_whisper(self):
        if self._whisper is not None:
            return
        from faster_whisper import WhisperModel
        # CPU only — VRAM is reserved for the LLM per the arbiter rules.
        self._whisper = WhisperModel(
            self.cfg.whisper_model,
            device="cpu",
            compute_type=self.cfg.whisper_compute_type,
        )
        logger.info(
            "Whisper loaded: %s on CPU (compute=%s, lang=%s)",
            self.cfg.whisper_model, self.cfg.whisper_compute_type, self.cfg.whisper_language,
        )

    def _ensure_piper(self):
        """Resolve piper bin + voice. Voice is re-checked on every call so
        live dashboard switches don't require a daemon restart."""
        from kee.core import voice_config as vcfg
        prefs = vcfg.load()
        # Prefer per-language voice based on the most recent STT language
        chosen_voice = prefs.voice_for_lang(getattr(self, "_last_lang", None))
        voice_path = vcfg.voice_file_for(chosen_voice)
        if not voice_path.exists():
            # Fall back to the bundled default if the user-selected voice
            # was uninstalled.
            voice_path = settings.models_dir / "piper" / "es_MX-claude-high.onnx"
        if not voice_path.exists():
            logger.warning("Piper voice not found at %s — TTS disabled", voice_path)
            self.cfg.speak_responses = False
            self._piper_voice = None
            return
        if not hasattr(self, "_piper_bin") or self._piper_bin is None:
            piper_bin = _find_piper()
            if piper_bin is None:
                logger.warning("piper executable not found — TTS disabled")
                self.cfg.speak_responses = False
                return
            self._piper_bin = piper_bin
        self._piper_voice = str(voice_path)
        # Cache prefs for the speak() call.
        self._piper_prefs = prefs
        # Honor live toggles
        self.cfg.speak_responses = prefs.speak_responses

    # ── Lifecycle ─────────────────────────────────────────────────────────
    async def run(self) -> None:
        """Main loop. Runs forever until `stop()` or KeyboardInterrupt."""
        self._ensure_wake_model()
        self._ensure_whisper()
        self._ensure_piper()

        import sounddevice as sd

        logger.info("Voice pipeline starting. Speak the wake word to trigger.")
        loop = asyncio.get_running_loop()

        # Open the mic. Robust against devices whose default samplerate
        # isn't 16 kHz (AirPods are 44.1 kHz by default — sounddevice
        # silently fails or distorts when forced to 16 kHz on those).
        # Strategy:
        #   1. Try the device the user pinned via KEE_MIC_DEVICE env
        #   2. Try opening at native 16 kHz; if it fails, open at the
        #      device's preferred samplerate and resample on-the-fly.
        device_idx = os.environ.get("KEE_MIC_DEVICE")
        if device_idx is not None:
            try: device_idx = int(device_idx)
            except ValueError: device_idx = None
        try:
            self._stream = sd.InputStream(
                device=device_idx,
                samplerate=SR, channels=1, dtype="int16", blocksize=WAKE_CHUNK,
            )
            self._stream.start()
            self._native_sr = SR
            self._resample_ratio = 1.0
            logger.info("Mic open at native 16 kHz (device=%s)", device_idx if device_idx is not None else 'default')
        except Exception as e:
            logger.warning("16 kHz mic open failed (%s) — falling back to device default + resample", e)
            try: dev_info = sd.query_devices(device_idx, kind='input')
            except Exception: dev_info = sd.query_devices(kind='input')
            native_sr = int(dev_info.get("default_samplerate", 48000) or 48000)
            ratio = native_sr / SR
            native_blocksize = int(WAKE_CHUNK * ratio)
            self._stream = sd.InputStream(
                device=device_idx,
                samplerate=native_sr, channels=1, dtype="int16", blocksize=native_blocksize,
            )
            self._stream.start()
            self._native_sr = native_sr
            self._resample_ratio = ratio
            logger.info("Mic open at %d Hz (device=%s) — resampling %.2fx to 16 kHz",
                        native_sr, device_idx if device_idx is not None else 'default', ratio)

        # ── VAD-driven segmentation loop ──────────────────────────────
        import re
        trigger_re = re.compile(self.cfg.wake_triggers_pattern, re.IGNORECASE)
        bare_re = re.compile(self.cfg.wake_bare_pattern, re.IGNORECASE)
        VAD_FRAME = 512                    # 32ms @ 16kHz
        SPEECH_PROB_ON = 0.55              # frame is speech if VAD > 0.55
        SPEECH_PROB_OFF = 0.35             # frame is silence if VAD < 0.35
        SILENCE_FRAMES_TO_CLOSE = 16       # ~500ms of silence ends a segment
        MIN_SEGMENT_FRAMES = 8             # ~250ms minimum (drops single beeps)
        MAX_SEGMENT_FRAMES = 600           # ~20s safety cap

        in_speech = False
        silence_run = 0
        segment_frames: list[np.ndarray] = []
        buffered_chunk = np.zeros(0, dtype=np.int16)

        # Resilience: catch transient errors per-iteration so a single bad
        # chunk / VAD hiccup / Whisper exception doesn't crash the surface
        # (which would force a supervisor respawn + multi-second cold start).
        consecutive_errors = 0
        try:
            while not self._stop.is_set():
                # Pull audio from the mic (handles native + resample).
                try:
                    chunk = await loop.run_in_executor(None, self._read_chunk)
                except Exception as e:
                    consecutive_errors += 1
                    logger.warning("mic read iteration error #%d: %s",
                                   consecutive_errors, e)
                    if consecutive_errors >= 10:
                        logger.error("too many consecutive mic errors, bailing out")
                        raise
                    await asyncio.sleep(0.1)
                    continue
                consecutive_errors = 0
                if chunk is None or len(chunk) == 0:
                    continue
                # Ambient sound detection (cheap, runs on every chunk).
                if self._ambient_enabled:
                    if self._ambient is None:
                        from kee.perception.ambient_sound import AmbientSoundDetector
                        self._ambient = AmbientSoundDetector(source="voice")
                    try: self._ambient.feed(chunk)
                    except Exception: pass

                # Append to buffer + slice into 512-sample VAD frames.
                buffered_chunk = np.concatenate([buffered_chunk, chunk])
                while len(buffered_chunk) >= VAD_FRAME:
                    frame = buffered_chunk[:VAD_FRAME]
                    buffered_chunk = buffered_chunk[VAD_FRAME:]
                    prob = self._vad_speech_prob(frame)

                    if not in_speech:
                        if prob >= SPEECH_PROB_ON:
                            in_speech = True
                            silence_run = 0
                            segment_frames = [frame]
                            logger.debug("VAD: speech start (p=%.2f)", prob)
                    else:
                        segment_frames.append(frame)
                        if prob < SPEECH_PROB_OFF:
                            silence_run += 1
                        else:
                            silence_run = 0
                        # End of segment: enough silence OR safety cap
                        if silence_run >= SILENCE_FRAMES_TO_CLOSE or \
                           len(segment_frames) >= MAX_SEGMENT_FRAMES:
                            in_speech = False
                            if len(segment_frames) >= MIN_SEGMENT_FRAMES:
                                audio = np.concatenate(segment_frames)
                                logger.info("VAD: segment closed (%d frames = %.1fs)",
                                            len(segment_frames), len(audio) / SR)
                                # Transcribe in executor (CPU-heavy). Catch
                                # errors so a single bad transcription doesn't
                                # crash the surface — just skip the segment.
                                try:
                                    text, lang = await loop.run_in_executor(
                                        None, self._transcribe, audio,
                                    )
                                except Exception as e:
                                    logger.warning("transcribe failed, skipping segment: %s", e)
                                    text, lang = "", "es"
                                text = (text or "").strip()
                                # ALWAYS log what Whisper heard so we can see why
                                # the trigger doesn't match.
                                logger.info("STT heard: %r [lang=%s]", text, lang)
                                if not text:
                                    pass
                                elif _is_likely_hallucination(text):
                                    logger.info("  → looked like hallucination, dropping")
                                else:
                                    # Active-listen mode: if we recently
                                    # responded, treat ANY transcript as a
                                    # command (no wake-word required).
                                    import time as _time
                                    now_t = _time.time()
                                    in_active = (now_t - getattr(self, "_active_until_t", 0)) < 0
                                    if in_active:
                                        logger.info("  → ACTIVE-MODE (no wake needed) → command=%r", text)
                                        try:
                                            self.agent.audit.log_event("wake_word", {
                                                "trigger": "active-mode", "transcript": text, "lang": lang,
                                            })
                                        except Exception: pass
                                        await self._dispatch_command(text, lang)
                                        # extend active window
                                        self._active_until_t = _time.time() + self.cfg.active_listen_s
                                        segment_frames = []; silence_run = 0
                                        continue

                                    # Try the prefix+kee-sound pattern first.
                                    m = trigger_re.search(text)
                                    # Fallback: bare wake-sound in a SHORT utterance
                                    if not m and len(text) <= self.cfg.wake_bare_max_len:
                                        if bare_re.match(text):
                                            class _M:
                                                def __init__(self, s): self.s = s
                                                def group(self, _=0): return text
                                                def end(self): return len(text)
                                            m = _M(text)
                                    if m:
                                        command = text[m.end():].strip(" ,.;!?¿¡")
                                        logger.info("  → WAKE TRIGGERED via %r → command=%r",
                                                    m.group(0), command or "<bare wake>")
                                        try:
                                            from kee.desktop.app import write_signal
                                            write_signal("show", mode="hud", reason="wake_word",
                                                         extra={"trigger": m.group(0).strip(),
                                                                "transcript": text})
                                        except Exception: pass
                                        # Audit row → cross-process WS broadcast
                                        # → dashboard/HUD pulses on this event.
                                        try:
                                            self.agent.audit.log_event("wake_word", {
                                                "trigger": m.group(0).strip(),
                                                "transcript": text,
                                                "lang": lang,
                                            })
                                        except Exception: pass
                                        if command:
                                            await self._dispatch_command(command, lang)
                                        else:
                                            self._next_no_trigger = True
                                    elif getattr(self, "_next_no_trigger", False):
                                        self._next_no_trigger = False
                                        logger.info("  → following bare wake, treating whole as command")
                                        await self._dispatch_command(text, lang)
                                    else:
                                        logger.info("  → no trigger phrase matched, ignored")
                            segment_frames = []
                            silence_run = 0
        except (KeyboardInterrupt, asyncio.CancelledError):
            logger.info("Voice pipeline interrupted.")
        finally:
            if self._stream is not None:
                self._stream.stop()
                self._stream.close()
                self._stream = None

    async def _dispatch_command(self, text: str, detected_lang: str) -> None:
        """Run a transcribed command through the agent + speak the reply.

        FORCED Spanish: Whisper sometimes mistakenly tags utterances as
        English when "Kee" comes through as "Ki" / "Hi" / "Hai".

        Multi-turn: keeps the same ConversationState across utterances
        within a 5-min idle window so "qué hora es" → "y mañana?" works.
        """
        import time as _time
        self._last_lang = "es"
        # Strip Whisper-jibberish that's just punctuation + 1 word (those
        # are rarely real commands; usually noise after the bare wake).
        clean = text.strip(" ¡¿,.!?")
        if len(clean) <= 6 and not any(c.isspace() for c in clean):
            text = f"(El usuario dijo solo «{text}» por voz — saluda en español brevemente.)"
        logger.info("VOICE → agent: %r", text)

        # Multi-turn: reuse state if last interaction was within idle window
        now = _time.time()
        if self._conv_state and (now - self._conv_last_used) > self._CONV_IDLE_S:
            logger.info("Voice conversation state expired (idle > %ds), resetting",
                        int(self._CONV_IDLE_S))
            self._conv_state = None

        from kee.config import settings as _s
        original_max = _s.max_iterations
        try:
            object.__setattr__(_s, "max_iterations", self.cfg.max_iterations)
            response, conv = await self.agent.process(
                text, source="voice", state=self._conv_state,
            )
            self._conv_state = conv
            self._conv_last_used = now
        except Exception:
            logger.exception("agent.process raised")
            return
        finally:
            object.__setattr__(_s, "max_iterations", original_max)
        response = (response or "").strip()
        if not response:
            return
        # Quality monitor — Jarvis-pattern. Runs heuristic QA + logs to a
        # rolling 20-sample window the dashboard renders as a sparkline.
        try:
            from kee.cognition.conversation_monitor import observe as qa_observe
            qv = qa_observe(response, source="voice", expected_lang="es")
            if qv["issues"]:
                logger.warning("response QA score=%.2f issues=%s",
                               qv["score"], qv["issues"])
            else:
                logger.info("response QA clean (score=%.2f)", qv["score"])
        except Exception:
            pass
        logger.info("Kee said: %s", response[:160])
        # Mark active-listen window: next utterance(s) within 8s don't
        # need the wake word — feels conversational.
        import time as _time
        self._active_until_t = _time.time() + self.cfg.active_listen_s
        if self.cfg.speak_responses and self._piper_voice and not _is_silent_ack(response):
            spoken = _clean_for_tts(response)
            # Streaming TTS opt-in: split into sentences, render+play each
            # so first audio arrives in ~1s instead of waiting for the
            # full Piper render (~3-5s for a paragraph).
            if os.environ.get("KEE_VOICE_STREAMING", "0") in ("1", "true", "on"):
                speak_fn = self._speak_streaming
            else:
                speak_fn = self._speak
            await asyncio.get_running_loop().run_in_executor(
                None, speak_fn, spoken,
            )

    def stop(self) -> None:
        self._stop.set()

    def _resample_to_16k(self, data: np.ndarray) -> np.ndarray:
        """Linearly resample int16 audio from native_sr to 16 kHz."""
        if self._resample_ratio == 1.0:
            return data
        n_in = len(data)
        n_out = int(n_in / self._resample_ratio)
        if n_out <= 0:
            return np.zeros(WAKE_CHUNK, dtype=np.int16)
        # Cheap nearest-neighbor resample — fast on every chunk, plenty
        # accurate for wake-word + speech-recognition usage.
        idx = np.linspace(0, n_in - 1, n_out).astype(np.int32)
        return data[idx].astype(np.int16)

    def _read_chunk(self) -> np.ndarray | None:
        try:
            data, _ = self._stream.read(self._stream.blocksize)
            data = data.flatten()
            return self._resample_to_16k(data)
        except Exception:
            logger.exception("mic read failed")
            return None

    # ── Recording (with simple energy-based VAD) ──────────────────────────
    def _record_until_silence(self) -> np.ndarray | None:
        """Record until the rolling-window RMS drops below a threshold for
        SILENCE_HANG_S, or MAX_UTTERANCE_S elapsed. Returns audio at 16 kHz
        (resampled from native if needed)."""
        frames: list[np.ndarray] = []
        silence_started: float | None = None
        start_t = time.time()
        # Calibrate noise floor on the first 200 ms.
        noise_samples: list[float] = []
        for _ in range(3):
            d, _ = self._stream.read(self._stream.blocksize)
            d = self._resample_to_16k(d.flatten())
            noise_samples.append(float(np.abs(d).mean()))
        noise_floor = max(80.0, np.mean(noise_samples) * 1.6)

        while True:
            d, _ = self._stream.read(self._stream.blocksize)
            d = self._resample_to_16k(d.flatten())
            frames.append(d)
            rms = float(np.abs(d).mean())
            now = time.time()
            if rms < noise_floor:
                if silence_started is None:
                    silence_started = now
                elif (now - silence_started) >= SILENCE_HANG_S:
                    break
            else:
                silence_started = None
            if (now - start_t) >= MAX_UTTERANCE_S:
                break
        if not frames:
            return None
        return np.concatenate(frames)

    # ── Per-utterance handling ────────────────────────────────────────────
    async def _handle_utterance(self, audio: np.ndarray) -> None:
        result = await asyncio.get_running_loop().run_in_executor(
            None, self._transcribe, audio,
        )
        text, detected_lang = result
        text = (text or "").strip()
        if not text:
            logger.info("STT returned empty.")
            return
        # Filter Whisper hallucinations (silence → "Subtítulos en español"
        # etc.). This is what made Kee speak from nowhere when wake-word
        # fires false-positively.
        if _is_likely_hallucination(text):
            logger.info("STT looked like hallucination, dropping: %r", text)
            return
        logger.info("STT: %r [lang=%s]", text, detected_lang)
        # Stash lang on self so _speak() picks the matching voice.
        self._last_lang = detected_lang

        # Speaker recognition (lite). Tag the source so audit can show
        # whether Kee was talking to Coco or somebody else.
        try:
            from kee.perception import speaker_id
            verdict = speaker_id.match(audio)
            if verdict.get("enabled") and not verdict.get("is_owner"):
                logger.warning("Speaker mismatch: z=%.2f conf=%.2f — tagging as unknown",
                               verdict.get("z_distance", 0), verdict.get("confidence", 0))
            self._last_speaker = verdict
        except Exception:
            self._last_speaker = None

        # Lower the agent's per-turn iteration budget so voice commands
        # never spend 15 LLM rounds on a tool-failure spiral. Restored in
        # `finally` so it doesn't leak to the terminal surface.
        from kee.config import settings as _s
        original_max = _s.max_iterations
        try:
            object.__setattr__(_s, "max_iterations", self.cfg.max_iterations)
            response, _ = await self.agent.process(text, source="voice")
        except Exception:
            logger.exception("agent.process raised on voice input")
            return
        finally:
            object.__setattr__(_s, "max_iterations", original_max)

        response = (response or "").strip()
        if not response:
            return
        logger.info("Kee said: %s", response[:160])

        if not (self.cfg.speak_responses and self._piper_voice):
            return
        if _is_silent_ack(response):
            logger.info("Suppressing TTS for ack-only reply: %r", response)
            return
        speak_fn = (
            self._speak_streaming
            if os.environ.get("KEE_VOICE_STREAMING", "0") in ("1", "true", "on")
            else self._speak
        )
        await asyncio.get_running_loop().run_in_executor(
            None, speak_fn, response,
        )

    def _transcribe(self, audio: np.ndarray) -> tuple[str, str]:
        """Transcribe → (text, detected_lang).

        Whisper's VAD filter is RE-ENABLED with looser params: without
        it, Whisper hallucinates text on silence/noise (classic
        "Subtítulos en español" / "Gracias por ver el video" output)
        which made Kee speak from nowhere when wake-word fires false-
        positively. With VAD, silence yields empty STT → we skip the
        agent call.

        Looser min_silence_duration_ms=1000 (default 2000) so brief
        pauses inside a sentence don't get split. Padding=400 keeps
        word boundaries clean.
        """
        from kee.core import voice_config as vcfg
        prefs = vcfg.load()
        f = audio.astype(np.float32) / 32768.0
        vad_params = {
            "min_silence_duration_ms": 1000,
            "speech_pad_ms": 400,
        }
        # condition_on_previous_text=False — Jarvis-pattern fix: when
        # previous-text conditioning is on, Whisper sometimes locks into
        # repetition loops on noise (saying "watching" 30 times). Off
        # by default in faster-whisper but worth being explicit.
        if prefs.auto_detect_language:
            segments, info = self._whisper.transcribe(
                f, language=None, vad_filter=True, vad_parameters=vad_params,
                beam_size=1, condition_on_previous_text=False,
            )
            text = " ".join(s.text.strip() for s in segments)
            return text, (getattr(info, "language", None) or prefs.stt_language)
        segments, info = self._whisper.transcribe(
            f, language=prefs.stt_language, vad_filter=True,
            vad_parameters=vad_params, beam_size=1,
            condition_on_previous_text=False,
        )
        return " ".join(s.text.strip() for s in segments), prefs.stt_language


    # Whisper hallucination patterns moved to module-level (line 97)
    # so they precede the class definition. The duplicate block here
    # was breaking class scope — the next method `_speak` was getting
    # interpreted as nested inside `_is_likely_hallucination` because
    # it lived between two top-level defs at indent 0.

    def _speak(self, text: str) -> None:
        """Speak `text` aloud. Tries ElevenLabs first if KEE_TTS_PROVIDER=
        elevenlabs and key is set, falls back to Piper. Supports barge-in.
        """
        import sounddevice as sd
        text = text[:1500]

        # ── ElevenLabs path (premium quality) ────────────────────────
        from kee.perception import tts_elevenlabs as el
        if el.selected_provider() == "elevenlabs":
            with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as mp:
                mp3_path = mp.name
            try:
                ok = el.synthesize(text, mp3_path)
                if ok:
                    el.play_mp3(mp3_path)
                    return
                # else fall through to Piper
                logger.info("ElevenLabs failed, falling back to Piper")
            finally:
                try: Path(mp3_path).unlink(missing_ok=True)
                except Exception: pass

        # ── Piper path (default, local, free) ────────────────────────
        self._ensure_piper()
        if not self._piper_voice:
            return
        text = text[:600]

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav:
            wav_path = wav.name

        try:
            args = list(getattr(self, "_piper_prefs", None).to_piper_args(Path(self._piper_voice))) \
                if getattr(self, "_piper_prefs", None) is not None \
                else ["--model", self._piper_voice]
            proc = subprocess.run(
                [self._piper_bin, *args, "--output_file", wav_path],
                input=text.encode("utf-8"),
                capture_output=True,
                timeout=60,
            )
            if proc.returncode != 0:
                logger.warning("piper failed: %s", proc.stderr.decode("utf-8", "replace")[:300])
                return
            with wave.open(wav_path, "rb") as wf:
                sr = wf.getframerate()
                data = wf.readframes(wf.getnframes())
            arr = np.frombuffer(data, dtype=np.int16)
            duration_s = len(arr) / float(sr)

            # ── Barge-in machinery ──────────────────────────────────────
            # While Kee speaks, the mic also picks up Kee's own speakers
            # (acoustic echo). To avoid self-trigger we use a high RMS
            # threshold that requires real human-loud speech, plus N
            # consecutive hits to suppress single transient peaks.
            self._barge_in = False
            if self._stream is None:
                # No open mic (e.g. running TTS-only test) — fall back to
                # blocking playback, no barge-in possible.
                sd.play(arr, samplerate=sr, blocking=True)
                return
            sd.play(arr, samplerate=sr, blocking=False)
            t_start = time.monotonic()
            hits = 0
            BARGE_RMS = 1200.0          # ~3-4× normal speech floor
            BARGE_HITS_REQ = 3          # need ~240ms of sustained input
            try:
                while (time.monotonic() - t_start) < duration_s + 0.2:
                    try:
                        d, _ = self._stream.read(WAKE_CHUNK)
                    except Exception:
                        break
                    rms = float(np.abs(d).mean())
                    if rms >= BARGE_RMS:
                        hits += 1
                        if hits >= BARGE_HITS_REQ:
                            logger.info("Barge-in detected (rms=%.0f) — interrupting TTS", rms)
                            sd.stop()
                            self._barge_in = True
                            break
                    else:
                        hits = 0
            except (KeyboardInterrupt, asyncio.CancelledError):
                sd.stop()
                raise
        except (subprocess.TimeoutExpired, FileNotFoundError) as e:
            logger.warning("piper subprocess failed: %s", e)
        finally:
            try:
                Path(wav_path).unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _split_for_streaming(text: str,
                              min_chars: int = 40,
                              max_chars: int = 280) -> list[str]:
        """Split a reply into chunks suitable for sequential TTS rendering.

        Heuristic: chunk on sentence terminators (. ! ? newline), but
        coalesce runs that are too short (< min_chars) into the next
        chunk so we don't render "OK." as its own 0.3s clip. Cap each
        chunk at max_chars so Piper renders fast (~0.6s per chunk).
        """
        import re as _re
        if not text:
            return []
        text = text.strip()
        if not text:
            return []
        if len(text) <= min_chars:
            return [text]
        # Split on sentence terminators but KEEP the terminator with the
        # preceding chunk for natural prosody.
        parts = _re.split(r"(?<=[.!?])\s+", text)
        chunks: list[str] = []
        buf = ""
        for p in parts:
            p = p.strip()
            if not p:
                continue
            # If buf is empty, just take p
            if not buf:
                buf = p
                continue
            # Coalesce short prior buffer into this part
            if len(buf) < min_chars:
                buf = (buf + " " + p).strip()
                continue
            # Buf is big enough — emit it and start fresh
            chunks.append(buf[:max_chars])
            buf = p
        if buf:
            chunks.append(buf[:max_chars])
        return chunks

    def _speak_streaming(self, text: str) -> None:
        """Sentence-by-sentence TTS — first audio in ~1s vs 4s for the
        full paragraph. Same Piper backend, same barge-in machinery, just
        chunked. Opt-in via KEE_VOICE_STREAMING=1.

        ElevenLabs path is NOT chunked here (already low-latency via MP3
        streaming on its end); falls through to the standard _speak() if
        ElevenLabs is the selected provider.
        """
        from kee.perception import tts_elevenlabs as el
        if el.selected_provider() == "elevenlabs":
            return self._speak(text)

        import sounddevice as sd
        chunks = self._split_for_streaming(text[:1500])
        if not chunks:
            return
        self._ensure_piper()
        if not self._piper_voice:
            return

        self._barge_in = False
        for idx, chunk in enumerate(chunks):
            if self._barge_in or self._stop.is_set():
                logger.info("streaming TTS aborted at chunk %d/%d",
                            idx, len(chunks))
                return
            with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as wav:
                wav_path = wav.name
            try:
                args = list(getattr(self, "_piper_prefs", None).to_piper_args(
                    Path(self._piper_voice))) \
                    if getattr(self, "_piper_prefs", None) is not None \
                    else ["--model", self._piper_voice]
                proc = subprocess.run(
                    [self._piper_bin, *args, "--output_file", wav_path],
                    input=chunk.encode("utf-8"),
                    capture_output=True, timeout=30,
                )
                if proc.returncode != 0:
                    logger.warning("piper chunk %d failed: %s",
                                   idx, proc.stderr.decode("utf-8", "replace")[:200])
                    continue
                with wave.open(wav_path, "rb") as wf:
                    sr = wf.getframerate()
                    data = wf.readframes(wf.getnframes())
                arr = np.frombuffer(data, dtype=np.int16)
                duration_s = len(arr) / float(sr)

                if self._stream is None:
                    sd.play(arr, samplerate=sr, blocking=True)
                    continue

                sd.play(arr, samplerate=sr, blocking=False)
                t_start = time.monotonic()
                hits = 0
                BARGE_RMS = 1200.0
                BARGE_HITS_REQ = 3
                try:
                    while (time.monotonic() - t_start) < duration_s + 0.05:
                        try:
                            d, _ = self._stream.read(WAKE_CHUNK)
                        except Exception:
                            break
                        rms = float(np.abs(d).mean())
                        if rms >= BARGE_RMS:
                            hits += 1
                            if hits >= BARGE_HITS_REQ:
                                logger.info(
                                    "Barge-in mid-chunk %d (rms=%.0f) — "
                                    "aborting remaining queue", idx, rms,
                                )
                                sd.stop()
                                self._barge_in = True
                                return
                        else:
                            hits = 0
                except (KeyboardInterrupt, asyncio.CancelledError):
                    sd.stop()
                    raise
            except (subprocess.TimeoutExpired, FileNotFoundError) as e:
                logger.warning("piper streaming chunk %d failed: %s", idx, e)
            finally:
                try:
                    Path(wav_path).unlink(missing_ok=True)
                except Exception:
                    pass
