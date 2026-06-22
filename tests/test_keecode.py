"""KeeCode integration smoke tests.

No external OpenCode install is required. These tests pin the clean-room
contract: KeeCode talks to OpenCode through config/env/launcher files and uses
Kee's current Ollama model as the default coding model.
"""

from __future__ import annotations

import tempfile
from pathlib import Path


MODEL = "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M"


def test_opencode_config_points_at_ollama() -> int:
    from kee.integrations.keecode import build_opencode_config

    cfg = build_opencode_config(model=MODEL, ollama_host="http://localhost:11434")
    provider = cfg["provider"]["ollama"]

    if cfg.get("model") != f"ollama/{MODEL}":
        print(f"  [FAIL] wrong model id: {cfg.get('model')}")
        return 1
    if provider.get("npm") != "@ai-sdk/openai-compatible":
        print(f"  [FAIL] wrong provider adapter: {provider}")
        return 1
    if provider.get("options", {}).get("baseURL") != "http://localhost:11434/v1":
        print(f"  [FAIL] wrong baseURL: {provider}")
        return 1
    if MODEL not in provider.get("models", {}):
        print(f"  [FAIL] model missing from provider.models: {provider}")
        return 1

    print("  [ok] OpenCode config uses Ollama + Kee model")
    return 0


def test_context_bridge_persists_notes() -> int:
    from kee.integrations.keecode import write_context_bridge

    with tempfile.TemporaryDirectory() as td:
        path = write_context_bridge(
            notes="normal Kee chat context",
            session_id="dashboard",
            data_dir=Path(td),
        )
        text = path.read_text(encoding="utf-8")

    if "normal Kee chat context" not in text or "dashboard" not in text:
        print(f"  [FAIL] context file missing expected data: {text!r}")
        return 1
    print("  [ok] context bridge file persists continuity notes")
    return 0


def test_registry_loads_keecode_tool() -> int:
    from kee.core.tool_registry import ToolRegistry

    r = ToolRegistry()
    r.load_builtins()
    tool = r.tools.get("keecode")
    if tool is None:
        print("  [FAIL] keecode tool not registered")
        return 1
    actions = (
        tool.parameters_schema.get("properties", {})
        .get("action", {})
        .get("enum", [])
    )
    if not {"status", "launch", "prompt", "sync_context"}.issubset(set(actions)):
        print(f"  [FAIL] expected action enum missing: {actions}")
        return 1
    print("  [ok] keecode tool registered with expected actions")
    return 0


if __name__ == "__main__":
    print("=== keecode integration ===")
    fails = 0
    fails += test_opencode_config_points_at_ollama()
    fails += test_context_bridge_persists_notes()
    fails += test_registry_loads_keecode_tool()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
