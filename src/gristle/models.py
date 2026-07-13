"""Data models for parsed code entities."""

from __future__ import annotations

from dataclasses import dataclass, field

# How a CALLS edge was resolved, ranked by confidence (higher = more reliable).
# Written to the edge's `resolution` property during ingestion and surfaced by the
# query engine so consumers can weight or filter edges. Single source of truth for
# the ranking — the ingestion pipeline and the query engine both read it.
RESOLUTION_RANK: dict[str, int] = {
    "exact": 6,  # exact qualified name — always unique
    "file_scoped": 5,  # qualified within the calling file
    "typed_receiver": 5,  # obj.method() where obj's type annotation names a class
    "import": 4,  # name/method imported from a resolved file
    "dotted": 4,  # self/this, ClassName.method, imported-module method
    "same_file": 3,  # bare name defined in the same file
    "unique_global": 2,  # only one function with that name repo-wide (weakest)
}


def resolution_confidence(resolution: str | None) -> str:
    """Bucket a CALLS `resolution` label into high / medium / low.

    Gives agents a coarse "how much should I trust this edge?" signal without
    needing to know the resolution strategies. Edges ingested before the
    `resolution` property existed have no label and report ``"unknown"``.
    """
    rank = RESOLUTION_RANK.get(resolution or "", 0)
    if rank >= 5:
        return "high"
    if rank == 4:
        return "medium"
    if rank >= 2:
        return "low"
    return "unknown"


def weakest_resolution(resolutions: list[str | None]) -> str | None:
    """The least-confident label in a call path — a path is only as good as its
    weakest edge. Returns None for an empty path."""
    if not resolutions:
        return None
    return min(resolutions, key=lambda r: RESOLUTION_RANK.get(r or "", 0))


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
    entry_point_reason: str | None = None  # Why it's an entry point (e.g. "route_handler", "react_component")
    is_fixture: bool = False  # pytest.fixture
    visibility: str = "public"  # public / private / protected
    return_type: str | None = None
    complexity: int = 1
    calls: list[str] = field(default_factory=list)
    # Call descriptors that include positional identifier arguments, e.g.
    # "session.query(User)" or "db.insert(chat)". Lets the schema linker see a
    # model passed as an argument (the model name is dropped from `calls`, which
    # keeps only the callee name). Consumed in-memory by SchemaExtractor.
    calls_with_args: list[str] = field(default_factory=list)
    callback_refs: list[tuple[str, str]] = field(default_factory=list)  # (callee_name, context)
    parameters: list[str] = field(default_factory=list)  # Parameter names
    typed_parameters: list[tuple[str, str | None]] = field(default_factory=list)  # (name, type) pairs
    todos: list[str] = field(default_factory=list)  # TODO/FIXME/HACK comments
    security_findings: list[str] = field(default_factory=list)  # e.g. ["unsafe_call:eval", "llm_output_risk:exec"]
    raises: list[str] = field(
        default_factory=list
    )  # Exception type names raised/thrown (Python raise, JS/TS throw new)
    catches: list[str] = field(default_factory=list)  # Exception type names caught (Python except <Type>)
    has_error_handling: bool = False  # Body contains a try/except (Python) or try/catch (JS/TS)


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
    fields: list[ParsedTypeField] = field(default_factory=list)
    # DRF class-based view `permission_classes = (IsAuthenticated, ...)` — the
    # permission class names. Lets consumers read a CBV route's auth posture by
    # joining Route-[:HANDLES]->Class. Empty for non-DRF classes.
    permission_classes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ParsedTypeField:
    """A field/property of a type (interface field, class property, dataclass field)."""

    name: str
    type_annotation: str | None = None
    is_optional: bool = False
    default_value: str | None = None
    file_path: str = ""
    line: int = 0


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
class ParsedVariable:
    """A module-level variable/constant binding that is not a function or class —
    a TS/JS ``const``/``let``/``var`` or a Python module-level assignment.

    Captures config objects, Zod/validation schemas, handler/route registries, and
    plain constants that are otherwise dropped (and unresolvable as import targets).
    """

    name: str
    qualified_name: str
    file_path: str
    start_line: int
    end_line: int
    kind: str = "const"  # const / let / var / assignment
    is_exported: bool = False
    # RHS shape hint: object / array / call / new / literal / reference / function
    value_kind: str = ""


