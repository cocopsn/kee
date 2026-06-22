"""Tests for router.md parser and local-primary routing. Pure-Python, $0."""

import asyncio
import os

from kee.core.router import Router


def test_yaml_list_parser():
    txt = """
- match: '^hola$'
  reply: 'Hola Coco.'

- match: '^bye$'
  reply: 'Hasta luego.'
"""
    rules = Router._parse_yaml_list(txt)
    assert len(rules) == 2, rules
    assert rules[0]["match"] == "^hola$"
    assert rules[0]["reply"] == "Hola Coco."
    assert rules[1]["match"] == "^bye$"
    print("  ✓ parse_yaml_list 2 entries")


def test_yaml_dict_parser():
    txt = """
simple:
  - 'cuántos correos'
  - 'qué hora'

heavy:
  - 'haz un plan'
  - 'estrategia'
"""
    d = Router._parse_yaml_dict(txt)
    assert "simple" in d
    assert len(d["simple"]) == 2, d
    assert d["simple"][0] == "cuántos correos"
    assert d["heavy"][1] == "estrategia"
    print("  ✓ parse_yaml_dict 2 keys with 2 entries each")


def test_extract_yaml_after():
    md = """
## DIRECT_ANSWERS

Some prose here.

```yaml
- match: 'foo'
  reply: 'bar'
```

## TIER_HINTS

```yaml
simple:
  - 'baz'
```
"""
    direct = Router._extract_yaml_after(md, "## DIRECT_ANSWERS")
    assert "match: 'foo'" in direct
    assert "TIER_HINTS" not in direct  # stops at next section
    hints = Router._extract_yaml_after(md, "## TIER_HINTS")
    assert "simple:" in hints
    print("  ✓ extract_yaml_after isolates section blocks")


async def test_ollama_primary_skips_llm_classifier():
    old_primary = os.environ.get("KEE_LLM_PRIMARY")
    os.environ["KEE_LLM_PRIMARY"] = "ollama"
    try:
        router = Router()

        async def fail_classifier(_text: str):
            raise AssertionError("classifier should not run when primary is ollama")

        router._classify = fail_classifier  # type: ignore[method-assign]
        decision = await router.route("esto no debe llamar clasificador x9")
        assert decision.provider_target == "ollama", decision
        assert "local primary" in decision.reason, decision
    finally:
        if old_primary is None:
            os.environ.pop("KEE_LLM_PRIMARY", None)
        else:
            os.environ["KEE_LLM_PRIMARY"] = old_primary
    print("  ✓ ollama primary skips LLM classifier")


if __name__ == "__main__":
    fails = 0
    for name in (
        "test_yaml_list_parser",
        "test_yaml_dict_parser",
        "test_extract_yaml_after",
        "test_ollama_primary_skips_llm_classifier",
    ):
        try:
            result = globals()[name]()
            if asyncio.iscoroutine(result):
                asyncio.run(result)
        except AssertionError as e:
            fails += 1
            print(f"  ✗ {name}: {e}")
    if fails:
        print(f"{fails} test(s) failed")
        raise SystemExit(1)
    print("All passed ✓")
