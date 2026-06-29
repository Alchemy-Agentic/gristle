"""Batch collector for buffering graph writes and flushing via UNWIND."""

from __future__ import annotations

from collections import defaultdict
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient


class BatchCollector:
    """Accumulates nodes and relationships, then flushes in batched UNWIND queries.

    Usage::

        batch = BatchCollector(graph, batch_size=200)
        batch.add_node("Function", {"id": "func::foo", "name": "foo", ...})
        batch.add_relationship("CONTAINS", "file::x", "func::foo")
        batch.add_merge_relationship("CALLS", "func::a", "func::b")
        counts = batch.flush()
        # counts == {"nodes_created": N, "relationships_created": M}
    """

    def __init__(self, graph: GraphClient, batch_size: int = 200) -> None:
        self._graph = graph
        self._batch_size = batch_size
        self._nodes: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._create_rels: dict[str, list[dict[str, Any]]] = defaultdict(list)
        self._merge_rels: dict[str, list[dict[str, Any]]] = defaultdict(list)
        # The business `id` is the unique node key. Nodes are written with CREATE
        # (not MERGE) for speed, so the same id added twice would produce duplicate
        # nodes — and because ids embed the file path, collisions only happen within
        # one file (e.g. several same-named local functions), i.e. within one
        # collector. Track seen ids (persisting across flushes) and drop repeats, so
        # one id maps to exactly one node. Otherwise duplicate endpoints make every
        # MERGE relationship fan out Cartesian-style (N callers x M callees edges).
        self._seen_node_ids: set[str] = set()

    def add_node(self, label: str, properties: dict[str, Any]) -> None:
        """Buffer a node creation, skipping a repeat of an already-seen ``id``."""
        node_id = properties.get("id")
        if node_id is not None:
            if node_id in self._seen_node_ids:
                return
            self._seen_node_ids.add(node_id)
        self._nodes[label].append(properties)

    def add_relationship(
        self,
        rel_type: str,
        from_id: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Buffer a CREATE relationship."""
        item: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        if properties:
            item.update(properties)
        self._create_rels[rel_type].append(item)

    def add_merge_relationship(
        self,
        rel_type: str,
        from_id: str,
        to_id: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Buffer a MERGE (upsert) relationship."""
        item: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        if properties:
            item.update(properties)
        self._merge_rels[rel_type].append(item)

    def flush(self) -> dict[str, int]:
        """Flush all buffered operations.  Nodes first, then relationships.

        Returns counts of nodes and relationships created.  Counts are
        tracked locally (not from DB return values) for mock compatibility.
        """
        nodes_created = 0
        rels_created = 0

        # Flush nodes (must come before relationships that reference them)
        for label, items in self._nodes.items():
            for i in range(0, len(items), self._batch_size):
                chunk = items[i : i + self._batch_size]
                self._graph.batch_create_nodes(label, chunk)
                nodes_created += len(chunk)
        self._nodes.clear()

        # Flush CREATE relationships
        for rel_type, items in self._create_rels.items():
            for i in range(0, len(items), self._batch_size):
                chunk = items[i : i + self._batch_size]
                self._graph.batch_create_relationships(rel_type, chunk)
                rels_created += len(chunk)
        self._create_rels.clear()

        # Flush MERGE relationships
        for rel_type, items in self._merge_rels.items():
            for i in range(0, len(items), self._batch_size):
                chunk = items[i : i + self._batch_size]
                self._graph.batch_merge_relationships(rel_type, chunk)
                rels_created += len(chunk)
        self._merge_rels.clear()

        return {"nodes_created": nodes_created, "relationships_created": rels_created}

    @property
    def pending_count(self) -> int:
        """Total buffered items not yet flushed."""
        return (
            sum(len(v) for v in self._nodes.values())
            + sum(len(v) for v in self._create_rels.values())
            + sum(len(v) for v in self._merge_rels.values())
        )
