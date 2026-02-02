"""Tests for gristle.logging — formatters, configure_logging, Timer."""

from __future__ import annotations

import json
import logging

from gristle.logging import JSONFormatter, TextFormatter, Timer, configure_logging

# ------------------------------------------------------------------
# JSONFormatter
# ------------------------------------------------------------------


class TestJSONFormatter:
    def _make_record(self, msg="hello", level=logging.INFO, **extra):
        logger = logging.getLogger("test.json")
        record = logger.makeRecord(
            name="test.json",
            level=level,
            fn="test.py",
            lno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )
        for k, v in extra.items():
            setattr(record, k, v)
        return record

    def test_basic_json_output(self):
        fmt = JSONFormatter()
        record = self._make_record("test message")
        output = fmt.format(record)
        data = json.loads(output)
        assert data["msg"] == "test message"
        assert data["level"] == "INFO"
        assert data["logger"] == "test.json"
        assert "ts" in data

    def test_extra_fields_included(self):
        fmt = JSONFormatter()
        record = self._make_record("ingestion", repo_id="abc123", files=50, nodes=200)
        output = fmt.format(record)
        data = json.loads(output)
        assert data["repo_id"] == "abc123"
        assert data["files"] == 50
        assert data["nodes"] == 200

    def test_extra_fields_absent_when_not_set(self):
        fmt = JSONFormatter()
        record = self._make_record("simple")
        output = fmt.format(record)
        data = json.loads(output)
        assert "repo_id" not in data
        assert "duration_ms" not in data

    def test_exception_included(self):
        fmt = JSONFormatter()
        logger = logging.getLogger("test.json")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            record = logger.makeRecord(
                name="test.json",
                level=logging.ERROR,
                fn="test.py",
                lno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = fmt.format(record)
        data = json.loads(output)
        assert "exception" in data
        assert "ValueError" in data["exception"]


# ------------------------------------------------------------------
# TextFormatter
# ------------------------------------------------------------------


class TestTextFormatter:
    def _make_record(self, msg="hello", level=logging.INFO):
        logger = logging.getLogger("test.text")
        return logger.makeRecord(
            name="test.text",
            level=level,
            fn="test.py",
            lno=1,
            msg=msg,
            args=(),
            exc_info=None,
        )

    def test_basic_text_output(self):
        fmt = TextFormatter()
        record = self._make_record("test message")
        output = fmt.format(record)
        assert "test message" in output
        assert "test.text" in output

    def test_colour_codes_present(self):
        fmt = TextFormatter()
        record = self._make_record("info msg", logging.INFO)
        output = fmt.format(record)
        assert "\033[36m" in output  # cyan for INFO
        assert "\033[0m" in output  # reset

    def test_error_colour(self):
        fmt = TextFormatter()
        record = self._make_record("err", logging.ERROR)
        output = fmt.format(record)
        assert "\033[31m" in output  # red for ERROR

    def test_exception_appended(self):
        fmt = TextFormatter()
        logger = logging.getLogger("test.text")
        try:
            raise RuntimeError("crash")
        except RuntimeError:
            import sys

            record = logger.makeRecord(
                name="test.text",
                level=logging.ERROR,
                fn="test.py",
                lno=1,
                msg="failed",
                args=(),
                exc_info=sys.exc_info(),
            )
        output = fmt.format(record)
        assert "RuntimeError" in output


# ------------------------------------------------------------------
# configure_logging
# ------------------------------------------------------------------


class TestConfigureLogging:
    def test_stdio_transport_uses_text_formatter(self):
        configure_logging("stdio")
        root = logging.getLogger()
        assert len(root.handlers) == 1
        assert isinstance(root.handlers[0].formatter, TextFormatter)

    def test_http_transport_uses_json_formatter(self):
        configure_logging("streamable-http")
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_env_override_json(self, monkeypatch):
        monkeypatch.setenv("GRISTLE_LOG_FORMAT", "json")
        configure_logging("stdio")
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, JSONFormatter)

    def test_env_override_text(self, monkeypatch):
        monkeypatch.setenv("GRISTLE_LOG_FORMAT", "text")
        configure_logging("streamable-http")
        root = logging.getLogger()
        assert isinstance(root.handlers[0].formatter, TextFormatter)

    def test_log_level_from_env(self, monkeypatch):
        monkeypatch.setenv("GRISTLE_LOG_LEVEL", "DEBUG")
        configure_logging()
        root = logging.getLogger()
        assert root.level == logging.DEBUG

    def test_invalid_level_defaults_to_info(self, monkeypatch):
        monkeypatch.setenv("GRISTLE_LOG_LEVEL", "NONEXISTENT")
        configure_logging()
        root = logging.getLogger()
        assert root.level == logging.INFO

    def test_noisy_loggers_silenced(self):
        configure_logging()
        for name in ("httpx", "httpcore", "uvicorn.access", "watchfiles"):
            assert logging.getLogger(name).level == logging.WARNING

    def test_clears_existing_handlers(self):
        root = logging.getLogger()
        root.addHandler(logging.StreamHandler())
        root.addHandler(logging.StreamHandler())
        assert len(root.handlers) >= 2
        configure_logging()
        assert len(root.handlers) == 1


# ------------------------------------------------------------------
# Timer
# ------------------------------------------------------------------


class TestTimer:
    def test_measures_duration(self):
        import time

        with Timer() as t:
            time.sleep(0.01)
        assert t.ms > 0

    def test_ms_initially_zero(self):
        with Timer() as t:
            assert t.ms == 0.0

    def test_context_manager_returns_self(self):
        timer = Timer()
        with timer as t:
            assert t is timer
