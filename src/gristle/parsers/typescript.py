"""TypeScript/JavaScript parser using tree-sitter."""

from __future__ import annotations

import re

import tree_sitter_javascript as ts_js
import tree_sitter_typescript as ts_ts
from tree_sitter import Language, Node, Parser

from gristle.models import (
    ParsedClass,
    ParsedFile,
    ParsedFunction,
    ParsedImport,
    ParsedRoute,
    ParsedTestCase,
    ParsedTypeField,
    ParsedVariable,
)
from gristle.parsers.base import LanguageParser

# Patterns for TODO/FIXME/HACK comments
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG|WARN(?:ING)?)\b[:\s]*(.*)", re.IGNORECASE)

# Patterns for detecting test files
# Require test[-_]/spec[-_] at a path-segment boundary (after / or start)
# to avoid false positives like "test-runner/" or "v1-specs/"
_TEST_FILE_RE = re.compile(
    r"(?:"
    r"__tests__"  # __tests__/ directory
    r"|__mocks__"  # __mocks__/ directory
    r"|\.test\."  # file.test.ts
    r"|\.spec\."  # file.spec.ts
    r"|(?:^|/)tests/"  # tests/ directory at segment boundary
    r"|(?:^|/)specs/"  # specs/ directory at segment boundary
    r"|(?:^|/)test[-_][^/]*\.[tj]sx?$"  # test-foo.ts or test_foo.ts (filename, not directory)
    r"|(?:^|/)spec[-_][^/]*\.[tj]sx?$"  # spec-foo.ts or spec_foo.ts (filename, not directory)
    r")",
    re.IGNORECASE,
)

# Test function names
_TEST_FUNC_RE = re.compile(r"^(?:test|it|describe|beforeAll|beforeEach|afterAll|afterEach|expect)$")

# Next.js app router page files
_NEXTJS_PAGE_RE = re.compile(r"(?:^|/)(?:app|pages)/.*?(?:page|route|layout|loading|error|not-found)\.[tj]sx?$")

# Route method patterns for Express/Hono/Fastify etc.
_ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch", "all", "options", "head"})

# Supabase edge function path pattern: supabase/functions/<name>/index.ts
_SUPABASE_FUNC_RE = re.compile(r"(?:^|/)supabase/functions/([^/]+)/index\.[tj]sx?$")


def _supabase_edge_function_name(file_path: str) -> str | None:
    """Return the deployed function name for a Supabase edge-function file, or None.

    Only ``supabase/functions/<name>/index.ts`` qualifies; ``_``-prefixed dirs are
    Supabase shared code (never deployed). The edge-handler idioms
    (``serve``-prefixed calls, ``export default { fetch }``, ``addEventListener``)
    are only meaningful in these files, so all edge detection is gated on this —
    the same idioms in ordinary app code (``export default MyComponent``, a HOC
    call, a Cloudflare Worker's ``fetch``) must not be treated as edge handlers.
    """
    m = _SUPABASE_FUNC_RE.search(file_path.replace("\\", "/"))
    if not m or m.group(1).startswith("_"):
        return None
    return m.group(1)


# Storybook story files
_STORYBOOK_RE = re.compile(r"\.stories\.[tj]sx?$")

# First quoted argument of a decorator call, e.g. Get(':id') -> ':id'
_DECORATOR_ARG_RE = re.compile(r"""\(\s*['"]([^'"]*)['"]""")

# Serverless handler export patterns (AWS Lambda convention)
_SERVERLESS_HANDLER_NAMES = frozenset({"handler"})


