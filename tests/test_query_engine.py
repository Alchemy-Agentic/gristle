"""Tests for the query engine."""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock

from gristle.graph.client import QueryResult
from gristle.query.engine import QueryEngine

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _qr(records: list[dict[str, Any]]) -> QueryResult:
    """Shortcut to build a QueryResult with no summary."""
    return QueryResult(records=records, summary={})


def _empty() -> QueryResult:
    return _qr([])


def _make_engine(execute_side_effect=None, repo_path=None) -> tuple[QueryEngine, MagicMock]:
    """Create a QueryEngine with a mock graph client."""
    graph = MagicMock()
    if execute_side_effect is not None:
        graph.execute.side_effect = execute_side_effect
    engine = QueryEngine(graph, repo_path=repo_path)
    return engine, graph


# ==================================================================
# 1. get_function_context
# ==================================================================


class TestGetFunctionContext:
    def test_returns_none_when_not_found(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_function_context("missing_func") is None

    def test_returns_function_record(self):
        rec = {
            "qualified_name": "mod.foo",
            "name": "foo",
            "signature": "def foo(x)",
            "docstring": "Does stuff",
            "start_line": 10,
            "end_line": 20,
            "is_async": False,
            "complexity": 3,
            "decorators": None,
            "visibility": "public",
            "return_type": "int",
            "file_path": "mod.py",
            "class_name": None,
            "callers": ["bar"],
            "callees": ["baz"],
        }
        engine, graph = _make_engine()
        graph.execute.return_value = _qr([rec])
        result = engine.get_function_context("foo", include_source=False)
        assert result["qualified_name"] == "mod.foo"
        assert result["callers"] == ["bar"]
        assert result["callees"] == ["baz"]

    def test_includes_source_when_repo_path_set(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            # Write a fake source file
            src = os.path.join(tmpdir, "mod.py")
            with open(src, "w") as f:
                f.write("\n".join(f"line {i}" for i in range(1, 25)))

            rec = {
                "qualified_name": "mod.foo",
                "name": "foo",
                "signature": "def foo()",
                "docstring": None,
                "start_line": 3,
                "end_line": 5,
                "is_async": False,
                "complexity": 1,
                "decorators": None,
                "visibility": "public",
                "return_type": None,
                "file_path": "mod.py",
                "class_name": None,
                "callers": [],
                "callees": [],
            }
            engine, graph = _make_engine(repo_path=tmpdir)
            graph.execute.return_value = _qr([rec])
            result = engine.get_function_context("foo", include_source=True)
            assert result["source_code"] is not None
            assert "line 3" in result["source_code"]

    def test_no_source_when_include_source_false(self):
        engine, graph = _make_engine(repo_path="/some/path")
        rec = {
            "qualified_name": "f",
            "name": "f",
            "signature": "def f()",
            "docstring": None,
            "start_line": 1,
            "end_line": 2,
            "is_async": False,
            "complexity": 1,
            "decorators": None,
            "visibility": "public",
            "return_type": None,
            "file_path": "x.py",
            "class_name": None,
            "callers": [],
            "callees": [],
        }
        graph.execute.return_value = _qr([rec])
        result = engine.get_function_context("f", include_source=False)
        assert "source_code" not in result


# ==================================================================
# 2. get_class_structure
# ==================================================================


class TestGetClassStructure:
    def test_returns_none_when_not_found(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_class_structure("Missing") is None

    def test_returns_class_with_methods_and_hierarchy(self):
        class_rec = {
            "qualified_name": "mod.MyClass",
            "name": "MyClass",
            "signature": "class MyClass(Base)",
            "docstring": "A class",
            "start_line": 1,
            "end_line": 50,
            "bases": ["Base"],
            "is_abstract": False,
            "decorators": None,
            "file_path": "mod.py",
            "methods": [
                {
                    "name": "do_thing",
                    "signature": "def do_thing()",
                    "visibility": "public",
                    "is_async": False,
                    "docstring": None,
                }
            ],
        }
        hierarchy_rec = {"chain": ["MyClass", "Base", "object"]}

        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr([class_rec]), _qr([hierarchy_rec])]
        result = engine.get_class_structure("MyClass")
        assert result["name"] == "MyClass"
        assert result["hierarchy"] == ["MyClass", "Base", "object"]
        assert len(result["methods"]) == 1

    def test_hierarchy_defaults_to_self_when_no_ancestors(self):
        class_rec = {
            "qualified_name": "mod.Standalone",
            "name": "Standalone",
            "signature": "class Standalone",
            "docstring": None,
            "start_line": 1,
            "end_line": 10,
            "bases": [],
            "is_abstract": False,
            "decorators": None,
            "file_path": "mod.py",
            "methods": [],
        }
        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr([class_rec]), _empty()]
        result = engine.get_class_structure("Standalone")
        assert result["hierarchy"] == ["Standalone"]


# ==================================================================
# 3. get_file_overview
# ==================================================================


class TestGetFileOverview:
    def test_returns_none_when_not_found(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_file_overview("nonexistent.py") is None

    def test_returns_overview_with_routes_and_tests(self):
        file_rec = {
            "path": "api.py",
            "language": "python",
            "line_count": 100,
            "docstring": None,
            "is_test_file": False,
            "classes": [],
            "functions": [{"name": "handler", "signature": "def handler()", "start_line": 5}],
            "imports": [{"module": "fastapi", "names": ["FastAPI"]}],
        }
        route_recs = [{"method": "GET", "path": "/users", "handler": "handler", "line": 5}]
        test_recs = [{"test_file": "test_api.py"}]

        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([file_rec]),  # main file query
            _qr(route_recs),  # routes query
            _qr(test_recs),  # test coverage query
        ]
        result = engine.get_file_overview("api.py")
        assert result["path"] == "api.py"
        assert result["routes"] == route_recs
        assert result["tested_by"] == ["test_api.py"]

    def test_test_file_shows_targets(self):
        file_rec = {
            "path": "test_api.py",
            "language": "python",
            "line_count": 50,
            "docstring": None,
            "is_test_file": True,
            "classes": [],
            "functions": [],
            "imports": [],
        }
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([file_rec]),  # main file query
            _empty(),  # routes
            _empty(),  # test coverage
            _qr([{"production_file": "api.py"}]),  # tests_targets
        ]
        result = engine.get_file_overview("test_api.py")
        assert result["tests_files"] == ["api.py"]

    def test_no_routes_or_tests(self):
        file_rec = {
            "path": "utils.py",
            "language": "python",
            "line_count": 20,
            "docstring": None,
            "is_test_file": False,
            "classes": [],
            "functions": [],
            "imports": [],
        }
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([file_rec]),
            _empty(),  # routes
            _empty(),  # test coverage
        ]
        result = engine.get_file_overview("utils.py")
        assert "routes" not in result
        assert "tested_by" not in result


