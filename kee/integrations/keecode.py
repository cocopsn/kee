"""KeeCode bridge for OpenCode.

KeeCode is Kee's clean-room coding-agent surface. It does not copy Claude
Code. It launches OpenCode with an inline Ollama provider config, shares a
small continuity file with Kee's normal chat, and can run either a TUI window
or a one-shot `opencode run` prompt.
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from kee.config import settings


DEFAULT_MODEL = "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M"


def _env(name: str, fallback: str) -> str:
    value = os.environ.get(name)
    return value if value is not None and value.strip() else fallback


def current_agent() -> str:
    return _env("KEE_CODE_AGENT", settings.code_agent)


def current_model() -> str:
    return _env("KEE_CODE_AGENT_MODEL", settings.code_agent_model or settings.model)


def current_opencode_command() -> str:
    return _env("KEE_OPENCODE_COMMAND", settings.opencode_command)


def current_opencode_repo() -> Path:
    raw = os.environ.get("KEE_OPENCODE_REPO")
    return Path(raw).expanduser().resolve() if raw else settings.opencode_repo


def current_data_dir() -> Path:
    raw = os.environ.get("KEE_CODE_AGENT_DATA_DIR")
    return Path(raw).expanduser().resolve() if raw else settings.keecode_data_dir


def _ollama_openai_base(ollama_host: str) -> str:
    base = (ollama_host or "http://localhost:11434").rstrip("/")
    if base.endswith("/v1"):
        return base
    return f"{base}/v1"


def build_opencode_config(
    *,
    model: str | None = None,
    ollama_host: str | None = None,
    provider_id: str = "ollama",
) -> dict[str, Any]:
    """Return an OpenCode config dict for Kee's local Ollama model."""
    model_id = model or current_model()
    host = ollama_host or settings.ollama_host
    return {
        "$schema": "https://opencode.ai/config.json",
        "model": f"{provider_id}/{model_id}",
        "provider": {
            provider_id: {
                "npm": "@ai-sdk/openai-compatible",
                "name": "Ollama (local)",
                "options": {
                    "baseURL": _ollama_openai_base(host),
                },
                "models": {
                    model_id: {
                        "name": f"Kee main - {model_id}",
                    },
                },
            },
        },
    }


def config_content(*, model: str | None = None) -> str:
    return json.dumps(build_opencode_config(model=model), indent=2, ensure_ascii=False)


def write_opencode_config(
    *,
    data_dir: Path | None = None,
    model: str | None = None,
) -> Path:
    root = data_dir or current_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "opencode.json"
    path.write_text(config_content(model=model), encoding="utf-8")
    return path


def write_context_bridge(
    *,
    notes: str = "",
    session_id: str = "dashboard",
    data_dir: Path | None = None,
    workdir: Path | None = None,
) -> Path:
    """Persist a small continuity file shared between Kee chat and KeeCode."""
    root = data_dir or current_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    path = root / "context.md"
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    body = (
        "# KeeCode Shared Context\n\n"
        f"- updated_at: {now}\n"
        f"- session_id: {session_id or 'dashboard'}\n"
        f"- model: {current_model()}\n"
        f"- normal_chat_surface: Kee\n"
        f"- code_surface: KeeCode/OpenCode\n"
        f"- workdir: {str(workdir) if workdir else str(settings.project_root)}\n\n"
        "## Continuity Notes\n\n"
        f"{notes.strip() or 'No extra notes yet.'}\n\n"
        "## Protocol\n\n"
        "- Treat this file as the bridge between Kee chat and KeeCode.\n"
        "- Keep changes focused on the active project directory.\n"
        "- Add durable session notes under the KeeCode data directory when useful.\n"
    )
    path.write_text(body, encoding="utf-8")
    return path


@dataclass(frozen=True)
class CommandChoice:
    args: list[str]
    source: str
    executable: str | None


def choose_opencode_command(command: str | None = None) -> CommandChoice | None:
    """Pick the best available OpenCode launcher without installing anything."""
    requested = (command or current_opencode_command()).strip()
    if requested:
        first = requested.split()[0]
        found = shutil.which(first)
        if found:
            tail = requested.split()[1:]
            return CommandChoice(args=[found, *tail], source="configured", executable=found)
        path = Path(requested).expanduser()
        if path.exists():
            return CommandChoice(args=[str(path.resolve())], source="configured_path", executable=str(path.resolve()))

    npx = shutil.which("npx")
    if npx:
        return CommandChoice(args=[npx, "-y", "opencode-ai@latest"], source="npx", executable=npx)

    return None


def status() -> dict[str, Any]:
    choice = choose_opencode_command()
    repo = current_opencode_repo()
    data_dir = current_data_dir()
    cfg_path = data_dir / "opencode.json"
    context_path = data_dir / "context.md"
    return {
        "ok": choice is not None,
        "agent": current_agent(),
        "model": current_model(),
        "opencode_repo": str(repo),
        "opencode_repo_exists": repo.exists(),
        "opencode_command": current_opencode_command(),
        "opencode_command_resolved": choice.executable if choice else None,
        "opencode_command_source": choice.source if choice else "missing",
        "npx_available": shutil.which("npx") is not None,
        "bun_available": shutil.which("bun") is not None,
        "config_path": str(cfg_path),
        "config_exists": cfg_path.exists(),
        "context_path": str(context_path),
        "context_exists": context_path.exists(),
        "data_dir": str(data_dir),
        "ollama_host": settings.ollama_host,
        "model_id": f"ollama/{current_model()}",
        "hint": None if choice else "Install OpenCode or Node/npx, or set KEE_OPENCODE_COMMAND.",
    }