class TypeScriptParser(LanguageParser):
    """Parses TypeScript and TSX source files."""

    def __init__(self) -> None:
        self._ts_parser = Parser(Language(ts_ts.language_typescript()))
        self._tsx_parser = Parser(Language(ts_ts.language_tsx()))

    @property
    def language_name(self) -> str:
        return "typescript"

    @property
    def file_extensions(self) -> list[str]:
        return ["ts", "tsx"]

    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        parser = self._tsx_parser if file_path.endswith(".tsx") else self._ts_parser
        tree = parser.parse(content.encode())
        root = tree.root_node
        src = content.encode()

        is_test_file = bool(_TEST_FILE_RE.search(file_path))
        functions = self._extract_module_functions(root, src, file_path)
        is_tsx = file_path.endswith(".tsx") or file_path.endswith(".jsx")

        # Detect serve() / Deno.serve() entry point patterns (Deno/Supabase edge functions)
        serve_callees = self._detect_serve_entry_points(root, src, file_path)

        # Post-process: detect components, tests, entry points
        for func in functions:
            if is_tsx and func.name and func.name[0].isupper():
                func.is_component = self._body_contains_jsx(root, src, func)
            if is_test_file or _TEST_FUNC_RE.match(func.name):
                func.is_test = True
            reason = self._classify_entry_point(func, file_path)
            if reason:
                func.is_entry_point = True
                func.entry_point_reason = reason
            # Functions called from inside a serve() handler are entry points
            if func.name in serve_callees:
                func.is_entry_point = True
                if not func.entry_point_reason:
                    func.entry_point_reason = "serve_handler"

        # Also check methods in classes
        classes = self._extract_classes(root, src, file_path)
        for cls in classes:
            for method in cls.methods:
                if is_test_file or _TEST_FUNC_RE.match(method.name):
                    method.is_test = True

        # Extract TODOs from comments
        file_todos = self._extract_todos(root, src)
        # Extract routes and auth middleware paths
        routes = self._extract_routes(root, src, file_path)
        # Inline arrow/function route handlers become real Function nodes.
        functions.extend(self._synth_route_handlers)
        auth_mw_paths = self._extract_auth_middleware_paths(root, src)
        # Extract test cases (describe/it/test blocks) from test files
        test_cases = self._extract_test_cases(root, src, file_path) if is_test_file else []

        # Extract env var references
        from gristle.parsers.env_vars import extract_env_var_refs

        env_var_refs = extract_env_var_refs(content, "typescript")

        # Security pattern detection
        from gristle.parsers.security import (
            detect_hardcoded_secrets,
            detect_llm_output_risks,
            detect_sql_injection,
            detect_unsafe_calls,
        )

        file_security = (
            detect_hardcoded_secrets(content, "typescript") + detect_sql_injection(content, "typescript")
            if not is_test_file
            else []
        )

        if not is_test_file:
            all_funcs = list(functions)
            for cls in classes:
                all_funcs.extend(cls.methods)
            for func in all_funcs:
                func.security_findings = detect_unsafe_calls(func.calls) + detect_llm_output_risks(func.calls)
            for finding in file_security:
                tag = f"{finding.category}:{finding.detail}"
                for func in all_funcs:
                    if func.start_line <= finding.line <= func.end_line and tag not in func.security_findings:
                        func.security_findings.append(tag)
                        break

        return ParsedFile(
            path=file_path,
            language="typescript",
            classes=classes,
            functions=functions,
            imports=self._extract_imports(root, src)
            + self._extract_reexports(root, src)
            + self._extract_dynamic_imports(root, src),
            routes=routes,
            test_cases=test_cases,
            variables=self._extract_module_variables(root, src, file_path),
            module_docstring=self._extract_module_docstring(root, src),
            line_count=content.count("\n") + 1,
            is_test_file=is_test_file,
            todos=file_todos,
            env_var_refs=env_var_refs,
            security_findings=file_security,
            auth_middleware_paths=auth_mw_paths,
            react_directive=self._detect_react_directive(root, src),
        )

    # ------------------------------------------------------------------
    # React directive detection
    # ------------------------------------------------------------------

    def _detect_react_directive(self, root: Node, src: bytes) -> str | None:
        """Detect 'use client' or 'use server' directive at the top of a file."""
        for child in root.children:
            if child.type == "expression_statement":
                named = child.named_children
                if len(named) == 1 and named[0].type == "string":
                    text = self._text(named[0], src).strip("'\"")
                    if text in ("use client", "use server"):
                        return text
            elif child.type in ("comment", "import_statement", "export_statement"):
                continue
            else:
                break
        return None

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_imports(self, root: Node, src: bytes) -> list[ParsedImport]:
        imports: list[ParsedImport] = []
        for node in root.children:
            if node.type == "import_statement":
                imp = self._parse_import(node, src)
                if imp:
                    imports.append(imp)
        return imports

    def _parse_import(self, node: Node, src: bytes) -> ParsedImport | None:
        source_node = node.child_by_field_name("source")
        if not source_node:
            return None

        module_path = self._text(source_node, src).strip("'\"")
        names: list[str] = []
        aliases: dict[str, str] = {}

        # Find the import clause
        for child in node.children:
            if child.type == "import_clause":
                self._extract_import_names(child, src, names, aliases)

        is_relative = module_path.startswith(".")

        return ParsedImport(
            line=node.start_point[0] + 1,
            module_path=module_path,
            imported_names=names,
            aliases=aliases,
            is_relative=is_relative,
        )

    def _extract_import_names(self, node: Node, src: bytes, names: list[str], aliases: dict[str, str]) -> None:
        for child in node.children:
            if child.type == "identifier":
                # Default import
                names.append(self._text(child, src))
            elif child.type == "named_imports":
                for spec in child.children:
                    if spec.type == "import_specifier":
                        name_node = spec.child_by_field_name("name")
                        alias_node = spec.child_by_field_name("alias")
                        if name_node:
                            name = self._text(name_node, src)
                            names.append(name)
                            if alias_node:
                                aliases[name] = self._text(alias_node, src)
            elif child.type == "namespace_import":
                # import * as X
                alias = self._find_child(child, "identifier")
                if alias:
                    names.append("*")
                    aliases["*"] = self._text(alias, src)

    def _extract_reexports(self, root: Node, src: bytes) -> list[ParsedImport]:
        """Extract re-export statements as ParsedImport objects.

        Handles barrel file patterns like::

            export { Foo, Bar } from './module';
            export * from './module';
            export type { Baz } from './module';
            export { default as Qux } from './module';
        """
        reexports: list[ParsedImport] = []
        for node in root.children:
            if node.type != "export_statement":
                continue
            source_node = node.child_by_field_name("source")
            if not source_node:
                continue  # Not a re-export — no ``from`` clause

            module_path = self._text(source_node, src).strip("'\"")
            is_relative = module_path.startswith(".")

            names: list[str] = []
            aliases: dict[str, str] = {}
            is_wildcard = False

            for child in node.children:
                if child.type == "export_clause":
                    for spec in child.children:
                        if spec.type == "export_specifier":
                            name_node = spec.child_by_field_name("name")
                            alias_node = spec.child_by_field_name("alias")
                            if name_node:
                                name = self._text(name_node, src)
                                names.append(name)
                                if alias_node:
                                    aliases[name] = self._text(alias_node, src)
                elif child.type == "*":
                    is_wildcard = True
                    names.append("*")

            reexports.append(
                ParsedImport(
                    line=node.start_point[0] + 1,
                    module_path=module_path,
                    imported_names=names,
                    aliases=aliases,
                    is_relative=is_relative,
                    is_wildcard=is_wildcard,
                )
            )
        return reexports

    # ------------------------------------------------------------------
    # Dynamic imports
    # ------------------------------------------------------------------

    def _extract_dynamic_imports(self, root: Node, src: bytes) -> list[ParsedImport]:
        """Extract dynamic ``import()`` and ``require()`` calls as imports.

        These are ``call_expression`` nodes rather than ``import_statement``
        nodes, so the normal import extractor misses them.  Only string-literal
        arguments are captured; template literals and variable references are
        skipped because the module path is not statically known.
        """
        results: list[ParsedImport] = []
        self._walk_dynamic_imports(root, src, results)
        return results

    def _walk_dynamic_imports(self, node: Node, src: bytes, out: list[ParsedImport]) -> None:
        if node.type == "call_expression":
            func = node.child_by_field_name("function")
            if func is not None:
                # import('./utils')  — func.type == "import"
                # require('./lib')  — func.type == "identifier", text == "require"
                is_dynamic = func.type == "import" or (func.type == "identifier" and self._text(func, src) == "require")
                if is_dynamic:
                    args = node.child_by_field_name("arguments")
                    if args is not None and args.named_child_count > 0:
                        first_arg = args.named_children[0]
                        if first_arg.type == "string":
                            module_path = self._text(first_arg, src).strip("'\"")
                            is_relative = module_path.startswith(".")
                            out.append(
                                ParsedImport(
                                    line=node.start_point[0] + 1,
                                    module_path=module_path,
                                    imported_names=["*"],
                                    aliases={},
                                    is_relative=is_relative,
                                    is_wildcard=True,
                                )
                            )
                            return  # Don't recurse into children

        for child in node.children:
            self._walk_dynamic_imports(child, src, out)

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _extract_classes(self, root: Node, src: bytes, file_path: str) -> list[ParsedClass]:
        classes: list[ParsedClass] = []
        for node in root.children:
            if node.type in ("class_declaration", "abstract_class_declaration"):
                classes.append(self._parse_class(node, src, file_path))
            elif node.type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                classes.append(self._parse_interface(node, src, file_path))
            elif node.type == "export_statement":
                decl = node.child_by_field_name("declaration")
                if decl and decl.type in ("class_declaration", "abstract_class_declaration"):
                    cls = self._parse_class(decl, src, file_path)
                    cls.is_exported = True
                    classes.append(cls)
                elif decl and decl.type in ("interface_declaration", "type_alias_declaration", "enum_declaration"):
                    cls = self._parse_interface(decl, src, file_path)
                    cls.is_exported = True
                    classes.append(cls)
        return classes

    def _extract_decorators(self, node: Node, src: bytes) -> list[str]:
        """Collect decorator expressions attached to a class or method.

        tree-sitter places ``decorator`` nodes as siblings immediately preceding
        the decorated node (before any ``export``/``abstract``/``default``
        keyword). Stored as the text after ``@`` to match the Python parser
        (e.g. ``Controller('users')``, ``Get(':id')``).
        """
        decorators: list[str] = []
        sib = node.prev_sibling
        while sib is not None:
            if sib.type == "decorator":
                decorators.insert(0, self._text(sib, src).lstrip("@").strip())
            elif sib.type in ("export", "default", "abstract", "comment"):
                pass  # keywords/comments may sit between decorators and the node
            else:
                break
            sib = sib.prev_sibling
        return decorators

    def _parse_class(self, node: Node, src: bytes, file_path: str) -> ParsedClass:
        name = self._text(node.child_by_field_name("name"), src)
        body = node.child_by_field_name("body")
        bases = self._extract_heritage(node, src)
        methods = self._extract_class_methods(body, src, file_path, name) if body else []

        bases_str = f" extends {', '.join(bases)}" if bases else ""
        sig = f"class {name}{bases_str}"

        class_fields = self._extract_type_fields(node, src, file_path)

        return ParsedClass(
            name=name,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=sig,
            docstring=self._extract_jsdoc(node, src),
            is_abstract=(node.type == "abstract_class_declaration" or self._has_modifier(node, "abstract")),
            visibility="public",
            bases=bases,
            methods=methods,
            fields=class_fields,
            decorators=self._extract_decorators(node, src),
        )

    def _parse_interface(self, node: Node, src: bytes, file_path: str) -> ParsedClass:
        name = self._text(node.child_by_field_name("name"), src)
        kind_map = {
            "interface_declaration": "interface",
            "type_alias_declaration": "type",
            "enum_declaration": "enum",
        }
        kind = kind_map.get(node.type, "class")
        bases = self._extract_heritage(node, src)
        type_fields = self._extract_type_fields(node, src, file_path)

        sig_text = self._get_first_line(node, src)

        return ParsedClass(
            name=name,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=sig_text,
            docstring=self._extract_jsdoc(node, src),
            is_abstract=False,
            visibility="public",
            bases=bases,
            methods=[],
            kind=kind,
            fields=type_fields,
        )

    def _extract_heritage(self, node: Node, src: bytes) -> list[str]:
        """Extract extends/implements clauses."""
        bases: list[str] = []
        for child in node.children:
            # tree-sitter wraps extends/implements in a class_heritage node
            if child.type == "class_heritage":
                for heritage_child in child.children:
                    if heritage_child.type in ("extends_clause", "extends_type_clause", "implements_clause"):
                        for sub in heritage_child.children:
                            if sub.type in (
                                "identifier",
                                "nested_identifier",
                                "generic_type",
                                "type_identifier",
                                "nested_type_identifier",
                            ):
                                bases.append(self._text(sub, src))
            # Also check direct children (interfaces use extends_clause directly)
            elif child.type in ("extends_clause", "extends_type_clause", "implements_clause"):
                for sub in child.children:
                    if sub.type in (
                        "identifier",
                        "nested_identifier",
                        "generic_type",
                        "type_identifier",
                        "nested_type_identifier",
                    ):
                        bases.append(self._text(sub, src))
        return bases

    def _extract_class_methods(self, body: Node, src: bytes, file_path: str, class_name: str) -> list[ParsedFunction]:
        methods: list[ParsedFunction] = []
        if body is None:
            return methods
        for child in body.children:
            if child.type in ("method_definition", "public_field_definition") and child.type == "method_definition":
                methods.append(self._parse_method(child, src, file_path, class_name))
        return methods

    def _extract_type_fields(self, node: Node, src: bytes, file_path: str) -> list[ParsedTypeField]:
        """Extract fields from interface/type/class body."""
        fields: list[ParsedTypeField] = []
        body = node.child_by_field_name("body")
        if body is None:
            # type_alias_declaration uses 'value' field for the type body
            body = node.child_by_field_name("value")
        if body is None:
            return fields

        for child in body.named_children:
            if child.type in ("property_signature", "public_field_definition"):
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = self._text(name_node, src)
                # Extract type annotation
                type_node = child.child_by_field_name("type")
                type_text = self._text(type_node, src).lstrip(": ") if type_node else None
                # Check for optional marker (?)
                is_optional = any(c.type == "?" for c in child.children)
                # Check for default value
                default_node = child.child_by_field_name("value")
                default_value = self._text(default_node, src) if default_node else None

                fields.append(
                    ParsedTypeField(
                        name=name,
                        type_annotation=type_text,
                        is_optional=is_optional,
                        default_value=default_value,
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                    )
                )
            elif child.type == "enum_assignment":
                # Enum member: enum Status { Active = 0, Inactive = 1 }
                name_node = child.child_by_field_name("name")
                if not name_node:
                    continue
                name = self._text(name_node, src)
                value_node = child.child_by_field_name("value")
                default_value = self._text(value_node, src) if value_node else None
                fields.append(
                    ParsedTypeField(
                        name=name,
                        type_annotation=None,
                        default_value=default_value,
                        file_path=file_path,
                        line=child.start_point[0] + 1,
                    )
                )
        return fields

    def _extract_typed_params(self, params_node: Node | None, src: bytes) -> list[tuple[str, str | None]]:
        """Extract (name, type) pairs from a function's parameters node."""
        if params_node is None:
            return []
        result: list[tuple[str, str | None]] = []
        for child in params_node.named_children:
            if child.type in ("required_parameter", "optional_parameter"):
                # Get name: could be identifier, or pattern (destructuring)
                name_node = child.child_by_field_name("pattern") or child.child_by_field_name("name")
                if not name_node:
                    continue
                name = self._text(name_node, src)
                # Get type annotation
                type_node = child.child_by_field_name("type")
                type_text = self._text(type_node, src).lstrip(": ") if type_node else None
                result.append((name, type_text))
            elif child.type == "rest_pattern":
                # ...args: string[]
                for sub in child.named_children:
                    if sub.type == "identifier":
                        name = f"...{self._text(sub, src)}"
                        type_node = child.child_by_field_name("type")
                        type_text = self._text(type_node, src).lstrip(": ") if type_node else None
                        result.append((name, type_text))
                        break
        return result

    def _parse_method(self, node: Node, src: bytes, file_path: str, class_name: str) -> ParsedFunction:
        name = self._text(node.child_by_field_name("name"), src)
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        body = node.child_by_field_name("body")

        params_text = self._text(params_node, src) if params_node else "()"
        return_text = self._text(return_node, src).lstrip(": ") if return_node else None
        is_async = self._has_modifier(node, "async")
        typed_params = self._extract_typed_params(params_node, src)

        sig = f"{'async ' if is_async else ''}{name}{params_text}{f': {return_text}' if return_text else ''}"
        qualified = f"{file_path}::{class_name}.{name}"

        calls = self._extract_calls(body, src) if body else []
        calls_with_args = self._extract_call_arg_refs(body, src) if body else []
        callback_refs = self._extract_callback_refs(body, src) if body else []
        raises, catches = self._extract_error_flow(body, src) if body else ([], [])
        has_error_handling = self._has_try(body) if body else False

        return ParsedFunction(
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=sig,
            docstring=self._extract_jsdoc(node, src),
            is_async=is_async,
            is_static=self._has_modifier(node, "static"),
            visibility=self._ts_visibility(node, src),
            return_type=return_text,
            complexity=self._cyclomatic_complexity(body) if body else 1,
            calls=calls,
            calls_with_args=calls_with_args,
            callback_refs=callback_refs,
            parameters=[p[0] for p in typed_params],
            typed_parameters=typed_params,
            raises=raises,
            catches=catches,
            has_error_handling=has_error_handling,
            decorators=self._extract_decorators(node, src),
        )

    # ------------------------------------------------------------------
    # Functions (module-level)
    # ------------------------------------------------------------------

    def _extract_module_functions(self, root: Node, src: bytes, file_path: str) -> list[ParsedFunction]:
        functions: list[ParsedFunction] = []
        for node in root.children:
            func = self._try_extract_function(node, src, file_path)
            if func:
                functions.append(func)
            elif node.type == "export_statement":
                decl = node.child_by_field_name("declaration")
                if decl:
                    func = self._try_extract_function(decl, src, file_path)
                    if func:
                        func.is_exported = True
                        functions.append(func)
                # Also handle export default function
                for child in node.children:
                    if child is decl:
                        continue
                    func = self._try_extract_function(child, src, file_path)
                    if func and func.name not in [f.name for f in functions]:
                        func.is_exported = True
                        functions.append(func)
        return functions

    def _try_extract_function(self, node: Node, src: bytes, file_path: str) -> ParsedFunction | None:
        if node.type == "function_declaration":
            return self._parse_function_decl(node, src, file_path)
        if node.type == "lexical_declaration":
            # const foo = () => {} or const foo = function() {}
            return self._parse_variable_function(node, src, file_path)
        return None

    def _parse_function_decl(self, node: Node, src: bytes, file_path: str) -> ParsedFunction:
        name = self._text(node.child_by_field_name("name"), src)
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        body = node.child_by_field_name("body")

        params_text = self._text(params_node, src) if params_node else "()"
        return_text = self._text(return_node, src).lstrip(": ") if return_node else None
        is_async = "async" in self._text(node, src).split("function")[0]
        typed_params = self._extract_typed_params(params_node, src)

        sig = f"{'async ' if is_async else ''}function {name}{params_text}{f': {return_text}' if return_text else ''}"

        calls = self._extract_calls(body, src) if body else []
        calls_with_args = self._extract_call_arg_refs(body, src) if body else []
        callback_refs = self._extract_callback_refs(body, src) if body else []
        raises, catches = self._extract_error_flow(body, src) if body else ([], [])
        has_error_handling = self._has_try(body) if body else False

        return ParsedFunction(
            name=name,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=sig,
            docstring=self._extract_jsdoc(node, src),
            is_async=is_async,
            visibility="public",
            return_type=return_text,
            complexity=self._cyclomatic_complexity(body) if body else 1,
            calls=calls,
            calls_with_args=calls_with_args,
            callback_refs=callback_refs,
            parameters=[p[0] for p in typed_params],
            typed_parameters=typed_params,
            raises=raises,
            catches=catches,
            has_error_handling=has_error_handling,
        )

    def _parse_variable_function(self, node: Node, src: bytes, file_path: str) -> ParsedFunction | None:
        """Parse `const foo = (...) => { ... }` or `const foo = function(...) { ... }`."""
        for child in node.children:
            if child.type == "variable_declarator":
                name_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if not name_node or not value_node:
                    continue
                if value_node.type not in ("arrow_function", "function_expression", "function"):
                    continue

                name = self._text(name_node, src)
                params_node = value_node.child_by_field_name("parameters")
                return_node = value_node.child_by_field_name("return_type")
                body = value_node.child_by_field_name("body")

                params_text = self._text(params_node, src) if params_node else "()"
                return_text = self._text(return_node, src).lstrip(": ") if return_node else None
                is_async = "async" in self._text(value_node, src).split("=>")[0].split("function")[0]
                typed_params = self._extract_typed_params(params_node, src)

                sig = (
                    f"const {name} = {'async ' if is_async else ''}{params_text} => ..."
                    if value_node.type == "arrow_function"
                    else f"const {name} = {'async ' if is_async else ''}function{params_text}"
                )

                calls = self._extract_calls(body, src) if body else []
                calls_with_args = self._extract_call_arg_refs(body, src) if body else []
                callback_refs = self._extract_callback_refs(body, src) if body else []
                raises, catches = self._extract_error_flow(body, src) if body else ([], [])
                has_error_handling = self._has_try(body) if body else False

                return ParsedFunction(
                    name=name,
                    qualified_name=f"{file_path}::{name}",
                    file_path=file_path,
                    start_line=node.start_point[0] + 1,
                    end_line=node.end_point[0] + 1,
                    signature=sig,
                    docstring=self._extract_jsdoc(node, src),
                    is_async=is_async,
                    visibility="public",
                    return_type=return_text,
                    complexity=self._cyclomatic_complexity(body) if body else 1,
                    calls=calls,
                    calls_with_args=calls_with_args,
                    callback_refs=callback_refs,
                    parameters=[p[0] for p in typed_params],
                    typed_parameters=typed_params,
                    raises=raises,
                    catches=catches,
                    has_error_handling=has_error_handling,
                )
        return None

    # ------------------------------------------------------------------
    # Module-level variables / constants
    # ------------------------------------------------------------------

    _VALUE_KIND_MAP = {
        "object": "object",
        "array": "array",
        "call_expression": "call",
        "new_expression": "new",
        "string": "literal",
        "template_string": "literal",
        "number": "literal",
        "true": "literal",
        "false": "literal",
        "null": "literal",
        "identifier": "reference",
        "member_expression": "reference",
    }
    _FUNCTION_VALUE_TYPES = ("arrow_function", "function_expression", "function")

    def _extract_module_variables(self, root: Node, src: bytes, file_path: str) -> list[ParsedVariable]:
        """Extract module-level const/let/var bindings that aren't functions.

        Functions (``const f = () => …``) are handled by
        :meth:`_extract_module_functions`; this captures the rest — config
        objects, Zod schemas, registries, constants.
        """
        variables: list[ParsedVariable] = []
        for node in root.children:
            if node.type in ("lexical_declaration", "variable_declaration"):
                variables.extend(self._vars_from_declaration(node, src, file_path, is_exported=False))
            elif node.type == "export_statement":
                decl = node.child_by_field_name("declaration")
                if decl is not None and decl.type in ("lexical_declaration", "variable_declaration"):
                    variables.extend(self._vars_from_declaration(decl, src, file_path, is_exported=True))
        return variables

    def _vars_from_declaration(self, node: Node, src: bytes, file_path: str, is_exported: bool) -> list[ParsedVariable]:
        keyword = self._text(node.children[0], src) if node.children else "const"
        kind = keyword if keyword in ("const", "let", "var") else "const"
        out: list[ParsedVariable] = []
        for child in node.children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None or name_node.type != "identifier":
                continue  # skip destructuring patterns
            if value_node is not None and value_node.type in self._FUNCTION_VALUE_TYPES:
                continue  # a function — handled elsewhere
            name = self._text(name_node, src)
            out.append(
                ParsedVariable(
                    name=name,
                    qualified_name=f"{file_path}::{name}",
                    file_path=file_path,
                    start_line=child.start_point[0] + 1,
                    end_line=child.end_point[0] + 1,
                    kind=kind,
                    is_exported=is_exported,
                    value_kind=self._VALUE_KIND_MAP.get(value_node.type, "other") if value_node else "",
                )
            )
        return out

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    # Method names that indicate a callback/handler pattern
    _MIDDLEWARE_METHODS = frozenset({"use", "middleware"})
    _ROUTE_METHODS = frozenset({"get", "post", "put", "delete", "patch", "all", "head", "options"})
    _EVENT_METHODS = frozenset({"on", "once", "addEventListener", "addListener", "removeListener"})
    _PROMISE_METHODS = frozenset({"then", "catch", "finally"})
    _ARRAY_METHODS = frozenset(
        {
            "map",
            "filter",
            "reduce",
            "forEach",
            "find",
            "findIndex",
            "some",
            "every",
            "flatMap",
            "sort",
            "reduceRight",
        }
    )

    def _extract_calls(self, node: Node, src: bytes) -> list[str]:
        calls: list[str] = []
        self._walk_calls(node, src, calls)
        seen: set[str] = set()
        unique: list[str] = []
        for c in calls:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return unique

    def _extract_callback_refs(
        self,
        node: Node,
        src: bytes,
    ) -> list[tuple[str, str]]:
        """Extract (callee_name, context) for function references passed as arguments."""
        refs: list[tuple[str, str]] = []
        self._walk_callback_refs(node, src, refs)
        # Deduplicate while preserving order
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for r in refs:
            if r not in seen:
                seen.add(r)
                unique.append(r)
        return unique

    _JSX_EVENT_PREFIX = "on"

    def _walk_callback_refs(
        self,
        node: Node,
        src: bytes,
        out: list[tuple[str, str]],
    ) -> None:
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if func_node and args_node:
                method_name = self._get_method_name(func_node, src)
                context = self._classify_callback_context(method_name)
                if context:
                    self._collect_callback_args(args_node, src, context, out)
        # JSX attributes: <Component onClick={handler} /> -> PASSED_TO
        if node.type == "jsx_attribute":
            self._collect_jsx_callback(node, src, out)
        for child in node.children:
            self._walk_callback_refs(child, src, out)

    def _collect_jsx_callback(
        self,
        attr_node: Node,
        src: bytes,
        out: list[tuple[str, str]],
    ) -> None:
        """Extract callback from JSX event handler attributes (e.g. onClick={handler})."""
        named = attr_node.named_children
        if len(named) < 2:
            return
        # First named child is property_identifier, second is jsx_expression
        name_node = named[0]
        if name_node.type != "property_identifier":
            return
        attr_name = self._text(name_node, src)
        if not attr_name:
            return
        # Only treat on* attributes as callbacks (onClick, onChange, onSubmit, etc.)
        if not (attr_name.startswith(self._JSX_EVENT_PREFIX) and len(attr_name) > 2 and attr_name[2].isupper()):
            return
        value_node = named[1]
        if value_node.type != "jsx_expression":
            return
        # The expression inside {}: look for identifier or member_expression
        for child in value_node.named_children:
            if child.type in ("identifier", "member_expression"):
                name = self._resolve_call_name(child, src)
                if name:
                    out.append((name, "jsx_callback"))

    def _get_method_name(self, func_node: Node, src: bytes) -> str | None:
        """Extract the method name from a call expression's function node."""
        if func_node.type == "member_expression":
            prop = func_node.child_by_field_name("property")
            if prop:
                return self._text(prop, src)
        if func_node.type == "identifier":
            return self._text(func_node, src)
        return None

    def _classify_callback_context(self, method_name: str | None) -> str | None:
        """Return the callback context for a known method, or None."""
        if not method_name:
            return None
        if method_name in self._MIDDLEWARE_METHODS:
            return "middleware"
        if method_name in self._ROUTE_METHODS:
            return "route_handler"
        if method_name in self._EVENT_METHODS:
            return "callback"
        if method_name in self._PROMISE_METHODS:
            return "callback"
        if method_name in self._ARRAY_METHODS:
            return "array_method"
        return None

    def _collect_callback_args(
        self,
        args_node: Node,
        src: bytes,
        context: str,
        out: list[tuple[str, str]],
    ) -> None:
        """Collect identifier/member_expression arguments as callback refs."""
        for child in args_node.named_children:
            if child.type in ("identifier", "member_expression"):
                name = self._resolve_call_name(child, src)
                if name:
                    out.append((name, context))

    def _walk_calls(self, node: Node, src: bytes, out: list[str]) -> None:
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node:
                call_name = self._resolve_call_name(func_node, src)
                if call_name:
                    out.append(call_name)
        # JSX elements are component calls
        if node.type in ("jsx_self_closing_element", "jsx_opening_element"):
            tag = node.child_by_field_name("name")
            if tag:
                tag_text = self._text(tag, src)
                # Only capture PascalCase (custom components), not HTML tags
                if tag_text and tag_text[0].isupper():
                    out.append(tag_text)
        for child in node.children:
            self._walk_calls(child, src, out)

    def _resolve_call_name(self, node: Node, src: bytes) -> str | None:
        if node.type == "identifier":
            return self._text(node, src)
        if node.type == "member_expression":
            obj = node.child_by_field_name("object")
            prop = node.child_by_field_name("property")
            if obj and prop:
                obj_name = self._resolve_call_name(obj, src)
                prop_name = self._text(prop, src)
                if obj_name:
                    return f"{obj_name}.{prop_name}"
                return prop_name
        return None

    def _extract_call_arg_refs(self, node: Node, src: bytes) -> list[str]:
        """Extract call descriptors that include positional identifier arguments.

        For ``db.insert(chat)`` this records ``"db.insert(chat)"`` so the schema
        linker can see both the access verb and the model passed as an argument.
        :meth:`_extract_calls` keeps only the callee name, dropping args.
        """
        refs: list[str] = []
        self._walk_call_arg_refs(node, src, refs)
        seen: set[str] = set()
        unique: list[str] = []
        for r in refs:
            if r not in seen:
                seen.add(r)
                unique.append(r)
        return unique

    # Drizzle select-builders whose table arrives via a later `.from(table)` call.
    _DRIZZLE_SELECT_METHODS = frozenset({"select", "selectDistinct", "selectDistinctOn"})

    # PostgREST/Supabase table verbs chained after `.from('table')`.
    _POSTGREST_VERBS = frozenset({"select", "insert", "update", "upsert", "delete"})

    def _walk_call_arg_refs(self, node: Node, src: bytes, out: list[str]) -> None:
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if func_node and args_node:
                arg_idents = self._positional_arg_idents(args_node, src)
                if arg_idents:
                    # Prefer the synthetic select-chain name so Drizzle reads carry
                    # a read verb; otherwise the plain callee name.
                    call_name = self._select_from_chain_name(func_node, src) or self._resolve_call_name(func_node, src)
                    if call_name:
                        out.append(f"{call_name}({','.join(arg_idents)})")
                supabase_ref = self._supabase_from_descriptor(func_node, src)
                if supabase_ref:
                    out.append(supabase_ref)
                rpc_ref = self._supabase_rpc_descriptor(func_node, args_node, src)
                if rpc_ref:
                    out.append(rpc_ref)
        for child in node.children:
            self._walk_call_arg_refs(child, src, out)

    def _supabase_rpc_descriptor(self, func_node: Node, args_node: Node, src: bytes) -> str | None:
        """For Supabase ``X.rpc('fn', {...})``, return ``"rpc('fn')"`` — a call to a
        Postgres stored function whose name is the first string-literal argument.

        No receiver check is needed: precision comes from the schema linker, which
        only creates an edge when ``'fn'`` matches a DBFunction declared in the
        Supabase generated types. A stray ``x.rpc('foo')`` on some other object
        won't match unless ``foo`` is a real declared function.
        """
        if func_node.type != "member_expression":
            return None
        prop = func_node.child_by_field_name("property")
        if prop is None or self._text(prop, src) != "rpc":
            return None
        named = args_node.named_children
        if not named or named[0].type != "string":
            return None
        fragments = [c for c in named[0].children if c.type == "string_fragment"]
        if len(fragments) != 1:
            return None
        name = self._text(fragments[0], src)
        if not name:
            return None
        return f"rpc('{name}')"

    def _supabase_from_descriptor(self, func_node: Node, src: bytes) -> str | None:
        """For Supabase/PostgREST ``X.from('table').verb(...)``, return
        ``"<verb>.from('table')"`` — quotes kept so the schema linker can tell a
        string-literal table access from a Drizzle identifier descriptor.

        Fires only when the table is a plain string literal and the verb chained
        directly after ``.from()`` is a PostgREST table verb. The receiver is
        checked so ``supabase.storage.from('bucket')`` (a storage bucket, not a
        table) and ``Buffer.from(...)``/``Array.from(...)`` never match.
        """
        if func_node.type != "member_expression":
            return None
        verb_node = func_node.child_by_field_name("property")
        verb = self._text(verb_node, src) if verb_node else None
        if verb not in self._POSTGREST_VERBS:
            return None
        from_call = func_node.child_by_field_name("object")
        if from_call is None or from_call.type != "call_expression":
            return None
        from_fn = from_call.child_by_field_name("function")
        if from_fn is None or from_fn.type != "member_expression":
            return None
        from_prop = from_fn.child_by_field_name("property")
        if from_prop is None or self._text(from_prop, src) != "from":
            return None
        receiver = from_fn.child_by_field_name("object")
        receiver_text = self._text(receiver, src) if receiver else None
        if not receiver_text:
            return None
        # `const storage = supabase.storage` is the common idiom, so a bare
        # `storage` receiver is treated as the storage API too.
        if receiver_text in ("Buffer", "Array") or receiver_text.rsplit(".", 1)[-1] == "storage":
            return None
        args_node = from_call.child_by_field_name("arguments")
        if args_node is None:
            return None
        named = args_node.named_children
        if len(named) != 1 or named[0].type != "string":
            return None
        fragments = [c for c in named[0].children if c.type == "string_fragment"]
        if len(fragments) != 1:
            return None
        table = self._text(fragments[0], src)
        if not table:
            return None
        return f"{verb}.from('{table}')"

    def _select_from_chain_name(self, func_node: Node, src: bytes) -> str | None:
        """For Drizzle ``db.select().from(table)``, return ``"select.from"``.

        The table is the argument to ``.from()`` but the read verb (``select``)
        lives in an earlier call of the chain, so a plain callee name would lose
        it. Fires only when ``.from()`` is called directly on a select-builder
        call — ``Array.from(x)`` and other ``.from()`` uses are not matched.
        """
        if func_node.type != "member_expression":
            return None
        prop = func_node.child_by_field_name("property")
        if not prop or self._text(prop, src) != "from":
            return None
        obj = func_node.child_by_field_name("object")
        if not obj or obj.type != "call_expression":
            return None
        inner_fn = obj.child_by_field_name("function")
        if inner_fn and self._get_method_name(inner_fn, src) in self._DRIZZLE_SELECT_METHODS:
            return "select.from"
        return None

    def _positional_arg_idents(self, args_node: Node, src: bytes) -> list[str]:
        """Return the text of positional identifier/member-expression arguments."""
        idents: list[str] = []
        for child in args_node.named_children:
            if child.type in ("identifier", "member_expression"):
                text = self._text(child, src)
                if text:
                    idents.append(text)
        return idents

    # ------------------------------------------------------------------
    # Error flow (throw)
    # ------------------------------------------------------------------

    def _extract_error_flow(self, node: Node, src: bytes) -> tuple[list[str], list[str]]:
        """Return (thrown, caught) exception type names. Only ``throw new X()``
        yields a type; JS/TS ``catch`` clauses can't name an exception type, so
        the caught list is always empty (kept for symmetry with Python)."""
        raises: list[str] = []
        self._walk_throws(node, src, raises)
        return list(dict.fromkeys(raises)), []

    def _walk_throws(self, node: Node, src: bytes, raises: list[str]) -> None:
        if node.type == "throw_statement":
            for child in node.named_children:
                if child.type == "new_expression":
                    ctor = child.child_by_field_name("constructor")
                    name = self._exception_ctor_name(ctor, src) if ctor is not None else None
                    if name:
                        raises.append(name)
                break  # only the thrown expression
        for child in node.children:
            self._walk_throws(child, src, raises)

    def _exception_ctor_name(self, node: Node, src: bytes) -> str | None:
        if node.type == "identifier":
            return self._text(node, src)
        if node.type == "member_expression":
            prop = node.child_by_field_name("property")
            return self._text(prop, src) if prop is not None else None
        return None

    def _has_try(self, node: Node) -> bool:
        """True if the subtree contains a ``try`` statement (any error handling)."""
        if node.type == "try_statement":
            return True
        return any(self._has_try(child) for child in node.children)

    # ------------------------------------------------------------------
    # JSDoc extraction
    # ------------------------------------------------------------------

    def _extract_jsdoc(self, node: Node, src: bytes) -> str | None:
        """Look for a JSDoc comment immediately before the node."""
        if node.prev_named_sibling and node.prev_named_sibling.type == "comment":
            text = self._text(node.prev_named_sibling, src)
            if text.startswith("/**"):
                # Strip /** ... */ and leading *
                lines = text.split("\n")
                cleaned = []
                for line in lines:
                    line = line.strip()
                    line = line.lstrip("/*").rstrip("*/").strip()
                    if line:
                        cleaned.append(line)
                return " ".join(cleaned) if cleaned else None
        return None

    def _extract_module_docstring(self, root: Node, src: bytes) -> str | None:
        """Extract the leading comment block from a file as the module description.

        Looks for:
        - A JSDoc block (/** ... */) before any code, preferring @module/@fileoverview tags
        - A leading // comment block before any code
        - Skips license/copyright headers

        Truncates to 200 characters.
        """
        for child in root.children:
            if child.type == "comment":
                text = self._text(child, src)
                # Skip license/copyright headers
                lower = text.lower()
                if "license" in lower or "copyright" in lower:
                    continue
                if text.startswith("/**"):
                    # JSDoc block — extract meaningful content
                    cleaned = self._clean_jsdoc_for_module(text)
                    if cleaned:
                        return cleaned[:200]
                elif text.startswith("//"):
                    # Single-line comment block — take the first meaningful line
                    cleaned = text.lstrip("/").strip()
                    if cleaned:
                        return cleaned[:200]
            elif child.type in ("import_statement", "export_statement"):
                # Imports/exports before comments are fine — keep looking
                continue
            else:
                # Hit actual code — stop looking
                break
        return None

    @staticmethod
    def _clean_jsdoc_for_module(text: str) -> str | None:
        """Clean a JSDoc comment for use as a module description."""
        lines = text.split("\n")
        cleaned: list[str] = []
        for line in lines:
            line = line.strip().lstrip("/*").rstrip("*/").strip()
            if not line:
                continue
            # Prefer @module or @fileoverview content
            for tag in ("@module", "@fileoverview"):
                if line.startswith(tag):
                    content = line[len(tag) :].strip()
                    if content:
                        return content
            # Skip other JSDoc tags
            if line.startswith("@"):
                continue
            cleaned.append(line)
        return " ".join(cleaned) if cleaned else None

    # ------------------------------------------------------------------
    # Complexity
    # ------------------------------------------------------------------

    def _cyclomatic_complexity(self, node: Node) -> int:
        decision_types = {
            "if_statement",
            "else_clause",
            "for_statement",
            "for_in_statement",
            "while_statement",
            "do_statement",
            "switch_case",
            "catch_clause",
            "ternary_expression",
            "binary_expression",  # counted only for && and ||
        }
        count = 1
        count += self._count_decisions(node, decision_types)
        return count

    def _count_decisions(self, node: Node, types: set[str]) -> int:
        count = 0
        if node.type in types:
            if node.type == "binary_expression":
                op = node.child_by_field_name("operator")
                if op and self._text(op, node.text or b"") in ("&&", "||"):
                    count = 1
            else:
                count = 1
        for child in node.children:
            count += self._count_decisions(child, types)
        return count

    # ------------------------------------------------------------------
    # Component detection
    # ------------------------------------------------------------------

    def _body_contains_jsx(self, root: Node, src: bytes, func: ParsedFunction) -> bool:
        """Check if a function body contains JSX return statements."""
        # Find the function's AST node by line range and check for JSX
        for node in self._iter_descendants(root):
            if node.type in ("return_statement", "parenthesized_expression"):
                line = node.start_point[0] + 1
                if func.start_line <= line <= func.end_line and self._subtree_has_jsx(node):
                    return True
            # Arrow functions with implicit JSX return
            if node.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
                line = node.start_point[0] + 1
                if func.start_line <= line <= func.end_line:
                    return True
        return False

    @staticmethod
    def _subtree_has_jsx(node: Node) -> bool:
        if node.type in ("jsx_element", "jsx_self_closing_element", "jsx_fragment"):
            return True
        return any(TypeScriptParser._subtree_has_jsx(child) for child in node.children)

    @staticmethod
    def _iter_descendants(node: Node):
        yield node
        for child in node.children:
            yield from TypeScriptParser._iter_descendants(child)

    # ------------------------------------------------------------------
    # Entry point detection
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_entry_point(func: ParsedFunction, file_path: str) -> str | None:
        """Classify a function's entry point reason, or return None if not an entry point.

        Returns the entry_point_reason string.
        """
        # Next.js page/route/layout exports (check before generic react_component)
        if _NEXTJS_PAGE_RE.search(file_path):
            if func.is_exported and func.name in (
                "default",
                "GET",
                "POST",
                "PUT",
                "DELETE",
                "PATCH",
                "HEAD",
                "OPTIONS",
                "generateMetadata",
                "generateStaticParams",
            ):
                return "nextjs_page"
            # Default exported component in a page file
            if func.is_exported and func.name and func.name[0].isupper():
                return "nextjs_page"

        # Storybook stories (check before generic react_component)
        if _STORYBOOK_RE.search(file_path) and func.is_exported:
            return "storybook_story"

        # React components — entry points by convention
        if func.is_component:
            return "react_component"

        # Serverless handler exports (AWS Lambda convention)
        if func.is_exported and func.name in _SERVERLESS_HANDLER_NAMES:
            return "serverless_handler"

        # Express/Hono-style route handler decorators
        for dec in func.decorators:
            if any(m in dec.lower() for m in ("get", "post", "put", "delete", "route")):
                return "route_handler"

        # Exported hooks in barrel files only
        # use* prefixed functions re-exported from index.{ts,js} are consumed by convention
        if (
            func.is_exported
            and func.name.startswith("use")
            and len(func.name) > 3
            and func.name[3].isupper()
            and re.search(r"(?:^|/)index\.[tj]sx?$", file_path)
        ):
            return "react_hook"

        # main() function
        if func.name == "main" and func.is_exported:
            return "main"

        return None

    def _detect_serve_entry_points(self, root: Node, src: bytes, file_path: str) -> set[str]:
        """Return the names of functions that are entry points via a Deno/Supabase
        edge-function handler.

        Gated to Supabase edge-function files — the handler idioms
        (``serve``-prefixed calls, ``export default { fetch }``,
        ``addEventListener``) also appear in ordinary app code (a default-exported
        component, an HOC call, a Cloudflare Worker), and marking those as entry
        points would corrupt dead-export/impact analysis repo-wide.

        If the handler is an inline arrow (``serve(async (req) => …)``,
        ``export default { fetch: withSupabase(opts, async (req) => …) }``, a
        wrapper such as ``serveWithInstrumentation('name', async (req) => …)``),
        the *named functions it calls* are the entry points. If the handler is a
        named reference (``serve(handleRequest)``), that name is the entry point.
        See :meth:`_find_edge_handler`.
        """
        if _supabase_edge_function_name(file_path) is None:
            return set()
        handler_node, handler_ref = self._find_edge_handler(root, src)
        callees: set[str] = set()
        if handler_node is not None:
            self._collect_call_names(handler_node, src, callees)
        elif handler_ref:
            callees.add(handler_ref)
        return callees

    @staticmethod
    def _collect_call_names(node: Node, src: bytes, out: set[str]) -> None:
        """Recursively collect function call names from a subtree."""
        if node.type == "call_expression":
            func_node = node.child_by_field_name("function")
            if func_node and func_node.type == "identifier":
                out.add(src[func_node.start_byte : func_node.end_byte].decode(errors="replace"))
        for child in node.children:
            TypeScriptParser._collect_call_names(child, src, out)

    # ------------------------------------------------------------------
    # Supabase edge function handler + route extraction
    # ------------------------------------------------------------------
    #
    # Registration idioms recognised (all top-level in
    # supabase/functions/<name>/index.ts):
    #   serve(handler) / Deno.serve(handler)              - Deno std / native
    #   serveWithInstrumentation('name', handler)         - any wrapper whose name starts "serve"
    #   export default { fetch: handler | wrapper(...) }  - modern default-export form
    #   addEventListener('fetch', handler)                - service-worker form
    # The handler is either an inline arrow (synthesised into a Function so its
    # calls chain to the DB) or a named reference (linked by name in Phase 2).

    def _find_edge_handler(self, root: Node, src: bytes) -> tuple[Node | None, str | None]:
        """Locate a Supabase edge function's request handler from its top-level
        registration. Returns ``(inline_handler_node, named_handler)`` with
        exactly one set, or ``(None, None)`` if no registration is recognised.
        Scans only top-level statements — edge functions register at module scope.
        """
        for node in root.named_children:
            # export default { fetch: <handler> }  |  export default <handler>
            if node.type == "export_statement":
                for c in node.named_children:
                    if c.type == "object":
                        val = self._object_pair_value(c, "fetch", src)
                        if val is not None:
                            found = self._unwrap_handler(val, src)
                            if found != (None, None):
                                return found
                    elif c.type in (
                        "arrow_function",
                        "function_expression",
                        "function",
                        "call_expression",
                        "identifier",
                    ):
                        found = self._unwrap_handler(c, src)
                        if found != (None, None):
                            return found
                continue

            call = self._statement_call(node)
            if call is None:
                continue
            name = self._callee_name(call.child_by_field_name("function"), src)
            if name and name.lower().startswith("serve"):
                node2, ref2 = self._unwrap_handler(call, src)
                if node2 is not None:
                    return (node2, None)  # inline handler arg — trust any serve* wrapper
                # A named handler is trusted for the classic serve()/Deno.serve()
                # forms, or a wrapper keyed by a function-name string
                # (serveWithInstrumentation('name', handleRequest)) — but not a bare
                # `server(app)` / `serveData(collectMetrics)` whose first arg is the
                # value itself, nor `serveStatic('/x')` (no handler arg at all).
                if ref2 is not None and (name == "serve" or self._first_arg_is_string(call, src)):
                    return (None, ref2)
            elif name == "addEventListener":
                handler = self._event_listener_handler(call, "fetch", src)
                if handler is not None:
                    return self._unwrap_handler(handler, src)
        return (None, None)

    @staticmethod
    def _unwrap_to_call(node: Node) -> Node | None:
        """A call_expression, unwrapping a leading ``await``."""
        if node.type == "call_expression":
            return node
        if node.type == "await_expression":
            for c in node.named_children:
                if c.type == "call_expression":
                    return c
        return None

    @staticmethod
    def _statement_call(node: Node) -> Node | None:
        """The call_expression of a top-level statement, if any — handling a bare
        call, ``await serve(...)``, and ``const s = Deno.serve(...)``."""
        direct = TypeScriptParser._unwrap_to_call(node)
        if direct is not None:
            return direct
        if node.type == "expression_statement":
            for child in node.named_children:
                call = TypeScriptParser._unwrap_to_call(child)
                if call is not None:
                    return call
        if node.type == "lexical_declaration":
            for decl in node.named_children:
                if decl.type == "variable_declarator":
                    value = decl.child_by_field_name("value")
                    if value is not None:
                        call = TypeScriptParser._unwrap_to_call(value)
                        if call is not None:
                            return call
        return None

    def _callee_name(self, callee: Node | None, src: bytes) -> str | None:
        """Rightmost identifier of a call target: ``serve`` / ``Deno.serve`` → 'serve'."""
        if callee is None:
            return None
        if callee.type == "identifier":
            return self._text(callee, src)
        if callee.type == "member_expression":
            prop = callee.child_by_field_name("property")
            return self._text(prop, src) if prop else None
        return None

    # Argument handler types (an arg is never a method_definition). The inline set
    # additionally covers `method_definition` for the `{ async fetch(req){} }` form.
    _HANDLER_FN_TYPES = ("arrow_function", "function_expression", "function")
    _INLINE_HANDLER_TYPES = ("arrow_function", "function_expression", "function", "method_definition")

    def _unwrap_handler(self, expr: Node, src: bytes) -> tuple[Node | None, str | None]:
        """Resolve a handler expression to ``(inline_node, named)``.

        An arrow/function/method is the handler itself; a call contributes its
        handler argument — the *last function-typed* arg, since the handler is not
        always the final argument (``withSupabase(opts, fn)`` puts it last but
        ``serveWithInstrumentation('name', fn, {options})`` puts it in the
        middle) — or, for a config-object call (``serve({ fetch: fn })``), the
        object's ``fetch`` value; falling back to the last identifier/member arg (a
        named handler reference, ``serve(handleRequest)``). A bare
        identifier/member/shorthand is itself a named handler reference.
        """
        if expr.type in self._INLINE_HANDLER_TYPES:
            return (expr, None)
        if expr.type == "call_expression":
            args = expr.child_by_field_name("arguments")
            named = args.named_children if args else []
            for cand in reversed(named):
                if cand.type in self._HANDLER_FN_TYPES:
                    return (cand, None)
            for cand in reversed(named):
                if cand.type == "object":
                    val = self._object_pair_value(cand, "fetch", src)
                    if val is not None:
                        return self._unwrap_handler(val, src)
            for cand in reversed(named):
                if cand.type in ("identifier", "member_expression"):
                    return (None, self._text(cand, src))
            return (None, None)
        if expr.type in ("identifier", "member_expression", "shorthand_property_identifier"):
            return (None, self._text(expr, src))
        return (None, None)

    def _object_pair_value(self, obj: Node, key: str, src: bytes) -> Node | None:
        """The handler node for ``<key>`` in an object literal, across the three
        member forms: ``{ fetch: fn }`` (pair → value), ``{ async fetch(){} }``
        (method_definition → the method itself), and ``{ fetch }`` (shorthand →
        the shorthand identifier, a named ref). Returns None if absent.
        """
        for member in obj.named_children:
            if member.type == "pair":
                k = member.child_by_field_name("key")
                if k is not None and self._object_key_text(k, src) == key:
                    return member.child_by_field_name("value")
            elif member.type == "method_definition":
                name = member.child_by_field_name("name")
                if name is not None and self._text(name, src) == key:
                    return member
            elif member.type == "shorthand_property_identifier":
                if self._text(member, src) == key:
                    return member
        return None

    def _object_key_text(self, key: Node, src: bytes) -> str | None:
        """Property-key text, unwrapping a computed key (``['fetch']`` → 'fetch')."""
        if key.type == "computed_property_name":
            for c in key.named_children:
                if c.type == "string":
                    return self._string_value(c, src)
            return None
        return self._text(key, src).strip("'\"")

    def _first_arg_is_string(self, call: Node, src: bytes) -> bool:
        """True when the call's first argument is a string literal (a wrapper keyed
        by a function-name string, e.g. ``serveWithInstrumentation('name', fn)``)."""
        args = call.child_by_field_name("arguments")
        named = args.named_children if args else []
        return bool(named) and named[0].type == "string"

    def _event_listener_handler(self, call: Node, event: str, src: bytes) -> Node | None:
        """Second arg of ``addEventListener('<event>', handler)``, else None."""
        args = call.child_by_field_name("arguments")
        named = args.named_children if args else []
        if len(named) >= 2 and named[0].type == "string" and self._string_value(named[0], src) == event:
            return named[1]
        return None

    @staticmethod
    def _string_value(node: Node, src: bytes) -> str | None:
        """Text of a single-fragment string literal (``'fetch'`` → 'fetch')."""
        fragments = [c for c in node.children if c.type == "string_fragment"]
        if len(fragments) != 1:
            return None
        return src[fragments[0].start_byte : fragments[0].end_byte].decode(errors="replace")

    def _is_router_delegate(self, handler_ref: str) -> bool:
        """True when a named handler delegates to a framework app rather than being
        a real function — ``app.fetch`` (a member reference) or a bare router
        instance (``export default app`` where ``app`` is a detected Hono/Express
        router). Such a handler can never resolve to a Function, and the app's own
        routes are the real endpoints, so the directory envelope is skipped.
        """
        base = handler_ref.split(".")[0]
        return "." in handler_ref or base in getattr(self, "_dynamic_router_names", set())

    def _extract_supabase_routes(self, root: Node, src: bytes, file_path: str) -> list[ParsedRoute]:
        """Synthesize a route for a Supabase edge function.

        Edge functions live at ``supabase/functions/<name>/index.ts`` and are
        deployed at ``/<name>`` (invoked via POST) — the directory IS the route,
        regardless of how the handler is registered inside. An inline handler
        arrow is synthesised into a Function (reusing the inline-handler
        machinery) so route → handler → …calls… → Model resolves; a named handler
        is linked by name in Phase 2. Dirs starting with ``_`` are Supabase's
        shared code, never deployed, so they are skipped.

        When the handler delegates to a framework app (``Deno.serve(app.fetch)``,
        ``export default app``), the app's own ``app.get(...)`` routes are the
        real endpoints, so the directory envelope is skipped to avoid a dangling
        or duplicate route.
        """
        func_name = _supabase_edge_function_name(file_path)
        if func_name is None:
            return []

        handler_node, handler_ref = self._find_edge_handler(root, src)
        if handler_node is not None:
            synth = self._synthesize_arrow_handler(handler_node, src, "POST", f"/{func_name}", file_path)
            self._synth_route_handlers.append(synth)
            handler_name = synth.name
            line = handler_node.start_point[0] + 1
            end_line = handler_node.end_point[0] + 1
        elif handler_ref and not self._is_router_delegate(handler_ref):
            handler_name, line, end_line = handler_ref, 1, 0
        else:
            # No handler, or a delegate to a framework app (member ref like
            # app.fetch, or a bare router instance). Stay precise — emit no
            # envelope route; the app's own routes (if any) already cover it.
            return []

        return [
            ParsedRoute(
                method="POST",
                path=f"/{func_name}",
                handler_name=handler_name,
                file_path=file_path,
                line=line,
                end_line=end_line,
            )
        ]

    # ------------------------------------------------------------------
    # TODO/FIXME extraction
    # ------------------------------------------------------------------

    def _extract_todos(self, root: Node, src: bytes) -> list[str]:
        """Extract TODO/FIXME/HACK comments from the AST."""
        todos: list[str] = []
        for node in self._iter_descendants(root):
            if node.type == "comment":
                text = self._text(node, src)
                m = _TODO_RE.search(text)
                if m:
                    tag = m.group(1).upper()
                    msg = m.group(2).strip().rstrip("*/").strip()
                    line = node.start_point[0] + 1
                    todos.append(f"{tag}(L{line}): {msg}" if msg else f"{tag}(L{line})")
        return todos

    # ------------------------------------------------------------------
    # Route extraction
    # ------------------------------------------------------------------

    def _extract_routes(self, root: Node, src: bytes, file_path: str) -> list[ParsedRoute]:
        """Extract HTTP route definitions from Express/Hono/Fastify patterns."""
        routes: list[ParsedRoute] = []

        # Detect router-like variable names by scanning for framework constructors
        # e.g. const health = new Hono<AppEnv>()  →  "health" is a router object
        self._dynamic_router_names = self._detect_router_variables(root, src)
        # Inline arrow/function handlers synthesized during this call; drained by
        # parse_file into ParsedFile.functions so they become Function nodes.
        self._synth_route_handlers: list[ParsedFunction] = []

        # Next.js app router convention: file path IS the route
        if _NEXTJS_PAGE_RE.search(file_path):
            route_path = self._nextjs_route_from_path(file_path)
            if route_path:
                # Check for route.ts (API route) with named exports
                if "/route." in file_path:
                    for node in root.children:
                        func = self._try_get_exported_func_name(node, src)
                        if func and func.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH", "HEAD", "OPTIONS"):
                            routes.append(
                                ParsedRoute(
                                    method=func.upper(),
                                    path=route_path,
                                    handler_name=func,
                                    file_path=file_path,
                                    line=node.start_point[0] + 1,
                                )
                            )
                else:
                    # Page component — represents a GET route
                    routes.append(
                        ParsedRoute(
                            method="GET",
                            path=route_path,
                            handler_name="default",
                            file_path=file_path,
                            line=1,
                        )
                    )

        # Express/Hono-style: router.get('/path', handler) or app.post('/path', ...)
        for node in self._iter_descendants(root):
            if node.type == "call_expression":
                route = self._try_parse_route_call(node, src, file_path)
                if route:
                    routes.append(route)

        # Supabase edge function convention: the directory IS the endpoint. Always
        # attempt it (a stray false framework route in the body must not suppress
        # the real edge route); _extract_supabase_routes itself skips the envelope
        # when the handler delegates to an internal Hono/Express app.
        routes.extend(self._extract_supabase_routes(root, src, file_path))

        # NestJS: @Controller(base) classes with @Get/@Post/... decorated methods
        routes.extend(self._extract_nestjs_routes(root, src, file_path))

        return routes

    _HTTP_VERB_DECORATORS = frozenset({"Get", "Post", "Put", "Delete", "Patch", "All", "Options", "Head"})

    @staticmethod
    def _join_route(base: str, sub: str) -> str:
        parts = [p.strip("/") for p in (base, sub) if p and p.strip("/")]
        return "/" + "/".join(parts) if parts else "/"

    def _extract_nestjs_routes(self, root: Node, src: bytes, file_path: str) -> list[ParsedRoute]:
        """Synthesize routes from NestJS @Controller classes + @Get/@Post methods."""
        routes: list[ParsedRoute] = []
        for node in self._iter_descendants(root):
            if node.type not in ("class_declaration", "abstract_class_declaration"):
                continue
            base: str | None = None
            for deco in self._extract_decorators(node, src):
                if deco.split("(", 1)[0].strip() == "Controller":
                    arg = _DECORATOR_ARG_RE.search(deco)
                    base = arg.group(1) if arg else ""
                    break
            if base is None:
                continue  # not a NestJS controller
            body = node.child_by_field_name("body")
            if body is None:
                continue
            for child in body.children:
                if child.type != "method_definition":
                    continue
                for deco in self._extract_decorators(child, src):
                    verb = deco.split("(", 1)[0].strip()
                    if verb not in self._HTTP_VERB_DECORATORS:
                        continue
                    arg = _DECORATOR_ARG_RE.search(deco)
                    sub = arg.group(1) if arg else ""
                    name_node = child.child_by_field_name("name")
                    routes.append(
                        ParsedRoute(
                            method="ALL" if verb == "All" else verb.upper(),
                            path=self._join_route(base, sub),
                            handler_name=self._text(name_node, src) if name_node else "",
                            file_path=file_path,
                            line=child.start_point[0] + 1,
                            end_line=child.end_point[0] + 1,
                        )
                    )
                    break  # one HTTP verb per method
        return routes

    # Auth keywords for detecting auth middleware in .use() calls
    _AUTH_MW_KEYWORDS = frozenset(
        {
            "auth",
            "login",
            "permission",
            "protect",
            "jwt",
            "token",
            "verify",
            "guard",
            "session",
            "bearer",
            "oauth",
            "apikey",
            "api_key",
            "credentials",
            "clerk",
            "passport",
            "unkey",
        }
    )

    def _extract_auth_middleware_paths(self, root: Node, src: bytes) -> list[str]:
        """Extract path patterns guarded by auth middleware via .use() calls.

        Detects patterns like:
        - ``app.use('/api/admin/*', clerkAuth)``
        - ``v1.use('*', unkeyVerify, appConfigLoader)``
        - ``admin.use('*', adminAuth)``

        Returns list of path patterns (e.g. ``['/api/admin/*', '*']``).
        """
        router_objects = {"app", "router", "server", "route", "api", "blueprint"}
        dynamic: set[str] = getattr(self, "_dynamic_router_names", set())
        paths: list[str] = []

        for node in self._iter_descendants(root):
            if node.type != "call_expression":
                continue
            func_node = node.child_by_field_name("function")
            if not func_node or func_node.type != "member_expression":
                continue

            # Must be .use() method
            prop = func_node.child_by_field_name("property")
            if not prop or self._text(prop, src) != "use":
                continue

            # Object must be a router-like variable
            obj = func_node.child_by_field_name("object")
            if not obj:
                continue
            obj_text = self._text(obj, src).lower()
            obj_base = obj_text.split(".")[-1] if "." in obj_text else obj_text
            if obj_base not in router_objects and obj_base not in dynamic:
                continue

            args = node.child_by_field_name("arguments")
            if not args:
                continue

            # Parse arguments: first string is path, rest are middleware names
            path_pattern: str | None = None
            mw_names: list[str] = []

            for child in args.children:
                if child.type in (",", "(", ")"):
                    continue
                if path_pattern is None and child.type in ("string", "template_string"):
                    path_pattern = self._text(child, src).strip("'\"`")
                else:
                    name = self._resolve_call_name(child, src)
                    if name:
                        mw_names.append(name)

            if not path_pattern or not mw_names:
                continue

            # Check if any middleware name contains auth keywords
            for mw in mw_names:
                mw_lower = mw.lower()
                if any(kw in mw_lower for kw in self._AUTH_MW_KEYWORDS):
                    paths.append(path_pattern)
                    break

        return paths

    def _detect_router_variables(self, root: Node, src: bytes) -> set[str]:
        """Detect variable names assigned from router/app framework constructors.

        Scans for patterns like:
        - ``const health = new Hono<AppEnv>()``
        - ``const api = express()``
        - ``const app = new Fastify()``
        """
        router_constructors = {"hono", "express", "fastify", "koa", "router"}
        names: set[str] = set()

        for node in root.children:
            # const/let/var declarations
            if node.type in ("lexical_declaration", "variable_declaration"):
                for decl in node.children:
                    if decl.type != "variable_declarator":
                        continue
                    name_node = decl.child_by_field_name("name")
                    value_node = decl.child_by_field_name("value")
                    if not name_node or not value_node:
                        continue

                    # Check for new Hono(), new Fastify(), etc.
                    if value_node.type == "new_expression":
                        constructor = value_node.child_by_field_name("constructor")
                        if constructor:
                            ctor_text = self._text(constructor, src).split("<")[0].lower()
                            if ctor_text in router_constructors:
                                names.add(self._text(name_node, src).lower())

                    # Check for express(), Router(), etc.
                    elif value_node.type == "call_expression":
                        func = value_node.child_by_field_name("function")
                        if func:
                            func_text = self._text(func, src).split(".")[0].lower()
                            if func_text in router_constructors:
                                names.add(self._text(name_node, src).lower())

        return names

    def _try_parse_route_call(self, node: Node, src: bytes, file_path: str) -> ParsedRoute | None:
        """Try to parse a call like router.get('/users', handler)."""
        func_node = node.child_by_field_name("function")
        if not func_node or func_node.type != "member_expression":
            return None

        # Check the object looks like a router/app (not req.query, c.req, etc.)
        obj = func_node.child_by_field_name("object")
        if not obj:
            return None
        obj_text = self._text(obj, src).lower()
        # Only match router-like objects, not request/response objects
        router_objects = {"app", "router", "server", "route", "api", "blueprint"}
        # Include dynamically detected router variables (e.g. const chat = new Hono())
        dynamic: set[str] = getattr(self, "_dynamic_router_names", set())
        obj_base = obj_text.split(".")[-1] if "." in obj_text else obj_text
        if obj_base not in router_objects and obj_base not in dynamic:
            return None

        prop = func_node.child_by_field_name("property")
        if not prop:
            return None

        method_name = self._text(prop, src).lower()
        if method_name not in _ROUTE_METHODS:
            return None

        args = node.child_by_field_name("arguments")
        if not args:
            return None

        # First argument should be the route path (string starting with /)
        path_arg = None
        middleware: list[str] = []
        callback_nodes: list[Node] = []
        arg_index = 0

        for child in args.children:
            if child.type in (",", "(", ")"):
                continue
            if arg_index == 0:
                # Route path — must be a string starting with /
                if child.type in ("string", "template_string"):
                    val = self._text(child, src).strip("'\"`")
                    if val.startswith("/"):
                        path_arg = val
            else:
                callback_nodes.append(child)
            arg_index += 1

        if not path_arg:
            return None

        # The last callback argument is the route handler; earlier callbacks are
        # middleware. An inline arrow/function handler is synthesized into a
        # Function node (the callback is otherwise anonymous and never linked).
        handler_name = None
        if callback_nodes:
            *middleware_nodes, handler_node = callback_nodes
            for mw in middleware_nodes:
                mw_name = self._resolve_call_name(mw, src)
                if mw_name:
                    middleware.append(mw_name)
            if handler_node.type in ("arrow_function", "function_expression", "function"):
                synth = self._synthesize_arrow_handler(handler_node, src, method_name, path_arg, file_path)
                handler_name = synth.name
                self._synth_route_handlers.append(synth)
            else:
                handler_name = self._resolve_call_name(handler_node, src)

        return ParsedRoute(
            method=method_name.upper(),
            path=path_arg,
            handler_name=handler_name or "<anonymous>",
            file_path=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            middleware=middleware,
        )

    def _synthesize_arrow_handler(
        self, node: Node, src: bytes, method: str, path: str, file_path: str
    ) -> ParsedFunction:
        """Create a Function for an inline route-handler callback.

        Express/Hono handlers are usually inline arrows — ``app.get('/x', (c) => …)``
        — that never become Function nodes, so route→handler→callee/model tracing
        dead-ends. This synthesizes a named entry-point Function for the callback,
        capturing the calls it makes, so the route HANDLES a real, traceable node.
        """
        body = node.child_by_field_name("body")
        name = f"{method.upper()} {path}"
        return ParsedFunction(
            name=name,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=f"{name} (inline handler)",
            is_async=self._text(node, src).lstrip().startswith("async"),
            is_entry_point=True,
            entry_point_reason="route_handler",
            visibility="public",
            complexity=self._cyclomatic_complexity(body) if body else 1,
            calls=self._extract_calls(body, src) if body else [],
            calls_with_args=self._extract_call_arg_refs(body, src) if body else [],
        )

    def _try_get_exported_func_name(self, node: Node, src: bytes) -> str | None:
        """Get the name of an exported function."""
        if node.type == "export_statement":
            decl = node.child_by_field_name("declaration")
            if decl:
                if decl.type == "function_declaration":
                    name_node = decl.child_by_field_name("name")
                    return self._text(name_node, src) if name_node else None
                if decl.type == "lexical_declaration":
                    for child in decl.children:
                        if child.type == "variable_declarator":
                            name_node = child.child_by_field_name("name")
                            return self._text(name_node, src) if name_node else None
        return None

    @staticmethod
    def _nextjs_route_from_path(file_path: str) -> str | None:
        """Convert a Next.js file path to its route path."""
        # app/users/[id]/page.tsx -> /users/[id]
        # app/api/users/route.ts -> /api/users
        # pages/about.tsx -> /about
        normalized = file_path.replace("\\", "/")

        # Strip leading dirs up to app/ or pages/
        for prefix in ("app/", "pages/"):
            idx = normalized.find(prefix)
            if idx >= 0:
                normalized = normalized[idx + len(prefix) :]
                break
        else:
            return None

        # Remove the filename (page.tsx, route.ts, index.tsx, etc.)
        parts = normalized.split("/")
        if parts:
            last = parts[-1]
            if last.startswith(("page.", "route.", "layout.", "loading.", "error.", "not-found.")):
                parts = parts[:-1]
            elif "." in last:
                # pages/ dir style: about.tsx -> /about
                parts[-1] = last.rsplit(".", 1)[0]
                if parts[-1] == "index":
                    parts = parts[:-1]

        route = "/" + "/".join(parts)
        # Convert Next.js [param] to :param for readability
        route = re.sub(r"\[\.\.\.(\w+)\]", r"*\1", route)
        route = re.sub(r"\[(\w+)\]", r":\1", route)
        return route if route != "/" or not parts else route

    # ------------------------------------------------------------------
    # Test case extraction (describe/it/test blocks)
    # ------------------------------------------------------------------

    _TEST_BLOCK_NAMES = frozenset({"describe", "it", "test"})

    def _extract_test_cases(self, root: Node, src: bytes, file_path: str) -> list[ParsedTestCase]:
        """Extract describe/it/test blocks from Vitest/Jest/Mocha test files."""
        cases: list[ParsedTestCase] = []
        self._walk_test_blocks(root, src, file_path, None, cases)
        return cases

    def _walk_test_blocks(
        self,
        node: Node,
        src: bytes,
        file_path: str,
        parent_describe: str | None,
        out: list[ParsedTestCase],
    ) -> None:
        """Recursively walk the AST extracting test blocks."""
        for child in node.children:
            if child.type == "expression_statement":
                # The call_expression is a child of the expression_statement
                expr = child.children[0] if child.children else None
                if expr and expr.type == "call_expression":
                    tc = self._try_parse_test_block(expr, src, file_path, parent_describe)
                    if tc:
                        out.append(tc)
                        # If it's a describe block, recurse into its body
                        if tc.block_type == "describe":
                            body = self._get_test_block_body(expr)
                            if body:
                                self._walk_test_blocks(body, src, file_path, tc.name, out)
                        continue
            # Recurse into statement blocks, arrow function bodies, etc.
            if child.type in (
                "statement_block",
                "program",
                "export_statement",
                "lexical_declaration",
                "variable_declarator",
            ):
                self._walk_test_blocks(child, src, file_path, parent_describe, out)

    def _try_parse_test_block(
        self, node: Node, src: bytes, file_path: str, parent_describe: str | None
    ) -> ParsedTestCase | None:
        """Try to parse a call like describe('name', () => { ... })."""
        func = node.child_by_field_name("function")
        if not func:
            return None

        # describe(...), it(...), test(...), or describe.each(...), it.skip(...)
        func_name = ""
        if func.type == "identifier":
            func_name = self._text(func, src)
        elif func.type == "member_expression":
            obj = func.child_by_field_name("object")
            if obj:
                func_name = self._text(obj, src)

        if func_name not in self._TEST_BLOCK_NAMES:
            return None

        args = node.child_by_field_name("arguments")
        if not args:
            return None

        # First argument should be the test description (string)
        name = ""
        for child in args.children:
            if child.type in (",", "(", ")"):
                continue
            if child.type in ("string", "template_string"):
                name = self._text(child, src).strip("'\"`")
                break
            break  # first non-punctuation arg wasn't a string

        if not name:
            return None

        return ParsedTestCase(
            name=name,
            block_type=func_name,
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            parent_describe=parent_describe,
        )

    def _get_test_block_body(self, call_node: Node) -> Node | None:
        """Get the callback body from a describe/it/test call."""
        args = call_node.child_by_field_name("arguments")
        if not args:
            return None
        for child in args.children:
            if child.type in ("arrow_function", "function_expression"):
                return child.child_by_field_name("body")
        return None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _text(node: Node | None, src: bytes) -> str:
        if node is None:
            return ""
        return src[node.start_byte : node.end_byte].decode(errors="replace")

    @staticmethod
    def _find_child(node: Node, child_type: str) -> Node | None:
        for child in node.children:
            if child.type == child_type:
                return child
        return None

    @staticmethod
    def _has_modifier(node: Node, modifier: str) -> bool:
        for child in node.children:
            if child.type == modifier:
                return True
            if child.type == "accessibility_modifier" or child.type == modifier + "_modifier":
                return True
        return False

    @staticmethod
    def _ts_visibility(node: Node, src: bytes) -> str:
        for child in node.children:
            if child.type == "accessibility_modifier":
                text = src[child.start_byte : child.end_byte].decode()
                if text in ("private", "protected", "public"):
                    return text
        return "public"

    @staticmethod
    def _get_first_line(node: Node, src: bytes) -> str:
        text = src[node.start_byte : node.end_byte].decode(errors="replace")
        first_line = text.split("\n")[0].strip()
        return first_line


