"""Python parser using tree-sitter for structure and extraction."""

from __future__ import annotations

import re

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from gristle.models import (
    ParsedClass,
    ParsedFile,
    ParsedFunction,
    ParsedImport,
    ParsedRoute,
    ParsedTestCase,
    ParsedTypeField,
)
from gristle.parsers.base import LanguageParser

# Patterns for TODO/FIXME/HACK comments
_TODO_RE = re.compile(r"\b(TODO|FIXME|HACK|XXX|BUG|WARN(?:ING)?)\b[:\s]*(.*)", re.IGNORECASE)

# Test file detection
_TEST_FILE_RE = re.compile(r"(?:^|/)(?:test_|tests/|_test\.py|conftest\.py)", re.IGNORECASE)

# Test function/method detection
_TEST_FUNC_RE = re.compile(r"^test_")

# Route decorator patterns (FastAPI, Flask, Django, etc.)
_ROUTE_DECORATOR_RE = re.compile(
    r"^(?:app|router|blueprint|bp)\."
    r"(get|post|put|delete|patch|options|head|route|api_route|websocket)"
    r"(?:\(|$)",
    re.IGNORECASE,
)
_ROUTE_PATH_RE = re.compile(r"""["']([^"']+)["']""")

# Click/Typer CLI command decorators
_CLI_COMMAND_RE = re.compile(
    r"(?:click\.command|click\.group|typer\.command|app\.command|cli\.command|group\.command)",
    re.IGNORECASE,
)

# Django views.py detection
_DJANGO_VIEWS_RE = re.compile(r"(?:^|/)views\.py$")

# Flask/FastAPI dependency injection patterns
_DEPENDS_RE = re.compile(r"Depends\(")
_INJECT_RE = re.compile(r"^inject")


