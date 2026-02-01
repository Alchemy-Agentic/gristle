"""Tests for gristle.ingestion.watcher — start/stop/is_watching helpers."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from gristle.ingestion.watcher import (
    _active_watchers,
    is_watching,
    start_watching,
    stop_watching,
)


@pytest.fixture(autouse=True)
def _clear_watchers():
    """Ensure watcher registry is clean before and after each test."""
    _active_watchers.clear()
    yield
    # Cancel any lingering tasks
    for task in _active_watchers.values():
        if not task.done():
            task.cancel()
    _active_watchers.clear()


class TestIsWatching:
    def test_false_when_no_watcher(self):
        assert is_watching("repo1") is False

    def test_true_when_task_active(self):
        task = MagicMock()
        task.done.return_value = False
        _active_watchers["repo1"] = task
        assert is_watching("repo1") is True

    def test_false_when_task_done(self):
        task = MagicMock()
        task.done.return_value = True
        _active_watchers["repo1"] = task
        assert is_watching("repo1") is False


class TestStopWatching:
    def test_stop_cancels_active_task(self):
        task = MagicMock()
        task.done.return_value = False
        _active_watchers["repo1"] = task
        result = stop_watching("repo1")
        assert result is True
        task.cancel.assert_called_once()
        assert "repo1" not in _active_watchers

    def test_stop_returns_false_when_not_watching(self):
        assert stop_watching("repo1") is False

    def test_stop_returns_false_when_task_done(self):
        task = MagicMock()
        task.done.return_value = True
        _active_watchers["repo1"] = task
        result = stop_watching("repo1")
        assert result is False


class TestStartWatching:
    def test_start_creates_task(self):
        pipeline = MagicMock()
        mock_task = MagicMock()
        mock_task.done.return_value = False

        mock_loop = MagicMock()
        mock_loop.create_task.return_value = mock_task

        with patch("gristle.ingestion.watcher.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value = mock_loop
            result = start_watching("repo1", "/tmp/repo", pipeline)

        assert result is True
        assert "repo1" in _active_watchers

    def test_start_returns_false_if_already_watching(self):
        task = MagicMock()
        task.done.return_value = False
        _active_watchers["repo1"] = task

        pipeline = MagicMock()
        result = start_watching("repo1", "/tmp/repo", pipeline)
        assert result is False

    def test_start_replaces_done_task(self):
        old_task = MagicMock()
        old_task.done.return_value = True
        _active_watchers["repo1"] = old_task

        pipeline = MagicMock()
        mock_task = MagicMock()
        mock_task.done.return_value = False

        mock_loop = MagicMock()
        mock_loop.create_task.return_value = mock_task

        with patch("gristle.ingestion.watcher.asyncio") as mock_asyncio:
            mock_asyncio.get_event_loop.return_value = mock_loop
            result = start_watching("repo1", "/tmp/repo", pipeline)

        assert result is True
