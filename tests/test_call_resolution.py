"""Tests for import-aware call resolution and TESTS edges in the ingestion pipeline."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.ingestion.batch import BatchCollector
from gristle.ingestion.pipeline import IngestionPipeline, IngestionResult
from gristle.models import ParsedClass, ParsedFile, ParsedFunction, ParsedImport


def _make_graph_mock() -> MagicMock:
    """Create a mock GraphClient that tracks node/relationship creation."""
    mock = MagicMock()
    mock.repo_id = "test"
    mock.batch_create_nodes.return_value = 0
    mock.batch_create_relationships.return_value = 0
    mock.batch_merge_relationships.return_value = 0
    return mock


def _extract_batch_merge_rels(mock_graph: MagicMock) -> list[tuple[str, str, str]]:
    """Extract (from_id, to_id, rel_type) tuples from batch_merge_relationships calls."""
    rels = []
    for call in mock_graph.batch_merge_relationships.call_args_list:
        rel_type = call[0][0]
        items = call[0][1]
        for item in items:
            rels.append((item["from_id"], item["to_id"], rel_type))
    return rels


def _extract_batch_create_rels(mock_graph: MagicMock) -> list[tuple[str, str, str]]:
    """Extract (from_id, to_id, rel_type) tuples from batch_create_relationships calls."""
    rels = []
    for call in mock_graph.batch_create_relationships.call_args_list:
        rel_type = call[0][0]
        items = call[0][1]
        for item in items:
            rels.append((item["from_id"], item["to_id"], rel_type))
    return rels


def _extract_batch_nodes(mock_graph: MagicMock, label: str | None = None) -> list[dict]:
    """Extract node property dicts from batch_create_nodes calls, optionally filtering by label."""
    nodes = []
    for call in mock_graph.batch_create_nodes.call_args_list:
        call_label = call[0][0]
        items = call[0][1]
        if label is None or call_label == label:
            nodes.extend(items)
    return nodes


def _make_func(
    name: str,
    file_path: str,
    calls: list[str] | None = None,
    qualified_name: str | None = None,
    is_exported: bool = False,
    is_test: bool = False,
    is_fixture: bool = False,
    parameters: list[str] | None = None,
) -> ParsedFunction:
    qn = qualified_name or f"{file_path}::{name}"
    return ParsedFunction(
        name=name,
        qualified_name=qn,
        file_path=file_path,
        start_line=1,
        end_line=10,
        signature=f"function {name}()",
        calls=calls or [],
        is_exported=is_exported,
        is_test=is_test,
        is_fixture=is_fixture,
        parameters=parameters or [],
    )


def _make_class(
    name: str,
    file_path: str,
    methods: list[ParsedFunction] | None = None,
    is_exported: bool = False,
    bases: list[str] | None = None,
) -> ParsedClass:
    return ParsedClass(
        name=name,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        start_line=1,
        end_line=50,
        signature=f"class {name}",
        methods=methods or [],
        is_exported=is_exported,
        bases=bases or [],
    )


def _make_file(
    path: str,
    language: str = "typescript",
    functions: list[ParsedFunction] | None = None,
    classes: list[ParsedClass] | None = None,
    imports: list[ParsedImport] | None = None,
    is_test_file: bool = False,
) -> ParsedFile:
    return ParsedFile(
        path=path,
        language=language,
        functions=functions or [],
        classes=classes or [],
        imports=imports or [],
        line_count=100,
        is_test_file=is_test_file,
    )


def _make_import(
    module_path: str,
    imported_names: list[str] | None = None,
    is_relative: bool = True,
    aliases: dict[str, str] | None = None,
    is_wildcard: bool = False,
) -> ParsedImport:
    return ParsedImport(
        line=1,
        module_path=module_path,
        imported_names=imported_names or [],
        is_relative=is_relative,
        aliases=aliases or {},
        is_wildcard=is_wildcard,
    )


def _setup_pipeline(parsed_files: list[ParsedFile]) -> IngestionPipeline:
    """Build a pipeline with populated maps from parsed files (no graph writes)."""
    mock_graph = _make_graph_mock()
    pipeline = IngestionPipeline(graph=mock_graph)

    # Populate maps as Phase 1 would
    for pf in parsed_files:
        pipeline._build_file_graph(pf, MagicMock())

    # Compute source roots as Phase 2 would
    pipeline._source_roots = pipeline._detect_source_roots(parsed_files)
    pipeline._register_python_source_roots(parsed_files)
    for pf in parsed_files:
        pipeline._parsed_files_by_path[pf.path] = pf
    pipeline._build_init_reexport_maps(parsed_files)
    pipeline._import_cache.clear()

    return pipeline


class TestSameFileResolution:
    """Functions in the same file should resolve without imports."""

    def test_same_file_function_call(self):
        helper = _make_func("helper", "src/utils.ts")
        caller = _make_func("main", "src/utils.ts", calls=["helper"])
        pf = _make_file("src/utils.ts", functions=[helper, caller])

        pipeline = _setup_pipeline([pf])
        result = pipeline._find_callee("helper", caller, pf)

        assert result == "func::src/utils.ts::helper"

    def test_same_file_preferred_over_other_files(self):
        """When a name exists in both the same file and another, prefer same file."""
        local_query = _make_func("query", "src/routes/api.ts")
        caller = _make_func("handler", "src/routes/api.ts", calls=["query"])
        file_a = _make_file("src/routes/api.ts", functions=[local_query, caller])

        remote_query = _make_func("query", "src/graph/client.ts")
        file_b = _make_file("src/graph/client.ts", functions=[remote_query])

        pipeline = _setup_pipeline([file_a, file_b])
        result = pipeline._find_callee("query", caller, file_a)

        assert result == "func::src/routes/api.ts::query"


class TestImportAwareResolution:
    """Calls should resolve based on what the file imports."""

    def test_resolves_imported_function(self):
        """import { query } from './client' -> query() resolves to client's query."""
        query_func = _make_func("query", "src/graph/client.ts")
        client_file = _make_file("src/graph/client.ts", functions=[query_func])

        imp = _make_import("./client", imported_names=["query"])
        caller = _make_func("handler", "src/routes/api.ts", calls=["query"])
        api_file = _make_file(
            "src/routes/api.ts",
            functions=[caller],
            imports=[imp],
        )

        pipeline = _setup_pipeline([client_file, api_file])
        result = pipeline._find_callee("query", caller, api_file)

        assert result == "func::src/graph/client.ts::query"

    def test_disambiguates_via_imports(self):
        """Two files export 'query'; import decides which one."""
        query_a = _make_func("query", "src/db/postgres.ts")
        file_a = _make_file("src/db/postgres.ts", functions=[query_a])

        query_b = _make_func("query", "src/db/redis.ts")
        file_b = _make_file("src/db/redis.ts", functions=[query_b])

        # Caller imports from postgres, not redis
        imp = _make_import("./postgres", imported_names=["query"])
        caller = _make_func("handler", "src/db/handler.ts", calls=["query"])
        caller_file = _make_file(
            "src/db/handler.ts",
            functions=[caller],
            imports=[imp],
        )

        pipeline = _setup_pipeline([file_a, file_b, caller_file])
        result = pipeline._find_callee("query", caller, caller_file)

        assert result == "func::src/db/postgres.ts::query"

    def test_resolves_with_js_extension_convention(self):
        """TypeScript imports with .js extension resolve to .ts files."""
        query_func = _make_func("query", "src/graph/client.ts")
        client_file = _make_file("src/graph/client.ts", functions=[query_func])

        # Import uses .js extension (Node16/NodeNext convention)
        imp = _make_import("./client.js", imported_names=["query"])
        caller = _make_func("handler", "src/graph/api.ts", calls=["query"])
        api_file = _make_file(
            "src/graph/api.ts",
            functions=[caller],
            imports=[imp],
        )

        pipeline = _setup_pipeline([client_file, api_file])
        result = pipeline._find_callee("query", caller, api_file)

        assert result == "func::src/graph/client.ts::query"

    def test_aliased_import_resolves(self):
        """import { query as q } from '../graph/client' -> q() resolves."""
        query_func = _make_func("query", "src/graph/client.ts")
        client_file = _make_file("src/graph/client.ts", functions=[query_func])

        imp = _make_import(
            "../graph/client",
            imported_names=["query"],
            aliases={"query": "q"},
        )
        caller = _make_func("handler", "src/routes/api.ts", calls=["q"])
        api_file = _make_file(
            "src/routes/api.ts",
            functions=[caller],
            imports=[imp],
        )

        pipeline = _setup_pipeline([client_file, api_file])
        result = pipeline._find_callee("q", caller, api_file)

        assert result == "func::src/graph/client.ts::query"


class TestDottedCallResolution:
    """Tests for self.method, this.method, ClassName.method, obj.method."""

    def test_this_method_resolves_to_enclosing_class(self):
        """this.getUser() inside UserService resolves to UserService.getUser."""
        get_user = _make_func(
            "getUser",
            "src/services/user.ts",
            qualified_name="src/services/user.ts::UserService.getUser",
        )
        caller = _make_func(
            "listUsers",
            "src/services/user.ts",
            qualified_name="src/services/user.ts::UserService.listUsers",
            calls=["this.getUser"],
        )
        cls = _make_class(
            "UserService",
            "src/services/user.ts",
            methods=[get_user, caller],
        )
        pf = _make_file("src/services/user.ts", classes=[cls])

        pipeline = _setup_pipeline([pf])
        result = pipeline._find_callee("this.getUser", caller, pf)

        assert result == "func::src/services/user.ts::UserService.getUser"

    def test_self_method_resolves_in_python(self):
        """self.validate() inside User resolves to User.validate."""
        validate = _make_func(
            "validate",
            "models/user.py",
            qualified_name="models/user.py::User.validate",
        )
        caller = _make_func(
            "save",
            "models/user.py",
            qualified_name="models/user.py::User.save",
            calls=["self.validate"],
        )
        cls = _make_class("User", "models/user.py", methods=[validate, caller])
        pf = _make_file("models/user.py", language="python", classes=[cls])

        pipeline = _setup_pipeline([pf])
        result = pipeline._find_callee("self.validate", caller, pf)

        assert result == "func::models/user.py::User.validate"

    def test_class_method_call_in_same_file(self):
        """ClassName.staticMethod() resolves via file-scoped qualified name."""
        static_method = _make_func(
            "create",
            "src/models/user.ts",
            qualified_name="src/models/user.ts::UserFactory.create",
        )
        cls = _make_class(
            "UserFactory",
            "src/models/user.ts",
            methods=[static_method],
        )
        caller = _make_func(
            "handler",
            "src/models/user.ts",
            calls=["UserFactory.create"],
        )
        pf = _make_file("src/models/user.ts", functions=[caller], classes=[cls])

        pipeline = _setup_pipeline([pf])
        result = pipeline._find_callee("UserFactory.create", caller, pf)

        assert result == "func::src/models/user.ts::UserFactory.create"

    def test_imported_object_method_resolves(self):
        """import { client } from './db'; client.query() resolves to db's query."""
        query_func = _make_func("query", "src/db.ts")
        db_file = _make_file("src/db.ts", functions=[query_func])

        imp = _make_import("./db", imported_names=["client"])
        caller = _make_func("handler", "src/api.ts", calls=["client.query"])
        api_file = _make_file(
            "src/api.ts",
            functions=[caller],
            imports=[imp],
        )

        pipeline = _setup_pipeline([db_file, api_file])
        result = pipeline._find_callee("client.query", caller, api_file)

        assert result == "func::src/db.ts::query"


