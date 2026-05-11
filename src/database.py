"""
database.py — SQLite WAL + Event Management

All I/O is async via aiosqlite. WAL mode is enabled on every connection
for safe concurrent access between the background watcher and the CLI.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import aiosqlite

# Default path for the database
SUPERGIT_DIR = Path.home() / ".supergit"
DB_PATH = SUPERGIT_DIR / "events.db"

_SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    repo_root     TEXT    NOT NULL,
    filepath      TEXT    NOT NULL,
    timestamp     REAL    NOT NULL,
    event_type    TEXT    NOT NULL,
    diff_snippet  TEXT,               -- Base64-encoded diff
    ia_analysis   TEXT,               -- Summary from MAP phase
    tokens_used   INTEGER,            -- Groq token cost
    committed     BOOLEAN DEFAULT 0   -- Merged into working branch?
);

CREATE INDEX IF NOT EXISTS idx_events_committed ON events(committed);
CREATE INDEX IF NOT EXISTS idx_events_repo      ON events(repo_root);

CREATE TABLE IF NOT EXISTS audit_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   REAL    NOT NULL,
    filepath    TEXT,
    prompt_hash TEXT,               -- SHA256 of prompt sent (privacy)
    response    TEXT,               -- AI response stored for audit
    tokens_used INTEGER,
    model       TEXT,
    error       TEXT
);
"""


class DatabaseManager:
    """Async context manager wrapping aiosqlite with WAL mode."""

    def __init__(self, db_path: str | Path = DB_PATH) -> None:
        self.db_path = Path(db_path)
        self._conn: aiosqlite.Connection | None = None

    async def __aenter__(self) -> "DatabaseManager":
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self.db_path)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        return self

    async def __aexit__(self, *args: Any) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Event CRUD
    # ------------------------------------------------------------------

    async def save_event(
        self,
        *,
        repo_root: str,
        filepath: str,
        event_type: str,
        diff_snippet: str | None = None,
        ia_analysis: str | None = None,
        tokens_used: int | None = None,
    ) -> int:
        """Persist a file-change event. Returns the new row id."""
        assert self._conn, "Not connected"
        cursor = await self._conn.execute(
            """
            INSERT INTO events
                (repo_root, filepath, timestamp, event_type,
                 diff_snippet, ia_analysis, tokens_used, committed)
            VALUES (?, ?, ?, ?, ?, ?, ?, 0)
            """,
            (
                repo_root,
                filepath,
                time.time(),
                event_type,
                diff_snippet,
                ia_analysis,
                tokens_used,
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid  # type: ignore[return-value]

    async def update_ia_analysis(
        self,
        event_id: int,
        ia_analysis: str,
        tokens_used: int,
    ) -> None:
        """Set ia_analysis after async MAP phase completes."""
        assert self._conn
        await self._conn.execute(
            "UPDATE events SET ia_analysis=?, tokens_used=? WHERE id=?",
            (ia_analysis, tokens_used, event_id),
        )
        await self._conn.commit()

    async def get_pending_events(self, repo_root: str) -> list[dict]:
        """Return all uncommitted events for a given repo."""
        assert self._conn
        cursor = await self._conn.execute(
            """
            SELECT id, filepath, timestamp, event_type,
                   diff_snippet, ia_analysis, tokens_used
            FROM   events
            WHERE  repo_root = ? AND committed = 0
            ORDER  BY timestamp ASC
            """,
            (repo_root,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def mark_committed(self, event_ids: list[int]) -> None:
        """Mark events as consumed after a successful merge."""
        assert self._conn
        placeholders = ",".join("?" * len(event_ids))
        await self._conn.execute(
            f"UPDATE events SET committed=1 WHERE id IN ({placeholders})",
            event_ids,
        )
        await self._conn.commit()

    async def get_logs(self, limit: int = 50) -> list[dict]:
        """Return recent audit log entries (most recent first)."""
        assert self._conn
        cursor = await self._conn.execute(
            """
            SELECT id, timestamp, filepath, response, tokens_used, model, error
            FROM   audit_log
            ORDER  BY timestamp DESC
            LIMIT  ?
            """,
            (limit,),
        )
        rows = await cursor.fetchall()
        return [dict(r) for r in rows]

    async def get_log(self, log_id: int) -> dict | None:
        """Fetch a specific log entry by its ID."""
        assert self._conn
        cursor = await self._conn.execute(
            "SELECT * FROM audit_log WHERE id = ?",
            (log_id,)
        )
        row = await cursor.fetchone()
        return dict(row) if row else None

    async def clear_audit_logs(self) -> None:
        """Delete all entries from the audit_log table."""
        assert self._conn
        await self._conn.execute("DELETE FROM audit_log")
        await self._conn.commit()

    async def append_audit(
        self,
        *,
        filepath: str | None,
        prompt_hash: str,
        response: str | None,
        tokens_used: int | None,
        model: str,
        error: str | None = None,
    ) -> None:
        """Record one AI interaction in the audit log."""
        assert self._conn
        await self._conn.execute(
            """
            INSERT INTO audit_log
                (timestamp, filepath, prompt_hash, response,
                 tokens_used, model, error)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                time.time(),
                filepath,
                prompt_hash,
                response,
                tokens_used,
                model,
                error,
            ),
        )
        await self._conn.commit()

    async def token_total(self, repo_root: str) -> int:
        """Sum of all tokens used for a repo (pending events only)."""
        assert self._conn
        cursor = await self._conn.execute(
            """
            SELECT COALESCE(SUM(tokens_used), 0)
            FROM   events
            WHERE  repo_root = ? AND committed = 0
            """,
            (repo_root,),
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 0


# ---------------------------------------------------------------------------
# Convenience: run a coroutine synchronously (used by CLI which isn't async)
# ---------------------------------------------------------------------------

def run_sync(coro):  # type: ignore[no-untyped-def]
    """Run an async coroutine from sync context."""
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        return loop.run_until_complete(coro)
    except RuntimeError:
        return asyncio.run(coro)
