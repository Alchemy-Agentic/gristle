"""Gristle configuration via environment variables."""

import os
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings

_VALID_TRANSPORTS = {"stdio", "streamable-http"}


class Settings(BaseSettings):
    model_config = {"env_prefix": "GRISTLE_", "env_file": ".env", "extra": "ignore"}

    falkordb_host: str = "localhost"
    falkordb_port: int = 6390
    falkordb_password: str | None = None

    max_file_size_bytes: int = 512_000  # 500KB
    repo_storage_path: Path = Path("./repos")

    # Directories to always skip during ingestion
    excluded_dirs: frozenset[str] = frozenset(
        {
            "node_modules",
            ".git",
            "__pycache__",
            ".pycache",
            "dist",
            "build",
            ".venv",
            "venv",
            ".env",
            "env",
            ".tox",
            ".mypy_cache",
            ".pytest_cache",
            ".ruff_cache",
            "egg-info",
            ".eggs",
        }
    )

    # File watcher debounce in seconds
    watcher_debounce_seconds: float = 2.0

    # Batch size for UNWIND Cypher queries during ingestion
    ingestion_batch_size: int = 200

    # MCP transport: "stdio" (local dev) or "streamable-http" (remote/Railway)
    transport: str = "stdio"

    # HTTP server settings (only used with streamable-http transport)
    # Empty string = dual-stack (IPv4 + IPv6) for Railway private networking
    http_host: str = ""
    http_port: int = 8080

    # Dependency staleness & vulnerability checking
    dependency_check_enabled: bool = True
    dependency_timeout_seconds: float = 5.0
    dependency_concurrency: int = 20

    # Bearer token auth — set GRISTLE_API_KEY to enable, leave unset for no auth
    api_key: str | None = None

    @field_validator("falkordb_port", "http_port")
    @classmethod
    def _port_in_range(cls, v: int) -> int:
        if not (1 <= v <= 65535):
            raise ValueError(f"Port must be between 1 and 65535, got {v}")
        return v

    @field_validator("ingestion_batch_size")
    @classmethod
    def _positive_batch_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Batch size must be >= 1, got {v}")
        return v

    @field_validator("max_file_size_bytes")
    @classmethod
    def _positive_file_size(cls, v: int) -> int:
        if v < 1:
            raise ValueError(f"Max file size must be >= 1, got {v}")
        return v

    @field_validator("transport")
    @classmethod
    def _valid_transport(cls, v: str) -> str:
        if v not in _VALID_TRANSPORTS:
            raise ValueError(f"Transport must be one of {_VALID_TRANSPORTS}, got {v!r}")
        return v

    @property
    def effective_port(self) -> int:
        """Port for HTTP transport. Railway injects ``PORT``; use it as fallback."""
        return int(os.environ.get("PORT", self.http_port))


settings = Settings()
