"""Register Kee to start automatically.

Windows: registers a Task Scheduler entry that runs ``python -m kee.main all``
hidden, at user logon, with auto-restart on failure.

Usage:
    python -m kee.main install-autostart      # adds the task
    python -m kee.main uninstall-autostart    # removes it

Linux (future): a systemd --user unit goes here when Coco migrates to Ubuntu.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from kee.config import settings

TASK_NAME = "Kee"


# ── Windows ───────────────────────────────────────────────────────────────
def _wsl_check() -> bool:
    return sys.platform == "win32"


def _build_xml() -> str:
    """Build the Task Scheduler XML.

    We avoid `schtasks /Create /TR` because the cmd-line form has trouble
    quoting the `python -m kee.main all` payload + working dir + hidden
    flag. XML import gives full fidelity.
    """
    python_exe = sys.executable
    project_root = str(settings.project_root)
    # PowerShell wraps python so the console window stays hidden.
    # `-WindowStyle Hidden` + `-NoLogo` keeps the supervisor invisible at
    # logon; the tray icon (or dashboard) is the user-visible surface.
    args = (
        f'-NoProfile -WindowStyle Hidden -Command '
        f'"& \'{python_exe}\' -m kee.main all"'
    )
    # Task Scheduler XML is XML — `&` and `"` MUST be escaped or schtasks
    # rejects with "task XML is malformed" at the Arguments line.
    from xml.sax.saxutils import escape as _xml_escape
    args = _xml_escape(args, {'"': "&quot;", "'": "&apos;"})
    return f"""<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.4" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo>
    <Description>Kee — sovereign autonomous agent (supervisor: api + telegram + voice + notif-bridge + heartbeat).</Description>
    <Author>kee</Author>
  </RegistrationInfo>
  <Triggers>
    <LogonTrigger>
      <Enabled>true</Enabled>
    </LogonTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author">
      <LogonType>InteractiveToken</LogonType>
      <RunLevel>LeastPrivilege</RunLevel>
    </Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <AllowHardTerminate>true</AllowHardTerminate>
    <StartWhenAvailable>true</StartWhenAvailable>
    <RunOnlyIfNetworkAvailable>false</RunOnlyIfNetworkAvailable>
    <IdleSettings>
      <StopOnIdleEnd>false</StopOnIdleEnd>
      <RestartOnIdle>false</RestartOnIdle>
    </IdleSettings>
    <AllowStartOnDemand>true</AllowStartOnDemand>
    <Enabled>true</Enabled>
    <Hidden>false</Hidden>
    <RunOnlyIfIdle>false</RunOnlyIfIdle>
    <WakeToRun>false</WakeToRun>
    <ExecutionTimeLimit>PT0S</ExecutionTimeLimit>
    <Priority>7</Priority>
    <RestartOnFailure>
      <Interval>PT1M</Interval>
      <Count>9999</Count>
    </RestartOnFailure>
  </Settings>
  <Actions Context="Author">
    <Exec>
      <Command>powershell.exe</Command>
      <Arguments>{args}</Arguments>
      <WorkingDirectory>{project_root}</WorkingDirectory>
    </Exec>
  </Actions>
</Task>
"""


def _startup_dir() -> Path:
    """Per-user Startup folder. No admin required to write here."""
    appdata = os.environ.get("APPDATA") or str(Path.home() / "AppData/Roaming")
    return (Path(appdata) / "Microsoft" / "Windows" / "Start Menu"
            / "Programs" / "Startup")


def _install_via_startup_folder() -> int:
    """Drop a .vbs in the Startup folder. VBScript is the cleanest way to
    spawn pythonw.exe with no console window flicker. Works without admin
    because the Startup folder is per-user (HKCU scope)."""
    python_exe = Path(sys.executable)
    # Prefer pythonw.exe (no console). It sits next to python.exe.
    pyw = python_exe.with_name("pythonw.exe")
    if not pyw.exists():
        pyw = python_exe  # fallback — will flash a console at logon

    project_root = settings.project_root
    vbs_body = (
        f'Set WshShell = CreateObject("WScript.Shell")\r\n'
        f'WshShell.CurrentDirectory = "{project_root}"\r\n'
        f'WshShell.Run """{pyw}"" -m kee.main all", 0, False\r\n'
    )
    startup = _startup_dir()
    startup.mkdir(parents=True, exist_ok=True)
    target = startup / "Kee.vbs"
    target.write_text(vbs_body, encoding="utf-8")
    print(f"Installed startup launcher: {target}")
    print(f"  -> spawns: {pyw} -m kee.main all")
    print(f"  -> cwd:    {project_root}")
    print(f"\nKee will launch at next user logon.")
    print(f"To start now: wscript \"{target}\"")
    return 0


def install_windows_autostart() -> int:
    if not _wsl_check():
        print("install-autostart only supports Windows for now. "
              "On Linux, write a systemd --user unit invoking `python -m kee.main all`.")
        return 1

    # Strategy A: Startup folder VBS (no admin, always works for current user).
    # We prefer this over Task Scheduler because Task Scheduler /Create
    # requires elevated privileges on this machine, and we want
    # `install-autostart` to succeed from a normal terminal.
    if shutil.which("schtasks") is None:
        return _install_via_startup_folder()

    xml_path = settings.data_dir / "kee_autostart.xml"
    xml_path.parent.mkdir(parents=True, exist_ok=True)
    xml_path.write_text(_build_xml(), encoding="utf-16")

    cmd = ["schtasks", "/Create", "/TN", TASK_NAME, "/XML", str(xml_path), "/F"]
    print("Trying Windows scheduled task:", " ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(res.stdout.strip() or "ok")
        print(f"\nTask '{TASK_NAME}' installed. Kee will launch at next user logon.")
        print(f"To start it now without rebooting:  schtasks /Run /TN {TASK_NAME}")
        return 0

    # Fallback: schtasks needs admin → Startup folder works without it.
    print("schtasks failed (likely needs elevation):")
    print((res.stdout + res.stderr).strip()[:300])
    print("\nFalling back to per-user Startup folder (no admin required)...")
    return _install_via_startup_folder()


def uninstall_windows_autostart() -> int:
    if not _wsl_check():
        print("uninstall-autostart only supports Windows for now.")
        return 1
    removed_any = False
    # Remove Startup folder VBS if present
    startup_vbs = _startup_dir() / "Kee.vbs"
    if startup_vbs.exists():
        startup_vbs.unlink()
        print(f"Removed startup launcher: {startup_vbs}")
        removed_any = True
    # Remove Task Scheduler entry if present
    cmd = ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode == 0:
        print(res.stdout.strip() or f"Task '{TASK_NAME}' removed from Task Scheduler.")
        removed_any = True
    if not removed_any:
        print(f"Nothing to uninstall — no Kee.vbs in Startup folder, no '{TASK_NAME}' task.")
        return 1
    return 0