# ==================================================================
# 4. get_callers
# ==================================================================


class TestGetCallers:
    def test_returns_callers(self):
        recs = [
            {"caller": "mod.bar", "file_path": "mod.py", "line": 10, "depth": 1},
            {"caller": "mod.baz", "file_path": "mod.py", "line": 20, "depth": 2},
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_callers("foo")
        assert len(result) == 2
        assert result[0]["caller"] == "mod.bar"

    def test_empty_callers(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_callers("isolated") == []


# ==================================================================
# 5. get_callees
# ==================================================================


class TestGetCallees:
    def test_returns_callees(self):
        recs = [
            {"callee": "mod.helper", "file_path": "mod.py", "line": 30, "depth": 1},
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_callees("foo")
        assert len(result) == 1
        assert result[0]["callee"] == "mod.helper"

    def test_empty_callees(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_callees("leaf_func") == []


# ==================================================================
# 6. impact_analysis
# ==================================================================


class TestImpactAnalysis:
    def test_returns_none_when_not_found(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.impact_analysis("missing") is None

    def test_full_impact_analysis(self):
        impact_rec = {
            "target": "mod.create_user",
            "target_type": "Function",
            "target_file": "mod.py",
            "direct_callers": ["mod.api_handler"],
            "affected_files": ["mod.py"],
        }
        transitive_callers = [
            {"caller": "mod.api_handler", "file_path": "mod.py", "line": 5, "depth": 1},
            {"caller": "mod.router", "file_path": "router.py", "line": 10, "depth": 2},
        ]
        test_files = [{"test_file": "test_mod.py"}]
        test_funcs_direct = [
            {
                "test_name": "test_create",
                "test_qualified_name": "test_mod.test_create",
                "test_file": "test_mod.py",
                "line": 10,
                "via": "calls",
            },
        ]
        test_funcs_file = []
        routes = []

        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),  # main impact query
            _qr(transitive_callers),  # get_callers (inside impact)
            _qr(test_files),  # test file coverage
            _empty(),  # get_tests_for_entity -> TESTS_FUNCTION
            _qr(test_funcs_direct),  # get_tests_for_entity -> CALLS
            _qr(test_funcs_file),  # get_tests_for_entity -> file_level
            _qr(routes),  # routes query
        ]
        result = engine.impact_analysis("create_user")
        assert result["target"] == "mod.create_user"
        assert "mod.router" in result["transitive_callers"]
        assert "test_mod.py" in result["test_files"]

    def test_impact_no_target_file(self):
        """When target_file is None, test_files query is skipped."""
        impact_rec = {
            "target": "orphan",
            "target_type": "Function",
            "target_file": None,
            "direct_callers": [],
            "affected_files": [],
        }
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),  # main impact query
            _empty(),  # get_callers
            _empty(),  # get_tests_for_entity -> TESTS_FUNCTION
            _empty(),  # get_tests_for_entity -> CALLS
            _empty(),  # get_tests_for_entity -> file_level
            _empty(),  # routes
        ]
        result = engine.impact_analysis("orphan")
        assert result is not None
        assert "test_files" not in result


# ==================================================================
# 6b. Impact Analysis with Scoring
# ==================================================================


class TestImpactScoring:
    def test_low_impact_function(self):
        """Function with no callers should have low impact score."""
        # Base impact_analysis mock
        impact_rec = {
            "target": "mod.helper",
            "target_type": "Function",
            "target_file": "mod.py",
            "direct_callers": [],
            "affected_files": [],
        }
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),  # impact_analysis base
            _empty(),  # get_callers
            _empty(),  # test files
            _empty(),  # get_tests_for_entity
            _empty(),
            _empty(),
            _empty(),  # routes
            _qr([{"is_entry_point": False, "is_exported": False}]),  # entity check
            _qr([{"count": 0}]),  # PASSED_TO count
        ]
        result = engine.get_impact_analysis("mod.helper")
        assert result is not None
        assert result["direct_callers_count"] == 0
        assert result["passed_to_count"] == 0
        assert result["risk_level"] == "low"
        assert result["blast_radius_score"] < 30

    def test_high_impact_route_handler(self):
        """Route handler with many callers should have high impact."""
        impact_rec = {
            "target": "api.get_users",
            "target_type": "Function",
            "target_file": "api.py",
            "direct_callers": ["api.auth", "api.validate", "api.log"],
            "affected_files": ["api.py", "auth.py"],
            "routes": [{"method": "GET", "path": "/users", "file_path": "api.py", "line": 10}],
        }
        transitive_callers = [
            {"caller": f"api.caller{i}", "file_path": f"mod{i}.py", "depth": i % 2 + 1} for i in range(10)
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),  # base impact
            _qr(transitive_callers),  # get_callers
            _empty(),  # test files
            _empty(),  # get_tests_for_entity
            _empty(),
            _empty(),
            _empty(),  # routes
            _qr([{"is_entry_point": True, "is_exported": True}]),  # entity check
            _qr([{"count": 2}]),  # PASSED_TO count (callbacks)
        ]
        result = engine.get_impact_analysis("api.get_users")
        assert result is not None
        assert result["direct_callers_count"] == 3
        assert result["passed_to_count"] == 2
        assert result["has_route"] is True
        assert result["is_entry_point"] is True
        assert result["risk_level"] in ("high", "critical")
        assert result["blast_radius_score"] >= 60

    def test_critical_impact_no_tests(self):
        """High usage with no tests = high/critical risk."""
        impact_rec = {
            "target": "core.process",
            "target_type": "Function",
            "target_file": "core.py",
            "direct_callers": [f"mod.caller{i}" for i in range(8)],  # Many direct callers
            "affected_files": [f"mod{i}.py" for i in range(15)],  # Many affected files
        }
        transitive_callers = [
            {"caller": f"transitive{i}", "file_path": f"file{i}.py", "depth": 2}
            for i in range(25)  # Many transitive callers
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),
            _qr(transitive_callers),
            _empty(),  # NO test files
            _empty(),  # get_tests_for_entity
            _empty(),
            _empty(),
            _empty(),  # routes
            _qr([{"is_entry_point": False, "is_exported": True}]),
            _qr([{"count": 0}]),
        ]
        result = engine.get_impact_analysis("core.process")
        assert result is not None
        assert result["has_tests"] is False
        assert result["risk_level"] in ("high", "critical")
        assert result["blast_radius_score"] >= 60

    def test_medium_impact_with_tests(self):
        """Moderate usage with test coverage = low/medium risk."""
        impact_rec = {
            "target": "util.format",
            "target_type": "Function",
            "target_file": "util.py",
            "direct_callers": ["app.main", "app.helper"],
            "affected_files": ["util.py", "app.py"],
            "test_files": ["test_util.py"],
        }
        transitive_callers = [
            {"caller": "app.main", "file_path": "app.py", "depth": 1},
            {"caller": "app.helper", "file_path": "app.py", "depth": 1},
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),
            _qr(transitive_callers),
            _qr([{"test_file": "test_util.py"}]),  # Has tests
            _empty(),
            _empty(),
            _empty(),
            _empty(),  # routes
            _qr([{"is_entry_point": False, "is_exported": False}]),
            _qr([{"count": 0}]),
        ]
        result = engine.get_impact_analysis("util.format")
        assert result is not None
        assert result["has_tests"] is True
        assert result["risk_level"] in ("low", "medium")
        assert result["blast_radius_score"] < 60

    def test_callback_heavy_function(self):
        """Function passed as callback many times = medium+ direct impact."""
        impact_rec = {
            "target": "handlers.on_click",
            "target_type": "Function",
            "target_file": "handlers.py",
            "direct_callers": ["app.setup"],
            "affected_files": ["handlers.py", "app.py"],
        }
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),
            _empty(),  # transitive
            _empty(),  # test files
            _empty(),
            _empty(),
            _empty(),
            _empty(),  # routes
            _qr([{"is_entry_point": False, "is_exported": False}]),
            _qr([{"count": 10}]),  # PASSED_TO 10 times (high callback usage)
        ]
        result = engine.get_impact_analysis("handlers.on_click")
        assert result is not None
        assert result["passed_to_count"] == 10
        # 10 callbacks * 8 pts/callback (capped at 20) + 1 direct caller * 5 = 25 direct score
        assert result["direct_impact_score"] >= 20  # Callbacks contribute significantly
        assert result["risk_level"] in ("low", "medium", "high")  # Depends on transitive

    def test_missing_entity(self):
        """Non-existent entity should return None."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _empty(),  # impact_analysis returns None
        ]
        result = engine.get_impact_analysis("nonexistent")
        assert result is None


# ==================================================================
# 7. search
# ==================================================================


class TestSearch:
    def test_search_by_name(self):
        recs = [
            {"type": "Function", "name": "foo", "qualified_name": "mod.foo", "file_path": "mod.py", "start_line": 10}
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.search("foo", search_type="name")
        assert len(result) == 1
        assert result[0]["name"] == "foo"

    def test_search_by_docstring(self):
        recs = [
            {
                "type": "Function",
                "name": "bar",
                "qualified_name": "mod.bar",
                "file_path": "mod.py",
                "docstring": "Handles bar operations",
            }
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.search("bar operations", search_type="docstring")
        assert len(result) == 1

    def test_search_all(self):
        recs = [
            {
                "type": "Class",
                "name": "MyClass",
                "qualified_name": "mod.MyClass",
                "file_path": "mod.py",
                "start_line": 1,
            }
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.search("MyClass", search_type="all")
        assert len(result) == 1

    def test_search_empty(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.search("nonexistent") == []


# ==================================================================
# 8. get_repo_overview
# ==================================================================


class TestGetRepoOverview:
    def test_repo_overview(self):
        node_stats = [
            {"type": "Function", "count": 100},
            {"type": "Class", "count": 20},
            {"type": "File", "count": 50},
        ]
        rel_stats = [
            {"type": "CALLS", "count": 200},
            {"type": "DEFINED_IN", "count": 120},
        ]
        files = [
            {"path": "a.py", "language": "python"},
            {"path": "b.ts", "language": "typescript"},
        ]
        top_funcs = [
            {"name": "mod.create_user", "file_path": "mod.py", "caller_count": 15},
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr(node_stats),
            _qr(rel_stats),
            _qr(files),
            _qr(top_funcs),
        ]
        result = engine.get_repo_overview()
        assert result["nodes"]["Function"] == 100
        assert result["relationships"]["CALLS"] == 200
        assert len(result["files"]) == 2
        assert set(result["languages"]) == {"python", "typescript"}
        assert len(result["most_called_functions"]) == 1


# ==================================================================
# 9. Documentation queries
# ==================================================================


class TestDocQueries:
    def test_get_docs_for_entity(self):
        recs = [
            {
                "doc_path": "README.md",
                "doc_title": "API",
                "section": "Usage",
                "line": 10,
                "references_entity": "create_user",
            }
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_docs_for_entity("create_user")
        assert len(result) == 1
        assert result[0]["doc_path"] == "README.md"

    def test_get_docs_for_entity_empty(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_docs_for_entity("undocumented") == []

    def test_get_doc_staleness(self):
        recs = [
            {"doc_path": "README.md", "title": "Guide", "doc_type": "readme", "total_refs": 5, "resolved_sections": 3}
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_doc_staleness()
        assert len(result) == 1
        assert result[0]["total_refs"] == 5

    def test_get_doc_overview(self):
        stats = [{"doc_type": "readme", "count": 2}]
        total_refs = [{"count": 10}]
        top_ref = [{"entity": "create_user", "entity_type": "Function", "ref_count": 5}]

        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr(stats), _qr(total_refs), _qr(top_ref)]
        result = engine.get_doc_overview()
        assert result["doc_types"]["readme"] == 2
        assert result["total_references"] == 10
        assert len(result["most_referenced_entities"]) == 1

    def test_get_doc_overview_no_refs(self):
        engine, graph = _make_engine()
        graph.execute.side_effect = [_empty(), _empty(), _empty()]
        result = engine.get_doc_overview()
        assert result["total_references"] == 0


# ==================================================================
# 10. get_routes
# ==================================================================


class TestGetRoutes:
    def test_get_all_routes(self):
        recs = [
            {
                "method": "GET",
                "path": "/users",
                "handler": "list_users",
                "file_path": "api.py",
                "line": 10,
                "middleware": None,
                "handler_signature": "def list_users()",
            },
            {
                "method": "POST",
                "path": "/users",
                "handler": "create_user",
                "file_path": "api.py",
                "line": 20,
                "middleware": None,
                "handler_signature": "def create_user()",
            },
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_routes()
        assert len(result) == 2

    def test_get_routes_filtered_by_method(self):
        recs = [
            {
                "method": "GET",
                "path": "/users",
                "handler": "list_users",
                "file_path": "api.py",
                "line": 10,
                "middleware": None,
                "handler_signature": "def list_users()",
            }
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_routes(method="get")
        assert len(result) == 1
        # Verify the method was uppercased in the query
        call_params = graph.execute.call_args[0][1]
        assert call_params["method"] == "GET"

    def test_get_routes_empty(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_routes() == []


# ==================================================================
# 11. get_components
# ==================================================================


class TestGetComponents:
    def test_returns_components(self):
        recs = [
            {
                "name": "Button",
                "qualified_name": "ui.Button",
                "file_path": "ui.tsx",
                "start_line": 5,
                "signature": "function Button()",
                "is_exported": True,
                "usage_count": 12,
            },
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_components(limit=10)
        assert len(result) == 1
        assert result[0]["name"] == "Button"

    def test_components_empty(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_components() == []


# ==================================================================
# 12. Tests queries
# ==================================================================


class TestTestQueries:
    def test_get_tests_for_entity_direct_calls(self):
        direct = [
            {
                "test_name": "test_create",
                "test_qualified_name": "test_mod.test_create",
                "test_file": "test_mod.py",
                "line": 10,
                "via": "calls",
            },
        ]
        engine, graph = _make_engine()
        # 3 queries: TESTS_FUNCTION, CALLS, file_level
        graph.execute.side_effect = [_empty(), _qr(direct), _empty()]
        result = engine.get_tests_for_entity("create_user")
        assert len(result) == 1
        assert result[0]["via"] == "calls"

    def test_get_tests_for_entity_file_coverage_only(self):
        file_level = [{"test_file": "test_mod.py", "via": "file_coverage"}]
        engine, graph = _make_engine()
        # 3 queries: TESTS_FUNCTION, CALLS, file_level
        graph.execute.side_effect = [_empty(), _empty(), _qr(file_level)]
        result = engine.get_tests_for_entity("create_user")
        assert len(result) == 1
        assert result[0]["via"] == "file_coverage"
        assert result[0]["test_name"] is None

    def test_get_tests_deduplicates_by_file(self):
        """Direct calls take precedence over file-level coverage for same file."""
        direct = [
            {
                "test_name": "test_create",
                "test_qualified_name": "test_mod.test_create",
                "test_file": "test_mod.py",
                "line": 10,
                "via": "calls",
            },
        ]
        file_level = [{"test_file": "test_mod.py", "via": "file_coverage"}]
        engine, graph = _make_engine()
        # 3 queries: TESTS_FUNCTION, CALLS, file_level
        graph.execute.side_effect = [_empty(), _qr(direct), _qr(file_level)]
        result = engine.get_tests_for_entity("create_user")
        # Should not duplicate test_mod.py
        assert len(result) == 1
        assert result[0]["via"] == "calls"

    def test_get_untested_functions(self):
        recs = [
            {"name": "orphan_func", "qualified_name": "mod.orphan_func", "file_path": "mod.py", "complexity": 8},
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_untested_functions(limit=10)
        assert len(result) == 1
        assert result[0]["name"] == "orphan_func"


# ==================================================================
# 13. get_todos
# ==================================================================


class TestGetTodos:
    def test_returns_todos(self):
        recs = [{"file_path": "mod.py", "todo_count": 3, "language": "python"}]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_todos(limit=10)
        assert len(result) == 1
        assert result[0]["todo_count"] == 3

    def test_no_todos(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.get_todos() == []


# ==================================================================
# 14. infer_conventions
# ==================================================================


class TestInferConventions:
    def test_infer_conventions(self):
        dir_stats = [{"language": "python", "file_count": 30}]
        component_stats = [{"file_path": "src/components/Button.tsx", "is_documentation": False}]
        test_stats = [{"path": "tests/test_api.py"}, {"path": "tests/test_auth.py"}]
        route_stats = [{"method": "GET", "count": 10}, {"method": "POST", "count": 5}]
        entry_points = [{"name": "main", "file_path": "app.py", "signature": "def main()"}]
        top_imported = [{"path": "src/utils.py", "import_count": 15}]
        visibility_stats = [{"visibility": "public", "count": 80}, {"visibility": "private", "count": 20}]

        engine, graph = _make_engine()
        layer_violations = [{"source": "src/routes/users.py", "target": "src/db/models.py"}]
        graph.execute.side_effect = [
            _qr(dir_stats),
            _qr(component_stats),
            _qr(test_stats),
            _qr(route_stats),
            _qr(entry_points),
            _qr(top_imported),
            _qr(visibility_stats),
            _qr(layer_violations),  # detect_layer_violations query
            _qr([]),  # _detect_frameworks: dependency names (empty)
        ]
        result = engine.infer_conventions()
        assert result["languages"]["python"] == 30
        assert result["route_methods"]["GET"] == 10
        assert "tests" in result["test_locations"]
        assert "src/components" in result["component_locations"]
        assert result["production_components"] == 1
        assert result["documentation_components"] == 0
        assert len(result["entry_points"]) == 1
        assert result["visibility_distribution"]["public"] == 80
        assert "layer_violations" in result
        assert result["layer_violations"]["total"] == 1
        assert result["frameworks"] == {}


# ==================================================================
# 15. Dependencies
# ==================================================================


class TestDependencies:
    def test_get_dependencies(self):
        recs = [
            {"name": "requests", "file_count": 5, "function_count": 12},
            {"name": "numpy", "file_count": 3, "function_count": 8},
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_dependencies(limit=10)
        assert len(result) == 2
        assert result[0]["name"] == "requests"

    def test_get_dependency_users(self):
        files = [{"file_path": "api.py"}, {"file_path": "client.py"}]
        funcs = [
            {"name": "fetch", "qualified_name": "api.fetch", "file_path": "api.py", "start_line": 10, "is_test": False},
        ]
        health = [
            {
                "version": ">=2.28.0",
                "latest_version": "2.31.0",
                "is_outdated": True,
                "vulnerability_count": 0,
                "vulnerabilities": [],
            }
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr(files), _qr(funcs), _qr(health)]
        result = engine.get_dependency_users("requests")
        assert result["dependency"] == "requests"
        assert result["file_count"] == 2
        assert result["function_count"] == 1
        assert "api.py" in result["files"]
        assert result["latest_version"] == "2.31.0"


# ==================================================================
# Trace path
# ==================================================================


class TestFindPath:
    def test_find_path(self):
        recs = [
            {"path": ["api.handler", "service.create_user", "email.send"], "hops": 2},
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.find_path("api.handler", "email.send")
        assert len(result) == 1
        assert result[0]["hops"] == 2

    def test_find_path_no_connection(self):
        engine, graph = _make_engine()
        graph.execute.return_value = _empty()
        assert engine.find_path("a", "b") == []


# ==================================================================
# _load_source (private helper)
# ==================================================================


class TestLoadSource:
    def test_load_source_success(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            src = os.path.join(tmpdir, "mod.py")
            lines = [f"line {i}" for i in range(1, 11)]
            with open(src, "w") as f:
                f.write("\n".join(lines))

            engine = QueryEngine(MagicMock(), repo_path=tmpdir)
            result = engine._load_source("mod.py", 3, 5)
            assert result is not None
            assert "line 3" in result
            assert "line 5" in result
            assert "line 6" not in result

    def test_load_source_no_repo_path(self):
        engine = QueryEngine(MagicMock(), repo_path=None)
        assert engine._load_source("mod.py", 1, 5) is None

    def test_load_source_missing_file(self):
        engine = QueryEngine(MagicMock(), repo_path="/nonexistent")
        assert engine._load_source("missing.py", 1, 5) is None


# ==================================================================
# Component filtering (is_documentation)
# ==================================================================


class TestComponentFiltering:
    def test_exclude_docs_filters_documentation_components(self):
        """exclude_docs=True should pass the parameter to the Cypher query."""
        recs = [
            {
                "name": "Button",
                "qualified_name": "src/Button.tsx::Button",
                "file_path": "src/Button.tsx",
                "start_line": 1,
                "signature": "function Button()",
                "is_exported": True,
                "is_documentation": False,
                "usage_count": 5,
            },
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_components(limit=50, exclude_docs=True)
        assert len(result) == 1
        # Verify the query was called with correct params
        params = graph.execute.call_args[0][1]
        assert params["exclude_docs"] is True

    def test_include_docs_shows_all(self):
        """exclude_docs=False should pass false to the Cypher query."""
        recs = [
            {
                "name": "Button",
                "qualified_name": "src/Button.tsx::Button",
                "file_path": "src/Button.tsx",
                "start_line": 1,
                "signature": "function Button()",
                "is_exported": True,
                "is_documentation": False,
                "usage_count": 5,
            },
            {
                "name": "MockButton",
                "qualified_name": "docs/MockButton.tsx::MockButton",
                "file_path": "docs/MockButton.tsx",
                "start_line": 1,
                "signature": "function MockButton()",
                "is_exported": True,
                "is_documentation": True,
                "usage_count": 0,
            },
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_components(limit=50, exclude_docs=False)
        assert len(result) == 2
        params = graph.execute.call_args[0][1]
        assert params["exclude_docs"] is False


# ==================================================================
# Framework detection
# ==================================================================


class TestFrameworkDetection:
    def test_nextjs_detected_from_deps(self):
        """Next.js should be detected from 'next' Dependency node."""
        engine, graph = _make_engine()
        # Queries: deps, nextjs(app/pages/directives/api/middleware),
        # react conventions (css modules, func, class), ui conventions (shadcn)
        graph.execute.side_effect = [
            _qr([{"name": "next"}, {"name": "react"}]),  # deps
            _qr([{"c": 10}]),  # app files
            _qr([{"c": 0}]),  # pages files
            _qr([{"directive": "use client", "c": 5}]),  # directives
            _qr([{"c": 3}]),  # api routes
            _qr([{"c": 1}]),  # middleware
            # react conventions (merged into nextjs.react)
            _qr([{"c": 0}]),  # css modules
            _qr([{"c": 25}]),  # functional components
            _qr([{"c": 2}]),  # class components
            # ui conventions
            _qr([{"c": 0}]),  # shadcn imports
        ]
        result = engine._detect_frameworks()
        assert "nextjs" in result
        assert result["nextjs"]["router"] == "app"
        assert result["nextjs"]["use_client"] == 5
        assert result["nextjs"]["api_routes"] == 3
        assert result["nextjs"]["has_middleware"] is True

    def test_react_conventions_detected(self):
        """React state management and styling should be detected from deps."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "react"}, {"name": "zustand"}, {"name": "tailwindcss"}]),  # deps
            # React conventions: css modules check, func components, class components
            _qr([{"c": 0}]),  # css modules
            _qr([{"c": 25}]),  # functional components
            _qr([{"c": 2}]),  # class components
            # ui conventions (react detected → _detect_ui_conventions)
            _qr([{"c": 0}]),  # shadcn imports
        ]
        result = engine._detect_frameworks()
        assert "react" in result
        assert "zustand" in result["react"]["state_management"]
        assert "tailwind" in result["react"]["styling"]
        assert result["react"]["component_style"] == "functional"

    def test_empty_deps_returns_empty(self):
        """No deps should return empty frameworks dict."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr([])
        result = engine._detect_frameworks()
        assert result == {}


# ==================================================================
# External service mapping
# ==================================================================


class TestExternalServices:
    def test_supabase_clerk_stripe_classified(self):
        """Known packages should be classified into correct categories."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(
            [
                {"name": "@supabase/supabase-js", "version": "2.39.0"},
                {"name": "@clerk/nextjs", "version": "4.29.0"},
                {"name": "stripe", "version": "14.0.0"},
                {"name": "some-unknown-lib", "version": "1.0.0"},
            ]
        )
        result = engine.get_external_services()
        assert "database" in result["categories"]
        assert "auth" in result["categories"]
        assert "payments" in result["categories"]
        assert result["categories"]["database"]["label"] == "Database & ORM"
        assert len(result["uncategorized"]) == 1
        assert result["uncategorized"][0]["name"] == "some-unknown-lib"

    def test_prefix_matching_for_scoped_packages(self):
        """Packages should match by exact name (no wildcard patterns in current impl)."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(
            [
                {"name": "tailwindcss", "version": "3.4.0"},
                {"name": "clsx", "version": "2.0.0"},
            ]
        )
        result = engine.get_external_services()
        assert "ui" in result["categories"]
        ui_pkg_names = [p["name"] for p in result["categories"]["ui"]["packages"]]
        assert "tailwindcss" in ui_pkg_names
        assert "clsx" in ui_pkg_names

    def test_unknown_packages_uncategorized(self):
        """Packages not in any category should be in uncategorized."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(
            [
                {"name": "my-custom-lib", "version": "1.0.0"},
            ]
        )
        result = engine.get_external_services()
        assert len(result["categories"]) == 0
        assert len(result["uncategorized"]) == 1

    def test_empty_deps_returns_empty(self):
        """No dependencies should return empty categories."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr([])
        result = engine.get_external_services()
        assert result["categories"] == {}
        assert result["uncategorized"] == []


# ==================================================================
# Vibe coder stack detection
# ==================================================================


class TestVibeCoderDetection:
    def test_supabase_conventions_detected(self):
        """Supabase conventions should include client files and auth helpers."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "@supabase/supabase-js"}, {"name": "@supabase/auth-helpers-nextjs"}]),  # deps
            # supabase conventions: client import count
            _qr([{"c": 5}]),
            # supabase edge functions
            _qr([{"c": 2}]),
        ]
        result = engine._detect_frameworks()
        assert "supabase" in result
        assert result["supabase"]["client_files"] == 5
        assert result["supabase"]["uses_auth_helpers"] is True
        assert result["supabase"]["edge_function_count"] == 2

    def test_shadcn_detected_via_imports(self):
        """shadcn should be detected from @/components/ui/* import patterns."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "react"}, {"name": "tailwindcss"}]),  # deps
            # react conventions: css modules, func components, class components
            _qr([{"c": 0}]),
            _qr([{"c": 10}]),
            _qr([{"c": 0}]),
            # ui conventions: shadcn imports
            _qr([{"c": 8}]),
        ]
        result = engine._detect_frameworks()
        assert "ui" in result
        assert result["ui"]["uses_shadcn"] is True
        assert result["ui"]["shadcn_component_count"] == 8

    def test_auth_provider_clerk(self):
        """Clerk should be detected as auth provider."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "@clerk/nextjs"}]),  # deps
        ]
        result = engine._detect_frameworks()
        assert "auth" in result
        assert result["auth"]["provider"] == "clerk"

    def test_auth_provider_nextauth(self):
        """NextAuth should be detected as auth provider."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "next-auth"}]),  # deps
        ]
        result = engine._detect_frameworks()
        assert "auth" in result
        assert result["auth"]["provider"] == "nextauth"

    def test_orm_prisma_with_schema(self):
        """Prisma with schema file should show has_schema=True."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "@prisma/client"}]),  # deps
            # orm: prisma schema check
            _qr([{"c": 1}]),
        ]
        result = engine._detect_frameworks()
        assert "orm" in result
        assert result["orm"]["orm"] == "prisma"
        assert result["orm"]["has_schema"] is True

    def test_orm_drizzle(self):
        """Drizzle should be detected as ORM."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "drizzle-orm"}]),  # deps
        ]
        result = engine._detect_frameworks()
        assert "orm" in result
        assert result["orm"]["orm"] == "drizzle"

    def test_payment_stripe(self):
        """Stripe should be detected as payment provider."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "stripe"}]),  # deps
        ]
        result = engine._detect_frameworks()
        assert "payments" in result
        assert result["payments"]["provider"] == "stripe"

    def test_payment_lemonsqueezy(self):
        """LemonSqueezy should be detected as payment provider."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([{"name": "@lemonsqueezy/lemonsqueezy.js"}]),  # deps
        ]
        result = engine._detect_frameworks()
        # lemonsqueezy is not in _FRAMEWORK_PACKAGES, so no "payments" key
        # It is only in _SERVICE_CATEGORIES
        assert "payments" not in result

    def test_full_vibe_stack(self):
        """Full vibe coder stack should detect all components."""
        engine, graph = _make_engine()
        graph.execute.side_effect = [
            # deps
            _qr(
                [
                    {"name": "next"},
                    {"name": "react"},
                    {"name": "@supabase/supabase-js"},
                    {"name": "@clerk/nextjs"},
                    {"name": "stripe"},
                    {"name": "@prisma/client"},
                    {"name": "tailwindcss"},
                    {"name": "lucide-react"},
                ]
            ),
            # nextjs conventions: app files, pages files, directives, api routes, middleware
            _qr([{"c": 20}]),  # app files
            _qr([{"c": 0}]),  # pages files
            _qr([{"directive": "use client", "c": 12}]),  # directives
            _qr([{"c": 5}]),  # api routes
            _qr([{"c": 1}]),  # middleware
            # react conventions (merged into nextjs.react): css modules, func, class
            _qr([{"c": 0}]),
            _qr([{"c": 30}]),
            _qr([{"c": 0}]),
            # supabase conventions: client imports, edge functions
            _qr([{"c": 8}]),
            _qr([{"c": 1}]),
            # auth conventions (clerk detected from deps)
            # orm conventions: prisma schema check
            _qr([{"c": 1}]),
            # ui conventions: shadcn imports
            _qr([{"c": 6}]),
            # payments (stripe detected from deps)
        ]
        result = engine._detect_frameworks()
        assert "nextjs" in result
        assert "supabase" in result
        assert "auth" in result
        assert result["auth"]["provider"] == "clerk"
        assert "orm" in result
        assert result["orm"]["orm"] == "prisma"
        assert "ui" in result
        assert result["ui"]["uses_shadcn"] is True
        assert result["ui"]["icon_library"] == "lucide-react"
        assert "payments" in result
        assert result["payments"]["provider"] == "stripe"


# ==================================================================
# Changelog
# ==================================================================


class TestChangelog:
    def test_diff_computed_correctly(self):
        """Two snapshots should produce correct deltas."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(
            [
                {
                    "s": {
                        "captured_at": "2024-01-02T00:00:00Z",
                        "file_count": 15,
                        "function_count": 50,
                        "class_count": 5,
                        "route_count": 3,
                        "test_count": 10,
                        "component_count": 8,
                        "dependency_count": 20,
                        "edge_count": 100,
                    }
                },
                {
                    "s": {
                        "captured_at": "2024-01-01T00:00:00Z",
                        "file_count": 12,
                        "function_count": 45,
                        "class_count": 5,
                        "route_count": 3,
                        "test_count": 8,
                        "component_count": 6,
                        "dependency_count": 18,
                        "edge_count": 80,
                    }
                },
            ]
        )
        result = engine.get_changelog()
        assert result["status"] == "diff"
        assert result["changes"]["file_count"]["delta"] == 3
        assert result["changes"]["function_count"]["delta"] == 5
        assert result["changes"]["class_count"]["delta"] == 0
        assert result["changes"]["edge_count"]["delta"] == 20
        assert "Added 3 files" in result["summary"]

    def test_single_snapshot_first_ingestion(self):
        """Single snapshot should return first_ingestion status."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(
            [
                {
                    "s": {
                        "captured_at": "2024-01-01T00:00:00Z",
                        "file_count": 10,
                        "function_count": 30,
                        "class_count": 3,
                        "route_count": 2,
                        "test_count": 5,
                        "component_count": 4,
                        "dependency_count": 15,
                        "edge_count": 50,
                    }
                },
            ]
        )
        result = engine.get_changelog()
        assert result["status"] == "first_ingestion"
        assert result["current"]["file_count"] == 10

    def test_no_snapshots_returns_empty(self):
        """No snapshots should return no_snapshots status."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr([])
        result = engine.get_changelog()
        assert result["status"] == "no_snapshots"

    def test_summary_text_generated(self):
        """Summary should describe added and removed items."""
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(
            [
                {
                    "s": {
                        "captured_at": "2024-01-02T00:00:00Z",
                        "file_count": 10,
                        "function_count": 30,
                        "class_count": 3,
                        "route_count": 1,
                        "test_count": 5,
                        "component_count": 4,
                        "dependency_count": 15,
                        "edge_count": 50,
                    }
                },
                {
                    "s": {
                        "captured_at": "2024-01-01T00:00:00Z",
                        "file_count": 10,
                        "function_count": 30,
                        "class_count": 3,
                        "route_count": 3,
                        "test_count": 5,
                        "component_count": 4,
                        "dependency_count": 15,
                        "edge_count": 50,
                    }
                },
            ]
        )
        result = engine.get_changelog()
        assert "Removed 2 routes" in result["summary"]