class TestSingleCandidateFallback:
    """When there's only one function with a name globally, use it."""

    def test_unique_name_resolves_without_import(self):
        """A globally unique function name resolves even without an import."""
        unique_func = _make_func("initializeApp", "src/bootstrap.ts")
        bootstrap = _make_file("src/bootstrap.ts", functions=[unique_func])

        caller = _make_func("main", "src/index.ts", calls=["initializeApp"])
        index = _make_file("src/index.ts", functions=[caller])

        pipeline = _setup_pipeline([bootstrap, index])
        result = pipeline._find_callee("initializeApp", caller, index)

        assert result == "func::src/bootstrap.ts::initializeApp"

    def test_ambiguous_name_without_import_returns_none(self):
        """A name with multiple candidates and no import context returns None."""
        query_a = _make_func("query", "src/db/postgres.ts")
        file_a = _make_file("src/db/postgres.ts", functions=[query_a])

        query_b = _make_func("query", "src/db/redis.ts")
        file_b = _make_file("src/db/redis.ts", functions=[query_b])

        caller = _make_func("handler", "src/api.ts", calls=["query"])
        api_file = _make_file("src/api.ts", functions=[caller])

        pipeline = _setup_pipeline([file_a, file_b, api_file])
        result = pipeline._find_callee("query", caller, api_file)

        # No import context, multiple candidates -> None (ambiguous)
        assert result is None


class TestQualifiedNameResolution:
    """Exact qualified names should always resolve."""

    def test_exact_qualified_name(self):
        func = _make_func("query", "src/graph/client.ts")
        pf = _make_file("src/graph/client.ts", functions=[func])

        pipeline = _setup_pipeline([pf])
        caller = _make_func("test", "src/test.ts")
        test_file = _make_file("src/test.ts", functions=[caller])
        pipeline._build_file_graph(test_file, MagicMock())

        result = pipeline._find_callee("src/graph/client.ts::query", caller, test_file)
        assert result == "func::src/graph/client.ts::query"


class TestTestCoverageEdges:
    """TESTS edges should link test files to the production files they import."""

    def _run_test_edges(self, parsed_files: list[ParsedFile]) -> tuple[IngestionPipeline, IngestionResult]:
        """Set up pipeline maps and run _resolve_test_edges."""
        pipeline = _setup_pipeline(parsed_files)
        result = IngestionResult(repo_id="test", repo_path="/tmp")
        batch = BatchCollector(pipeline.graph, batch_size=200)
        pipeline._resolve_test_edges(parsed_files, result, batch)
        batch.flush()
        return pipeline, result

    def test_test_file_creates_tests_edge(self):
        """A test file importing a production file creates a TESTS edge."""
        prod_func = _make_func("query", "src/graph/client.ts")
        prod_file = _make_file("src/graph/client.ts", functions=[prod_func])

        imp = _make_import("../../graph/client", imported_names=["query"])
        test_func = _make_func("testQuery", "src/__tests__/graph/client.test.ts")
        test_file = _make_file(
            "src/__tests__/graph/client.test.ts",
            functions=[test_func],
            imports=[imp],
        )
        test_file.is_test_file = True

        pipeline, result = self._run_test_edges([prod_file, test_file])

        assert result.test_coverage_edges == 1
        rels = _extract_batch_merge_rels(pipeline.graph)
        assert ("file::src/__tests__/graph/client.test.ts", "file::src/graph/client.ts", "TESTS") in rels

    def test_non_test_file_creates_no_tests_edge(self):
        """A non-test file importing another file does NOT create a TESTS edge."""
        prod_a = _make_func("query", "src/graph/client.ts")
        file_a = _make_file("src/graph/client.ts", functions=[prod_a])

        imp = _make_import("./client", imported_names=["query"])
        prod_b = _make_func("handler", "src/graph/api.ts")
        file_b = _make_file(
            "src/graph/api.ts",
            functions=[prod_b],
            imports=[imp],
        )
        # file_b is NOT a test file

        _, result = self._run_test_edges([file_a, file_b])

        assert result.test_coverage_edges == 0

    def test_test_importing_test_helper_skipped(self):
        """A test file importing another test file should NOT create a TESTS edge."""
        helper_func = _make_func("createMock", "src/__tests__/helpers/mock.ts")
        helper_file = _make_file(
            "src/__tests__/helpers/mock.ts",
            functions=[helper_func],
        )
        helper_file.is_test_file = True

        imp = _make_import("../helpers/mock", imported_names=["createMock"])
        test_func = _make_func("testApi", "src/__tests__/routes/api.test.ts")
        test_file = _make_file(
            "src/__tests__/routes/api.test.ts",
            functions=[test_func],
            imports=[imp],
        )
        test_file.is_test_file = True

        _, result = self._run_test_edges([helper_file, test_file])

        assert result.test_coverage_edges == 0

    def test_multiple_imports_from_same_file_deduplicated(self):
        """Multiple imports from the same production file create only one TESTS edge."""
        func_a = _make_func("query", "src/graph/client.ts")
        func_b = _make_func("execute", "src/graph/client.ts")
        prod_file = _make_file("src/graph/client.ts", functions=[func_a, func_b])

        imp1 = _make_import("../../graph/client", imported_names=["query"])
        imp2 = _make_import("../../graph/client", imported_names=["execute"])
        test_func = _make_func("test", "src/__tests__/graph/client.test.ts")
        test_file = _make_file(
            "src/__tests__/graph/client.test.ts",
            functions=[test_func],
            imports=[imp1, imp2],
        )
        test_file.is_test_file = True

        _, result = self._run_test_edges([prod_file, test_file])

        assert result.test_coverage_edges == 1

    def test_test_file_multiple_production_targets(self):
        """A test file importing from multiple production files creates edges to each."""
        func_a = _make_func("query", "src/graph/client.ts")
        file_a = _make_file("src/graph/client.ts", functions=[func_a])

        func_b = _make_func("validate", "src/utils/validation.ts")
        file_b = _make_file("src/utils/validation.ts", functions=[func_b])

        imp1 = _make_import("../../graph/client", imported_names=["query"])
        imp2 = _make_import("../../utils/validation", imported_names=["validate"])
        test_func = _make_func("test", "src/__tests__/integration/full.test.ts")
        test_file = _make_file(
            "src/__tests__/integration/full.test.ts",
            functions=[test_func],
            imports=[imp1, imp2],
        )
        test_file.is_test_file = True

        _, result = self._run_test_edges([file_a, file_b, test_file])

        assert result.test_coverage_edges == 2


