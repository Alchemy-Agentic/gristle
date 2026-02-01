"""Gristle configuration via environment variables."""

import os
from pathlib import Path

from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    model_config = {"env_prefix": "GRISTLE_", "env_file": ".env", "extra": "ignore"}

    falkordb_host: str = "localhost"
    falkordb_port: int = 6390
    falkordb_password: str | None = None

    max_file_size_bytes: int = 512_000  # 500KB
    repo_storage_path: Path = Path("./repos")

    # Directories to always skip during ingestion
    excluded_dirs: frozenset[str] = frozenset({
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
    })

    # File watcher debounce in seconds
    watcher_debounce_seconds: float = 2.0

    # Batch size for UNWIND Cypher queries during ingestion
    ingestion_batch_size: int = 200

    # MCP transport: "stdio" (local dev) or "streamable-http" (remote/Railway)
    transport: str = "stdio"

    # HTTP server settings (only used with streamable-http transport)
    http_host: str = "0.0.0.0"
    http_port: int = 8080

    # Bearer token auth — set GRISTLE_API_KEY to enable, leave unset for no auth
    api_key: str | None = None

    @property
    def effective_port(self) -> int:
        """Port for HTTP transport. Railway injects ``PORT``; use it as fallback."""
        return int(os.environ.get("PORT", self.http_port))


settings = Settings()
