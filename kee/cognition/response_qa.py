"""Post-response quality checker — Jarvis-pattern QA without the LLM cost.

Given an agent reply (text), runs a battery of cheap heuristic checks
and returns ``{ok, score 0..1, issues: [...]}``. Issues map to fix
suggestions the caller can either auto-apply (e.g. strip markdown for
voice) or feed back to the model on a retry.

Six categories of issue:
  1. character_break  — phrases that betray "I'm an AI" framing
  2. verbosity        — over-long for the apparent question complexity
  3. markdown_for_voice — formatting that Piper would mispronounce
  4. apology_loop     — "Lo siento" / "I'm sorry" repeated
  5. uncertainty_spam — "Sin embargo" / "es importante notar" filler
  6. language_mismatch — replied in English when source was voice (forced ES)

Cost: 0. Latency: < 1ms. Use as a pre-TTS gate or as input to a retry.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


# Phrases that always feel robotic. Both English and Spanish variants —
# the user can hear English even when Whisper mistakes the language.
_CHARACTER_BREAKS = [
    r"\bas an ai\b", r"\bas a (?:large )?language model\b",
    r"\bi cannot (?:provide|assist with|help with)\b",
    r"\bi(?:'m| am) (?:just |only |merely )?(?:an? )?ai\b",
    r"\bcomo (?:una? )?(?:asistente|ia|inteligencia artificial)\b",
    r"\bsoy (?:una? )?(?:asistente|ia|modelo)\b",
    r"\bno tengo emociones\b", r"\bno puedo sentir\b",
    r"\babsolutely\b",  # generic AI sycophancy
    r"\bcertainly[,!]\b",
    r"\bclaro[,!]\s+(?:te|le|aquí)\b",  # generic Spanish servility opener
]
_CHARACTER_BREAK_RE = [re.compile(p, re.IGNORECASE) for p in _CHARACTER_BREAKS]

_FILLER_PATTERNS = [
    r"\bes importante (?:notar|destacar|señalar|recordar)\b",
    r"\bsin embargo,?\b",
    r"\bcabe (?:mencionar|destacar)\b",
    r"\ben resumen,?\b.{0,80}$",  # only if at the end
    r"\bit'?s important to note\b",
    r"\bplease (?:let me know|note)\b",
]
_FILLER_RE = [re.compile(p, re.IGNORECASE) for p in _FILLER_PATTERNS]

_MARKDOWN_BAD_FOR_TTS = [
    r"`[^`]+`",          # inline code
    r"```",              # code blocks
    r"\*\*[^*]+\*\*",    # bold
    r"\[[^\]]+\]\([^)]+\)",  # markdown links
    r"#{1,6}\s",         # headers
    r"^\s*[-*•]\s",      # bullet points
    r"^\s*\d+[.)]\s",    # numbered lists
]
_MD_BAD_RE = [re.compile(p, re.MULTILINE) for p in _MARKDOWN_BAD_FOR_TTS]


@dataclass
class QAVerdict:
    ok: bool
    score: float
    issues: list[str]
    suggestions: list[str]
    meta: dict


def check(
    reply: str,
    *,
    source: str = "chat",          # 'voice' enforces stricter rules
    user_msg: str = "",
    expected_lang: str = "es",
) -> QAVerdict:
    """Run all heuristic checks; return a verdict."""
    issues: list[str] = []
    suggestions: list[str] = []
    meta: dict[str, Any] = {}
    score = 1.0

    if not reply.strip():
        return QAVerdict(ok=False, score=0.0,
                         issues=["empty_reply"], suggestions=["regenerate"],
                         meta={})

    # 1. Character breaks
    for rx in _CHARACTER_BREAK_RE:
        m = rx.search(reply)
        if m:
            issues.append(f"character_break: {m.group(0)!r}")
            suggestions.append("Drop AI-self-reference; speak as Kee directly.")
            score -= 0.25
            break

    # 2. Verbosity (voice-specific tighter cap)
    word_count = len(re.findall(r"\w+", reply))
    sentence_count = max(1, len(re.findall(r"[.!?]+", reply)))
    avg_sentence_len = word_count / sentence_count
    meta["word_count"] = word_count
    meta["sentence_count"] = sentence_count
    meta["avg_sentence_len"] = round(avg_sentence_len, 1)
    if source == "voice":
        if word_count > 80:
            issues.append(f"voice_verbosity: {word_count} words")
            suggestions.append(f"Reduce to ≤30 words for voice mode.")
            score -= 0.30
        if sentence_count > 3:
            issues.append(f"voice_too_many_sentences: {sentence_count}")
            suggestions.append("Voice mode: ≤2 sentences default.")
            score -= 0.15
    else:
        if word_count > 350:
            issues.append(f"chat_verbosity: {word_count} words")
            suggestions.append("Tighten — answer first, elaborate only if asked.")
            score -= 0.15

    # 3. Markdown that breaks TTS
    if source == "voice":
        md_hits = sum(1 for rx in _MD_BAD_RE if rx.search(reply))
        if md_hits:
            issues.append(f"markdown_in_voice: {md_hits} markdown elements")
            suggestions.append("Voice replies: plain prose. No backticks, lists, headers.")
            score -= 0.20

    # 4. Apology loops
    apologies = len(re.findall(r"\b(?:lo siento|disculpa|i'm sorry|sorry|perdón)\b",
                                reply, re.IGNORECASE))
    if apologies >= 2:
        issues.append(f"apology_loop: {apologies} apologies")
        suggestions.append("One acknowledgement max; then act.")
        score -= 0.10

    # 5. Filler
    filler_hits = sum(1 for rx in _FILLER_RE if rx.search(reply))
    if filler_hits >= 2:
        issues.append(f"filler: {filler_hits} stock phrases")
        suggestions.append("Cut 'es importante notar', 'sin embargo'.")
        score -= 0.10

    # 6. Language mismatch (only flagged if user explicitly expected ES)
    if expected_lang == "es" and source == "voice":
        # Crude heuristic: count common-English-word ratio
        en_markers = re.findall(
            r"\b(?:the|and|you|that|this|with|have|here|please|let me know)\b",
            reply, re.IGNORECASE,
        )
        es_markers = re.findall(
            r"\b(?:el|la|los|las|que|para|con|este|aquí|por favor)\b",
            reply, re.IGNORECASE,
        )
        if len(en_markers) > 3 and len(en_markers) > len(es_markers):
            issues.append("language_mismatch: replied in English")
            suggestions.append("Respond in Spanish (forced for voice).")
            score -= 0.40

    score = max(0.0, score)
    return QAVerdict(
        ok=score >= 0.6 and not any(i.startswith("language_mismatch") for i in issues),
        score=round(score, 2),
        issues=issues,
        suggestions=suggestions,
        meta=meta,
    )


def summary_for_retry(verdict: QAVerdict) -> str:
    """Format a short message the agent can prepend on a retry."""
    if not verdict.suggestions:
        return ""
    bullets = "\n".join(f"- {s}" for s in verdict.suggestions[:5])
    return (
        "El último intento de respuesta tuvo estos problemas (score "
        f"{verdict.score}). Corrige y vuelve a intentar:\n{bullets}"
    )