class TestExportAwareFiltering:
    """Import resolution should respect export visibility for JS/TS."""

    def test_wildcard_import_only_includes_exported(self):
        """import * from './utils' should only pick up exported entities."""
        exported_fn = _make_func("publicHelper", "src/utils.ts", is_exported=True)
        private_fn = _make_func("_internal", "src/utils.ts", is_exported=False)
        utils_file = _make_file("src/utils.ts", functions=[exported_fn, private_fn])

        imp = _make_import("./utils", imported_names=[], is_relative=True)
        imp.is_wildcard = True
        caller = _make_func("handler", "src/api.ts", calls=["publicHelper", "_internal"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline = _setup_pipeline([utils_file, api_file])

        # publicHelper is exported -> should resolve
        assert pipeline._find_callee("publicHelper", caller, api_file) == "func::src/utils.ts::publicHelper"
        # _internal is NOT exported -> wildcard should NOT include it
        # (falls through to single-candidate which would match, but the
        #  import-aware step should not provide it)
        imported = pipeline._get_imported_entities(api_file)
        assert "publicHelper" in imported
        assert "_internal" not in imported

    def test_named_import_resolves_regardless_of_export(self):
        """import { helper } from './utils' resolves even if not marked exported."""
        helper = _make_func("helper", "src/utils.ts", is_exported=False)
        utils_file = _make_file("src/utils.ts", functions=[helper])

        imp = _make_import("./utils", imported_names=["helper"])
        caller = _make_func("main", "src/app.ts", calls=["helper"])
        app_file = _make_file("src/app.ts", functions=[caller], imports=[imp])

        pipeline = _setup_pipeline([utils_file, app_file])

        # Named import: trust the import statement
        result = pipeline._find_callee("helper", caller, app_file)
        assert result == "func::src/utils.ts::helper"

    def test_python_wildcard_includes_all(self):
        """Python wildcard import should include all entities (no export keyword)."""
        public_fn = _make_func("helper", "utils.py", is_exported=False)
        private_fn = _make_func("_private", "utils.py", is_exported=False)
        utils_file = _make_file("utils.py", language="python", functions=[public_fn, private_fn])

        imp = _make_import("utils", imported_names=[], is_relative=False)
        imp.is_wildcard = True
        caller = _make_func("main", "app.py")
        app_file = _make_file("app.py", language="python", functions=[caller], imports=[imp])

        pipeline = _setup_pipeline([utils_file, app_file])
        imported = pipeline._get_imported_entities(app_file)

        # Python: all entities should be available
        assert "helper" in imported
        assert "_private" in imported

    def test_dotted_call_prefers_exported_in_ts(self):
        """obj.method on imported module should prefer exported methods."""
        exported_method = _make_func("query", "src/db.ts", is_exported=True)
        internal_method = _make_func("_connect", "src/db.ts", is_exported=False)
        db_file = _make_file("src/db.ts", functions=[exported_method, internal_method])

        imp = _make_import("./db", imported_names=["db"], is_relative=True)
        caller = _make_func("handler", "src/api.ts", calls=["db.query"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline = _setup_pipeline([db_file, api_file])

        # db.query should resolve (exported)
        result = pipeline._find_callee("db.query", caller, api_file)
        assert result == "func::src/db.ts::query"

    def test_dotted_call_falls_back_to_all_entities(self):
        """If no exported entities match, fall back to all entities."""
        # Some codebases don't consistently mark exports
        method = _make_func("query", "src/db.ts", is_exported=False)
        db_file = _make_file("src/db.ts", functions=[method])

        imp = _make_import("./db", imported_names=["db"], is_relative=True)
        caller = _make_func("handler", "src/api.ts", calls=["db.query"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline = _setup_pipeline([db_file, api_file])

        # Should still resolve via fallback
        result = pipeline._find_callee("db.query", caller, api_file)
        assert result == "func::src/db.ts::query"


class TestDependencyUsageEdges:
    """USES_DEPENDENCY edges should link functions to external packages."""

    def _run_dependency_resolution(self, parsed_files: list[ParsedFile]) -> tuple[IngestionPipeline, IngestionResult]:
        """Set up pipeline, run import resolution (which creates deps), return result."""
        pipeline = _setup_pipeline(parsed_files)
        result = IngestionResult(repo_id="test", repo_path="/tmp")
        batch = BatchCollector(pipeline.graph, batch_size=200)
        pipeline._resolve_imports(parsed_files, result, batch)
        batch.flush()
        return pipeline, result

    def test_function_using_external_import_gets_edge(self):
        """A function calling an imported external name creates USES_DEPENDENCY."""
        imp = _make_import("redis", imported_names=["createClient"], is_relative=False)
        caller = _make_func("initCache", "src/cache.ts", calls=["createClient"])
        cache_file = _make_file("src/cache.ts", functions=[caller], imports=[imp])

        pipeline, result = self._run_dependency_resolution([cache_file])

        # Should have created a Dependency node and USES_DEPENDENCY edge
        assert result.dependencies_found == 1
        rels = _extract_batch_merge_rels(pipeline.graph)
        assert ("func::src/cache.ts::initCache", "dep::redis", "USES_DEPENDENCY") in rels

    def test_function_calling_internal_gets_no_dep_edge(self):
        """A function calling an internal function should NOT create USES_DEPENDENCY."""
        helper = _make_func("helper", "src/utils.ts", is_exported=True)
        utils_file = _make_file("src/utils.ts", functions=[helper])

        imp = _make_import("./utils", imported_names=["helper"])
        caller = _make_func("main", "src/app.ts", calls=["helper"])
        app_file = _make_file("src/app.ts", functions=[caller], imports=[imp])

        pipeline, result = self._run_dependency_resolution([utils_file, app_file])

        # No external deps
        assert result.dependencies_found == 0

    def test_dotted_external_call_resolves_to_dep(self):
        """console.log or axios.get should link to the dependency via module basename."""
        imp = _make_import("axios", imported_names=[], is_relative=False)
        caller = _make_func("fetchData", "src/api.ts", calls=["axios.get"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline, result = self._run_dependency_resolution([api_file])

        assert result.dependencies_found == 1
        rels = _extract_batch_merge_rels(pipeline.graph)
        assert ("func::src/api.ts::fetchData", "dep::axios", "USES_DEPENDENCY") in rels

    def test_aliased_external_import_links_to_dep(self):
        """import { Redis as RedisClient } from 'ioredis' -> RedisClient() links to ioredis."""
        imp = _make_import(
            "ioredis",
            imported_names=["Redis"],
            aliases={"Redis": "RedisClient"},
            is_relative=False,
        )
        caller = _make_func("connect", "src/db.ts", calls=["RedisClient"])
        db_file = _make_file("src/db.ts", functions=[caller], imports=[imp])

        pipeline, result = self._run_dependency_resolution([db_file])

        assert result.dependencies_found == 1
        rels = _extract_batch_merge_rels(pipeline.graph)
        assert ("func::src/db.ts::connect", "dep::ioredis", "USES_DEPENDENCY") in rels

    def test_multiple_deps_per_function(self):
        """A function using names from two different packages gets two USES_DEPENDENCY edges."""
        imp1 = _make_import("redis", imported_names=["createClient"], is_relative=False)
        imp2 = _make_import("zod", imported_names=["z"], is_relative=False)
        caller = _make_func("init", "src/app.ts", calls=["createClient", "z.object"])
        app_file = _make_file("src/app.ts", functions=[caller], imports=[imp1, imp2])

        pipeline, result = self._run_dependency_resolution([app_file])

        assert result.dependencies_found == 2
        rels = _extract_batch_merge_rels(pipeline.graph)
        assert ("func::src/app.ts::init", "dep::redis", "USES_DEPENDENCY") in rels
        assert ("func::src/app.ts::init", "dep::zod", "USES_DEPENDENCY") in rels


class TestIncrementalUpdate:
    """update_file should purge old maps and rebuild nodes + edges."""

    def test_purge_maps_removes_file_entities(self):
        """_purge_maps_for_file should remove entities from all maps."""
        func_a = _make_func("query", "src/db.ts", is_exported=True)
        func_b = _make_func("helper", "src/utils.ts")
        file_a = _make_file("src/db.ts", functions=[func_a])
        file_b = _make_file("src/utils.ts", functions=[func_b])

        pipeline = _setup_pipeline([file_a, file_b])

        # Verify maps before purge
        assert "src/db.ts" in pipeline._file_entities
        assert "src/db.ts" in pipeline._exported_file_entities
        assert "func::src/db.ts::query" in pipeline._qualified_map.values()

        # Purge file_a
        pipeline._purge_maps_for_file("src/db.ts")

        # Maps should no longer contain file_a entities
        assert "src/db.ts" not in pipeline._file_entities
        assert "src/db.ts" not in pipeline._exported_file_entities
        # file_b should still be there
        assert "src/utils.ts" in pipeline._file_entities

    def test_purge_maps_removes_from_candidates(self):
        """After purge, short_to_candidates should not include the old node_id."""
        func = _make_func("query", "src/db.ts")
        file_a = _make_file("src/db.ts", functions=[func])

        pipeline = _setup_pipeline([file_a])

        candidates = pipeline._short_to_candidates.get("query", [])
        assert "func::src/db.ts::query" in candidates

        pipeline._purge_maps_for_file("src/db.ts")

        candidates = pipeline._short_to_candidates.get("query", [])
        assert "func::src/db.ts::query" not in candidates

    def test_purge_clears_test_file_tracking(self):
        """Purging a test file removes it from _test_file_paths."""
        func = _make_func("testFoo", "src/__tests__/foo.test.ts")
        test_file = _make_file("src/__tests__/foo.test.ts", functions=[func])
        test_file.is_test_file = True

        pipeline = _setup_pipeline([test_file])
        assert "src/__tests__/foo.test.ts" in pipeline._test_file_paths

        pipeline._purge_maps_for_file("src/__tests__/foo.test.ts")
        assert "src/__tests__/foo.test.ts" not in pipeline._test_file_paths

    def test_purge_clears_import_cache(self):
        """Purging a file invalidates its import cache entry."""
        func = _make_func("handler", "src/api.ts")
        imp = _make_import("./utils", imported_names=["helper"])
        file_a = _make_file("src/api.ts", functions=[func], imports=[imp])

        pipeline = _setup_pipeline([file_a])
        # Simulate a cached entry
        pipeline._import_cache["src/api.ts"] = {"helper": "func::src/utils.ts::helper"}

        pipeline._purge_maps_for_file("src/api.ts")
        assert "src/api.ts" not in pipeline._import_cache


def _setup_pipeline_with_resolution(
    parsed_files: list[ParsedFile],
) -> IngestionPipeline:
    """Build a pipeline with populated maps AND run Phase 2 resolution."""
    pipeline = _setup_pipeline(parsed_files)
    result = MagicMock()
    result.relationships_created = 0
    result.test_coverage_edges = 0
    result.dependencies_found = 0
    result.nodes_created = 0
    pipeline._resolve_calls(parsed_files, result)
    return pipeline


class TestPythonSourceRoots:
    """Test Python source root detection and import resolution."""

    def test_source_root_detection_strips_src_prefix(self):
        """Python files under src/ get module keys with src. prefix stripped."""
        init = _make_file(
            "src/mylib/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[],
        )
        mod = _make_file(
            "src/mylib/core.py",
            language="python",
            functions=[_make_func("do_stuff", "src/mylib/core.py")],
            classes=[],
            imports=[],
        )
        pipeline = _setup_pipeline([init, mod])
        # The full key should exist
        assert "src.mylib.core" in pipeline._pymodule_to_id
        # The stripped key should also exist
        assert "mylib.core" in pipeline._pymodule_to_id
        # Package key stripped
        assert "mylib" in pipeline._pymodule_to_id

    def test_absolute_import_resolves_through_source_root(self):
        """'from mylib.core import do_stuff' resolves when file is at src/mylib/core.py."""
        init = _make_file(
            "src/mylib/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[],
        )
        mod = _make_file(
            "src/mylib/core.py",
            language="python",
            functions=[_make_func("do_stuff", "src/mylib/core.py")],
            classes=[],
            imports=[],
        )
        imp = _make_import("mylib.core", imported_names=["do_stuff"], is_relative=False)
        test_file = _make_file(
            "tests/test_core.py",
            language="python",
            is_test_file=True,
            functions=[_make_func("test_it", "tests/test_core.py", calls=["do_stuff"])],
            classes=[],
            imports=[imp],
        )
        pipeline = _setup_pipeline_with_resolution([init, mod, test_file])
        rels = _extract_batch_merge_rels(pipeline.graph)
        import_rels = [(f, t, r) for f, t, r in rels if r == "IMPORTS"]
        assert any(f == "file::tests/test_core.py" and t == "file::src/mylib/core.py" for f, t, _ in import_rels), (
            f"Expected IMPORTS edge not found. Import rels: {import_rels}"
        )

    def test_relative_import_dot_resolves(self):
        """'from . import fields' in __init__.py resolves to sibling module."""
        init_imp = _make_import(".", imported_names=["fields"], is_relative=True)
        init = _make_file(
            "src/pkg/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[init_imp],
        )
        fields_mod = _make_file(
            "src/pkg/fields.py",
            language="python",
            functions=[_make_func("parse", "src/pkg/fields.py")],
            classes=[],
            imports=[],
        )
        pipeline = _setup_pipeline_with_resolution([init, fields_mod])
        rels = _extract_batch_merge_rels(pipeline.graph)
        import_rels = [(f, t, r) for f, t, r in rels if r == "IMPORTS"]
        assert any(f == "file::src/pkg/__init__.py" and t == "file::src/pkg/fields.py" for f, t, _ in import_rels), (
            f"Expected IMPORTS edge not found. Import rels: {import_rels}"
        )

    def test_relative_import_dotname_resolves(self):
        """'from .schema import Schema' resolves to sibling module."""
        imp = _make_import(".schema", imported_names=["Schema"], is_relative=True)
        mod = _make_file(
            "src/pkg/utils.py",
            language="python",
            functions=[_make_func("helper", "src/pkg/utils.py", calls=["Schema"])],
            classes=[],
            imports=[imp],
        )
        init = _make_file(
            "src/pkg/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[],
        )
        schema_mod = _make_file(
            "src/pkg/schema.py",
            language="python",
            functions=[],
            classes=[_make_class("Schema", "src/pkg/schema.py")],
            imports=[],
        )
        pipeline = _setup_pipeline_with_resolution([init, mod, schema_mod])
        rels = _extract_batch_merge_rels(pipeline.graph)
        import_rels = [(f, t, r) for f, t, r in rels if r == "IMPORTS"]
        assert any(f == "file::src/pkg/utils.py" and t == "file::src/pkg/schema.py" for f, t, _ in import_rels), (
            f"Expected IMPORTS edge not found. Import rels: {import_rels}"
        )

    def test_init_package_registered_as_module(self):
        """__init__.py registers the package name as a module key."""
        init = _make_file(
            "src/mylib/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[],
        )
        pipeline = _setup_pipeline([init])
        # Both full and stripped should be registered
        assert "src.mylib" in pipeline._pymodule_to_id
        assert "mylib" in pipeline._pymodule_to_id

    def test_init_reexport_resolves_named_import(self):
        """'from pkg import Schema' resolves through __init__.py re-export."""
        schema_cls = _make_class("Schema", "src/pkg/schema.py")
        schema_mod = _make_file(
            "src/pkg/schema.py",
            language="python",
            functions=[],
            classes=[schema_cls],
            imports=[],
        )
        # __init__.py re-exports Schema from schema.py
        init_imp = _make_import(
            "pkg.schema",
            imported_names=["Schema"],
            is_relative=False,
        )
        init = _make_file(
            "src/pkg/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[init_imp],
        )
        # Consumer imports Schema from the package
        consumer_imp = _make_import(
            "pkg",
            imported_names=["Schema"],
            is_relative=False,
        )
        consumer = _make_file(
            "tests/test_it.py",
            language="python",
            functions=[_make_func("test", "tests/test_it.py", calls=["Schema"])],
            classes=[],
            imports=[consumer_imp],
        )
        pipeline = _setup_pipeline_with_resolution([schema_mod, init, consumer])
        imported = pipeline._get_imported_entities(consumer)
        assert "Schema" in imported
        assert imported["Schema"] == "class::src/pkg/schema.py::Schema"

    def test_submodule_dotted_call_resolves(self):
        """'fields.Nested' resolves when fields is a submodule imported via __init__.py."""
        nested_cls = _make_class("Nested", "src/pkg/fields.py")
        string_cls = _make_class("String", "src/pkg/fields.py")
        fields_mod = _make_file(
            "src/pkg/fields.py",
            language="python",
            functions=[],
            classes=[nested_cls, string_cls],
            imports=[],
        )
        # __init__.py does 'from . import fields'
        init_imp = _make_import(".", imported_names=["fields"], is_relative=True)
        init = _make_file(
            "src/pkg/__init__.py",
            language="python",
            functions=[],
            classes=[],
            imports=[init_imp],
        )
        # Consumer does 'from pkg import fields' then calls fields.Nested
        consumer_imp = _make_import(
            "pkg",
            imported_names=["fields"],
            is_relative=False,
        )
        caller = _make_func(
            "test",
            "tests/test_it.py",
            calls=["fields.Nested", "fields.String"],
        )
        consumer = _make_file(
            "tests/test_it.py",
            language="python",
            functions=[caller],
            classes=[],
            imports=[consumer_imp],
        )
        pipeline = _setup_pipeline([fields_mod, init, consumer])
        # fields.Nested should resolve to Nested class
        assert pipeline._find_callee("fields.Nested", caller, consumer) == "class::src/pkg/fields.py::Nested"
        assert pipeline._find_callee("fields.String", caller, consumer) == "class::src/pkg/fields.py::String"

    def test_dotted_call_alias_match_with_parens(self):
        """Operator precedence bug: aliased import dotted call should match."""
        helper = _make_func("query", "src/db.ts", is_exported=True)
        db_file = _make_file("src/db.ts", functions=[helper])

        imp = _make_import(
            "./db",
            imported_names=["db"],
            aliases={"db": "database"},
        )
        caller = _make_func("handler", "src/api.ts", calls=["database.query"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline = _setup_pipeline([db_file, api_file])
        result = pipeline._find_callee("database.query", caller, api_file)
        assert result == "func::src/db.ts::query"


class TestInheritanceAwareResolution:
    """self.method() should resolve through base classes when not on the child."""

    def test_self_method_resolves_to_base_class(self):
        """self.dump() in MySchema (extends Schema) resolves to Schema.dump."""
        dump = _make_func(
            "dump",
            "src/schema.py",
            qualified_name="src/schema.py::Schema.dump",
        )
        base_cls = _make_class(
            "Schema",
            "src/schema.py",
            methods=[dump],
        )

        # MySchema extends Schema but doesn't override dump
        caller = _make_func(
            "process",
            "src/schema.py",
            qualified_name="src/schema.py::MySchema.process",
            calls=["MySchema.dump"],  # self.dump() already resolved to MySchema.dump by parser
        )
        child_cls = _make_class(
            "MySchema",
            "src/schema.py",
            methods=[caller],
            bases=["Schema"],
        )

        pf = _make_file("src/schema.py", language="python", classes=[base_cls, child_cls])

        pipeline = _setup_pipeline_with_resolution([pf])
        # MySchema.dump should resolve to Schema.dump via inheritance
        result = pipeline._find_callee("MySchema.dump", caller, pf)
        assert result == "func::src/schema.py::Schema.dump"

    def test_self_method_prefers_own_class(self):
        """If MySchema defines dump(), don't walk to base class."""
        base_dump = _make_func(
            "dump",
            "src/schema.py",
            qualified_name="src/schema.py::Schema.dump",
        )
        base_cls = _make_class(
            "Schema",
            "src/schema.py",
            methods=[base_dump],
        )

        child_dump = _make_func(
            "dump",
            "src/schema.py",
            qualified_name="src/schema.py::MySchema.dump",
        )
        caller = _make_func(
            "process",
            "src/schema.py",
            qualified_name="src/schema.py::MySchema.process",
            calls=["MySchema.dump"],
        )
        child_cls = _make_class(
            "MySchema",
            "src/schema.py",
            methods=[child_dump, caller],
            bases=["Schema"],
        )

        pf = _make_file("src/schema.py", language="python", classes=[base_cls, child_cls])

        pipeline = _setup_pipeline_with_resolution([pf])
        result = pipeline._find_callee("MySchema.dump", caller, pf)
        # Should resolve to MySchema's own dump (file-scoped match wins)
        assert result == "func::src/schema.py::MySchema.dump"

    def test_multi_level_inheritance(self):
        """Grandparent method resolves through multiple levels."""
        base_method = _make_func(
            "validate",
            "src/base.py",
            qualified_name="src/base.py::Base.validate",
        )
        base_cls = _make_class("Base", "src/base.py", methods=[base_method])
        base_file = _make_file("src/base.py", language="python", classes=[base_cls])

        mid_cls = _make_class("Middle", "src/mid.py", bases=["Base"])
        mid_file = _make_file("src/mid.py", language="python", classes=[mid_cls])

        caller = _make_func(
            "run",
            "src/child.py",
            qualified_name="src/child.py::Child.run",
            calls=["Child.validate"],
        )
        child_cls = _make_class(
            "Child",
            "src/child.py",
            methods=[caller],
            bases=["Middle"],
        )
        child_file = _make_file("src/child.py", language="python", classes=[child_cls])

        pipeline = _setup_pipeline_with_resolution([base_file, mid_file, child_file])
        result = pipeline._find_callee("Child.validate", caller, child_file)
        assert result == "func::src/base.py::Base.validate"

    def test_self_call_walks_inheritance(self):
        """self.method -> ClassName.method -> walks to base when not found on self."""
        base_method = _make_func(
            "serialize",
            "src/base.py",
            qualified_name="src/base.py::BaseSerializer.serialize",
        )
        base_cls = _make_class("BaseSerializer", "src/base.py", methods=[base_method])
        base_file = _make_file("src/base.py", language="python", classes=[base_cls])

        # In the parser, self.serialize inside UserSerializer becomes
        # UserSerializer.serialize after self. resolution
        caller = _make_func(
            "to_json",
            "src/user.py",
            qualified_name="src/user.py::UserSerializer.to_json",
            calls=["UserSerializer.serialize"],
        )
        child_cls = _make_class(
            "UserSerializer",
            "src/user.py",
            methods=[caller],
            bases=["BaseSerializer"],
        )
        child_file = _make_file("src/user.py", language="python", classes=[child_cls])

        pipeline = _setup_pipeline_with_resolution([base_file, child_file])
        result = pipeline._find_callee("UserSerializer.serialize", caller, child_file)
        assert result == "func::src/base.py::BaseSerializer.serialize"


class TestFixtureEdges:
    """USES_FIXTURE edges should link test functions to fixtures via parameter names."""

    def _run_fixture_resolution(self, parsed_files: list[ParsedFile]) -> tuple[IngestionPipeline, IngestionResult]:
        """Set up pipeline, run full resolution."""
        pipeline = _setup_pipeline(parsed_files)
        result = IngestionResult(repo_id="test", repo_path="/tmp")
        pipeline._resolve_calls(parsed_files, result)
        return pipeline, result

    def test_test_function_links_to_fixture(self):
        """A test function with a 'client' param links to the 'client' fixture."""
        fixture_fn = _make_func(
            "client",
            "tests/conftest.py",
            is_fixture=True,
        )
        conftest = _make_file(
            "tests/conftest.py",
            language="python",
            functions=[fixture_fn],
            is_test_file=True,
        )

        test_fn = _make_func(
            "test_get",
            "tests/test_api.py",
            is_test=True,
            parameters=["client"],
        )
        test_file = _make_file(
            "tests/test_api.py",
            language="python",
            functions=[test_fn],
            is_test_file=True,
        )

        pipeline, result = self._run_fixture_resolution([conftest, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        assert ("func::tests/test_api.py::test_get", "func::tests/conftest.py::client", "USES_FIXTURE") in rels

    def test_non_fixture_param_ignored(self):
        """Params that don't match fixture names create no edges."""
        test_fn = _make_func(
            "test_basic",
            "tests/test_api.py",
            is_test=True,
            parameters=["x", "y"],
        )
        test_file = _make_file(
            "tests/test_api.py",
            language="python",
            functions=[test_fn],
            is_test_file=True,
        )

        pipeline, result = self._run_fixture_resolution([test_file])

        # No USES_FIXTURE edges
        rels = _extract_batch_merge_rels(pipeline.graph)
        fixture_rels = [(f, t, r) for f, t, r in rels if r == "USES_FIXTURE"]
        assert len(fixture_rels) == 0

    def test_class_method_links_to_fixture(self):
        """A test method inside a class with a fixture param creates an edge."""
        fixture_fn = _make_func(
            "db",
            "tests/conftest.py",
            is_fixture=True,
        )
        conftest = _make_file(
            "tests/conftest.py",
            language="python",
            functions=[fixture_fn],
            is_test_file=True,
        )

        test_method = _make_func(
            "test_insert",
            "tests/test_db.py",
            qualified_name="tests/test_db.py::TestDatabase.test_insert",
            is_test=True,
            parameters=["db"],
        )
        cls = _make_class("TestDatabase", "tests/test_db.py", methods=[test_method])
        test_file = _make_file(
            "tests/test_db.py",
            language="python",
            classes=[cls],
            is_test_file=True,
        )

        pipeline, result = self._run_fixture_resolution([conftest, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        assert (
            "func::tests/test_db.py::TestDatabase.test_insert",
            "func::tests/conftest.py::db",
            "USES_FIXTURE",
        ) in rels


class TestBarrelFileReexportResolution:
    """TS/JS barrel file (index.ts) re-exports should resolve through to the source."""

    def test_named_reexport_resolves_through_barrel(self):
        """import { Button } from './components' resolves through index.ts."""
        button_fn = _make_func("Button", "src/components/Button.ts", is_exported=True)
        button_file = _make_file("src/components/Button.ts", functions=[button_fn])

        # index.ts re-exports Button from ./Button
        reexport_imp = _make_import("./Button", imported_names=["Button"])
        index_file = _make_file("src/components/index.ts", imports=[reexport_imp])

        # Consumer imports from the directory (resolves to index.ts)
        consumer_imp = _make_import("./components", imported_names=["Button"])
        caller = _make_func("App", "src/App.ts", calls=["Button"])
        consumer_file = _make_file(
            "src/App.ts",
            functions=[caller],
            imports=[consumer_imp],
        )

        pipeline = _setup_pipeline([button_file, index_file, consumer_file])
        result = pipeline._find_callee("Button", caller, consumer_file)
        assert result == "func::src/components/Button.ts::Button"

    def test_wildcard_reexport_resolves_through_barrel(self):
        """export * from './spacing' makes spacing's exports available."""
        sm_fn = _make_func("sm", "src/theme/spacing.ts", is_exported=True)
        spacing_file = _make_file("src/theme/spacing.ts", functions=[sm_fn])

        # index.ts wildcard re-exports from ./spacing
        reexport_imp = _make_import(
            "./spacing",
            imported_names=["*"],
            is_wildcard=True,
        )
        index_file = _make_file("src/theme/index.ts", imports=[reexport_imp])

        # Consumer imports sm from the theme directory
        consumer_imp = _make_import("./theme", imported_names=["sm"])
        caller = _make_func("Layout", "src/Layout.ts", calls=["sm"])
        consumer_file = _make_file(
            "src/Layout.ts",
            functions=[caller],
            imports=[consumer_imp],
        )

        pipeline = _setup_pipeline([spacing_file, index_file, consumer_file])
        result = pipeline._find_callee("sm", caller, consumer_file)
        assert result == "func::src/theme/spacing.ts::sm"

    def test_aliased_default_reexport_resolves(self):
        """export { default as ModeCard } from './ModeCard' resolves."""
        # The target file exports a function named "default"
        mode_card = _make_func("default", "src/components/ModeCard.ts", is_exported=True)
        mode_file = _make_file("src/components/ModeCard.ts", functions=[mode_card])

        # index.ts re-exports default as ModeCard
        reexport_imp = _make_import(
            "./ModeCard",
            imported_names=["default"],
            aliases={"default": "ModeCard"},
        )
        index_file = _make_file("src/components/index.ts", imports=[reexport_imp])

        # Consumer imports ModeCard from components
        consumer_imp = _make_import("./components", imported_names=["ModeCard"])
        caller = _make_func("App", "src/App.ts", calls=["ModeCard"])
        consumer_file = _make_file(
            "src/App.ts",
            functions=[caller],
            imports=[consumer_imp],
        )

        pipeline = _setup_pipeline([mode_file, index_file, consumer_file])
        result = pipeline._find_callee("ModeCard", caller, consumer_file)
        assert result == "func::src/components/ModeCard.ts::default"

    def test_hook_through_barrel_resolves(self):
        """Custom hook imported through barrel file should resolve."""
        use_auth = _make_func("useAuth", "src/contexts/AuthContext.ts", is_exported=True)
        auth_file = _make_file("src/contexts/AuthContext.ts", functions=[use_auth])

        reexport_imp = _make_import("./AuthContext", imported_names=["useAuth"])
        index_file = _make_file("src/contexts/index.ts", imports=[reexport_imp])

        consumer_imp = _make_import("../contexts", imported_names=["useAuth"])
        caller = _make_func("Dashboard", "src/pages/Dashboard.tsx", calls=["useAuth"])
        consumer_file = _make_file(
            "src/pages/Dashboard.tsx",
            functions=[caller],
            imports=[consumer_imp],
        )

        pipeline = _setup_pipeline([auth_file, index_file, consumer_file])
        result = pipeline._find_callee("useAuth", caller, consumer_file)
        assert result == "func::src/contexts/AuthContext.ts::useAuth"


class TestMultiLevelBarrelReexports:
    """Barrel → barrel → definition chains should resolve through multiple levels."""

    def test_two_level_named_chain(self):
        """a/index.ts re-exports from b/index.ts which re-exports from b/Button.tsx."""
        button = _make_func("Button", "src/b/Button.tsx", is_exported=True)
        button_file = _make_file("src/b/Button.tsx", functions=[button])

        # b/index.ts re-exports Button from ./Button
        b_imp = _make_import("./Button", imported_names=["Button"])
        b_index = _make_file("src/b/index.ts", imports=[b_imp])

        # a/index.ts re-exports Button from ../b (resolves to b/index.ts)
        a_imp = _make_import("../b", imported_names=["Button"])
        a_index = _make_file("src/a/index.ts", imports=[a_imp])

        # Consumer imports Button from ../a (resolves to a/index.ts)
        consumer_imp = _make_import("../a", imported_names=["Button"])
        caller = _make_func("App", "src/pages/App.tsx", calls=["Button"])
        consumer = _make_file("src/pages/App.tsx", functions=[caller], imports=[consumer_imp])

        pipeline = _setup_pipeline([button_file, b_index, a_index, consumer])
        result = pipeline._find_callee("Button", caller, consumer)
        assert result == "func::src/b/Button.tsx::Button"

    def test_two_level_wildcard_chain(self):
        """export * through two barrel files should resolve."""
        helper = _make_func("formatDate", "src/utils/date.ts", is_exported=True)
        date_file = _make_file("src/utils/date.ts", functions=[helper])

        # utils/index.ts: export * from './date'
        utils_imp = _make_import("./date", imported_names=["*"], is_wildcard=True)
        utils_index = _make_file("src/utils/index.ts", imports=[utils_imp])

        # lib/index.ts: export * from '../utils'
        lib_imp = _make_import("../utils", imported_names=["*"], is_wildcard=True)
        lib_index = _make_file("src/lib/index.ts", imports=[lib_imp])

        # Consumer imports formatDate from ../lib
        consumer_imp = _make_import("../lib", imported_names=["formatDate"])
        caller = _make_func("render", "src/pages/Home.tsx", calls=["formatDate"])
        consumer = _make_file("src/pages/Home.tsx", functions=[caller], imports=[consumer_imp])

        pipeline = _setup_pipeline([date_file, utils_index, lib_index, consumer])
        result = pipeline._find_callee("formatDate", caller, consumer)
        assert result == "func::src/utils/date.ts::formatDate"


class TestTestsFunctionEdges:
    """TESTS_FUNCTION edges should link test functions to production functions they exercise."""

    def _run_full_resolve(self, parsed_files: list[ParsedFile]) -> tuple[IngestionPipeline, IngestionResult]:
        """Set up pipeline maps and run full _resolve_calls (populates adjacency + TESTS_FUNCTION)."""
        pipeline = _setup_pipeline(parsed_files)
        result = IngestionResult(repo_id="test", repo_path="/tmp")
        pipeline._resolve_calls(parsed_files, result)
        return pipeline, result

    def test_direct_call_creates_depth_1_edge(self):
        """test_foo() -> foo() creates TESTS_FUNCTION edge with depth=1."""
        prod_func = _make_func("foo", "src/utils.ts", is_exported=True)
        prod_file = _make_file("src/utils.ts", functions=[prod_func])

        test_func = _make_func(
            "test_foo",
            "tests/test_utils.ts",
            calls=["foo"],
            is_test=True,
        )
        test_file = _make_file(
            "tests/test_utils.ts",
            functions=[test_func],
            imports=[_make_import("../src/utils", imported_names=["foo"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        tf_rels = [(f, t, r) for f, t, r in rels if r == "TESTS_FUNCTION"]
        assert len(tf_rels) == 1
        assert tf_rels[0][0] == "func::tests/test_utils.ts::test_foo"
        assert tf_rels[0][1] == "func::src/utils.ts::foo"

        # Check depth property
        merge_calls = pipeline.graph.batch_merge_relationships.call_args_list
        for call in merge_calls:
            rel_type = call[0][0]
            items = call[0][1]
            if rel_type == "TESTS_FUNCTION":
                for item in items:
                    if item["to_id"] == "func::src/utils.ts::foo":
                        assert item["depth"] == 1

    def test_indirect_call_creates_depth_2_edge(self):
        """test_bar() -> helper() -> target() creates TESTS_FUNCTION with depth=2 for target."""
        target = _make_func("target", "src/core.ts", is_exported=True)
        helper = _make_func("helper", "src/core.ts", calls=["target"], is_exported=True)
        prod_file = _make_file("src/core.ts", functions=[target, helper])

        test_func = _make_func(
            "test_bar",
            "tests/test_core.ts",
            calls=["helper"],
            is_test=True,
        )
        test_file = _make_file(
            "tests/test_core.ts",
            functions=[test_func],
            imports=[_make_import("../src/core", imported_names=["helper"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        tf_rels = [(f, t, r) for f, t, r in rels if r == "TESTS_FUNCTION"]
        # Should have depth 1 for helper, depth 2 for target
        tf_targets = {t: [] for _, t, _ in tf_rels}
        for f, t, _r in tf_rels:
            tf_targets[t].append(f)
        assert "func::src/core.ts::helper" in tf_targets
        assert "func::src/core.ts::target" in tf_targets

        # Verify depths from the raw merge calls
        merge_calls = pipeline.graph.batch_merge_relationships.call_args_list
        depths = {}
        for call in merge_calls:
            rel_type = call[0][0]
            items = call[0][1]
            if rel_type == "TESTS_FUNCTION":
                for item in items:
                    depths[item["to_id"]] = item["depth"]
        assert depths["func::src/core.ts::helper"] == 1
        assert depths["func::src/core.ts::target"] == 2

    def test_direct_preferred_over_indirect(self):
        """If a test calls target directly AND indirectly, depth should be 1."""
        target = _make_func("target", "src/lib.ts", is_exported=True)
        helper = _make_func("helper", "src/lib.ts", calls=["target"], is_exported=True)
        prod_file = _make_file("src/lib.ts", functions=[target, helper])

        test_func = _make_func(
            "test_both",
            "tests/test_lib.ts",
            calls=["target", "helper"],
            is_test=True,
        )
        test_file = _make_file(
            "tests/test_lib.ts",
            functions=[test_func],
            imports=[_make_import("../src/lib", imported_names=["target", "helper"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file])

        # target should appear at depth 1 (direct), not duplicated at depth 2
        merge_calls = pipeline.graph.batch_merge_relationships.call_args_list
        target_depths = []
        for call in merge_calls:
            rel_type = call[0][0]
            items = call[0][1]
            if rel_type == "TESTS_FUNCTION":
                for item in items:
                    if item["to_id"] == "func::src/lib.ts::target":
                        target_depths.append(item["depth"])
        assert target_depths == [1]  # Only one edge, depth 1

    def test_tested_by_count_updated(self):
        """Production functions should get tested_by_count updated via graph query."""
        prod_func = _make_func("process", "src/engine.ts", is_exported=True)
        prod_file = _make_file("src/engine.ts", functions=[prod_func])

        test_a = _make_func("test_a", "tests/test_a.ts", calls=["process"], is_test=True)
        test_b = _make_func("test_b", "tests/test_b.ts", calls=["process"], is_test=True)
        test_file_a = _make_file(
            "tests/test_a.ts",
            functions=[test_a],
            imports=[_make_import("../src/engine", imported_names=["process"])],
            is_test_file=True,
        )
        test_file_b = _make_file(
            "tests/test_b.ts",
            functions=[test_b],
            imports=[_make_import("../src/engine", imported_names=["process"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file_a, test_file_b])

        # Check that graph.execute was called with tested_by_count update
        execute_calls = pipeline.graph.execute.call_args_list
        update_calls = [c for c in execute_calls if "tested_by_count" in str(c)]
        assert len(update_calls) > 0
        # The update should set count=2 for process (two test functions)
        for call in update_calls:
            params = call[0][1] if len(call[0]) > 1 else call[1].get("params", {})
            items = params.get("items", [])
            for item in items:
                if item["id"] == "func::src/engine.ts::process":
                    assert item["count"] == 2

    def test_test_function_not_targeted(self):
        """Test functions should not get TESTS_FUNCTION edges pointing to them."""
        prod_func = _make_func("validate", "src/validate.ts", is_exported=True)
        prod_file = _make_file("src/validate.ts", functions=[prod_func])

        test_helper = _make_func("assert_valid", "tests/helpers.ts", calls=["validate"], is_test=True)
        test_main = _make_func("test_main", "tests/test_val.ts", calls=["assert_valid"], is_test=True)
        helper_file = _make_file(
            "tests/helpers.ts",
            functions=[test_helper],
            imports=[_make_import("../src/validate", imported_names=["validate"])],
            is_test_file=True,
        )
        test_file = _make_file(
            "tests/test_val.ts",
            functions=[test_main],
            imports=[_make_import("./helpers", imported_names=["assert_valid"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, helper_file, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        tf_rels = [(f, t, r) for f, t, r in rels if r == "TESTS_FUNCTION"]
        # No TESTS_FUNCTION edges should point to test functions
        for _, to_id, _ in tf_rels:
            assert to_id not in pipeline._test_func_ids

    def test_no_tests_function_edges_when_no_tests(self):
        """No TESTS_FUNCTION edges when there are no test functions."""
        prod_a = _make_func("a", "src/a.ts", is_exported=True)
        prod_b = _make_func("b", "src/b.ts", calls=["a"], is_exported=True)
        file_a = _make_file("src/a.ts", functions=[prod_a])
        file_b = _make_file(
            "src/b.ts",
            functions=[prod_b],
            imports=[_make_import("./a", imported_names=["a"])],
        )

        pipeline, result = self._run_full_resolve([file_a, file_b])

        rels = _extract_batch_merge_rels(pipeline.graph)
        tf_rels = [(f, t, r) for f, t, r in rels if r == "TESTS_FUNCTION"]
        assert len(tf_rels) == 0

    def test_tested_by_count_default_zero(self):
        """Function nodes should have tested_by_count=0 by default."""
        prod_func = _make_func("untested", "src/utils.ts", is_exported=True)
        prod_file = _make_file("src/utils.ts", functions=[prod_func])

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Function")
        func_nodes = [n for n in nodes if n["name"] == "untested"]
        assert len(func_nodes) == 1
        assert func_nodes[0]["tested_by_count"] == 0


class TestDependencyVersionExtraction:
    """Test that dependency versions are extracted from manifest files."""

    def test_package_json_versions(self, tmp_path):
        import json

        pkg = {
            "dependencies": {"react": "^18.2.0", "next": "14.0.0"},
            "devDependencies": {"typescript": "~5.3.0"},
        }
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        pipeline = IngestionPipeline(_make_graph_mock())
        pipeline._extract_dependency_versions(str(tmp_path))

        assert pipeline._dependency_versions["react"] == "^18.2.0"
        assert pipeline._dependency_versions["next"] == "14.0.0"
        assert pipeline._dependency_versions["typescript"] == "~5.3.0"

    def test_requirements_txt_versions(self, tmp_path):
        (tmp_path / "requirements.txt").write_text("flask==2.3.0\nrequests>=2.28.0\npytest\n# comment\n-r other.txt\n")

        pipeline = IngestionPipeline(_make_graph_mock())
        pipeline._extract_dependency_versions(str(tmp_path))

        assert pipeline._dependency_versions["flask"] == "==2.3.0"
        assert pipeline._dependency_versions["requests"] == ">=2.28.0"
        # pytest has no version spec
        assert "pytest" not in pipeline._dependency_versions

    def test_pyproject_toml_versions(self, tmp_path):
        toml_content = '[project]\ndependencies = [\n  "fastapi>=0.100.0",\n  "uvicorn>=0.23.0",\n  "pydantic",\n]\n'
        (tmp_path / "pyproject.toml").write_text(toml_content)

        pipeline = IngestionPipeline(_make_graph_mock())
        pipeline._extract_dependency_versions(str(tmp_path))

        assert pipeline._dependency_versions["fastapi"] == ">=0.100.0"
        assert pipeline._dependency_versions["uvicorn"] == ">=0.23.0"
        assert "pydantic" not in pipeline._dependency_versions

    def test_no_manifest_files(self, tmp_path):
        pipeline = IngestionPipeline(_make_graph_mock())
        pipeline._extract_dependency_versions(str(tmp_path))
        assert pipeline._dependency_versions == {}

    def test_version_on_dependency_node(self, tmp_path):
        """Verify the version property propagates to Dependency graph nodes."""
        import json

        pkg = {"dependencies": {"lodash": "^4.17.21"}}
        (tmp_path / "package.json").write_text(json.dumps(pkg))

        mock_graph = _make_graph_mock()
        pipeline = IngestionPipeline(mock_graph)
        pipeline._dependency_versions.clear()
        pipeline._extract_dependency_versions(str(tmp_path))

        assert pipeline._dependency_versions["lodash"] == "^4.17.21"


class TestRouteHasAuth:
    """Route nodes should have has_auth computed from middleware and handler decorators."""

    def test_route_no_auth(self):
        """Route with no auth middleware or decorators has has_auth=False."""
        from gristle.models import ParsedRoute

        handler = _make_func("getUsers", "src/routes.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/users",
            handler_name="getUsers",
            file_path="src/routes.ts",
            line=5,
        )
        prod_file = _make_file("src/routes.ts", functions=[handler])
        prod_file.routes = [route]

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is False

    def test_route_with_auth_middleware(self):
        """Route with auth middleware has has_auth=True."""
        from gristle.models import ParsedRoute

        handler = _make_func("getUsers", "src/routes.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/users",
            handler_name="getUsers",
            file_path="src/routes.ts",
            line=5,
            middleware=["authMiddleware"],
        )
        prod_file = _make_file("src/routes.ts", functions=[handler])
        prod_file.routes = [route]

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True

    def test_route_with_auth_decorator_on_handler(self):
        """Route whose handler has auth decorator has has_auth=True."""
        from gristle.models import ParsedRoute

        handler = _make_func("getUsers", "src/routes.ts", is_exported=True)
        handler.decorators = ["login_required"]
        route = ParsedRoute(
            method="GET",
            path="/users",
            handler_name="getUsers",
            file_path="src/routes.ts",
            line=5,
        )
        prod_file = _make_file("src/routes.ts", functions=[handler])
        prod_file.routes = [route]

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True

    def test_route_with_jwt_decorator(self):
        """Route whose handler has jwt_required decorator has has_auth=True."""
        from gristle.models import ParsedRoute

        handler = _make_func("getUsers", "src/routes.ts", is_exported=True)
        handler.decorators = ["jwt_required"]
        route = ParsedRoute(
            method="GET",
            path="/users",
            handler_name="getUsers",
            file_path="src/routes.ts",
            line=5,
        )
        prod_file = _make_file("src/routes.ts", functions=[handler])
        prod_file.routes = [route]

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True

    def test_route_with_protect_middleware(self):
        """Route with 'protect' middleware has has_auth=True."""
        from gristle.models import ParsedRoute

        handler = _make_func("getUsers", "src/routes.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/users",
            handler_name="getUsers",
            file_path="src/routes.ts",
            line=5,
            middleware=["protectRoute"],
        )
        prod_file = _make_file("src/routes.ts", functions=[handler])
        prod_file.routes = [route]

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True

    def test_route_unresolved_handler(self):
        """Route with unresolved handler name has has_auth=False (no decorator info)."""
        from gristle.models import ParsedRoute

        route = ParsedRoute(
            method="GET",
            path="/health",
            handler_name="unknownHandler",
            file_path="src/routes.ts",
            line=5,
        )
        prod_file = _make_file("src/routes.ts")
        prod_file.routes = [route]

        pipeline = _setup_pipeline([prod_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is False


class TestImportResolvedProperty:
    """Import nodes should have a resolved property set during Phase 2."""

    def _run_import_resolve(self, parsed_files: list[ParsedFile]) -> IngestionPipeline:
        """Set up pipeline, run Phase 2 import resolution."""
        pipeline = _setup_pipeline(parsed_files)
        result = IngestionResult(repo_id="test", repo_path="/tmp")
        pipeline._resolve_calls(parsed_files, result)
        return pipeline

    def test_resolved_import_tracked(self):
        """Internal import that resolves to a file sets resolved=True."""
        prod_func = _make_func("query", "src/client.ts", is_exported=True)
        prod_file = _make_file("src/client.ts", functions=[prod_func])

        imp = _make_import("./client", imported_names=["query"])
        caller = _make_func("handler", "src/api.ts", calls=["query"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline = self._run_import_resolve([prod_file, api_file])
        # The import at line 1 in src/api.ts should be tracked as resolved
        imp_id = "import::src/api.ts::1"
        assert imp_id in pipeline._import_resolved
        assert pipeline._import_resolved[imp_id] is True

    def test_unresolved_external_import_tracked(self):
        """External import that doesn't resolve sets resolved=False."""
        imp = _make_import("lodash", imported_names=["debounce"], is_relative=False)
        caller = _make_func("handler", "src/api.ts", calls=["debounce"])
        api_file = _make_file("src/api.ts", functions=[caller], imports=[imp])

        pipeline = self._run_import_resolve([api_file])
        imp_id = "import::src/api.ts::1"
        assert imp_id in pipeline._import_resolved
        assert pipeline._import_resolved[imp_id] is False


class TestImportBasedTestFunctionEdges:
    """Import-based TESTS_FUNCTION edges for JS/TS test files without call graph coverage."""

    def _run_full_resolve(self, parsed_files: list[ParsedFile]) -> tuple[IngestionPipeline, IngestionResult]:
        pipeline = _setup_pipeline(parsed_files)
        result = IngestionResult(repo_id="test", repo_path="/tmp")
        pipeline._resolve_calls(parsed_files, result)
        return pipeline, result

    def test_ts_test_helper_gets_depth_3_edges(self):
        """TS test helper function without call coverage gets import-based depth-3 edges."""
        prod_func = _make_func("validate", "src/validate.ts", is_exported=True)
        prod_file = _make_file("src/validate.ts", functions=[prod_func])

        # Helper function in test file — doesn't call validate directly
        helper = _make_func("createMock", "tests/validate.test.ts", is_test=True)
        test_file = _make_file(
            "tests/validate.test.ts",
            functions=[helper],
            imports=[_make_import("../src/validate", imported_names=["validate"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        tf_rels = [(f, t, r) for f, t, r in rels if r == "TESTS_FUNCTION"]
        assert len(tf_rels) == 1
        assert tf_rels[0][0] == "func::tests/validate.test.ts::createMock"
        assert tf_rels[0][1] == "func::src/validate.ts::validate"

        # Verify depth=3
        merge_calls = pipeline.graph.batch_merge_relationships.call_args_list
        for call in merge_calls:
            if call[0][0] == "TESTS_FUNCTION":
                for item in call[0][1]:
                    if item["to_id"] == "func::src/validate.ts::validate":
                        assert item["depth"] == 3

    def test_python_test_file_no_depth_3(self):
        """Python test files should NOT get import-based depth-3 edges."""
        prod_func = _make_func("validate", "src/validate.py", is_exported=True)
        prod_file = _make_file("src/validate.py", language="python", functions=[prod_func])

        # Helper in python test file without direct calls
        helper = _make_func("make_data", "tests/test_validate.py", is_test=True)
        test_file = _make_file(
            "tests/test_validate.py",
            language="python",
            functions=[helper],
            imports=[_make_import("src.validate", imported_names=["validate"], is_relative=False)],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file])

        rels = _extract_batch_merge_rels(pipeline.graph)
        tf_rels = [(f, t, r) for f, t, r in rels if r == "TESTS_FUNCTION"]
        assert len(tf_rels) == 0

    def test_ts_test_with_call_coverage_no_depth_3(self):
        """TS test function with existing call coverage should NOT get depth-3 fallback."""
        prod_func = _make_func("validate", "src/validate.ts", is_exported=True)
        prod_file = _make_file("src/validate.ts", functions=[prod_func])

        # Test function that DOES call validate directly
        test_func = _make_func(
            "test_validate",
            "tests/validate.test.ts",
            calls=["validate"],
            is_test=True,
        )
        test_file = _make_file(
            "tests/validate.test.ts",
            functions=[test_func],
            imports=[_make_import("../src/validate", imported_names=["validate"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([prod_file, test_file])

        # Should have depth=1 only, no depth=3
        depths = set()
        merge_calls = pipeline.graph.batch_merge_relationships.call_args_list
        for call in merge_calls:
            if call[0][0] == "TESTS_FUNCTION":
                for item in call[0][1]:
                    depths.add(item["depth"])
        assert 1 in depths
        assert 3 not in depths

    def test_ts_test_imports_test_file_no_depth_3(self):
        """TS test helper importing another test file should NOT create depth-3 edges."""
        helper_func = _make_func("createMock", "tests/helpers.ts", is_test=True)
        helper_file = _make_file(
            "tests/helpers.ts",
            functions=[helper_func],
            is_test_file=True,
        )

        test_func = _make_func("testSetup", "tests/api.test.ts", is_test=True)
        test_file = _make_file(
            "tests/api.test.ts",
            functions=[test_func],
            imports=[_make_import("./helpers", imported_names=["createMock"])],
            is_test_file=True,
        )

        pipeline, result = self._run_full_resolve([helper_file, test_file])

        # No depth-3 edges since helper_file is also a test file
        merge_calls = pipeline.graph.batch_merge_relationships.call_args_list
        for call in merge_calls:
            if call[0][0] == "TESTS_FUNCTION":
                for item in call[0][1]:
                    assert item["depth"] != 3


class TestAppLevelAuthMiddleware:
    """Route has_auth should detect app-level middleware from .use() calls."""

    def test_route_under_auth_middleware_path(self):
        """Route under a path guarded by app.use('/api/admin/*', auth) gets has_auth=True."""
        from gristle.models import ParsedRoute

        handler = _make_func("listUsers", "src/admin.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/api/admin/users",
            handler_name="listUsers",
            file_path="src/admin.ts",
            line=10,
        )
        admin_file = _make_file("src/admin.ts", functions=[handler])
        admin_file.routes = [route]
        admin_file.auth_middleware_paths = ["/api/admin/*"]

        pipeline = _setup_pipeline([admin_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True

    def test_route_outside_auth_middleware_path(self):
        """Route NOT under the guarded path gets has_auth=False."""
        from gristle.models import ParsedRoute

        handler = _make_func("healthCheck", "src/routes.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/health",
            handler_name="healthCheck",
            file_path="src/routes.ts",
            line=5,
        )
        routes_file = _make_file("src/routes.ts", functions=[handler])
        routes_file.routes = [route]
        routes_file.auth_middleware_paths = ["/api/admin/*"]

        pipeline = _setup_pipeline([routes_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is False

    def test_same_file_wildcard_applies(self):
        """Same-file '*' wildcard applies to all routes in that file."""
        from gristle.models import ParsedRoute

        handler = _make_func("getUser", "src/admin.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/users",
            handler_name="getUser",
            file_path="src/admin.ts",
            line=10,
        )
        admin_file = _make_file("src/admin.ts", functions=[handler])
        admin_file.routes = [route]
        admin_file.auth_middleware_paths = ["*"]

        pipeline = _setup_pipeline([admin_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True

    def test_cross_file_wildcard_does_not_apply(self):
        """Cross-file '*' wildcard does NOT apply (sub-router scoping)."""
        from gristle.models import ParsedRoute

        # File A has admin.use('*', auth) — wildcard scoped to sub-router
        admin_file = _make_file("src/admin.ts")
        admin_file.auth_middleware_paths = ["*"]

        # File B has a route — should NOT inherit admin's wildcard auth
        handler = _make_func("getPublic", "src/public.ts", is_exported=True)
        route = ParsedRoute(
            method="GET",
            path="/public",
            handler_name="getPublic",
            file_path="src/public.ts",
            line=5,
        )
        public_file = _make_file("src/public.ts", functions=[handler])
        public_file.routes = [route]

        pipeline = _setup_pipeline([admin_file, public_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is False

    def test_cross_file_explicit_path_applies(self):
        """Cross-file explicit path pattern DOES apply."""
        from gristle.models import ParsedRoute

        # File A has app.use('/api/admin/*', auth)
        app_file = _make_file("src/app.ts")
        app_file.auth_middleware_paths = ["/api/admin/*"]

        # File B has a route under /api/admin/
        handler = _make_func("deleteUser", "src/admin-routes.ts", is_exported=True)
        route = ParsedRoute(
            method="DELETE",
            path="/api/admin/users",
            handler_name="deleteUser",
            file_path="src/admin-routes.ts",
            line=5,
        )
        routes_file = _make_file("src/admin-routes.ts", functions=[handler])
        routes_file.routes = [route]

        pipeline = _setup_pipeline([app_file, routes_file])
        nodes = _extract_batch_nodes(pipeline.graph, "Route")
        assert len(nodes) == 1
        assert nodes[0]["has_auth"] is True


class TestUnlinkedRouteHandlerResolution:
    """Route handlers imported from other files should resolve in Phase 2."""

    def test_handler_resolved_via_import(self):
        """Handler imported from shared module should link via HANDLES in Phase 2."""
        from gristle.models import ParsedRoute

        # Shared module with the handler function
        get_model = _make_func("getPhaseModel", "supabase/functions/_shared/modelConfig.ts", is_exported=True)
        shared_file = _make_file(
            "supabase/functions/_shared/modelConfig.ts",
            functions=[get_model],
        )

        # Edge function that imports and uses the handler
        edge_func_file = _make_file(
            "supabase/functions/analyze-intent/index.ts",
            imports=[
                _make_import(
                    "./_shared/modelConfig",
                    imported_names=["getPhaseModel"],
                )
            ],
        )
        edge_func_file.routes = [
            ParsedRoute(
                method="POST",
                path="/analyze-intent",
                handler_name="getPhaseModel",
                file_path="supabase/functions/analyze-intent/index.ts",
                line=10,
            )
        ]

        pipeline = _setup_pipeline_with_resolution([shared_file, edge_func_file])

        # Check HANDLES edge was created via Phase 2 resolution
        rels = _extract_batch_create_rels(pipeline.graph)
        handles_rels = [(f, t) for f, t, r in rels if r == "HANDLES"]
        assert len(handles_rels) == 1
        assert handles_rels[0][1] == "func::supabase/functions/_shared/modelConfig.ts::getPhaseModel"

    def test_handler_in_same_file_resolved_in_phase1(self):
        """Handler defined in the same file should resolve in Phase 1 (no unlinked route)."""
        from gristle.models import ParsedRoute

        handler = _make_func("validatePrompt", "supabase/functions/analyze-domain/index.ts", is_exported=True)
        edge_func_file = _make_file(
            "supabase/functions/analyze-domain/index.ts",
            functions=[handler],
        )
        edge_func_file.routes = [
            ParsedRoute(
                method="POST",
                path="/analyze-domain",
                handler_name="validatePrompt",
                file_path="supabase/functions/analyze-domain/index.ts",
                line=10,
            )
        ]

        pipeline = _setup_pipeline([edge_func_file])

        # Handler should resolve in Phase 1 — not tracked as unlinked
        assert len(pipeline._unlinked_routes) == 0

        # HANDLES edge should exist
        rels = _extract_batch_create_rels(pipeline.graph)
        handles_rels = [(f, t) for f, t, r in rels if r == "HANDLES"]
        assert len(handles_rels) == 1

    def test_serve_placeholder_not_tracked(self):
        """Routes with <serve> handler_name should not be tracked as unlinked."""
        from gristle.models import ParsedRoute

        edge_func_file = _make_file("supabase/functions/cleanup/index.ts")
        edge_func_file.routes = [
            ParsedRoute(
                method="POST",
                path="/cleanup",
                handler_name="<serve>",
                file_path="supabase/functions/cleanup/index.ts",
                line=5,
            )
        ]

        pipeline = _setup_pipeline([edge_func_file])
        assert len(pipeline._unlinked_routes) == 0


class TestDocumentationFiltering:
    """Files in doc/design/stories directories should have is_documentation=True."""

    def test_docs_dir_file_is_documentation(self):
        """File in docs/ should have is_documentation=True on File and Function nodes."""
        func = _make_func("MockButton", "docs/design/MockButton.tsx", is_exported=True)
        pf = _make_file("docs/design/MockButton.tsx", functions=[func])

        pipeline = _setup_pipeline([pf])
        file_nodes = _extract_batch_nodes(pipeline.graph, "File")
        func_nodes = _extract_batch_nodes(pipeline.graph, "Function")

        doc_file = next(n for n in file_nodes if n["path"] == "docs/design/MockButton.tsx")
        assert doc_file["is_documentation"] is True

        doc_func = next(n for n in func_nodes if n["name"] == "MockButton")
        assert doc_func["is_documentation"] is True

    def test_src_dir_file_not_documentation(self):
        """File in src/ should have is_documentation=False."""
        func = _make_func("Button", "src/components/Button.tsx", is_exported=True)
        pf = _make_file("src/components/Button.tsx", functions=[func])

        pipeline = _setup_pipeline([pf])
        file_nodes = _extract_batch_nodes(pipeline.graph, "File")
        func_nodes = _extract_batch_nodes(pipeline.graph, "Function")

        src_file = next(n for n in file_nodes if n["path"] == "src/components/Button.tsx")
        assert src_file["is_documentation"] is False

        src_func = next(n for n in func_nodes if n["name"] == "Button")
        assert src_func["is_documentation"] is False

    def test_storybook_dir_is_documentation(self):
        """File in stories/ directory should be documentation."""
        func = _make_func("ButtonStory", "stories/Button.stories.tsx")
        pf = _make_file("stories/Button.stories.tsx", functions=[func])

        pipeline = _setup_pipeline([pf])
        file_nodes = _extract_batch_nodes(pipeline.graph, "File")
        doc_file = next(n for n in file_nodes if n["path"] == "stories/Button.stories.tsx")
        assert doc_file["is_documentation"] is True

    def test_examples_dir_is_documentation(self):
        """File in examples/ directory should be documentation."""
        func = _make_func("Example", "examples/basic/App.tsx")
        pf = _make_file("examples/basic/App.tsx", functions=[func])

        pipeline = _setup_pipeline([pf])
        file_nodes = _extract_batch_nodes(pipeline.graph, "File")
        doc_file = next(n for n in file_nodes if n["path"] == "examples/basic/App.tsx")
        assert doc_file["is_documentation"] is True

    def test_react_directive_written_to_file_node(self):
        """react_directive from ParsedFile should be written to File node."""
        pf = _make_file("app/page.tsx")
        pf.react_directive = "use client"

        pipeline = _setup_pipeline([pf])
        file_nodes = _extract_batch_nodes(pipeline.graph, "File")
        file_node = next(n for n in file_nodes if n["path"] == "app/page.tsx")
        assert file_node["react_directive"] == "use client"

    def test_no_react_directive_writes_empty_string(self):
        """File without react_directive should have empty string."""
        pf = _make_file("src/utils.ts")

        pipeline = _setup_pipeline([pf])
        file_nodes = _extract_batch_nodes(pipeline.graph, "File")
        file_node = next(n for n in file_nodes if n["path"] == "src/utils.ts")
        assert file_node["react_directive"] == ""


# ==================================================================
# Snapshot capture
# ==================================================================


class TestSnapshotCapture:
    """Test _capture_snapshot and _write_snapshot methods."""

    def test_capture_snapshot_returns_counts(self):
        """_capture_snapshot should query all node types and return counts."""
        from unittest.mock import MagicMock

        from gristle.graph.client import QueryResult
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.parsers.registry import ParserRegistry

        graph = MagicMock()
        registry = ParserRegistry().build_default()
        pipeline = IngestionPipeline(graph, registry)

        # Mock the count queries: File, Function, Class, Route, TestCase, Dependency,
        # component count, edge count
        graph.execute.side_effect = [
            QueryResult(records=[{"c": 10}], summary={}),  # File
            QueryResult(records=[{"c": 30}], summary={}),  # Function
            QueryResult(records=[{"c": 5}], summary={}),  # Class
            QueryResult(records=[{"c": 3}], summary={}),  # Route
            QueryResult(records=[{"c": 8}], summary={}),  # TestCase
            QueryResult(records=[{"c": 15}], summary={}),  # Dependency
            QueryResult(records=[{"c": 6}], summary={}),  # components
            QueryResult(records=[{"c": 100}], summary={}),  # edges
        ]

        snapshot = pipeline._capture_snapshot()
        assert snapshot["file_count"] == 10
        assert snapshot["function_count"] == 30
        assert snapshot["class_count"] == 5
        assert snapshot["route_count"] == 3
        assert snapshot["test_count"] == 8
        assert snapshot["dependency_count"] == 15
        assert snapshot["component_count"] == 6
        assert snapshot["edge_count"] == 100
        assert "snapshot_id" in snapshot
        assert "captured_at" in snapshot

    def test_write_snapshot_creates_node(self):
        """_write_snapshot should execute CREATE and prune queries."""
        from unittest.mock import MagicMock

        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.parsers.registry import ParserRegistry

        graph = MagicMock()
        registry = ParserRegistry().build_default()
        pipeline = IngestionPipeline(graph, registry)

        snapshot = {
            "snapshot_id": "test-id",
            "captured_at": "2024-01-01T00:00:00Z",
            "file_count": 10,
            "function_count": 30,
            "class_count": 5,
            "route_count": 3,
            "test_count": 8,
            "component_count": 6,
            "dependency_count": 15,
            "edge_count": 100,
        }

        pipeline._write_snapshot(snapshot)

        # Should have 2 calls: CREATE and prune
        assert graph.execute.call_count == 2
        # First call is CREATE
        create_query = graph.execute.call_args_list[0][0][0]
        assert "CREATE" in create_query
        assert "Snapshot" in create_query
        # Second call is prune
        prune_query = graph.execute.call_args_list[1][0][0]
        assert "SKIP 20" in prune_query

    def test_capture_snapshot_has_uuid_and_timestamp(self):
        """Snapshot should have valid UUID and ISO timestamp."""
        import uuid
        from unittest.mock import MagicMock

        from gristle.graph.client import QueryResult
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.parsers.registry import ParserRegistry

        graph = MagicMock()
        registry = ParserRegistry().build_default()
        pipeline = IngestionPipeline(graph, registry)

        # All count queries return 0
        graph.execute.return_value = QueryResult(records=[{"c": 0}], summary={})

        snapshot = pipeline._capture_snapshot()

        # Should be a valid UUID
        uuid.UUID(snapshot["snapshot_id"])
        # Should be a valid ISO timestamp
        assert "T" in snapshot["captured_at"]
        assert snapshot["captured_at"].endswith("+00:00") or snapshot["captured_at"].endswith("Z")
