"""TypeScript/JavaScript parser using tree-sitter."""

from __future__ import annotations

import re

import tree_sitter_javascript as ts_js
import tree_sitter_typescript as ts_ts
from tree_sitter import Language, Node, Parser

from gristle.models import ParsedClass, ParsedFile, ParsedFunction, ParsedImport, ParsedRoute, ParsedTestCase
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

# Storybook story files
_STORYBOOK_RE = re.compile(r"\.stories\.[tj]sx?$")

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
        serve_callees = self._detect_serve_entry_points(root, src)

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
        # Extract routes
        routes = self._extract_routes(root, src, file_path)
        # Extract test cases (describe/it/test blocks) from test files
        test_cases = self._extract_test_cases(root, src, file_path) if is_test_file else []

        # Extract env var references
        from gristle.parsers.env_vars import extract_env_var_refs

        env_var_refs = extract_env_var_refs(content, "typescript")

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
            module_docstring=self._extract_module_docstring(root, src),
            line_count=content.count("\n") + 1,
            is_test_file=is_test_file,
            todos=file_todos,
            env_var_refs=env_var_refs,
        )

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

    def _parse_class(self, node: Node, src: bytes, file_path: str) -> ParsedClass:
        name = self._text(node.child_by_field_name("name"), src)
        body = node.child_by_field_name("body")
        bases = self._extract_heritage(node, src)
        methods = self._extract_class_methods(body, src, file_path, name) if body else []

        bases_str = f" extends {', '.join(bases)}" if bases else ""
        sig = f"class {name}{bases_str}"

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

    def _parse_method(self, node: Node, src: bytes, file_path: str, class_name: str) -> ParsedFunction:
        name = self._text(node.child_by_field_name("name"), src)
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        body = node.child_by_field_name("body")

        params_text = self._text(params_node, src) if params_node else "()"
        return_text = self._text(return_node, src).lstrip(": ") if return_node else None
        is_async = self._has_modifier(node, "async")

        sig = f"{'async ' if is_async else ''}{name}{params_text}{f': {return_text}' if return_text else ''}"
        qualified = f"{file_path}::{class_name}.{name}"

        calls = self._extract_calls(body, src) if body else []

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

        sig = f"{'async ' if is_async else ''}function {name}{params_text}{f': {return_text}' if return_text else ''}"

        calls = self._extract_calls(body, src) if body else []

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

                sig = (
                    f"const {name} = {'async ' if is_async else ''}{params_text} => ..."
                    if value_node.type == "arrow_function"
                    else f"const {name} = {'async ' if is_async else ''}function{params_text}"
                )

                calls = self._extract_calls(body, src) if body else []

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
                )
        return None

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

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

    @staticmethod
    def _detect_serve_entry_points(root: Node, src: bytes) -> set[str]:
        """Detect serve() / Deno.serve() calls and return names of functions called inside.

        Deno/Supabase edge functions use ``serve(async (req) => { ... })`` or
        ``Deno.serve(async (req) => { ... })`` as their entry point.  The handler
        is typically an anonymous arrow function, so we can't mark it directly.
        Instead we find named function calls made *inside* the handler body and
        return their names so they can be flagged as entry points.
        """
        callees: set[str] = set()
        for node in root.children:
            # expression_statement wrapping a call_expression
            if node.type != "expression_statement":
                continue
            call = None
            for child in node.children:
                if child.type == "call_expression":
                    call = child
                    break
            if call is None:
                continue

            func_node = call.child_by_field_name("function")
            if func_node is None:
                continue
            func_text = src[func_node.start_byte : func_node.end_byte].decode(errors="replace")
            if func_text not in ("serve", "Deno.serve"):
                continue

            # Found a serve() call — collect named function calls inside the handler body
            args = call.child_by_field_name("arguments")
            if args is None:
                continue
            TypeScriptParser._collect_call_names(args, src, callees)

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
    # Supabase edge function route extraction
    # ------------------------------------------------------------------

    def _extract_supabase_routes(self, root: Node, src: bytes, file_path: str) -> list[ParsedRoute]:
        """Extract a POST route from a Supabase edge function.

        Supabase edge functions live at ``supabase/functions/<name>/index.ts``
        and use ``serve(handler)`` as their entry.  The function name IS the
        route path: ``supabase/functions/analyze-gaps/index.ts`` → ``POST /analyze-gaps``.
        """
        m = _SUPABASE_FUNC_RE.search(file_path.replace("\\", "/"))
        if not m:
            return []

        # Confirm the file actually calls serve() / Deno.serve()
        serve_callees = self._detect_serve_entry_points(root, src)
        has_serve = bool(serve_callees) or self._has_top_level_serve(root, src)
        if not has_serve:
            return []

        func_name = m.group(1)  # e.g. "analyze-gaps"
        handler_name = next(iter(serve_callees)) if serve_callees else "<serve>"

        # Find the line of the serve() call for accurate positioning
        serve_line = 1
        for node in root.children:
            if node.type != "expression_statement":
                continue
            for child in node.children:
                if child.type == "call_expression":
                    fn = child.child_by_field_name("function")
                    if fn:
                        text = src[fn.start_byte : fn.end_byte].decode(errors="replace")
                        if text in ("serve", "Deno.serve"):
                            serve_line = node.start_point[0] + 1
                            break

        return [
            ParsedRoute(
                method="POST",
                path=f"/{func_name}",
                handler_name=handler_name,
                file_path=file_path,
                line=serve_line,
            )
        ]

    @staticmethod
    def _has_top_level_serve(root: Node, src: bytes) -> bool:
        """Check if the file has a top-level serve() or Deno.serve() call."""
        for node in root.children:
            if node.type != "expression_statement":
                continue
            for child in node.children:
                if child.type == "call_expression":
                    fn = child.child_by_field_name("function")
                    if fn:
                        text = src[fn.start_byte : fn.end_byte].decode(errors="replace")
                        if text in ("serve", "Deno.serve"):
                            return True
        return False

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

        # Supabase edge function convention: path derived from directory name
        routes.extend(self._extract_supabase_routes(root, src, file_path))

        return routes

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
        dynamic = getattr(self, "_dynamic_router_names", set())
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
        handler_name = None
        middleware: list[str] = []
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
                arg_index += 1
            else:
                # Handler or middleware
                name = self._resolve_call_name(child, src)
                if name:
                    if handler_name is None:
                        handler_name = name
                    else:
                        middleware.append(handler_name)
                        handler_name = name
                arg_index += 1

        if not path_arg:
            return None

        return ParsedRoute(
            method=method_name.upper(),
            path=path_arg,
            handler_name=handler_name or "<anonymous>",
            file_path=file_path,
            line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            middleware=middleware,
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

        ts_result = ParsedFile(
            path=file_path,
            language="javascript",
            classes=self._ts_parser._extract_classes(root, src, file_path),
            functions=self._ts_parser._extract_module_functions(root, src, file_path),
            imports=self._ts_parser._extract_imports(root, src)
            + self._ts_parser._extract_reexports(root, src)
            + self._ts_parser._extract_dynamic_imports(root, src),
            routes=self._ts_parser._extract_routes(root, src, file_path),
            test_cases=self._ts_parser._extract_test_cases(root, src, file_path) if is_test_file else [],
            module_docstring=self._ts_parser._extract_module_docstring(root, src),
            line_count=content.count("\n") + 1,
            is_test_file=is_test_file,
            todos=self._ts_parser._extract_todos(root, src),
            env_var_refs=env_var_refs,
        )

        # Detect serve() / Deno.serve() entry points
        serve_callees = TypeScriptParser._detect_serve_entry_points(root, src)

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
