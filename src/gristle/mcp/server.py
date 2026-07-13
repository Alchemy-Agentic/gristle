"""Gristle MCP server: exposes code graph intelligence to AI agents."""

from __future__ import annotations

import asyncio
import functools
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal, cast

from mcp.server.fastmcp import FastMCP
from redis.exceptions import ConnectionError as RedisConnectionError
from starlette.responses import JSONResponse

from gristle.config import settings
from gristle.graph.client import GraphClient
from gristle.ingestion.pipeline import IngestionPipeline
from gristle.logging import Timer
from gristle.parsers.registry import ParserRegistry
from gristle.query.engine import QueryEngine

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from starlette.requests import Request

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Auth setup — only enabled when GRISTLE_API_KEY is set
# ------------------------------------------------------------------

_token_verifier = None
_auth_settings = None

if settings.api_key:
    from mcp.server.auth.settings import AuthSettings
    from pydantic import AnyHttpUrl

    from gristle.mcp.auth import ApiKeyVerifier

    _token_verifier = ApiKeyVerifier(settings.api_key)
    _auth_settings = AuthSettings(
        issuer_url=AnyHttpUrl("https://gristle.local"),
        resource_server_url=AnyHttpUrl("https://gristle.local"),
    )

# ------------------------------------------------------------------
# Server instance
# ------------------------------------------------------------------

mcp = FastMCP(
    "gristle",
    instructions="Graph-based code intelligence. Call gristle_ingest first, then query.",
    host=settings.http_host,
    port=settings.effective_port,
    json_response=True,
    stateless_http=True,
    token_verifier=_token_verifier,
    auth=_auth_settings,
)

# These are initialised lazily per-repo via gristle_ingest.
_engines: dict[str, QueryEngine] = {}
_pipelines: dict[str, IngestionPipeline] = {}
_semantic_indexes: dict[str, Any] = {}  # repo_id -> SemanticIndex (if available)
_registry = ParserRegistry().build_default()


def _get_engine(repo_id: str) -> QueryEngine | None:
    return _engines.get(repo_id)


# ------------------------------------------------------------------
# Uniform tool error boundary
# ------------------------------------------------------------------
# Every @mcp.tool() is wrapped so a raw exception becomes a structured
# {"error": ...} the calling agent can act on, instead of a protocol error.
# functools.wraps preserves the signature/annotations FastMCP reads to build
# each tool's JSON schema, so wrapping is transparent to the client.


