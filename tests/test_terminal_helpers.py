"""Regression tests for terminal-only helpers.

These keep local REPL affordances out of the LLM/tool path where possible.
"""

from __future__ import annotations

from kee.surfaces.terminal import _local_model_answer


class _FakeLLM:
    host = "http://localhost:11434"
    model = "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M"


class _FakeRegistry:
    tools = {"one": object(), "two": object()}


class _FakeAgent:
    llm = _FakeLLM()
    registry = _FakeRegistry()


def test_local_model_answer():
    answer = _local_model_answer(_FakeAgent(), "cual es tu modelo de llm actual")
    fails = 0
    if answer is None:
        print("  [FAIL] model question returned no local answer")
        return 1
    checks = [
        (_FakeLLM.model in answer, "includes model id"),
        (_FakeLLM.host in answer, "includes Ollama host"),
        ("2" in answer, "includes tool count"),
    ]
    for ok, label in checks:
        if ok:
            print(f"  [ok] {label}")
        else:
            fails += 1
            print(f"  [FAIL] {label}")
    unrelated = _local_model_answer(_FakeAgent(), "hola kee")
    if unrelated is None:
        print("  [ok] ignores normal chat")
    else:
        fails += 1
        print(f"  [FAIL] normal chat matched unexpectedly: {unrelated!r}")
    return fails


if __name__ == "__main__":
    print("=== terminal local model answer ===")
    total = test_local_model_answer()
    if total == 0:
        print("All passed.")
    else:
        print(f"{total} test(s) failed")
        raise SystemExit(1)