def _ps_single_quoted(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def build_launcher_script(
    *,
    workdir: Path,
    prompt: str = "",
    data_dir: Path | None = None,
    model: str | None = None,
    command: str | None = None,
) -> Path:
    choice = choose_opencode_command(command)
    if choice is None:
        raise RuntimeError("OpenCode command not found. Install OpenCode or Node/npx.")

    root = data_dir or current_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    write_opencode_config(data_dir=root, model=model)
    context_path = write_context_bridge(
        notes=prompt,
        session_id="keecode",
        data_dir=root,
        workdir=workdir,
    )

    cfg = config_content(model=model)
    model_id = f"ollama/{model or current_model()}"
    args = [*choice.args, "--model", model_id]
    if prompt.strip():
        args += ["--prompt", f"Read {context_path} first. {prompt.strip()}"]
    args.append(str(workdir))

    arg_lines = "\n".join(f"  {_ps_single_quoted(a)}" for a in args)
    script = (
        "$ErrorActionPreference = \"Stop\"\n"
        f"Set-Location -LiteralPath {_ps_single_quoted(str(workdir))}\n"
        "$env:OPENCODE_CONFIG_CONTENT = @'\n"
        f"{cfg}\n"
        "'@\n"
        "$env:OPENCODE_DISABLE_AUTOUPDATE = \"true\"\n"
        "$env:OPENCODE_DISABLE_MODELS_FETCH = \"true\"\n"
        "$env:OLLAMA_HOST = \"http://127.0.0.1:11434\"\n"
        "Write-Host \"KeeCode / OpenCode\"\n"
        f"Write-Host \"Model: {model_id}\"\n"
        f"Write-Host \"Context: {context_path}\"\n"
        "Write-Host \"\"\n"
        "$opencodeArgs = @(\n"
        f"{arg_lines}\n"
        ")\n"
        "& $opencodeArgs[0] @($opencodeArgs[1..($opencodeArgs.Count - 1)])\n"
        "if ($LASTEXITCODE -ne 0) {\n"
        "  Write-Host \"\"\n"
        "  Write-Host \"OpenCode exited with code $LASTEXITCODE.\"\n"
        "}\n"
    )
    script_path = root / "launch-keecode.ps1"
    script_path.write_text(script, encoding="utf-8")
    return script_path


def launch_terminal(
    *,
    workdir: str | None = None,
    prompt: str = "",
    model: str | None = None,
) -> dict[str, Any]:
    target = Path(workdir).expanduser().resolve() if workdir else settings.project_root
    if not target.exists() or not target.is_dir():
        return {"ok": False, "error": f"workdir not found: {target}"}
    try:
        script = build_launcher_script(workdir=target, prompt=prompt, model=model)
    except Exception as e:
        return {"ok": False, "error": str(e), **status()}

    creationflags = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
    proc = subprocess.Popen(
        [
            "powershell.exe",
            "-NoExit",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script),
        ],
        cwd=str(target),
        creationflags=creationflags,
    )
    return {
        "ok": True,
        "pid": proc.pid,
        "script": str(script),
        "workdir": str(target),
        "model": f"ollama/{model or current_model()}",
        "context_path": str(current_data_dir() / "context.md"),
    }


async def run_prompt(
    *,
    prompt: str,
    workdir: str | None = None,
    model: str | None = None,
    timeout_s: int = 600,
) -> dict[str, Any]:
    choice = choose_opencode_command()
    if choice is None:
        return {"ok": False, "error": "OpenCode command not found.", **status()}
    target = Path(workdir).expanduser().resolve() if workdir else settings.project_root
    if not target.exists() or not target.is_dir():
        return {"ok": False, "error": f"workdir not found: {target}"}

    root = current_data_dir()
    root.mkdir(parents=True, exist_ok=True)
    write_opencode_config(data_dir=root, model=model)
    context_path = write_context_bridge(
        notes=prompt,
        session_id="keecode-run",
        data_dir=root,
        workdir=target,
    )

    model_id = f"ollama/{model or current_model()}"
    cmd = [
        *choice.args,
        "run",
        "--model",
        model_id,
        "--dir",
        str(target),
        "--format",
        "json",
        f"Read {context_path} first. {prompt}",
    ]
    env = os.environ.copy()
    env["OPENCODE_CONFIG_CONTENT"] = config_content(model=model)
    env["OPENCODE_DISABLE_AUTOUPDATE"] = "true"
    env["OPENCODE_DISABLE_MODELS_FETCH"] = "true"

    started = time.monotonic()
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=str(target),
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env,
    )
    try:
        stdout_b, stderr_b = await asyncio.wait_for(proc.communicate(), timeout=timeout_s)
    except asyncio.TimeoutError:
        proc.kill()
        await proc.wait()
        return {
            "ok": False,
            "status": "timeout",
            "elapsed_s": round(time.monotonic() - started, 1),
            "workdir": str(target),
        }

    stdout = stdout_b.decode("utf-8", errors="replace")
    stderr = stderr_b.decode("utf-8", errors="replace")
    return {
        "ok": proc.returncode == 0,
        "status": "ok" if proc.returncode == 0 else "nonzero_exit",
        "exit_code": proc.returncode,
        "elapsed_s": round(time.monotonic() - started, 1),
        "workdir": str(target),
        "model": model_id,
        "result": stdout[-6000:],
        "stderr": stderr[-1200:],
    }
