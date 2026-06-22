"""Backup story — daily snapshots of the things that hurt to lose.

Sleep Cycle Phase 14 calls `run_backups()` every night. Targets:

  1. **kee.db** (SQLite with all of audit/plans/focus/learnings/memory).
     Uses sqlite3.Connection.backup() over a read-only second connection
     so we don't fight the agent's writes via the shared connection lock.
     This is the WAL-safe path; `shutil.copy(kee.db)` would silently miss
     the -wal and -shm files.
  2. **vault/** as tar.gz, excluding `.obsidian/workspace*`,
     `_kee/tools/__pycache__`, and any `.git` if you accidentally
     git-init'd inside.
  3. **Auctorum's `/var/lib/chromadb`** — opt-in via env, pulled over
     SSH to the local backup dir.

Output:
    data/backups/<YYYY-MM-DD>/
        kee.db                (SQLite snapshot, queryable as-is)
        vault.tar.gz          (tarball)
        chroma.tar.gz         (if AUCTORUM_HOST reachable)
        manifest.json         (paths, sizes, sha256 prefixes, elapsed_s)

Rotation: keeps the last `KEE_BACKUP_ROTATE_DAYS` days (default 30).
Older snapshot dirs get rmtree'd at the end of each run.

Manual:
    python -m kee.main backup-now

Sleep Cycle:
    Phase 14, opt-out via `KEE_SLEEP_BACKUP=0`.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import sqlite3
import subprocess
import tarfile
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from kee.config import settings

logger = logging.getLogger(__name__)


def _backup_root() -> Path:
    base = Path(os.environ.get("KEE_BACKUP_DIR",
                               str(settings.data_dir / "backups")))
    base.mkdir(parents=True, exist_ok=True)
    return base


def _today_dir() -> Path:
    d = _backup_root() / date.today().isoformat()
    d.mkdir(parents=True, exist_ok=True)
    return d


def _sha256_prefix(path: Path, n_bytes: int = 64 * 1024) -> str | None:
    """Cheap integrity hint — first ~64KB hashed. Avoids hashing GB."""
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            h.update(f.read(n_bytes))
        return h.hexdigest()[:16]
    except OSError:
        return None


def backup_sqlite(dest_dir: Path) -> dict[str, Any]:
    """WAL-safe snapshot of kee.db via sqlite3.Connection.backup().

    Opens the source as a SECOND read-only connection so we don't block
    the shared connection's lock during the copy.
    """
    src = settings.db_path if hasattr(settings, "db_path") else (
        settings.data_dir / "kee.db"
    )
    if not src.exists():
        return {"ok": False, "reason": f"no db at {src}"}
    out_path = dest_dir / "kee.db"
    t0 = time.time()
    try:
        # `mode=ro` tells SQLite to open read-only without taking a write lock
        src_conn = sqlite3.connect(
            f"file:{src}?mode=ro", uri=True, timeout=10,
        )
        try:
            dst_conn = sqlite3.connect(str(out_path))
            try:
                src_conn.backup(dst_conn, pages=256)
            finally:
                dst_conn.close()
        finally:
            src_conn.close()
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}
    elapsed_s = round(time.time() - t0, 2)
    size = out_path.stat().st_size
    return {
        "ok": True,
        "path": str(out_path),
        "size_mb": round(size / (1024 * 1024), 2),
        "sha256_head": _sha256_prefix(out_path),
        "elapsed_s": elapsed_s,
    }


def backup_vault(dest_dir: Path) -> dict[str, Any]:
    """tar.gz the vault, excluding noise."""
    src = settings.vault_dir
    if not src.exists():
        return {"ok": False, "reason": f"no vault at {src}"}
    out_path = dest_dir / "vault.tar.gz"
    excludes = (
        ".git", "__pycache__",
        "workspace.json", "workspace-mobile.json", "workspace.json.bak",
        ".trash", ".obsidian/workspace.json",
    )

    def _filter(tarinfo):
        name = Path(tarinfo.name).name
        for pat in excludes:
            if pat in tarinfo.name or name == pat:
                return None
        return tarinfo

    t0 = time.time()
    try:
        with tarfile.open(out_path, "w:gz") as tar:
            tar.add(src, arcname="vault", filter=_filter)
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}
    elapsed_s = round(time.time() - t0, 2)
    size = out_path.stat().st_size
    return {
        "ok": True,
        "path": str(out_path),
        "size_mb": round(size / (1024 * 1024), 2),
        "sha256_head": _sha256_prefix(out_path),
        "elapsed_s": elapsed_s,
    }


def backup_worker_chroma(dest_dir: Path,
                          ssh_host: str | None = None) -> dict[str, Any]:
    """Pull /var/lib/chromadb from Auctorum via SSH stream → tar.gz.

    Opt-in: requires `KEE_BACKUP_WORKER_CHROMA=1`. Default OFF because
    SSH can hang on flaky networks.
    """
    if os.environ.get("KEE_BACKUP_WORKER_CHROMA", "0") not in (
        "1", "true", "yes",
    ):
        return {"ok": False, "reason": "opt-in: set KEE_BACKUP_WORKER_CHROMA=1"}
    host = ssh_host or os.environ.get("AUCTORUM_SSH_HOST",
                                      os.environ.get("AUCTORUM_HOST", "auctorum"))
    user = os.environ.get("AUCTORUM_SSH_USER", "cocopsn")
    out_path = dest_dir / "chroma.tar.gz"
    t0 = time.time()
    cmd = [
        "ssh", "-o", "ConnectTimeout=8", "-o", "BatchMode=yes",
        f"{user}@{host}",
        "tar -czf - /var/lib/chromadb 2>/dev/null",
    ]
    try:
        with out_path.open("wb") as fout:
            r = subprocess.run(
                cmd, stdout=fout, stderr=subprocess.PIPE,
                timeout=300, check=False,
            )
        if r.returncode != 0:
            try:
                out_path.unlink()
            except OSError:
                pass
            return {"ok": False,
                    "reason": f"ssh tar exit {r.returncode}: "
                              f"{r.stderr.decode('utf-8', 'replace')[:160]}"}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:160]}
    elapsed_s = round(time.time() - t0, 2)
    size = out_path.stat().st_size
    return {
        "ok": True,
        "path": str(out_path),
        "size_mb": round(size / (1024 * 1024), 2),
        "sha256_head": _sha256_prefix(out_path),
        "elapsed_s": elapsed_s,
        "host": host,
    }


def rotate(rotate_days: int | None = None) -> list[str]:
    """Delete backup dirs older than `rotate_days` (default 30)."""
    days = int(rotate_days if rotate_days is not None else
               os.environ.get("KEE_BACKUP_ROTATE_DAYS", "30"))
    if days <= 0:
        return []
    cutoff = date.today() - timedelta(days=days)
    removed: list[str] = []
    base = _backup_root()
    for child in base.iterdir():
        if not child.is_dir():
            continue
        try:
            d = date.fromisoformat(child.name)
        except ValueError:
            continue
        if d < cutoff:
            try:
                shutil.rmtree(child)
                removed.append(child.name)
            except OSError as e:
                logger.warning("rotate: could not remove %s: %s", child, e)
    return removed


def run_backups(rotate_days: int | None = None) -> dict[str, Any]:
    """Main entry — kick all targets and write a manifest. Idempotent
    within the same day (overwrites that day's snapshot)."""
    out_dir = _today_dir()
    t0 = time.time()
    results: dict[str, Any] = {
        "date": date.today().isoformat(),
        "started_at": datetime.now().isoformat(timespec="seconds"),
        "out_dir": str(out_dir),
    }
    results["sqlite"] = backup_sqlite(out_dir)
    results["vault"] = backup_vault(out_dir)
    results["worker_chroma"] = backup_worker_chroma(out_dir)
    removed = rotate(rotate_days)
    results["rotated_removed"] = removed
    results["elapsed_s"] = round(time.time() - t0, 2)

    # Write the manifest last so a half-finished run is obvious.
    try:
        (out_dir / "manifest.json").write_text(
            json.dumps(results, indent=2),
            encoding="utf-8",
        )
    except OSError as e:
        results["manifest_error"] = str(e)
    return results
