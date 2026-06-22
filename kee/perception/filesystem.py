"""Vault file watcher.

Watches the Obsidian vault for `.md` changes and triggers re-indexing via
the `VaultIndexer`. Uses `watchdog` (cross-platform) and a debounce window
so that a single save event doesn't fire dozens of times.

Phase 1 wires the watcher in `main.py` behind a `--watch` flag. Phase 3 will
also produce live perception events ("vault file edited") for the agent.
"""

from __future__ import annotations

import asyncio
import logging
import threading
import time
from pathlib import Path

from watchdog.events import FileSystemEvent, FileSystemEventHandler
from watchdog.observers import Observer

from kee.config import settings
from kee.distributed.indexer import VaultIndexer

logger = logging.getLogger(__name__)


DEBOUNCE_S = 1.5


class _Debouncer(FileSystemEventHandler):
    """Collect events and emit one job per path after a quiet window."""

    def __init__(self, loop: asyncio.AbstractEventLoop, indexer: VaultIndexer) -> None:
        super().__init__()
        self.loop = loop
        self.indexer = indexer
        self._lock = threading.Lock()
        self._pending: dict[str, float] = {}
        self._timer: threading.Timer | None = None

    def _enqueue(self, path: str) -> None:
        with self._lock:
            self._pending[path] = time.monotonic() + DEBOUNCE_S
            if self._timer is None:
                self._timer = threading.Timer(DEBOUNCE_S + 0.1, self._flush)
                self._timer.daemon = True
                self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            now = time.monotonic()
            ready = [p for p, due in self._pending.items() if due <= now]
            for p in ready:
                self._pending.pop(p, None)
            still_pending = bool(self._pending)
            self._timer = None
            if still_pending:
                self._timer = threading.Timer(DEBOUNCE_S, self._flush)
                self._timer.daemon = True
                self._timer.start()

        for path in ready:
            asyncio.run_coroutine_threadsafe(
                self._index_one(path), self.loop,
            )

    async def _index_one(self, path: str) -> None:
        try:
            result = await self.indexer.index_file(path)
            if result["status"] == "indexed":
                logger.info("Re-indexed %s (%d chunks)", path, result["chunks"])
            elif result["status"] not in ("skipped", "skipped_offline"):
                logger.warning("Index result for %s: %s", path, result)
        except Exception:
            logger.exception("Indexer raised on %s", path)

    # FileSystemEventHandler hooks
    def on_modified(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if str(event.src_path).lower().endswith(".md"):
            self._enqueue(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        if str(event.src_path).lower().endswith(".md"):
            self._enqueue(event.src_path)

    def on_moved(self, event: FileSystemEvent) -> None:
        if event.is_directory:
            return
        dest = getattr(event, "dest_path", None) or event.src_path
        if str(dest).lower().endswith(".md"):
            self._enqueue(dest)


class VaultWatcher:
    def __init__(self, indexer: VaultIndexer | None = None) -> None:
        self.indexer = indexer or VaultIndexer()
        self.root = settings.vault_dir
        self.observer: Observer | None = None  # type: ignore[assignment]

    def start(self, loop: asyncio.AbstractEventLoop) -> None:
        if self.observer is not None:
            return
        handler = _Debouncer(loop, self.indexer)
        observer = Observer()
        observer.schedule(handler, str(self.root), recursive=True)
        observer.daemon = True
        observer.start()
        self.observer = observer
        logger.info("Vault watcher started on %s", self.root)

    def stop(self) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=2)
            self.observer = None
            logger.info("Vault watcher stopped")
