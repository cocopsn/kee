"""Voice streaming TTS chunker — $0, no audio.

Tests the pure split helper `_split_for_streaming` so we don't ship
broken sentence-boundary logic. Audio playback path is hardware-bound
and stays manual / live.

Run::

    .venv\\Scripts\\python.exe tests/test_voice_streaming.py
"""

from __future__ import annotations

import sys


def test_short_text_single_chunk() -> int:
    from kee.perception.voice import VoicePipeline
    out = VoicePipeline._split_for_streaming("Listo.")
    if out == ["Listo."]:
        print("  [ok] short text -> 1 chunk")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_two_sentences_split() -> int:
    from kee.perception.voice import VoicePipeline
    text = ("Acabo de revisar el commit y todo se ve bien. "
            "El próximo paso es subirlo a producción cuando estés listo.")
    out = VoicePipeline._split_for_streaming(text)
    if len(out) == 2 and out[0].endswith(".") and out[1].startswith("El"):
        print(f"  [ok] 2 sentences -> 2 chunks ({len(out[0])} + {len(out[1])} chars)")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_short_first_sentence_coalesces() -> int:
    """\"OK.\" + long sentence should NOT emit \"OK.\" alone — the chunker
    must coalesce sub-min_chars buffers into the next part."""
    from kee.perception.voice import VoicePipeline
    text = ("OK. Te explico lo que pasó: el deploy falló porque el build "
            "no encontraba las dependencias del nuevo módulo de imágenes.")
    out = VoicePipeline._split_for_streaming(text)
    # Expected: 1 chunk, because "OK." is too short and gets merged.
    if len(out) == 1 and out[0].startswith("OK."):
        print(f"  [ok] short first sentence coalesced into 1 chunk")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_max_chars_caps_chunk() -> int:
    from kee.perception.voice import VoicePipeline
    long_chunk = "Una sola oracion sin terminador " * 30  # ~960 chars
    out = VoicePipeline._split_for_streaming(long_chunk, max_chars=200)
    if all(len(c) <= 200 for c in out):
        print(f"  [ok] all chunks <= 200 chars (got {[len(c) for c in out]})")
        return 0
    print(f"  [FAIL] {[len(c) for c in out]}")
    return 1


def test_empty_text_no_chunks() -> int:
    from kee.perception.voice import VoicePipeline
    if VoicePipeline._split_for_streaming("") == [] and \
       VoicePipeline._split_for_streaming("   ") == []:
        print("  [ok] empty / whitespace returns no chunks")
        return 0
    return 1


def test_three_sentences_three_chunks() -> int:
    from kee.perception.voice import VoicePipeline
    text = ("Primera oracion bastante larga para no coalescerse. "
            "Segunda oracion tambien larga para que sea su propio chunk. "
            "Tercera oracion final del bloque.")
    out = VoicePipeline._split_for_streaming(text)
    if len(out) == 3:
        print(f"  [ok] 3 long sentences -> 3 chunks")
        return 0
    print(f"  [FAIL] expected 3, got {len(out)}: {out}")
    return 1


def test_method_exists_and_callable() -> int:
    """Sanity: _speak_streaming method present on VoicePipeline."""
    from kee.perception.voice import VoicePipeline
    fn = getattr(VoicePipeline, "_speak_streaming", None)
    if fn and callable(fn):
        print("  [ok] _speak_streaming method exists")
        return 0
    print(f"  [FAIL] {fn}")
    return 1


if __name__ == "__main__":
    print("=== voice streaming chunker ===")
    fails = 0
    fails += test_method_exists_and_callable()
    fails += test_short_text_single_chunk()
    fails += test_two_sentences_split()
    fails += test_short_first_sentence_coalesces()
    fails += test_max_chars_caps_chunk()
    fails += test_empty_text_no_chunks()
    fails += test_three_sentences_three_chunks()
    print()
    print(f"Done. failures={fails}")
    sys.exit(0 if fails == 0 else 1)
