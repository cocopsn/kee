"""Piper voice catalog & installer.

Downloads voices from the official ``rhasspy/piper-voices`` HuggingFace repo.
Each voice is two files:

    https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/<lang>/<locale>/<name>/<quality>/<locale>-<name>-<quality>.onnx
    same path with .onnx.json suffix

Both go to ``models/piper/<locale>-<name>-<quality>.{onnx,onnx.json}`` so
the rest of the codebase can find them by stem name.
"""

from __future__ import annotations

import logging
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from kee.config import settings

logger = logging.getLogger(__name__)

HF_BASE = "https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0"


@dataclass(frozen=True)
class CatalogVoice:
    locale: str          # e.g. es_MX
    name: str            # e.g. claude
    quality: str         # x_low | low | medium | high
    description: str = ""
    approx_mb: float = 0

    @property
    def stem(self) -> str:
        return f"{self.locale}-{self.name}-{self.quality}"

    @property
    def lang(self) -> str:
        return self.locale.split("_")[0]

    def url_onnx(self) -> str:
        return f"{HF_BASE}/{self.lang}/{self.locale}/{self.name}/{self.quality}/{self.stem}.onnx"

    def url_meta(self) -> str:
        return f"{self.url_onnx()}.json"


# Curated, lean catalog. Spanish-first because Coco speaks Spanish; a
# couple of English options for code-mixed contexts. Sizes are approximate.
CATALOG: list[CatalogVoice] = [
    # ── Spanish (Mexico) ─────────────────────────────────────────────────
    CatalogVoice("es_MX", "claude",  "high",   "Latam / clear / default", 60.0),
    CatalogVoice("es_MX", "ald",     "medium", "Latam / warm / faster",   28.0),
    # ── Spanish (Spain) ──────────────────────────────────────────────────
    CatalogVoice("es_ES", "davefx",  "medium", "Spain / male / neutral",  28.0),
    CatalogVoice("es_ES", "sharvard","medium", "Spain / female / smooth", 28.0),
    CatalogVoice("es_ES", "carlfm",  "x_low",  "Spain / tiny / mobile",   13.0),
    # ── English (US/UK), useful for code-mixed contexts ─────────────────
    CatalogVoice("en_US", "amy",     "medium", "US / female / standard",  28.0),
    CatalogVoice("en_US", "ryan",    "high",   "US / male / dramatic",    60.0),
    CatalogVoice("en_US", "lessac",  "medium", "US / female / clear",     28.0),
    CatalogVoice("en_GB", "alan",    "medium", "UK / male / RP",          28.0),
]


def find(stem: str) -> Optional[CatalogVoice]:
    for v in CATALOG:
        if v.stem == stem:
            return v
    return None


def voice_dir() -> Path:
    p = settings.models_dir / "piper"
    p.mkdir(parents=True, exist_ok=True)
    return p


def is_installed(stem: str) -> bool:
    return (voice_dir() / f"{stem}.onnx").exists()


def _fetch(url: str, dest: Path) -> int:
    """Stream-download a single file. Returns bytes written."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    written = 0
    req = urllib.request.Request(url, headers={"User-Agent": "kee-piper-installer/1.0"})
    with urllib.request.urlopen(req, timeout=60) as resp, tmp.open("wb") as f:
        while True:
            chunk = resp.read(64 * 1024)
            if not chunk:
                break
            f.write(chunk)
            written += len(chunk)
    tmp.replace(dest)
    return written


def install(stem: str) -> dict:
    """Download voice + metadata. Idempotent."""
    voice = find(stem)
    if voice is None:
        return {"ok": False, "stem": stem, "error": f"unknown voice '{stem}'"}
    base = voice_dir()
    onnx_path = base / f"{stem}.onnx"
    meta_path = base / f"{stem}.onnx.json"
    if onnx_path.exists() and meta_path.exists():
        return {
            "ok": True, "stem": stem, "already_installed": True,
            "size_mb": round(onnx_path.stat().st_size / (1024 * 1024), 2),
        }
    try:
        size = _fetch(voice.url_onnx(), onnx_path)
        _fetch(voice.url_meta(), meta_path)
        return {
            "ok": True, "stem": stem, "already_installed": False,
            "size_mb": round(size / (1024 * 1024), 2),
        }
    except Exception as e:
        # Clean up partial file so a retry starts fresh
        for p in (onnx_path, meta_path):
            try:
                if p.with_suffix(p.suffix + ".part").exists():
                    p.with_suffix(p.suffix + ".part").unlink()
            except Exception:
                pass
        logger.warning("piper install failed for %s: %s", stem, e)
        return {"ok": False, "stem": stem, "error": str(e)}


def install_many(stems: list[str]) -> list[dict]:
    return [install(s) for s in stems]


def remove(stem: str) -> dict:
    base = voice_dir()
    removed = 0
    for suffix in (".onnx", ".onnx.json"):
        p = base / f"{stem}{suffix}"
        if p.exists():
            try:
                p.unlink()
                removed += 1
            except Exception as e:
                return {"ok": False, "stem": stem, "error": str(e)}
    return {"ok": True, "stem": stem, "removed_files": removed}