def _tool_error_boundary(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @functools.wraps(fn)
    async def wrapper(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except RedisConnectionError:
            return {
                "error": (
                    f"Cannot reach FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}. "
                    "Is it running? Start it with: docker compose up -d falkordb"
                ),
                "tool": fn.__name__,
            }
        except Exception as e:  # noqa: BLE001 - boundary: every tool returns a clean error dict
            logger.exception("Tool %s failed", fn.__name__)
            return {"error": str(e), "tool": fn.__name__}

    return wrapper


_raw_tool = mcp.tool


def _tool(*args: Any, **kwargs: Any) -> Callable[[Callable[..., Awaitable[Any]]], Any]:
    decorator = _raw_tool(*args, **kwargs)

    def apply(fn: Callable[..., Awaitable[Any]]) -> Any:
        return decorator(_tool_error_boundary(fn))

    return apply


mcp.tool = _tool  # type: ignore[method-assign]


# ======================================================================
# Health check (bypasses auth)
# ======================================================================


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
    """Liveness: the process is up and serving. Does not touch FalkorDB."""
    from gristle import __version__

    return JSONResponse(
        {
            "status": "ok",
            "server": "gristle",
            "version": __version__,
            "transport": settings.transport,
            "repos_loaded": len(_engines),
            "repos": list(_engines.keys()),
        }
    )


@mcp.custom_route("/ready", methods=["GET"])
async def readiness_check(request: Request) -> JSONResponse:
    """Readiness: returns 503 unless FalkorDB is reachable (orchestrator gate)."""
    from gristle import __version__

    reachable = await asyncio.to_thread(
        lambda: GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
        ).ping()
    )
    if reachable:
        return JSONResponse({"status": "ready", "version": __version__, "falkordb": "reachable"})
    return JSONResponse(
        {
            "status": "unavailable",
            "version": __version__,
            "falkordb": f"unreachable at {settings.falkordb_host}:{settings.falkordb_port}",
        },
        status_code=503,
    )


# ======================================================================
# Tools
# ======================================================================


@mcp.tool()
async def gristle_ingest(repo_path: str, repo_id: str | None = None) -> dict:
    """Index a local repository into the Gristle code graph.

    Point this at a directory containing source code. Gristle will parse
    all supported files, extract functions, classes, imports, and call
    relationships, and store them in a queryable graph database.

    Args:
        repo_path: Absolute path to the repository root directory.
        repo_id: Optional short identifier for this repo. Defaults to a
                 hash of the path.
    """
    repo_path_resolved = str(Path(repo_path).resolve())
    if not Path(repo_path_resolved).is_dir():
        return {"error": f"Directory not found: {repo_path}"}

    rid = repo_id or GraphClient.repo_id_from_path(repo_path_resolved)
    graph = GraphClient(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        repo_id=rid,
        password=settings.falkordb_password,
    )
    pipeline = IngestionPipeline(graph, _registry)

    def _ingest():
        with Timer() as t:
            res = pipeline.ingest_repo(repo_path_resolved)
        return res, t.ms

    result, duration_ms = await asyncio.to_thread(_ingest)

    engine = QueryEngine(graph, repo_path=repo_path_resolved)
    _engines[rid] = engine
    _pipelines[rid] = pipeline

    logger.info(
        "gristle_ingest completed for %s",
        rid,
        extra={
            "event": "tool_ingest",
            "repo_id": rid,
            "duration_ms": duration_ms,
            "files": result.files_processed,
            "nodes": result.nodes_created,
            "rels": result.relationships_created,
        },
    )

    return {
        "status": "success",
        "repo_id": rid,
        "files_processed": result.files_processed,
        "files_skipped": result.files_skipped,
        "docs_processed": result.docs_processed,
        "nodes_created": result.nodes_created,
        "relationships_created": result.relationships_created,
        "doc_references_total": result.doc_references_total,
        "doc_references_resolved": result.doc_references_resolved,
        "routes_found": result.routes_found,
        "components_found": result.components_found,
        "test_files_found": result.test_files_found,
        "test_cases_found": result.test_cases_found,
        "todos_found": result.todos_found,
        "dependencies_found": result.dependencies_found,
        "test_coverage_edges": result.test_coverage_edges,
        "models_found": result.models_found,
        "model_fields_found": result.model_fields_found,
        "model_relations_found": result.model_relations_found,
        "duration_ms": duration_ms,
        "errors": result.errors[:10] if result.errors else [],
    }


@mcp.tool()
async def gristle_ingest_github(
    repo_url: str,
    token: str | None = None,
    ref: str | None = None,
    repo_id: str | None = None,
) -> dict:
    """Clone and index a GitHub repository into the Gristle code graph.

    Clones the repository into local storage (GRISTLE_REPO_STORAGE_PATH), runs
    full ingestion, and keeps the clone so source-loading tools (explore,
    impact) work afterward. The code graph persists in FalkorDB; call
    gristle_drop to remove both the graph and the stored clone.

    Args:
        repo_url: GitHub repository — either "owner/repo" or a full URL
                  like "https://github.com/owner/repo".
        token: Optional GitHub personal access token for private repos.
        ref: Optional branch, tag, or commit SHA to check out.
        repo_id: Optional short identifier for this repo. Defaults to a
                 hash derived from the repo URL.
    """
    import shutil

    from git import Repo

    # Normalize repo_url to a clone-able HTTPS URL
    if repo_url.startswith(("http://", "https://")):
        clone_url = repo_url
    else:
        # "owner/repo" shorthand
        clone_url = f"https://github.com/{repo_url}.git"

    # Inject token into URL for private repos
    if token and clone_url.startswith("https://"):
        clone_url = clone_url.replace("https://", f"https://x-access-token:{token}@", 1)

    # Derive a stable repo_id from the URL (strip token first)
    clean_url = repo_url if not repo_url.startswith("http") else repo_url
    rid = repo_id or GraphClient.repo_id_from_path(clean_url)

    # Clone into persistent storage (not a temp dir) so source loading and
    # engine rehydration keep working after ingestion / a server restart.
    clone_path = (settings.repo_storage_path / rid).resolve()
    clone_dir = str(clone_path)
    try:
        if clone_path.exists():
            shutil.rmtree(clone_path, ignore_errors=True)
        clone_path.parent.mkdir(parents=True, exist_ok=True)

        # Clone (shallow for speed) — run in thread to avoid blocking event loop
        clone_kwargs: dict = {"depth": 1}
        if ref:
            clone_kwargs["branch"] = ref

        def _clone() -> float:
            with Timer() as t:
                logger.info("Cloning %s to %s", repo_url, clone_dir)
                Repo.clone_from(clone_url, clone_dir, **clone_kwargs)
            return t.ms

        clone_ms = await asyncio.to_thread(_clone)

        logger.info(
            "Clone completed",
            extra={"event": "clone_done", "repo_id": rid, "duration_ms": clone_ms},
        )

        # Ingest using existing pipeline — run in thread to avoid blocking event loop
        graph = GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            repo_id=rid,
            password=settings.falkordb_password,
        )
        pipeline = IngestionPipeline(graph, _registry)

        def _ingest():
            with Timer() as t:
                res = pipeline.ingest_repo(clone_dir)
            return res, t.ms

        result, ingest_ms = await asyncio.to_thread(_ingest)

        engine = QueryEngine(graph, repo_path=clone_dir)
        _engines[rid] = engine
        _pipelines[rid] = pipeline

        logger.info(
            "gristle_ingest_github completed for %s",
            rid,
            extra={
                "event": "tool_ingest_github",
                "repo_id": rid,
                "duration_ms": clone_ms + ingest_ms,
                "files": result.files_processed,
                "nodes": result.nodes_created,
                "rels": result.relationships_created,
            },
        )

        return {
            "status": "success",
            "repo_id": rid,
            "graph_name": graph.graph_name,
            "files_processed": result.files_processed,
            "files_skipped": result.files_skipped,
            "docs_processed": result.docs_processed,
            "nodes_created": result.nodes_created,
            "relationships_created": result.relationships_created,
            "routes_found": result.routes_found,
            "components_found": result.components_found,
            "test_files_found": result.test_files_found,
            "test_cases_found": result.test_cases_found,
            "dependencies_found": result.dependencies_found,
            "test_coverage_edges": result.test_coverage_edges,
            "models_found": result.models_found,
            "model_fields_found": result.model_fields_found,
            "model_relations_found": result.model_relations_found,
            "duration_ms": clone_ms + ingest_ms,
            "errors": result.errors[:10] if result.errors else [],
        }
    except Exception as e:
        logger.error("Failed to ingest %s: %s", repo_url, e, exc_info=True)
        # Clean up a partial/failed clone; a successful clone is kept on disk
        # so source loading keeps working (removed by gristle_drop).
        shutil.rmtree(clone_path, ignore_errors=True)
        return {"error": str(e)}


