"""
ai_engine.py — Groq Map-Reduce Pipeline

Implements the two-phase analysis:
  MAP    — one async Groq call per file (semaphore-limited to 5)
  REDUCE — single call that consolidates MAP summaries into a Conventional Commit

Also handles:
  - Hunk splitting for oversized diffs
  - Circuit breaker (3 consecutive failures → generic message)
  - Prompt injection protection via <DIFF_DATA> tags
  - Token usage tracking
  - Audit log writes via DatabaseManager
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import re
import time
from pathlib import Path
from typing import NamedTuple

from groq import AsyncGroq

from .database import DatabaseManager

logger = logging.getLogger("supergit.ai")

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
MODEL = "llama-3.3-70b-versatile"
MAX_CONCURRENT_MAPS = 5          # asyncio.Semaphore value
HUNK_TOKEN_THRESHOLD = 12_000    # estimated tokens; split diff if exceeded
CHARS_PER_TOKEN_ESTIMATE = 4     # rough approximation
CIRCUIT_BREAKER_THRESHOLD = 3
GENERIC_COMMIT_MSG = "chore: update pending AI review"

LOGS_DIR = Path.home() / ".supergit" / "logs"


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------

class FileAnalysis(NamedTuple):
    filepath: str
    summary: str
    tokens_used: int
    error: str | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _estimate_tokens(text: str) -> int:
    return len(text) // CHARS_PER_TOKEN_ESTIMATE


def _split_into_hunks(diff_text: str) -> list[str]:
    """Split a unified diff into individual @@ hunks."""
    hunks: list[str] = []
    current: list[str] = []
    for line in diff_text.splitlines(keepends=True):
        if line.startswith("@@") and current:
            hunks.append("".join(current))
            current = [line]
        else:
            current.append(line)
    if current:
        hunks.append("".join(current))
    return hunks or [diff_text]


def _log_error(msg: str) -> None:
    """Append error to the persistent error log."""
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    log_file = LOGS_DIR / "errors.log"
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(log_file, "a", encoding="utf-8") as f:
        f.write(f"[{timestamp}] {msg}\n")


# ---------------------------------------------------------------------------
# AIEngine
# ---------------------------------------------------------------------------

class AIEngine:
    """
    Orchestrates the Map-Reduce pipeline against Groq's API.

    Usage:
        async with AIEngine(api_key) as engine:
            commit_msg = await engine.run_commit_analysis(events)
    """

    def __init__(self, api_key: str, db_manager: DatabaseManager | None = None) -> None:
        self._api_key = api_key
        self._client: AsyncGroq | None = None
        self._db = db_manager
        self._semaphore = asyncio.Semaphore(MAX_CONCURRENT_MAPS)
        self._failure_count = 0

    async def __aenter__(self) -> "AIEngine":
        self._client = AsyncGroq(api_key=self._api_key)
        return self

    async def __aexit__(self, *args) -> None:  # type: ignore[override]
        if self._client:
            await self._client.close()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_commit_analysis(self, events: list[dict]) -> str:
        """
        Full Map-Reduce pipeline.

        Args:
            events: list of dicts from DatabaseManager.get_pending_events()

        Returns:
            A Conventional Commit message string.
        """
        if not events:
            return "chore: no pending changes detected"

        # MAP phase
        map_tasks = [self._map_event(ev) for ev in events]
        analyses: list[FileAnalysis] = await asyncio.gather(*map_tasks)

        # Separate successes from errors
        successes = [a for a in analyses if a.error is None]
        errors = [a for a in analyses if a.error is not None]

        if errors:
            logger.warning("MAP phase had %d error(s)", len(errors))

        if not successes:
            # All MAP calls failed — circuit breaker already triggered
            return GENERIC_COMMIT_MSG

        # REDUCE phase
        return await self._reduce(successes)

    async def analyze_file(
        self,
        filepath: str,
        diff_b64: str,
        event_id: int | None = None,
    ) -> FileAnalysis:
        """
        Public entry point for ia-on mode (single-file immediate analysis).
        Updates DB with the result if event_id is provided.
        """
        diff_text = base64.b64decode(diff_b64).decode("utf-8", errors="replace")
        analysis = await self._call_map(filepath, diff_text)

        if event_id is not None and self._db is not None and analysis.error is None:
            await self._db.update_ia_analysis(
                event_id, analysis.summary, analysis.tokens_used
            )

        return analysis

    # ------------------------------------------------------------------
    # MAP phase internals
    # ------------------------------------------------------------------

    async def _map_event(self, event: dict) -> FileAnalysis:
        """Decode diff from DB event and run MAP analysis."""
        filepath = event["filepath"]
        diff_b64 = event.get("diff_snippet", "")

        # If ia_analysis already exists (ia-on pre-filled), reuse it
        if event.get("ia_analysis"):
            return FileAnalysis(
                filepath=filepath,
                summary=event["ia_analysis"],
                tokens_used=event.get("tokens_used") or 0,
            )

        if not diff_b64:
            return FileAnalysis(
                filepath=filepath,
                summary=f"Modified {filepath} (no diff available)",
                tokens_used=0,
            )

        diff_text = base64.b64decode(diff_b64).decode("utf-8", errors="replace")
        return await self._call_map(filepath, diff_text)

    async def _call_map(self, filepath: str, diff_text: str) -> FileAnalysis:
        """
        Send one MAP call to Groq. Splits into hunks if diff is too large.
        Uses asyncio.Semaphore to cap concurrent calls.
        """
        estimated = _estimate_tokens(diff_text)

        if estimated > HUNK_TOKEN_THRESHOLD:
            return await self._call_map_hunked(filepath, diff_text)

        async with self._semaphore:
            return await self._single_map_call(filepath, diff_text)

    async def _call_map_hunked(self, filepath: str, diff_text: str) -> FileAnalysis:
        """Split a large diff into hunks and MAP each independently."""
        hunks = _split_into_hunks(diff_text)
        hunk_tasks = [
            self._single_map_call(f"{filepath}#hunk{i+1}", hunk)
            for i, hunk in enumerate(hunks)
        ]
        hunk_results: list[FileAnalysis] = await asyncio.gather(*hunk_tasks)

        # Merge summaries
        combined = "; ".join(r.summary for r in hunk_results if r.error is None)
        total_tokens = sum(r.tokens_used for r in hunk_results)
        errors = [r for r in hunk_results if r.error]

        if not combined and errors:
            return FileAnalysis(
                filepath=filepath,
                summary="",
                tokens_used=total_tokens,
                error=errors[0].error,
            )

        return FileAnalysis(filepath=filepath, summary=combined, tokens_used=total_tokens)

    async def _single_map_call(self, filepath: str, diff_text: str) -> FileAnalysis:
        """Execute one Groq MAP call with circuit-breaker and audit logging."""
        assert self._client, "Client not initialized"

        prompt = (
            f"Analyze the change in the file `{filepath}` and summarize in one "
            f"technical sentence what functionality was added, fixed, or refactored.\n\n"
            f"<DIFF_DATA>\n{diff_text}\n</DIFF_DATA>"
        )
        prompt_hash = hashlib.sha256(prompt.encode()).hexdigest()[:16]

        try:
            response = await self._client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.2,
                max_tokens=256,
            )
            summary = response.choices[0].message.content.strip()
            tokens = response.usage.total_tokens if response.usage else 0
            self._failure_count = 0  # reset circuit breaker

            if self._db:
                await self._db.append_audit(
                    filepath=filepath,
                    prompt_hash=prompt_hash,
                    response=summary,
                    tokens_used=tokens,
                    model=MODEL,
                )

            return FileAnalysis(filepath=filepath, summary=summary, tokens_used=tokens)

        except Exception as exc:
            self._failure_count += 1
            error_msg = f"MAP call failed for {filepath}: {exc}"
            logger.error(error_msg)
            _log_error(error_msg)

            if self._db:
                await self._db.append_audit(
                    filepath=filepath,
                    prompt_hash=prompt_hash,
                    response=None,
                    tokens_used=None,
                    model=MODEL,
                    error=str(exc),
                )

            if self._failure_count >= CIRCUIT_BREAKER_THRESHOLD:
                logger.critical(
                    "Circuit breaker tripped after %d failures", self._failure_count
                )
                _log_error(
                    f"CIRCUIT BREAKER TRIPPED after {self._failure_count} failures"
                )

            return FileAnalysis(filepath=filepath, summary="", tokens_used=0, error=str(exc))

    # ------------------------------------------------------------------
    # REDUCE phase
    # ------------------------------------------------------------------

    async def _reduce(self, analyses: list[FileAnalysis]) -> str:
        """
        Consolidate all MAP summaries into a single Conventional Commit message.
        Returns GENERIC_COMMIT_MSG if Groq fails at this stage.
        """
        if self._failure_count >= CIRCUIT_BREAKER_THRESHOLD:
            return GENERIC_COMMIT_MSG

        summary_block = "\n".join(
            f"- [{a.filepath}]: {a.summary}" for a in analyses
        )

        master_prompt = (
            "You are a senior software engineer writing a Git commit message.\n"
            "Based on the following per-file change summaries, write a commit message "
            "following the Conventional Commits standard (https://conventionalcommits.org).\n\n"
            "Requirements:\n"
            "  1. First line: `<type>(<scope>): <short description>` (≤72 chars)\n"
            "  2. Blank line\n"
            "  3. Bullet list of key changes (mention file names, line ranges if known)\n"
            "  4. If breaking change, add `BREAKING CHANGE:` footer\n\n"
            "File summaries:\n"
            f"<DIFF_DATA>\n{summary_block}\n</DIFF_DATA>\n\n"
            "Respond with ONLY the commit message, no extra explanation."
        )
        prompt_hash = hashlib.sha256(master_prompt.encode()).hexdigest()[:16]

        assert self._client
        try:
            response = await self._client.chat.completions.create(
                model=MODEL,
                messages=[{"role": "user", "content": master_prompt}],
                temperature=0.3,
                max_tokens=512,
            )
            result = response.choices[0].message.content.strip()
            tokens = response.usage.total_tokens if response.usage else 0
            self._failure_count = 0

            if self._db:
                await self._db.append_audit(
                    filepath=None,
                    prompt_hash=prompt_hash,
                    response=result,
                    tokens_used=tokens,
                    model=MODEL,
                )

            return result

        except Exception as exc:
            self._failure_count += 1
            error_msg = f"REDUCE call failed: {exc}"
            logger.error(error_msg)
            _log_error(error_msg)

            if self._db:
                await self._db.append_audit(
                    filepath=None,
                    prompt_hash=prompt_hash,
                    response=None,
                    tokens_used=None,
                    model=MODEL,
                    error=str(exc),
                )

            return GENERIC_COMMIT_MSG
