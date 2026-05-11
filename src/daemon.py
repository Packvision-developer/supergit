"""
daemon.py — Background Process Lifecycle Manager

Controls the SuperGit watcher process:
  start_daemon()  → fork background Watcher process, write PID
  stop_daemon()   → send SIGTERM via PID file
  daemon_status() → check liveness + return metadata dict
"""
from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PID_FILE = Path.home() / ".supergit" / "watcher.pid"
CONFIG_FILE = Path.home() / ".supergit" / "config"
LOGS_DIR = Path.home() / ".supergit" / "logs"


def _write_config(mode: str, repo_root: str) -> None:
    """Persist watcher mode and repo to config file."""
    CONFIG_FILE.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(f"mode={mode}\nrepo_root={repo_root}\n")


def _read_config() -> dict[str, str]:
    """Read watcher config, return empty dict if missing."""
    if not CONFIG_FILE.is_file():
        return {}
    cfg: dict[str, str] = {}
    for line in CONFIG_FILE.read_text().splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            cfg[k.strip()] = v.strip()
    return cfg


def _is_running(pid: int) -> bool:
    """Check whether a process with the given PID is alive."""
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def read_pid() -> int | None:
    """Return the watcher PID from file, or None if not running."""
    if not PID_FILE.is_file():
        return None
    try:
        pid = int(PID_FILE.read_text().strip())
        return pid if _is_running(pid) else None
    except (ValueError, OSError):
        return None


def start_daemon(
    repo_root: str,
    mode: str = "ia-off",
    api_key: str | None = None,
) -> int:
    """
    Spawn the watcher as a detached background subprocess.

    Returns the PID of the background process.
    Raises RuntimeError if a watcher is already running.
    """
    existing = read_pid()
    if existing:
        raise RuntimeError(f"SuperGit watcher already running (PID {existing})")

    _write_config(mode, repo_root)
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOGS_DIR / "watcher.log"

    env = os.environ.copy()
    if api_key:
        env["SUPERGIT_GROQ_KEY"] = api_key

    # Build the command — no shell=True
    cmd = [
        sys.executable,
        "-m",
        "src.watcher_entry",  # thin entry-point module
        "--repo-root",
        repo_root,
        "--mode",
        mode,
    ]

    with open(log_path, "a") as log_fh:
        proc = subprocess.Popen(
            cmd,
            stdout=log_fh,
            stderr=log_fh,
            env=env,
            # Detach from controlling terminal on POSIX
            start_new_session=True,
        )

    # Give it a moment to write its own PID file
    for _ in range(20):
        time.sleep(0.1)
        if PID_FILE.is_file():
            break

    return proc.pid


def stop_daemon() -> bool:
    """
    Stop the running watcher.

    Returns True if a process was stopped, False if none was running.
    """
    pid = read_pid()
    if pid is None:
        return False

    try:
        os.kill(pid, signal.SIGTERM)
        # Wait up to 3 seconds for it to exit
        for _ in range(30):
            time.sleep(0.1)
            if not _is_running(pid):
                break
        else:
            # Force kill if graceful shutdown timed out
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
    except ProcessLookupError:
        pass

    PID_FILE.unlink(missing_ok=True)
    return True


def daemon_status() -> dict:
    """
    Return a status dictionary with watcher metadata.

    Keys: running (bool), pid (int|None), mode (str), repo_root (str)
    """
    pid = read_pid()
    cfg = _read_config()
    return {
        "running": pid is not None,
        "pid": pid,
        "mode": cfg.get("mode", "ia-off"),
        "repo_root": cfg.get("repo_root", ""),
    }