@mcp.tool()
async def gristle_watch(
    action: str = "status",
    repo_id: str | None = None,
) -> dict:
    """Start, stop, or check the file watcher for incremental re-indexing.

    When watching is active, code changes are automatically detected and
    the graph is updated within a few seconds.

    Args:
        action: One of 'start', 'stop', or 'status' (default).
        repo_id: Repository identifier. Defaults to last ingested repo.
    """
    from gristle.ingestion.watcher import is_watching, start_watching, stop_watching

    # Resolve repo_id
    rid = repo_id
    if not rid and _engines:
        rid = list(_engines.keys())[-1]
    if not rid:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    if action == "status":
        return {
            "repo_id": rid,
            "watching": is_watching(rid),
        }

    if action == "start":
        pipeline = _pipelines.get(rid)
        engine = _engines.get(rid)
        if not pipeline or not engine:
            return {"error": f"Repo '{rid}' not found. Call gristle_ingest first."}
        if not engine.repo_path:
            return {"error": f"Repo '{rid}' has no source path on record. Re-ingest first."}
        started = start_watching(rid, engine.repo_path, pipeline)
        return {
            "repo_id": rid,
            "watching": True,
            "started": started,
            "note": "Already watching" if not started else "Watcher started",
        }

    if action == "stop":
        stopped = stop_watching(rid)
        return {
            "repo_id": rid,
            "watching": False,
            "stopped": stopped,
            "note": "Watcher stopped" if stopped else "Was not watching",
        }

    return {"error": f"Unknown action: {action}. Use 'start', 'stop', or 'status'."}


