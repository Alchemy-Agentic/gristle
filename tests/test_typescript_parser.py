"""Tests for the TypeScript/JavaScript parser."""

from gristle.parsers.typescript import JavaScriptParser, TypeScriptParser


class TestImportExtraction:
    def test_extracts_named_import(self):
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", "import { foo, bar } from './utils';\n")
        assert len(result.imports) == 1
        assert result.imports[0].module_path == "./utils"
        assert "foo" in result.imports[0].imported_names
        assert "bar" in result.imports[0].imported_names
        assert result.imports[0].is_relative is True

    def test_extracts_default_import(self):
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", "import React from 'react';\n")
        assert len(result.imports) == 1
        assert result.imports[0].module_path == "react"
        assert "React" in result.imports[0].imported_names
        assert result.imports[0].is_relative is False

    def test_extracts_namespace_import(self):
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", "import * as path from 'path';\n")
        assert len(result.imports) == 1
        assert result.imports[0].module_path == "path"
        assert "*" in result.imports[0].imported_names
        assert result.imports[0].aliases.get("*") == "path"

    def test_extracts_aliased_import(self):
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", "import { foo as bar } from './utils';\n")
        assert len(result.imports) == 1
        assert "foo" in result.imports[0].imported_names
        assert result.imports[0].aliases.get("foo") == "bar"

    def test_extracts_relative_import(self):
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", "import { helper } from '../lib/helpers';\n")
        assert result.imports[0].is_relative is True
        assert result.imports[0].module_path == "../lib/helpers"

    def test_extracts_multiple_imports(self):
        parser = TypeScriptParser()
        code = (
            "import React from 'react';\nimport { useState, useEffect } from 'react';\nimport { api } from './api';\n"
        )
        result = parser.parse_file("test.ts", code)
        assert len(result.imports) == 3


class TestClassExtraction:
    def test_extracts_class(self):
        parser = TypeScriptParser()
        code = "class UserService {\n  getUser(id: string) { return id; }\n}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.classes) == 1
        assert result.classes[0].name == "UserService"
        assert result.classes[0].kind == "class"

    def test_extracts_class_bases(self):
        parser = TypeScriptParser()
        code = "class Dog extends Animal {\n  bark() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].bases == ["Animal"]

    def test_extracts_class_methods(self):
        parser = TypeScriptParser()
        code = (
            "class Calc {\n"
            "  add(a: number, b: number): number { return a + b; }\n"
            "  subtract(a: number, b: number): number { return a - b; }\n"
            "}\n"
        )
        result = parser.parse_file("test.ts", code)
        methods = result.classes[0].methods
        assert len(methods) == 2
        assert methods[0].name == "add"
        assert methods[1].name == "subtract"

    def test_extracts_async_method(self):
        parser = TypeScriptParser()
        code = "class Svc {\n  async fetch() { return await get(); }\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].methods[0].is_async is True

    def test_extracts_static_method(self):
        parser = TypeScriptParser()
        code = "class Svc {\n  static create() { return new Svc(); }\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].methods[0].is_static is True

    def test_extracts_exported_class(self):
        parser = TypeScriptParser()
        code = "export class MyClass {\n  run() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].is_exported is True

    def test_extracts_abstract_class(self):
        parser = TypeScriptParser()
        code = "abstract class Base {\n  abstract run(): void;\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].is_abstract is True

    def test_extracts_jsdoc_on_class(self):
        parser = TypeScriptParser()
        code = "/** Service for handling users. */\nclass UserService {}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].docstring is not None
        assert "users" in result.classes[0].docstring.lower()


class TestInterfaceExtraction:
    def test_extracts_interface(self):
        parser = TypeScriptParser()
        code = "interface User {\n  name: string;\n  age: number;\n}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.classes) == 1
        assert result.classes[0].name == "User"
        assert result.classes[0].kind == "interface"

    def test_extracts_exported_interface(self):
        parser = TypeScriptParser()
        code = "export interface Config {\n  host: string;\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].is_exported is True
        assert result.classes[0].kind == "interface"

    def test_extracts_type_alias(self):
        parser = TypeScriptParser()
        code = "type ID = string | number;\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.classes) == 1
        assert result.classes[0].name == "ID"
        assert result.classes[0].kind == "type"

    def test_extracts_enum(self):
        parser = TypeScriptParser()
        code = "enum Color {\n  Red,\n  Green,\n  Blue,\n}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.classes) == 1
        assert result.classes[0].name == "Color"
        assert result.classes[0].kind == "enum"

    def test_interface_with_extends(self):
        parser = TypeScriptParser()
        code = "interface Admin extends User {\n  role: string;\n}\n"
        result = parser.parse_file("test.ts", code)
        assert "User" in result.classes[0].bases


