"""Data models for parsed code entities."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(slots=True)
class ParsedImport:
    line: int
    module_path: str
    imported_names: list[str] = field(default_factory=list)
    aliases: dict[str, str] = field(default_factory=dict)
    is_relative: bool = False
    is_wildcard: bool = False


@dataclass(slots=True)
class ParsedFunction:
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str  # e.g. "def foo(a: int, b: str) -> bool"
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_async: bool = False
    is_static: bool = False
    is_classmethod: bool = False
    is_property: bool = False
    is_exported: bool = False
    is_component: bool = False  # Returns JSX (React component)
    is_test: bool = False  # test_ prefix, it()/describe()/test() etc.
    is_entry_point: bool = False  # Route handler, main(), page default export
    is_fixture: bool = False  # pytest.fixture
    visibility: str = "public"  # public / private / protected
    return_type: str | None = None
    complexity: int = 1
    calls: list[str] = field(default_factory=list)
    parameters: list[str] = field(default_factory=list)  # Parameter names
    todos: list[str] = field(default_factory=list)  # TODO/FIXME/HACK comments


@dataclass(slots=True)
class ParsedClass:
    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    signature: str  # e.g. "class Foo(Base, Mixin):"
    docstring: str | None = None
    decorators: list[str] = field(default_factory=list)
    is_abstract: bool = False
    is_exported: bool = False
    visibility: str = "public"
    bases: list[str] = field(default_factory=list)
    methods: list[ParsedFunction] = field(default_factory=list)
    kind: str = "class"  # class / interface / type / enum


@dataclass(slots=True)
class ParsedTestCase:
    """A test case block: describe/it/test (JS/TS) or TestClass/test_func (Python)."""

    name: str  # The test description or function/class name
    block_type: str  # "describe", "it", "test", "class"
    file_path: str
    start_line: int
    end_line: int
    parent_describe: str | None = None  # Enclosing describe/class name
    parametrize_count: int = 0  # Number of parametrize variants (0 = not parametrized)


@dataclass(slots=True)
class ParsedRoute:
    """An HTTP route/endpoint definition."""

    method: str  # GET, POST, PUT, DELETE, PATCH, ALL
    path: str  # /api/users/:id
    handler_name: str  # function name
    file_path: str
    line: int
    end_line: int = 0  # End line of handler body (for source loading)
    middleware: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedFile:
    path: str
    language: str
    classes: list[ParsedClass] = field(default_factory=list)
    functions: list[ParsedFunction] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    routes: list[ParsedRoute] = field(default_factory=list)
    test_cases: list[ParsedTestCase] = field(default_factory=list)
    module_docstring: str | None = None
    line_count: int = 0
    is_test_file: bool = False
    todos: list[str] = field(default_factory=list)  # File-level TODOs


# ------------------------------------------------------------------
# Document models (for markdown, RST, etc.)
# ------------------------------------------------------------------


@dataclass(slots=True)
class CodeReference:
    """A reference to a code entity found in a document."""

    raw_text: str  # The text as written in the doc
    ref_type: str  # inline_code, file_path, link, code_block
    line: int
    resolved: bool = False
    resolved_to: str | None = None  # qualified_name or file path if resolved


@dataclass(slots=True)
class DocumentSection:
    """A heading-delimited section in a document."""

    heading: str
    level: int  # 1-6
    start_line: int
    end_line: int
    code_references: list[CodeReference] = field(default_factory=list)


@dataclass(slots=True)
class ParsedDocument:
    """A parsed documentation file."""

    path: str
    title: str  # First H1 heading or filename
    doc_type: str  # readme, changelog, architecture, guide, adr, other
    sections: list[DocumentSection] = field(default_factory=list)
    code_references: list[CodeReference] = field(default_factory=list)
    line_count: int = 0
