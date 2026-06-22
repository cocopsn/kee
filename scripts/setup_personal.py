"""Create the personal vault files from templates.

After cloning the public Kee repo, run this once:

    python scripts/setup_personal.py

It copies `vault/config/*.template.md` → `vault/config/*.md` (only when
the destination is missing) and seeds an empty `vault/projects/` and
`vault/_kee/` directory tree. Open the resulting files in Obsidian or
your editor and fill them in. They're gitignored so your edits never
land in the public repo.
"""

from __future__ import annotations

import shutil
from pathlib import Path


_HERE = Path(__file__).resolve().parent.parent
_VAULT = _HERE / "vault"
_CONFIG = _VAULT / "config"


_TEMPLATES = [
    "user.md",
    "identity.md",
    "soul.md",
    "goals.md",
]

_SUBDIRS = [
    _VAULT / "projects",
    _VAULT / "notes",
    _VAULT / "people",
    _VAULT / "knowledge",
    _VAULT / "imports",
    _VAULT / "_kee" / "daily",
    _VAULT / "_kee" / "identity_proposals",
    _VAULT / "_kee" / "tool_rewrites",
    _VAULT / "_kee" / "code_proposals",
    _VAULT / "_kee" / "decisions",
    _VAULT / "_kee" / "learnings",
    _VAULT / "_kee" / "tools",
]


def main() -> int:
    if not _CONFIG.exists():
        _CONFIG.mkdir(parents=True)
        print(f"Created {_CONFIG}")

    copied = 0
    skipped = 0
    for name in _TEMPLATES:
        tpl = _CONFIG / f"{name}.template"
        dst = _CONFIG / name
        if not tpl.exists():
            print(f"  ! template missing: {tpl}")
            continue
        if dst.exists():
            print(f"  - skip (exists): {dst}")
            skipped += 1
            continue
        shutil.copy(tpl, dst)
        print(f"  + {dst}")
        copied += 1

    for sub in _SUBDIRS:
        sub.mkdir(parents=True, exist_ok=True)
    print(f"  + {len(_SUBDIRS)} vault subdirs ensured")

    print()
    print(f"Done. Copied {copied} file(s), skipped {skipped} existing.")
    print()
    print("Next steps:")
    print(f"  1. Edit the files in {_CONFIG} to describe yourself + Kee's voice.")
    print(f"  2. (Optional) Run `python scripts/import_project_docs.py` to "
          f"scrape your project READMEs into vault/projects/.")
    print(f"  3. (Optional) Run `python scripts/setup_obsidian.py` to "
          f"open the vault in Obsidian.")
    print(f"  4. Run `python -m kee.main check` to verify.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