class TestFunctionExtraction:
    def test_extracts_function_declaration(self):
        parser = TypeScriptParser()
        code = "function greet(name: string): string {\n  return `Hello ${name}`;\n}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.functions) == 1
        assert result.functions[0].name == "greet"
        assert result.functions[0].return_type == "string"

    def test_extracts_arrow_function(self):
        parser = TypeScriptParser()
        code = "const add = (a: number, b: number): number => a + b;\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.functions) == 1
        assert result.functions[0].name == "add"

    def test_extracts_async_function(self):
        parser = TypeScriptParser()
        code = "async function fetchData(): Promise<Data> {\n  return await api.get();\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].is_async is True

    def test_extracts_async_arrow_function(self):
        parser = TypeScriptParser()
        code = "const fetchData = async (): Promise<Data> => {\n  return await api.get();\n};\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].is_async is True

    def test_extracts_exported_function(self):
        parser = TypeScriptParser()
        code = "export function helper() { return 1; }\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].is_exported is True

    def test_extracts_exported_arrow_function(self):
        parser = TypeScriptParser()
        code = "export const helper = () => 1;\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].is_exported is True

    def test_does_not_include_methods_as_functions(self):
        parser = TypeScriptParser()
        code = "function standalone() {}\nclass Foo {\n  method() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        func_names = [f.name for f in result.functions]
        assert "standalone" in func_names
        assert "method" not in func_names

    def test_extracts_jsdoc_on_function(self):
        parser = TypeScriptParser()
        code = "/** Adds two numbers. */\nfunction add(a: number, b: number) { return a + b; }\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].docstring is not None
        assert "two numbers" in result.functions[0].docstring

    def test_extracts_const_function_expression(self):
        parser = TypeScriptParser()
        code = "const handler = function(req: Request) { return req; };\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.functions) == 1
        assert result.functions[0].name == "handler"

    def test_ignores_non_function_const(self):
        parser = TypeScriptParser()
        code = "const MAX_SIZE = 100;\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.functions) == 0


class TestCallExtraction:
    def test_extracts_simple_calls(self):
        parser = TypeScriptParser()
        code = "function foo() { bar(); baz(); }\n"
        result = parser.parse_file("test.ts", code)
        assert "bar" in result.functions[0].calls
        assert "baz" in result.functions[0].calls

    def test_extracts_method_calls(self):
        parser = TypeScriptParser()
        code = "function foo() { console.log('hi'); api.fetch(); }\n"
        result = parser.parse_file("test.ts", code)
        assert "console.log" in result.functions[0].calls
        assert "api.fetch" in result.functions[0].calls

    def test_extracts_jsx_component_calls(self):
        parser = TypeScriptParser()
        code = "function App() {\n  return <div><Header /><Footer /></div>;\n}\n"
        result = parser.parse_file("test.tsx", code)
        assert "Header" in result.functions[0].calls
        assert "Footer" in result.functions[0].calls

    def test_deduplicates_calls(self):
        parser = TypeScriptParser()
        code = "function foo() { bar(); bar(); bar(); }\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].calls.count("bar") == 1

    def test_extracts_chained_calls(self):
        parser = TypeScriptParser()
        code = "function foo() { arr.filter(x => x > 0).map(x => x * 2); }\n"
        result = parser.parse_file("test.ts", code)
        # Should capture arr.filter at minimum
        calls = result.functions[0].calls
        assert any("filter" in c for c in calls)


class TestComponentDetection:
    def test_detects_function_component(self):
        parser = TypeScriptParser()
        code = "function Button({ label }: Props) {\n  return <button>{label}</button>;\n}\n"
        result = parser.parse_file("test.tsx", code)
        assert result.functions[0].is_component is True

    def test_detects_arrow_component(self):
        parser = TypeScriptParser()
        code = "const Card = ({ title }: Props) => {\n  return <div className='card'>{title}</div>;\n};\n"
        result = parser.parse_file("test.tsx", code)
        assert result.functions[0].is_component is True

    def test_lowercase_not_component(self):
        parser = TypeScriptParser()
        code = "function helper() {\n  return <div>test</div>;\n}\n"
        result = parser.parse_file("test.tsx", code)
        # Lowercase functions are not treated as components
        assert result.functions[0].is_component is False

    def test_no_jsx_not_component(self):
        parser = TypeScriptParser()
        code = "function UserService() {\n  return { data: [] };\n}\n"
        # Even .tsx, no JSX in return -> not a component
        result = parser.parse_file("test.tsx", code)
        assert result.functions[0].is_component is False

    def test_ts_file_not_component(self):
        parser = TypeScriptParser()
        code = "function Button() {\n  return 42;\n}\n"
        result = parser.parse_file("test.ts", code)
        # .ts files don't get component detection
        assert result.functions[0].is_component is False


class TestTestDetection:
    def test_detects_test_file(self):
        parser = TypeScriptParser()
        code = "function setup() { return 1; }\n"
        result = parser.parse_file("src/__tests__/utils.test.ts", code)
        assert result.is_test_file is True

    def test_detects_spec_file(self):
        parser = TypeScriptParser()
        code = "function setup() {}\n"
        result = parser.parse_file("components/Button.spec.tsx", code)
        assert result.is_test_file is True

    def test_non_test_file(self):
        parser = TypeScriptParser()
        result = parser.parse_file("src/utils.ts", "function foo() {}\n")
        assert result.is_test_file is False

    def test_functions_in_test_file_marked_as_test(self):
        parser = TypeScriptParser()
        code = "function setup() {}\nfunction cleanup() {}\n"
        result = parser.parse_file("src/foo.test.ts", code)
        for func in result.functions:
            assert func.is_test is True

    def test_test_function_names_detected(self):
        parser = TypeScriptParser()
        # describe/it/test/beforeAll etc. are test functions by name
        code = "function describe() {}\nfunction it() {}\nfunction test() {}\nfunction beforeEach() {}\n"
        # Even in a non-test file, these function names are recognized
        result = parser.parse_file("src/helper.ts", code)
        for func in result.functions:
            assert func.is_test is True, f"{func.name} should be detected as test"


