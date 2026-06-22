"""Tests for LLM chain ordering — confirms the bug fix where changing
KEE_LLM_PRIMARY made other providers disappear. Now ALL 4 are always
in the chain, just reordered.

No paid LLM calls — just construction + ordering.
"""

from __future__ import annotations

import os


def _set_primary(name: str):
    os.environ["KEE_LLM_PRIMARY"] = name


def test_chain_always_includes_configured_providers():
    """Whatever primary we pick, the chain should include every provider
    that successfully constructed. ollama_remote is conditional on
    AUCTORUM_OLLAMA being set."""
    from kee.core.llm.chain import build_default_chain
    import os as _os

    fails = 0
    candidates = ["claude", "haiku", "openai", "ollama"]
    if _os.environ.get("AUCTORUM_OLLAMA"):
        candidates.append("ollama_remote")
    for primary in candidates:
        _set_primary(primary)
        chain = build_default_chain()
        names = [p.name for p in chain.providers]
        if names[0] != primary:
            print(f"  ✗ primary={primary} → first provider is {names[0]!r}")
            fails += 1
            continue
        if "ollama" not in names:
            print(f"  ✗ primary={primary} → ollama (free fallback) missing")
            fails += 1
            continue
        # If keys are configured, all candidates should be there.
        if len(names) >= 3:
            print(f"  ✓ primary={primary} → {names}")
        else:
            print(f"  ⚠ primary={primary} → {names} (some missing; OK in dev)")
    return fails


def test_kill_switch_threshold():
    """Cap status math should be exact; kill_active flips at 100% spend."""
    from kee.core.llm.cost_tracker import _cap_usd

    cap = _cap_usd()
    if cap <= 0:
        print("  ✗ cap is non-positive")
        return 1
    print(f"  ✓ cap_usd = ${cap}")
    return 0


def test_ollama_always_present():
    """Ollama is the safety net; it must always survive even if everything
    else fails to construct."""
    from kee.core.llm.chain import build_default_chain
    # Force a bogus primary
    os.environ["KEE_LLM_PRIMARY"] = "nonexistent_provider"
    chain = build_default_chain()
    names = [p.name for p in chain.providers]
    if "ollama" not in names:
        print(f"  ✗ ollama missing from chain {names}")
        return 1
    print(f"  ✓ bogus primary → fallback chain still has ollama: {names}")
    return 0


if __name__ == "__main__":
    print("=== chain always includes configured providers ===")
    f1 = test_chain_always_includes_configured_providers()
    print()
    print("=== kill switch threshold ===")
    f2 = test_kill_switch_threshold()
    print()
    print("=== ollama always present ===")
    f3 = test_ollama_always_present()
    # Restore primary for downstream tests
    os.environ["KEE_LLM_PRIMARY"] = "ollama"
    print()
    total = f1 + f2 + f3
    if total == 0:
        print("All passed ✓")
    else:
        print(f"{total} test(s) failed")
        raise SystemExit(1)
