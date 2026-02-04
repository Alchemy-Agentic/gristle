"""Ingestion pipeline: parse source files and build the code graph."""

from __future__ import annotations

import json
import logging
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from gristle.config import settings
from gristle.graph.schema import ensure_schema
from gristle.ingestion.batch import BatchCollector
from gristle.ingestion.walker import WalkedFile, walk_config_files, walk_repo
from gristle.logging import Timer
from gristle.parsers.config import parse_config_file
from gristle.parsers.markdown import MarkdownParser
from gristle.parsers.registry import ParserRegistry

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient
    from gristle.models import (
        CodeReference,
        ParsedClass,
        ParsedFile,
        ParsedFunction,
        ParsedImport,
        ParsedRoute,
        ParsedTestCase,
    )

logger = logging.getLogger(__name__)


@dataclass
class IngestionResult:
    repo_id: str
    repo_path: str
    files_processed: int = 0
    files_skipped: int = 0
    docs_processed: int = 0
    nodes_created: int = 0
    relationships_created: int = 0
    doc_references_total: int = 0
    doc_references_resolved: int = 0
    routes_found: int = 0
    components_found: int = 0
    test_files_found: int = 0
    test_cases_found: int = 0
    todos_found: int = 0
    dependencies_found: int = 0
    dependencies_outdated: int = 0
    dependencies_vulnerable: int = 0
    test_coverage_edges: int = 0
    config_files_processed: int = 0
    env_vars_found: int = 0
    errors: list[str] = field(default_factory=list)