class PythonParser(LanguageParser):
    """Parses Python source files into structured entities."""

    def __init__(self) -> None:
        self._parser = Parser(Language(tspython.language()))

    @property
    def language_name(self) -> str:
        return "python"

    @property
    def file_extensions(self) -> list[str]:
        return ["py", "pyi"]

    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        tree = self._parser.parse(content.encode())
        root = tree.root_node
        src = content.encode()

        is_test_file = bool(_TEST_FILE_RE.search(file_path))
        functions = self._extract_module_functions(root, src, file_path)
        classes = self._extract_classes(root, src, file_path)
        routes: list[ParsedRoute] = []

        # Mark exports. If __all__ is declared it is authoritative; otherwise fall
        # back to the Python convention that module-level public (non-underscore)
        # names are the public API. Without this, EXPORTS edges and public-API /
        # coverage queries are empty for the majority of Python repos (which don't
        # declare __all__). Methods are unaffected (only top-level defs/classes).
        dunder_all = self._extract_dunder_all(root, src)
        if dunder_all:
            for func in functions:
                if func.name in dunder_all:
                    func.is_exported = True
            for cls in classes:
                if cls.name in dunder_all:
                    cls.is_exported = True
        else:
            for func in functions:
                if not func.name.startswith("_"):
                    func.is_exported = True
            for cls in classes:
                if not cls.name.startswith("_"):
                    cls.is_exported = True

        # Post-process functions
        for func in functions:
            if is_test_file or _TEST_FUNC_RE.match(func.name):
                func.is_test = True
            reason = self._classify_entry_point(func, file_path)
            if reason:
                func.is_entry_point = True
                func.entry_point_reason = reason
            # Extract routes from decorators
            route = self._route_from_decorators(func, file_path)
            if route:
                routes.append(route)
                func.is_entry_point = True
                func.entry_point_reason = "route_handler"

        # Also process methods
        for cls in classes:
            for method in cls.methods:
                if is_test_file or _TEST_FUNC_RE.match(method.name):
                    method.is_test = True
                reason = self._classify_entry_point(method, file_path)
                if reason:
                    method.is_entry_point = True
                    method.entry_point_reason = reason

        # Extract nested classes (classes defined inside functions, common in pytest)
        nested_classes = self._extract_nested_classes(root, src, file_path)
        classes.extend(nested_classes)

        # Build parametrize count map: start_line -> variant count
        parametrize_map = self._build_parametrize_map(root, src)

        # Extract test cases for pytest: test_* functions and Test* classes
        test_cases = self._extract_test_cases(functions, classes, is_test_file, parametrize_map)

        # Extract TODOs
        file_todos = self._extract_todos(root, src)

        # Extract env var references
        from gristle.parsers.env_vars import extract_env_var_refs

        env_var_refs = extract_env_var_refs(content, "python")

        # Security pattern detection
        from gristle.parsers.security import (
            detect_hardcoded_secrets,
            detect_llm_output_risks,
            detect_sql_injection,
            detect_unsafe_calls,
        )

        file_security = (
            detect_hardcoded_secrets(content, "python") + detect_sql_injection(content, "python")
            if not is_test_file
            else []
        )

        # Per-function security: unsafe calls + LLM output risks
        if not is_test_file:
            all_funcs = list(functions)
            for cls in classes:
                all_funcs.extend(cls.methods)
            for func in all_funcs:
                func.security_findings = detect_unsafe_calls(func.calls) + detect_llm_output_risks(func.calls)
            # Attribute file-level findings to functions by line range
            for finding in file_security:
                tag = f"{finding.category}:{finding.detail}"
                for func in all_funcs:
                    if func.start_line <= finding.line <= func.end_line and tag not in func.security_findings:
                        func.security_findings.append(tag)
                        break

        return ParsedFile(
            path=file_path,
            language="python",
            classes=classes,
            functions=functions,
            imports=self._extract_imports(root, src),
            routes=routes,
            test_cases=test_cases,
            module_docstring=self._extract_module_docstring(root, src),
            line_count=content.count("\n") + 1,
            is_test_file=is_test_file,
            todos=file_todos,
            env_var_refs=env_var_refs,
            security_findings=file_security,
        )

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _extract_imports(self, root: Node, src: bytes) -> list[ParsedImport]:
        imports: list[ParsedImport] = []
        for node in root.children:
            if node.type == "import_statement":
                imports.append(self._parse_import(node, src))
            elif node.type == "import_from_statement":
                imports.append(self._parse_import_from(node, src))
        return imports

    def _parse_import(self, node: Node, src: bytes) -> ParsedImport:
        names: list[str] = []
        aliases: dict[str, str] = {}
        for child in node.children:
            if child.type == "dotted_name":
                names.append(self._text(child, src))
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                name = self._text(name_node, src) if name_node else ""
                names.append(name)
                if alias_node:
                    aliases[name] = self._text(alias_node, src)
        return ParsedImport(
            line=node.start_point[0] + 1,
            module_path=names[0] if names else "",
            imported_names=names,
            aliases=aliases,
        )

    def _parse_import_from(self, node: Node, src: bytes) -> ParsedImport:
        module_path = ""
        names: list[str] = []
        aliases: dict[str, str] = {}
        is_relative = False
        is_wildcard = False

        # First pass: extract the module path (the part after "from")
        # The module_name field contains the module being imported from.
        mod_node = node.child_by_field_name("module_name")
        if mod_node:
            module_path = self._text(mod_node, src)
            # Check for relative import (dots before module name)
            for child in node.children:
                if child.type == "import_prefix":
                    is_relative = True
                    break
                if child.type == "relative_import":
                    is_relative = True
                    break
        else:
            # Relative import without module name: "from . import foo"
            for child in node.children:
                if child.type == "relative_import":
                    is_relative = True
                    dotted = self._find_child(child, "dotted_name")
                    if dotted:
                        module_path = self._text(dotted, src)
                    break

        # Second pass: extract imported names (after "import")
        found_import_keyword = False
        for child in node.children:
            if child.type == "import" or self._text(child, src) == "import":
                found_import_keyword = True
                continue

            if not found_import_keyword:
                continue

            if child.type == "dotted_name" or child.type == "identifier":
                names.append(self._text(child, src))
            elif child.type == "wildcard_import":
                is_wildcard = True
                names.append("*")
            elif child.type == "aliased_import":
                name_node = child.child_by_field_name("name")
                alias_node = child.child_by_field_name("alias")
                name = self._text(name_node, src) if name_node else ""
                names.append(name)
                if alias_node:
                    aliases[name] = self._text(alias_node, src)

        return ParsedImport(
            line=node.start_point[0] + 1,
            module_path=module_path,
            imported_names=names,
            aliases=aliases,
            is_relative=is_relative,
            is_wildcard=is_wildcard,
        )

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _extract_classes(self, root: Node, src: bytes, file_path: str) -> list[ParsedClass]:
        classes: list[ParsedClass] = []
        for node in root.children:
            if node.type == "class_definition":
                classes.append(self._parse_class(node, src, file_path))
            elif node.type == "decorated_definition":
                inner = self._get_inner_definition(node)
                if inner and inner.type == "class_definition":
                    classes.append(self._parse_class(inner, src, file_path, decorator_node=node))
        return classes

    def _parse_class(
        self,
        node: Node,
        src: bytes,
        file_path: str,
        decorator_node: Node | None = None,
    ) -> ParsedClass:
        name = self._text(node.child_by_field_name("name"), src)
        bases = self._extract_bases(node, src)
        decorators = self._extract_decorators(decorator_node, src) if decorator_node else []
        body = node.child_by_field_name("body")
        docstring = self._extract_docstring(body, src) if body else None
        methods = self._extract_methods(body, src, file_path, name) if body else []
        class_fields = self._extract_class_fields(body, src, file_path, decorators, bases) if body else []

        # Build signature line
        bases_str = f"({', '.join(bases)})" if bases else ""
        sig = f"class {name}{bases_str}:"

        return ParsedClass(
            name=name,
            qualified_name=f"{file_path}::{name}",
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=sig,
            docstring=docstring,
            decorators=decorators,
            is_abstract="ABC" in bases or "ABCMeta" in bases or "abstractmethod" in str(decorators),
            visibility=self._visibility(name),
            bases=bases,
            methods=methods,
            fields=class_fields,
        )

    def _extract_bases(self, class_node: Node, src: bytes) -> list[str]:
        bases: list[str] = []
        arg_list = class_node.child_by_field_name("superclasses")
        if not arg_list:
            return bases
        for child in arg_list.children:
            if child.type in ("identifier", "dotted_name", "attribute"):
                bases.append(self._text(child, src))
        return bases

    # ------------------------------------------------------------------
    # Functions / Methods
    # ------------------------------------------------------------------

    def _extract_module_functions(self, root: Node, src: bytes, file_path: str) -> list[ParsedFunction]:
        functions: list[ParsedFunction] = []
        for node in root.children:
            if node.type == "function_definition":
                functions.append(self._parse_function(node, src, file_path))
            elif node.type == "decorated_definition":
                inner = self._get_inner_definition(node)
                if inner and inner.type == "function_definition":
                    functions.append(self._parse_function(inner, src, file_path, decorator_node=node))
        return functions

    def _extract_methods(self, body: Node, src: bytes, file_path: str, class_name: str) -> list[ParsedFunction]:
        methods: list[ParsedFunction] = []
        if body is None:
            return methods
        for node in body.children:
            if node.type == "function_definition":
                m = self._parse_function(node, src, file_path, class_name=class_name)
                methods.append(m)
            elif node.type == "decorated_definition":
                inner = self._get_inner_definition(node)
                if inner and inner.type == "function_definition":
                    m = self._parse_function(inner, src, file_path, class_name=class_name, decorator_node=node)
                    methods.append(m)
        return methods

    # ------------------------------------------------------------------
    # Nested classes (classes inside functions, e.g. pytest helpers)
    # ------------------------------------------------------------------

    def _extract_nested_classes(self, root: Node, src: bytes, file_path: str) -> list[ParsedClass]:
        """Find class definitions nested inside functions (common in pytest)."""
        nested: list[ParsedClass] = []
        for node in root.children:
            func_node = None
            if node.type == "function_definition":
                func_node = node
            elif node.type == "decorated_definition":
                inner = self._get_inner_definition(node)
                if inner and inner.type == "function_definition":
                    func_node = inner
            if func_node:
                body = func_node.child_by_field_name("body")
                if body:
                    self._walk_nested_classes(body, src, file_path, nested)
        return nested

    def _walk_nested_classes(self, node: Node, src: bytes, file_path: str, out: list[ParsedClass]) -> None:
        """Recursively walk into function bodies to find nested class definitions."""
        for child in node.children:
            class_node = None
            if child.type == "class_definition":
                class_node = child
            elif child.type == "decorated_definition":
                inner = self._get_inner_definition(child)
                if inner and inner.type == "class_definition":
                    class_node = inner
            if class_node:
                out.append(self._parse_class(class_node, src, file_path))
            # Also recurse into nested function bodies
            if child.type in ("function_definition",):
                body = child.child_by_field_name("body")
                if body:
                    self._walk_nested_classes(body, src, file_path, out)

    # ------------------------------------------------------------------
    # Parametrize map
    # ------------------------------------------------------------------

    def _build_parametrize_map(self, root: Node, src: bytes) -> dict[int, int]:
        """Build a map of function start_line -> parametrize variant count.

        Walks root-level and class-level decorated definitions looking for
        @pytest.mark.parametrize decorators.
        """
        result: dict[int, int] = {}
        for node in root.children:
            self._check_parametrize(node, src, result)
            # Also check inside classes
            if node.type == "class_definition":
                body = node.child_by_field_name("body")
                if body:
                    for child in body.children:
                        self._check_parametrize(child, src, result)
        return result

    def _check_parametrize(self, node: Node, src: bytes, result: dict[int, int]) -> None:
        """Check if a node is a decorated function with @pytest.mark.parametrize."""
        if node.type != "decorated_definition":
            return
        inner = self._get_inner_definition(node)
        if not inner or inner.type != "function_definition":
            return
        count = self._count_parametrize_variants(node, src)
        if count > 0:
            line = inner.start_point[0] + 1
            result[line] = count

    # ------------------------------------------------------------------
    # Test case extraction (pytest)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_test_cases(
        functions: list[ParsedFunction],
        classes: list[ParsedClass],
        is_test_file: bool,
        parametrize_map: dict[int, int] | None = None,
    ) -> list[ParsedTestCase]:
        """Build ParsedTestCase entries from pytest test functions and classes."""
        cases: list[ParsedTestCase] = []
        pmap = parametrize_map or {}

        # Test classes act as "describe" groups
        for cls in classes:
            is_test_class = cls.name.startswith("Test") and not cls.name.endswith("Mixin")
            if not is_test_class:
                continue
            cases.append(
                ParsedTestCase(
                    name=cls.name,
                    block_type="class",
                    file_path=cls.file_path,
                    start_line=cls.start_line,
                    end_line=cls.end_line,
                )
            )
            # Methods inside test classes — only test_* methods
            for method in cls.methods:
                if _TEST_FUNC_RE.match(method.name):
                    cases.append(
                        ParsedTestCase(
                            name=method.name,
                            block_type="test",
                            file_path=method.file_path,
                            start_line=method.start_line,
                            end_line=method.end_line,
                            parent_describe=cls.name,
                            parametrize_count=pmap.get(method.start_line, 0),
                        )
                    )

        # Module-level test_* functions (use name pattern, not is_test flag
        # which also marks helpers in test files)
        for func in functions:
            if _TEST_FUNC_RE.match(func.name):
                cases.append(
                    ParsedTestCase(
                        name=func.name,
                        block_type="test",
                        file_path=func.file_path,
                        start_line=func.start_line,
                        end_line=func.end_line,
                        parametrize_count=pmap.get(func.start_line, 0),
                    )
                )

        return cases

    # ------------------------------------------------------------------

    def _parse_function(
        self,
        node: Node,
        src: bytes,
        file_path: str,
        class_name: str | None = None,
        decorator_node: Node | None = None,
    ) -> ParsedFunction:
        name = self._text(node.child_by_field_name("name"), src)
        params_node = node.child_by_field_name("parameters")
        return_node = node.child_by_field_name("return_type")
        body = node.child_by_field_name("body")

        decorators = self._extract_decorators(decorator_node, src) if decorator_node else []

        params_text = self._text(params_node, src) if params_node else "()"
        return_text = self._text(return_node, src) if return_node else None
        param_names = self._extract_param_names(params_node, src) if params_node else []
        typed_params = self._extract_typed_params(params_node, src) if params_node else []

        # Detect async
        is_async = False
        full_text = self._text(node, src)
        # Check parent or preceding sibling for 'async'
        if node.parent and node.parent.type == "decorated_definition":
            # Check the decorated_definition text
            parent_text = self._text(node.parent, src)
            is_async = (
                "async def" in parent_text.split("\n")[len(decorators)][:50]
                if decorators
                else "async def" in parent_text[:50]
            )
        else:
            is_async = full_text.lstrip().startswith("async ")

        # Build signature
        async_prefix = "async " if is_async else ""
        return_suffix = f" -> {return_text}" if return_text else ""
        sig = f"{async_prefix}def {name}{params_text}{return_suffix}"

        qualified = f"{file_path}::{class_name}.{name}" if class_name else f"{file_path}::{name}"

        # Extract calls from the function body
        calls = self._extract_calls(body, src) if body else []
        callback_refs = self._extract_callback_refs(body, src) if body else []

        # Resolve self.method -> ClassName.method
        if class_name:
            calls = [f"{class_name}.{c[5:]}" if c.startswith("self.") else c for c in calls]
            callback_refs = [
                (f"{class_name}.{name[5:]}" if name.startswith("self.") else name, ctx) for name, ctx in callback_refs
            ]

        is_fixture = any(
            d in ("fixture", "pytest.fixture") or d.startswith("fixture(") or d.startswith("pytest.fixture(")
            for d in decorators
        )

        return ParsedFunction(
            name=name,
            qualified_name=qualified,
            file_path=file_path,
            start_line=node.start_point[0] + 1,
            end_line=node.end_point[0] + 1,
            signature=sig,
            docstring=self._extract_docstring(body, src) if body else None,
            decorators=decorators,
            is_async=is_async,
            is_static="staticmethod" in decorators,
            is_classmethod="classmethod" in decorators,
            is_property="property" in decorators,
            is_fixture=is_fixture,
            visibility=self._visibility(name),
            return_type=return_text,
            complexity=self._cyclomatic_complexity(body) if body else 1,
            calls=calls,
            callback_refs=callback_refs,
            parameters=param_names,
            typed_parameters=typed_params,
        )

    # ------------------------------------------------------------------
    # Call extraction
    # ------------------------------------------------------------------

    def _extract_calls(self, node: Node, src: bytes) -> list[str]:
        """Extract function/method calls from a subtree."""
        calls: list[str] = []
        self._walk_calls(node, src, calls)
        # Deduplicate while preserving order
        seen: set[str] = set()
        unique: list[str] = []
        for c in calls:
            if c not in seen:
                seen.add(c)
                unique.append(c)
        return unique

    def _walk_calls(self, node: Node, src: bytes, out: list[str]) -> None:
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            if func_node:
                call_name = self._resolve_call_name(func_node, src)
                if call_name:
                    out.append(call_name)
        for child in node.children:
            self._walk_calls(child, src, out)

    def _resolve_call_name(self, node: Node, src: bytes) -> str | None:
        """Resolve a call target to a string like 'foo', 'self.bar', 'os.path.join'."""
        if node.type == "identifier":
            return self._text(node, src)
        if node.type == "attribute":
            obj = node.child_by_field_name("object")
            attr = node.child_by_field_name("attribute")
            if obj and attr:
                obj_name = self._resolve_call_name(obj, src)
                attr_name = self._text(attr, src)
                if obj_name:
                    return f"{obj_name}.{attr_name}"
                return attr_name
        return None

    # ------------------------------------------------------------------
    # Callback / handler detection
    # ------------------------------------------------------------------

    # Known patterns where arguments are function references
    # First-arg-is-callback builtins: map(fn, iter), filter(fn, iter), reduce(fn, iter)
    _PY_FIRST_ARG_HOF = frozenset({"map", "filter", "reduce", "functools.reduce"})
    # Keyword-arg-is-callback builtins: sorted(iter, key=fn)
    _PY_KWARG_HOF = frozenset({"sorted", "min", "max"})
    _PY_EVENT_METHODS = frozenset({"connect", "on", "add_handler", "add_event_handler"})
    _PY_MIDDLEWARE_METHODS = frozenset({"add_middleware", "middleware", "use"})
    _PY_ROUTE_METHODS = frozenset({"add_route", "add_api_route", "add_url_rule"})
    _PY_ARRAY_METHODS = frozenset({"apply"})

    def _extract_callback_refs(
        self,
        node: Node,
        src: bytes,
    ) -> list[tuple[str, str]]:
        """Extract (callee_name, context) for function references passed as arguments."""
        refs: list[tuple[str, str]] = []
        self._walk_callback_refs(node, src, refs)
        seen: set[tuple[str, str]] = set()
        unique: list[tuple[str, str]] = []
        for r in refs:
            if r not in seen:
                seen.add(r)
                unique.append(r)
        return unique

    def _walk_callback_refs(
        self,
        node: Node,
        src: bytes,
        out: list[tuple[str, str]],
    ) -> None:
        if node.type == "call":
            func_node = node.child_by_field_name("function")
            args_node = node.child_by_field_name("arguments")
            if func_node and args_node:
                call_name = self._resolve_call_name(func_node, src)
                context = self._classify_py_callback_context(call_name)
                if context:
                    self._collect_py_callback_args(args_node, src, context, call_name, out)
        for child in node.children:
            self._walk_callback_refs(child, src, out)

    def _classify_py_callback_context(self, call_name: str | None) -> str | None:
        """Return the callback context for a known Python pattern, or None."""
        if not call_name:
            return None
        # Builtin higher-order functions: map(fn, iterable), filter(fn, iterable)
        if call_name in self._PY_FIRST_ARG_HOF:
            return "argument"
        # Keyword-arg HOFs: sorted(iter, key=fn)
        if call_name in self._PY_KWARG_HOF:
            return "argument"
        # Method-based patterns: check the last dotted part
        method = call_name.rsplit(".", 1)[-1] if "." in call_name else call_name
        if method in self._PY_EVENT_METHODS:
            return "callback"
        if method in self._PY_MIDDLEWARE_METHODS:
            return "middleware"
        if method in self._PY_ROUTE_METHODS:
            return "route_handler"
        if method in self._PY_ARRAY_METHODS:
            return "array_method"
        return None

    _PY_CALLBACK_KWARG_NAMES = frozenset(
        {
            "key",
            "default",
            "callback",
            "handler",
            "func",
            "target",
        }
    )

    def _collect_py_callback_args(
        self,
        args_node: Node,
        src: bytes,
        context: str,
        call_name: str | None,
        out: list[tuple[str, str]],
    ) -> None:
        """Collect identifier/attribute arguments as callback refs."""
        first_arg_only = call_name in self._PY_FIRST_ARG_HOF
        kwarg_only = call_name in self._PY_KWARG_HOF
        found_first = False
        for child in args_node.named_children:
            if child.type == "keyword_argument":
                # Handle key=fn in sorted(iterable, key=fn)
                key_node = child.child_by_field_name("name")
                value_node = child.child_by_field_name("value")
                if key_node and value_node:
                    key_text = self._text(key_node, src)
                    if key_text in self._PY_CALLBACK_KWARG_NAMES and value_node.type in ("identifier", "attribute"):
                        name = self._resolve_call_name(value_node, src)
                        if name:
                            out.append((name, context))
            elif kwarg_only:
                # For sorted/min/max, only keyword args are callbacks
                continue
            elif child.type in ("identifier", "attribute"):
                name = self._resolve_call_name(child, src)
                if name:
                    out.append((name, context))
                    if first_arg_only:
                        return
                    found_first = True
            elif first_arg_only and not found_first:
                # For map/filter, skip if first arg is not an identifier
                return

    # ------------------------------------------------------------------
    # Decorators
    # ------------------------------------------------------------------

    def _extract_decorators(self, node: Node, src: bytes) -> list[str]:
        decorators: list[str] = []
        for child in node.children:
            if child.type == "decorator":
                # Store full decorator text (skip the @ sign).
                # For calls like @app.get("/users") this captures 'app.get("/users")'
                # which preserves the route path argument.
                for dchild in child.children:
                    if dchild.type in ("identifier", "dotted_name", "attribute") or dchild.type == "call":
                        decorators.append(self._text(dchild, src))
                        break
        return decorators

    # ------------------------------------------------------------------
    # Docstrings
    # ------------------------------------------------------------------

    def _extract_dunder_all(self, root: Node, src: bytes) -> set[str]:
        """Extract names from module-level ``__all__ = [...]`` assignment."""
        names: set[str] = set()
        for child in root.children:
            if child.type != "expression_statement":
                continue
            expr = child.children[0] if child.children else None
            if not expr or expr.type != "assignment":
                continue
            left = expr.child_by_field_name("left")
            right = expr.child_by_field_name("right")
            if not left or not right:
                continue
            if self._text(left, src) != "__all__":
                continue
            # right should be a list: ["name1", "name2"]
            if right.type not in ("list", "tuple"):
                continue
            for item in right.children:
                if item.type == "string":
                    name = self._text(item, src).strip("\"'")
                    if name:
                        names.add(name)
        return names

    def _extract_module_docstring(self, root: Node, src: bytes) -> str | None:
        for child in root.children:
            if child.type == "expression_statement":
                expr = child.children[0] if child.children else None
                if expr and expr.type == "string":
                    return self._clean_docstring(self._text(expr, src))
            elif child.type in ("comment", "newline"):
                continue
            else:
                break
        return None

    def _extract_docstring(self, body: Node, src: bytes) -> str | None:
        if body is None:
            return None
        for child in body.children:
            if child.type == "expression_statement":
                expr = child.children[0] if child.children else None
                if expr and expr.type in ("string", "concatenated_string"):
                    return self._clean_docstring(self._text(expr, src))
            elif child.type in ("comment", "newline"):
                continue
            else:
                break
        return None

    @staticmethod
    def _clean_docstring(raw: str) -> str:
        """Strip surrounding quotes from a docstring."""
        for quote in ('"""', "'''", '"', "'"):
            if raw.startswith(quote) and raw.endswith(quote):
                return raw[len(quote) : -len(quote)].strip()
        return raw.strip()

    # ------------------------------------------------------------------
    # Cyclomatic complexity (simplified)
    # ------------------------------------------------------------------

    def _cyclomatic_complexity(self, node: Node) -> int:
        """Approximate cyclomatic complexity by counting decision points."""
        decision_types = {
            "if_statement",
            "elif_clause",
            "for_statement",
            "while_statement",
            "except_clause",
            "with_statement",
            "assert_statement",
            "boolean_operator",  # and / or
            "conditional_expression",  # ternary
        }
        count = 1  # base complexity
        count += self._count_node_types(node, decision_types)
        return count

    def _count_node_types(self, node: Node, types: set[str]) -> int:
        count = 1 if node.type in types else 0
        for child in node.children:
            count += self._count_node_types(child, types)
        return count

    # ------------------------------------------------------------------
    # Entry point / Route / TODO detection
    # ------------------------------------------------------------------

    @staticmethod
    def _classify_entry_point(func: ParsedFunction, file_path: str) -> str | None:
        """Classify a function's entry point reason, or return None if not an entry point.

        Returns the entry_point_reason string, e.g. "main", "cli_command", "pytest_fixture".
        """
        # main() function
        if func.name == "main":
            return "main"

        # pytest fixtures — entry points by convention (consumed by pytest, not by code)
        if func.is_fixture:
            return "pytest_fixture"

        # __init__ methods — entry points by convention (called by constructors)
        if func.name == "__init__":
            return "constructor"

        # Django views — functions in views.py are entry points by convention
        if _DJANGO_VIEWS_RE.search(file_path) and func.visibility == "public":
            return "django_view"

        for dec in func.decorators:
            # Click/Typer CLI commands
            if _CLI_COMMAND_RE.search(dec):
                return "cli_command"
            # Generic "command" or "cli" in decorator (existing behavior)
            if "command" in dec or "cli" in dec:
                return "cli_command"
            # Flask/FastAPI dependency injection
            if _INJECT_RE.match(dec) or _DEPENDS_RE.search(dec):
                return "dependency_injection"

        return None

    @staticmethod
    def _route_from_decorators(func: ParsedFunction, file_path: str) -> ParsedRoute | None:
        """Extract route info from FastAPI/Flask-style decorators."""
        for dec in func.decorators:
            m = _ROUTE_DECORATOR_RE.match(dec)
            if not m:
                continue
            method = m.group(1).upper()
            if method == "ROUTE" or method == "API_ROUTE":
                method = "ALL"
            # Extract the path from the decorator's string argument
            path_match = _ROUTE_PATH_RE.search(dec)
            path = path_match.group(1) if path_match else f"/{func.name}"
            return ParsedRoute(
                method=method,
                path=path,
                handler_name=func.name,
                file_path=file_path,
                line=func.start_line,
            )
        return None

    def _extract_todos(self, root: Node, src: bytes) -> list[str]:
        """Extract TODO/FIXME/HACK comments from the AST."""
        todos: list[str] = []
        for node in self._iter_descendants(root):
            if node.type == "comment":
                text = self._text(node, src)
                m = _TODO_RE.search(text)
                if m:
                    tag = m.group(1).upper()
                    msg = m.group(2).strip()
                    line = node.start_point[0] + 1
                    todos.append(f"{tag}(L{line}): {msg}" if msg else f"{tag}(L{line})")
        return todos

    @staticmethod
    def _iter_descendants(node: Node):
        yield node
        for child in node.children:
            yield from PythonParser._iter_descendants(child)

    # ------------------------------------------------------------------
    # Parameter extraction
    # ------------------------------------------------------------------

    def _extract_param_names(self, params_node: Node, src: bytes) -> list[str]:
        """Extract parameter names from a function's parameters node."""
        names: list[str] = []
        for child in params_node.children:
            if child.type == "identifier":
                name = self._text(child, src)
                if name not in ("self", "cls"):
                    names.append(name)
            elif child.type in (
                "typed_parameter",
                "default_parameter",
                "typed_default_parameter",
            ):
                # Try the 'name' field first (typed_default_parameter has it)
                name_node = child.child_by_field_name("name")
                if not name_node:
                    # Fall back to first identifier child (typed_parameter)
                    for sub in child.children:
                        if sub.type == "identifier":
                            name_node = sub
                            break
                if name_node:
                    name = self._text(name_node, src)
                    if name not in ("self", "cls"):
                        names.append(name)
            elif child.type == "list_splat_pattern":
                # *args
                for sub in child.children:
                    if sub.type == "identifier":
                        names.append(f"*{self._text(sub, src)}")
                        break
            elif child.type == "dictionary_splat_pattern":
                # **kwargs
                for sub in child.children:
                    if sub.type == "identifier":
                        names.append(f"**{self._text(sub, src)}")
                        break
        return names

    def _extract_typed_params(self, params_node: Node, src: bytes) -> list[tuple[str, str | None]]:
        """Extract (name, type) pairs from a function's parameters node."""
        result: list[tuple[str, str | None]] = []
        for child in params_node.children:
            if child.type == "identifier":
                name = self._text(child, src)
                if name not in ("self", "cls"):
                    result.append((name, None))
            elif child.type == "typed_parameter":
                name_node = None
                for sub in child.children:
                    if sub.type == "identifier":
                        name_node = sub
                        break
                if name_node:
                    name = self._text(name_node, src)
                    if name not in ("self", "cls"):
                        type_node = child.child_by_field_name("type")
                        type_text = self._text(type_node, src) if type_node else None
                        result.append((name, type_text))
            elif child.type == "default_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self._text(name_node, src)
                    if name not in ("self", "cls"):
                        result.append((name, None))
            elif child.type == "typed_default_parameter":
                name_node = child.child_by_field_name("name")
                if name_node:
                    name = self._text(name_node, src)
                    if name not in ("self", "cls"):
                        type_node = child.child_by_field_name("type")
                        type_text = self._text(type_node, src) if type_node else None
                        result.append((name, type_text))
            elif child.type == "list_splat_pattern":
                for sub in child.children:
                    if sub.type == "identifier":
                        result.append((f"*{self._text(sub, src)}", None))
                        break
            elif child.type == "dictionary_splat_pattern":
                for sub in child.children:
                    if sub.type == "identifier":
                        result.append((f"**{self._text(sub, src)}", None))
                        break
        return result

    # Decorators/bases that indicate field-bearing classes
    _FIELD_CLASS_DECORATORS = frozenset({"dataclass", "dataclasses.dataclass"})
    _FIELD_CLASS_BASES = frozenset({"BaseModel", "TypedDict", "NamedTuple"})

    def _extract_class_fields(
        self,
        body: Node,
        src: bytes,
        file_path: str,
        decorators: list[str],
        bases: list[str],
    ) -> list[ParsedTypeField]:
        """Extract typed fields from dataclass/Pydantic/TypedDict class bodies."""
        # Only extract fields from known field-bearing class patterns
        has_field_decorator = any(
            d in self._FIELD_CLASS_DECORATORS or d.startswith("dataclass(") or d.startswith("dataclasses.dataclass(")
            for d in decorators
        )
        has_field_base = bool(self._FIELD_CLASS_BASES & set(bases))
        if not has_field_decorator and not has_field_base:
            return []

        fields: list[ParsedTypeField] = []
        for child in body.children:
            if child.type != "expression_statement":
                continue
            expr = child.children[0] if child.children else None
            if expr is None:
                continue

            if expr.type == "assignment":
                # x: int = 5  (tree-sitter: assignment with type)
                type_node = expr.child_by_field_name("type")
                if type_node is None:
                    continue  # Plain assignment without annotation
                left = expr.child_by_field_name("left")
                right = expr.child_by_field_name("right")
                if left and left.type == "identifier":
                    name = self._text(left, src)
                    type_text = self._text(type_node, src)
                    default_value = self._text(right, src) if right else None
                    is_optional = "None" in (type_text or "") or "Optional" in (type_text or "")
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
            elif expr.type == "type":
                # x: int  (annotation without assignment)
                # tree-sitter parses this as a "type" node within expression_statement
                # Actually this might be different in tree-sitter-python
                pass

        return fields

    @staticmethod
    def _count_parametrize_variants(decorator_node: Node | None, src: bytes) -> int:
        """Count parametrize variants from @pytest.mark.parametrize decorator.

        Returns the number of test variants, or 0 if not parametrized.
        Handles stacked parametrize decorators by multiplying counts.
        """
        if decorator_node is None:
            return 0

        total = 0
        for child in decorator_node.children:
            if child.type != "decorator":
                continue
            # Check if this decorator is parametrize
            text = src[child.start_byte : child.end_byte].decode(errors="replace")
            if "parametrize" not in text:
                continue
            # Find the call node
            call_node = None
            for dchild in child.children:
                if dchild.type == "call":
                    call_node = dchild
                    break
            if not call_node:
                continue
            # Count the items in the second argument (the parameter list)
            args = call_node.child_by_field_name("arguments")
            if not args:
                continue
            # Find the list/tuple argument (second positional arg)
            arg_idx = 0
            count = 0
            for arg in args.children:
                if arg.type in (",", "(", ")"):
                    continue
                arg_idx += 1
                if arg_idx == 2:
                    # This should be a list or tuple of test cases
                    if arg.type == "list":
                        count = sum(1 for c in arg.children if c.type not in (",", "[", "]"))
                    elif arg.type == "tuple":
                        count = sum(1 for c in arg.children if c.type not in (",", "(", ")"))
                    break
            if count > 0:
                total = count if total == 0 else total * count
        return total

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
    def _get_inner_definition(decorated: Node) -> Node | None:
        """Get the actual definition inside a decorated_definition."""
        for child in decorated.children:
            if child.type in ("function_definition", "class_definition"):
                return child
        return None

    @staticmethod
    def _visibility(name: str) -> str:
        if name.startswith("__") and name.endswith("__"):
            return "public"  # dunder methods are public API
        if name.startswith("__"):
            return "private"
        if name.startswith("_"):
            return "protected"
        return "public"
