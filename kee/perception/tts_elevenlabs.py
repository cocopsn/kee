"""ElevenLabs TTS provider — optional fallback for higher quality voice.

Configuration (env or `.env`):
    ELEVENLABS_API_KEY=sk_...
    ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # "Rachel" default
    ELEVENLABS_MODEL=eleven_multilingual_v2

Pricing notes (2025): free tier is ~10k chars/month, paid starts $5/mo
for 30k. Each Kee voice reply averages 80-200 chars, so ~50-150 replies
on free, ~150-450 on starter. Activate by setting `KEE_TTS_PROVIDER=elevenlabs`
in .env. Without that, Piper stays as the default.

Streaming optional: we use the basic synth (returns full mp3) since
ElevenLabs streaming requires WebSocket setup and we don't yet stream
TTS to the client. Future improvement: chunked WS streaming.
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


def is_configured() -> bool:
    return bool(os.environ.get("ELEVENLABS_API_KEY", "").strip())


def selected_provider() -> str:
    """Returns 'elevenlabs' | 'piper'. Pulled from env each call so a
    settings change applies live without restart."""
    pref = os.environ.get("KEE_TTS_PROVIDER", "").strip().lower()
    if pref in ("elevenlabs", "11labs", "eleven"):
        return "elevenlabs" if is_configured() else "piper"
    return "piper"


def synthesize(
    text: str,
    out_path: str | Path,
    voice_id: Optional[str] = None,
    model: Optional[str] = None,
    timeout: float = 30.0,
) -> bool:
    """Synthesize `text` → MP3 file at `out_path`. Returns True on success.

    Falls back to False (caller should fall back to Piper) on any error.
    """
    api_key = os.environ.get("ELEVENLABS_API_KEY", "").strip()
    if not api_key:
        return False
    voice_id = voice_id or os.environ.get(
        "ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM",
    )
    model_id = model or os.environ.get(
        "ELEVENLABS_MODEL", "eleven_multilingual_v2",
    )
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}"
    headers = {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": text[:2500],  # service caps at ~2500
        "model_id": model_id,
        "voice_settings": {
            "stability": 0.55,
            "similarity_boost": 0.75,
            "style": 0.30,
            "use_speaker_boost": True,
        },
    }
    t0 = time.monotonic()
    try:
        with httpx.Client(timeout=timeout) as c:
            r = c.post(url, json=payload, headers=headers)
            if r.status_code != 200:
                logger.warning("ElevenLabs HTTP %d: %s",
                               r.status_code, r.text[:200])
                return False
            Path(out_path).write_bytes(r.content)
            elapsed_ms = int((time.monotonic() - t0) * 1000)
            logger.info("ElevenLabs synth ok: %d bytes in %d ms",
                        len(r.content), elapsed_ms)
            return True
    except Exception as e:
        logger.warning("ElevenLabs synth failed: %s", e)
        return False


def play_mp3(path: str | Path) -> bool:
    """Decode + play an MP3 file via sounddevice. Returns True if played."""
    try:
        import numpy as np
        try:
            import miniaudio
        except ImportError:
            # Fall back to pydub if available
            try:
                from pydub import AudioSegment
                seg = AudioSegment.from_file(str(path), format="mp3")
                arr = np.array(seg.get_array_of_samples(), dtype=np.int16)
                if seg.channels > 1:
                    arr = arr.reshape(-1, seg.channels).mean(axis=1).astype(np.int16)
                import sounddevice as sd
                sd.play(arr, samplerate=seg.frame_rate, blocking=True)
                return True
            except ImportError:
                logger.warning("Neither miniaudio nor pydub installed; "
                               "can't decode mp3. Run `pip install miniaudio`.")
                return False

        # Preferred: miniaudio (no FFmpeg required)
        sample = miniaudio.decode_file(str(path))
        arr = np.frombuffer(sample.samples, dtype=np.int16)
        if sample.nchannels > 1:
            arr = arr.reshape(-1, sample.nchannels).mean(axis=1).astype(np.int16)
        import sounddevice as sd
        sd.play(arr, samplerate=sample.sample_rate, blocking=True)
        return True
    except Exception as e:
        logger.warning("mp3 playback failed: %s", e)
        return False
