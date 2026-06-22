"""Regression tests for response post-processing — no LLM calls, $0.

Covers:
  - _strip_followup_offers: drop trailing "would you like…?" / "¿te gustaría…?"
  - _strip_thinking: remove qwen3 reasoning leak (paired tags + bare prefix)
  - _strip_repetition_loop: cut repeated paragraph degeneration

Run:
    cd D:\\Kee
    .venv\\Scripts\\python.exe -m pytest tests/test_strip_helpers.py -v
or just:
    .venv\\Scripts\\python.exe tests/test_strip_helpers.py
"""

from __future__ import annotations

from kee.core.agent import _strip_followup_offers
from kee.core.ollama_client import OllamaClient


# ── _strip_followup_offers ────────────────────────────────────────────────
FOLLOWUP_CASES = [
    # (input, expected, label)
    (
        "Tienes 27945 sin leer.\n\nWould you like help managing your inbox?",
        "Tienes 27945 sin leer.",
        "paragraph offer en",
    ),
    (
        "La abolición es compleja. ¿Te gustaría profundizar en algún aspecto?",
        "La abolición es compleja.",
        "sentence-boundary offer es",
    ),
    (
        "27,945 unread. This suggests a backlog - would you like me to help prioritize?",
        "27,945 unread. This suggests a backlog.",
        "inline-dash offer en",
    ),
    (
        "Plan listo.\n\n¿Quieres que proceda con el rollout?",
        "Plan listo.",
        "paragraph quieres es",
    ),
    (
        "Lista A, B, C.\n\nLet me know if you want a summary.",
        "Lista A, B, C.",
        "let-me-know paragraph en",
    ),
    (
        "27945 sin leer. Want me to filter by sender?",
        "27945 sin leer.",
        "want-me-to sentence en",
    ),
    # Should NOT strip (no offer)
    ("Las 5:30 PM.", "Las 5:30 PM.", "plain — keep"),
    (
        "Tienes 4 eventos hoy: Fat Dogs, Hackathon, NETPROBE, ISC2.",
        "Tienes 4 eventos hoy: Fat Dogs, Hackathon, NETPROBE, ISC2.",
        "plain list — keep",
    ),
]


def test_strip_followup_offers():
    fails = 0
    for inp, expected, label in FOLLOWUP_CASES:
        got = _strip_followup_offers(inp)
        if got == expected:
            print(f"  ✓ {label}")
        else:
            fails += 1
            print(f"  ✗ {label}")
            print(f"     IN : {inp!r}")
            print(f"     GOT: {got!r}")
            print(f"     EXP: {expected!r}")
    return fails


# ── _strip_thinking ──────────────────────────────────────────────────────
THINK_CASES = [
    (
        "<think>internal reasoning</think>Real answer here.",
        "Real answer here.",
        "paired think tags",
    ),
    (
        "<thinking>different variant</thinking>Hello.",
        "Hello.",
        "thinking variant",
    ),
    (
        "Okay, let's see. The user wants A. Let me check.\n\nThe answer is X.",
        "The answer is X.",
        "bare-text leak prefix",
    ),
    (
        "Wait, I should reconsider.\n\nResponse: 27,945 unread.",
        "Response: 27,945 unread.",
        "wait-prefix leak",
    ),
    ("Hello world.", "Hello world.", "no leak — keep"),
]


def test_strip_thinking():
    fails = 0
    for inp, expected, label in THINK_CASES:
        got = OllamaClient._strip_thinking(inp)
        if got == expected:
            print(f"  ✓ {label}")
        else:
            fails += 1
            print(f"  ✗ {label}")
            print(f"     IN : {inp!r}")
            print(f"     GOT: {got!r}")
            print(f"     EXP: {expected!r}")
    return fails


# ── _strip_repetition_loop ───────────────────────────────────────────────
def test_strip_repetition_loop():
    # 12 copies of the same paragraph (simulating qwen3 degeneration)
    para = "The response is also written with a clear and concise language, avoiding unnecessary words and complex structures.\n\n"
    loop = "Real opening sentence about the topic.\n\n" + para * 12
    out = OllamaClient._strip_repetition_loop(loop)
    if len(out) < len(loop) * 0.5:
        print(f"  ✓ loop cut: {len(loop)} → {len(out)} bytes")
        return 0
    print(f"  ✗ loop NOT cut: {len(loop)} → {len(out)} bytes")
    return 1


def test_tool_parser_failure_detection():
    cases = [
        (
            "Unable to generate parser for this template. Automatic parser generation failed",
            True,
            "ollama template parser failure",
        ),
        (
            "unexpected EOF while parsing tool call",
            True,
            "legacy eof parser failure",
        ),
        (
            "some unrelated bad request",
            False,
            "ordinary bad request",
        ),
    ]
    fails = 0
    for msg, expected, label in cases:
        got = OllamaClient._is_tool_parser_failure(msg)
        if got == expected:
            print(f"  ✓ {label}")
        else:
            fails += 1
            print(f"  ✗ {label}")
            print(f"     MSG: {msg!r}")
            print(f"     GOT: {got!r}")
            print(f"     EXP: {expected!r}")
    return fails


if __name__ == "__main__":
    print("=== _strip_followup_offers ===")
    f1 = test_strip_followup_offers()
    print()
    print("=== _strip_thinking ===")
    f2 = test_strip_thinking()
    print()
    print("=== _strip_repetition_loop ===")
    f3 = test_strip_repetition_loop()
    print()
    print("=== _is_tool_parser_failure ===")
    f4 = test_tool_parser_failure_detection()
    print()
    total = f1 + f2 + f3 + f4
    if total == 0:
        print("All passed ✓")
    else:
        print(f"{total} test(s) failed")
        raise SystemExit(1)
