"""Sanity check every registered tool — no LLM, no network, $0.

Loads the registry, then for each tool asserts:
  * `name` is a non-empty string of [a-z0-9_]
  * `description` exists and is at least 20 chars
  * `parameters_schema` is a dict with `type:"object"` (Ollama tool-call
    contract — anything else gets silently ignored by the LLM)
  * `risk_level` is an int in [0, 3]
  * `to_schema()` produces a dict the OpenAI/Ollama tool-call API accepts

Catches typos / accidental regressions when adding new tools.

Run::

    .venv\\Scripts\\python.exe tests/test_tool_schemas.py
"""

from __future__ import annotations

import re

NAME_RE = re.compile(r"^[a-z][a-z0-9_]{1,63}$")


def test_all_tools_well_formed() -> int:
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.load_builtins()
    tools = r.tools
    if not tools:
        print("  [FAIL] registry loaded zero tools")
        return 1

    fails: list[str] = []
    for name, t in tools.items():
        if not NAME_RE.match(name or ""):
            fails.append(f"{name}: bad name (must match {NAME_RE.pattern})")
        desc = (t.description or "").strip()
        if len(desc) < 20:
            fails.append(f"{name}: description too short ({len(desc)} chars)")
        sch = getattr(t, "parameters_schema", None)
        if not isinstance(sch, dict):
            fails.append(f"{name}: parameters_schema must be a dict")
        elif sch.get("type") != "object":
            fails.append(f"{name}: schema type must be 'object'")
        rl = getattr(t, "risk_level", None)
        if not isinstance(rl, int) or rl < 0 or rl > 3:
            fails.append(f"{name}: risk_level must be int 0..3 (got {rl!r})")
        try:
            schema = t.to_schema()
            if not isinstance(schema, dict):
                fails.append(f"{name}: to_schema() not a dict")
        except Exception as e:
            fails.append(f"{name}: to_schema() raised {e!r}")
    if fails:
        for f in fails:
            print(f"  [FAIL] {f}")
        return 1
    print(f"  [ok] {len(tools)} tools all well-formed")
    return 0


def test_no_duplicate_names() -> int:
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.load_builtins()
    names = list(r.names())
    dupes = [n for n in set(names) if names.count(n) > 1]
    if dupes:
        print(f"  [FAIL] duplicate tool names: {dupes}")
        return 1
    print(f"  [ok] no duplicate tool names ({len(names)} unique)")
    return 0


def test_minimum_tool_count() -> int:
    """Floor that catches accidental import deletions in tool_registry."""
    from kee.core.tool_registry import ToolRegistry
    r = ToolRegistry()
    r.load_builtins()
    n = len(r.tools)
    if n < 40:
        print(f"  [FAIL] tool count regressed: {n} < 40")
        return 1
    print(f"  [ok] {n} tools registered (floor 40)")
    return 0


if __name__ == "__main__":
    print("=== tool registry schemas ===")
    fails = 0
    fails += test_all_tools_well_formed()
    fails += test_no_duplicate_names()
    fails += test_minimum_tool_count()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
