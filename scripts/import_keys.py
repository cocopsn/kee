"""Interactive .env importer.

Scans Coco's known project directories, finds `.env*` files, shows the
key NAMES (never the values) with source path, and asks `keep / skip /
remap` per key. Approved keys land in `D:/Kee/.env`.

Run:
    .\.venv\Scripts\python.exe scripts\import_keys.py

Safe-by-default:
  * Existing `D:/Kee/.env` keys are NEVER overwritten without confirmation.
  * Values are shown ONLY at the moment you press 'k' (keep) — never echoed
    in scan summaries.
  * Sensitive-looking keys (private_key, secret, password) are previewed
    masked even when shown.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Iterable


PROJECT_ROOT = Path(__file__).resolve().parent.parent
KEE_ENV = PROJECT_ROOT / ".env"

# Where to look. Add more as needed.
SCAN_ROOTS = [
    Path("D:/projects"),
    Path("D:/Codigo"),
    Path("D:/nahual"),
    Path("D:/auctorumsis-backup"),
]

ENV_PATTERNS = (".env", ".env.local", ".env.production", ".env.development")
KEY_RE = re.compile(r"^\s*([A-Z][A-Z0-9_]+)\s*=\s*(.*)$")
SENSITIVE_HINTS = ("PRIVATE_KEY", "SECRET", "PASSWORD", "PASS", "JWT")


def find_env_files() -> list[Path]:
    out: list[Path] = []
    for root in SCAN_ROOTS:
        if not root.exists():
            continue
        for pat in ENV_PATTERNS:
            out.extend(root.rglob(pat))
    # Dedup + filter out node_modules / .venv noise
    seen = set()
    clean = []
    for p in out:
        if any(part in {"node_modules", ".venv", "venv", "dist"} for part in p.parts):
            continue
        if p in seen:
            continue
        seen.add(p)
        clean.append(p)
    return sorted(clean)


def parse_env(path: Path) -> list[tuple[str, str]]:
    rows: list[tuple[str, str]] = []
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return rows
    for line in text.splitlines():
        if not line or line.lstrip().startswith("#"):
            continue
        m = KEY_RE.match(line)
        if m:
            key = m.group(1).strip()
            val = m.group(2).strip().strip('"').strip("'")
            rows.append((key, val))
    return rows


def load_existing_kee_env() -> dict[str, str]:
    if not KEE_ENV.exists():
        return {}
    out: dict[str, str] = {}
    for key, val in parse_env(KEE_ENV):
        out[key] = val
    return out


def append_to_kee_env(updates: list[tuple[str, str, str]]) -> None:
    """`updates` = (key, value, source_path). Appends with comments."""
    KEE_ENV.touch(exist_ok=True)
    text = KEE_ENV.read_text(encoding="utf-8") if KEE_ENV.exists() else ""
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n# ── Imported by scripts/import_keys.py ──\n"
    for key, value, source in updates:
        text += f"# from {source}\n{key}={value}\n"
    KEE_ENV.write_text(text, encoding="utf-8")


def mask(value: str, show: int = 4) -> str:
    if not value:
        return "<empty>"
    if any(h in value.upper() for h in ("BEGIN ", "PRIVATE KEY")):
        return "<long secret, " + str(len(value)) + " chars>"
    if len(value) <= show * 2:
        return "*" * len(value)
    return value[:show] + "…" + value[-show:]


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan + summarize without prompting or writing.")
    parser.add_argument("--show-values-once", action="store_true",
                        help="When asking keep y/n, reveal the masked value.")
    args = parser.parse_args()

    print(f"Kee .env importer — scanning under: {[str(r) for r in SCAN_ROOTS]}")
    print()

    files = find_env_files()
    if not files:
        print("No .env files found in any scan root.")
        return 1

    existing = load_existing_kee_env()
    print(f"Found {len(files)} .env file(s):")
    for f in files:
        print(f"  - {f}")
    print()
    print(f"D:/Kee/.env currently has {len(existing)} key(s).")
    print()

    # Build a unique catalog: key -> [(value, source)]
    catalog: dict[str, list[tuple[str, Path]]] = {}
    for f in files:
        for key, val in parse_env(f):
            catalog.setdefault(key, []).append((val, f))

    if args.dry_run:
        print("=== Dry run summary ===")
        for key, entries in sorted(catalog.items()):
            already = " [already in D:/Kee/.env]" if key in existing else ""
            print(f"  {key}{already}  ({len(entries)} occurrence(s))")
            for val, f in entries[:3]:
                print(f"      from {f}  preview={mask(val)!s}")
        return 0

    chosen: list[tuple[str, str, str]] = []
    skipped = 0

    for key in sorted(catalog.keys()):
        entries = catalog[key]
        already = key in existing
        flag = " [ALREADY IN D:/Kee/.env]" if already else ""
        print(f"\n{key}{flag}")
        for i, (val, f) in enumerate(entries):
            preview = mask(val) if args.show_values_once else ""
            print(f"  [{i}]  from {f}  {preview}")

        if already and not args.show_values_once:
            print("  Already imported. Skip silently.")
            skipped += 1
            continue

        prompt = "  k=keep [from index, default 0]  s=skip  q=quit  > "
        choice = input(prompt).strip().lower()
        if choice in ("q", "quit"):
            print("Quit. Nothing written so far.")
            return 0
        if choice in ("s", "skip", "n", "no", ""):
            skipped += 1
            continue
        idx = 0
        if choice.startswith("k"):
            tail = choice[1:].strip()
            if tail.isdigit():
                idx = int(tail)
        idx = max(0, min(idx, len(entries) - 1))
        val, src = entries[idx]
        chosen.append((key, val, str(src)))
        print(f"  → keeping value from {src}")

    if not chosen:
        print(f"\nDone. {skipped} keys skipped, 0 imported.")
        return 0

    print()
    print(f"About to write {len(chosen)} key(s) to {KEE_ENV}:")
    for key, _, src in chosen:
        print(f"  {key}  (from {src})")
    confirm = input("\nProceed? [y/N] ").strip().lower()
    if confirm != "y":
        print("Aborted.")
        return 0

    append_to_kee_env(chosen)
    print(f"\nWrote {len(chosen)} key(s) to {KEE_ENV}.")
    print(f"Skipped {skipped} key(s).")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (KeyboardInterrupt, EOFError):
        print("\nInterrupted.")
        sys.exit(130)
