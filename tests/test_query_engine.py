"""Tests for the query engine."""

from __future__ import annotations

import os
import tempfile
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
                {"name": "do_thing", "signature": "def do_thing()", "visibility": "public", "is_async": False, "docstring": None}
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
            _qr([file_rec]),       # main file query
            _qr(route_recs),       # routes query
            _qr(test_recs),        # test coverage query
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
            _qr([file_rec]),       # main file query
            _empty(),              # routes
            _empty(),              # test coverage
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
            {"test_name": "test_create", "test_qualified_name": "test_mod.test_create",
             "test_file": "test_mod.py", "line": 10, "via": "calls"},
        ]
        test_funcs_file = []
        routes = []

        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr([impact_rec]),             # main impact query
            _qr(transitive_callers),       # get_callers (inside impact)
            _qr(test_files),               # test file coverage
            _qr(test_funcs_direct),        # get_tests_for_entity -> direct
            _qr(test_funcs_file),          # get_tests_for_entity -> file_level
            _qr(routes),                   # routes query
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
            _qr([impact_rec]),    # main impact query
            _empty(),             # get_callers
            _empty(),             # get_tests_for_entity -> direct
            _empty(),             # get_tests_for_entity -> file_level
            _empty(),             # routes
        ]
        result = engine.impact_analysis("orphan")
        assert result is not None
        assert "test_files" not in result


# ==================================================================
# 7. search
# ==================================================================


class TestSearch:
    def test_search_by_name(self):
        recs = [{"type": "Function", "name": "foo", "qualified_name": "mod.foo",
                 "file_path": "mod.py", "start_line": 10}]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.search("foo", search_type="name")
        assert len(result) == 1
        assert result[0]["name"] == "foo"

    def test_search_by_docstring(self):
        recs = [{"type": "Function", "name": "bar", "qualified_name": "mod.bar",
                 "file_path": "mod.py", "docstring": "Handles bar operations"}]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.search("bar operations", search_type="docstring")
        assert len(result) == 1

    def test_search_all(self):
        recs = [{"type": "Class", "name": "MyClass", "qualified_name": "mod.MyClass",
                 "file_path": "mod.py", "start_line": 1}]
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
        recs = [{"doc_path": "README.md", "doc_title": "API", "section": "Usage",
                 "line": 10, "references_entity": "create_user"}]
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
        recs = [{"doc_path": "README.md", "title": "Guide", "doc_type": "readme",
                 "total_refs": 5, "resolved_sections": 3}]
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
            {"method": "GET", "path": "/users", "handler": "list_users",
             "file_path": "api.py", "line": 10, "middleware": None, "handler_signature": "def list_users()"},
            {"method": "POST", "path": "/users", "handler": "create_user",
             "file_path": "api.py", "line": 20, "middleware": None, "handler_signature": "def create_user()"},
        ]
        engine, graph = _make_engine()
        graph.execute.return_value = _qr(recs)
        result = engine.get_routes()
        assert len(result) == 2

    def test_get_routes_filtered_by_method(self):
        recs = [{"method": "GET", "path": "/users", "handler": "list_users",
                 "file_path": "api.py", "line": 10, "middleware": None, "handler_signature": "def list_users()"}]
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
            {"name": "Button", "qualified_name": "ui.Button", "file_path": "ui.tsx",
             "start_line": 5, "signature": "function Button()", "is_exported": True, "usage_count": 12},
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
            {"test_name": "test_create", "test_qualified_name": "test_mod.test_create",
             "test_file": "test_mod.py", "line": 10, "via": "calls"},
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr(direct), _empty()]
        result = engine.get_tests_for_entity("create_user")
        assert len(result) == 1
        assert result[0]["via"] == "calls"

    def test_get_tests_for_entity_file_coverage_only(self):
        file_level = [{"test_file": "test_mod.py", "via": "file_coverage"}]
        engine, graph = _make_engine()
        graph.execute.side_effect = [_empty(), _qr(file_level)]
        result = engine.get_tests_for_entity("create_user")
        assert len(result) == 1
        assert result[0]["via"] == "file_coverage"
        assert result[0]["test_name"] is None

    def test_get_tests_deduplicates_by_file(self):
        """Direct calls take precedence over file-level coverage for same file."""
        direct = [
            {"test_name": "test_create", "test_qualified_name": "test_mod.test_create",
             "test_file": "test_mod.py", "line": 10, "via": "calls"},
        ]
        file_level = [{"test_file": "test_mod.py", "via": "file_coverage"}]
        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr(direct), _qr(file_level)]
        result = engine.get_tests_for_entity("create_user")
        # Should not duplicate test_mod.py
        assert len(result) == 1
        assert result[0]["via"] == "calls"

    def test_get_untested_functions(self):
        recs = [
            {"name": "orphan_func", "qualified_name": "mod.orphan_func",
             "file_path": "mod.py", "complexity": 8},
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
        component_stats = [{"file_path": "src/components/Button.tsx"}]
        test_stats = [{"path": "tests/test_api.py"}, {"path": "tests/test_auth.py"}]
        route_stats = [{"method": "GET", "count": 10}, {"method": "POST", "count": 5}]
        entry_points = [{"name": "main", "file_path": "app.py", "signature": "def main()"}]
        top_imported = [{"path": "src/utils.py", "import_count": 15}]
        visibility_stats = [{"visibility": "public", "count": 80}, {"visibility": "private", "count": 20}]

        engine, graph = _make_engine()
        graph.execute.side_effect = [
            _qr(dir_stats),
            _qr(component_stats),
            _qr(test_stats),
            _qr(route_stats),
            _qr(entry_points),
            _qr(top_imported),
            _qr(visibility_stats),
        ]
        result = engine.infer_conventions()
        assert result["languages"]["python"] == 30
        assert result["route_methods"]["GET"] == 10
        assert "tests" in result["test_locations"]
        assert "src/components" in result["component_locations"]
        assert len(result["entry_points"]) == 1
        assert result["visibility_distribution"]["public"] == 80


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
            {"name": "fetch", "qualified_name": "api.fetch", "file_path": "api.py",
             "start_line": 10, "is_test": False},
        ]
        engine, graph = _make_engine()
        graph.execute.side_effect = [_qr(files), _qr(funcs)]
        result = engine.get_dependency_users("requests")
        assert result["dependency"] == "requests"
        assert result["file_count"] == 2
        assert result["function_count"] == 1
        assert "api.py" in result["files"]


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