class TestEntryPointDetection:
    def test_nextjs_page_component(self):
        parser = TypeScriptParser()
        code = "export default function HomePage() {\n  return <div>Home</div>;\n}\n"
        result = parser.parse_file("app/page.tsx", code)
        func = next(f for f in result.functions if f.is_entry_point)
        assert func.entry_point_reason == "nextjs_page"

    def test_nextjs_route_handler(self):
        parser = TypeScriptParser()
        code = "export async function GET(request: Request) {\n  return Response.json({});\n}\n"
        result = parser.parse_file("app/api/users/route.ts", code)
        get_func = result.functions[0]
        assert get_func.is_entry_point is True
        assert get_func.entry_point_reason == "nextjs_page"

    def test_exported_main_is_entry_point(self):
        parser = TypeScriptParser()
        code = "export function main() {\n  console.log('start');\n}\n"
        result = parser.parse_file("src/index.ts", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "main"

    def test_non_exported_main_not_entry_point(self):
        parser = TypeScriptParser()
        code = "function main() {\n  console.log('start');\n}\n"
        result = parser.parse_file("src/index.ts", code)
        assert result.functions[0].is_entry_point is False
        assert result.functions[0].entry_point_reason is None

    def test_regular_function_not_entry_point(self):
        parser = TypeScriptParser()
        code = "export function helper() { return 1; }\n"
        result = parser.parse_file("src/utils.ts", code)
        assert result.functions[0].is_entry_point is False
        assert result.functions[0].entry_point_reason is None

    def test_react_component_is_entry_point(self):
        parser = TypeScriptParser()
        code = "function Button({ label }: Props) {\n  return <button>{label}</button>;\n}\n"
        result = parser.parse_file("components/Button.tsx", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "react_component"

    def test_storybook_story_is_entry_point(self):
        parser = TypeScriptParser()
        code = "export default { title: 'Button' };\nexport const Primary = () => <Button />;\n"
        # Note: the arrow function is extracted as a function
        result = parser.parse_file("components/Button.stories.tsx", code)
        exported = [f for f in result.functions if f.is_exported]
        for func in exported:
            assert func.is_entry_point is True
            assert func.entry_point_reason == "storybook_story"

    def test_serverless_handler_is_entry_point(self):
        parser = TypeScriptParser()
        code = "export const handler = async (event: APIGatewayEvent) => {\n  return { statusCode: 200 };\n};\n"
        result = parser.parse_file("src/lambda.ts", code)
        assert result.functions[0].is_entry_point is True
        assert result.functions[0].entry_point_reason == "serverless_handler"

    def test_serverless_handler_not_exported_not_entry(self):
        parser = TypeScriptParser()
        code = "const handler = async (event: APIGatewayEvent) => {\n  return { statusCode: 200 };\n};\n"
        result = parser.parse_file("src/lambda.ts", code)
        assert result.functions[0].entry_point_reason != "serverless_handler"

    def test_exported_hook_in_barrel_is_entry_point(self):
        parser = TypeScriptParser()
        code = "export function useAuth() { return {}; }\nexport function useTheme() { return {}; }\n"
        result = parser.parse_file("hooks/index.ts", code)
        for func in result.functions:
            assert func.is_entry_point is True
            assert func.entry_point_reason == "react_hook"

    def test_exported_hook_in_non_barrel_not_entry_point(self):
        """use* exports in non-barrel files should NOT be marked as entry points."""
        parser = TypeScriptParser()
        code = "export function useAuth() { return {}; }\n"
        result = parser.parse_file("hooks/useAuth.ts", code)
        assert result.functions[0].entry_point_reason != "react_hook"

    def test_non_hook_in_barrel_not_entry_point(self):
        parser = TypeScriptParser()
        code = "export function formatDate() { return ''; }\n"
        result = parser.parse_file("utils/index.ts", code)
        assert result.functions[0].entry_point_reason != "react_hook"


class TestModuleDocstring:
    """Test module-level description extraction from leading comments."""

    def test_jsdoc_module_description(self):
        parser = TypeScriptParser()
        code = "/** Utility functions for date formatting. */\nimport { format } from 'date-fns';\n"
        result = parser.parse_file("utils/dates.ts", code)
        assert result.module_docstring is not None
        assert "date formatting" in result.module_docstring

    def test_fileoverview_tag(self):
        parser = TypeScriptParser()
        code = "/** @fileoverview API client for the users service. */\nconst BASE_URL = '/api';\n"
        result = parser.parse_file("api/users.ts", code)
        assert result.module_docstring == "API client for the users service."

    def test_module_tag(self):
        parser = TypeScriptParser()
        code = "/** @module UserService */\nexport class UserService {}\n"
        result = parser.parse_file("services/user.ts", code)
        assert result.module_docstring == "UserService"

    def test_single_line_comment(self):
        parser = TypeScriptParser()
        code = "// Helper utilities for string manipulation\nexport function capitalize(s: string) { return s; }\n"
        result = parser.parse_file("utils/strings.ts", code)
        assert result.module_docstring is not None
        assert "string manipulation" in result.module_docstring

    def test_skips_license_header(self):
        parser = TypeScriptParser()
        code = (
            "/** Copyright 2024 Acme Corp. MIT License. */\n/** Data validation helpers. */\nfunction validate() {}\n"
        )
        result = parser.parse_file("utils/validate.ts", code)
        assert result.module_docstring is not None
        assert "validation" in result.module_docstring.lower()

    def test_no_comment_returns_none(self):
        parser = TypeScriptParser()
        code = "export function foo() { return 1; }\n"
        result = parser.parse_file("utils.ts", code)
        assert result.module_docstring is None

    def test_truncates_to_200_chars(self):
        parser = TypeScriptParser()
        long_desc = "A" * 300
        code = f"/** {long_desc} */\nfunction foo() {{}}\n"
        result = parser.parse_file("utils.ts", code)
        assert result.module_docstring is not None
        assert len(result.module_docstring) <= 200

    def test_js_parser_extracts_module_docstring(self):
        parser = JavaScriptParser()
        code = "/** Core business logic module. */\nfunction process() {}\n"
        result = parser.parse_file("core.js", code)
        assert result.module_docstring is not None
        assert "business logic" in result.module_docstring


class TestPythonModuleDocstring:
    """Test that Python module docstrings are extracted (already works, verify)."""

    def test_python_module_docstring(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        code = '"""Utility functions for data processing."""\n\ndef process():\n    pass\n'
        result = parser.parse_file("utils.py", code)
        assert result.module_docstring == "Utility functions for data processing."


class TestTodoExtraction:
    def test_extracts_todo_comment(self):
        parser = TypeScriptParser()
        code = "// TODO: Fix this later\nfunction foo() {}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.todos) == 1
        assert "TODO" in result.todos[0]
        assert "Fix this later" in result.todos[0]

    def test_extracts_fixme_comment(self):
        parser = TypeScriptParser()
        code = "// FIXME: broken edge case\nfunction foo() {}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.todos) == 1
        assert "FIXME" in result.todos[0]

    def test_extracts_hack_comment(self):
        parser = TypeScriptParser()
        code = "function foo() {\n  // HACK: temporary workaround\n  return 1;\n}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.todos) == 1
        assert "HACK" in result.todos[0]

    def test_extracts_multiple_todos(self):
        parser = TypeScriptParser()
        code = "// TODO: first thing\nfunction foo() {\n  // FIXME: second thing\n  // TODO: third thing\n}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.todos) == 3

    def test_extracts_todo_from_block_comment(self):
        parser = TypeScriptParser()
        code = "/* TODO: migrate this */\nfunction foo() {}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.todos) == 1

    def test_no_todos_when_none_present(self):
        parser = TypeScriptParser()
        code = "// A regular comment\nfunction foo() {}\n"
        result = parser.parse_file("test.ts", code)
        assert len(result.todos) == 0


class TestRouteExtraction:
    def test_express_get_route(self):
        parser = TypeScriptParser()
        code = "const router = express.Router();\nrouter.get('/users', getUsers);\n"
        result = parser.parse_file("routes.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].method == "GET"
        assert result.routes[0].path == "/users"
        assert result.routes[0].handler_name == "getUsers"

    def test_express_post_route(self):
        parser = TypeScriptParser()
        code = "app.post('/users', createUser);\n"
        result = parser.parse_file("routes.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].method == "POST"
        assert result.routes[0].path == "/users"

    def test_multiple_routes(self):
        parser = TypeScriptParser()
        code = (
            "router.get('/users', listUsers);\n"
            "router.post('/users', createUser);\n"
            "router.delete('/users/:id', deleteUser);\n"
        )
        result = parser.parse_file("routes.ts", code)
        assert len(result.routes) == 3
        methods = {r.method for r in result.routes}
        assert methods == {"GET", "POST", "DELETE"}

    def test_ignores_non_router_method_calls(self):
        parser = TypeScriptParser()
        code = "const data = c.req.query('offset');\nmap.get('key');\n"
        result = parser.parse_file("handler.ts", code)
        assert len(result.routes) == 0

    def test_route_path_must_start_with_slash(self):
        parser = TypeScriptParser()
        code = "app.get('key', handler);\n"
        result = parser.parse_file("routes.ts", code)
        assert len(result.routes) == 0

    def test_nextjs_page_route(self):
        parser = TypeScriptParser()
        code = "export default function AboutPage() {\n  return <div>About</div>;\n}\n"
        result = parser.parse_file("app/about/page.tsx", code)
        assert len(result.routes) == 1
        assert result.routes[0].method == "GET"
        assert result.routes[0].path == "/about"

    def test_nextjs_api_route(self):
        parser = TypeScriptParser()
        code = (
            "export async function GET() { return Response.json({}); }\n"
            "export async function POST() { return Response.json({}); }\n"
        )
        result = parser.parse_file("app/api/users/route.ts", code)
        assert len(result.routes) == 2
        methods = {r.method for r in result.routes}
        assert methods == {"GET", "POST"}

    def test_nextjs_dynamic_route(self):
        parser = TypeScriptParser()
        code = "export default function UserPage() {\n  return <div>User</div>;\n}\n"
        result = parser.parse_file("app/users/[id]/page.tsx", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/users/:id"

    def test_hono_routes(self):
        parser = TypeScriptParser()
        code = "const app = new Hono();\napp.get('/health', (c) => c.json({ ok: true }));\n"
        result = parser.parse_file("server.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].method == "GET"
        assert result.routes[0].path == "/health"

    def test_hono_custom_variable_name(self):
        """Hono apps often use domain-specific names like 'chat', 'health'."""
        parser = TypeScriptParser()
        code = (
            "import { Hono } from 'hono';\n"
            "const health = new Hono<AppEnv>();\n"
            "health.get('/', (c) => c.json({ status: 'ok' }));\n"
            "health.get('/health', async (c) => {\n"
            "  return c.json({ status: 'ok' });\n"
            "});\n"
            "health.post('/check', (c) => c.json({ ok: true }));\n"
        )
        result = parser.parse_file("src/routes/health.ts", code)
        assert len(result.routes) == 3
        methods = [r.method for r in result.routes]
        assert methods == ["GET", "GET", "POST"]
        assert result.routes[0].path == "/"
        assert result.routes[1].path == "/health"

    def test_hono_multiple_routers_in_file(self):
        parser = TypeScriptParser()
        code = (
            "const chat = new Hono();\n"
            "const admin = new Hono();\n"
            "chat.post('/message', sendMessage);\n"
            "admin.get('/stats', getStats);\n"
        )
        result = parser.parse_file("routes.ts", code)
        assert len(result.routes) == 2


class TestComplexity:
    def test_simple_function(self):
        parser = TypeScriptParser()
        code = "function foo() { return 1; }\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].complexity == 1

    def test_branching_increases_complexity(self):
        parser = TypeScriptParser()
        code = "function foo(x: number) {\n  if (x > 0) {\n    return x;\n  } else {\n    return -x;\n  }\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].complexity >= 2

    def test_ternary_increases_complexity(self):
        parser = TypeScriptParser()
        code = "function foo(x: number) { return x > 0 ? x : -x; }\n"
        result = parser.parse_file("test.ts", code)
        assert result.functions[0].complexity >= 2


class TestVisibility:
    def test_public_method(self):
        parser = TypeScriptParser()
        code = "class Foo {\n  public bar() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].methods[0].visibility == "public"

    def test_private_method(self):
        parser = TypeScriptParser()
        code = "class Foo {\n  private bar() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].methods[0].visibility == "private"

    def test_protected_method(self):
        parser = TypeScriptParser()
        code = "class Foo {\n  protected bar() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].methods[0].visibility == "protected"

    def test_default_visibility_is_public(self):
        parser = TypeScriptParser()
        code = "class Foo {\n  bar() {}\n}\n"
        result = parser.parse_file("test.ts", code)
        assert result.classes[0].methods[0].visibility == "public"


class TestQualifiedNames:
    def test_function_qualified_name(self):
        parser = TypeScriptParser()
        code = "function greet() {}\n"
        result = parser.parse_file("src/utils.ts", code)
        assert result.functions[0].qualified_name == "src/utils.ts::greet"

    def test_class_qualified_name(self):
        parser = TypeScriptParser()
        code = "class Service {}\n"
        result = parser.parse_file("src/service.ts", code)
        assert result.classes[0].qualified_name == "src/service.ts::Service"

    def test_method_qualified_name(self):
        parser = TypeScriptParser()
        code = "class Service {\n  run() {}\n}\n"
        result = parser.parse_file("src/service.ts", code)
        assert result.classes[0].methods[0].qualified_name == "src/service.ts::Service.run"


class TestJavaScriptParser:
    def test_parses_js_file(self):
        parser = JavaScriptParser()
        code = "function hello() { return 'world'; }\n"
        result = parser.parse_file("test.js", code)
        assert result.language == "javascript"
        assert len(result.functions) == 1
        assert result.functions[0].name == "hello"

    def test_parses_jsx_component(self):
        parser = JavaScriptParser()
        code = "function Button() {\n  return <button>Click</button>;\n}\n"
        result = parser.parse_file("test.jsx", code)
        assert result.functions[0].is_component is True

    def test_parses_require_style_captured_as_dynamic_import(self):
        # require() is captured as a dynamic import
        parser = JavaScriptParser()
        code = "const fs = require('fs');\n"
        result = parser.parse_file("test.js", code)
        assert len(result.imports) == 1
        assert result.imports[0].module_path == "fs"
        assert result.imports[0].is_wildcard is True

    def test_parses_es_module_import(self):
        parser = JavaScriptParser()
        code = "import { readFile } from 'fs';\n"
        result = parser.parse_file("test.mjs", code)
        assert len(result.imports) == 1
        assert result.imports[0].module_path == "fs"


class TestTestCaseExtraction:
    def test_extracts_describe_blocks(self):
        parser = TypeScriptParser()
        code = (
            "import { describe, it } from 'vitest';\n"
            "describe('Math utils', () => {\n"
            "  it('adds numbers', () => {\n"
            "    expect(1 + 1).toBe(2);\n"
            "  });\n"
            "});\n"
        )
        result = parser.parse_file("math.test.ts", code)
        assert len(result.test_cases) == 2
        describe = [tc for tc in result.test_cases if tc.block_type == "describe"]
        its = [tc for tc in result.test_cases if tc.block_type == "it"]
        assert len(describe) == 1
        assert describe[0].name == "Math utils"
        assert len(its) == 1
        assert its[0].name == "adds numbers"
        assert its[0].parent_describe == "Math utils"

    def test_extracts_test_blocks(self):
        parser = TypeScriptParser()
        code = "import { test } from 'vitest';\ntest('returns true', () => {\n  expect(true).toBe(true);\n});\n"
        result = parser.parse_file("simple.test.ts", code)
        assert len(result.test_cases) == 1
        assert result.test_cases[0].block_type == "test"
        assert result.test_cases[0].name == "returns true"
        assert result.test_cases[0].parent_describe is None

    def test_nested_describe_blocks(self):
        parser = TypeScriptParser()
        code = "describe('Outer', () => {\n  describe('Inner', () => {\n    it('works', () => {});\n  });\n});\n"
        result = parser.parse_file("nested.test.ts", code)
        names = {tc.name for tc in result.test_cases}
        assert "Outer" in names
        assert "Inner" in names
        assert "works" in names
        # Inner's parent should be Outer
        inner = [tc for tc in result.test_cases if tc.name == "Inner"][0]
        assert inner.parent_describe == "Outer"
        # "works" parent should be Inner
        works = [tc for tc in result.test_cases if tc.name == "works"][0]
        assert works.parent_describe == "Inner"

    def test_no_test_cases_in_non_test_file(self):
        parser = TypeScriptParser()
        code = "function foo() { return 1; }\n"
        result = parser.parse_file("src/utils.ts", code)
        assert len(result.test_cases) == 0

    def test_multiple_it_blocks(self):
        parser = TypeScriptParser()
        code = (
            "describe('API', () => {\n"
            "  it('GET returns 200', () => {});\n"
            "  it('POST creates resource', () => {});\n"
            "  it('DELETE removes resource', () => {});\n"
            "});\n"
        )
        result = parser.parse_file("api.test.ts", code)
        its = [tc for tc in result.test_cases if tc.block_type == "it"]
        assert len(its) == 3
        assert all(tc.parent_describe == "API" for tc in its)

    def test_tracks_line_numbers(self):
        parser = TypeScriptParser()
        code = "describe('Suite', () => {\n  it('case 1', () => {\n    expect(1).toBe(1);\n  });\n});\n"
        result = parser.parse_file("suite.test.ts", code)
        it_case = [tc for tc in result.test_cases if tc.block_type == "it"][0]
        assert it_case.start_line == 2
        assert it_case.end_line == 4


class TestLineCount:
    def test_counts_lines(self):
        parser = TypeScriptParser()
        code = "line1\nline2\nline3\n"
        result = parser.parse_file("test.ts", code)
        assert result.line_count == 4  # 3 lines + trailing newline counts as 4

    def test_single_line(self):
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", "const x = 1;")
        assert result.line_count == 1


class TestTestFileDetection:
    """Test that the test file regex correctly identifies test vs non-test files."""

    def test_dot_test_file_is_test(self):
        parser = TypeScriptParser()
        result = parser.parse_file("src/utils.test.ts", "export function foo() {}\n")
        assert result.is_test_file is True

    def test_dot_spec_file_is_test(self):
        parser = TypeScriptParser()
        result = parser.parse_file("src/utils.spec.tsx", "export function foo() {}\n")
        assert result.is_test_file is True

    def test_tests_directory_is_test(self):
        parser = TypeScriptParser()
        result = parser.parse_file("tests/integration/api.ts", "export function foo() {}\n")
        assert result.is_test_file is True

    def test_dunder_tests_is_test(self):
        parser = TypeScriptParser()
        result = parser.parse_file("__tests__/helper.ts", "export function foo() {}\n")
        assert result.is_test_file is True

    def test_production_test_runner_not_test(self):
        """supabase/functions/test-runner/index.ts is NOT a test file."""
        parser = TypeScriptParser()
        result = parser.parse_file(
            "supabase/functions/test-runner/index.ts",
            "export function handler() {}\n",
        )
        assert result.is_test_file is False

    def test_production_test_analytics_not_test(self):
        """Files with 'test' in a directory name (not at filename boundary) are not tests."""
        parser = TypeScriptParser()
        result = parser.parse_file(
            "supabase/functions/test-analytics/index.ts",
            "export function handler() {}\n",
        )
        assert result.is_test_file is False

    def test_embedded_test_in_path_not_test(self):
        """app-analyze-test-results has 'test' mid-word in a directory name."""
        parser = TypeScriptParser()
        result = parser.parse_file(
            "supabase/functions/app-analyze-test-results/index.ts",
            "export function handler() {}\n",
        )
        assert result.is_test_file is False

    def test_v1_specs_directory_not_test(self):
        """v1-specs/ is not the same as specs/ at a directory root."""
        parser = TypeScriptParser()
        result = parser.parse_file(
            "docs/archive/v1-specs/mockup.jsx",
            "export function Mockup() {}\n",
        )
        assert result.is_test_file is False

    def test_setup_test_script_not_test(self):
        """scripts/setup-test-user.ts is a utility, not a test file."""
        parser = TypeScriptParser()
        result = parser.parse_file(
            "scripts/setup-test-user.ts",
            "export function setup() {}\n",
        )
        assert result.is_test_file is False


class TestServeEntryPointDetection:
    """Test that serve() / Deno.serve() handlers are detected as entry points."""

    def test_serve_marks_called_functions_as_entry_points(self):
        parser = TypeScriptParser()
        code = (
            'import { serve } from "https://deno.land/std/http/server.ts";\n'
            "\n"
            "function handleRequest(req: Request): Response {\n"
            "  return new Response('ok');\n"
            "}\n"
            "\n"
            "serve(async (req) => {\n"
            "  return handleRequest(req);\n"
            "});\n"
        )
        result = parser.parse_file("supabase/functions/my-func/index.ts", code)
        handle = next(f for f in result.functions if f.name == "handleRequest")
        assert handle.is_entry_point is True

    def test_deno_serve_marks_called_functions(self):
        parser = TypeScriptParser()
        code = (
            "function handler(req: Request): Response {\n"
            "  return new Response('ok');\n"
            "}\n"
            "\n"
            "Deno.serve(async (req) => {\n"
            "  return handler(req);\n"
            "});\n"
        )
        result = parser.parse_file("supabase/functions/other/index.ts", code)
        handler = next(f for f in result.functions if f.name == "handler")
        assert handler.is_entry_point is True

    def test_no_serve_no_entry_points(self):
        parser = TypeScriptParser()
        code = "function helper() { return 1; }\n"
        result = parser.parse_file("src/utils.ts", code)
        assert all(not f.is_entry_point for f in result.functions)

    def test_serve_multiple_callees(self):
        parser = TypeScriptParser()
        code = (
            'import { serve } from "https://deno.land/std/http/server.ts";\n'
            "\n"
            "function validate(req: Request) { return true; }\n"
            "function process(req: Request) { return {}; }\n"
            "function respond(data: any) { return new Response('ok'); }\n"
            "\n"
            "serve(async (req) => {\n"
            "  validate(req);\n"
            "  const data = process(req);\n"
            "  return respond(data);\n"
            "});\n"
        )
        result = parser.parse_file("supabase/functions/multi/index.ts", code)
        entry_names = {f.name for f in result.functions if f.is_entry_point}
        assert "validate" in entry_names
        assert "process" in entry_names
        assert "respond" in entry_names


class TestSupabaseRouteExtraction:
    """Supabase edge functions should produce POST routes from directory name."""

    def test_serve_in_supabase_path_creates_post_route(self):
        parser = TypeScriptParser()
        code = (
            'import { serve } from "https://deno.land/std/http/server.ts";\n'
            "serve(async (req) => {\n"
            '  return new Response("ok");\n'
            "});\n"
        )
        result = parser.parse_file("supabase/functions/analyze-gaps/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].method == "POST"
        assert result.routes[0].path == "/analyze-gaps"

    def test_deno_serve_creates_route(self):
        parser = TypeScriptParser()
        code = "Deno.serve(async (req) => {\n  return handleRequest(req);\n});\n"
        result = parser.parse_file("supabase/functions/my-func/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/my-func"
        assert result.routes[0].handler_name == "handleRequest"

    def test_non_supabase_path_no_route(self):
        parser = TypeScriptParser()
        code = 'serve(async (req) => { return new Response("ok"); });\n'
        result = parser.parse_file("src/server.ts", code)
        assert len(result.routes) == 0

    def test_supabase_path_without_serve_no_route(self):
        parser = TypeScriptParser()
        code = 'export function handler() { return "ok"; }\n'
        result = parser.parse_file("supabase/functions/my-func/index.ts", code)
        assert len(result.routes) == 0

    def test_named_handler_detected(self):
        parser = TypeScriptParser()
        code = (
            'function processRequest(req: Request) { return new Response("ok"); }\n'
            "serve(async (req) => {\n"
            "  return processRequest(req);\n"
            "});\n"
        )
        result = parser.parse_file("supabase/functions/process-data/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].handler_name == "processRequest"
        assert result.routes[0].path == "/process-data"


class TestReexportExtraction:
    """Barrel file re-export statements should be captured as imports."""

    def test_named_reexport(self):
        parser = TypeScriptParser()
        code = "export { Button } from './Button';\n"
        result = parser.parse_file("components/index.ts", code)
        reexports = [i for i in result.imports if i.module_path == "./Button"]
        assert len(reexports) == 1
        assert "Button" in reexports[0].imported_names
        assert reexports[0].is_relative is True
        assert reexports[0].is_wildcard is False

    def test_wildcard_reexport(self):
        parser = TypeScriptParser()
        code = "export * from './spacing';\n"
        result = parser.parse_file("theme/index.ts", code)
        reexports = [i for i in result.imports if i.module_path == "./spacing"]
        assert len(reexports) == 1
        assert reexports[0].is_wildcard is True
        assert "*" in reexports[0].imported_names

    def test_aliased_reexport(self):
        parser = TypeScriptParser()
        code = "export { default as ModeCard } from './ModeComparisonCard';\n"
        result = parser.parse_file("components/index.ts", code)
        reexports = [i for i in result.imports if i.module_path == "./ModeComparisonCard"]
        assert len(reexports) == 1
        assert "default" in reexports[0].imported_names
        assert reexports[0].aliases.get("default") == "ModeCard"

    def test_multiple_reexports_in_barrel(self):
        parser = TypeScriptParser()
        code = "export { Button } from './Button';\nexport { Input } from './Input';\nexport * from './spacing';\n"
        result = parser.parse_file("components/index.ts", code)
        reexport_paths = {i.module_path for i in result.imports}
        assert "./Button" in reexport_paths
        assert "./Input" in reexport_paths
        assert "./spacing" in reexport_paths

    def test_type_reexport(self):
        parser = TypeScriptParser()
        code = "export type { ModelOption } from './ModelSelector';\n"
        result = parser.parse_file("types/index.ts", code)
        reexports = [i for i in result.imports if i.module_path == "./ModelSelector"]
        assert len(reexports) == 1
        assert "ModelOption" in reexports[0].imported_names

    def test_multi_name_reexport(self):
        parser = TypeScriptParser()
        code = "export { Foo, Bar as Baz } from './module';\n"
        result = parser.parse_file("lib/index.ts", code)
        reexports = [i for i in result.imports if i.module_path == "./module"]
        assert len(reexports) == 1
        assert "Foo" in reexports[0].imported_names
        assert "Bar" in reexports[0].imported_names
        assert reexports[0].aliases.get("Bar") == "Baz"

    def test_reexports_coexist_with_regular_imports(self):
        parser = TypeScriptParser()
        code = "import { helper } from './helper';\nexport { Button } from './Button';\n"
        result = parser.parse_file("components/index.ts", code)
        assert len(result.imports) == 2
        paths = {i.module_path for i in result.imports}
        assert "./helper" in paths
        assert "./Button" in paths

    def test_js_parser_extracts_reexports(self):
        parser = JavaScriptParser()
        code = "export { Button } from './Button';\nexport * from './utils';\n"
        result = parser.parse_file("components/index.js", code)
        assert len(result.imports) == 2


class TestDynamicImportExtraction:
    """Test extraction of dynamic import() and require() calls."""

    def test_import_call_captured(self):
        parser = TypeScriptParser()
        code = "const mod = import('./utils');\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 1
        assert dynamic[0].module_path == "./utils"
        assert dynamic[0].is_relative is True

    def test_require_call_captured(self):
        parser = TypeScriptParser()
        code = "const fs = require('fs');\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 1
        assert dynamic[0].module_path == "fs"
        assert dynamic[0].is_relative is False

    def test_require_relative_captured(self):
        parser = TypeScriptParser()
        code = "const lib = require('./lib');\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 1
        assert dynamic[0].module_path == "./lib"
        assert dynamic[0].is_relative is True

    def test_template_literal_skipped(self):
        parser = TypeScriptParser()
        code = "const mod = import(`./mod/${name}`);\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 0

    def test_variable_arg_skipped(self):
        parser = TypeScriptParser()
        code = "const mod = import(path);\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 0

    def test_multiple_dynamic_imports(self):
        parser = TypeScriptParser()
        code = "const a = import('./a');\nconst b = require('./b');\nconst c = import('./c');\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 3
        paths = [i.module_path for i in dynamic]
        assert paths == ["./a", "./b", "./c"]

    def test_nested_in_function(self):
        parser = TypeScriptParser()
        code = "async function loadModule() {\n  const mod = await import('./lazy');\n  return mod;\n}\n"
        result = parser.parse_file("test.ts", code)
        dynamic = [i for i in result.imports if i.is_wildcard]
        assert len(dynamic) == 1
        assert dynamic[0].module_path == "./lazy"


class TestAuthMiddlewarePaths:
    """Parser should extract auth middleware path patterns from .use() calls."""

    def test_hono_app_use_with_auth(self):
        parser = TypeScriptParser()
        code = """
const app = new Hono();
app.use('/api/admin/*', clerkAuth);
app.get('/api/admin/users', listUsers);
"""
        result = parser.parse_file("src/app.ts", code)
        assert "/api/admin/*" in result.auth_middleware_paths

    def test_express_router_use_with_auth(self):
        parser = TypeScriptParser()
        code = """
const router = express.Router();
router.use('/protected/*', verifyToken);
router.get('/protected/data', getData);
"""
        result = parser.parse_file("src/routes.ts", code)
        assert "/protected/*" in result.auth_middleware_paths

    def test_wildcard_auth_middleware(self):
        parser = TypeScriptParser()
        code = """
const admin = new Hono();
admin.use('*', adminAuth);
admin.get('/users', listUsers);
"""
        result = parser.parse_file("src/admin.ts", code)
        assert "*" in result.auth_middleware_paths

    def test_use_without_auth_not_included(self):
        parser = TypeScriptParser()
        code = """
const app = new Hono();
app.use('*', logger);
app.use('/api/*', cors);
"""
        result = parser.parse_file("src/app.ts", code)
        assert result.auth_middleware_paths == []

    def test_multiple_auth_middleware_paths(self):
        parser = TypeScriptParser()
        code = """
const app = new Hono();
app.use('/api/admin/*', clerkAuth);
app.use('/api/billing/*', sessionGuard);
app.use('*', logger);
"""
        result = parser.parse_file("src/app.ts", code)
        assert "/api/admin/*" in result.auth_middleware_paths
        assert "/api/billing/*" in result.auth_middleware_paths
        assert len(result.auth_middleware_paths) == 2


class TestReactDirective:
    """Test 'use client' / 'use server' directive detection."""

    def test_use_client_detected(self):
        parser = TypeScriptParser()
        code = "'use client';\n\nimport React from 'react';\n\nexport function Button() {\n  return <button>Click</button>;\n}\n"
        result = parser.parse_file("app/Button.tsx", code)
        assert result.react_directive == "use client"

    def test_use_server_detected(self):
        parser = TypeScriptParser()
        code = '"use server";\n\nexport async function createUser() {\n  // server action\n}\n'
        result = parser.parse_file("app/actions.ts", code)
        assert result.react_directive == "use server"

    def test_no_directive_returns_none(self):
        parser = TypeScriptParser()
        code = "import React from 'react';\n\nexport function App() {\n  return <div />;\n}\n"
        result = parser.parse_file("app/App.tsx", code)
        assert result.react_directive is None

    def test_directive_after_import_still_detected(self):
        parser = TypeScriptParser()
        code = "import React from 'react';\n'use client';\n\nexport function App() {\n  return <div />;\n}\n"
        result = parser.parse_file("app/App.tsx", code)
        assert result.react_directive == "use client"

    def test_directive_after_code_returns_none(self):
        parser = TypeScriptParser()
        code = "const x = 1;\n'use client';\n\nexport function App() {\n  return <div />;\n}\n"
        result = parser.parse_file("app/App.tsx", code)
        assert result.react_directive is None

    def test_directive_in_js_file(self):
        from gristle.parsers.typescript import JavaScriptParser

        parser = JavaScriptParser()
        code = "'use client';\n\nexport function Button() {\n  return <button>Click</button>;\n}\n"
        result = parser.parse_file("app/Button.jsx", code)
        assert result.react_directive == "use client"


_NEST_CONTROLLER = (
    "@Controller('users')\n"
    "export class UsersController {\n"
    "  @Get(':id')\n"
    "  findOne(id: string) { return id; }\n"
    "  @Post()\n"
    "  create() { return 1; }\n"
    "}\n"
)


class TestDecorators:
    def test_class_and_method_decorators_extracted(self):
        result = TypeScriptParser().parse_file("users.controller.ts", _NEST_CONTROLLER)
        cls = result.classes[0]
        assert cls.decorators == ["Controller('users')"]
        assert cls.methods[0].decorators == ["Get(':id')"]
        assert cls.methods[1].decorators == ["Post()"]

    def test_undecorated_class_has_no_decorators(self):
        result = TypeScriptParser().parse_file("svc.ts", "export class Plain {\n  do() { return 1; }\n}\n")
        assert result.classes[0].decorators == []


class TestNestJSRoutes:
    def test_controller_methods_become_routes(self):
        routes = {
            (r.method, r.path, r.handler_name) for r in TypeScriptParser().parse_file("u.ts", _NEST_CONTROLLER).routes
        }
        assert ("GET", "/users/:id", "findOne") in routes
        assert ("POST", "/users", "create") in routes

    def test_non_controller_class_yields_no_routes(self):
        result = TypeScriptParser().parse_file("svc.ts", "export class PlainService {\n  doThing() { return 1; }\n}\n")
        assert result.routes == []
