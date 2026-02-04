"""Gristle MCP server: exposes code graph intelligence to AI agents."""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from mcp.server.fastmcp import FastMCP
from starlette.responses import JSONResponse

from gristle.config import settings
from gristle.graph.client import GraphClient
from gristle.ingestion.pipeline import IngestionPipeline
from gristle.logging import Timer
from gristle.parsers.registry import ParserRegistry
from gristle.query.engine import QueryEngine

if TYPE_CHECKING:
    from starlette.requests import Request

logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# Auth setup — only enabled when GRISTLE_API_KEY is set
# ------------------------------------------------------------------

_token_verifier = None
_auth_settings = None

if settings.api_key:
    from mcp.server.auth.settings import AuthSettings

    from gristle.mcp.auth import ApiKeyVerifier

    _token_verifier = ApiKeyVerifier(settings.api_key)
    _auth_settings = AuthSettings(
        issuer_url="https://gristle.local",
        resource_server_url="https://gristle.local",
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
_semantic_indexes: dict[str, object] = {}  # repo_id -> SemanticIndex (if available)
_registry = ParserRegistry().build_default()


def _get_engine(repo_id: str) -> QueryEngine | None:
    return _engines.get(repo_id)


# ======================================================================
# Health check (bypasses auth)
# ======================================================================


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request: Request) -> JSONResponse:
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

    with Timer() as t:
        result = pipeline.ingest_repo(repo_path_resolved)

    engine = QueryEngine(graph, repo_path=repo_path_resolved)
    _engines[rid] = engine
    _pipelines[rid] = pipeline

    logger.info(
        "gristle_ingest completed for %s",
        rid,
        extra={
            "event": "tool_ingest",
            "repo_id": rid,
            "duration_ms": t.ms,
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
        "duration_ms": t.ms,
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

    Clones the repository to a temporary directory, runs full ingestion,
    then deletes the clone. The code graph persists in FalkorDB.

    Args:
        repo_url: GitHub repository — either "owner/repo" or a full URL
                  like "https://github.com/owner/repo".
        token: Optional GitHub personal access token for private repos.
        ref: Optional branch, tag, or commit SHA to check out.
        repo_id: Optional short identifier for this repo. Defaults to a
                 hash derived from the repo URL.
    """
    import shutil
    import tempfile

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

    tmp_dir = tempfile.mkdtemp(prefix="gristle_")
    try:
        # Clone (shallow for speed)
        clone_kwargs: dict = {"depth": 1}
        if ref:
            clone_kwargs["branch"] = ref

        with Timer() as clone_timer:
            logger.info("Cloning %s to %s", repo_url, tmp_dir)
            Repo.clone_from(clone_url, tmp_dir, **clone_kwargs)

        logger.info(
            "Clone completed",
            extra={"event": "clone_done", "repo_id": rid, "duration_ms": clone_timer.ms},
        )

        # Ingest using existing pipeline
        graph = GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            repo_id=rid,
            password=settings.falkordb_password,
        )
        pipeline = IngestionPipeline(graph, _registry)

        with Timer() as ingest_timer:
            result = pipeline.ingest_repo(tmp_dir)

        engine = QueryEngine(graph, repo_path=tmp_dir)
        _engines[rid] = engine
        _pipelines[rid] = pipeline

        logger.info(
            "gristle_ingest_github completed for %s",
            rid,
            extra={
                "event": "tool_ingest_github",
                "repo_id": rid,
                "duration_ms": clone_timer.ms + ingest_timer.ms,
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
            "duration_ms": clone_timer.ms + ingest_timer.ms,
            "errors": result.errors[:10] if result.errors else [],
        }
    except Exception as e:
        logger.error("Failed to ingest %s: %s", repo_url, e, exc_info=True)
        return {"error": str(e)}
    finally:
        # Always clean up the clone
        shutil.rmtree(tmp_dir, ignore_errors=True)


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

    if engine is None:
        # Still try to drop the graph directly
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
    repos = []
    for rid, engine in _engines.items():
        repos.append({"repo_id": rid, "repo_path": engine.repo_path})
    return json.dumps(repos, indent=2)


@mcp.resource(
    "gristle://repos/{repo_id}/overview",
    name="Repository Overview",
    description="Statistics and structure for a specific repo",
    mime_type="application/json",
)
async def repo_overview(repo_id: str) -> str:
    engine = _engines.get(repo_id)
    if engine is None:
        return json.dumps({"error": f"Repo '{repo_id}' not found."})
    return json.dumps(engine.get_repo_overview(), indent=2, default=str)


# ======================================================================
# Helpers
# ======================================================================


def _resolve_engine(repo_id: str | None) -> QueryEngine | None:
    """Resolve a repo_id to a QueryEngine, defaulting to the last ingested."""
    if repo_id:
        return _engines.get(repo_id)
    if _engines:
        # Return the most recently added
        return list(_engines.values())[-1]
    return None


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
    mcp.run(transport=transport)


if __name__ == "__main__":
    main()
