"""Live-FalkorDB smoke test over QueryEngine's Cypher-semantic methods.

WHY THIS EXISTS
---------------
The rest of the suite uses a mock graph client that never executes Cypher. That
means a query method can be 100% broken against real FalkorDB -- e.g. an
``EXISTS { subquery }`` form FalkorDB doesn't support, or a variable-length
bound passed as a query *parameter* (which arrives as a literal ``{max_len}``) --
and still pass every mock test. That false-green let two shipped tools
(``detect_import_cycles``, ``detect_dead_exports``) error on EVERY real call
while CI stayed green (fixed in 12d7527).

This test ingests a real fixture into a live FalkorDB and asserts that each
Cypher-semantic method *executes without raising*. A FalkorDB-incompatible query
raises at ``graph.execute`` time, so "did not raise" is exactly the guard we want.

It SKIPS when no FalkorDB is reachable, so CI (mock-only) stays green and this
runs only where a real instance is available (locally / a live-marked job).
Point it at an instance via ``GRISTLE_FALKORDB_HOST`` / ``GRISTLE_FALKORDB_PORT``
(defaults: localhost:6390).
"""

from __future__ import annotations

import uuid
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from gristle.config import settings
from gristle.graph.client import GraphClient
from gristle.ingestion.pipeline import IngestionPipeline
from gristle.parsers.registry import ParserRegistry
from gristle.query.engine import QueryEngine

if TYPE_CHECKING:
    from collections.abc import Callable

SAMPLE_PYTHON_DIR = Path(__file__).parent / "fixtures" / "sample_python"


def _falkordb_reachable() -> bool:
    try:
        return GraphClient(
            host=settings.falkordb_host,
            port=settings.falkordb_port,
            password=settings.falkordb_password,
            repo_id="smoke_ping",
        ).ping()
    except Exception:
        return False


# Evaluated at collection time: no FalkorDB -> whole module skips, CI stays mock-only.
pytestmark = pytest.mark.skipif(
    not _falkordb_reachable(),
    reason="No live FalkorDB reachable; Cypher-semantic smoke test skipped (CI stays mock-only).",
)


@pytest.fixture(scope="module")
def live_engine() -> Any:
    """Ingest the sample fixture into a throwaway graph; drop it on teardown."""
    repo_id = f"smoke_{uuid.uuid4().hex[:8]}"
    graph = GraphClient(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        password=settings.falkordb_password,
        repo_id=repo_id,
    )
    IngestionPipeline(graph, ParserRegistry().build_default()).ingest_repo(str(SAMPLE_PYTHON_DIR))
    try:
        yield QueryEngine(graph)
    finally:
        graph.drop()  # never leave test graphs in the shared instance


@pytest.fixture(scope="module")
def ctx(live_engine: Any) -> dict[str, Any]:
    """Discover representative args (a function, a class, a file) from the graph."""
    g = live_engine.graph
    fn = g.execute(
        "MATCH (f:Function) WHERE f.qualified_name IS NOT NULL RETURN f.qualified_name AS q ORDER BY q LIMIT 1"
    ).records
    cls = g.execute(
        "MATCH (c:Class) WHERE c.qualified_name IS NOT NULL RETURN c.qualified_name AS q ORDER BY q LIMIT 1"
    ).records
    fil = g.execute("MATCH (f:File) WHERE f.path IS NOT NULL RETURN f.path AS p ORDER BY p LIMIT 1").records
    assert fn and cls and fil, "fixture ingest produced no Function/Class/File nodes"
    return {"engine": live_engine, "func": fn[0]["q"], "cls": cls[0]["q"], "file": fil[0]["p"]}


