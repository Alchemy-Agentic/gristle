"""Tests for the MCP server tools, resources, and helpers."""

from __future__ import annotations

import json
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from gristle.graph.client import QueryResult
from gristle.ingestion.pipeline import IngestionResult
from gristle.query.engine import QueryEngine

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _qr(records: list[dict[str, Any]]) -> QueryResult:
    return QueryResult(records=records, summary={})


def _empty() -> QueryResult:
    return _qr([])


def _make_ingestion_result(**overrides) -> IngestionResult:
    defaults = dict(
        repo_id="abc123",
        repo_path="/tmp/repo",
        files_processed=10,
        files_skipped=1,
        docs_processed=2,
        nodes_created=100,
        relationships_created=80,
        doc_references_total=5,
        doc_references_resolved=3,
        routes_found=4,
        components_found=2,
        test_files_found=3,
        test_cases_found=15,
        todos_found=1,
        dependencies_found=6,
        test_coverage_edges=8,
        errors=[],
    )
    defaults.update(overrides)
    return IngestionResult(**defaults)


def _mock_engine(repo_path: str = "/tmp/repo") -> MagicMock:
    """Create a mock QueryEngine with a mock graph client."""
    engine = MagicMock(spec=QueryEngine)
    engine.repo_path = repo_path
    engine.graph = MagicMock()
    return engine


# ------------------------------------------------------------------
# Fixtures — patch the module-level dicts in server.py
# ------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clean_server_state():
    """Reset server state before each test."""
    import gristle.mcp.server as srv

    orig_engines = srv._engines.copy()
    orig_pipelines = srv._pipelines.copy()
    orig_semantic = srv._semantic_indexes.copy()
    srv._engines.clear()
    srv._pipelines.clear()
    srv._semantic_indexes.clear()
    yield
    srv._engines.clear()
    srv._pipelines.clear()
    srv._semantic_indexes.clear()
    srv._engines.update(orig_engines)
    srv._pipelines.update(orig_pipelines)
    srv._semantic_indexes.update(orig_semantic)


# ==================================================================
# _resolve_engine
# ==================================================================


