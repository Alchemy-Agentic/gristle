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

    def test_captures_positional_arg_identifiers(self):
        """calls_with_args records the table passed as an argument (Drizzle
        `db.insert(chat)`) so the schema linker can see it."""
        parser = TypeScriptParser()
        code = "function save() {\n  return db.insert(chat).values({ id: 1 });\n}\n"
        result = parser.parse_file("queries.ts", code)
        assert "db.insert(chat)" in result.functions[0].calls_with_args
        # The plain callee name remains in `calls`, args dropped.
        assert "db.insert" in result.functions[0].calls

    def test_arg_refs_skip_non_identifier_args(self):
        """Object/array/literal args produce no identifier ref."""
        parser = TypeScriptParser()
        code = "function f() {\n  doThing({ a: 1 }, 'str', 42);\n}\n"
        result = parser.parse_file("test.ts", code)
        assert all("doThing(" not in c for c in result.functions[0].calls_with_args)

    def test_captures_drizzle_select_chain(self):
        """Drizzle `db.select().from(chat)` carries the read verb via a synthetic
        `select.from(chat)` descriptor; a plain `.from()` does not."""
        parser = TypeScriptParser()
        code = "function load() {\n  return db.select().from(chat).where(eq(chat.id, id));\n}\n"
        result = parser.parse_file("queries.ts", code)
        assert "select.from(chat)" in result.functions[0].calls_with_args

    def test_plain_from_is_not_a_select_chain(self):
        """`.from()` not preceded by select() (e.g. Array.from) gets no synthetic verb."""
        parser = TypeScriptParser()
        code = "function f() {\n  return Array.from(items);\n}\n"
        result = parser.parse_file("test.ts", code)
        assert all(not c.startswith("select.from") for c in result.functions[0].calls_with_args)


class TestSupabaseChains:
    """Supabase/PostgREST `X.from('table').verb(...)` string-literal capture."""

    def _refs(self, code: str) -> list[str]:
        parser = TypeScriptParser()
        result = parser.parse_file("test.ts", code)
        return result.functions[0].calls_with_args

    def test_captures_all_postgrest_verbs(self):
        code = """async function f(id) {
  const { data } = await supabase
    .from('executions')
    .select('id, status')
    .eq('id', id);
  await supabase.from('profiles').update({ name: 'x' }).eq('id', id);
  await supabase.from('chats').insert({ id });
  await supabase.from('runs').delete().eq('id', id);
  await supabase.from('drafts').upsert({ id });
}
"""
        refs = self._refs(code)
        assert "select.from('executions')" in refs  # multi-line chain
        assert "update.from('profiles')" in refs
        assert "insert.from('chats')" in refs
        assert "delete.from('runs')" in refs
        assert "upsert.from('drafts')" in refs

    def test_storage_buckets_are_not_tables(self):
        """`supabase.storage.from('bucket')` and the common
        `const storage = supabase.storage` idiom must not match — storage's own
        `.update()` would otherwise read as a table write."""
        code = """async function f(file) {
  await supabase.storage.from('avatars').update('p.png', file);
  await storage.from('avatars').upsert('p.png', file);
}
"""
        assert all(".from('" not in c for c in self._refs(code))

    def test_buffer_and_bare_from_do_not_match(self):
        code = """function f() {
  const b = Buffer.from('abcdef');
  const q = supabase.from('orphans');
}
"""
        assert all(".from('" not in c for c in self._refs(code))

    def test_non_literal_tables_do_not_match(self):
        """Identifier and template-literal args are not string-literal tables."""
        code = """async function f(tableVar) {
  await supabase.from(tableVar).select('*');
  await supabase.from(`tmpl`).select('*');
}
"""
        assert all(".from('" not in c for c in self._refs(code))

    def test_captures_rpc_string_literal(self):
        """`supabase.rpc('fn', {...})` yields an `rpc('fn')` descriptor."""
        code = """async function f(id) {
  await supabase.rpc('deduct_credits', { p_amount: 5 });
  const { data } = await supabase.rpc('can_afford', { p_user_id: id });
}
"""
        refs = self._refs(code)
        assert "rpc('deduct_credits')" in refs
        assert "rpc('can_afford')" in refs

    def test_rpc_non_literal_ignored(self):
        """A dynamic/computed rpc name yields no descriptor (only a real name can
        match a declared DBFunction)."""
        code = "async function f(name) {\n  await supabase.rpc(name);\n  await supabase.rpc(`tmpl`);\n}\n"
        assert all(not c.startswith("rpc(") for c in self._refs(code))


