"""Graph schema: index creation and validation."""

from __future__ import annotations

import contextlib
import logging
from typing import TYPE_CHECKING

from redis.exceptions import ResponseError

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient

logger = logging.getLogger(__name__)

# Version of the graph's contents/shape. BUMP THIS whenever an ingestion change means
# existing graphs should be re-ingested to benefit — a new node/edge type, or new
# extraction that adds edges to existing types (e.g. the 0.9.0 SQL parser added
# DBFunction-[:USES_MODEL]->Model without a new label). It is NOT the package version:
# a query-only or perf release does not bump it. Consumers stamp this on their own
# graph record at ingest and re-ingest when the running service reports a higher value,
# so a gristle upgrade auto-refreshes stale graphs. Reported by /health, /ready, and
# gristle_ingest_github. History: 1 = first stamped schema (post-0.9.0 SQL edges);
# 2 = 0.10.0 recovered SQL functions tree-sitter couldn't parse (more accurate
# DBFunction-[:USES_MODEL]->Model coverage + removal of string/comment false edges);
# 3 = feature-flag graph: Flag nodes (registry + DB migrations) and Flag-[:GATES]->
# Function edges from configured flag-check call sites;
# 4 = IMPORTS edges carry a `names` list (the union of symbols each file imports from a
# target), enabling symbol-level dead-export detection instead of the file-level floor.
# 5 = inline test coverage: TestCase-[:TESTS_FUNCTION]->Function edges (depth=0) from the
# calls in it()/test() callback bodies, so describe/it blocks (which create no Function
# node) now carry precise test->production coverage and stop orphaning test helpers.
# 6 = Next.js App Router page/layout routes now link to their default-export component via
# HANDLES (previously synthesized with an inert handler_name="default" that never matched),
# so route -> page component -> CALLS/RENDERS -> USES_MODEL traces a page's data access.
# 7 = design tokens: Token nodes (CSS custom properties from :root / Tailwind v4 @theme
# blocks, categorized color/spacing/typography/radius/shadow/z_index/animation) with a
# File-[:CONTAINS]->Token home edge — the definition inventory for design-system analysis.
GRAPH_SCHEMA_VERSION = 7

# Indexes to create for efficient lookups.
# Each entry is (NodeLabel, property_name).
_INDEXES: list[tuple[str, str]] = [
    ("File", "id"),
    ("File", "path"),
    ("Function", "id"),
    ("Function", "name"),
    ("Function", "qualified_name"),
    ("Function", "file_path"),
    ("Class", "id"),
    ("Class", "name"),
    ("Class", "qualified_name"),
    ("Class", "file_path"),
    ("Import", "id"),
    ("Import", "module_path"),
    ("Document", "id"),
    ("Document", "path"),
    ("Document", "doc_type"),
    ("DocumentSection", "id"),
    ("DocumentSection", "file_path"),
    ("Route", "id"),
    ("Route", "path"),
    ("Route", "method"),
    ("Dependency", "id"),
    ("Dependency", "name"),
    ("EnvVar", "id"),
    ("EnvVar", "name"),
    ("TypeField", "id"),
    ("TypeField", "name"),
    ("Snapshot", "captured_at"),
    ("Model", "id"),
    ("Model", "name"),
    ("Model", "file_path"),
    ("Model", "orm"),
    ("ModelField", "id"),
    ("ModelField", "name"),
    ("DBFunction", "id"),
    ("DBFunction", "name"),
    ("DBFunction", "file_path"),
    ("Variable", "id"),
    ("Variable", "name"),
    ("Variable", "file_path"),
    ("Flag", "id"),
    ("Flag", "key"),
    ("TestCase", "id"),
    ("Token", "id"),
    ("Token", "name"),
    ("Token", "category"),
]

# Full-text indexes for docstring search.
_FULLTEXT_INDEXES: list[tuple[str, str, str]] = [
    # (index_name, label, property)
    ("ft_function_doc", "Function", "docstring"),
    ("ft_class_doc", "Class", "docstring"),
]


def ensure_schema(client: GraphClient) -> None:
    """Create all required indexes if they don't already exist."""
    for label, prop in _INDEXES:
        with contextlib.suppress(ResponseError):
            client.execute(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")

    for _idx_name, label, prop in _FULLTEXT_INDEXES:
        with contextlib.suppress(ResponseError):
            client.execute(f"CALL db.idx.fulltext.createNodeIndex('{label}', '{prop}')")
