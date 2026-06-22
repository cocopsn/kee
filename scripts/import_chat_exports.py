"""Import Claude / ChatGPT JSON chat exports into Kee's vault.

Both providers offer a "data export" feature that produces a ZIP
containing JSON conversations. This script accepts either the ZIP
directly or the extracted JSON file(s) and converts each conversation
into a markdown file under `vault/imports/<provider>/<date>-<slug>.md`.

Why import? So Kee's `memory_search` (RAG) can pull in past
conversations as long-term context. Also for human review in Obsidian.

Provider data formats:
  * **Claude**: `conversations.json` — list of objects with
    `name`, `created_at`, `chat_messages: [{sender, text, created_at}]`
  * **ChatGPT**: `conversations.json` — list with `title`, `create_time`,
    `mapping: {id: {message: {author: {role}, content: {parts}}}}` (tree)

Usage:
    python -m scripts.import_chat_exports --provider claude    path/to/export.zip
    python -m scripts.import_chat_exports --provider chatgpt   path/to/export.zip
    python -m scripts.import_chat_exports --provider claude    path/to/conversations.json
"""

from __future__ import annotations

import argparse
import io
import json
import re
import sys
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

VAULT = Path(r"D:/Kee/vault")
IMPORTS = VAULT / "imports"


def _slug(s: str) -> str:
    s = re.sub(r"[^\w\s-]", "", s, flags=re.UNICODE).strip().lower()
    s = re.sub(r"[\s_-]+", "-", s)
    return s[:60] or "untitled"


def _read_json_from_input(path: Path) -> dict | list:
    """Accept either a JSON file or a ZIP containing conversations.json."""
    if path.suffix.lower() == ".zip":
        with zipfile.ZipFile(path) as z:
            for n in z.namelist():
                if n.endswith("conversations.json"):
                    with z.open(n) as f:
                        return json.loads(f.read().decode("utf-8"))
            raise FileNotFoundError("conversations.json not in zip")
    if path.is_dir():
        # Look for conversations.json inside
        cand = path / "conversations.json"
        if cand.exists():
            return json.loads(cand.read_text(encoding="utf-8"))
        raise FileNotFoundError(f"no conversations.json in {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _claude_to_md(conv: dict) -> tuple[str, str]:
    """Return (filename, markdown) for one Claude conversation."""
    title = conv.get("name") or "untitled"
    created = conv.get("created_at", "")
    date = (created[:10] if created else datetime.now().strftime("%Y-%m-%d"))
    fname = f"{date}-{_slug(title)}.md"
    lines = [f"# {title}", "", f"*Imported from Claude — created {created}*", ""]
    for m in conv.get("chat_messages", []):
        sender = m.get("sender", "?")
        ts = m.get("created_at", "")
        text = m.get("text", "") or ""
        lines.append(f"## {sender.title()} — {ts}")
        lines.append("")
        lines.append(text.strip())
        lines.append("")
    return fname, "\n".join(lines)


def _chatgpt_to_md(conv: dict) -> tuple[str, str]:
    """Return (filename, markdown) for one ChatGPT conversation."""
    title = conv.get("title") or "untitled"
    ts = conv.get("create_time")
    date = (datetime.fromtimestamp(ts).strftime("%Y-%m-%d")
            if isinstance(ts, (int, float)) else datetime.now().strftime("%Y-%m-%d"))
    fname = f"{date}-{_slug(title)}.md"
    # Walk the message tree from the root
    mapping = conv.get("mapping", {})
    # Find the root (no parent)
    roots = [k for k, v in mapping.items() if not v.get("parent")]
    ordered: list[dict] = []

    def walk(node_id: str) -> None:
        node = mapping.get(node_id)
        if not node:
            return
        msg = node.get("message")
        if msg:
            ordered.append(msg)
        for child in node.get("children") or []:
            walk(child)

    for r in roots:
        walk(r)

    lines = [f"# {title}", "", f"*Imported from ChatGPT — created {date}*", ""]
    for msg in ordered:
        author = msg.get("author", {}).get("role", "?")
        if author == "system":
            continue
        content = msg.get("content", {})
        parts = content.get("parts") or []
        text = "\n".join(str(p) for p in parts if p).strip()
        if not text:
            continue
        ct = msg.get("create_time")
        ct_str = (datetime.fromtimestamp(ct).isoformat(timespec="minutes")
                  if isinstance(ct, (int, float)) else "")
        lines.append(f"## {author.title()} — {ct_str}")
        lines.append("")
        lines.append(text)
        lines.append("")
    return fname, "\n".join(lines)


def import_export(provider: str, source: Path) -> dict:
    data = _read_json_from_input(source)
    if not isinstance(data, list):
        # Sometimes wrapped in {"conversations": [...]}
        if isinstance(data, dict) and "conversations" in data:
            data = data["conversations"]
        else:
            return {"ok": False, "error": "expected JSON list of conversations"}

    out_dir = IMPORTS / provider
    out_dir.mkdir(parents=True, exist_ok=True)
    written = 0
    skipped = 0
    converters = {"claude": _claude_to_md, "chatgpt": _chatgpt_to_md}
    if provider not in converters:
        return {"ok": False, "error": f"unknown provider {provider}"}
    convert = converters[provider]
    for conv in data:
        try:
            fname, md = convert(conv)
            out = out_dir / fname
            n = 0
            while out.exists():
                n += 1
                out = out_dir / f"{fname[:-3]}-{n}.md"
            out.write_text(md, encoding="utf-8")
            written += 1
        except Exception as e:
            skipped += 1
    return {"ok": True, "written": written, "skipped": skipped, "out_dir": str(out_dir)}


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--provider", choices=["claude", "chatgpt"], required=True)
    p.add_argument("source", help="ZIP file, JSON file, or directory containing conversations.json")
    args = p.parse_args()
    src = Path(args.source).expanduser().resolve()
    if not src.exists():
        print(f"✗ not found: {src}")
        return 1
    print(f"Importing {args.provider} export from {src} ...")
    r = import_export(args.provider, src)
    if not r["ok"]:
        print(f"✗ {r['error']}")
        return 1
    print(f"✓ wrote {r['written']} conversations (skipped {r['skipped']}) → {r['out_dir']}")
    print(f"\nNext: Kee's RAG will index these on the next vault sweep "
          f"(or run `python -m kee.main index` to force).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
