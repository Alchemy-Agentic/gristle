"""Tests for gristle.config — Settings validators and properties."""

from __future__ import annotations

import os

import pytest
from pydantic import ValidationError

from gristle.config import Settings


class TestPortValidation:
    def test_default_falkordb_port(self):
        s = Settings()
        assert s.falkordb_port == 6390

    def test_valid_falkordb_port(self):
        s = Settings(falkordb_port=6379)
        assert s.falkordb_port == 6379

    def test_falkordb_port_zero_rejected(self):
        with pytest.raises(ValidationError, match="Port must be between 1 and 65535"):
            Settings(falkordb_port=0)

    def test_falkordb_port_negative_rejected(self):
        with pytest.raises(ValidationError, match="Port must be between 1 and 65535"):
            Settings(falkordb_port=-1)

    def test_falkordb_port_too_high(self):
        with pytest.raises(ValidationError, match="Port must be between 1 and 65535"):
            Settings(falkordb_port=65536)

    def test_falkordb_port_boundary_low(self):
        s = Settings(falkordb_port=1)
        assert s.falkordb_port == 1

    def test_falkordb_port_boundary_high(self):
        s = Settings(falkordb_port=65535)
        assert s.falkordb_port == 65535

    def test_http_port_zero_rejected(self):
        with pytest.raises(ValidationError, match="Port must be between 1 and 65535"):
            Settings(http_port=0)

    def test_http_port_valid(self):
        s = Settings(http_port=3000)
        assert s.http_port == 3000


class TestBatchSizeValidation:
    def test_default_batch_size(self):
        s = Settings()
        assert s.ingestion_batch_size == 200

    def test_batch_size_one(self):
        s = Settings(ingestion_batch_size=1)
        assert s.ingestion_batch_size == 1

    def test_batch_size_zero_rejected(self):
        with pytest.raises(ValidationError, match="Batch size must be >= 1"):
            Settings(ingestion_batch_size=0)

    def test_batch_size_negative_rejected(self):
        with pytest.raises(ValidationError, match="Batch size must be >= 1"):
            Settings(ingestion_batch_size=-10)


class TestMaxFileSizeValidation:
    def test_default_max_file_size(self):
        s = Settings()
        assert s.max_file_size_bytes == 512_000

    def test_max_file_size_one(self):
        s = Settings(max_file_size_bytes=1)
        assert s.max_file_size_bytes == 1

    def test_max_file_size_zero_rejected(self):
        with pytest.raises(ValidationError, match="Max file size must be >= 1"):
            Settings(max_file_size_bytes=0)


class TestTransportValidation:
    def test_default_transport(self):
        s = Settings()
        assert s.transport == "stdio"

    def test_stdio_valid(self):
        s = Settings(transport="stdio")
        assert s.transport == "stdio"

    def test_streamable_http_valid(self):
        s = Settings(transport="streamable-http")
        assert s.transport == "streamable-http"

    def test_invalid_transport_rejected(self):
        with pytest.raises(ValidationError, match="Transport must be one of"):
            Settings(transport="grpc")

    def test_empty_transport_rejected(self):
        with pytest.raises(ValidationError, match="Transport must be one of"):
            Settings(transport="")


class TestEffectivePort:
    def test_defaults_to_http_port(self):
        s = Settings(http_port=9090)
        assert s.effective_port == 9090

    def test_railway_port_override(self, monkeypatch):
        monkeypatch.setenv("PORT", "5555")
        s = Settings(http_port=8080)
        assert s.effective_port == 5555


class TestPasswordAndApiKey:
    def test_password_none_by_default(self):
        s = Settings()
        assert s.falkordb_password is None

    def test_password_set(self):
        s = Settings(falkordb_password="secret")
        assert s.falkordb_password == "secret"

    def test_api_key_none_by_default(self):
        s = Settings()
        assert s.api_key is None

    def test_api_key_set(self):
        s = Settings(api_key="my-key")
        assert s.api_key == "my-key"


class TestExcludedDirs:
    def test_default_excluded_dirs(self):
        s = Settings()
        assert "node_modules" in s.excluded_dirs
        assert ".git" in s.excluded_dirs
        assert "__pycache__" in s.excluded_dirs

    def test_excluded_dirs_is_frozenset(self):
        s = Settings()
        assert isinstance(s.excluded_dirs, frozenset)
