"""
watcher.py — Watchdog File Monitor + Debounce

Monitors a git repository directory for file modifications.
Debounces events at 750 ms per file and saves Base64-encoded diffs to SQLite.
In ia-on mode, immediately dispatches async MAP analysis via AIEngine.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import subprocess
import threading
import time
from pathlib import Path
from typing import Callable

from watchdog.events import FileModifiedEvent, FileSystemEventHandler
from watchdog.observers import Observer

from .database import DatabaseManager
from .filters import should_process

logger = logging.getLogger("supergit.watcher")

DEBOUNCE_SECONDS = 0.750
PID_FILE = Path.home() / ".supergit" / "watcher.pid"
CONFIG_FILE = Path.home() / ".supergit" / "config"


def _get_git_diff(filepath: str, repo_root: str) -> str | None:
    """
    Run `git diff` for the file and return the raw diff text.
    Returns None if the file is untracked or the command fails.
    Uses list-form args (no shell=True) to prevent injection.
    """
    try:
        result = subprocess.run(
            ["git", "diff", "--", filepath],
            capture_output=True,
            text=True,
            cwd=repo_root,
            timeout=10,
        )
        diff = result.stdout.strip()
        if not diff:
            # File might be untracked — produce a pseudo-diff via git diff --no-index
            result2 = subprocess.run(
                ["git", "diff", "--no-index", "/dev/null", filepath],
                capture_output=True,
                text=True,
                cwd=repo_root,
                timeout=10,
            )
            diff = result2.stdout.strip()
        return diff or None
    except (subprocess.TimeoutExpired, FileNotFoundError, OSError) as exc:
        logger.warning("git diff failed for %s: %s", filepath, exc)
        return None


def _encode_diff(diff_text: str) -> str:
    """Base64-encode diff text for safe storage."""
    return base64.b64encode(diff_text.encode("utf-8", errors="replace")).decode("ascii")


class _DebounceHandler(FileSystemEventHandler):
    """
    Watchdog event handler that debounces file modifications per filepath.
    After DEBOUNCE_SECONDS of inactivity for a given file, fires the callback.
    """

    def __init__(
        self,
        repo_root: str,
        on_file_changed: Callable[[str], None],
    ) -> None:
        super().__init__()
        self._repo_root = repo_root
        self._on_file_changed = on_file_changed
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.Lock()

    def on_modified(self, event: FileModifiedEvent) -> None:  # type: ignore[override]
        if event.is_directory:
            return
        src = event.src_path
        self._schedule(src)

    def _schedule(self, filepath: str) -> None:
        with self._lock:
            # Cancel existing timer for this file
            existing = self._timers.get(filepath)
            if existing:
                existing.cancel()
            # Schedule new timer
            timer = threading.Timer(
                DEBOUNCE_SECONDS,
                self._fire,
                args=(filepath,),
            )
            timer.daemon = True
            self._timers[filepath] = timer
            timer.start()

    def _fire(self, filepath: str) -> None:
        with self._lock:
            self._timers.pop(filepath, None)
        # Check filter before firing
        ok, reason = should_process(filepath, self._repo_root)
        if not ok:
            logger.debug("Skipped %s: %s", filepath, reason)
            return
        self._on_file_changed(filepath)


class Watcher:
    """
    High-level watcher that ties together:
    - watchdog Observer
    - _DebounceHandler
    - DatabaseManager
    - Optional AIEngine (ia-on mode)
    """

    def __init__(
        self,
        repo_root: str,
        ia_mode: bool = False,
        api_key: str | None = None,
    ) -> None:
        self.repo_root = str(Path(repo_root).resolve())
        self.ia_mode = ia_mode
        self.api_key = api_key
        self._observer: Observer | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the file watcher (blocking call; run in background process)."""
        # Write PID so daemon.py can manage this process
        PID_FILE.parent.mkdir(parents=True, exist_ok=True)
        PID_FILE.write_text(str(os.getpid()))

        # Start a dedicated asyncio loop in a thread for async DB/AI calls
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(
            target=self._loop.run_forever, daemon=True
        )
        self._loop_thread.start()

        handler = _DebounceHandler(
            repo_root=self.repo_root,
            on_file_changed=self._handle_change,
        )

        self._observer = Observer()
        self._observer.schedule(handler, self.repo_root, recursive=True)
        self._observer.start()
        logger.info(
            "SuperGit watcher started for %s (ia-on=%s)",
            self.repo_root,
            self.ia_mode,
        )

        try:
            while self._observer.is_alive():
                self._observer.join(timeout=1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def stop(self) -> None:
        """Gracefully stop the watcher."""
        if self._observer:
            self._observer.stop()
            self._observer.join()
        if self._loop:
            self._loop.call_soon_threadsafe(self._loop.stop)
        if PID_FILE.exists():
            PID_FILE.unlink(missing_ok=True)
        logger.info("SuperGit watcher stopped")

    def _handle_change(self, filepath: str) -> None:
        """Synchronous trampoline — dispatches async work to the dedicated loop."""
        assert self._loop, "Event loop not initialized"
        asyncio.run_coroutine_threadsafe(
            self._process_change(filepath), self._loop
        )

    async def _process_change(self, filepath: str) -> None:
        """Core async handler: diff → DB → optional MAP analysis."""
        diff_text = _get_git_diff(filepath, self.repo_root)
        diff_b64 = _encode_diff(diff_text) if diff_text else None

        async with DatabaseManager() as db:
            event_id = await db.save_event(
                repo_root=self.repo_root,
                filepath=filepath,
                event_type="modified",
                diff_snippet=diff_b64,
            )
            logger.info("Saved event #%d for %s", event_id, filepath)

            if self.ia_mode and self.api_key and diff_b64:
                from .ai_engine import AIEngine  # lazy import to avoid cycles
                async with AIEngine(self.api_key, db) as engine:
                    analysis = await engine.analyze_file(
                        filepath, diff_b64, event_id=event_id
                    )
                    if analysis.error:
                        logger.warning(
                            "ia-on MAP failed for %s: %s", filepath, analysis.error
                        )
                    else:
                        logger.info(
                            "ia-on MAP done for %s (%d tokens)",
                            filepath,
                            analysis.tokens_used,
                        )
