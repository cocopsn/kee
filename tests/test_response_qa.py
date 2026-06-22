"""Regression tests for response_qa heuristics — no LLM calls, $0.

Covers the 6 categories Kee uses to grade voice/chat replies before sending:
  - character breaks ("como una IA, no puedo …")
  - voice verbosity (>80 words / >3 sentences)
  - markdown that breaks TTS
  - apology loops
  - filler phrases
  - language mismatch (English in voice mode when ES expected)

Plus a sanity check that `summary_for_retry` produces a non-empty bulleted
prompt the agent can prepend on a retry pass.

Run::

    cd D:\\Kee
    .venv\\Scripts\\python.exe tests/test_response_qa.py
"""

from __future__ import annotations

from kee.cognition.response_qa import check, summary_for_retry


OK = "[ok]"
NO = "[FAIL]"


def _has(verdict, kind: str) -> bool:
    return any(i.startswith(kind) for i in verdict.issues)


def test_character_break() -> int:
    v = check("Como una IA, no puedo hacer eso.", source="voice")
    if _has(v, "character_break") and v.score < 1.0:
        print("  [ok] character_break detected (score %.2f)" % v.score)
        return 0
    print("  [FAIL] character_break NOT detected:", v.issues)
    return 1


def test_voice_verbosity() -> int:
    long_reply = " ".join(["palabra"] * 90) + "."
    v = check(long_reply, source="voice", expected_lang="es")
    if _has(v, "voice_verbosity"):
        print("  [ok] voice_verbosity flagged at 90 words (score %.2f)" % v.score)
        return 0
    print("  [FAIL] voice_verbosity NOT flagged:", v.issues)
    return 1


def test_voice_too_many_sentences() -> int:
    v = check("Una. Dos. Tres. Cuatro.", source="voice", expected_lang="es")
    if _has(v, "voice_too_many_sentences"):
        print("  [ok] voice_too_many_sentences flagged (score %.2f)" % v.score)
        return 0
    print("  [FAIL] voice_too_many_sentences NOT flagged:", v.issues)
    return 1


def test_markdown_in_voice() -> int:
    v = check("Aquí va `code` y un - bullet.", source="voice")
    if _has(v, "markdown_in_voice"):
        print("  [ok] markdown_in_voice flagged (score %.2f)" % v.score)
        return 0
    print("  [FAIL] markdown_in_voice NOT flagged:", v.issues)
    return 1


def test_apology_loop() -> int:
    v = check("Lo siento. Disculpa, no debí. Lo siento de nuevo.",
              source="voice")
    if _has(v, "apology_loop"):
        print("  [ok] apology_loop flagged (score %.2f)" % v.score)
        return 0
    print("  [FAIL] apology_loop NOT flagged:", v.issues)
    return 1


def test_language_mismatch_voice() -> int:
    v = check(
        "The status is fine and you have here the report with the data.",
        source="voice", expected_lang="es",
    )
    if _has(v, "language_mismatch"):
        print("  [ok] language_mismatch flagged (score %.2f)" % v.score)
        return 0
    print("  [FAIL] language_mismatch NOT flagged:", v.issues)
    return 1


def test_clean_voice_reply_passes() -> int:
    v = check("Listo. Eventos cargados.", source="voice", expected_lang="es")
    if v.ok and v.score >= 0.9:
        print("  [ok] clean reply passes (score %.2f)" % v.score)
        return 0
    print("  [FAIL] clean reply rejected:", v.issues, "score", v.score)
    return 1


def test_chat_verbosity_threshold() -> int:
    # Chat tier allows up to 350 words — 100 should pass.
    text = " ".join(["palabra"] * 100) + "."
    v = check(text, source="chat", expected_lang="es")
    if not _has(v, "chat_verbosity"):
        print("  [ok] chat verbosity NOT triggered at 100 words")
        return 0
    print("  [FAIL] chat_verbosity false-positive:", v.issues)
    return 1


def test_summary_for_retry() -> int:
    v = check("Como una IA, no puedo. Lo siento. Disculpa.", source="voice")
    msg = summary_for_retry(v)
    if "score" in msg and "-" in msg:
        print("  [ok] summary_for_retry produces a bulleted prompt")
        return 0
    print("  [FAIL] summary_for_retry weak:", repr(msg))
    return 1


if __name__ == "__main__":
    print("=== response_qa heuristics ===")
    fails = 0
    fails += test_character_break()
    fails += test_voice_verbosity()
    fails += test_voice_too_many_sentences()
    fails += test_markdown_in_voice()
    fails += test_apology_loop()
    fails += test_language_mismatch_voice()
    fails += test_clean_voice_reply_passes()
    fails += test_chat_verbosity_threshold()
    fails += test_summary_for_retry()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
