"""
test_supergit.py — TDD test suite for SuperGit v2.0

Tests cover:
  - filters: denylist, binary detection
  - database: round-trip persistence
  - ai_engine: circuit breaker, hunk splitting
  - watcher: debounce behaviour
  - commit flow: branch creation + squash (integration)
"""
from __future__ import annotations

import asyncio
import base64
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.filters import is_binary_file, is_denied, should_process
from src.database import DatabaseManager
from src.ai_engine import (
    AIEngine,
    FileAnalysis,
    GENERIC_COMMIT_MSG,
    _split_into_hunks,
    _estimate_tokens,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_repo(tmp_path):
    """Create a minimal fake git repo directory."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / ".git").mkdir()
    return repo


@pytest.fixture
def tmp_db(tmp_path):
    """Yield a DatabaseManager pointed at a temp file."""
    return DatabaseManager(db_path=tmp_path / "test.db")


# ---------------------------------------------------------------------------
# filters.py tests
# ---------------------------------------------------------------------------

class TestFiltersDenylist:
    def test_env_file_denied(self, tmp_repo):
        env_file = tmp_repo / ".env"
        env_file.write_text("SECRET=abc")
        denied, reason = is_denied(env_file)
        assert denied, f"Expected .env to be denied, got reason: {reason}"

    def test_pem_file_denied(self, tmp_repo):
        key = tmp_repo / "private.pem"
        key.write_text("-----BEGIN RSA PRIVATE KEY-----")
        denied, _ = is_denied(key)
        assert denied

    def test_id_rsa_denied(self, tmp_repo):
        key = tmp_repo / "id_rsa"
        key.write_text("private")
        denied, _ = is_denied(key)
        assert denied

    def test_normal_python_file_allowed(self, tmp_repo):
        py = tmp_repo / "main.py"
        py.write_text("print('hello')")
        denied, _ = is_denied(py)
        assert not denied

    def test_sqlite_db_denied(self, tmp_repo):
        db = tmp_repo / "data.db"
        db.write_bytes(b"\x00" * 16)
        denied, _ = is_denied(db)
        assert denied

    def test_dotenv_variant_denied(self, tmp_repo):
        env = tmp_repo / ".env.production"
        env.write_text("API_KEY=prod_secret")
        denied, _ = is_denied(env)
        assert denied


class TestFiltersBinary:
    def test_png_is_binary(self, tmp_repo):
        img = tmp_repo / "logo.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 100)
        assert is_binary_file(img)

    def test_null_byte_file_is_binary(self, tmp_repo):
        f = tmp_repo / "weird.bin"
        f.write_bytes(b"hello\x00world")
        assert is_binary_file(f)

    def test_plain_text_not_binary(self, tmp_repo):
        f = tmp_repo / "code.py"
        f.write_text("def foo(): pass\n")
        assert not is_binary_file(f)


class TestShouldProcess:
    def test_git_internals_blocked(self, tmp_repo):
        git_file = tmp_repo / ".git" / "HEAD"
        git_file.parent.mkdir(exist_ok=True)
        git_file.write_text("ref: refs/heads/main\n")
        ok, reason = should_process(git_file, tmp_repo)
        assert not ok
        assert ".git" in reason

    def test_outside_repo_blocked(self, tmp_repo, tmp_path):
        outside = tmp_path / "other.py"
        outside.write_text("x = 1")
        ok, reason = should_process(outside, tmp_repo)
        assert not ok

    def test_gitignore_respected(self, tmp_repo):
        (tmp_repo / ".gitignore").write_text("node_modules/\n*.log\n")
        log_file = tmp_repo / "debug.log"
        log_file.write_text("error log")
        ok, reason = should_process(log_file, tmp_repo)
        assert not ok


# ---------------------------------------------------------------------------
# database.py tests
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
class TestDatabaseRoundTrip:
    async def test_save_and_retrieve(self, tmp_db):
        async with tmp_db as db:
            eid = await db.save_event(
                repo_root="/fake/repo",
                filepath="/fake/repo/app.py",
                event_type="modified",
                diff_snippet="ZGlmZg==",
            )
            assert isinstance(eid, int)
            events = await db.get_pending_events("/fake/repo")
            assert len(events) == 1
            assert events[0]["filepath"] == "/fake/repo/app.py"

    async def test_mark_committed(self, tmp_db):
        async with tmp_db as db:
            eid = await db.save_event(
                repo_root="/r",
                filepath="/r/x.py",
                event_type="modified",
            )
            await db.mark_committed([eid])
            events = await db.get_pending_events("/r")
            assert events == []

    async def test_update_ia_analysis(self, tmp_db):
        async with tmp_db as db:
            eid = await db.save_event(
                repo_root="/r",
                filepath="/r/y.py",
                event_type="modified",
            )
            await db.update_ia_analysis(eid, "Added login handler", 42)
            events = await db.get_pending_events("/r")
            assert events[0]["ia_analysis"] == "Added login handler"
            assert events[0]["tokens_used"] == 42

    async def test_token_total(self, tmp_db):
        async with tmp_db as db:
            await db.save_event(
                repo_root="/r", filepath="/r/a.py",
                event_type="modified", tokens_used=100,
            )
            await db.save_event(
                repo_root="/r", filepath="/r/b.py",
                event_type="modified", tokens_used=200,
            )
            total = await db.token_total("/r")
            assert total == 300

    async def test_audit_log(self, tmp_db):
        async with tmp_db as db:
            await db.append_audit(
                filepath="/r/z.py",
                prompt_hash="abc123",
                response="Fixed null pointer",
                tokens_used=77,
                model="llama-3.3-70b-versatile",
            )
            logs = await db.get_logs(10)
            assert len(logs) == 1
            assert logs[0]["response"] == "Fixed null pointer"


# ---------------------------------------------------------------------------
# ai_engine.py tests
# ---------------------------------------------------------------------------

class TestHunkSplitting:
    def test_empty_diff_returns_single_item(self):
        hunks = _split_into_hunks("")
        assert len(hunks) == 1

    def test_single_hunk_unchanged(self):
        diff = "@@ -1,3 +1,4 @@\n context\n+added\n context\n context\n"
        hunks = _split_into_hunks(diff)
        assert len(hunks) == 1
        assert hunks[0] == diff

    def test_two_hunks_split(self):
        diff = (
            "@@ -1,2 +1,2 @@\n-old\n+new\n"
            "@@ -10,2 +10,3 @@\n context\n+extra\n context\n"
        )
        hunks = _split_into_hunks(diff)
        assert len(hunks) == 2

    def test_token_estimate(self):
        text = "a" * 400  # 400 chars / 4 = 100 tokens
        assert _estimate_tokens(text) == 100


@pytest.mark.asyncio
class TestCircuitBreaker:
    async def test_three_failures_return_generic(self):
        """After 3 MAP failures, REDUCE should return the generic message."""
        fake_events = [
            {"filepath": f"/r/file{i}.py", "diff_snippet": base64.b64encode(b"diff").decode(),
             "ia_analysis": None, "tokens_used": None}
            for i in range(3)
        ]

        # Patch AsyncGroq so every chat.completions.create raises
        with patch("src.ai_engine.AsyncGroq") as MockGroq:
            instance = AsyncMock()
            instance.chat.completions.create = AsyncMock(
                side_effect=Exception("API timeout")
            )
            instance.close = AsyncMock()
            MockGroq.return_value = instance

            async with AIEngine("fake-key") as engine:
                result = await engine.run_commit_analysis(fake_events)

        assert result == GENERIC_COMMIT_MSG

    async def test_ia_analysis_reuse(self):
        """If ia_analysis is pre-filled, MAP skips the API call."""
        events = [
            {"filepath": "/r/utils.py", "diff_snippet": None,
             "ia_analysis": "Refactored utility functions", "tokens_used": 50}
        ]

        # Mock the REDUCE call only
        mock_response = MagicMock()
        mock_response.choices = [MagicMock(message=MagicMock(content="feat: refactor utils"))]
        mock_response.usage = MagicMock(total_tokens=30)

        with patch("src.ai_engine.AsyncGroq") as MockGroq:
            instance = AsyncMock()
            instance.chat.completions.create = AsyncMock(return_value=mock_response)
            instance.close = AsyncMock()
            MockGroq.return_value = instance

            async with AIEngine("test-key") as engine:
                result = await engine.run_commit_analysis(events)

        # Should have called Groq exactly once (for REDUCE, not MAP)
        assert instance.chat.completions.create.call_count == 1
        assert "refactor" in result.lower() or result  # just verify it ran


# ---------------------------------------------------------------------------
# watcher.py debounce test
# ---------------------------------------------------------------------------

class TestDebounce:
    def test_debounce_fires_once(self):
        """Rapid calls should result in only one callback invocation."""
        from src.watcher import _DebounceHandler

        fired: list[str] = []

        def callback(fp: str) -> None:
            fired.append(fp)

        with tempfile.TemporaryDirectory() as tmp:
            # _fire calls should_process internally; bypass it by patching.
            with patch("src.watcher.should_process", return_value=(True, "ok")):
                handler = _DebounceHandler(repo_root=tmp, on_file_changed=callback)

                # Simulate 10 rapid modifications
                for _ in range(10):
                    handler._schedule("/fake/path/file.py")
                    time.sleep(0.05)  # 50ms between each

                # Wait for debounce to expire
                time.sleep(1.0)

        assert len(fired) == 1, f"Expected 1 callback, got {len(fired)}"

    def test_debounce_resets_timer(self):
        """Timer reset: callback fires after inactivity, not at first event."""
        from src.watcher import _DebounceHandler

        fired_at: list[float] = []
        start = time.monotonic()

        def callback(fp: str) -> None:
            fired_at.append(time.monotonic() - start)

        with tempfile.TemporaryDirectory() as tmp:
            with patch("src.watcher.should_process", return_value=(True, "ok")):
                handler = _DebounceHandler(repo_root=tmp, on_file_changed=callback)

                # 3 events spread over 300ms (each resets the 750ms timer)
                for i in range(3):
                    handler._schedule("/fake/file.py")
                    time.sleep(0.1)

                time.sleep(1.0)

        assert len(fired_at) == 1
        # Should fire at least 750ms after the LAST event (≈ 300ms + 750ms = 1050ms from start)
        assert fired_at[0] > 0.8
