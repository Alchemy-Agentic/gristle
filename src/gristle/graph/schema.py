"""Graph schema: index creation and validation."""

from __future__ import annotations

from gristle.graph.client import GraphClient

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
        try:
            client.execute(f"CREATE INDEX FOR (n:{label}) ON (n.{prop})")
        except Exception:
            pass  # Index already exists

    for idx_name, label, prop in _FULLTEXT_INDEXES:
        try:
            client.execute(
                f"CALL db.idx.fulltext.createNodeIndex('{label}', '{prop}')"
            )
        except Exception:
            pass  # Index already exists or FalkorDB version doesn't support it