@mcp.tool()
async def gristle_explore(
    entity: str,
    repo_id: str | None = None,
) -> dict:
    """Explore a code entity — function, class, or file.

    Use this to understand what something is, what it contains, and how
    it fits into the codebase. Automatically detects the entity type.

    Args:
        entity: A function name, class name, qualified name, or file path.
        repo_id: Repository identifier. If omitted, uses the most recently
                 ingested repo.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    # Try function first
    ctx = engine.get_function_context(entity)
    if ctx:
        docs = engine.get_docs_for_entity(entity)
        if docs:
            ctx["referenced_in_docs"] = docs
        return {"type": "function", **ctx}

    # Try class
    cls = engine.get_class_structure(entity)
    if cls:
        docs = engine.get_docs_for_entity(entity)
        if docs:
            cls["referenced_in_docs"] = docs
        return {"type": "class", **cls}

    # Try file path
    overview = engine.get_file_overview(entity)
    if overview:
        return {"type": "file", **overview}

    # Fallback: search
    results = engine.search(entity, limit=10)
    if results:
        return {
            "type": "search_results",
            "note": f"No exact match for '{entity}'. Showing search results.",
            "results": results,
        }

    return {"error": f"Nothing found for '{entity}'."}


@mcp.tool()
async def gristle_impact(
    entity_name: str,
    repo_id: str | None = None,
) -> dict:
    """Analyze what would be affected if you change a function or class.

    ALWAYS use this before modifying code to understand the blast radius.
    Returns direct callers, affected files, and transitive impact.

    Call edges are name/heuristic-resolved, not type-resolved, so each direct
    caller comes with how reliably it was resolved:
    - direct_callers_detail: [{caller, resolution, confidence}] where confidence is
      high / medium / low (see gristle://schema for the resolution strategies)
    - low_confidence_callers: the subset worth verifying by hand before you rely on it

    Args:
        entity_name: Name of the function or class to analyze.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.impact_analysis(entity_name)
    if result is None:
        return {"error": f"Entity '{entity_name}' not found."}

    return result


@mcp.tool()
async def gristle_impact_score(
    entity_name: str,
    include_source: bool = False,
    repo_id: str | None = None,
) -> dict:
    """Analyze change impact with blast radius scoring (0-100).

    Returns enhanced impact analysis with:
    - blast_radius_score (0-100): Combined impact metric
    - risk_level: low/medium/high/critical classification
    - direct_impact_score: Based on callers, callbacks, routes
    - transitive_impact_score: Based on affected files, test coverage

    Higher scores = more risky to modify. Critical (85+) requires extra care.

    Args:
        entity_name: Name of the function or class to analyze.
        include_source: Include source code in response (default False).
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.get_impact_analysis(entity_name, include_source=include_source)
    if result is None:
        return {"error": f"Entity '{entity_name}' not found."}

    return result


@mcp.tool()
async def gristle_change_impact(
    entity_name: str,
    repo_id: str | None = None,
) -> dict:
    """Pre-edit safety check: what breaks if you change this, and what to run.

    Call this BEFORE modifying a function or class. It bundles, in one response:
    - blast_radius_score (0-100) + risk_level (low/medium/high/critical)
    - direct_callers and affected_files
    - direct_callers_detail: each caller with the confidence of its call edge
      (high/medium/low), plus low_confidence_callers — edges to verify by hand,
      since call resolution is name-based, not type-resolved
    - tests_to_run: the exact covering tests to run before and after the change
    - recommendation: a one-line summary

    Saves chaining gristle_impact_score + gristle_tests — the whole "is this safe
    to edit, and how do I verify it" question in a single call.

    Args:
        entity_name: Name of the function or class you're about to change.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.get_change_impact(entity_name)
    if result is None:
        return {"error": f"Entity '{entity_name}' not found."}

    return result


@mcp.tool()
async def gristle_changeset_impact(
    entity_names: list[str],
    repo_id: str | None = None,
) -> dict:
    """Pre-edit safety check for a SET of entities you're changing together.

    Like gristle_change_impact, but for a whole diff. Pass every function/class
    your change touches and get one aggregated view:
    - external_callers: callers OUTSIDE the changeset (co-edited symbols aren't
      blast radius) — the real surface this edit might break
    - tests_to_run: the de-duplicated union of every entity's covering tests
    - affected_files: files touched by external callers (excludes files you're editing)
    - overall_risk_level + max_blast_radius_score: worst case across the set
    - low_confidence_callers: external callers whose call edge was weakly resolved —
      verify these by hand before trusting the blast radius
    - entities: per-entity risk summary; not_found: names that didn't resolve

    Use this when an edit spans multiple functions/files to vet the combined
    blast radius and the full test set in a single call.

    Args:
        entity_names: Names of the functions/classes your change touches.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    return engine.get_changeset_impact(entity_names)


@mcp.tool()
async def gristle_trace(
    from_entity: str,
    to_entity: str,
    max_hops: int = 5,
    repo_id: str | None = None,
) -> dict:
    """Find how two code entities are connected through call relationships.

    Use this for understanding data flow, tracing execution paths, or
    discovering architectural connections.

    Args:
        from_entity: Starting function name.
        to_entity: Target function name.
        max_hops: Maximum path length (default 5).
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    paths = engine.find_path(from_entity, to_entity, max_hops)
    if not paths:
        return {
            "note": f"No call path found from '{from_entity}' to '{to_entity}' within {max_hops} hops.",
        }

    return {"from": from_entity, "to": to_entity, "paths": paths}


@mcp.tool()
async def gristle_search(
    query: str,
    search_type: str = "all",
    limit: int = 20,
    repo_id: str | None = None,
) -> dict:
    """Search the codebase for functions, classes, or files.

    Use this when you don't know where something is defined or to discover
    related functionality.

    Args:
        query: Search term (name, partial name, or docstring text).
        search_type: One of 'name', 'docstring', or 'all' (default).
        limit: Maximum results (default 20).
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    results = engine.search(query, search_type=search_type, limit=limit)
    return {"query": query, "count": len(results), "results": results}


@mcp.tool()
async def gristle_docs(
    entity: str | None = None,
    mode: str = "find",
    repo_id: str | None = None,
) -> dict:
    """Query documentation and its relationship to code.

    Use this to find docs that reference a code entity, check if docs are
    stale, or get an overview of all indexed documentation.

    Args:
        entity: Code entity name or file path to find docs for.
                Required when mode is 'find'.
        mode: One of:
              - 'find': Find documentation that references the given entity.
              - 'staleness': List documents with potentially stale code refs.
              - 'overview': Get summary statistics of all indexed docs.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    if mode == "overview":
        return engine.get_doc_overview()

    if mode == "staleness":
        results = engine.get_doc_staleness()
        return {"count": len(results), "documents": results}

    # Default: find docs for entity
    if not entity:
        return {"error": "Entity name required for 'find' mode."}

    results = engine.get_docs_for_entity(entity)
    if not results:
        return {"note": f"No documentation references found for '{entity}'."}

    return {"entity": entity, "count": len(results), "documents": results}


@mcp.tool()
async def gristle_routes(
    method: str | None = None,
    repo_id: str | None = None,
) -> dict:
    """List all HTTP routes/API endpoints in the codebase.

    Use this to understand the API surface area, find endpoints to modify,
    or discover what routes exist before adding a new one.

    Args:
        method: Optional HTTP method filter (GET, POST, PUT, DELETE, etc.).
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    routes = engine.get_routes(method)
    return {"count": len(routes), "routes": routes}


@mcp.tool()
async def gristle_components(
    limit: int = 50,
    include_docs: bool = False,
    repo_id: str | None = None,
) -> dict:
    """List React/UI components with usage counts.

    Use this to understand the component hierarchy, find reusable
    components, or identify unused ones.

    Args:
        limit: Maximum results (default 50).
        include_docs: Include components in documentation/mockup directories (default False).
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    components = engine.get_components(limit, exclude_docs=not include_docs)
    return {"count": len(components), "components": components}


@mcp.tool()
async def gristle_deps(
    name: str | None = None,
    limit: int = 50,
    repo_id: str | None = None,
) -> dict:
    """Query external dependencies (npm packages, Python packages).

    Use this to understand which third-party libraries are used, find all
    code that depends on a specific package, or assess dependency impact.

    Args:
        name: Specific dependency name to drill into (e.g. 'redis', '@hono/zod-validator').
              If omitted, lists all dependencies ranked by usage.
        limit: Maximum results when listing all dependencies (default 50).
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    if name:
        result = engine.get_dependency_users(name)
        if not result["files"] and not result["functions"]:
            return {"note": f"No usage found for dependency '{name}'."}
        return result

    deps = engine.get_dependencies(limit)
    return {"count": len(deps), "dependencies": deps}


@mcp.tool()
async def gristle_tests(
    entity: str | None = None,
    mode: str = "coverage",
    repo_id: str | None = None,
) -> dict:
    """Query test coverage and find tests for code entities.

    Args:
        entity: Code entity name to find tests for (required for 'find' and
                'coverage_detail' modes).
        mode: One of:
              - 'find': Find tests that exercise a specific entity.
              - 'coverage': Find exported functions with no test coverage.
              - 'coverage_detail': Detailed coverage for a specific function
                (tested_by_count, which tests at what depth).
              - 'untested_critical': Exported functions with callers but no tests.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    if mode == "find":
        if not entity:
            return {"error": "Entity name required for 'find' mode."}
        results = engine.get_tests_for_entity(entity)
        if not results:
            return {"note": f"No tests found that exercise '{entity}'."}
        return {"entity": entity, "count": len(results), "tests": results}

    if mode == "coverage_detail":
        if not entity:
            return {"error": "Entity name required for 'coverage_detail' mode."}
        return engine.get_function_coverage(entity)

    if mode == "untested_critical":
        results = engine.get_untested_critical()
        return {"count": len(results), "untested_critical": results}

    # Default: untested functions
    results = engine.get_untested_functions()
    return {"count": len(results), "untested_functions": results}


@mcp.tool()
async def gristle_conventions(
    repo_id: str | None = None,
) -> dict:
    """Infer project conventions, patterns, and structure from the code graph.

    Use this FIRST when starting work on an unfamiliar codebase. Returns
    detected patterns for file organization, component locations, test
    structure, routes, entry points, commonly imported modules, and
    architectural layer violations (e.g. presentation importing directly
    from data layer, bypassing business logic).

    Args:
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    conventions = engine.infer_conventions()
    overview = engine.get_repo_overview()

    # Add TODO summary
    todos = engine.get_todos(limit=10)

    return {
        "project_overview": overview,
        "conventions": conventions,
        "top_todo_files": todos,
    }


@mcp.tool()
async def gristle_embed(
    repo_id: str | None = None,
) -> dict:
    """Build semantic search index for a repository.

    Generates vector embeddings for all functions and classes so you can
    use gristle_semantic_search to find code by description. Requires
    the sentence-transformers package (pip install gristle[search]).

    Run this after gristle_ingest to enable semantic search.

    Args:
        repo_id: Repository identifier.
    """
    rid = repo_id or (list(_engines.keys())[-1] if _engines else None)
    if not rid or rid not in _engines:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    try:
        from gristle.search.embeddings import CodeEmbedder, SemanticIndex
    except ImportError:
        return {"error": "sentence-transformers not installed. Run: pip install gristle[search]"}

    engine = _engines[rid]
    graph = engine.graph  # Access the graph client from engine

    embedder = CodeEmbedder()
    index = SemanticIndex(graph, embedder)
    index.create_indexes()
    counts = index.index_all()

    _semantic_indexes[rid] = index

    return {
        "status": "success",
        "repo_id": rid,
        "model": "all-MiniLM-L6-v2",
        "dimension": embedder.dimension,
        "functions_indexed": counts.get("Function", 0),
        "classes_indexed": counts.get("Class", 0),
    }


@mcp.tool()
async def gristle_semantic_search(
    query: str,
    limit: int = 10,
    repo_id: str | None = None,
) -> dict:
    """Find code by description using semantic similarity.

    Use this when you want to find code that DOES something, rather than
    searching by name. For example: "validates email addresses",
    "handles authentication", "connects to the database".

    Requires gristle_embed to have been run first.

    Args:
        query: Natural language description of what the code does.
        limit: Maximum results (default 10).
        repo_id: Repository identifier.
    """
    rid = repo_id or (list(_engines.keys())[-1] if _engines else None)
    if not rid:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    index = _semantic_indexes.get(rid)
    if index is None:
        # Try to reconstruct from existing graph (embeddings may already exist)
        try:
            from gristle.search.embeddings import CodeEmbedder, SemanticIndex

            engine = _engines[rid]
            embedder = CodeEmbedder()
            index = SemanticIndex(engine.graph, embedder)
            _semantic_indexes[rid] = index
        except ImportError:
            return {"error": "sentence-transformers not installed. Run: pip install gristle[search]"}
        except Exception as e:
            return {"error": f"Failed to initialize semantic search: {e}"}

    results = index.search(query, limit=limit)
    if not results:
        return {"note": f"No semantic matches for '{query}'. Run gristle_embed first if you haven't already."}

    # Format results
    formatted = []
    for r in results:
        formatted.append(
            {
                "name": r["name"],
                "type": r["label"],
                "signature": r["signature"],
                "docstring": r.get("docstring") or None,
                "file": r["file_path"],
                "similarity": round(1 - r["score"], 3),  # Convert distance to similarity
            }
        )

    return {
        "query": query,
        "count": len(formatted),
        "results": formatted,
    }


@mcp.tool()
async def gristle_drop(
    repo_id: str,
) -> dict:
    """Drop a repository's code graph from FalkorDB.

    Use this to clean up ephemeral graphs after analysis is complete.
    Removes all graph data and frees memory.

    Args:
        repo_id: Repository identifier to drop.
    """
    engine = _engines.pop(repo_id, None)
    _pipelines.pop(repo_id, None)
    _semantic_indexes.pop(repo_id, None)

    # Remove any stored GitHub clone for this repo (kept by gristle_ingest_github).
    import shutil

    shutil.rmtree(settings.repo_storage_path / repo_id, ignore_errors=True)

    if engine is None:
        # Still try to drop the graph directly (e.g. ingested before a restart)
        graph = GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            repo_id=repo_id,
            password=settings.falkordb_password,
        )
        graph.drop()
        return {"status": "dropped", "repo_id": repo_id, "was_loaded": False}

    engine.graph.drop()
    return {"status": "dropped", "repo_id": repo_id, "was_loaded": True}


@mcp.tool()
async def gristle_config(
    mode: str = "env_vars",
    repo_id: str | None = None,
) -> dict:
    """Query config files and environment variables in the codebase.

    Modes:
    - **env_vars**: List all environment variables with where they're defined and used.
    - **config_files**: List config files (Dockerfile, docker-compose, CI, etc.) with types.
    - **setup_requirements**: Full setup checklist — required env vars, config files, dependencies.

    Args:
        mode: Query mode — "env_vars", "config_files", or "setup_requirements".
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    if mode == "env_vars":
        return engine.get_env_vars()
    elif mode == "config_files":
        return engine.get_config_files()
    elif mode == "setup_requirements":
        return engine.get_setup_requirements()
    else:
        return {"error": f"Unknown mode: {mode}. Use 'env_vars', 'config_files', or 'setup_requirements'."}


@mcp.tool()
async def gristle_data_contract(
    entity_name: str,
    repo_id: str | None = None,
) -> dict:
    """Get the input/output data contract for a function.

    Shows what types a function accepts as parameters and returns,
    including the fields of those types. Useful for understanding
    API boundaries and data flow between modules.

    Args:
        entity_name: Function name or qualified name.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.get_data_contract(entity_name)
    if result is None:
        return {"error": f"Entity '{entity_name}' not found."}

    return result


@mcp.tool()
async def gristle_type_usage(
    type_name: str,
    repo_id: str | None = None,
) -> dict:
    """Find all usage of a type across the codebase.

    Shows functions that accept or return this type, and other types
    that reference it in their fields. Useful for understanding type
    dependencies and impact of type changes.

    Args:
        type_name: Type, interface, or class name.
        repo_id: Repository identifier.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.get_type_usage(type_name)
    if result is None:
        return {"error": f"Type '{type_name}' not found."}

    return result


@mcp.tool()
async def gristle_dead_exports(
    repo_id: str | None = None,
) -> dict:
    """Find exported functions/classes that are never imported by other files.

    Identifies unused public API surface — entities that are exported but
    never imported. Excludes entry points (they're meant to be external).
    Useful for finding dead code in barrel files and library APIs.

    Returns counts and a list of dead exports with their location.

    Args:
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.detect_dead_exports()


@mcp.tool()
async def gristle_cycles(
    max_length: int = 10,
    repo_id: str | None = None,
) -> dict:
    """Detect circular import dependencies in the codebase.

    Finds all import cycles up to max_length. Returns cycle paths as lists
    of file paths, grouped by cycle length. Cycles are deduplicated.

    Args:
        max_length: Maximum cycle length to detect (default: 10). Lower values are faster.
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.detect_import_cycles(max_length=max_length)


@mcp.tool()
async def gristle_public_api(
    include_internal: bool = False,
    repo_id: str | None = None,
) -> dict:
    """List all public API entities (exported functions and classes).

    Returns the public API surface — all exported, non-test entities.
    Excludes files in paths containing 'internal', '__', or '_private'
    unless include_internal=True.

    Useful for documenting library APIs or understanding what's exposed.

    Args:
        include_internal: Include entities from internal paths (default: False).
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.get_public_api(include_internal=include_internal)


@mcp.tool()
async def gristle_security(
    repo_id: str | None = None,
) -> dict:
    """Combined security overview: code findings + unauthenticated routes + vulnerable deps.

    Detects hardcoded secrets, SQL injection risks, unsafe calls (eval, exec,
    pickle), LLM insecure output handling (OWASP LLM05), routes lacking
    authentication decorators or middleware, and dependencies with known CVEs.

    Returns total issue count, code findings grouped by category, unauthenticated
    routes, and vulnerable dependencies.

    Args:
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.get_security_overview()


@mcp.tool()
async def gristle_unauthenticated_routes(
    repo_id: str | None = None,
) -> dict:
    """Find HTTP routes whose handlers lack authentication decorators or middleware.

    Checks route handlers for common auth patterns (login_required, jwt,
    protect, verify, etc.) and middleware presence. Routes without any auth
    indicator are flagged for review.

    Useful for focused auth audits — call gristle_security for the full picture.

    Args:
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.detect_unauthenticated_routes()


@mcp.tool()
async def gristle_dependency_health(
    severity: str = "all",
    repo_id: str | None = None,
) -> dict:
    """Check dependency staleness and known vulnerabilities.

    Finds outdated dependencies by comparing declared versions against latest
    releases from npm/PyPI registries. Also reports known CVEs from OSV.dev.

    Args:
        severity: Filter level — "all" (all outdated), "vulnerable" (CVEs only),
                  "safe" (outdated but no CVEs). Default "all".
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.get_outdated_dependencies(severity=severity)


@mcp.tool()
async def gristle_services(
    repo_id: str | None = None,
) -> dict:
    """Map external services and integrations used by the codebase.

    Classifies dependencies into categories: database, auth, payments, email,
    AI, storage, analytics, UI, forms, and state management. Returns matched
    packages per category plus an uncategorized list.

    Useful for understanding a project's service architecture at a glance.

    Args:
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.get_external_services()


@mcp.tool()
async def gristle_changelog(
    repo_id: str | None = None,
) -> dict:
    """Show what changed since last ingestion.

    Compares the current graph state against the previous ingestion snapshot.
    Returns counts and deltas for files, functions, classes, routes, tests,
    components, dependencies, and edges.

    Useful for understanding the impact of recent code changes.

    Args:
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository loaded. Run gristle_ingest first."}

    return engine.get_changelog()


@mcp.tool()
async def gristle_models(repo_id: str | None = None) -> dict:
    """List all database models with their fields and relationships.

    Returns models detected from Prisma schemas, Drizzle table definitions,
    and ORM class patterns (TypeORM, SQLAlchemy, Django, etc.).

    Each model includes: name, ORM framework, table name, fields with types
    and constraints, and relationships to other models.

    Args:
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    return engine.get_models()


@mcp.tool()
async def gristle_model_detail(model_name: str, repo_id: str | None = None) -> dict:
    """Get detailed information about a specific database model.

    Returns the model definition including all fields with full constraint
    details, all relationships (incoming and outgoing), and which functions
    read/write this model's data.

    Args:
        model_name: Name of the model to look up (e.g. "User", "Post").
        repo_id: Repository identifier (optional, uses most recent if omitted).
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    return engine.get_model_detail(model_name)


@mcp.tool()
async def gristle_subgraph(
    view: Literal["call_hierarchy", "blast_radius", "request_trace"],
    center: str | None = None,
    depth: int = 2,
    edge_types: list[str] | None = None,
    repo_id: str | None = None,
    models_only: bool = False,
) -> dict:
    """Return a {nodes, edges, meta} subgraph for a code-visualization VIEW.

    Use this to SEE relationships, not just list them — the JSON is directly
    renderable (and `gristle viz` will turn the same data into a shareable HTML
    file in a later release):

    - call_hierarchy — who calls X and what X calls, transitively (center required)
    - blast_radius   — what breaks if X changes; includes covering tests + routes
    - request_trace  — HTTP route -> handler -> functions -> DB model, end to end
                       (center optional: a route path/id, or omit for all routes;
                       pass models_only=true to prune to just the route->DB paths)

    Each node carries {id, label, props}; each edge {source, target, type} plus
    edge metadata where it applies (CALLS `resolution` confidence, USES_MODEL
    read/write `access`). `center` accepts a business id (func::…) or a
    qualified_name. Returns meta.truncated when the result was capped at
    GRISTLE_VIZ_MAX_NODES.

    Args:
        view: Which subgraph to build (see above).
        center: Focal function/route — business id or qualified_name (path for routes).
        depth: Traversal depth, clamped to 1..4.
        edge_types: Override which edge types appear (defaults to the view's set).
        repo_id: Repository identifier (uses most recent if omitted).
        models_only: request_trace only — prune to nodes on a path to a DB Model.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": f"Repo '{repo_id or '(default)'}' not found. Call gristle_ingest first."}

    return engine.get_subgraph(view=view, center=center, depth=depth, edge_types=edge_types, models_only=models_only)


# ======================================================================
# Resources
# ======================================================================


@mcp.resource(
    "gristle://repos",
    name="Ingested Repositories",
    description="List of all currently ingested repositories",
    mime_type="application/json",
)
async def list_repos() -> str:
    repos: list[dict[str, Any]] = []
    for rid, engine in _engines.items():
        repos.append({"repo_id": rid, "repo_path": engine.repo_path, "loaded": True})
    # Also surface graphs that exist in FalkorDB but aren't loaded in this
    # process (e.g. ingested before a restart) so they're discoverable.
    try:
        loaded_graph_names = {e.graph.graph_name for e in _engines.values()}
        probe = GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
        )
        for gname in probe.list_gristle_graphs():
            if gname not in loaded_graph_names:
                repos.append({"graph_name": gname, "loaded": False})
    except Exception:
        logger.debug("Could not enumerate FalkorDB graphs", exc_info=True)
    return json.dumps(repos, indent=2)


@mcp.resource(
    "gristle://repos/{repo_id}/overview",
    name="Repository Overview",
    description="Statistics and structure for a specific repo",
    mime_type="application/json",
)
async def repo_overview(repo_id: str) -> str:
    engine = _resolve_engine(repo_id)
    if engine is None:
        return json.dumps({"error": f"Repo '{repo_id}' not found."})
    return json.dumps(engine.get_repo_overview(), indent=2, default=str)


# ======================================================================
# Helpers
# ======================================================================


def _resolve_engine(repo_id: str | None) -> QueryEngine | None:
    """Resolve a repo_id to a QueryEngine, defaulting to the last ingested.

    If the repo isn't loaded in this process but its graph still exists in
    FalkorDB (e.g. after a server restart), rehydrate a read-only engine from it
    instead of reporting "not ingested" and forcing a destructive re-ingest.
    """
    if repo_id:
        engine = _engines.get(repo_id)
        if engine is None:
            engine = _rehydrate_engine(repo_id)
        return engine
    if _engines:
        # Return the most recently added
        return list(_engines.values())[-1]
    return None


def _rehydrate_engine(repo_id: str) -> QueryEngine | None:
    """Rebuild a QueryEngine from an existing FalkorDB graph, or None if absent."""
    try:
        graph = GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            repo_id=repo_id,
            password=settings.falkordb_password,
        )
        if not graph.graph_exists():
            return None
    except RedisConnectionError:
        # FalkorDB unreachable (the client connects eagerly on construction): we
        # can't rehydrate, so treat the repo as unavailable (None) rather than
        # letting the error escape — callers like the repo_overview resource
        # aren't wrapped by the tool error boundary.
        logger.debug("FalkorDB unreachable while rehydrating '%s'; treating as unavailable", repo_id)
        return None
    repo_path: str | None = None
    try:
        rows = graph.execute("MATCH (s:Snapshot) RETURN s.repo_path AS p ORDER BY s.captured_at DESC LIMIT 1").records
        if rows and rows[0].get("p"):
            repo_path = rows[0]["p"]
    except Exception:
        logger.debug("Could not read repo_path from snapshot for %s", repo_id, exc_info=True)
    engine = QueryEngine(graph, repo_path=repo_path)
    _engines[repo_id] = engine
    logger.info("Rehydrated engine for repo '%s' from existing FalkorDB graph", repo_id)
    return engine


# ======================================================================
# Entry point
# ======================================================================


def main():
    from gristle.logging import configure_logging

    transport = settings.transport
    if transport not in ("stdio", "streamable-http"):
        raise SystemExit(f"Unknown transport: {transport}. Use 'stdio' or 'streamable-http'.")

    configure_logging(transport)
    logger.info("Starting Gristle MCP server (transport=%s)", transport)
    mcp.run(transport=cast("Literal['stdio', 'streamable-http']", transport))


if __name__ == "__main__":
    main()