@dataclass(slots=True)
class ParsedEnvVar:
    """An environment variable reference found in source or config."""

    name: str
    source_file: str  # File where it was defined/referenced
    default_value: str | None = None
    required: bool = False  # True for .env.example vars without defaults


@dataclass(slots=True)
class ParsedConfigFile:
    """Config file metadata extracted during parsing."""

    path: str
    config_type: str  # "package", "tsconfig", "dockerfile", "compose", "ci", "env_template"
    properties: dict[str, str] = field(default_factory=dict)  # config-specific properties
    env_vars: list[ParsedEnvVar] = field(default_factory=list)
    line_count: int = 0


@dataclass(slots=True)
class SecurityFinding:
    """A security issue detected during parsing."""

    category: str  # "hardcoded_secret", "sql_injection", "unsafe_call", "llm_output_risk"
    detail: str  # e.g. "eval", "cursor.execute", "AWS_ACCESS_KEY"
    line: int  # line number in source file
    severity: str = "high"  # "high", "medium", "low"


@dataclass(slots=True)
class ParsedFile:
    path: str
    language: str
    classes: list[ParsedClass] = field(default_factory=list)
    functions: list[ParsedFunction] = field(default_factory=list)
    imports: list[ParsedImport] = field(default_factory=list)
    routes: list[ParsedRoute] = field(default_factory=list)
    test_cases: list[ParsedTestCase] = field(default_factory=list)
    variables: list[ParsedVariable] = field(default_factory=list)
    module_docstring: str | None = None
    line_count: int = 0
    is_test_file: bool = False
    todos: list[str] = field(default_factory=list)  # File-level TODOs
    env_var_refs: list[str] = field(default_factory=list)  # Env var names referenced in source
    security_findings: list[SecurityFinding] = field(default_factory=list)  # File-level findings
    auth_middleware_paths: list[str] = field(default_factory=list)  # Path patterns with auth middleware
    react_directive: str | None = None  # "use client" or "use server" (Next.js)


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


@dataclass(slots=True)
class ParsedModelField:
    """A field/column in a database model."""

    name: str
    field_type: str  # Application type: "string", "number", "boolean", "Date", etc.
    db_type: str | None = None  # DB type if explicit: "uuid", "varchar(255)", "text"
    is_primary_key: bool = False
    is_nullable: bool = True  # Default to nullable; ORMs override
    is_unique: bool = False
    is_indexed: bool = False
    has_default: bool = False
    default_value: str | None = None
    is_foreign_key: bool = False
    references_model: str | None = None  # FK target model name
    references_field: str | None = None  # FK target field (usually "id")
    line: int = 0


@dataclass(slots=True)
class ParsedModelRelation:
    """A relationship between two models."""

    target_model: str  # Name of the related model
    relation_type: str  # "one-to-one" | "one-to-many" | "many-to-one" | "many-to-many"
    foreign_key_field: str | None = None  # FK field on this model (for many-to-one)
    through_model: str | None = None  # Junction table (for many-to-many)
    source_field: str | None = None  # ORM relation field name (e.g., Prisma relation field)
    orm_hint: str = ""  # How detected: "prisma_relation", "fk_inference", "decorator"


@dataclass(slots=True)
class ParsedModel:
    """A database model/table definition detected from ORM or schema DSL."""

    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    orm: str  # "prisma" | "drizzle" | "mongoose" | "typeorm" | "sqlalchemy" | "django" | "sequelize"
    table_name: str | None = None  # Explicit table name override (null = inferred from model name)
    primary_key: str | None = None  # PK field name(s)
    is_junction: bool = False
    is_enum: bool = False  # True for enum definitions (Prisma enums, TS enums, etc.)
    docstring: str | None = None
    fields: list[ParsedModelField] = field(default_factory=list)
    relations: list[ParsedModelRelation] = field(default_factory=list)
    source_class_qualified_name: str | None = None  # For ORM class promoter: links back to Class node


@dataclass(slots=True)
class SchemaExtractionResult:
    """Result of schema extraction phase."""

    models_found: int = 0
    fields_found: int = 0
    relations_found: int = 0
    nodes_created: int = 0
    relationships_created: int = 0
