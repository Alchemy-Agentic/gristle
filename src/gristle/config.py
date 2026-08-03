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

    # Graph visualization subgraphs (read-only; see docs/graph-visualization-spec.md)
    viz_max_nodes: int = 300  # cap nodes per subgraph; over -> drop lowest-degree
    viz_default_depth: int = 2  # default traversal depth for subgraph views
    viz_output_path: Path = Path("./gristle-graph.html")  # default HTML export path (CLI, P1)

    # Feature-flag detection profile — how an app's flag convention is recognised.
    # A configured convention (not a heuristic) keeps extraction precise: only calls
    # to these functions become GATES edges, so no guessing about what's a flag.
    # Defaults describe the Supabase/`useFeatureFlag` shape (homegrown DB-backed
    # flags); override via env for other conventions. Managed-lib profiles
    # (LaunchDarkly/Unleash/…) are just alternate values for these fields.
    flag_detection_enabled: bool = True
    # Functions whose argument at a given position is the flag key, mapping name ->
    # candidate key-arg indices. The key is the string literal at the FIRST listed
    # index that has one — so `useFeatureFlag('K')` (arg 0) and the server
    # `isFeatureFlagEnabled(supabase, 'K', userId)` (arg 1) both resolve, and a
    # non-key string in another position (e.g. a 4th-arg log tag) is never mistaken
    # for the key. A key passed as a variable/const at the key position is skipped
    # (a coverage limit), not guessed. `isFeatureFlagEnabled` lists (0, 1) because
    # the same name is arg 0 client-side and arg 1 server-side.
    flag_check_functions: dict[str, tuple[int, ...]] = {
        "useFeatureFlag": (0,),
        "isFeatureEnabled": (0,),
        "isFeatureFlagEnabled": (0, 1),
        "isFlagEnabledForUser": (1,),
    }
    # Tables whose rows ARE flags: `.from('<table>').eq('id','K')` reads a flag, and
    # SQL `INSERT/DELETE` on the table declare/retire flag rows. `id` is the key col.
    flag_tables: frozenset[str] = frozenset({"feature_flags"})
    # `const <symbol> = { KEY: true/false, ... } as const` objects that declare the
    # client-side flag registry (key + default value + doc comment).
    flag_registry_symbols: frozenset[str] = frozenset({"featureFlags"})
    # Objects whose SCREAMING_SNAKE member access reads a flag — `featureFlags.KEY`
    # and the runtime cache `runtimeFlagCache.KEY`. Captures the wrapper-accessor
    # surface (isXEnabled() helpers) so a flag read only through its wrapper still
    # gets a GATES edge instead of looking dead. Defaults include the registry
    # symbols plus the conventional runtime cache.
    flag_accessor_symbols: frozenset[str] = frozenset({"featureFlags", "runtimeFlagCache"})

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
