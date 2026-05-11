"""
watcher_entry.py — CLI entry-point for the background watcher subprocess.

Called by daemon.py via:
    python -m src.watcher_entry --repo-root /path --mode ia-off
"""
from __future__ import annotations

import argparse
import logging
import os
from pathlib import Path

from .watcher import Watcher

LOGS_DIR = Path.home() / ".supergit" / "logs"


def _setup_logging() -> None:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "watcher.log"
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
        handlers=[
            logging.FileHandler(log_file),
            logging.StreamHandler(),
        ],
    )


def main() -> None:
    _setup_logging()
    parser = argparse.ArgumentParser(prog="supergit-watcher")
    parser.add_argument("--repo-root", required=True)
    parser.add_argument("--mode", default="ia-off", choices=["ia-off", "ia-on"])
    args = parser.parse_args()

    api_key = os.environ.get("SUPERGIT_GROQ_KEY")
    ia_mode = args.mode == "ia-on"

    watcher = Watcher(
        repo_root=args.repo_root,
        ia_mode=ia_mode,
        api_key=api_key,
    )
    watcher.start()  # blocking


if __name__ == "__main__":
    main()
