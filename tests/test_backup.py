"""Backup module — $0, no LLM, no worker.

Verifies:
  - sqlite hot copy via .backup() produces a queryable file
  - vault tar.gz contains the vault tree
  - rotation prunes only old dirs
  - manifest.json written even when worker_chroma is opt-out
  - WAL-safe: source connection stays usable after backup

Run::

    .venv\\Scripts\\python.exe tests/test_backup.py
"""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path


def _isolated_backup_dir() -> str:
    """Spin a temp dir + point KEE_BACKUP_DIR there for one test."""
    td = tempfile.mkdtemp(prefix="keebk_")
    os.environ["KEE_BACKUP_DIR"] = td
    return td


def _cleanup(td: str) -> None:
    shutil.rmtree(td, ignore_errors=True)
    os.environ.pop("KEE_BACKUP_DIR", None)


def test_sqlite_snapshot_is_queryable() -> int:
    from kee.cognition.backup import backup_sqlite, _today_dir
    td = _isolated_backup_dir()
    try:
        out = backup_sqlite(_today_dir())
        if not out.get("ok"):
            print(f"  [FAIL] {out}")
            return 1
        # Open the snapshot — must answer SELECT name FROM sqlite_master
        conn = sqlite3.connect(out["path"])
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        conn.close()
        if len(rows) >= 5:
            print(f"  [ok] snapshot has {len(rows)} tables, "
                  f"size={out['size_mb']}MB, elapsed={out['elapsed_s']}s")
            return 0
        print(f"  [FAIL] snapshot has only {len(rows)} tables")
        return 1
    finally:
        _cleanup(td)


def test_vault_tarball_contains_files() -> int:
    from kee.cognition.backup import backup_vault, _today_dir
    import tarfile
    td = _isolated_backup_dir()
    try:
        out = backup_vault(_today_dir())
        if not out.get("ok"):
            print(f"  [FAIL] {out}")
            return 1
        with tarfile.open(out["path"], "r:gz") as tar:
            names = tar.getnames()
        if any("vault/" in n for n in names) and len(names) >= 3:
            print(f"  [ok] vault tarball has {len(names)} entries, "
                  f"{out['size_mb']}MB")
            return 0
        print(f"  [FAIL] tarball entries: {names[:5]}")
        return 1
    finally:
        _cleanup(td)


def test_worker_chroma_opt_out_default() -> int:
    from kee.cognition.backup import backup_worker_chroma, _today_dir
    td = _isolated_backup_dir()
    try:
        # Ensure flag is unset
        os.environ.pop("KEE_BACKUP_WORKER_CHROMA", None)
        out = backup_worker_chroma(_today_dir())
        if not out.get("ok") and "opt-in" in (out.get("reason") or ""):
            print(f"  [ok] worker_chroma opt-out by default")
            return 0
        print(f"  [FAIL] {out}")
        return 1
    finally:
        _cleanup(td)


def test_manifest_written() -> int:
    from kee.cognition.backup import run_backups
    td = _isolated_backup_dir()
    try:
        res = run_backups(rotate_days=999)
        manifest = Path(res["out_dir"]) / "manifest.json"
        if manifest.exists():
            data = json.loads(manifest.read_text(encoding="utf-8"))
            if "sqlite" in data and "vault" in data:
                print(f"  [ok] manifest.json written with {len(data)} keys")
                return 0
        print(f"  [FAIL] {res}")
        return 1
    finally:
        _cleanup(td)


def test_rotation_prunes_old() -> int:
    from kee.cognition.backup import rotate
    td = _isolated_backup_dir()
    try:
        base = Path(td)
        # Seed: today + 5 days ago + 100 days ago
        today = date.today().isoformat()
        old = (date.today() - timedelta(days=100)).isoformat()
        recent = (date.today() - timedelta(days=5)).isoformat()
        for name in (today, recent, old):
            (base / name).mkdir(parents=True, exist_ok=True)
            (base / name / "manifest.json").write_text("{}",
                                                        encoding="utf-8")
        removed = rotate(rotate_days=30)
        survivors = sorted(p.name for p in base.iterdir() if p.is_dir())
        if (old in removed and old not in survivors
                and today in survivors and recent in survivors):
            print(f"  [ok] rotation removed {old}, kept {today} + {recent}")
            return 0
        print(f"  [FAIL] removed={removed}, survivors={survivors}")
        return 1
    finally:
        _cleanup(td)


def test_sqlite_source_still_usable_after_backup() -> int:
    """WAL-safe contract: snapshotting must NOT block subsequent writes
    on the shared connection."""
    from kee.cognition.backup import backup_sqlite, _today_dir
    from kee.core import db
    td = _isolated_backup_dir()
    try:
        out = backup_sqlite(_today_dir())
        if not out.get("ok"):
            print(f"  [FAIL] backup itself failed: {out}")
            return 1
        # Try a write through the shared connection
        with db.cursor() as cur:
            cur.execute(
                "INSERT INTO audit_log (action, tool_name, success) "
                "VALUES (?, ?, ?)",
                ("backup_smoke_test", "test_backup", 1),
            )
            inserted = cur.lastrowid
        # Verify + cleanup
        con = db.get_connection()
        row = con.execute(
            "SELECT action FROM audit_log WHERE id = ?",
            (inserted,),
        ).fetchone()
        with db.cursor() as cur:
            cur.execute("DELETE FROM audit_log WHERE id = ?", (inserted,))
        if row and row[0] == "backup_smoke_test":
            print("  [ok] source DB still writable after backup snapshot")
            return 0
        print(f"  [FAIL] insert disappeared: {row}")
        return 1
    finally:
        _cleanup(td)


if __name__ == "__main__":
    print("=== backup module ===")
    fails = 0
    fails += test_sqlite_snapshot_is_queryable()
    fails += test_vault_tarball_contains_files()
    fails += test_worker_chroma_opt_out_default()
    fails += test_manifest_written()
    fails += test_rotation_prunes_old()
    fails += test_sqlite_source_still_usable_after_backup()
    print()
    print(f"Done. failures={fails}")
    sys.exit(0 if fails == 0 else 1)
