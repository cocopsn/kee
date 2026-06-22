"""Runtime configuration for Kee.

Loads settings from environment variables (with `.env` support) and exposes
typed accessors. All paths default to project-local locations on Windows
development; production values come from the environment.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# Load .env from project root (D:\Kee\.env) if present.
PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_ROOT / ".env", override=True)


def _path(env_var: str, default: Path) -> Path:
    """Resolve a path env var to an absolute Path, falling back to default."""
    raw = os.environ.get(env_var, str(default))
    p = Path(raw).expanduser()
    if not p.is_absolute():
        p = (PROJECT_ROOT / p).resolve()
    return p


def _default_desktop() -> Path:
    """Best-effort Windows desktop path, including OneDrive localized setups."""
    home = Path.home()
    for rel in (
        Path("OneDrive") / "Escritorio",
        Path("OneDrive") / "Desktop",
        Path("Desktop"),
        Path("Escritorio"),
    ):
        candidate = home / rel
        if candidate.exists():
            return candidate
    return home / "Desktop"


@dataclass(frozen=True)
class Settings:
    # Ollama
    ollama_host: str = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
    # Primary local brain. Override via KEE_MODEL in .env for quick swaps.
    model: str = os.environ.get(
        "KEE_MODEL",
        # kee-uncensored:latest = the HauhauCS HF weights re-wrapped with
        # the official Qwen3 chat template (Apache 2.0) so Ollama's tool
        # parser can read the model's responses. Build it once with:
        #   ollama create kee-uncensored:latest \
        #     -f scripts/kee_uncensored.Modelfile
        # The HF model itself ships with an empty `TEMPLATE {{ .Prompt }}`
        # which breaks every tool-using turn — do not point KEE_MODEL at it
        # directly.
        "kee-uncensored:latest",
    )
    temperature: float = float(os.environ.get("KEE_TEMPERATURE", "0.7"))
    # 4096 was the original v2 roadmap §1.2 target, but the agent's
    # system prompt alone is ~5,870 tokens and the 65-tool schema is
    # another ~15,900 tokens — every chat turn exceeded the window and
    # came back empty. 8192 fits the system prompt with headroom; the
    # extra ~1GB of KV cache still fits on the 8GB RTX 5050 (model is
    # 6.25GB Q4_K_M → total ~7.5GB). For larger tool schemas, bump to
    # 16384 in .env (will require ~7.8GB total — tight but workable).
    num_ctx: int = int(os.environ.get("KEE_NUM_CTX", "8192"))

    # Auctorum worker
    auctorum_host: str = os.environ.get("AUCTORUM_HOST", "auctorum")
    auctorum_ollama: str = os.environ.get("AUCTORUM_OLLAMA", "http://auctorum:11434")
    chromadb_host: str = os.environ.get("CHROMADB_HOST", "http://auctorum:8000")
    require_auctorum: bool = os.environ.get("KEE_REQUIRE_AUCTORUM", "false").lower() == "true"

    # Paths (defaults are project-relative; absolute via env override)
    project_root: Path = PROJECT_ROOT
    data_dir: Path = _path("KEE_DATA_DIR", PROJECT_ROOT / "data")
    vault_dir: Path = _path("KEE_VAULT_DIR", PROJECT_ROOT / "vault")
    models_dir: Path = _path("KEE_MODELS_DIR", PROJECT_ROOT / "models")

    # Logging
    log_level: str = os.environ.get("KEE_LOG_LEVEL", "INFO").upper()

    # Agent loop
    max_iterations: int = int(os.environ.get("KEE_MAX_ITERATIONS", "15"))

    # KeeCode / OpenCode bridge
    code_agent: str = os.environ.get("KEE_CODE_AGENT", "keecode")
    code_agent_model: str = os.environ.get(
        "KEE_CODE_AGENT_MODEL",
        os.environ.get(
            "KEE_MODEL",
            "hf.co/HauhauCS/Qwen3.5-9B-Uncensored-HauhauCS-Aggressive:Q4_K_M",
        ),
    )
    code_agent_provider: str = os.environ.get("KEE_CODE_AGENT_PROVIDER", "ollama")
    opencode_command: str = os.environ.get("KEE_OPENCODE_COMMAND", "opencode")
    opencode_repo: Path = _path("KEE_OPENCODE_REPO", _default_desktop() / "opencode")
    keecode_data_dir: Path = _path("KEE_CODE_AGENT_DATA_DIR", PROJECT_ROOT / "data" / "keecode")

    @property
    def db_path(self) -> Path:
        return self.data_dir / "kee.db"

    @property
    def identity_path(self) -> Path:
        return self.vault_dir / "config" / "identity.md"

    @property
    def soul_path(self) -> Path:
        return self.vault_dir / "config" / "soul.md"

    @property
    def user_path(self) -> Path:
        return self.vault_dir / "config" / "user.md"

    @property
    def custom_tools_dir(self) -> Path:
        return self.vault_dir / "_kee" / "tools"

    def ensure_dirs(self) -> None:
        for p in (
            self.data_dir,
            self.vault_dir,
            self.models_dir,
            self.custom_tools_dir,
            self.keecode_data_dir,
        ):
            p.mkdir(parents=True, exist_ok=True)


settings = Settings()


def setup_logging(quiet_libs: bool = True) -> None:
    """Configure root logging.

    `quiet_libs=True` (default) suppresses chatty INFO logs from httpx/urllib3
    so they don't bleed into the REPL prompt while the user is typing. Errors
    and warnings still surface.

    Set `KEE_LOG_LEVEL=DEBUG` to see everything; set it to `WARNING` for an
    even quieter terminal.
    """
    import sys
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    ))
    root = logging.getLogger()
    # Replace any prior handlers (so re-entering setup_logging doesn't stack).
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(getattr(logging, settings.log_level, logging.INFO))

    if quiet_libs:
        for noisy in ("httpx", "httpcore", "urllib3", "watchdog"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