class JavaScriptParser(LanguageParser):
    """Parses JavaScript and JSX source files.

    Delegates to the TypeScript parser since TS is a superset of JS,
    and tree-sitter-typescript handles JS syntax fine. We just use
    the JS-specific grammar for accuracy.
    """

    def __init__(self) -> None:
        self._js_parser = Parser(Language(ts_js.language()))
        self._ts_parser = TypeScriptParser()

    @property
    def language_name(self) -> str:
        return "javascript"

    @property
    def file_extensions(self) -> list[str]:
        return ["js", "jsx", "mjs", "cjs"]

    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        # Use the JS grammar for parsing, but reuse TS extraction logic
        tree = self._js_parser.parse(content.encode())
        root = tree.root_node
        src = content.encode()

        is_test_file = bool(_TEST_FILE_RE.search(file_path))
        # Delegate to TS parser's parse_file logic but with JS language tag
        # Extract env var references
        from gristle.parsers.env_vars import extract_env_var_refs

        env_var_refs = extract_env_var_refs(content, "javascript")

        # Security pattern detection
        from gristle.parsers.security import (
            detect_hardcoded_secrets,
            detect_llm_output_risks,
            detect_sql_injection,
            detect_unsafe_calls,
        )

        classes = self._ts_parser._extract_classes(root, src, file_path)
        functions = self._ts_parser._extract_module_functions(root, src, file_path)
        # Extract routes now so inline arrow/function handlers can be drained into
        # `functions` (becoming Function nodes) before the security scan below.
        routes = self._ts_parser._extract_routes(root, src, file_path)
        functions.extend(self._ts_parser._synth_route_handlers)

        file_security = (
            detect_hardcoded_secrets(content, "javascript") + detect_sql_injection(content, "javascript")
            if not is_test_file
            else []
        )

        if not is_test_file:
            all_funcs = list(functions)
            for cls in classes:
                all_funcs.extend(cls.methods)
            for func in all_funcs:
                func.security_findings = detect_unsafe_calls(func.calls) + detect_llm_output_risks(func.calls)
            for finding in file_security:
                tag = f"{finding.category}:{finding.detail}"
                for func in all_funcs:
                    if func.start_line <= finding.line <= func.end_line and tag not in func.security_findings:
                        func.security_findings.append(tag)
                        break

        ts_result = ParsedFile(
            path=file_path,
            language="javascript",
            classes=classes,
            functions=functions,
            imports=self._ts_parser._extract_imports(root, src)
            + self._ts_parser._extract_reexports(root, src)
            + self._ts_parser._extract_dynamic_imports(root, src),
            routes=routes,
            test_cases=self._ts_parser._extract_test_cases(root, src, file_path) if is_test_file else [],
            variables=self._ts_parser._extract_module_variables(root, src, file_path),
            module_docstring=self._ts_parser._extract_module_docstring(root, src),
            line_count=content.count("\n") + 1,
            is_test_file=is_test_file,
            todos=self._ts_parser._extract_todos(root, src),
            env_var_refs=env_var_refs,
            security_findings=file_security,
            react_directive=self._ts_parser._detect_react_directive(root, src),
        )

        # Detect serve() / Deno.serve() entry points
        serve_callees = self._ts_parser._detect_serve_entry_points(root, src, file_path)

        # Post-process functions for component/test/entry_point detection
        is_jsx = file_path.endswith(".jsx")
        for func in ts_result.functions:
            if is_jsx and func.name and func.name[0].isupper():
                func.is_component = self._ts_parser._body_contains_jsx(root, src, func)
            if ts_result.is_test_file or _TEST_FUNC_RE.match(func.name):
                func.is_test = True
            reason = TypeScriptParser._classify_entry_point(func, file_path)
            if reason:
                func.is_entry_point = True
                func.entry_point_reason = reason
            if func.name in serve_callees:
                func.is_entry_point = True
                if not func.entry_point_reason:
                    func.entry_point_reason = "serve_handler"

        return ts_result
