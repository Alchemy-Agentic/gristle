"""Tests for callback/handler detection (PASSED_TO edges)."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.parsers.python import PythonParser
from gristle.parsers.typescript import TypeScriptParser

# ======================================================================
# TypeScript / JavaScript callback detection
# ======================================================================


class TestTSMiddlewareCallbacks:
    def test_app_use(self):
        parser = TypeScriptParser()
        code = "function setup() { app.use(authMiddleware); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("authMiddleware", "middleware") in func.callback_refs

    def test_app_use_multiple(self):
        parser = TypeScriptParser()
        code = "function setup() { app.use(cors, helmet, logger); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("cors", "middleware") in func.callback_refs
        assert ("helmet", "middleware") in func.callback_refs
        assert ("logger", "middleware") in func.callback_refs

    def test_middleware_method(self):
        parser = TypeScriptParser()
        code = "function setup() { server.middleware(validate); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("validate", "middleware") in func.callback_refs


class TestTSRouteHandlerCallbacks:
    def test_router_get(self):
        parser = TypeScriptParser()
        code = "function setup() { router.get('/users', getUsers); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("getUsers", "route_handler") in func.callback_refs

    def test_router_post(self):
        parser = TypeScriptParser()
        code = "function setup() { router.post('/users', createUser); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("createUser", "route_handler") in func.callback_refs

    def test_router_put_delete(self):
        parser = TypeScriptParser()
        code = (
            "function setup() {\n"
            "  router.put('/u/:id', updateUser);\n"
            "  router.delete('/u/:id', deleteUser);\n"
            "}\n"
        )
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("updateUser", "route_handler") in func.callback_refs
        assert ("deleteUser", "route_handler") in func.callback_refs

    def test_skips_string_args(self):
        """String arguments like route paths should not be captured."""
        parser = TypeScriptParser()
        code = "function setup() { router.get('/users', getUsers); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        # Only getUsers should be captured, not '/users'
        assert len(func.callback_refs) == 1
        assert func.callback_refs[0][0] == "getUsers"


class TestTSEventCallbacks:
    def test_on_event(self):
        parser = TypeScriptParser()
        code = "function setup() { emitter.on('error', handleError); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("handleError", "callback") in func.callback_refs

    def test_once_event(self):
        parser = TypeScriptParser()
        code = "function setup() { server.once('close', cleanup); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("cleanup", "callback") in func.callback_refs

    def test_addEventListener(self):
        parser = TypeScriptParser()
        code = "function setup() { btn.addEventListener('click', onClick); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("onClick", "callback") in func.callback_refs


class TestTSPromiseCallbacks:
    def test_then(self):
        parser = TypeScriptParser()
        code = "function run() { fetchData().then(processResult); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("processResult", "callback") in func.callback_refs

    def test_catch(self):
        parser = TypeScriptParser()
        code = "function run() { fetchData().catch(handleError); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("handleError", "callback") in func.callback_refs


class TestTSArrayMethodCallbacks:
    def test_map(self):
        parser = TypeScriptParser()
        code = "function transform() { items.map(processItem); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("processItem", "array_method") in func.callback_refs

    def test_filter(self):
        parser = TypeScriptParser()
        code = "function clean() { items.filter(isValid); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("isValid", "array_method") in func.callback_refs

    def test_forEach(self):
        parser = TypeScriptParser()
        code = "function process() { items.forEach(logItem); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("logItem", "array_method") in func.callback_refs

    def test_reduce(self):
        parser = TypeScriptParser()
        code = "function sum() { items.reduce(accumulate); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("accumulate", "array_method") in func.callback_refs

    def test_sort(self):
        parser = TypeScriptParser()
        code = "function order() { items.sort(compareFn); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("compareFn", "array_method") in func.callback_refs


class TestTSMemberExpressionCallback:
    def test_dotted_callback(self):
        """Member expressions like utils.processItem should be captured."""
        parser = TypeScriptParser()
        code = "function run() { items.map(utils.processItem); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert ("utils.processItem", "array_method") in func.callback_refs


class TestTSNoFalsePositives:
    def test_no_callback_for_unknown_method(self):
        """Unknown methods should not produce callback refs."""
        parser = TypeScriptParser()
        code = "function run() { foo.bar(myVar); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert func.callback_refs == []

    def test_no_callback_for_regular_call(self):
        """Regular function calls should not produce callback refs."""
        parser = TypeScriptParser()
        code = "function run() { console.log(message); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert func.callback_refs == []

    def test_arrow_function_args_not_captured(self):
        """Inline arrow functions should not be captured as callback refs."""
        parser = TypeScriptParser()
        code = "function run() { items.map((x) => x + 1); }\n"
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        assert func.callback_refs == []


class TestTSMethodCallbacks:
    def test_class_method_callbacks(self):
        parser = TypeScriptParser()
        code = (
            "class App {\n"
            "  setup() {\n"
            "    this.router.get('/api', this.handler);\n"
            "  }\n"
            "  handler() {}\n"
            "}\n"
        )
        result = parser.parse_file("test.ts", code)
        setup = next(m for m in result.classes[0].methods if m.name == "setup")
        # this.handler is resolved to just "handler" by the TS member expression resolver
        assert ("handler", "route_handler") in setup.callback_refs


class TestTSDeduplication:
    def test_duplicate_refs_deduplicated(self):
        parser = TypeScriptParser()
        code = (
            "function run() {\n"
            "  items.map(process);\n"
            "  items.map(process);\n"
            "}\n"
        )
        result = parser.parse_file("test.ts", code)
        func = result.functions[0]
        process_refs = [r for r in func.callback_refs if r[0] == "process"]
        assert len(process_refs) == 1


# ======================================================================
# Python callback detection
# ======================================================================


class TestPyBuiltinHOF:
    def test_map(self):
        parser = PythonParser()
        code = "def run():\n    result = map(process, items)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("process", "argument") in func.callback_refs

    def test_filter(self):
        parser = PythonParser()
        code = "def run():\n    result = filter(is_valid, items)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("is_valid", "argument") in func.callback_refs

    def test_sorted_key(self):
        parser = PythonParser()
        code = "def run():\n    result = sorted(items, key=get_name)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("get_name", "argument") in func.callback_refs

    def test_map_only_first_arg(self):
        """For map(fn, iterable), only fn should be captured, not iterable."""
        parser = PythonParser()
        code = "def run():\n    result = map(transform, data_list)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert len(func.callback_refs) == 1
        assert func.callback_refs[0][0] == "transform"


class TestPyEventCallbacks:
    def test_signal_connect(self):
        parser = PythonParser()
        code = "def setup():\n    post_save.connect(on_save)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("on_save", "callback") in func.callback_refs

    def test_on_event(self):
        parser = PythonParser()
        code = "def setup():\n    emitter.on('error', handle_error)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("handle_error", "callback") in func.callback_refs


class TestPyMiddlewareCallbacks:
    def test_add_middleware(self):
        parser = PythonParser()
        code = "def setup():\n    app.add_middleware(cors_middleware)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("cors_middleware", "middleware") in func.callback_refs


class TestPyRouteCallbacks:
    def test_add_route(self):
        parser = PythonParser()
        code = "def setup():\n    app.add_route('/users', get_users)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("get_users", "route_handler") in func.callback_refs

    def test_add_api_route(self):
        parser = PythonParser()
        code = "def setup():\n    app.add_api_route('/users', get_users)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("get_users", "route_handler") in func.callback_refs


class TestPyKeywordArgs:
    def test_sorted_key_kwarg(self):
        parser = PythonParser()
        code = "def setup():\n    sorted(items, key=get_key)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("get_key", "argument") in func.callback_refs

    def test_handler_kwarg(self):
        parser = PythonParser()
        code = "def run():\n    emitter.on('click', handler=process_item)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert ("process_item", "callback") in func.callback_refs


class TestPySelfResolution:
    def test_self_callback_resolved(self):
        parser = PythonParser()
        code = (
            "class App:\n"
            "    def setup(self):\n"
            "        self.router.add_route('/api', self.handler)\n"
            "    def handler(self):\n"
            "        pass\n"
        )
        result = parser.parse_file("test.py", code)
        setup = next(m for m in result.classes[0].methods if m.name == "setup")
        # self.handler -> App.handler
        assert ("App.handler", "route_handler") in setup.callback_refs


class TestPyNoFalsePositives:
    def test_no_callback_for_unknown_method(self):
        parser = PythonParser()
        code = "def run():\n    foo.bar(my_var)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert func.callback_refs == []

    def test_no_callback_for_regular_call(self):
        parser = PythonParser()
        code = "def run():\n    print(message)\n"
        result = parser.parse_file("test.py", code)
        func = result.functions[0]
        assert func.callback_refs == []


# ======================================================================
# Pipeline integration — PASSED_TO edge creation
# ======================================================================


class TestPipelinePassedTo:
    def test_passed_to_edges_created(self):
        """PASSED_TO edges should be created for resolved callback refs."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedFile, ParsedFunction

        mock_graph = MagicMock()
        pipeline = IngestionPipeline(mock_graph)

        caller = ParsedFunction(
            name="setup",
            qualified_name="app.ts::setup",
            file_path="app.ts",
            start_line=1,
            end_line=5,
            signature="function setup()",
            calls=["app.use"],
            callback_refs=[("authMiddleware", "middleware")],
        )
        pf = ParsedFile(path="app.ts", language="typescript", functions=[caller])

        # Register the callee in the qualified map
        pipeline._qualified_map["app.ts::authMiddleware"] = "func::app.ts::authMiddleware"
        pipeline._file_entities["app.ts"] = {"authMiddleware": "func::app.ts::authMiddleware"}

        batch = BatchCollector(mock_graph, batch_size=500)
        pipeline._resolve_function_calls(caller, pf, batch)

        # Check that PASSED_TO was buffered
        passed_to_items = batch._merge_rels.get("PASSED_TO", [])
        assert len(passed_to_items) == 1
        assert passed_to_items[0]["from_id"] == "func::app.ts::setup"
        assert passed_to_items[0]["to_id"] == "func::app.ts::authMiddleware"
        assert passed_to_items[0]["context"] == "middleware"

    def test_unresolved_callback_skipped(self):
        """Callback refs that can't be resolved should not create edges."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedFile, ParsedFunction

        mock_graph = MagicMock()
        pipeline = IngestionPipeline(mock_graph)

        caller = ParsedFunction(
            name="setup",
            qualified_name="app.ts::setup",
            file_path="app.ts",
            start_line=1,
            end_line=5,
            signature="function setup()",
            calls=[],
            callback_refs=[("unknownFn", "middleware")],
        )
        pf = ParsedFile(path="app.ts", language="typescript", functions=[caller])

        batch = BatchCollector(mock_graph, batch_size=500)
        pipeline._resolve_function_calls(caller, pf, batch)

        passed_to_items = batch._merge_rels.get("PASSED_TO", [])
        assert len(passed_to_items) == 0

    def test_multiple_contexts(self):
        """Different contexts should be preserved on edges."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedFile, ParsedFunction

        mock_graph = MagicMock()
        pipeline = IngestionPipeline(mock_graph)

        caller = ParsedFunction(
            name="setup",
            qualified_name="app.ts::setup",
            file_path="app.ts",
            start_line=1,
            end_line=10,
            signature="function setup()",
            calls=[],
            callback_refs=[
                ("authMiddleware", "middleware"),
                ("getUsers", "route_handler"),
                ("processItem", "array_method"),
            ],
        )
        pf = ParsedFile(path="app.ts", language="typescript", functions=[caller])

        pipeline._qualified_map["app.ts::authMiddleware"] = "func::app.ts::authMiddleware"
        pipeline._qualified_map["app.ts::getUsers"] = "func::app.ts::getUsers"
        pipeline._qualified_map["app.ts::processItem"] = "func::app.ts::processItem"
        pipeline._file_entities["app.ts"] = {
            "authMiddleware": "func::app.ts::authMiddleware",
            "getUsers": "func::app.ts::getUsers",
            "processItem": "func::app.ts::processItem",
        }

        batch = BatchCollector(mock_graph, batch_size=500)
        pipeline._resolve_function_calls(caller, pf, batch)

        passed_to_items = batch._merge_rels.get("PASSED_TO", [])
        assert len(passed_to_items) == 3
        contexts = {item["context"] for item in passed_to_items}
        assert contexts == {"middleware", "route_handler", "array_method"}