def _methods(ctx: dict[str, Any]) -> dict[str, Callable[[], Any]]:
    """Every Cypher-semantic method, bound to representative args.

    Keep this list in sync with QueryEngine's public query methods so a new
    FalkorDB-incompatible query can't slip through a mock-only suite.
    """
    e, fn, cls, fil = ctx["engine"], ctx["func"], ctx["cls"], ctx["file"]
    return {
        "get_repo_overview": lambda: e.get_repo_overview(),
        "get_function_context": lambda: e.get_function_context(fn),
        "get_class_structure": lambda: e.get_class_structure(cls),
        "get_file_overview": lambda: e.get_file_overview(fil),
        "get_callers": lambda: e.get_callers(fn),
        "get_callees": lambda: e.get_callees(fn),
        "impact_analysis": lambda: e.impact_analysis(fn),
        "get_impact_analysis": lambda: e.get_impact_analysis(fn),
        "get_change_impact": lambda: e.get_change_impact(fn),
        "get_changeset_impact": lambda: e.get_changeset_impact([fn]),
        "find_path": lambda: e.find_path(fn, fn),
        "search": lambda: e.search("user"),
        "get_routes": lambda: e.get_routes(),
        "get_components": lambda: e.get_components(),
        "get_tests_for_entity": lambda: e.get_tests_for_entity(fn),
        "get_function_coverage": lambda: e.get_function_coverage(fn),
        "get_untested_functions": lambda: e.get_untested_functions(),
        "get_untested_critical": lambda: e.get_untested_critical(),
        "get_todos": lambda: e.get_todos(),
        "infer_conventions": lambda: e.infer_conventions(),
        "get_external_services": lambda: e.get_external_services(),
        "detect_layer_violations": lambda: e.detect_layer_violations(),
        "get_dependencies": lambda: e.get_dependencies(),
        "get_outdated_dependencies": lambda: e.get_outdated_dependencies(),
        "get_env_vars": lambda: e.get_env_vars(),
        "get_config_files": lambda: e.get_config_files(),
        "get_setup_requirements": lambda: e.get_setup_requirements(),
        "get_data_contract": lambda: e.get_data_contract(cls),
        "get_type_usage": lambda: e.get_type_usage(cls),
        "detect_dead_exports": lambda: e.detect_dead_exports(),  # was broken (EXISTS{}) -- 12d7527
        "detect_import_cycles": lambda: e.detect_import_cycles(),  # was broken (param var-len) -- 12d7527
        "get_public_api": lambda: e.get_public_api(),
        "detect_security_issues": lambda: e.detect_security_issues(),
        "detect_unauthenticated_routes": lambda: e.detect_unauthenticated_routes(),
        "get_security_overview": lambda: e.get_security_overview(),
        "get_changelog": lambda: e.get_changelog(),  # was broken (Node.get()) on 1st-ingest path
        "get_snapshot_history": lambda: e.get_snapshot_history(),  # returned raw Node objects
        "get_models": lambda: e.get_models(),
        "get_model_relationships": lambda: e.get_model_relationships(),
        "get_db_functions": lambda: e.get_db_functions(),
        "get_docs_for_entity": lambda: e.get_docs_for_entity(fn),
        "get_doc_staleness": lambda: e.get_doc_staleness(),
        "get_doc_overview": lambda: e.get_doc_overview(),
    }


# Static list of method names so pytest can parametrize at collection time;
# the callable is resolved per-test from the (FalkorDB-backed) ctx fixture.
_METHOD_NAMES = sorted(_methods({"engine": None, "func": "", "cls": "", "file": ""}).keys())


@pytest.mark.parametrize("method_name", _METHOD_NAMES)
def test_cypher_method_executes(ctx: dict[str, Any], method_name: str) -> None:
    """Each query method must run against real FalkorDB without raising.

    A FalkorDB-incompatible Cypher raises at execute() time; an empty result
    (no matching data in the small fixture) is a valid pass.
    """
    _methods(ctx)[method_name]()


@pytest.mark.parametrize(
    "view,center_key",
    [
        ("call_hierarchy", "func"),
        ("blast_radius", "func"),
        ("request_trace", None),  # all routes; empty on this fixture but must execute
    ],
)
def test_get_subgraph_view_executes(ctx: dict[str, Any], view: str, center_key: str | None) -> None:
    """The shipped get_subgraph views must execute and return the {meta,nodes,edges} contract."""
    e = ctx["engine"]
    center = ctx[center_key] if center_key else None
    result = e.get_subgraph(view, center=center)
    assert "error" not in result, f"{view} returned an error: {result.get('error')}"
    assert set(result) >= {"meta", "nodes", "edges"}
    ids = {n["id"] for n in result["nodes"]}
    dangling = [ed for ed in result["edges"] if ed["source"] not in ids or ed["target"] not in ids]
    assert not dangling, f"{view} produced dangling edges: {dangling[:3]}"


def test_request_trace_models_only_executes(ctx: dict[str, Any]) -> None:
    """models_only must execute and set the flag (empty is valid if the fixture has no ORM models)."""
    out = ctx["engine"].get_subgraph("request_trace", center=None, models_only=True)
    assert out["meta"]["models_only"] is True
    ids = {n["id"] for n in out["nodes"]}
    assert not [ed for ed in out["edges"] if ed["source"] not in ids or ed["target"] not in ids]
