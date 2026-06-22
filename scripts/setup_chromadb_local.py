"""Stand up a local ChromaDB server on this machine (Windows-friendly).

Two modes:
  1. **Embedded** (default) — uses ChromaDB's `chromadb.PersistentClient`
     directly inside the API process. No separate server, no port. Fast,
     simple, but doesn't share state with other Kee surfaces.
  2. **HTTP server** — runs `chroma run --path D:/Kee/data/chroma --port 8000`
     as its own process. Used when Auctorum is offline and we want
     vector search to still work locally.

This script:
  - pip-installs `chromadb` if missing
  - creates the `data/chroma/` storage dir
  - writes a launcher script (`scripts/run_chroma.bat`)
  - patches `.env` so `CHROMADB_HOST=http://127.0.0.1:8000`
  - writes a Task Scheduler XML for auto-start (optional)

Run once:
    python -m scripts.setup_chromadb_local
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

KEE_ROOT = Path(r"D:/Kee")
DATA_DIR = KEE_ROOT / "data" / "chroma"
SCRIPTS_DIR = KEE_ROOT / "scripts"
ENV_PATH = KEE_ROOT / ".env"


def ensure_chromadb_installed() -> bool:
    try:
        import chromadb  # noqa: F401
        print("✓ chromadb already installed")
        return True
    except ImportError:
        pass
    print("Installing chromadb (this is heavy — ~250 MB with deps)...")
    pip = KEE_ROOT / ".venv" / "Scripts" / "pip.exe"
    if not pip.exists():
        pip = sys.executable.replace("python.exe", "pip.exe")
    r = subprocess.run([str(pip), "install", "chromadb"], capture_output=True, text=True)
    if r.returncode != 0:
        print(f"✗ pip install failed:\n{r.stderr[-500:]}")
        return False
    print("✓ chromadb installed")
    return True


def write_launcher() -> Path:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    bat = SCRIPTS_DIR / "run_chroma.bat"
    bat.write_text(
        f"""@echo off
REM Local ChromaDB HTTP server for Kee.
REM Stores vectors under {DATA_DIR}.
REM Listens on 127.0.0.1:8000 (loopback only — never network-exposed).
cd /d {KEE_ROOT}
"{KEE_ROOT}\\.venv\\Scripts\\chroma.exe" run --path "{DATA_DIR}" --host 127.0.0.1 --port 8000
""",
        encoding="utf-8",
    )
    print(f"✓ wrote launcher → {bat}")
    return bat


def patch_env() -> None:
    if not ENV_PATH.exists():
        print(f"⚠ {ENV_PATH} doesn't exist; skipping env patch")
        return
    src = ENV_PATH.read_text(encoding="utf-8")
    new = re.sub(
        r"^CHROMADB_HOST=.*$",
        "CHROMADB_HOST=http://127.0.0.1:8000",
        src, flags=re.M,
    )
    if "CHROMADB_HOST=" not in new:
        new += "\nCHROMADB_HOST=http://127.0.0.1:8000\n"
    if new != src:
        ENV_PATH.write_text(new, encoding="utf-8")
        print(f"✓ patched .env → CHROMADB_HOST=http://127.0.0.1:8000")
    else:
        print("  .env already configured")


def write_autostart_xml() -> None:
    """Optional Task Scheduler XML for auto-start at login. Not auto-imported
    — Coco can `schtasks /Create /TN KeeChromaDB /XML <path>` if he wants."""
    xml = SCRIPTS_DIR / "chroma_autostart.xml"
    bat = SCRIPTS_DIR / "run_chroma.bat"
    xml.write_text(
        f"""<?xml version=\"1.0\" encoding=\"UTF-16\"?>
<Task version=\"1.4\" xmlns=\"http://schemas.microsoft.com/windows/2004/02/mit/task\">
  <RegistrationInfo>
    <Description>ChromaDB local server for Kee — vector store on D:/Kee/data/chroma</Description>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled></LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id=\"Author\">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <Hidden>true</Hidden>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <RestartOnFailure><Interval>PT1M</Interval><Count>9999</Count></RestartOnFailure>
  </Settings>
  <Actions Context=\"Author\">
    <Exec>
      <Command>cmd.exe</Command>
      <Arguments>/c \"{bat}\"</Arguments>
    </Exec>
  </Actions>
</Task>
""",
        encoding="utf-16",
    )
    print(f"✓ wrote Task Scheduler XML → {xml}")
    print(f"  install with:  schtasks /Create /TN KeeChromaDB /XML \"{xml}\" /F")


def main() -> int:
    print(f"ChromaDB local-server setup for {KEE_ROOT}")
    if not ensure_chromadb_installed():
        return 1
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    print(f"✓ storage dir: {DATA_DIR}")
    write_launcher()
    patch_env()
    write_autostart_xml()
    print("\nDone. To start ChromaDB now:")
    print(f"  {SCRIPTS_DIR}\\run_chroma.bat")
    print("Then re-launch Kee — the indexer will auto-detect ChromaDB on 127.0.0.1:8000.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