# ======================================================================
# JavaScript parser (same as TS but via JS parser)
# ======================================================================


# ======================================================================
# JSX prop callback detection
# ======================================================================


class TestJSXCallbackDetection:
    def test_onclick_handler(self):
        parser = TypeScriptParser()
        code = """
function App() {
  return <Button onClick={handleClick} />;
}
"""
        result = parser.parse_file("App.tsx", code)
        func = result.functions[0]
        assert ("handleClick", "jsx_callback") in func.callback_refs

    def test_onchange_handler(self):
        parser = TypeScriptParser()
        code = """
function Form() {
  return <input onChange={validateField} />;
}
"""
        result = parser.parse_file("Form.tsx", code)
        func = result.functions[0]
        assert ("validateField", "jsx_callback") in func.callback_refs

    def test_onsubmit_handler(self):
        parser = TypeScriptParser()
        code = """
function Form() {
  return <form onSubmit={handleSubmit}><button /></form>;
}
"""
        result = parser.parse_file("Form.tsx", code)
        func = result.functions[0]
        assert ("handleSubmit", "jsx_callback") in func.callback_refs

    def test_multiple_jsx_callbacks(self):
        parser = TypeScriptParser()
        code = """
function App() {
  return (
    <div>
      <Button onClick={handleClick} onHover={handleHover} />
      <Input onChange={handleChange} />
    </div>
  );
}
"""
        result = parser.parse_file("App.tsx", code)
        func = result.functions[0]
        refs = func.callback_refs
        assert ("handleClick", "jsx_callback") in refs
        assert ("handleHover", "jsx_callback") in refs
        assert ("handleChange", "jsx_callback") in refs

    def test_member_expression_jsx_callback(self):
        parser = TypeScriptParser()
        code = """
function App() {
  return <Button onClick={handlers.submit} />;
}
"""
        result = parser.parse_file("App.tsx", code)
        func = result.functions[0]
        assert ("handlers.submit", "jsx_callback") in func.callback_refs

    def test_non_event_jsx_props_ignored(self):
        """Non-on* attributes like className, ref, key should not be callbacks."""
        parser = TypeScriptParser()
        code = """
function App() {
  return <div className={styles.container} ref={myRef} key={id} />;
}
"""
        result = parser.parse_file("App.tsx", code)
        func = result.functions[0]
        jsx_refs = [(n, c) for n, c in func.callback_refs if c == "jsx_callback"]
        assert len(jsx_refs) == 0

    def test_inline_arrow_not_captured(self):
        """Inline arrow functions like onClick={() => foo()} should not create PASSED_TO."""
        parser = TypeScriptParser()
        code = """
function App() {
  return <Button onClick={() => doSomething()} />;
}
"""
        result = parser.parse_file("App.tsx", code)
        func = result.functions[0]
        jsx_refs = [(n, c) for n, c in func.callback_refs if c == "jsx_callback"]
        assert len(jsx_refs) == 0

    def test_jsx_callback_in_js_file(self):
        from gristle.parsers.typescript import JavaScriptParser

        parser = JavaScriptParser()
        code = """
function App() {
  return <Button onClick={handleClick} />;
}
"""
        result = parser.parse_file("App.jsx", code)
        func = result.functions[0]
        assert ("handleClick", "jsx_callback") in func.callback_refs


