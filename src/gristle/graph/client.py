"""FalkorDB graph client wrapper with per-repo isolation."""

from __future__ import annotations

import hashlib
import logging
import re
from collections import defaultdict
from typing import Any

from falkordb import FalkorDB
from redis.exceptions import ResponseError

logger = logging.getLogger(__name__)

# Node ids encode their label as a leading ``prefix::`` segment. Labeling the
# endpoints of relationship-write queries lets FalkorDB use the per-label id
# index instead of planning an unlabeled ``MATCH (a),(b)`` as a Cartesian
# product of two full node scans (the dominant ingest cost on large repos).
_ID_PREFIX_TO_LABEL: dict[str, str] = {
    "func": "Function",
    "class": "Class",
    "file": "File",
    "import": "Import",
    "route": "Route",
    "testcase": "TestCase",
    "doc": "Document",
    "docsec": "DocumentSection",
    "dep": "Dependency",
    "envvar": "EnvVar",
    "typefield": "TypeField",
    "model": "Model",
    "mf": "ModelField",
}


def _label_for_id(node_id: str) -> str | None:
    """Return the node label encoded in an id's ``prefix::`` segment, if known."""
    return _ID_PREFIX_TO_LABEL.get(node_id.split("::", 1)[0])


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

    def graph_exists(self) -> bool:
        """True if this repo's graph already exists on the server.

        Lets the server rehydrate engines for previously-ingested repos after a
        restart without a destructive re-ingest.
        """
        try:
            return self._graph_name in self._db.list_graphs()
        except Exception:
            return False

    def list_gristle_graphs(self) -> list[str]:
        """Return all ``gristle_*`` graph names present on the server."""
        try:
            return [g for g in self._db.list_graphs() if g.startswith("gristle_")]
        except Exception:
            return []

    def describe_gristle_graphs(self) -> list[dict[str, Any]]:
        """Describe every ``gristle_*`` graph on the server: identity + freshness.

        Reads each graph's most recent ingest Snapshot for its source
        ``repo_path`` and ``captured_at``, plus a node count — enough to tell
        which repository a graph belongs to and whether it's stale, so orphaned
        graphs (deleted checkouts, old worktrees) can be identified and dropped.
        Fields are None for graphs that predate snapshots or can't be read; one
        unreadable graph never hides the rest.
        """
        entries: list[dict[str, Any]] = []
        for gname in sorted(self.list_gristle_graphs()):
            entry: dict[str, Any] = {
                "repo_id": gname.removeprefix("gristle_"),
                "graph": gname,
                "repo_path": None,
                "last_ingested_at": None,
                "nodes": None,
            }
            try:
                g = self._db.select_graph(gname)
                snap = g.query(
                    "MATCH (s:Snapshot) RETURN s.repo_path, s.captured_at ORDER BY s.captured_at DESC LIMIT 1"
                ).result_set
                if snap:
                    entry["repo_path"] = snap[0][0] or None
                    entry["last_ingested_at"] = snap[0][1]
                count = g.query("MATCH (n) RETURN count(n)").result_set
                if count:
                    entry["nodes"] = count[0][0]
            except Exception:
                logger.debug("Could not read metadata for graph %s", gname, exc_info=True)
            entries.append(entry)
        return entries

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

        a, b = self._labeled_endpoints(from_id, to_id)
        query = (
            f"MATCH ({a}), ({b}) WHERE a.id = $from_id AND b.id = $to_id CREATE (a)-[:{rel_type}{props_clause}]->(b)"
        )
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
        """Merge (upsert) a relationship between two nodes.

        Properties are applied with SET (not in the MERGE pattern) so they are
        not treated as match criteria.
        """
        a, b = self._labeled_endpoints(from_id, to_id)
        rel_clause = f"MERGE (a)-[r:{rel_type}]->(b)"
        if properties:
            rel_clause += " SET " + ", ".join(f"r.{k} = ${k}" for k in properties)
        query = f"MATCH ({a}), ({b}) WHERE a.id = $from_id AND b.id = $to_id {rel_clause}"
        params: dict[str, Any] = {"from_id": from_id, "to_id": to_id}
        if properties:
            params.update(properties)
        self.execute(query, params)

    # ------------------------------------------------------------------
    # Batch operations (UNWIND)
    # ------------------------------------------------------------------

    @staticmethod
    def _labeled_endpoints(from_id: str, to_id: str) -> tuple[str, str]:
        """Build ``a[:Label]``/``b[:Label]`` match patterns from id prefixes."""
        from_label = _label_for_id(from_id)
        to_label = _label_for_id(to_id)
        a = f"a:{from_label}" if from_label else "a"
        b = f"b:{to_label}" if to_label else "b"
        return a, b

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
        return self._batch_write_relationships(rel_type, items, verb="CREATE")

    def batch_merge_relationships(self, rel_type: str, items: list[dict[str, Any]]) -> int:
        """Merge (upsert) multiple relationships of the same type via UNWIND.

        Each item must have ``from_id`` and ``to_id`` keys.
        """
        return self._batch_write_relationships(rel_type, items, verb="MERGE")

    def _batch_write_relationships(self, rel_type: str, items: list[dict[str, Any]], *, verb: str) -> int:
        """CREATE/MERGE relationships via UNWIND, grouped by endpoint label.

        Grouping by the (from, to) labels derived from id prefixes lets each
        sub-query use the per-label id index (``Node By Index Scan``) instead of
        an unlabeled ``MATCH (a),(b)`` Cartesian product. Items whose ids have an
        unknown prefix fall back to an unlabeled match.
        """
        if not items:
            return 0
        prop_keys = [k for k in items[0] if k not in ("from_id", "to_id")]

        # CREATE puts props in the pattern. MERGE must NOT — a property map inside
        # a MERGE relationship pattern is treated as match criteria and FalkorDB
        # mis-binds it across UNWIND rows; MERGE the bare edge, then SET props.
        if verb == "MERGE":
            rel_clause = f"MERGE (a)-[r:{rel_type}]->(b)"
            if prop_keys:
                rel_clause += " SET " + ", ".join(f"r.{k} = rel.{k}" for k in prop_keys)
        else:
            props_clause = ""
            if prop_keys:
                props_clause = " {" + ", ".join(f"{k}: rel.{k}" for k in prop_keys) + "}"
            rel_clause = f"CREATE (a)-[:{rel_type}{props_clause}]->(b)"

        groups: dict[tuple[str | None, str | None], list[dict[str, Any]]] = defaultdict(list)
        for item in items:
            groups[(_label_for_id(item["from_id"]), _label_for_id(item["to_id"]))].append(item)

        total = 0
        for (from_label, to_label), group in groups.items():
            a = f"a:{from_label}" if from_label else "a"
            b = f"b:{to_label}" if to_label else "b"
            query = f"UNWIND $rels AS rel MATCH ({a}), ({b}) WHERE a.id = rel.from_id AND b.id = rel.to_id {rel_clause}"
            result = self.execute(query, {"rels": group})
            total += int(result.summary["relationships_created"])
        return total

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

    @staticmethod
    def canonical_repo_path(repo_path: str) -> str:
        """Resolve a path to the repo root it should take its *identity* from.

        A git worktree is a checkout OF a repository, not a separate repository:
        its ``.git`` is a file pointing into the main repo's
        ``.git/worktrees/<name>``. Hashing the worktree's own path would give
        every worktree its own full graph (a repo with N worktrees ends up as N
        near-identical graphs with no cleanup story), so worktrees map to the
        main working tree and share its graph — a re-ingest from any worktree
        refreshes it. Passing an explicit repo_id still isolates deliberately.

        Submodules also have a ``.git`` file, but it points at
        ``.git/modules/<name>`` — a genuinely different repository — so they
        keep their own identity. So do worktrees of *bare* repos (their gitdir
        is ``<name>.git/worktrees/...`` with no main working tree to map to)
        and orphaned worktree dirs whose main repo is gone.
        """
        from pathlib import Path

        resolved = Path(repo_path).resolve()
        try:
            dotgit = resolved / ".git"
            if not dotgit.is_file():  # a normal repo has a .git *directory*
                return str(resolved)
            content = dotgit.read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^gitdir:\s*(.+)$", content, flags=re.MULTILINE)
            if not match:
                return str(resolved)
            gitdir = Path(match.group(1).strip())
            if not gitdir.is_absolute():
                gitdir = (resolved / gitdir).resolve()
            # A worktree's gitdir is <main>/.git/worktrees/<name>; recover <main>.
            # Scan from the right for a ".git"/"worktrees" adjacent pair — a
            # worktree may itself be named "worktrees", so matching the last
            # bare "worktrees" component is not enough.
            parts = [p.lower().rstrip("\\/") for p in gitdir.parts]
            for idx in range(len(parts) - 1, 0, -1):
                if parts[idx] == "worktrees" and parts[idx - 1] == ".git":
                    main = Path(*gitdir.parts[: idx - 1])
                    if main.is_dir():
                        return str(main.resolve())
                    break  # main repo gone (pruned worktree) — keep own identity
        except (OSError, ValueError):
            # ValueError covers undecodable/malformed .git contents; either way
            # we can't attribute the checkout, so it keeps its own identity.
            logger.debug("Could not inspect %s for worktree identity", repo_path, exc_info=True)
        return str(resolved)