class TestResolveEngine:
    def test_returns_none_when_no_repos(self):
        from gristle.mcp.server import _resolve_engine

        assert _resolve_engine(None) is None

    def test_returns_engine_by_id(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import _resolve_engine

        engine = _mock_engine()
        srv._engines["myrepo"] = engine
        assert _resolve_engine("myrepo") is engine

    def test_returns_none_for_unknown_id(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import _resolve_engine

        srv._engines["other"] = _mock_engine()
        assert _resolve_engine("unknown") is None

    def test_defaults_to_last_ingested(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import _resolve_engine

        e1 = _mock_engine()
        e2 = _mock_engine()
        srv._engines["first"] = e1
        srv._engines["second"] = e2
        assert _resolve_engine(None) is e2


# ==================================================================
# gristle_ingest
# ==================================================================


class TestGristleIngest:
    @pytest.mark.asyncio
    async def test_returns_error_for_missing_dir(self):
        from gristle.mcp.server import gristle_ingest

        result = await gristle_ingest(repo_path="/nonexistent/path/xyzzy")
        assert "error" in result
        assert "not found" in result["error"].lower() or "Directory" in result["error"]

    @pytest.mark.asyncio
    @patch("gristle.mcp.server.IngestionPipeline")
    @patch("gristle.mcp.server.GraphClient")
    async def test_successful_ingestion(self, MockGraphClient, MockPipeline):
        from gristle.mcp.server import _engines, _pipelines, gristle_ingest

        with tempfile.TemporaryDirectory() as tmpdir:
            mock_result = _make_ingestion_result(repo_path=tmpdir)
            MockPipeline.return_value.ingest_repo.return_value = mock_result
            MockGraphClient.repo_id_from_path.return_value = "abc123"

            result = await gristle_ingest(repo_path=tmpdir)

            assert result["status"] == "success"
            assert result["files_processed"] == 10
            assert result["nodes_created"] == 100
            assert result["relationships_created"] == 80
            assert result["routes_found"] == 4
            assert result["duration_ms"] >= 0
            assert "abc123" in _engines
            assert "abc123" in _pipelines

    @pytest.mark.asyncio
    @patch("gristle.mcp.server.IngestionPipeline")
    @patch("gristle.mcp.server.GraphClient")
    async def test_custom_repo_id(self, MockGraphClient, MockPipeline):
        from gristle.mcp.server import _engines, gristle_ingest

        with tempfile.TemporaryDirectory() as tmpdir:
            MockPipeline.return_value.ingest_repo.return_value = _make_ingestion_result(
                repo_id="custom", repo_path=tmpdir
            )
            result = await gristle_ingest(repo_path=tmpdir, repo_id="custom")
            assert result["repo_id"] == "custom"
            assert "custom" in _engines

    @pytest.mark.asyncio
    @patch("gristle.mcp.server.IngestionPipeline")
    @patch("gristle.mcp.server.GraphClient")
    async def test_errors_truncated_to_10(self, MockGraphClient, MockPipeline):
        from gristle.mcp.server import gristle_ingest

        with tempfile.TemporaryDirectory() as tmpdir:
            errors = [f"error {i}" for i in range(20)]
            MockPipeline.return_value.ingest_repo.return_value = _make_ingestion_result(repo_path=tmpdir, errors=errors)
            MockGraphClient.repo_id_from_path.return_value = "abc123"
            result = await gristle_ingest(repo_path=tmpdir)
            assert len(result["errors"]) == 10


# ==================================================================
# gristle_explore
# ==================================================================


class TestGristleExplore:
    @pytest.mark.asyncio
    async def test_no_repo_ingested(self):
        from gristle.mcp.server import gristle_explore

        result = await gristle_explore(entity="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_finds_function(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_explore

        engine = _mock_engine()
        engine.get_function_context.return_value = {
            "name": "foo",
            "qualified_name": "mod.foo",
            "signature": "def foo()",
        }
        engine.get_docs_for_entity.return_value = []
        srv._engines["r1"] = engine

        result = await gristle_explore(entity="foo")
        assert result["type"] == "function"
        assert result["name"] == "foo"

    @pytest.mark.asyncio
    async def test_finds_class(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_explore

        engine = _mock_engine()
        engine.get_function_context.return_value = None
        engine.get_class_structure.return_value = {
            "name": "MyClass",
            "qualified_name": "mod.MyClass",
        }
        engine.get_docs_for_entity.return_value = []
        srv._engines["r1"] = engine

        result = await gristle_explore(entity="MyClass")
        assert result["type"] == "class"

    @pytest.mark.asyncio
    async def test_finds_file(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_explore

        engine = _mock_engine()
        engine.get_function_context.return_value = None
        engine.get_class_structure.return_value = None
        engine.get_file_overview.return_value = {"path": "mod.py", "language": "python"}
        srv._engines["r1"] = engine

        result = await gristle_explore(entity="mod.py")
        assert result["type"] == "file"

    @pytest.mark.asyncio
    async def test_falls_back_to_search(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_explore

        engine = _mock_engine()
        engine.get_function_context.return_value = None
        engine.get_class_structure.return_value = None
        engine.get_file_overview.return_value = None
        engine.search.return_value = [{"name": "foobar", "type": "Function"}]
        srv._engines["r1"] = engine

        result = await gristle_explore(entity="foobar")
        assert result["type"] == "search_results"
        assert len(result["results"]) == 1

    @pytest.mark.asyncio
    async def test_nothing_found(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_explore

        engine = _mock_engine()
        engine.get_function_context.return_value = None
        engine.get_class_structure.return_value = None
        engine.get_file_overview.return_value = None
        engine.search.return_value = []
        srv._engines["r1"] = engine

        result = await gristle_explore(entity="xyzzy")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_includes_docs_for_function(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_explore

        engine = _mock_engine()
        engine.get_function_context.return_value = {"name": "foo"}
        engine.get_docs_for_entity.return_value = [{"doc_path": "README.md"}]
        srv._engines["r1"] = engine

        result = await gristle_explore(entity="foo")
        assert "referenced_in_docs" in result
        assert result["referenced_in_docs"][0]["doc_path"] == "README.md"


# ==================================================================
# gristle_impact
# ==================================================================


class TestGristleImpact:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_impact

        result = await gristle_impact(entity_name="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_entity_not_found(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_impact

        engine = _mock_engine()
        engine.impact_analysis.return_value = None
        srv._engines["r1"] = engine
        result = await gristle_impact(entity_name="missing")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_impact(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_impact

        engine = _mock_engine()
        engine.impact_analysis.return_value = {
            "target": "mod.foo",
            "direct_callers": ["bar"],
        }
        srv._engines["r1"] = engine
        result = await gristle_impact(entity_name="foo")
        assert result["target"] == "mod.foo"


# ==================================================================
# gristle_impact_score
# ==================================================================


class TestGristleImpactScore:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_impact_score

        result = await gristle_impact_score(entity_name="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_entity_not_found(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_impact_score

        engine = _mock_engine()
        engine.get_impact_analysis.return_value = None
        srv._engines["r1"] = engine
        result = await gristle_impact_score(entity_name="missing")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_scored_impact(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_impact_score

        engine = _mock_engine()
        engine.get_impact_analysis.return_value = {
            "target": "mod.foo",
            "direct_callers": ["bar"],
            "blast_radius_score": 65.5,
            "risk_level": "high",
            "direct_impact_score": 50.0,
            "transitive_impact_score": 40.0,
        }
        srv._engines["r1"] = engine
        result = await gristle_impact_score(entity_name="foo")
        assert result["target"] == "mod.foo"
        assert result["blast_radius_score"] == 65.5
        assert result["risk_level"] == "high"
        engine.get_impact_analysis.assert_called_once_with("foo", include_source=False)

    @pytest.mark.asyncio
    async def test_include_source(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_impact_score

        engine = _mock_engine()
        engine.get_impact_analysis.return_value = {
            "target": "mod.foo",
            "blast_radius_score": 30.0,
            "risk_level": "medium",
            "source": "def foo(): pass",
        }
        srv._engines["r1"] = engine
        result = await gristle_impact_score(entity_name="foo", include_source=True)
        assert "source" in result
        engine.get_impact_analysis.assert_called_once_with("foo", include_source=True)


# ==================================================================
# gristle_trace
# ==================================================================


class TestGristleTrace:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_trace

        result = await gristle_trace(from_entity="a", to_entity="b")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_no_path_found(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_trace

        engine = _mock_engine()
        engine.find_path.return_value = []
        srv._engines["r1"] = engine
        result = await gristle_trace(from_entity="a", to_entity="b")
        assert "note" in result

    @pytest.mark.asyncio
    async def test_returns_paths(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_trace

        engine = _mock_engine()
        engine.find_path.return_value = [{"path": ["a", "c", "b"], "hops": 2}]
        srv._engines["r1"] = engine
        result = await gristle_trace(from_entity="a", to_entity="b")
        assert result["from"] == "a"
        assert len(result["paths"]) == 1


# ==================================================================
# gristle_search
# ==================================================================


class TestGristleSearch:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_search

        result = await gristle_search(query="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_results(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_search

        engine = _mock_engine()
        engine.search.return_value = [{"name": "foo", "type": "Function"}]
        srv._engines["r1"] = engine
        result = await gristle_search(query="foo")
        assert result["count"] == 1
        assert result["query"] == "foo"

    @pytest.mark.asyncio
    async def test_passes_search_type_and_limit(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_search

        engine = _mock_engine()
        engine.search.return_value = []
        srv._engines["r1"] = engine
        await gristle_search(query="foo", search_type="name", limit=5)
        engine.search.assert_called_once_with("foo", search_type="name", limit=5)


# ==================================================================
# gristle_docs
# ==================================================================


class TestGristleDocs:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_docs

        result = await gristle_docs(entity="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_overview_mode(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_docs

        engine = _mock_engine()
        engine.get_doc_overview.return_value = {"doc_types": {"readme": 1}}
        srv._engines["r1"] = engine
        result = await gristle_docs(mode="overview")
        assert "doc_types" in result

    @pytest.mark.asyncio
    async def test_staleness_mode(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_docs

        engine = _mock_engine()
        engine.get_doc_staleness.return_value = [{"doc_path": "README.md"}]
        srv._engines["r1"] = engine
        result = await gristle_docs(mode="staleness")
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_find_mode_requires_entity(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_docs

        srv._engines["r1"] = _mock_engine()
        result = await gristle_docs(mode="find", entity=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_find_mode_returns_docs(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_docs

        engine = _mock_engine()
        engine.get_docs_for_entity.return_value = [{"doc_path": "README.md"}]
        srv._engines["r1"] = engine
        result = await gristle_docs(entity="foo", mode="find")
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_find_mode_no_docs(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_docs

        engine = _mock_engine()
        engine.get_docs_for_entity.return_value = []
        srv._engines["r1"] = engine
        result = await gristle_docs(entity="foo", mode="find")
        assert "note" in result


# ==================================================================
# gristle_routes
# ==================================================================


class TestGristleRoutes:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_routes

        result = await gristle_routes()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_routes(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_routes

        engine = _mock_engine()
        engine.get_routes.return_value = [{"method": "GET", "path": "/users"}]
        srv._engines["r1"] = engine
        result = await gristle_routes()
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_filter_by_method(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_routes

        engine = _mock_engine()
        engine.get_routes.return_value = []
        srv._engines["r1"] = engine
        await gristle_routes(method="POST")
        engine.get_routes.assert_called_once_with("POST")


# ==================================================================
# gristle_components
# ==================================================================


class TestGristleComponents:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_components

        result = await gristle_components()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_components(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_components

        engine = _mock_engine()
        engine.get_components.return_value = [{"name": "Button", "usage_count": 5}]
        srv._engines["r1"] = engine
        result = await gristle_components(limit=10)
        assert result["count"] == 1
        engine.get_components.assert_called_once_with(10, exclude_docs=True)


# ==================================================================
# gristle_deps
# ==================================================================


class TestGristleDeps:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_deps

        result = await gristle_deps()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_list_all_deps(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_deps

        engine = _mock_engine()
        engine.get_dependencies.return_value = [{"name": "requests", "function_count": 5}]
        srv._engines["r1"] = engine
        result = await gristle_deps()
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_drill_into_dep(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_deps

        engine = _mock_engine()
        engine.get_dependency_users.return_value = {
            "dependency": "requests",
            "files": ["api.py"],
            "functions": [{"name": "fetch"}],
            "file_count": 1,
            "function_count": 1,
        }
        srv._engines["r1"] = engine
        result = await gristle_deps(name="requests")
        assert result["dependency"] == "requests"

    @pytest.mark.asyncio
    async def test_dep_not_found(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_deps

        engine = _mock_engine()
        engine.get_dependency_users.return_value = {
            "dependency": "nope",
            "files": [],
            "functions": [],
            "file_count": 0,
            "function_count": 0,
        }
        srv._engines["r1"] = engine
        result = await gristle_deps(name="nope")
        assert "note" in result


# ==================================================================
# gristle_tests
# ==================================================================


class TestGristleTests:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_tests

        result = await gristle_tests()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_find_mode_requires_entity(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_tests

        srv._engines["r1"] = _mock_engine()
        result = await gristle_tests(mode="find", entity=None)
        assert "error" in result

    @pytest.mark.asyncio
    async def test_find_mode_returns_tests(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_tests

        engine = _mock_engine()
        engine.get_tests_for_entity.return_value = [
            {"test_name": "test_foo", "test_file": "test_mod.py"},
        ]
        srv._engines["r1"] = engine
        result = await gristle_tests(entity="foo", mode="find")
        assert result["count"] == 1

    @pytest.mark.asyncio
    async def test_find_mode_no_tests(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_tests

        engine = _mock_engine()
        engine.get_tests_for_entity.return_value = []
        srv._engines["r1"] = engine
        result = await gristle_tests(entity="foo", mode="find")
        assert "note" in result

    @pytest.mark.asyncio
    async def test_coverage_mode(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_tests

        engine = _mock_engine()
        engine.get_untested_functions.return_value = [{"name": "orphan"}]
        srv._engines["r1"] = engine
        result = await gristle_tests(mode="coverage")
        assert result["count"] == 1


# ==================================================================
# gristle_conventions
# ==================================================================


class TestGristleConventions:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_conventions

        result = await gristle_conventions()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_conventions(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_conventions

        engine = _mock_engine()
        engine.infer_conventions.return_value = {"languages": {"python": 10}}
        engine.get_repo_overview.return_value = {"nodes": {"Function": 50}}
        engine.get_todos.return_value = [{"file_path": "mod.py", "todo_count": 3}]
        srv._engines["r1"] = engine
        result = await gristle_conventions()
        assert "project_overview" in result
        assert "conventions" in result
        assert "top_todo_files" in result


# ==================================================================
# gristle_watch
# ==================================================================


class TestGristleWatch:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_watch

        result = await gristle_watch()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_status(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_watch

        srv._engines["r1"] = _mock_engine()
        with patch("gristle.ingestion.watcher.is_watching", return_value=False):
            result = await gristle_watch(action="status", repo_id="r1")
        assert result["repo_id"] == "r1"
        assert result["watching"] is False

    @pytest.mark.asyncio
    async def test_start_no_pipeline(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_watch

        srv._engines["r1"] = _mock_engine()
        # No pipeline registered
        result = await gristle_watch(action="start", repo_id="r1")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_start_with_pipeline(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_watch

        srv._engines["r1"] = _mock_engine()
        srv._pipelines["r1"] = MagicMock()
        with patch("gristle.ingestion.watcher.start_watching", return_value=True):
            result = await gristle_watch(action="start", repo_id="r1")
        assert result["watching"] is True
        assert result["started"] is True

    @pytest.mark.asyncio
    async def test_stop(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_watch

        srv._engines["r1"] = _mock_engine()
        with patch("gristle.ingestion.watcher.stop_watching", return_value=True):
            result = await gristle_watch(action="stop", repo_id="r1")
        assert result["watching"] is False
        assert result["stopped"] is True

    @pytest.mark.asyncio
    async def test_unknown_action(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_watch

        srv._engines["r1"] = _mock_engine()
        result = await gristle_watch(action="invalid", repo_id="r1")
        assert "error" in result
        assert "Unknown action" in result["error"]


# ==================================================================
# gristle_drop
# ==================================================================


class TestGristleDrop:
    @pytest.mark.asyncio
    @patch("gristle.mcp.server.GraphClient")
    async def test_drop_loaded_repo(self, MockGraphClient):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_drop

        engine = _mock_engine()
        srv._engines["r1"] = engine
        srv._pipelines["r1"] = MagicMock()
        result = await gristle_drop(repo_id="r1")
        assert result["status"] == "dropped"
        assert result["was_loaded"] is True
        engine.graph.drop.assert_called_once()
        assert "r1" not in srv._engines

    @pytest.mark.asyncio
    @patch("gristle.mcp.server.GraphClient")
    async def test_drop_unloaded_repo(self, MockGraphClient):
        from gristle.mcp.server import gristle_drop

        mock_graph = MagicMock()
        MockGraphClient.return_value = mock_graph
        result = await gristle_drop(repo_id="unknown")
        assert result["status"] == "dropped"
        assert result["was_loaded"] is False
        mock_graph.drop.assert_called_once()


# ==================================================================
# gristle_embed
# ==================================================================


class TestGristleEmbed:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_embed

        result = await gristle_embed()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_import_error(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_embed

        srv._engines["r1"] = _mock_engine()
        with (
            patch.dict("sys.modules", {"gristle.search.embeddings": None}),
            patch("builtins.__import__", side_effect=ImportError("no module")),
        ):
            result = await gristle_embed(repo_id="r1")
        assert "error" in result
        assert "sentence-transformers" in result["error"]


# ==================================================================
# gristle_semantic_search
# ==================================================================


class TestGristleSemanticSearch:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_semantic_search

        result = await gristle_semantic_search(query="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_with_existing_index(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_semantic_search

        srv._engines["r1"] = _mock_engine()
        mock_index = MagicMock()
        mock_index.search.return_value = [
            {
                "name": "validate_email",
                "label": "Function",
                "signature": "def validate_email()",
                "docstring": "Validates email",
                "file_path": "utils.py",
                "score": 0.2,
            },
        ]
        srv._semantic_indexes["r1"] = mock_index
        result = await gristle_semantic_search(query="validates emails")
        assert result["count"] == 1
        assert result["results"][0]["similarity"] == 0.8

    @pytest.mark.asyncio
    async def test_no_results(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_semantic_search

        srv._engines["r1"] = _mock_engine()
        mock_index = MagicMock()
        mock_index.search.return_value = []
        srv._semantic_indexes["r1"] = mock_index
        result = await gristle_semantic_search(query="xyzzy")
        assert "note" in result


# ==================================================================
# Resources
# ==================================================================


class TestResources:
    @pytest.mark.asyncio
    async def test_list_repos_empty(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import list_repos

        srv._engines.clear()
        with patch("gristle.mcp.server.GraphClient") as MockGC:
            MockGC.return_value.list_gristle_graphs.return_value = []
            result = json.loads(await list_repos())
        assert result == []

    @pytest.mark.asyncio
    async def test_list_repos_with_engines(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import list_repos

        srv._engines.clear()
        srv._engines["r1"] = _mock_engine("/tmp/repo1")
        srv._engines["r2"] = _mock_engine("/tmp/repo2")
        with patch("gristle.mcp.server.GraphClient") as MockGC:
            MockGC.return_value.list_gristle_graphs.return_value = []
            result = json.loads(await list_repos())
        assert len(result) == 2
        assert result[0]["repo_id"] == "r1"
        assert result[0]["loaded"] is True
        assert result[1]["repo_path"] == "/tmp/repo2"
        srv._engines.clear()

    @pytest.mark.asyncio
    async def test_repo_overview_not_found(self):
        from gristle.mcp.server import repo_overview

        result = json.loads(await repo_overview(repo_id="unknown"))
        assert "error" in result

    @pytest.mark.asyncio
    async def test_repo_overview_success(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import repo_overview

        engine = _mock_engine()
        engine.get_repo_overview.return_value = {"nodes": {"Function": 50}}
        srv._engines["r1"] = engine
        result = json.loads(await repo_overview(repo_id="r1"))
        assert result["nodes"]["Function"] == 50


# ==================================================================
# Health check
# ==================================================================


class TestHealthCheck:
    @pytest.mark.asyncio
    async def test_health_check(self):
        from gristle.mcp.server import health_check

        # health_check is an ASGI route, call it directly with a mock request
        request = MagicMock()
        response = await health_check(request)
        body = json.loads(response.body)
        from gristle import __version__

        assert body["status"] == "ok"
        assert body["server"] == "gristle"
        assert body["version"] == __version__  # track the package version, not a literal
        assert "repos_loaded" in body


# ==================================================================
# main() entry point
# ==================================================================


class TestMain:
    @patch("gristle.mcp.server.mcp")
    @patch("gristle.mcp.server.settings")
    def test_main_stdio(self, mock_settings, mock_mcp):
        from gristle.mcp.server import main

        mock_settings.transport = "stdio"
        with patch("gristle.logging.configure_logging"):
            main()
        mock_mcp.run.assert_called_once_with(transport="stdio")

    @patch("gristle.mcp.server.mcp")
    @patch("gristle.mcp.server.settings")
    def test_main_http(self, mock_settings, mock_mcp):
        from gristle.mcp.server import main

        mock_settings.transport = "streamable-http"
        with patch("gristle.logging.configure_logging"):
            main()
        mock_mcp.run.assert_called_once_with(transport="streamable-http")

    @patch("gristle.mcp.server.settings")
    def test_main_invalid_transport(self, mock_settings):
        from gristle.mcp.server import main

        mock_settings.transport = "invalid"
        with pytest.raises(SystemExit, match="Unknown transport"):
            main()


# ==================================================================
# gristle_services
# ==================================================================


class TestGristleServices:
    @pytest.mark.asyncio
    async def test_returns_service_categories(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_services

        engine = _mock_engine()
        engine.get_external_services.return_value = {
            "categories": {"database": {"label": "Database & ORM", "packages": []}},
            "uncategorized": [],
        }
        srv._engines["r1"] = engine
        result = await gristle_services()
        assert "categories" in result
        engine.get_external_services.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_repo_returns_error(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_services

        srv._engines.clear()
        result = await gristle_services()
        assert "error" in result


# ==================================================================
# gristle_changelog
# ==================================================================


class TestGristleChangelog:
    @pytest.mark.asyncio
    async def test_returns_changelog(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_changelog

        engine = _mock_engine()
        engine.get_changelog.return_value = {
            "status": "diff",
            "changes": {},
            "summary": "Added 3 files.",
        }
        srv._engines["r1"] = engine
        result = await gristle_changelog()
        assert result["status"] == "diff"
        engine.get_changelog.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_repo_returns_error(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_changelog

        srv._engines.clear()
        result = await gristle_changelog()
        assert "error" in result


class TestToolErrorBoundary:
    @pytest.mark.asyncio
    async def test_catches_and_returns_error_dict(self):
        from gristle.mcp.server import _tool_error_boundary

        @_tool_error_boundary
        async def boom():
            raise ValueError("kaboom")

        assert await boom() == {"error": "kaboom", "tool": "boom"}

    @pytest.mark.asyncio
    async def test_connection_error_is_actionable(self):
        from redis.exceptions import ConnectionError as RedisConnectionError

        from gristle.mcp.server import _tool_error_boundary

        @_tool_error_boundary
        async def boom():
            raise RedisConnectionError("refused")

        result = await boom()
        assert "FalkorDB" in result["error"]
        assert "docker compose up -d falkordb" in result["error"]

    @pytest.mark.asyncio
    async def test_passes_through_success(self):
        from gristle.mcp.server import _tool_error_boundary

        @_tool_error_boundary
        async def ok():
            return {"status": "success"}

        assert await ok() == {"status": "success"}

    def test_preserves_signature_and_name(self):
        """The boundary must preserve the wrapped signature so FastMCP can still
        build the tool's JSON schema from it."""
        import inspect

        from gristle.mcp.server import _tool_error_boundary

        @_tool_error_boundary
        async def sample(a: int, b: str = "x") -> dict:
            return {}

        assert sample.__name__ == "sample"
        assert list(inspect.signature(sample).parameters) == ["a", "b"]


class TestRehydration:
    def test_rehydrate_returns_none_when_graph_absent(self):
        import gristle.mcp.server as srv

        srv._engines.clear()
        with patch("gristle.mcp.server.GraphClient") as MockGC:
            MockGC.return_value.graph_exists.return_value = False
            assert srv._rehydrate_engine("ghost") is None
        assert "ghost" not in srv._engines

    def test_rehydrate_builds_engine_from_existing_graph(self):
        import gristle.mcp.server as srv

        srv._engines.clear()
        with patch("gristle.mcp.server.GraphClient") as MockGC:
            gc = MockGC.return_value
            gc.graph_exists.return_value = True
            gc.execute.return_value = QueryResult(records=[{"p": "/repo/path"}], summary={})
            engine = srv._rehydrate_engine("myrepo")
        assert engine is not None
        assert engine.repo_path == "/repo/path"
        assert srv._engines["myrepo"] is engine
        srv._engines.clear()

    def test_resolve_engine_falls_back_to_rehydrate(self):
        import gristle.mcp.server as srv

        srv._engines.clear()
        sentinel = object()
        with patch("gristle.mcp.server._rehydrate_engine", return_value=sentinel) as reh:
            assert srv._resolve_engine("absent") is sentinel
            reh.assert_called_once_with("absent")


class TestMCPChangeImpact:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_change_impact

        result = await gristle_change_impact(entity_name="foo")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delegates_to_engine(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_change_impact

        engine = _mock_engine()
        engine.get_change_impact.return_value = {"entity": "foo", "risk_level": "low", "tests_to_run": []}
        srv._engines["r1"] = engine

        result = await gristle_change_impact(entity_name="foo")
        assert result["risk_level"] == "low"
        engine.get_change_impact.assert_called_once_with("foo")

    @pytest.mark.asyncio
    async def test_entity_not_found(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_change_impact

        engine = _mock_engine()
        engine.get_change_impact.return_value = None
        srv._engines["r1"] = engine

        result = await gristle_change_impact(entity_name="ghost")
        assert "error" in result