# ======================================================================
# Pipeline — is_callback marking
# ======================================================================


class TestPipelineIsCallback:
    def test_callback_targets_tracked(self):
        """PASSED_TO targets should be added to _callback_target_ids."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedFile, ParsedFunction

        mock_graph = MagicMock()
        pipeline = IngestionPipeline(mock_graph)

        caller = ParsedFunction(
            name="App",
            qualified_name="App.tsx::App",
            file_path="App.tsx",
            start_line=1,
            end_line=5,
            signature="function App()",
            calls=[],
            callback_refs=[("handleClick", "jsx_callback")],
        )
        pf = ParsedFile(path="App.tsx", language="typescript", functions=[caller])

        pipeline._qualified_map["App.tsx::handleClick"] = "func::App.tsx::handleClick"
        pipeline._file_entities["App.tsx"] = {"handleClick": "func::App.tsx::handleClick"}

        batch = BatchCollector(mock_graph, batch_size=500)
        pipeline._resolve_function_calls(caller, pf, batch)

        assert "func::App.tsx::handleClick" in pipeline._callback_target_ids

    def test_unresolved_callback_not_tracked(self):
        """Unresolved callback refs should not be tracked."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedFile, ParsedFunction

        mock_graph = MagicMock()
        pipeline = IngestionPipeline(mock_graph)

        caller = ParsedFunction(
            name="App",
            qualified_name="App.tsx::App",
            file_path="App.tsx",
            start_line=1,
            end_line=5,
            signature="function App()",
            calls=[],
            callback_refs=[("unknownFn", "jsx_callback")],
        )
        pf = ParsedFile(path="App.tsx", language="typescript", functions=[caller])

        batch = BatchCollector(mock_graph, batch_size=500)
        pipeline._resolve_function_calls(caller, pf, batch)

        assert len(pipeline._callback_target_ids) == 0


# ======================================================================
# JavaScript parser (same as TS but via JS parser)
# ======================================================================


class TestJSCallbackDetection:
    def test_js_parser_detects_callbacks(self):
        from gristle.parsers.typescript import JavaScriptParser

        parser = JavaScriptParser()
        code = "function setup() { app.use(auth); items.map(transform); }\n"
        result = parser.parse_file("test.js", code)
        func = result.functions[0]
        assert ("auth", "middleware") in func.callback_refs
        assert ("transform", "array_method") in func.callback_refs