class TestGetChangeImpact:
    """get_change_impact bundles scored impact + covering tests for a pre-edit check."""

    def test_bundles_impact_and_tests(self):
        from unittest.mock import MagicMock

        engine, _ = _make_engine()
        engine.get_impact_analysis = MagicMock(
            return_value={
                "target": "mod.foo",
                "target_type": "Function",
                "target_file": "mod.py",
                "blast_radius_score": 72.0,
                "risk_level": "high",
                "direct_callers": ["a", "b"],
                "direct_callers_count": 2,
                "affected_files": ["x.py"],
                "affected_files_count": 1,
                "is_entry_point": False,
                "is_exported": True,
                "has_route": False,
            }
        )
        engine.get_tests_for_entity = MagicMock(return_value=[{"test_name": "test_foo", "depth": 1}])

        out = engine.get_change_impact("mod.foo")
        assert out["blast_radius_score"] == 72.0
        assert out["risk_level"] == "high"
        assert out["tests_to_run"] == [{"test_name": "test_foo", "depth": 1}]
        assert "HIGH risk" in out["recommendation"]
        assert "1 covering test" in out["recommendation"]

    def test_no_tests_recommends_adding(self):
        from unittest.mock import MagicMock

        engine, _ = _make_engine()
        engine.get_impact_analysis = MagicMock(
            return_value={
                "target": "mod.foo",
                "blast_radius_score": 10.0,
                "risk_level": "low",
                "direct_callers_count": 0,
                "affected_files_count": 0,
            }
        )
        engine.get_tests_for_entity = MagicMock(return_value=[])

        out = engine.get_change_impact("mod.foo")
        assert out["tests_to_run"] == []
        assert "add tests" in out["recommendation"].lower()

    def test_not_found_returns_none(self):
        from unittest.mock import MagicMock

        engine, _ = _make_engine()
        engine.get_impact_analysis = MagicMock(return_value=None)
        assert engine.get_change_impact("nope") is None
