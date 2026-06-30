"""Static guard against FalkorDB-incompatible Cypher operators in the source.

WHY THIS EXISTS
---------------
FalkorDB does not implement the full openCypher surface. Some operators that are
valid Cypher elsewhere (Neo4j) raise a ``ResponseError`` on *every* call against
FalkorDB. Because the rest of the suite uses a mock graph client that never
executes Cypher, such an operator passes all mock tests and only fails on real
data -- e.g. ``infer_conventions`` used the regex match operator ``=~`` in its
Next.js/Supabase file-pattern checks, which made ``gristle_conventions`` error on
every Next.js repo while CI stayed green (the live smoke fixture is Python-only,
so the framework branch never ran).

This test runs in CI (no FalkorDB needed) and fails fast if a banned operator
reappears anywhere in the shipped Cypher. ``=~`` has no meaning in Python, so a
plain source scan has no false positives.
"""

from __future__ import annotations

from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src" / "gristle"

# Operators that are valid openCypher but unsupported by FalkorDB. Use
# CONTAINS / STARTS WITH / ENDS WITH instead of `=~` for string matching.
BANNED_OPERATORS = ["=~"]


@pytest.mark.parametrize("operator", BANNED_OPERATORS)
def test_no_falkordb_incompatible_operator(operator: str) -> None:
    offenders = [
        f"{path.relative_to(SRC)}:{i}"
        for path in SRC.rglob("*.py")
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if operator in line
    ]
    assert not offenders, (
        f"FalkorDB does not support the Cypher operator {operator!r}; "
        f"use CONTAINS / STARTS WITH / ENDS WITH instead. Found at: {offenders}"
    )