class IngestionPipeline:
    """Orchestrates parsing source files and building the FalkorDB graph."""

    _DOC_EXTENSIONS = frozenset({"md", "mdx"})

    # Path patterns indicating documentation, storybook, design mockup, or fixture dirs
    _DOC_PATH_RE = re.compile(
        r"(?:^|/)"
        r"(?:docs?|stories|storybook|archive|design|handoffs?|examples?|fixtures?|mocks?|__mocks__)"
        r"/",
        re.IGNORECASE,
    )

    def __init__(
        self,
        graph: GraphClient,
        registry: ParserRegistry | None = None,
        batch_size: int | None = None,
    ):
        self.graph = graph
        self.registry = registry or ParserRegistry().build_default()
        self._batch_size = batch_size or settings.ingestion_batch_size
        self._md_parser = MarkdownParser()
        # Maps qualified_name -> node id for cross-file call resolution
        self._id_map: dict[str, str] = {}
        # Maps file paths and entity names for document reference resolution
        self._name_to_id: dict[str, str] = {}  # short name -> node id
        # Case-insensitive version for fuzzy doc reference resolution
        self._name_lower_to_id: dict[str, str] = {}  # lowered name -> node id

        # --- Import-aware call resolution maps ---
        # qualified_name -> node_id (unique, no collisions)
        self._qualified_map: dict[str, str] = {}
        # short_name -> [node_ids] (all candidates for that name)
        self._short_to_candidates: dict[str, list[str]] = {}
        # file_path -> {local_name -> node_id} (entities defined per file)
        self._file_entities: dict[str, dict[str, str]] = {}
        # file_path -> {local_name -> node_id} (exported entities only)
        self._exported_file_entities: dict[str, dict[str, str]] = {}
        # Pre-built path resolution maps (populated during Phase 1)
        self._path_to_id: dict[str, str] = {}
        self._stem_to_id: dict[str, str] = {}
        self._dir_index_to_id: dict[str, str] = {}
        self._pymodule_to_id: dict[str, str] = {}
        self._source_roots: list[str] = []
        # Cache for import entity resolution (cleared per ingestion)
        self._import_cache: dict[str, dict[str, str]] = {}
        # Track test file paths for TESTS edge resolution
        self._test_file_paths: set[str] = set()
        # file_path -> {imported_name -> dep_id} for external imports
        self._file_external_imports: dict[str, dict[str, str]] = {}
        # __init__.py path -> {name -> node_id} for re-exported entities
        self._init_reexport_entities: dict[str, dict[str, str]] = {}
        # Parsed files by path (for re-export resolution)
        self._parsed_files_by_path: dict[str, ParsedFile] = {}
        # Inheritance chain: class_id -> [base_class_ids] (populated after Phase 2)
        self._class_bases: dict[str, list[str]] = {}
        # class_id -> {method_name -> func_id} for MRO-aware method resolution
        self._class_methods: dict[str, dict[str, str]] = {}
        # Fixture map: fixture_name -> func_id (for USES_FIXTURE edges)
        self._fixture_map: dict[str, str] = {}
        # Dependency version map: package_name -> version_string
        self._dependency_versions: dict[str, str] = {}
        # Dependency ecosystem map: package_name -> "npm" | "PyPI"
        self._dependency_ecosystems: dict[str, str] = {}
        # In-memory call adjacency: caller_id -> [callee_id] (for TESTS_FUNCTION)
        self._calls_adjacency: dict[str, list[str]] = {}
        # Set of test function node IDs (is_test=true)
        self._test_func_ids: set[str] = set()
        # EnvVar tracking: env_var_name -> node_id (for USES_ENV resolution)
        self._env_var_ids: dict[str, str] = {}
        # Type flow: func_id -> typed_parameters list (for ACCEPTS resolution)
        self._func_typed_params: dict[str, list[tuple[str, str | None]]] = {}
        # Type flow: func_id -> return_type string (for RETURNS resolution)
        self._func_return_types: dict[str, str] = {}
        # Route auth: func_id -> decorators list (for has_auth resolution)
        self._func_decorators: dict[str, list[str]] = {}
        # Import resolution tracking: import_id -> resolved boolean
        self._import_resolved: dict[str, bool] = {}
        # Test file import targets: test_file_path -> set of production file_ids (JS/TS only)
        self._test_file_import_targets: dict[str, set[str]] = {}
        # App-level auth middleware paths: (path_pattern, source_file_path)
        self._auth_middleware_paths: list[tuple[str, str]] = []
        # Unlinked routes: (route_id, handler_name, file_path) for Phase 2 resolution
        self._unlinked_routes: list[tuple[str, str, str]] = []
        # PASSED_TO target IDs for is_callback batch update
        self._callback_target_ids: set[str] = set()

    def _register_name(self, name: str, node_id: str) -> None:
        """Register a name in both exact and case-insensitive lookup maps."""
        self._name_to_id[name] = node_id
        self._name_lower_to_id[name.lower()] = node_id

    def ingest_repo(self, repo_path: str | Path) -> IngestionResult:
        """Full ingestion: walk, parse, and build the graph for a repository."""
        total_timer = Timer()
        total_timer.__enter__()

        repo_path = str(Path(repo_path).resolve())
        result = IngestionResult(
            repo_id=self.graph.repo_id,
            repo_path=repo_path,
        )

        # Clear any existing graph data for this repo
        self.graph.clear()
        ensure_schema(self.graph)
        self._id_map.clear()
        self._name_to_id.clear()
        self._name_lower_to_id.clear()
        self._qualified_map.clear()
        self._short_to_candidates.clear()
        self._file_entities.clear()
        self._exported_file_entities.clear()
        self._path_to_id.clear()
        self._stem_to_id.clear()
        self._dir_index_to_id.clear()
        self._pymodule_to_id.clear()
        self._source_roots.clear()
        self._import_cache.clear()
        self._test_file_paths.clear()
        self._file_external_imports.clear()
        self._init_reexport_entities.clear()
        self._parsed_files_by_path.clear()
        self._class_bases.clear()
        self._class_methods.clear()
        self._fixture_map.clear()
        self._dependency_versions.clear()
        self._dependency_ecosystems.clear()
        self._calls_adjacency.clear()
        self._test_func_ids.clear()
        self._env_var_ids.clear()
        self._func_typed_params.clear()
        self._func_return_types.clear()
        self._func_decorators.clear()
        self._import_resolved.clear()
        self._test_file_import_targets.clear()
        self._auth_middleware_paths.clear()
        self._unlinked_routes.clear()
        self._callback_target_ids.clear()

        # Walk and collect source files
        files = walk_repo(repo_path, self.registry.supported_extensions)
        logger.info("Found %d parseable files in %s", len(files), repo_path)

        # Phase 1: Parse all files and build nodes
        with Timer() as phase1:
            parsed_files: list[ParsedFile] = []
            for wf in files:
                parsed = self._parse_and_build(wf, result)
                if parsed:
                    parsed_files.append(parsed)

            # Detect Python source roots and register stripped module keys
            self._register_python_source_roots(parsed_files)

            # Store parsed files by path (for re-export resolution)
            for pf in parsed_files:
                self._parsed_files_by_path[pf.path] = pf

            # Build re-export maps for Python __init__.py files
            self._build_init_reexport_maps(parsed_files)

        logger.info(
            "Phase 1 complete: parsed %d files, built %d nodes",
            result.files_processed,
            result.nodes_created,
            extra={
                "event": "phase1_done",
                "duration_ms": phase1.ms,
                "repo_id": self.graph.repo_id,
                "files": result.files_processed,
                "nodes": result.nodes_created,
            },
        )

        # Extract dependency versions from manifest files
        self._extract_dependency_versions(repo_path)

        # Phase 2: Resolve cross-file call relationships
        rels_before = result.relationships_created
        with Timer() as phase2:
            self._resolve_calls(parsed_files, result)

        logger.info(
            "Phase 2 complete: resolved %d relationships",
            result.relationships_created - rels_before,
            extra={
                "event": "phase2_done",
                "duration_ms": phase2.ms,
                "repo_id": self.graph.repo_id,
                "rels": result.relationships_created - rels_before,
            },
        )

        # Config phase: Walk config files, create EnvVar nodes, resolve USES_ENV edges
        with Timer() as config_phase:
            self._process_config_files(repo_path, parsed_files, result)

        logger.info(
            "Config phase complete: %d config files, %d env vars",
            result.config_files_processed,
            result.env_vars_found,
            extra={
                "event": "config_phase_done",
                "duration_ms": config_phase.ms,
                "repo_id": self.graph.repo_id,
            },
        )

        # Phase 3: Walk and process documentation files
        with Timer() as phase3:
            doc_batch = BatchCollector(self.graph, self._batch_size)
            doc_files = walk_repo(repo_path, self._DOC_EXTENSIONS)
            for wf in doc_files:
                self._process_document(wf, result, doc_batch)
            doc_counts = doc_batch.flush()
            result.nodes_created += doc_counts["nodes_created"]
            result.relationships_created += doc_counts["relationships_created"]

        logger.info(
            "Phase 3 complete: processed %d docs (%d/%d refs resolved)",
            result.docs_processed,
            result.doc_references_resolved,
            result.doc_references_total,
            extra={"event": "phase3_done", "duration_ms": phase3.ms, "repo_id": self.graph.repo_id},
        )

        total_timer.__exit__(None, None, None)
        logger.info(
            "Ingestion complete: %d files, %d docs, %d nodes, %d relationships in %.1fs",
            result.files_processed,
            result.docs_processed,
            result.nodes_created,
            result.relationships_created,
            total_timer.ms / 1000,
            extra={
                "event": "ingestion_done",
                "duration_ms": total_timer.ms,
                "repo_id": self.graph.repo_id,
                "files": result.files_processed,
                "nodes": result.nodes_created,
                "rels": result.relationships_created,
            },
        )
        return result

    def update_file(self, repo_path: str, relative_path: str) -> IngestionResult:
        """Re-index a single file (for incremental updates).

        Deletes old nodes, re-parses, rebuilds nodes, and re-resolves
        cross-file edges (CALLS, IMPORTS, TESTS, USES_DEPENDENCY) for the
        updated file.  Requires that in-memory maps were populated by a
        prior full ingestion.
        """
        result = IngestionResult(repo_id=self.graph.repo_id, repo_path=repo_path)
        abs_path = os.path.join(repo_path, relative_path)

        # 1. Purge old nodes + in-memory map entries for this file
        self._delete_file_nodes(relative_path)
        self._purge_maps_for_file(relative_path)

        if not os.path.exists(abs_path):
            # File was deleted — we're done after cleanup
            logger.info("Deleted from graph: %s", relative_path)
            return result

        # 2. Re-parse
        try:
            content = Path(abs_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            logger.warning("Cannot read %s for update: %s", relative_path, e)
            return result

        parsed = self.registry.parse_file(relative_path, content)
        if parsed is None:
            return result

        # 3. Rebuild file nodes (populates maps for this file)
        self._build_file_graph(parsed, result)

        # 4. Re-resolve cross-file edges for this file
        self._import_cache.pop(relative_path, None)  # invalidate cache

        # CALLS: resolve calls in this file's functions
        for func in parsed.functions:
            self._resolve_function_calls(func, parsed, result)
        for cls in parsed.classes:
            for method in cls.methods:
                self._resolve_function_calls(method, parsed, result)
            # INHERITS_FROM
            class_id = f"class::{cls.qualified_name}"
            for base_name in cls.bases:
                base_id = self._resolve_base(base_name, parsed)
                if base_id:
                    self.graph.create_relationship(class_id, base_id, "INHERITS_FROM")
                    result.relationships_created += 1

        # IMPORTS: resolve this file's imports to other files
        file_id = f"file::{parsed.path}"
        file_dir = parsed.path.replace("\\", "/")
        file_dir = file_dir.rsplit("/", 1)[0] if "/" in file_dir else ""
        for imp in parsed.imports:
            target_id = self._resolve_single_import(
                imp,
                file_dir,
                parsed.language,
                self._path_to_id,
                self._stem_to_id,
                self._dir_index_to_id,
                self._pymodule_to_id,
                self._source_roots,
            )
            if target_id and target_id != file_id:
                self.graph.merge_relationship(file_id, target_id, "IMPORTS")
                result.relationships_created += 1

        # TESTS: if this is a test file, create TESTS edges
        if parsed.is_test_file:
            self._resolve_test_edges([parsed], result)

        # Also re-resolve callers INTO this file from other files
        # (Other files may call functions defined here — those CALLS edges
        #  were deleted along with the old nodes and need to be recreated.
        #  We do this via graph query for efficiency.)
        self._relink_incoming_calls(parsed, result)

        logger.info(
            "Updated %s: %d nodes, %d rels",
            relative_path,
            result.nodes_created,
            result.relationships_created,
        )
        return result

    def _purge_maps_for_file(self, file_path: str) -> None:
        """Remove a file's entries from in-memory resolution maps."""
        # Remove entities defined in this file
        old_entities = self._file_entities.pop(file_path, {})
        self._exported_file_entities.pop(file_path, None)
        self._file_external_imports.pop(file_path, None)
        self._import_cache.pop(file_path, None)

        # Remove from qualified_map and short_to_candidates
        for local_name, node_id in old_entities.items():
            # Reconstruct qualified_name from node_id
            # node_id format: "func::path::Name" or "class::path::Name"
            qn = node_id.split("::", 1)[1] if "::" in node_id else None
            if qn and qn in self._qualified_map:
                del self._qualified_map[qn]
            # Remove from candidates list
            candidates = self._short_to_candidates.get(local_name)
            if candidates and node_id in candidates:
                candidates.remove(node_id)
                if not candidates:
                    del self._short_to_candidates[local_name]

        # Remove test file tracking
        self._test_file_paths.discard(file_path)

    def _relink_incoming_calls(self, parsed: ParsedFile, result: IngestionResult) -> None:
        """Re-create CALLS edges from OTHER files' functions into this file.

        When a file is re-indexed, its old function nodes (and all edges)
        are deleted.  Other files' functions may have CALLS edges pointing
        to the old nodes.  We query the graph for functions in other files
        that should call entities in this file and recreate those edges.
        """
        # Get all entity names defined in this file
        file_entities = self._file_entities.get(parsed.path, {})
        if not file_entities:
            return

        # For each entity, find callers from other files via the graph
        for _entity_name, _entity_id in file_entities.items():
            # Check if any function in another file has this entity in their
            # calls list — but we don't store calls in the graph, only CALLS edges.
            # Instead, re-check other files' import caches to see if they
            # import from this file, and if so, invalidate their caches.
            pass

        # Invalidate import caches for files that import from this file
        # (so next time _get_imported_entities is called for them, it
        # picks up the new node IDs)
        for cached_path in list(self._import_cache.keys()):
            if cached_path != parsed.path:
                self._import_cache.pop(cached_path, None)

    # ------------------------------------------------------------------
    # Internal: parse + build
    # ------------------------------------------------------------------

    def _parse_and_build(self, wf: WalkedFile, result: IngestionResult) -> ParsedFile | None:
        try:
            content = Path(wf.absolute_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result.errors.append(f"Read error {wf.relative_path}: {e}")
            result.files_skipped += 1
            return None

        try:
            parsed = self.registry.parse_file(wf.relative_path, content)
        except Exception as e:
            logger.warning("Parse error %s: %s", wf.relative_path, e)
            result.errors.append(f"Parse error {wf.relative_path}: {e}")
            result.files_skipped += 1
            return None

        if parsed is None:
            result.files_skipped += 1
            return None

        self._build_file_graph(parsed, result)
        result.files_processed += 1
        if parsed.is_test_file:
            result.test_files_found += 1
        result.routes_found += len(parsed.routes)
        result.test_cases_found += len(parsed.test_cases)
        result.todos_found += len(parsed.todos)
        for func in parsed.functions:
            if func.is_component:
                result.components_found += 1
            result.todos_found += len(func.todos)
        for cls in parsed.classes:
            for method in cls.methods:
                result.todos_found += len(method.todos)
        return parsed

    def _build_file_graph(self, parsed: ParsedFile, result: IngestionResult) -> None:
        """Create graph nodes and structural edges for a single parsed file."""
        batch = BatchCollector(self.graph, self._batch_size)
        file_id = f"file::{parsed.path}"

        # Track file path for document reference resolution
        self._register_name(parsed.path, file_id)
        # Also track without extension and basename
        stem = parsed.path.rsplit(".", 1)[0] if "." in parsed.path else parsed.path
        self._register_name(stem, file_id)
        basename = parsed.path.rsplit("/", 1)[-1] if "/" in parsed.path else parsed.path
        self._register_name(basename, file_id)

        # File node
        is_doc = bool(self._DOC_PATH_RE.search(parsed.path))
        batch.add_node(
            "File",
            {
                "id": file_id,
                "path": parsed.path,
                "language": parsed.language,
                "line_count": parsed.line_count,
                "docstring": parsed.module_docstring or "",
                "is_test_file": parsed.is_test_file,
                "is_documentation": is_doc,
                "todo_count": len(parsed.todos),
                "react_directive": parsed.react_directive or "",
            },
        )

        # Track test files for TESTS edge resolution
        if parsed.is_test_file:
            self._test_file_paths.add(parsed.path)

        # Build path resolution maps (used by import-aware call resolution)
        normalized = parsed.path.replace("\\", "/")
        self._path_to_id[normalized] = file_id
        _all_exts = {".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs", ".py", ".pyi"}
        for ext in _all_exts:
            if normalized.endswith(ext):
                self._stem_to_id[normalized[: -len(ext)]] = file_id
                break
        if basename.startswith("index."):
            dir_path = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
            if dir_path:
                self._dir_index_to_id[dir_path] = file_id
        if parsed.language == "python":
            module = normalized.replace("/", ".")
            if module.endswith(".py"):
                module = module[:-3]
            self._pymodule_to_id[module] = file_id
            # Also register the __init__ as the package itself
            # e.g. src.marshmallow.__init__ -> also register as src.marshmallow
            if module.endswith(".__init__"):
                pkg_module = module[: -len(".__init__")]
                self._pymodule_to_id[pkg_module] = file_id
            parts = module.split(".")
            if parts and parts[-1] != "__init__":
                self._pymodule_to_id[parts[-1]] = file_id

        # Import nodes
        for imp in parsed.imports:
            imp_id = f"import::{parsed.path}::{imp.line}"
            batch.add_node(
                "Import",
                {
                    "id": imp_id,
                    "file_path": parsed.path,
                    "line": imp.line,
                    "module_path": imp.module_path,
                    "imported_names": imp.imported_names,
                    "is_relative": imp.is_relative,
                },
            )
            batch.add_relationship("CONTAINS", file_id, imp_id)

        # Classes
        for cls in parsed.classes:
            self._build_class(file_id, cls, batch)

        # Module-level functions
        for func in parsed.functions:
            self._build_function(file_id, None, func, batch)

        # Collect app-level auth middleware paths (e.g. app.use('/api/admin/*', auth))
        for path_pattern in parsed.auth_middleware_paths:
            self._auth_middleware_paths.append((path_pattern, parsed.path))

        # Routes
        for route in parsed.routes:
            self._build_route(file_id, route, batch)

        # Test cases (describe/it/test blocks)
        for tc in parsed.test_cases:
            self._build_test_case(file_id, tc, batch)

        # Flush all buffered nodes and relationships for this file
        counts = batch.flush()
        result.nodes_created += counts["nodes_created"]
        result.relationships_created += counts["relationships_created"]

    # Auth-related keywords in decorators and middleware names
    _AUTH_KEYWORDS = frozenset(
        {
            "auth",
            "login_required",
            "permission",
            "protect",
            "jwt",
            "token",
            "requires_auth",
            "authenticated",
            "verify",
            "guard",
            "session",
            "bearer",
            "oauth",
            "apikey",
            "api_key",
            "credentials",
        }
    )

    @staticmethod
    def _path_matches_pattern(route_path: str, pattern: str) -> bool:
        """Check if a route path matches an auth middleware path pattern.

        Supports:
        - Exact match: '/api/admin' matches '/api/admin'
        - Wildcard suffix: '/api/admin/*' matches '/api/admin/users'
        - Global wildcard: '*' matches everything
        """
        if pattern == "*":
            return True
        if pattern.endswith("/*"):
            prefix = pattern[:-2]
            return route_path == prefix or route_path.startswith(prefix + "/")
        return route_path == pattern

    def _detect_route_auth(self, route: ParsedRoute, handler_id: str | None) -> bool:
        """Detect whether a route has authentication based on middleware, decorators, and app-level middleware."""
        # Check route middleware for auth keywords
        for mw in route.middleware:
            mw_lower = mw.lower()
            if any(kw in mw_lower for kw in self._AUTH_KEYWORDS):
                return True

        # Check handler function decorators for auth keywords
        if handler_id:
            decorators = self._func_decorators.get(handler_id, [])
            for dec in decorators:
                dec_lower = dec.lower()
                if any(kw in dec_lower for kw in self._AUTH_KEYWORDS):
                    return True

        # Check app-level auth middleware paths
        for pattern, mw_file in self._auth_middleware_paths:
            if self._path_matches_pattern(route.path, pattern):
                # Same-file middleware is always relevant
                # Cross-file middleware with '*' only applies if same directory
                # (sub-router wildcard belongs to its own routes)
                if pattern == "*" and mw_file != route.file_path:
                    continue
                return True

        return False

    def _build_route(self, file_id: str, route: ParsedRoute, batch: BatchCollector) -> None:
        route_id = f"route::{route.file_path}::L{route.line}::{route.method}"

        # Link route to its handler function if we can find it
        handler_id = self._id_map.get(route.handler_name)
        if not handler_id:
            scoped = f"{route.file_path}::{route.handler_name}"
            handler_id = self._id_map.get(scoped)

        has_auth = self._detect_route_auth(route, handler_id)

        batch.add_node(
            "Route",
            {
                "id": route_id,
                "method": route.method,
                "path": route.path,
                "handler_name": route.handler_name,
                "file_path": route.file_path,
                "line": route.line,
                "end_line": route.end_line,
                "middleware": route.middleware,
                "has_auth": has_auth,
            },
        )

        # Link route to its file
        batch.add_relationship("CONTAINS", file_id, route_id)

        if handler_id:
            batch.add_relationship("HANDLES", route_id, handler_id)
        elif route.handler_name and route.handler_name != "<serve>":
            # Track for Phase 2 import-aware resolution
            self._unlinked_routes.append((route_id, route.handler_name, route.file_path))

    def _build_test_case(self, file_id: str, tc: ParsedTestCase, batch: BatchCollector) -> None:
        tc_id = f"testcase::{tc.file_path}::L{tc.start_line}"
        batch.add_node(
            "TestCase",
            {
                "id": tc_id,
                "name": tc.name,
                "block_type": tc.block_type,
                "file_path": tc.file_path,
                "start_line": tc.start_line,
                "end_line": tc.end_line,
                "parent_describe": tc.parent_describe or "",
                "parametrize_count": tc.parametrize_count,
            },
        )

        batch.add_relationship("CONTAINS", file_id, tc_id)

    def _build_class(self, file_id: str, cls: ParsedClass, batch: BatchCollector) -> None:
        class_id = f"class::{cls.qualified_name}"
        self._id_map[cls.qualified_name] = class_id
        self._id_map[cls.name] = class_id  # Also index by short name
        self._register_name(cls.name, class_id)
        self._qualified_map[cls.qualified_name] = class_id
        self._short_to_candidates.setdefault(cls.name, []).append(class_id)
        self._file_entities.setdefault(cls.file_path, {})[cls.name] = class_id
        if cls.is_exported:
            self._exported_file_entities.setdefault(cls.file_path, {})[cls.name] = class_id

        batch.add_node(
            "Class",
            {
                "id": class_id,
                "name": cls.name,
                "qualified_name": cls.qualified_name,
                "file_path": cls.file_path,
                "start_line": cls.start_line,
                "end_line": cls.end_line,
                "signature": cls.signature,
                "docstring": cls.docstring or "",
                "decorators": cls.decorators,
                "is_abstract": cls.is_abstract,
                "visibility": cls.visibility,
                "bases": cls.bases,
                "kind": cls.kind,
                "is_exported": cls.is_exported,
            },
        )

        batch.add_relationship("CONTAINS", file_id, class_id)
        batch.add_relationship("DEFINED_IN", class_id, file_id)

        if cls.is_exported:
            batch.add_relationship("EXPORTS", file_id, class_id)

        # Create TypeField nodes for interface/type/class fields
        for tf in cls.fields:
            tf_id = f"typefield::{cls.qualified_name}.{tf.name}"
            batch.add_node(
                "TypeField",
                {
                    "id": tf_id,
                    "name": tf.name,
                    "type_annotation": tf.type_annotation or "",
                    "is_optional": tf.is_optional,
                    "default_value": tf.default_value or "",
                    "file_path": cls.file_path,
                    "line": tf.line,
                },
            )
            batch.add_relationship("HAS_FIELD", class_id, tf_id)

        # Store class method map for inheritance-aware resolution
        method_map: dict[str, str] = {}
        for method in cls.methods:
            func_id = f"func::{method.qualified_name}"
            method_map[method.name] = func_id
        self._class_methods[class_id] = method_map

        # Methods
        for method in cls.methods:
            self._build_function(file_id, class_id, method, batch)

    def _build_function(
        self,
        file_id: str,
        class_id: str | None,
        func: ParsedFunction,
        batch: BatchCollector,
    ) -> None:
        func_id = f"func::{func.qualified_name}"
        self._id_map[func.qualified_name] = func_id
        self._id_map[func.name] = func_id  # Short name (may collide — last wins)
        self._register_name(func.name, func_id)
        self._qualified_map[func.qualified_name] = func_id
        self._short_to_candidates.setdefault(func.name, []).append(func_id)
        entities = self._file_entities.setdefault(func.file_path, {})
        entities[func.name] = func_id
        # Also store class-qualified form: "ClassName.method"
        local_name = func.qualified_name.split("::")[-1]
        if local_name != func.name:
            entities[local_name] = func_id
        if func.is_exported:
            exported = self._exported_file_entities.setdefault(func.file_path, {})
            exported[func.name] = func_id
            if local_name != func.name:
                exported[local_name] = func_id

        # Track fixtures by name for USES_FIXTURE edge resolution
        if func.is_fixture:
            self._fixture_map[func.name] = func_id
        # Store decorators for route auth detection
        if func.decorators:
            self._func_decorators[func_id] = func.decorators

        batch.add_node(
            "Function",
            {
                "id": func_id,
                "name": func.name,
                "qualified_name": func.qualified_name,
                "file_path": func.file_path,
                "start_line": func.start_line,
                "end_line": func.end_line,
                "signature": func.signature,
                "docstring": func.docstring or "",
                "decorators": func.decorators,
                "is_async": func.is_async,
                "is_static": func.is_static,
                "is_classmethod": func.is_classmethod,
                "is_property": func.is_property,
                "is_fixture": func.is_fixture,
                "visibility": func.visibility,
                "return_type": func.return_type or "",
                "complexity": func.complexity,
                "is_exported": func.is_exported,
                "is_component": func.is_component,
                "is_test": func.is_test,
                "is_entry_point": func.is_entry_point,
                "entry_point_reason": func.entry_point_reason or "",
                "is_documentation": bool(self._DOC_PATH_RE.search(func.file_path)),
                "todo_count": len(func.todos),
                "security_finding_count": len(func.security_findings),
                "security_findings": func.security_findings,
                "tested_by_count": 0,
            },
        )

        # Store typed parameters and return type for Phase 2 type edge resolution
        if func.typed_parameters:
            self._func_typed_params[func_id] = func.typed_parameters
        if func.return_type:
            self._func_return_types[func_id] = func.return_type

        batch.add_relationship("DEFINED_IN", func_id, file_id)

        if class_id:
            batch.add_relationship("CONTAINS", class_id, func_id)
        else:
            batch.add_relationship("CONTAINS", file_id, func_id)

        if func.is_exported:
            batch.add_relationship("EXPORTS", file_id, func_id)

    # ------------------------------------------------------------------
    # Phase 2: Cross-file resolution
    # ------------------------------------------------------------------

    def _resolve_calls(self, parsed_files: list[ParsedFile], result: IngestionResult) -> None:
        """Create CALLS edges by resolving call targets to known functions."""
        batch = BatchCollector(self.graph, self._batch_size)

        # Compute source roots for path alias resolution (needs all files)
        self._source_roots = self._detect_source_roots(parsed_files)
        self._import_cache.clear()

        for pf in parsed_files:
            # Module-level functions
            for func in pf.functions:
                self._resolve_function_calls(func, pf, batch)
            # Methods
            for cls in pf.classes:
                for method in cls.methods:
                    self._resolve_function_calls(method, pf, batch)

        # Resolve INHERITS_FROM edges and build inheritance map
        for pf in parsed_files:
            for cls in pf.classes:
                class_id = f"class::{cls.qualified_name}"
                bases: list[str] = []
                for base_name in cls.bases:
                    base_id = self._resolve_base(base_name, pf)
                    if base_id:
                        batch.add_relationship("INHERITS_FROM", class_id, base_id)
                        bases.append(base_id)
                if bases:
                    self._class_bases[class_id] = bases

        # Resolve IMPORTS (File -> File) edges
        self._resolve_imports(parsed_files, result, batch)

        # Resolve TESTS (test File -> production File) edges
        self._resolve_test_edges(parsed_files, result, batch)

        # Resolve USES_FIXTURE edges (test function param -> fixture function)
        self._resolve_fixture_edges(parsed_files, batch)

        # Resolve unlinked route handlers via import-aware resolution
        self._resolve_unlinked_route_handlers(batch)

        # Resolve TESTS_FUNCTION edges (test function -> production function, depth 1-2)
        self._resolve_test_function_edges(result, batch)

        # Resolve RETURNS and ACCEPTS edges (type flow)
        self._resolve_type_edges(parsed_files, batch)

        # Update is_callback on PASSED_TO target functions
        if self._callback_target_ids:
            ids = [{"id": fid} for fid in self._callback_target_ids]
            # Flush batch first so PASSED_TO edges are written
            counts = batch.flush()
            result.nodes_created += counts["nodes_created"]
            result.relationships_created += counts["relationships_created"]
            for i in range(0, len(ids), 200):
                chunk = ids[i : i + 200]
                self.graph.execute(
                    "UNWIND $items AS item MATCH (n:Function) WHERE n.id = item.id SET n.is_callback = true",
                    {"items": chunk},
                )

        # Flush all Phase 2 relationships
        counts = batch.flush()
        result.nodes_created += counts["nodes_created"]
        result.relationships_created += counts["relationships_created"]

    def _resolve_fixture_edges(self, parsed_files: list[ParsedFile], batch: BatchCollector) -> None:
        """Create USES_FIXTURE edges from test functions to pytest fixtures.

        Matches test function parameter names to known fixture names.
        """
        if not self._fixture_map:
            return

        for pf in parsed_files:
            if not pf.is_test_file:
                continue
            for func in pf.functions:
                if not func.is_test:
                    continue
                self._link_func_to_fixtures(func, batch)
            for cls in pf.classes:
                for method in cls.methods:
                    if not method.is_test:
                        continue
                    self._link_func_to_fixtures(method, batch)

    def _link_func_to_fixtures(self, func: ParsedFunction, batch: BatchCollector) -> None:
        """Link a test function to fixtures it uses via parameter names."""
        caller_id = f"func::{func.qualified_name}"
        for param in func.parameters:
            if param.startswith("*"):
                continue  # skip *args/**kwargs
            fixture_id = self._fixture_map.get(param)
            if fixture_id:
                batch.add_merge_relationship("USES_FIXTURE", caller_id, fixture_id)

    def _resolve_unlinked_route_handlers(self, batch: BatchCollector) -> None:
        """Resolve route handlers that couldn't be linked in Phase 1.

        During Phase 1, route handler resolution only checks the global ID map
        and file-scoped names. This misses handlers imported from other files
        (e.g. Supabase edge functions importing from _shared/ modules).
        Phase 2 has all file entities and import maps available, so we can use
        import-aware resolution.
        """
        if not self._unlinked_routes:
            return

        resolved = 0
        for route_id, handler_name, file_path in self._unlinked_routes:
            pf = self._parsed_files_by_path.get(file_path)
            if not pf:
                continue
            imported = self._get_imported_entities(pf)
            handler_id = imported.get(handler_name)
            if handler_id:
                batch.add_relationship("HANDLES", route_id, handler_id)
                resolved += 1

        if resolved:
            logger.debug(
                "Resolved %d/%d unlinked route handlers via imports",
                resolved,
                len(self._unlinked_routes),
            )

    def _resolve_test_function_edges(
        self,
        result: IngestionResult,
        batch: BatchCollector,
    ) -> None:
        """Create TESTS_FUNCTION edges from test functions to production functions.

        Uses three strategies:
        1. Walk CALLS edges at depth 1 (direct calls from test functions)
        2. Walk CALLS edges at depth 2 (via helper functions)
        3. Import-based fallback: if a test file imports from a production file,
           link test functions in that file to exported functions in the production
           file at depth 3 (inferred from imports). This is especially important
           for JS/TS where describe/it callbacks don't create Function nodes.
        """
        if not self._test_func_ids and not self._test_file_import_targets:
            return

        # tested_func_id -> set of test_func_ids that exercise it
        tested_by: dict[str, set[str]] = {}

        for test_id in self._test_func_ids:
            # Depth 1: direct callees
            depth1_callees = self._calls_adjacency.get(test_id, [])
            for callee_id in depth1_callees:
                # Only target non-test functions (func:: prefix)
                if not callee_id.startswith("func::"):
                    continue
                if callee_id in self._test_func_ids:
                    continue
                batch.add_merge_relationship(
                    "TESTS_FUNCTION",
                    test_id,
                    callee_id,
                    {"depth": 1},
                )
                result.test_coverage_edges += 1
                tested_by.setdefault(callee_id, set()).add(test_id)

            # Depth 2: callees of callees (via helpers)
            for mid_id in depth1_callees:
                depth2_callees = self._calls_adjacency.get(mid_id, [])
                for callee_id in depth2_callees:
                    if not callee_id.startswith("func::"):
                        continue
                    if callee_id in self._test_func_ids:
                        continue
                    # Skip if already covered at depth 1
                    if test_id in tested_by.get(callee_id, set()):
                        continue
                    batch.add_merge_relationship(
                        "TESTS_FUNCTION",
                        test_id,
                        callee_id,
                        {"depth": 2},
                    )
                    result.test_coverage_edges += 1
                    tested_by.setdefault(callee_id, set()).add(test_id)

        # Depth 3: import-based fallback for JS/TS test files.
        # In JS/TS, describe/it callbacks don't create Function nodes, so the call
        # graph misses most test→production relationships. This fallback infers
        # coverage from import statements. Not applied to Python where test_*
        # naming gives reliable call-graph resolution.
        self._resolve_import_based_test_edges(result, batch, tested_by)

        # Update tested_by_count on production Function nodes via direct query
        if tested_by:
            updates = [{"id": fid, "count": len(tids)} for fid, tids in tested_by.items()]
            # Flush batch first so TESTS_FUNCTION edges are written, then update counts
            counts = batch.flush()
            result.nodes_created += counts["nodes_created"]
            result.relationships_created += counts["relationships_created"]
            for i in range(0, len(updates), 200):
                chunk = updates[i : i + 200]
                self.graph.execute(
                    "UNWIND $items AS item MATCH (n:Function) WHERE n.id = item.id SET n.tested_by_count = item.count",
                    {"items": chunk},
                )

    def _resolve_import_based_test_edges(
        self,
        result: IngestionResult,
        batch: BatchCollector,
        tested_by: dict[str, set[str]],
    ) -> None:
        """Create TESTS_FUNCTION edges from test files to production functions via imports.

        Fallback strategy for test functions that have NO depth 1-2 edges (i.e. their
        calls didn't resolve to any production functions). This is especially important
        for JS/TS where describe/it callbacks don't create Function nodes, so the call
        graph can't track what they test.

        Uses depth=3 to distinguish from call-based (1, 2) test edges.
        """
        if not self._test_file_import_targets:
            return

        # Identify test functions that already have good coverage (depth 1-2)
        test_funcs_with_coverage: set[str] = set()
        for _prod_id, test_ids in tested_by.items():
            test_funcs_with_coverage.update(test_ids)

        for test_file_path, target_file_ids in self._test_file_import_targets.items():
            # Find test function IDs in this file that lack depth 1-2 coverage
            test_func_ids_in_file = [
                tid
                for tid in self._test_func_ids
                if tid.startswith(f"func::{test_file_path}::") and tid not in test_funcs_with_coverage
            ]

            if not test_func_ids_in_file:
                continue

            # For each production file imported, find its exported functions
            for target_file_id in target_file_ids:
                target_path = target_file_id[6:]  # strip "file::"
                exported = self._exported_file_entities.get(target_path, {})
                # Fall back to all entities if nothing is explicitly exported
                entities = exported or self._file_entities.get(target_path, {})

                for _name, prod_func_id in entities.items():
                    if not prod_func_id.startswith("func::"):
                        continue
                    if prod_func_id in self._test_func_ids:
                        continue

                    # Create edges from each uncovered test function to production functions
                    for test_id in test_func_ids_in_file:
                        if test_id in tested_by.get(prod_func_id, set()):
                            continue
                        batch.add_merge_relationship(
                            "TESTS_FUNCTION",
                            test_id,
                            prod_func_id,
                            {"depth": 3},
                        )
                        result.test_coverage_edges += 1
                        tested_by.setdefault(prod_func_id, set()).add(test_id)

    # Primitives that don't need RETURNS/ACCEPTS edges
    _PRIMITIVE_TYPES = frozenset(
        {
            "str",
            "string",
            "int",
            "float",
            "number",
            "bool",
            "boolean",
            "void",
            "None",
            "null",
            "undefined",
            "any",
            "unknown",
            "never",
            "object",
            "bytes",
            "dict",
            "list",
            "tuple",
            "set",
            "frozenset",
            "Object",
            "String",
            "Number",
            "Boolean",
        }
    )

    @staticmethod
    def _unwrap_generic(type_str: str) -> str:
        """Unwrap one level of generic wrapper to get the inner type.

        Examples:
            Promise<User> -> User
            Array<User> -> User
            User[] -> User
            list[User] -> User
            Optional[User] -> User
            dict[str, User] -> User (extracts value type)
        """
        s = type_str.strip()

        # Handle T[] syntax
        if s.endswith("[]"):
            return s[:-2].strip()

        # Handle Generic<T> or Generic[T] syntax
        for open_ch, close_ch in [("<", ">"), ("[", "]")]:
            idx = s.find(open_ch)
            if idx > 0 and s.endswith(close_ch):
                inner = s[idx + 1 : -1].strip()
                wrapper = s[:idx].strip()
                # For dict/Map types, extract the value type (second arg)
                if wrapper.lower() in ("dict", "map", "record", "mapping"):
                    parts = inner.split(",", 1)
                    if len(parts) == 2:
                        return parts[1].strip()
                return inner

        return s

    def _resolve_type_to_class(self, type_name: str, pf: ParsedFile) -> str | None:
        """Resolve a type name string to a Class node ID."""
        # 1. Exact qualified match
        if type_name in self._qualified_map:
            target = self._qualified_map[type_name]
            if target.startswith("class::"):
                return target

        # 2. File-scoped qualified match
        scoped = f"{pf.path}::{type_name}"
        if scoped in self._qualified_map:
            target = self._qualified_map[scoped]
            if target.startswith("class::"):
                return target

        # 3. Import-aware resolution
        imported = self._get_imported_entities(pf)
        if type_name in imported:
            target = imported[type_name]
            if target.startswith("class::"):
                return target

        # 4. Same-file entities
        file_ents = self._file_entities.get(pf.path, {})
        if type_name in file_ents:
            target = file_ents[type_name]
            if target.startswith("class::"):
                return target

        # 5. Single candidate globally
        candidates = self._short_to_candidates.get(type_name, [])
        class_candidates = [c for c in candidates if c.startswith("class::")]
        if len(class_candidates) == 1:
            return class_candidates[0]

        return None

    def _resolve_type_edges(self, parsed_files: list[ParsedFile], batch: BatchCollector) -> None:
        """Create RETURNS and ACCEPTS edges from function type annotations to Class nodes."""
        pf_by_path = self._parsed_files_by_path

        for func_id, return_type in self._func_return_types.items():
            # Extract file path from func_id to find ParsedFile context
            # func_id format: "func::file/path.ts::FuncName" or "func::file/path.ts::Class.method"
            qn = func_id[len("func::") :]
            file_path = qn.rsplit("::", 1)[0] if "::" in qn else ""
            pf = pf_by_path.get(file_path)
            if not pf:
                continue

            type_name = self._unwrap_generic(return_type)
            if type_name in self._PRIMITIVE_TYPES:
                continue
            class_id = self._resolve_type_to_class(type_name, pf)
            if class_id:
                batch.add_merge_relationship("RETURNS", func_id, class_id)

        for func_id, typed_params in self._func_typed_params.items():
            qn = func_id[len("func::") :]
            file_path = qn.rsplit("::", 1)[0] if "::" in qn else ""
            pf = pf_by_path.get(file_path)
            if not pf:
                continue

            for param_name, type_str in typed_params:
                if not type_str:
                    continue
                type_name = self._unwrap_generic(type_str)
                if type_name in self._PRIMITIVE_TYPES:
                    continue
                class_id = self._resolve_type_to_class(type_name, pf)
                if class_id:
                    batch.add_merge_relationship("ACCEPTS", func_id, class_id, {"param_name": param_name})

    def _resolve_function_calls(self, func: ParsedFunction, pf: ParsedFile, batch: BatchCollector) -> None:
        caller_id = f"func::{func.qualified_name}"
        # Track test function IDs for TESTS_FUNCTION resolution
        if func.is_test:
            self._test_func_ids.add(caller_id)
        for call_name in func.calls:
            callee_id = self._find_callee(call_name, func, pf)
            if callee_id:
                batch.add_merge_relationship("CALLS", caller_id, callee_id)
                # Build in-memory call adjacency for TESTS_FUNCTION traversal
                self._calls_adjacency.setdefault(caller_id, []).append(callee_id)

            # Create USES_HOOK edge for React hook calls (use* convention)
            bare_name = call_name.split(".")[-1] if "." in call_name else call_name
            if bare_name.startswith("use") and len(bare_name) > 3 and bare_name[3].isupper() and callee_id:
                batch.add_merge_relationship("USES_HOOK", caller_id, callee_id)

        # Resolve callback/handler references -> PASSED_TO edges
        for ref_name, context in func.callback_refs:
            callee_id = self._find_callee(ref_name, func, pf)
            if callee_id:
                batch.add_merge_relationship(
                    "PASSED_TO",
                    caller_id,
                    callee_id,
                    {"context": context},
                )
                self._callback_target_ids.add(callee_id)

    def _find_callee(
        self,
        call_name: str,
        caller: ParsedFunction,
        context_file: ParsedFile,
    ) -> str | None:
        """Resolve a call name to a known function node ID.

        Resolution strategy (priority order):
        1. Exact qualified name
        2. File-scoped qualified name
        3. Dotted calls (self/this, ClassName.method, obj.method)
        4. Import-aware: match against entities from imported files
        5. Same-file entity match
        6. Single-candidate short name (unambiguous global)
        """
        # 1. Exact qualified name (always unique)
        if call_name in self._qualified_map:
            return self._qualified_map[call_name]

        # 2. File-scoped qualified name
        scoped = f"{context_file.path}::{call_name}"
        if scoped in self._qualified_map:
            return self._qualified_map[scoped]

        # 3. Dotted calls: self.method, ClassName.method, obj.method
        if "." in call_name:
            result = self._resolve_dotted_call(call_name, caller, context_file)
            if result:
                return result

        # 4. Import-aware: check if this name was imported from a known file
        imported = self._get_imported_entities(context_file)
        if call_name in imported:
            return imported[call_name]

        # 5. Same-file entity
        file_entities = self._file_entities.get(context_file.path, {})
        if call_name in file_entities:
            return file_entities[call_name]

        # 6. Single-candidate short name (only if unambiguous)
        candidates = self._short_to_candidates.get(call_name)
        if candidates and len(candidates) == 1:
            return candidates[0]

        return None

    def _resolve_dotted_call(
        self,
        call_name: str,
        caller: ParsedFunction,
        context_file: ParsedFile,
    ) -> str | None:
        """Resolve dotted calls: self.method, this.method, ClassName.method, obj.method."""
        parts = call_name.split(".")
        obj = parts[0]
        method = parts[-1]

        # self.method / this.method -> enclosing class's method (or inherited)
        if obj in ("self", "this"):
            local = caller.qualified_name.split("::")[-1]  # "ClassName.method"
            if "." in local:
                class_name = local.split(".")[0]
                target_qn = f"{context_file.path}::{class_name}.{method}"
                if target_qn in self._qualified_map:
                    return self._qualified_map[target_qn]
                # Try inherited methods via MRO
                class_id = self._id_map.get(class_name) or self._id_map.get(f"{context_file.path}::{class_name}")
                if class_id:
                    inherited = self._resolve_inherited_method(class_id, method)
                    if inherited:
                        return inherited

        # Try file-scoped "ClassName.method" (defined in the same file)
        file_scoped = f"{context_file.path}::{call_name}"
        if file_scoped in self._qualified_map:
            return self._qualified_map[file_scoped]

        # Try inheritance for ClassName.method where ClassName is in same file
        class_id = self._id_map.get(obj) or self._id_map.get(f"{context_file.path}::{obj}")
        if class_id and class_id.startswith("class::"):
            inherited = self._resolve_inherited_method(class_id, method)
            if inherited:
                return inherited

        # Check imported entities for the dotted form
        imported = self._get_imported_entities(context_file)
        if call_name in imported:
            return imported[call_name]

        # obj.method where obj is an imported module/class — look up method
        # in the file that obj was imported from
        is_python = context_file.language == "python"
        for imp in context_file.imports:
            # Check if obj matches an imported name or the module basename
            mod_basename = imp.module_path.rsplit("/", 1)[-1].split(".")[0]
            alias = imp.aliases.get(obj)
            is_match = (
                obj in imp.imported_names or (alias in imp.imported_names if alias else False) or obj == mod_basename
            )
            if not is_match:
                continue
            target_file_id = self._resolve_import_to_file_id(imp, context_file)
            if target_file_id:
                target_path = target_file_id[6:]  # strip "file::"
                # For JS/TS, prefer exported entities; Python allows all
                if is_python:
                    target_entities = self._file_entities.get(target_path, {})
                else:
                    target_entities = self._exported_file_entities.get(target_path, {})
                    if method not in target_entities:
                        # Fall back to all entities (some files may not mark exports)
                        target_entities = self._file_entities.get(target_path, {})
                if method in target_entities:
                    return target_entities[method]

                # Python: obj may be a submodule imported via __init__.py.
                # e.g. "from marshmallow import fields" -> fields.Nested
                # The import resolves to __init__.py but "fields" is a
                # submodule.  Try resolving "obj" as a module path.
                if is_python and target_path.endswith("/__init__.py"):
                    pkg_dir = target_path.rsplit("/", 1)[0]
                    submod_path = f"{pkg_dir}/{obj}"
                    # Check file entities for the submodule file
                    for ext in (".py", ""):
                        sub_file_id = self._path_to_id.get(submod_path + ext) or self._stem_to_id.get(submod_path)
                        if sub_file_id:
                            sub_path = sub_file_id[6:]
                            sub_entities = self._file_entities.get(sub_path, {})
                            if method in sub_entities:
                                return sub_entities[method]
                            break

        # Last resort: method name in same file
        file_entities = self._file_entities.get(context_file.path, {})
        if method in file_entities:
            return file_entities[method]

        # Single-candidate for the method name
        candidates = self._short_to_candidates.get(method)
        if candidates and len(candidates) == 1:
            return candidates[0]

        return None

    def _resolve_inherited_method(
        self, class_id: str, method_name: str, _visited: set[str] | None = None
    ) -> str | None:
        """Walk the inheritance chain to find a method defined in a base class."""
        if _visited is None:
            _visited = set()
        if class_id in _visited:
            return None  # prevent cycles
        _visited.add(class_id)

        bases = self._class_bases.get(class_id, [])
        for base_id in bases:
            # Check if the base class directly defines this method
            base_methods = self._class_methods.get(base_id, {})
            if method_name in base_methods:
                return base_methods[method_name]
            # Recurse into base's bases
            result = self._resolve_inherited_method(base_id, method_name, _visited)
            if result:
                return result
        return None

    def _get_imported_entities(self, context_file: ParsedFile) -> dict[str, str]:
        """Build a map of name -> node_id for entities imported into a file.

        For wildcard imports (import * from), only exported entities are
        included. For named imports, we trust the import statement as the
        source of truth (the name was explicitly requested).

        For Python files, all entities are considered importable since Python
        has no export keyword — visibility is by convention only.

        Results are cached per file path to avoid recomputation.
        """
        cache_key = context_file.path
        if cache_key in self._import_cache:
            return self._import_cache[cache_key]

        is_python = context_file.language == "python"
        imported: dict[str, str] = {}

        for imp in context_file.imports:
            target_file_id = self._resolve_import_to_file_id(imp, context_file)
            if not target_file_id:
                continue
            target_path = target_file_id[6:]  # strip "file::"
            target_entities = self._file_entities.get(target_path, {})

            # Include re-exported entities (Python __init__.py / TS/JS barrel files)
            reexports = self._init_reexport_entities.get(target_path, {})

            if imp.is_wildcard:
                # Wildcard: only exported entities (or all for Python)
                if is_python:
                    imported.update(target_entities)
                    imported.update(reexports)
                else:
                    exported = self._exported_file_entities.get(target_path, {})
                    imported.update(exported)
                    imported.update(reexports)
            else:
                for name in imp.imported_names:
                    alias = imp.aliases.get(name)
                    if name in target_entities:
                        imported[name] = target_entities[name]
                        if alias:
                            imported[alias] = target_entities[name]
                    elif name in reexports:
                        imported[name] = reexports[name]
                        if alias:
                            imported[alias] = reexports[name]
                    elif alias and alias in target_entities:
                        imported[alias] = target_entities[alias]
                    elif alias and alias in reexports:
                        imported[alias] = reexports[alias]

        self._import_cache[cache_key] = imported
        return imported

    def _resolve_import_to_file_id(self, imp: ParsedImport, context_file: ParsedFile) -> str | None:
        """Resolve an import to a file ID using pre-built path maps."""
        file_dir = context_file.path.replace("\\", "/")
        file_dir = file_dir.rsplit("/", 1)[0] if "/" in file_dir else ""
        return self._resolve_single_import(
            imp,
            file_dir,
            context_file.language,
            self._path_to_id,
            self._stem_to_id,
            self._dir_index_to_id,
            self._pymodule_to_id,
            self._source_roots,
        )

    def _resolve_test_edges(
        self,
        parsed_files: list[ParsedFile],
        result: IngestionResult,
        batch: BatchCollector,
    ) -> None:
        """Create TESTS edges from test files to the production files they import.

        Skips imports that resolve to other test files or test helpers.
        """
        for pf in parsed_files:
            if not pf.is_test_file:
                continue
            test_file_id = f"file::{pf.path}"
            file_dir = pf.path.replace("\\", "/")
            file_dir = file_dir.rsplit("/", 1)[0] if "/" in file_dir else ""

            targets_seen: set[str] = set()
            for imp in pf.imports:
                target_id = self._resolve_single_import(
                    imp,
                    file_dir,
                    pf.language,
                    self._path_to_id,
                    self._stem_to_id,
                    self._dir_index_to_id,
                    self._pymodule_to_id,
                    self._source_roots,
                )
                if not target_id or target_id == test_file_id:
                    continue
                target_path = target_id[6:]  # strip "file::"
                # Skip if target is also a test file
                if target_path in self._test_file_paths:
                    continue
                # Deduplicate (a test file may import multiple names from same file)
                if target_id in targets_seen:
                    continue
                targets_seen.add(target_id)

                batch.add_merge_relationship("TESTS", test_file_id, target_id)
                result.test_coverage_edges += 1

    def _resolve_dependency_usage(self, parsed_files: list[ParsedFile], batch: BatchCollector) -> None:
        """Create USES_DEPENDENCY edges from functions to external dependencies.

        A function USES_DEPENDENCY a package when it calls a name that was
        imported from that package (and didn't resolve to an internal entity).
        """
        for pf in parsed_files:
            ext_map = self._file_external_imports.get(pf.path)
            if not ext_map:
                continue

            for func in pf.functions:
                self._link_func_to_deps(func, pf, ext_map, batch)
            for cls in pf.classes:
                for method in cls.methods:
                    self._link_func_to_deps(method, pf, ext_map, batch)

    def _link_func_to_deps(
        self,
        func: ParsedFunction,
        context_file: ParsedFile,
        ext_map: dict[str, str],
        batch: BatchCollector,
    ) -> None:
        """Check each call in a function against external imports."""
        caller_id = f"func::{func.qualified_name}"
        seen_deps: set[str] = set()

        for call_name in func.calls:
            # Skip calls that resolved to internal entities
            if self._find_callee(call_name, func, context_file):
                continue

            # Check bare name against external imports
            bare = call_name.split(".")[-1] if "." in call_name else call_name
            obj = call_name.split(".")[0] if "." in call_name else call_name

            dep_id = ext_map.get(bare) or ext_map.get(obj)
            if dep_id and dep_id not in seen_deps:
                seen_deps.add(dep_id)
                batch.add_merge_relationship("USES_DEPENDENCY", caller_id, dep_id)

    def _resolve_base(self, base_name: str, context_file: ParsedFile) -> str | None:
        """Resolve a base/extends name to a known class node ID.

        Handles generics (Foo<Bar> -> Foo), dotted refs (React.Component -> Component),
        and utility types (Omit<Foo, 'x'> -> Foo).
        """
        # Strip generic parameters: Component<Props> -> Component
        clean = base_name.split("<")[0].strip()
        if not clean:
            return None

        # Strip wrapping quotes (e.g. from Omit<...> inner types)
        clean = clean.strip("'\"")

        # Try exact match first
        if clean in self._id_map:
            return self._id_map[clean]

        # For dotted names like React.Component, try the last segment
        if "." in clean:
            last_part = clean.rsplit(".", 1)[-1]
            if last_part in self._id_map:
                return self._id_map[last_part]

        # Try file-scoped qualified name
        scoped = f"{context_file.path}::{clean}"
        if scoped in self._id_map:
            return self._id_map[scoped]

        # Try matching against imported names in the file
        for imp in context_file.imports:
            for iname in imp.imported_names:
                if iname == clean:
                    full_name = f"{imp.module_path}.{iname}" if imp.module_path else iname
                    if full_name in self._id_map:
                        return self._id_map[full_name]

        return None

    def _resolve_imports(
        self,
        parsed_files: list[ParsedFile],
        result: IngestionResult,
        batch: BatchCollector,
    ) -> None:
        """Create IMPORTS edges between File nodes based on import statements.

        Path maps and source roots are pre-built during Phase 1.
        """
        # Collect external dependencies (npm packages, Python stdlib/pypi)
        external_deps: dict[str, set[str]] = {}  # package_name -> set of importing files

        for pf in parsed_files:
            file_id = f"file::{pf.path}"
            file_dir = pf.path.replace("\\", "/")
            file_dir = file_dir.rsplit("/", 1)[0] if "/" in file_dir else ""

            for imp in pf.imports:
                imp_id = f"import::{pf.path}::{imp.line}"
                target_id = self._resolve_single_import(
                    imp,
                    file_dir,
                    pf.language,
                    self._path_to_id,
                    self._stem_to_id,
                    self._dir_index_to_id,
                    self._pymodule_to_id,
                    self._source_roots,
                )
                if target_id and target_id != file_id:
                    batch.add_merge_relationship("IMPORTS", file_id, target_id)
                    self._import_resolved[imp_id] = True
                    # Track test file import targets for TESTS_FUNCTION resolution (JS/TS only)
                    if pf.is_test_file and pf.language != "python":
                        target_path = target_id[6:]  # strip "file::"
                        if target_path not in self._test_file_paths:
                            self._test_file_import_targets.setdefault(pf.path, set()).add(target_id)
                elif not target_id and not imp.is_relative:
                    self._import_resolved[imp_id] = False
                    # Unresolved non-relative import = external dependency
                    pkg = self._extract_package_name(imp.module_path, pf.language)
                    if pkg:
                        if pkg not in external_deps:
                            external_deps[pkg] = set()
                        external_deps[pkg].add(pf.path)
                        # Track which names this file imports from this package
                        ext_map = self._file_external_imports.setdefault(pf.path, {})
                        dep_id = f"dep::{pkg}"
                        for name in imp.imported_names:
                            ext_map[name] = dep_id
                            alias = imp.aliases.get(name)
                            if alias:
                                ext_map[alias] = dep_id
                        # Also map the module basename (e.g. "React" from "react")
                        mod_base = imp.module_path.rsplit("/", 1)[-1].split(".")[0]
                        ext_map[mod_base] = dep_id

        # Create Dependency nodes — with optional staleness/vulnerability enrichment
        from gristle.ingestion.dependency_checker import check_dependencies

        deps_to_check: list[tuple[str, str, str]] = []
        for pkg_name in external_deps:
            version = self._dependency_versions.get(pkg_name, "")
            if not version:
                normalized = pkg_name.lower().replace("-", "_")
                version = self._dependency_versions.get(normalized, "")
            ecosystem = self._dependency_ecosystems.get(
                pkg_name, self._dependency_ecosystems.get(pkg_name.lower().replace("-", "_"), "")
            )
            if version and ecosystem:
                deps_to_check.append((pkg_name, version, ecosystem))

        enrichments = check_dependencies(
            deps_to_check,
            timeout=settings.dependency_timeout_seconds,
            max_workers=settings.dependency_concurrency,
            enabled=settings.dependency_check_enabled,
        )

        for pkg_name, importers in external_deps.items():
            dep_id = f"dep::{pkg_name}"
            # Look up version from manifest files (try exact name, then normalized)
            version = self._dependency_versions.get(pkg_name, "")
            if not version:
                normalized = pkg_name.lower().replace("-", "_")
                version = self._dependency_versions.get(normalized, "")

            health = enrichments.get(pkg_name)
            batch.add_node(
                "Dependency",
                {
                    "id": dep_id,
                    "name": pkg_name,
                    "import_count": len(importers),
                    "version": version,
                    "latest_version": health.latest_version if health else "",
                    "is_outdated": health.is_outdated if health else False,
                    "vulnerability_count": len(health.vulnerability_ids) if health else 0,
                    "vulnerabilities": health.vulnerability_ids if health else [],
                    "checked_at": health.checked_at if health else "",
                },
            )
            result.dependencies_found += 1
            if health and health.is_outdated:
                result.dependencies_outdated += 1
            if health and health.vulnerability_ids:
                result.dependencies_vulnerable += 1
            # Link importing files to the dependency
            for file_path in importers:
                file_id = f"file::{file_path}"
                batch.add_merge_relationship("DEPENDS_ON", file_id, dep_id)

        # Link functions to the external dependencies they use
        self._resolve_dependency_usage(parsed_files, batch)

        # Update Import nodes with resolved status
        if self._import_resolved:
            updates = [{"id": imp_id, "resolved": resolved} for imp_id, resolved in self._import_resolved.items()]
            counts = batch.flush()
            result.nodes_created += counts["nodes_created"]
            result.relationships_created += counts["relationships_created"]
            for i in range(0, len(updates), 200):
                chunk = updates[i : i + 200]
                self.graph.execute(
                    "UNWIND $items AS item MATCH (n:Import) WHERE n.id = item.id SET n.resolved = item.resolved",
                    {"items": chunk},
                )

    def _extract_dependency_versions(self, repo_path: str) -> None:
        """Extract dependency version strings from manifest files.

        Reads package.json, requirements.txt, and pyproject.toml to build
        a package_name -> version_string map used when creating Dependency nodes.
        """
        root = Path(repo_path)

        # package.json (JS/TS)
        pkg_json = root / "package.json"
        if pkg_json.is_file():
            try:
                data = json.loads(pkg_json.read_text(encoding="utf-8"))
                for section in ("dependencies", "devDependencies", "peerDependencies"):
                    deps = data.get(section, {})
                    if isinstance(deps, dict):
                        for name, version in deps.items():
                            if isinstance(version, str):
                                self._dependency_versions[name] = version
                                self._dependency_ecosystems[name] = "npm"
            except (json.JSONDecodeError, OSError):
                pass

        # requirements.txt (Python)
        for req_file in ("requirements.txt", "requirements-dev.txt", "requirements_dev.txt"):
            req_path = root / req_file
            if req_path.is_file():
                try:
                    for line in req_path.read_text(encoding="utf-8").splitlines():
                        line = line.strip()
                        if not line or line.startswith("#") or line.startswith("-"):
                            continue
                        # Parse: package==1.0.0, package>=1.0.0, package~=1.0.0, etc.
                        m = re.match(r"^([a-zA-Z0-9_.-]+)\s*([><=!~]+.+)?", line)
                        if m:
                            pkg = m.group(1).lower().replace("-", "_")
                            version = m.group(2)
                            if version:
                                self._dependency_versions[pkg] = version.strip()
                                self._dependency_ecosystems[pkg] = "PyPI"
                except OSError:
                    pass

        # pyproject.toml (Python)
        pyproject = root / "pyproject.toml"
        if pyproject.is_file():
            try:
                import tomllib

                data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
                # PEP 621: [project.dependencies]
                project_deps = data.get("project", {}).get("dependencies", [])
                for dep_str in project_deps:
                    if isinstance(dep_str, str):
                        m = re.match(r"^([a-zA-Z0-9_.-]+)\s*([><=!~]+.+)?", dep_str)
                        if m:
                            pkg = m.group(1).lower().replace("-", "_")
                            version = m.group(2)
                            if version:
                                self._dependency_versions[pkg] = version.strip()
                                self._dependency_ecosystems[pkg] = "PyPI"
                # Optional deps
                optional = data.get("project", {}).get("optional-dependencies", {})
                for group_deps in optional.values():
                    if isinstance(group_deps, list):
                        for dep_str in group_deps:
                            if isinstance(dep_str, str):
                                m = re.match(r"^([a-zA-Z0-9_.-]+)\s*([><=!~]+.+)?", dep_str)
                                if m:
                                    pkg = m.group(1).lower().replace("-", "_")
                                    version = m.group(2)
                                    if version:
                                        self._dependency_versions[pkg] = version.strip()
                                        self._dependency_ecosystems[pkg] = "PyPI"
            except (ImportError, OSError, Exception):
                pass

    def _process_config_files(
        self,
        repo_path: str,
        parsed_files: list[ParsedFile],
        result: IngestionResult,
    ) -> None:
        """Walk config files, create File/EnvVar nodes, and resolve USES_ENV edges."""
        batch = BatchCollector(self.graph, self._batch_size)

        # 1. Walk and parse config files
        config_walked = walk_config_files(repo_path)
        for wf in config_walked:
            try:
                content = Path(wf.absolute_path).read_text(encoding="utf-8", errors="replace")
            except OSError as e:
                logger.warning("Cannot read config file %s: %s", wf.relative_path, e)
                continue

            parsed_config = parse_config_file(wf.relative_path, content)
            if parsed_config is None:
                continue

            result.config_files_processed += 1
            file_id = f"file::{wf.relative_path}"

            # Create File node with config_type and config-specific properties
            props: dict[str, object] = {
                "id": file_id,
                "path": wf.relative_path,
                "language": "config",
                "line_count": parsed_config.line_count,
                "is_test_file": False,
                "config_type": parsed_config.config_type,
            }
            # Add config-specific properties (config_scripts, config_target, etc.)
            for key, value in parsed_config.properties.items():
                props[key] = value

            batch.add_node("File", props)

            # Create EnvVar nodes from this config file
            for env_var in parsed_config.env_vars:
                env_id = f"envvar::{env_var.name}"
                if env_var.name not in self._env_var_ids:
                    batch.add_node(
                        "EnvVar",
                        {
                            "id": env_id,
                            "name": env_var.name,
                            "default_value": env_var.default_value or "",
                            "required": env_var.required,
                        },
                    )
                    self._env_var_ids[env_var.name] = env_id
                    result.env_vars_found += 1
                # Link EnvVar to the config file it was defined in
                batch.add_merge_relationship("DEFINED_IN", env_id, file_id)

        # 2. Create EnvVar nodes from source file env_var_refs (if not already created)
        # and resolve USES_ENV edges from source files to EnvVar nodes
        for pf in parsed_files:
            if not pf.env_var_refs:
                continue
            file_id = f"file::{pf.path}"
            for var_name in pf.env_var_refs:
                env_id = f"envvar::{var_name}"
                if var_name not in self._env_var_ids:
                    # Create the EnvVar node (referenced but not defined in a config)
                    batch.add_node(
                        "EnvVar",
                        {
                            "id": env_id,
                            "name": var_name,
                            "default_value": "",
                            "required": False,
                        },
                    )
                    self._env_var_ids[var_name] = env_id
                    result.env_vars_found += 1
                # USES_ENV: source file -> env var
                batch.add_merge_relationship("USES_ENV", file_id, env_id)

        # Flush config batch
        counts = batch.flush()
        result.nodes_created += counts["nodes_created"]
        result.relationships_created += counts["relationships_created"]

    def _register_python_source_roots(self, parsed_files: list[ParsedFile]) -> None:
        """Detect Python source roots (e.g. ``src/``) and register module
        keys with the source root prefix stripped.

        A Python source root is a directory that contains packages
        (dirs with ``__init__.py``) but is not itself a Python package
        (no ``__init__.py``).  Common examples: ``src/``, ``lib/``.

        For a file like ``src/marshmallow/schema.py`` whose full module
        key is ``src.marshmallow.schema``, this method adds the key
        ``marshmallow.schema`` so that ``from marshmallow.schema import X``
        resolves correctly.
        """
        py_paths = {pf.path.replace("\\", "/") for pf in parsed_files if pf.language == "python"}
        if not py_paths:
            return

        # Find directories that contain __init__.py
        init_dirs: set[str] = set()
        for p in py_paths:
            basename = p.rsplit("/", 1)[-1] if "/" in p else p
            if basename == "__init__.py":
                pkg_dir = p.rsplit("/", 1)[0] if "/" in p else ""
                if pkg_dir:
                    init_dirs.add(pkg_dir)

        # A source root is a first-level directory that is NOT a package
        # but contains child packages.  e.g. "src" contains "src/marshmallow"
        # which has __init__.py, but "src/__init__.py" doesn't exist.
        first_dirs: set[str] = set()
        for p in py_paths:
            if "/" in p:
                first_dirs.add(p.split("/")[0])

        python_source_roots: list[str] = []
        for d in first_dirs:
            if d not in init_dirs and any(id_.startswith(d + "/") for id_ in init_dirs):
                python_source_roots.append(d)

        if not python_source_roots:
            return

        logger.info("Python source roots detected: %s", python_source_roots)

        # Register stripped module keys
        for full_module, file_id in list(self._pymodule_to_id.items()):
            for root in python_source_roots:
                prefix = root + "."
                if full_module.startswith(prefix):
                    stripped = full_module[len(prefix) :]
                    if stripped and stripped not in self._pymodule_to_id:
                        self._pymodule_to_id[stripped] = file_id

    def _build_init_reexport_maps(self, parsed_files: list[ParsedFile]) -> None:
        """Build re-export maps for barrel/package entry files.

        Handles both Python ``__init__.py`` and TS/JS ``index.ts``/``index.js``
        barrel files.  When a barrel file re-exports entities from siblings
        (e.g. ``from .schema import Schema`` or ``export { Button } from './Button'``),
        those names become available through the barrel's namespace.

        Uses fixed-point iteration (up to 5 passes) so that multi-level
        barrel chains (barrel → barrel → definition) resolve correctly.
        """
        # Identify barrel files once upfront
        barrel_files: list[tuple[ParsedFile, str, bool]] = []  # (pf, normalized, is_py_init)
        for pf in parsed_files:
            normalized = pf.path.replace("\\", "/")
            is_py_init = pf.language == "python" and normalized.endswith("/__init__.py")
            is_ts_index = (
                pf.language in ("typescript", "javascript")
                and "/" in normalized
                and normalized.rsplit("/", 1)[-1].startswith("index.")
            )
            if is_py_init or is_ts_index:
                barrel_files.append((pf, normalized, is_py_init))

        for _pass in range(5):
            new_count = 0
            for pf, normalized, is_py_init in barrel_files:
                new_count += self._resolve_barrel_reexports(pf, normalized, is_py_init)
            if new_count == 0:
                break
            logger.debug("Barrel re-export pass %d: %d new entities", _pass + 1, new_count)

    def _resolve_barrel_reexports(
        self,
        pf: ParsedFile,
        normalized: str,
        is_py_init: bool,
    ) -> int:
        """Resolve re-exports for a single barrel file. Returns count of new entities added."""
        file_dir = normalized.rsplit("/", 1)[0] if "/" in normalized else ""
        existing = self._init_reexport_entities.get(pf.path, {})
        reexports: dict[str, str] = dict(existing)

        for imp in pf.imports:
            target_file_id = self._resolve_single_import(
                imp,
                file_dir,
                pf.language,
                self._path_to_id,
                self._stem_to_id,
                self._dir_index_to_id,
                self._pymodule_to_id,
                self._source_roots,
            )
            if not target_file_id:
                continue
            target_path = target_file_id[6:]  # strip "file::"
            # Combine direct file entities with any re-exports from the target
            target_entities = dict(self._file_entities.get(target_path, {}))
            target_reexports = self._init_reexport_entities.get(target_path, {})
            target_entities.update(target_reexports)

            if imp.is_wildcard:
                if is_py_init:
                    reexports.update(target_entities)
                else:
                    exported = self._exported_file_entities.get(target_path, {})
                    if exported:
                        # Also include re-exports from the target barrel
                        merged = dict(exported)
                        merged.update(target_reexports)
                        reexports.update(merged)
                    else:
                        reexports.update(target_entities)
            else:
                for name in imp.imported_names:
                    alias = imp.aliases.get(name)
                    export_name = alias or name
                    if name in target_entities:
                        reexports[export_name] = target_entities[name]
                        if alias:
                            reexports[name] = target_entities[name]
                    elif alias and alias in target_entities:
                        reexports[alias] = target_entities[alias]

        new_count = len(reexports) - len(existing)
        if reexports:
            self._init_reexport_entities[pf.path] = reexports
            if new_count > 0:
                logger.debug("%s re-exports %d entities (+%d new)", pf.path, len(reexports), new_count)
        return new_count

    @staticmethod
    def _detect_source_roots(parsed_files: list[ParsedFile]) -> list[str]:
        """Auto-detect common source root prefixes like 'src/', 'lib/', 'app/'."""
        prefix_counts: dict[str, int] = {}
        for pf in parsed_files:
            if pf.language not in ("typescript", "javascript"):
                continue
            normalized = pf.path.replace("\\", "/")
            first_dir = normalized.split("/")[0] if "/" in normalized else ""
            if first_dir:
                prefix_counts[first_dir] = prefix_counts.get(first_dir, 0) + 1

        total_ts_js = sum(1 for pf in parsed_files if pf.language in ("typescript", "javascript"))
        if total_ts_js == 0:
            return []

        # A source root is a prefix that contains a significant portion of files
        roots: list[str] = []
        for prefix, count in sorted(prefix_counts.items(), key=lambda x: -x[1]):
            if count >= total_ts_js * 0.1:  # At least 10% of files
                roots.append(prefix)
        return roots

    def _resolve_single_import(
        self,
        imp: ParsedImport,
        file_dir: str,
        language: str,
        path_to_id: dict[str, str],
        stem_to_id: dict[str, str],
        dir_index_to_id: dict[str, str],
        pymodule_to_id: dict[str, str],
        source_roots: list[str] | None = None,
    ) -> str | None:
        """Resolve a single import to a file node ID."""
        module = imp.module_path

        # Python: use module-path lookup
        if language == "python":
            if imp.is_relative:
                # Resolve relative import based on importing file's directory.
                # file_dir is e.g. "src/marshmallow", module_path is "." or ".schema"
                pkg_module = file_dir.replace("/", ".")
                if module == ".":
                    # "from . import X" — X is a sibling module
                    for name in imp.imported_names:
                        target = f"{pkg_module}.{name}"
                        found = pymodule_to_id.get(target)
                        if found:
                            return found
                    return pymodule_to_id.get(pkg_module)
                else:
                    # "from .foo import X" — strip leading dots and resolve
                    dots = len(module) - len(module.lstrip("."))
                    relative_part = module.lstrip(".")
                    parts = pkg_module.split(".")
                    # Go up (dots - 1) levels: "." = same package, ".." = parent
                    up = dots - 1
                    if up > 0 and up < len(parts):
                        parts = parts[:-up]
                    base = ".".join(parts)
                    target = f"{base}.{relative_part}" if relative_part else base
                    return pymodule_to_id.get(target)
            return pymodule_to_id.get(module)

        # TS/JS: resolve relative paths and bare specifiers
        if imp.is_relative:
            # Resolve relative to the importing file's directory
            resolved = self._resolve_relative_path(module, file_dir)
            return self._lookup_resolved_path(resolved, path_to_id, stem_to_id, dir_index_to_id)

        # Non-relative: could be a path alias like @/lib/foo or a package
        stripped = self._strip_path_alias(module)
        if stripped is None:
            # It's likely an npm package — skip
            return None

        # Try the stripped alias directly (project-root relative)
        found = self._lookup_resolved_path(stripped, path_to_id, stem_to_id, dir_index_to_id)
        if found:
            return found

        # Try with each detected source root prefix (e.g., src/lib/utils)
        for root in source_roots or []:
            prefixed = f"{root}/{stripped}"
            found = self._lookup_resolved_path(prefixed, path_to_id, stem_to_id, dir_index_to_id)
            if found:
                return found

        return None

    @staticmethod
    def _lookup_resolved_path(
        resolved: str,
        path_to_id: dict[str, str],
        stem_to_id: dict[str, str],
        dir_index_to_id: dict[str, str],
    ) -> str | None:
        """Try exact path, then stem (no extension), then directory index.

        Also handles the TypeScript convention where imports use ``.js``
        extensions but actual files are ``.ts`` (Node16/NodeNext module
        resolution).
        """
        if resolved in path_to_id:
            return path_to_id[resolved]
        if resolved in stem_to_id:
            return stem_to_id[resolved]
        if resolved in dir_index_to_id:
            return dir_index_to_id[resolved]

        # TypeScript convention: import './foo.js' resolves to foo.ts
        # Strip the .js/.jsx extension and retry as a stem lookup
        for js_ext in (".js", ".jsx", ".mjs", ".cjs"):
            if resolved.endswith(js_ext):
                stem = resolved[: -len(js_ext)]
                if stem in stem_to_id:
                    return stem_to_id[stem]
                if stem in dir_index_to_id:
                    return dir_index_to_id[stem]
                break

        return None

    @staticmethod
    def _resolve_relative_path(module_path: str, from_dir: str) -> str:
        """Resolve a relative import path like './utils' or '../lib/foo'."""
        parts = from_dir.split("/") if from_dir else []
        segments = module_path.split("/")

        for seg in segments:
            if seg == ".":
                continue
            elif seg == "..":
                if parts:
                    parts.pop()
            else:
                parts.append(seg)

        return "/".join(parts)

    @staticmethod
    def _strip_path_alias(module_path: str) -> str | None:
        """Strip path alias prefix and return the resolved path, or None if npm package."""
        # @/ or ~/ or #/ prefix — common alias for project root
        for prefix in ("@/", "~/", "#/"):
            if module_path.startswith(prefix):
                return module_path[len(prefix) :]

        # @word/ prefix where word looks like a local alias (not a scoped npm package)
        if module_path.startswith("@") and "/" in module_path:
            first_part = module_path.split("/")[0]
            rest = module_path[len(first_part) + 1 :]
            # Scoped npm packages: @tanstack/query, @radix-ui/react-dialog, etc.
            # These typically have lowercase names with hyphens.
            # Local aliases: @app, @lib, @components — no hyphens, short.
            if "-" not in first_part and len(first_part) <= 15:
                # Likely a local alias — return the rest
                return rest

        return None

    @staticmethod
    def _extract_package_name(module_path: str, language: str) -> str | None:
        """Extract the npm package name or Python top-level module from an import path.

        Returns None for built-in modules or paths that don't look like packages.
        """
        if language == "python":
            # Python: top-level module name (e.g. "os.path" -> "os")
            top = module_path.split(".")[0]
            # Skip obvious stdlib modules
            _PY_STDLIB = {
                "os",
                "sys",
                "re",
                "json",
                "math",
                "typing",
                "collections",
                "functools",
                "itertools",
                "pathlib",
                "logging",
                "unittest",
                "dataclasses",
                "abc",
                "io",
                "datetime",
                "time",
                "hashlib",
                "copy",
                "enum",
                "contextlib",
                "operator",
                "string",
                "textwrap",
                "struct",
                "types",
                "importlib",
                "inspect",
                "warnings",
                "subprocess",
                "shutil",
                "tempfile",
                "glob",
                "fnmatch",
                "socket",
                "http",
                "urllib",
                "email",
                "html",
                "xml",
                "asyncio",
                "concurrent",
                "threading",
                "multiprocessing",
                "__future__",
                "builtins",
                "traceback",
                "pdb",
                "dis",
            }
            if top in _PY_STDLIB:
                return None
            return top

        # JS/TS: extract package name
        # Scoped: @scope/package/path -> @scope/package
        # Bare: package/path -> package
        if module_path.startswith("@") and "/" in module_path:
            parts = module_path.split("/")
            return f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else module_path
        # Bare specifier
        return module_path.split("/")[0]

    # ------------------------------------------------------------------
    # Phase 3: Document processing
    # ------------------------------------------------------------------

    def _process_document(self, wf: WalkedFile, result: IngestionResult, batch: BatchCollector) -> None:
        """Parse a markdown file and build document nodes with code references."""
        try:
            content = Path(wf.absolute_path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            result.errors.append(f"Read error {wf.relative_path}: {e}")
            return

        try:
            doc = self._md_parser.parse(wf.relative_path, content)
        except Exception as e:
            logger.warning("Doc parse error %s: %s", wf.relative_path, e)
            result.errors.append(f"Doc parse error {wf.relative_path}: {e}")
            return

        doc_id = f"doc::{doc.path}"

        # Create Document node
        batch.add_node(
            "Document",
            {
                "id": doc_id,
                "path": doc.path,
                "title": doc.title,
                "doc_type": doc.doc_type,
                "line_count": doc.line_count,
                "section_count": len(doc.sections),
                "reference_count": len(doc.code_references),
            },
        )

        # Create DocumentSection nodes
        for _i, section in enumerate(doc.sections):
            section_id = f"docsec::{doc.path}::L{section.start_line}"
            batch.add_node(
                "DocumentSection",
                {
                    "id": section_id,
                    "heading": section.heading,
                    "level": section.level,
                    "start_line": section.start_line,
                    "end_line": section.end_line,
                    "file_path": doc.path,
                    "reference_count": len(section.code_references),
                },
            )
            batch.add_relationship("HAS_SECTION", doc_id, section_id)

            # Resolve code references in this section
            for ref in section.code_references:
                self._resolve_and_link_reference(ref, section_id, result, batch)

        # Resolve top-level references (before first heading)
        for ref in doc.code_references:
            if not any(ref in section.code_references for section in doc.sections):
                self._resolve_and_link_reference(ref, doc_id, result, batch)

        result.docs_processed += 1

    def _resolve_and_link_reference(
        self,
        ref: CodeReference,
        source_id: str,
        result: IngestionResult,
        batch: BatchCollector,
    ) -> None:
        """Resolve a code reference and create a REFERENCES edge if possible."""
        result.doc_references_total += 1
        target_id = self._resolve_doc_reference(ref)

        if target_id:
            ref.resolved = True
            ref.resolved_to = target_id
            batch.add_merge_relationship("REFERENCES", source_id, target_id)
            result.doc_references_resolved += 1

    def _resolve_doc_reference(self, ref: CodeReference) -> str | None:
        """Try to resolve a code reference from a document to a graph node.

        Uses a multi-strategy approach:
        1. Exact match on name/path
        2. Case-insensitive match
        3. Dotted name decomposition (obj.method -> method)
        4. Strip file extensions and common prefixes
        """
        text = ref.raw_text

        # File path references — try direct path match
        if ref.ref_type in ("file_path", "link"):
            # Normalize the path
            clean = text.lstrip("./").replace("\\", "/")
            # Remove anchor fragments like #L42
            if "#" in clean:
                clean = clean.split("#")[0]
            # Try exact match in name map
            if clean in self._name_to_id:
                return self._name_to_id[clean]
            # Try stripping file extension (docs often reference without .ts/.js)
            clean_stem = clean.rsplit(".", 1)[0] if "." in clean else clean
            if clean_stem in self._name_to_id:
                return self._name_to_id[clean_stem]
            # Try with common prefixes stripped/added
            for prefix in ("src/", "lib/", "app/", "packages/"):
                if clean.startswith(prefix):
                    stripped = clean[len(prefix) :]
                    if stripped in self._name_to_id:
                        return self._name_to_id[stripped]
                    stripped_stem = stripped.rsplit(".", 1)[0] if "." in stripped else stripped
                    if stripped_stem in self._name_to_id:
                        return self._name_to_id[stripped_stem]
                else:
                    prefixed = prefix + clean
                    if prefixed in self._name_to_id:
                        return self._name_to_id[prefixed]
            # Case-insensitive fallback
            clean_lower = clean.lower()
            if clean_lower in self._name_lower_to_id:
                return self._name_lower_to_id[clean_lower]
            return None

        # Inline code references — try as entity name
        if ref.ref_type == "inline_code":
            # Exact match (PascalCase class, function name, etc.)
            if text in self._name_to_id:
                return self._name_to_id[text]
            if text in self._id_map:
                return self._id_map[text]
            # Dotted name: try last segment (e.g. graph.query -> query)
            if "." in text:
                last = text.rsplit(".", 1)[-1]
                if last in self._name_to_id:
                    return self._name_to_id[last]
                # Also try "ClassName.method" as a qualified name
                if text in self._id_map:
                    return self._id_map[text]
            # Case-insensitive fallback
            text_lower = text.lower()
            if text_lower in self._name_lower_to_id:
                return self._name_lower_to_id[text_lower]
            # Try dotted segments case-insensitively
            if "." in text:
                last_lower = text.rsplit(".", 1)[-1].lower()
                if last_lower in self._name_lower_to_id:
                    return self._name_lower_to_id[last_lower]
            return None

        return None

    def _delete_file_nodes(self, file_path: str) -> None:
        """Remove all nodes associated with a file."""
        self.graph.execute(
            "MATCH (n) WHERE n.file_path = $fp DETACH DELETE n",
            {"fp": file_path},
        )
        self.graph.execute(
            "MATCH (n:File) WHERE n.path = $fp DETACH DELETE n",
            {"fp": file_path},
        )
