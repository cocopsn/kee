"""apply_rewrite — $0, no LLM calls, no git mutation.

Tests the pure helpers + the safety guards (refuses without confirm,
refuses on missing proposal file, refuses on too-short text, properly
parses the proposal markdown, and replace_description finds + swaps
all 3 description-attribute syntactic forms).

Run::

    .venv\\Scripts\\python.exe tests/test_apply_rewrite.py
"""

from __future__ import annotations

import tempfile
from pathlib import Path


def test_parse_proposal_missing() -> int:
    from kee.cognition.tool_rewrite_apply import parse_proposal
    out = parse_proposal(Path("/no/such/file.md"))
    if not out["ok"] and "no proposal" in out["error"]:
        print("  [ok] missing file -> clear error")
        return 0
    print(f"  [FAIL] {out}")
    return 1


def test_parse_proposal_too_short() -> int:
    from kee.cognition.tool_rewrite_apply import parse_proposal
    with tempfile.NamedTemporaryFile(
        suffix="-foo_tool.md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write("# Tool rewrite\n\n## Proposed rewrite\n```\nshort\n```\n")
        p = Path(f.name)
    # Filename must look like YYYY-MM-DD-<tool>.md (parse_proposal slices [11:])
    target = p.with_name("2026-05-04-foo_tool.md")
    p.rename(target)
    try:
        out = parse_proposal(target)
        if not out["ok"] and "too short" in out["error"]:
            print("  [ok] short text rejected")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        target.unlink(missing_ok=True)


def test_parse_proposal_happy() -> int:
    from kee.cognition.tool_rewrite_apply import parse_proposal
    body = "A nice long replacement description that exceeds 20 chars."
    md = f"# Tool rewrite\n\n## Proposed rewrite\n```\n{body}\n```\n"
    with tempfile.NamedTemporaryFile(
        suffix=".md", mode="w", delete=False, encoding="utf-8"
    ) as f:
        f.write(md)
        p = Path(f.name)
    target = p.with_name("2026-05-04-mytool.md")
    p.rename(target)
    try:
        out = parse_proposal(target)
        if (out.get("ok") and out.get("tool") == "mytool"
                and out.get("proposed") == body):
            print("  [ok] proposal parsed correctly")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        target.unlink(missing_ok=True)


def test_replace_description_triple_quote() -> int:
    from kee.cognition.tool_rewrite_apply import replace_description
    src = '''class T:
    name = "x"
    description = """old desc"""
    risk_level = 0
'''
    out, ok = replace_description(src, "BRAND NEW DESCRIPTION")
    if ok and 'description = """BRAND NEW DESCRIPTION"""' in out and "old desc" not in out:
        print("  [ok] triple-quoted description replaced")
        return 0
    print(f"  [FAIL] ok={ok}\n{out}")
    return 1


def test_replace_description_paren_concat() -> int:
    from kee.cognition.tool_rewrite_apply import replace_description
    src = '''class T:
    name = "x"
    description = (
        "old "
        "concat "
        "desc"
    )
    risk_level = 0
'''
    out, ok = replace_description(src, "NEW")
    if ok and "NEW" in out and "old " not in out:
        print("  [ok] paren-concat description replaced")
        return 0
    print(f"  [FAIL] ok={ok}\n{out}")
    return 1


def test_replace_description_single_line() -> int:
    from kee.cognition.tool_rewrite_apply import replace_description
    src = '''class T:
    name = "x"
    description = "old single"
    risk_level = 0
'''
    out, ok = replace_description(src, "NEW SINGLE LINER")
    if ok and "NEW SINGLE LINER" in out and "old single" not in out:
        print("  [ok] single-line description replaced")
        return 0
    print(f"  [FAIL] ok={ok}\n{out}")
    return 1


def test_replace_description_none_found() -> int:
    from kee.cognition.tool_rewrite_apply import replace_description
    src = '''class T:
    name = "x"
    risk_level = 0
'''
    out, ok = replace_description(src, "won't apply")
    if not ok and out == src:
        print("  [ok] no description -> no replacement, source untouched")
        return 0
    print(f"  [FAIL] ok={ok}, src changed?")
    return 1


def test_apply_proposal_requires_confirm() -> int:
    from kee.cognition.tool_rewrite_apply import apply_proposal
    out = apply_proposal("2026-05-04", "shell", confirm=False)
    if not out["ok"] and "confirm=True required" in out["error"]:
        print("  [ok] apply refuses without confirm=True")
        return 0
    print(f"  [FAIL] {out}")
    return 1


if __name__ == "__main__":
    print("=== apply_rewrite ===")
    fails = 0
    fails += test_parse_proposal_missing()
    fails += test_parse_proposal_too_short()
    fails += test_parse_proposal_happy()
    fails += test_replace_description_triple_quote()
    fails += test_replace_description_paren_concat()
    fails += test_replace_description_single_line()
    fails += test_replace_description_none_found()
    fails += test_apply_proposal_requires_confirm()
    print()
    print(f"Done. failures={fails}")
    raise SystemExit(0 if fails == 0 else 1)
