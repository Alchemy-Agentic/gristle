"""FalkorDB graph client wrapper with per-repo isolation."""

from __future__ import annotations

import hashlib
import logging
import re
from typing import Any

from falkordb import FalkorDB
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)


class QueryResult:
    """Wrapper for FalkorDB query results."""

    __slots__ = ("records", "summary")

    def __init__(self, records: list[dict[str, Any]], summary: dict[str, Any]):
        self.records = records
        self.summary = summary


class GraphClient:
    """FalkorDB client with per-repo graph isolation.

    Each repository gets its own graph namespace: ``gristle_{repo_id}``.
    """

    def __init__(
        self,
        host: str = "localhost",
        port: int = 6379,
        repo_id: str = "default",
        password: str | None = None,
    ):
        self._host = host
        self._port = port
        self._db = FalkorDB(host=host, port=port, password=password)
        self._repo_id = repo_id
        self._graph_name = f"gristle_{self._sanitize_id(repo_id)}"
        self._graph = self._db.select_graph(self._graph_name)

    @property
    def repo_id(self) -> str:
        return self._repo_id

    @property
    def graph_name(self) -> str:
        return self._graph_name

    def ping(self) -> bool:
        """Return True if the FalkorDB server is reachable.

        Used by readiness checks and ``gristle doctor`` to verify the graph
        backend is up without raising.
        """
        try:
            self._db.connection.ping()
            return True
        except Exception:
            return False

    # ------------------------------------------------------------------
    # Query execution
    # ------------------------------------------------------------------

    def execute(self, query: str, params: dict[str, Any] | None = None) -> QueryResult:
        """Execute a Cypher query and return structured results."""
        result = self._graph.query(query, params or {})
        records = []
        if result.result_set:
            # FalkorDB headers are [[type_code, name], ...] — extract names
            headers = [h[1] if isinstance(h, list) else h for h in result.header]
            for row in result.result_set:
                records.append(dict(zip(headers, row, strict=False)))
        return QueryResult(
            records=records,
            summary={
                "nodes_created": result.nodes_created,
                "relationships_created": result.relationships_created,
                "nodes_deleted": result.nodes_deleted,
                "relationships_deleted": result.relationships_deleted,
            },
        )

    # ------------------------------------------------------------------
    # Node / relationship helpers
    # ------------------------------------------------------------------

    def create_node(self, label: str, properties: dict[str, Any]) -> str | None:
        """Create a node and return its ``id`` property."""
        props_str = ", ".join(f"{k}: ${k}" for k in properties)
        query = f"CREATE (n:{label} {{{props_str}}}) RETURN n.id"
        result = self.execute(query, properties)
        return result.records[0]["n.id"] if result.records else None

    def create_relationship(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Create a relationship between two nodes matched by ``id``."""
        props_clause = ""
        if properties:
            props_inner = ", ".join(f"{k}: ${k}" for k in properties)
            props_clause = f" {{{props_inner}}}"

        query = f"MATCH (a), (b) WHERE a.id = $from_id AND b.id = $to_id CREATE (a)-[:{rel_type}{props_clause}]->(b)"
        params: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        if properties:
            params.update(properties)
        self.execute(query, params)

    def merge_relationship(
        self,
        from_id: str,
        to_id: str,
        rel_type: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """Merge (upsert) a relationship between two nodes."""
        props_clause = ""
        if properties:
            props_inner = ", ".join(f"{k}: ${k}" for k in properties)
            props_clause = f" {{{props_inner}}}"

        query = f"MATCH (a), (b) WHERE a.id = $from_id AND b.id = $to_id MERGE (a)-[:{rel_type}{props_clause}]->(b)"
        params: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        if properties:
            params.update(properties)
        self.execute(query, params)

    # ------------------------------------------------------------------
    # Batch operations (UNWIND)
    # ------------------------------------------------------------------

    def batch_create_nodes(self, label: str, items: list[dict[str, Any]]) -> int:
        """Create multiple nodes of the same label in a single UNWIND query.

        All dicts in ``items`` must have the same keys.
        Returns the number of nodes created.
        """
        if not items:
            return 0
        keys = list(items[0].keys())
        set_clauses = ", ".join(f"{k}: item.{k}" for k in keys)
        query = f"UNWIND $items AS item CREATE (n:{label} {{{set_clauses}}})"
        result = self.execute(query, {"items": items})
        return int(result.summary["nodes_created"])

    def batch_create_relationships(self, rel_type: str, items: list[dict[str, Any]]) -> int:
        """Create multiple relationships of the same type via UNWIND.

        Each item must have ``from_id`` and ``to_id`` keys.
        Additional keys become relationship properties.
        """
        if not items:
            return 0
        prop_keys = [k for k in items[0] if k not in ("from_id", "to_id")]
        props_clause = ""
        if prop_keys:
            props_inner = ", ".join(f"{k}: rel.{k}" for k in prop_keys)
            props_clause = f" {{{props_inner}}}"
        query = (
            "UNWIND $rels AS rel "
            "MATCH (a), (b) "
            "WHERE a.id = rel.from_id AND b.id = rel.to_id "
            f"CREATE (a)-[:{rel_type}{props_clause}]->(b)"
        )
        result = self.execute(query, {"rels": items})
        return int(result.summary["relationships_created"])

    def batch_merge_relationships(self, rel_type: str, items: list[dict[str, Any]]) -> int:
        """Merge (upsert) multiple relationships of the same type via UNWIND.

        Each item must have ``from_id`` and ``to_id`` keys.
        """
        if not items:
            return 0
        prop_keys = [k for k in items[0] if k not in ("from_id", "to_id")]
        props_clause = ""
        if prop_keys:
            props_inner = ", ".join(f"{k}: rel.{k}" for k in prop_keys)
            props_clause = f" {{{props_inner}}}"
        query = (
            "UNWIND $rels AS rel "
            "MATCH (a), (b) "
            "WHERE a.id = rel.from_id AND b.id = rel.to_id "
            f"MERGE (a)-[:{rel_type}{props_clause}]->(b)"
        )
        result = self.execute(query, {"rels": items})
        return int(result.summary["relationships_created"])

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def clear(self) -> None:
        """Delete all nodes and relationships in this repo's graph."""
        self.execute("MATCH (n) DETACH DELETE n")

    def drop(self) -> None:
        """Drop the entire graph for this repo."""
        try:
            self._graph.delete()
        except ResponseError:
            # Graph may not exist yet
            pass
        except ConnectionError:
            logger.warning("Cannot reach FalkorDB to drop graph %s", self._graph_name)

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _sanitize_id(raw: str) -> str:
        """Convert an arbitrary string into a safe graph-name suffix."""
        slug = re.sub(r"[^a-zA-Z0-9]", "_", raw).strip("_").lower()
        if not slug:
            slug = hashlib.sha256(raw.encode()).hexdigest()[:12]
        # Truncate long slugs but keep them recognisable
        if len(slug) > 48:
            slug = slug[:36] + "_" + hashlib.sha256(raw.encode()).hexdigest()[:8]
        return slug

    @staticmethod
    def repo_id_from_path(repo_path: str) -> str:
        """Derive a stable repo_id from a filesystem path."""
        return hashlib.sha256(repo_path.encode()).hexdigest()[:12]