class TestErrorFlow:
    def test_throws_new_error_type(self):
        parser = TypeScriptParser()
        result = parser.parse_file("t.ts", "function f() { throw new CustomError('x'); }\n")
        assert "CustomError" in result.functions[0].raises

    def test_rethrow_variable_has_no_type(self):
        parser = TypeScriptParser()
        result = parser.parse_file("t.ts", "function f() { try { g(); } catch (e) { throw e; } }\n")
        assert result.functions[0].raises == []  # re-throwing a variable names no type
        assert result.functions[0].catches == []  # JS/TS catch clauses can't name a type

    def test_has_error_handling(self):
        parser = TypeScriptParser()
        with_try = parser.parse_file("t.ts", "function f() { try { g(); } catch (e) { handle(e); } }\n")
        without = parser.parse_file("t.ts", "function h() { return g(); }\n")
        # catches is always empty for TS, so has_error_handling is the only error signal
        assert with_try.functions[0].has_error_handling is True
        assert with_try.functions[0].catches == []
        assert without.functions[0].has_error_handling is False


class TestVariableExtraction:
    def test_extracts_exported_const_object(self):
        parser = TypeScriptParser()
        result = parser.parse_file("config.ts", "export const config = { port: 3000 };\n")
        v = next(v for v in result.variables if v.name == "config")
        assert v.kind == "const"
        assert v.is_exported is True
        assert v.value_kind == "object"

    def test_extracts_call_value_schema(self):
        parser = TypeScriptParser()
        result = parser.parse_file("schema.ts", "export const userSchema = z.object({ id: z.string() });\n")
        v = next(v for v in result.variables if v.name == "userSchema")
        assert v.value_kind == "call"
        assert v.is_exported is True

    def test_arrow_const_is_function_not_variable(self):
        parser = TypeScriptParser()
        result = parser.parse_file("h.ts", "export const handler = () => {};\n")
        assert all(v.name != "handler" for v in result.variables)
        assert any(f.name == "handler" for f in result.functions)

    def test_non_exported_const(self):
        parser = TypeScriptParser()
        result = parser.parse_file("c.ts", "const MAX = 3;\n")
        v = next(v for v in result.variables if v.name == "MAX")
        assert v.is_exported is False
        assert v.value_kind == "literal"


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

    def test_inline_arrow_handler_becomes_function(self):
        """An inline arrow handler is synthesized into an entry-point Function so
        the route links to a real node instead of '<anonymous>'."""
        parser = TypeScriptParser()
        code = "const app = new Hono();\napp.get('/health', (c) => c.json({ ok: true }));\n"
        result = parser.parse_file("server.ts", code)
        route = result.routes[0]
        assert route.handler_name == "GET /health"
        handler = next(f for f in result.functions if f.qualified_name == f"server.ts::{route.handler_name}")
        assert handler.is_entry_point is True
        assert handler.entry_point_reason == "route_handler"
        assert "c.json" in handler.calls

    def test_inline_handler_captures_db_calls(self):
        """The synthesized handler records the model/table calls in its body so
        route -> handler -> USES_MODEL tracing works."""
        parser = TypeScriptParser()
        code = (
            "const app = new Hono();\n"
            "app.post('/chat', async (c) => {\n"
            "  await db.insert(chat).values({ id: 1 });\n"
            "  return c.json({ ok: true });\n"
            "});\n"
        )
        result = parser.parse_file("server.ts", code)
        handler = next(f for f in result.functions if f.entry_point_reason == "route_handler")
        assert handler.is_async is True
        assert "db.insert(chat)" in handler.calls_with_args

    def test_named_handler_adds_no_synthetic_function(self):
        """A named handler reference links directly; no synthetic Function is added."""
        parser = TypeScriptParser()
        code = "const router = express.Router();\nrouter.get('/users', getUsers);\n"
        result = parser.parse_file("routes.ts", code)
        assert result.routes[0].handler_name == "getUsers"
        assert all(f.entry_point_reason != "route_handler" for f in result.functions)

    def test_middleware_before_inline_handler(self):
        """Earlier callbacks are middleware; the last (arrow) is the handler."""
        parser = TypeScriptParser()
        code = "app.get('/admin', requireAuth, (c) => c.json({ ok: true }));\n"
        result = parser.parse_file("routes.ts", code)
        route = result.routes[0]
        assert route.middleware == ["requireAuth"]
        assert route.handler_name == "GET /admin"


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
        # The route HANDLES the synthesized inline handler; the named function it
        # delegates to (handleRequest) is reached via that handler's calls, so
        # route -> handler -> ...calls... -> Model resolves.
        assert result.routes[0].handler_name == "POST /my-func"
        synth = next(f for f in result.functions if f.name == "POST /my-func")
        assert synth.entry_point_reason == "route_handler"
        assert "handleRequest" in synth.calls

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
        assert result.routes[0].path == "/process-data"
        # Inline arrow handler is synthesized; it calls processRequest (which does
        # the work), so the route still reaches processRequest via the handler.
        assert result.routes[0].handler_name == "POST /process-data"
        synth = next(f for f in result.functions if f.name == "POST /process-data")
        assert "processRequest" in synth.calls

    def test_serve_wrapper_creates_route_and_handler(self):
        """A custom wrapper whose name starts 'serve' (e.g. the common
        serveWithInstrumentation) is recognized like serve()."""
        parser = TypeScriptParser()
        code = (
            "import { serveWithInstrumentation } from '../_shared/serve.ts';\n"
            "async function handleCompute(req) { await supabase.from('runs').insert({}); }\n"
            "serveWithInstrumentation('plan', async (req, ctx) => {\n"
            "  return handleCompute(req);\n"
            "});\n"
        )
        result = parser.parse_file("supabase/functions/plan/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/plan"
        assert result.routes[0].handler_name == "POST /plan"
        synth = next(f for f in result.functions if f.name == "POST /plan")
        assert "handleCompute" in synth.calls

    def test_serve_wrapper_handler_not_last_arg(self):
        """The handler is the last *function* arg, not the last arg — real
        wrappers append an options object: serveWith('name', handler, {opts})."""
        parser = TypeScriptParser()
        code = (
            "async function handleIt(req, ctx) { await supabase.from('t').select(); }\n"
            "serveWithInstrumentation('analyze', async (req, ctx) => {\n"
            "  return handleIt(req, ctx);\n"
            "}, { timeout: 30 });\n"
        )
        result = parser.parse_file("supabase/functions/analyze/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/analyze"
        synth = next(f for f in result.functions if f.name == "POST /analyze")
        assert "handleIt" in synth.calls

    def test_export_default_fetch_creates_route(self):
        """The modern `export default { fetch: wrapper(opts, handler) }` form."""
        parser = TypeScriptParser()
        code = (
            "export default {\n"
            "  fetch: withSupabase({ auth: 'secret' }, async (req, ctx) => {\n"
            "    return doEmbed(req);\n"
            "  })\n"
            "}\n"
            "function doEmbed(r) { return supabase.from('docs').select('*'); }\n"
        )
        result = parser.parse_file("supabase/functions/generate-embedding/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/generate-embedding"
        synth = next(f for f in result.functions if f.name == "POST /generate-embedding")
        assert "doEmbed" in synth.calls

    def test_add_event_listener_fetch_creates_route(self):
        """Service-worker style `addEventListener('fetch', handler)`."""
        parser = TypeScriptParser()
        code = "addEventListener('fetch', (event) => event.respondWith(handleReq(event.request)));\n"
        result = parser.parse_file("supabase/functions/worker/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/worker"
        synth = next(f for f in result.functions if f.name == "POST /worker")
        assert "handleReq" in synth.calls

    def test_named_handler_reference_linked_by_name(self):
        """`serve(handleRequest)` — a bare named handler is linked by name, not
        synthesized (the function already exists as a node)."""
        parser = TypeScriptParser()
        code = "function handleRequest(req) { return supabase.from('t').select(); }\nserve(handleRequest);\n"
        result = parser.parse_file("supabase/functions/named/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].handler_name == "handleRequest"
        # No synthetic handler created for a named reference.
        assert not any(f.name == "POST /named" for f in result.functions)

    def test_shared_dir_is_not_an_endpoint(self):
        """Supabase never deploys `_`-prefixed dirs (shared code)."""
        parser = TypeScriptParser()
        code = "export function serveWithInstrumentation(name, h) { serve(h); }\n"
        result = parser.parse_file("supabase/functions/_shared/serve.ts", code)
        assert len(result.routes) == 0

    def test_internal_hono_routing_skips_directory_envelope(self):
        """An edge function that routes internally (Hono/Express) keeps its
        specific routes and does not also get a directory-envelope POST route."""
        parser = TypeScriptParser()
        code = "const app = new Hono();\napp.get('/health', (c) => c.json({ ok: 1 }));\nDeno.serve(app.fetch);\n"
        result = parser.parse_file("supabase/functions/api/index.ts", code)
        paths = {(r.method, r.path) for r in result.routes}
        assert ("GET", "/health") in paths
        assert ("POST", "/api") not in paths

    def test_javascript_edge_function_creates_route(self):
        """Edge-function synthesis also works via the JavaScript parser path."""
        code = "Deno.serve(async (req) => handleIt(req));\nfunction handleIt(r) {}\n"
        result = JavaScriptParser().parse_file("supabase/functions/js-func/index.js", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/js-func"
        assert result.routes[0].handler_name == "POST /js-func"

    def test_object_method_fetch_handler(self):
        """The canonical modern Deno form `export default { async fetch(req){} }`
        (a method_definition, not a `fetch:` pair)."""
        parser = TypeScriptParser()
        code = "export default {\n  async fetch(req) { return handle(req); }\n}\nfunction handle(r) {}\n"
        result = parser.parse_file("supabase/functions/modern/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/modern"
        synth = next(f for f in result.functions if f.name == "POST /modern")
        assert "handle" in synth.calls

    def test_computed_fetch_key(self):
        """`export default { ['fetch']: handler }` — a computed property key."""
        parser = TypeScriptParser()
        code = "export default { ['fetch']: async (req) => go(req) };\nfunction go(r) {}\n"
        result = parser.parse_file("supabase/functions/computed/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/computed"

    def test_await_and_const_serve_forms(self):
        """`await serve(...)` and `const s = Deno.serve(...)` register a handler."""
        parser = TypeScriptParser()
        for code, name in [
            ("await serve(async (req) => aa(req));\nfunction aa(r) {}\n", "await-fn"),
            ("const server = Deno.serve(async (req) => bb(req));\nfunction bb(r) {}\n", "const-fn"),
        ]:
            result = parser.parse_file(f"supabase/functions/{name}/index.ts", code)
            assert len(result.routes) == 1, name
            assert result.routes[0].path == f"/{name}"

    def test_false_framework_route_does_not_suppress_envelope(self):
        """A stray framework-looking call in the body (a false positive from the
        static router-name set) must not suppress the real edge route."""
        parser = TypeScriptParser()
        code = (
            "import { serveWithInstrumentation } from '../_shared/serve.ts';\n"
            "api.get('/v1/x', h);\n"  # `api` is in the static router set -> false route
            "serveWithInstrumentation('f4', async (req) => doIt(req), {});\n"
            "function doIt(r) {}\n"
        )
        result = parser.parse_file("supabase/functions/f4/index.ts", code)
        assert ("POST", "/f4") in {(r.method, r.path) for r in result.routes}

    def test_internal_hono_export_default_app_no_dangling_envelope(self):
        """`export default app` (a bare Hono instance) delegates to the app's own
        routes; no directory envelope with an unresolvable handler is emitted."""
        parser = TypeScriptParser()
        code = "const app = new Hono();\napp.post('/x', (c) => c.json({}));\nexport default app;\n"
        result = parser.parse_file("supabase/functions/deleg/index.ts", code)
        paths = {(r.method, r.path) for r in result.routes}
        assert ("POST", "/x") in paths
        assert ("POST", "/deleg") not in paths

    def test_wrapper_with_named_handler(self):
        """`serveWithInstrumentation('name', handleRequest)` — a wrapper keyed by a
        name string with a *named* (non-inline) handler. Linked by name."""
        parser = TypeScriptParser()
        code = (
            "function handleRequest(req) { return supabase.from('t').select(); }\n"
            "serveWithInstrumentation('my-fn', handleRequest);\n"
        )
        result = parser.parse_file("supabase/functions/my-fn/index.ts", code)
        assert len(result.routes) == 1
        assert result.routes[0].path == "/my-fn"
        assert result.routes[0].handler_name == "handleRequest"

    def test_serve_prefix_over_match_rejected(self):
        """`server(app)` and `serveStatic(...)` start with 'serve' but are not
        handler registrations — they must not create a route."""
        parser = TypeScriptParser()
        assert parser.parse_file("supabase/functions/s1/index.ts", "server(app);\n").routes == []
        assert (
            parser.parse_file("supabase/functions/s2/index.ts", "serveStatic('/public', { root: '.' });\n").routes == []
        )


class TestEdgeHandlerDetectionIsScoped:
    """The edge-function handler idioms must NOT be recognized outside
    supabase/functions/<name>/index.ts — otherwise ordinary default exports and
    serve-prefixed calls would be sprayed with false `serve_handler` entry
    points, corrupting dead-export/impact analysis (regression guard)."""

    def _serve_entry_points(self, path: str, code: str) -> list[str]:
        parser = TypeScriptParser()
        result = parser.parse_file(path, code)
        return sorted(f.name for f in result.functions if f.entry_point_reason == "serve_handler")

    def test_default_export_identifier_not_flagged(self):
        assert (
            self._serve_entry_points("src/pages/Home.tsx", "function Home(){ return x(); }\nexport default Home;\n")
            == []
        )

    def test_hoc_default_export_not_flagged(self):
        assert self._serve_entry_points("src/App.tsx", "export default connect(mapState)(App);\n") == []

    def test_cloudflare_worker_fetch_not_flagged(self):
        code = "export default { fetch: (request, env) => processRequest(request) };\n"
        assert self._serve_entry_points("src/worker.ts", code) == []

    def test_local_serve_prefixed_call_not_flagged(self):
        assert self._serve_entry_points("src/lib/data.ts", "serveData(collectMetrics);\n") == []

    def test_edge_function_still_flags_its_callees(self):
        """The gate must not disable legitimate edge-function entry-point marking."""
        code = "serve(async (req) => handleIt(req));\nfunction handleIt(r){}\n"
        assert "handleIt" in self._serve_entry_points("supabase/functions/f/index.ts", code)


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
