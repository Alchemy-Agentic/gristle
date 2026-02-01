"""Regex-based env var reference detection in source code."""

from __future__ import annotations

import re

# Python: os.environ["X"], os.environ.get("X"), os.getenv("X")
_PY_ENV_PATTERNS = [
    re.compile(r"""os\.environ\[["']([A-Z_][A-Z0-9_]*)["']\]"""),
    re.compile(r"""os\.environ\.get\(["']([A-Z_][A-Z0-9_]*)["']"""),
    re.compile(r"""os\.getenv\(["']([A-Z_][A-Z0-9_]*)["']"""),
]

# TypeScript/JS: process.env.X, process.env["X"], Deno.env.get("X")
_TS_ENV_PATTERNS = [
    re.compile(r"process\.env\.([A-Z_][A-Z0-9_]*)"),
    re.compile(r"""process\.env\[["']([A-Z_][A-Z0-9_]*)["']\]"""),
    re.compile(r"""Deno\.env\.get\(["']([A-Z_][A-Z0-9_]*)["']"""),
]


def extract_env_var_refs(content: str, language: str) -> list[str]:
    """Extract env var names referenced in source code.

    Returns a deduplicated, sorted list of env var names.
    """
    patterns = _PY_ENV_PATTERNS if language == "python" else _TS_ENV_PATTERNS
    found: set[str] = set()
    for pat in patterns:
        found.update(pat.findall(content))
    return sorted(found)
