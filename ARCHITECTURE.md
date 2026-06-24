# Gristle Architecture

Internal design reference for contributors. For tool usage, graph schema, configuration, and deployment, see the [Integration Guide](docs/integration-guide.md).

## Quick Reference

| Item | Value |
|------|-------|
| Language | Python 3.11+ |
| Graph DB | FalkorDB (Redis-based, default port 6390) |
| AST engine | tree-sitter (Python, TypeScript, JavaScript) |
| Protocol | MCP (Model Context Protocol) over stdio or Streamable HTTP |
| Entry point | `gristle` CLI or `gristle.mcp.server:main` |
| Package | `pip install -e .` from repo root |

---

## How It Works

```
Repository on disk
       |
       v
  +-----------+     +------------------+     +-----------+
  | File       | --> | Language Parsers  | --> | Parsed    |
  | Walker     |     | (tree-sitter)    |     | Models    |
  +-----------+     +------------------+     +-----------+
                                                    |
                                                    v
                                          +------------------+
                                          | Ingestion        |
                                          | Pipeline         |
                                          | (3 phases)       |
                                          +------------------+
                                                    |
                                                    v
                                          +------------------+
                                          | FalkorDB Graph   |
                                          | (Cypher queries) |
                                          +------------------+
                                                    |
                                                    v
                                          +------------------+
                                          | MCP Tools        |
                                          | (30 tools + 2 resources) |
                                          +------------------+
                                                    |
                                                    v
                                            AI Agent (Claude)
```

1. **Walk** the repository, respecting `.gitignore` and size limits.
2. **Parse** each file with a tree-sitter-based parser, producing typed models (functions, classes, imports, routes, tests).
3. **Build the graph** in three phases: nodes first, then cross-file edges, then documentation links.
4. **Expose MCP tools** so an AI agent can query the graph for exploration, impact analysis, search, and more.

---

## Project Layout

```
src/gristle/
  __init__.py              # Version (0.1.0)
  config.py                # Pydantic settings, env-var driven (GRISTLE_ prefix), with field validators
  models.py                # All parsed data models (dataclasses)
  graph/
    client.py              # FalkorDB wrapper, per-repo graph isolation
    schema.py              # Index creation (33 property indexes, 2 full-text)
  parsers/
    base.py                # Abstract LanguageParser base class
    registry.py            # Extension-based parser dispatch
    python.py              # Python parser (tree-sitter)
    typescript.py          # TypeScript + JavaScript parsers (tree-sitter)
    markdown.py            # Markdown document parser (regex-based)
    config.py              # Config file parser (package.json, Dockerfile, CI, .env)
    env_vars.py            # Regex-based env var reference detection
    security.py            # Security pattern detection (secrets, SQL injection, unsafe calls, LLM risks)
  ingestion/
    walker.py              # .gitignore-aware file discovery (source + config walkers)
    pipeline.py            # Three-phase + config graph builder (~2000 lines, core logic)
    batch.py               # BatchCollector for UNWIND-based bulk writes
    watcher.py             # Async file watcher for incremental updates
    dependency_checker.py  # Dependency staleness + vulnerability checking (npm/PyPI/OSV)
  query/
    engine.py              # 30+ Cypher query templates for code analysis
  search/
    embeddings.py          # Optional semantic search (sentence-transformers)
  logging.py               # Structured logging (JSON for prod, coloured text for dev)
  mcp/
    server.py              # MCP server, 30 tools + 2 resources

tests/
  conftest.py              # Shared pytest fixtures (sample Python code)
  fixtures/sample_python/  # Sample files for testing
  test_parser.py           # Python parser unit tests
  test_typescript_parser.py
  test_markdown_parser.py
  test_walker.py
  test_call_resolution.py  # Cross-file resolution integration tests
  test_batch.py            # BatchCollector and batch graph client tests
  test_query_engine.py     # Query engine method tests (all 20 methods)
  test_mcp_server.py       # MCP server tool, resource, and entry point tests
  test_config.py           # Settings validators (port, batch size, transport)
  test_graph_client.py     # GraphClient, QueryResult, sanitize, batch ops
  test_schema.py           # Index creation and error suppression
  test_logging.py          # JSON/Text formatters, configure_logging, Timer
  test_watcher.py          # start/stop/is_watching helpers
  test_parser_registry.py  # Parser dispatch and build_default
  test_auth.py             # ApiKeyVerifier token validation
  test_embeddings.py       # CodeEmbedder and SemanticIndex (mocked model)
  test_callback_detection.py # Callback/handler detection (PASSED_TO edges) for TS/JS and Python
  test_code_quality.py     # Dead export detection, import cycle detection, public API mapping
  test_type_flow.py        # Type field extraction, typed params, generic unwrapping, data contracts
  test_security.py         # Security detection: secrets, SQL injection, unsafe calls, LLM risks, MCP tools
  test_dependency_checker.py # Dependency staleness, vulnerability checking, version utils, API mocks
```

---

## Data Models

All models are defined in `src/gristle/models.py` as `@dataclass(slots=True)`.

For the full graph schema (node types, edge types, indexes), see the [Integration Guide](docs/integration-guide.md#graph-schema).

### ParsedFunction

Represents a function or method.

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Short name |
| `qualified_name` | `str` | `file_path::ClassName.method` or `file_path::func` |
| `file_path` | `str` | Relative path within repo |
| `start_line` / `end_line` | `int` | Source location |
| `signature` | `str` | Full signature text |
| `docstring` | `str \| None` | First docstring |
| `decorators` | `list[str]` | Decorator names |
| `is_async` | `bool` | async def |
| `is_static` | `bool` | @staticmethod |
| `is_classmethod` | `bool` | @classmethod |
| `is_property` | `bool` | @property |
| `is_exported` | `bool` | Exported from module (Python: in `__all__`; JS/TS: `export` keyword) |
| `is_component` | `bool` | Returns JSX (React component) |
| `is_test` | `bool` | test_ prefix or test framework function |
| `is_entry_point` | `bool` | Route handler, main(), page default export |
| `entry_point_reason` | `str \| None` | Why it's an entry point (e.g. `"route_handler"`, `"react_component"`, `"nextjs_page"`, `"pytest_fixture"`, `"cli_command"`) |
| `is_fixture` | `bool` | @pytest.fixture |
| `is_callback` | `bool` | Target of a PASSED_TO edge (function passed as argument) |
| `is_documentation` | `bool` | True if function is in a documentation directory (docs/, design/, stories/, examples/, fixtures/, mocks/) |
| `visibility` | `str` | `"public"` / `"protected"` / `"private"` |
| `return_type` | `str \| None` | Return type annotation |
| `complexity` | `int` | Cyclomatic complexity (default 1) |
| `calls` | `list[str]` | Raw call names extracted from body |
| `callback_refs` | `list[tuple[str, str]]` | `(callee_name, context)` — function references passed as arguments (middleware, route_handler, callback, array_method, argument) |
| `parameters` | `list[str]` | Parameter names (excluding self/cls) |
| `todos` | `list[str]` | TODO/FIXME/HACK comments in body |

### ParsedClass

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Class name |
| `qualified_name` | `str` | `file_path::ClassName` |
| `bases` | `list[str]` | Base class names |
| `methods` | `list[ParsedFunction]` | All methods |
| `kind` | `str` | `"class"` / `"interface"` / `"type"` / `"enum"` |
| `is_abstract` | `bool` | Has ABC base or abstract methods |
| `is_exported` | `bool` | Exported from module |
| `docstring` | `str \| None` | Class docstring |
| `decorators` | `list[str]` | Class-level decorators |

### ParsedImport

| Field | Type | Description |
|-------|------|-------------|
| `module_path` | `str` | What is being imported (`os.path`, `./utils`) |
| `imported_names` | `list[str]` | Named imports (`join`, `exists`) |
| `aliases` | `dict[str, str]` | Import aliases (`{pd: pandas}`) |
| `is_relative` | `bool` | Relative import (starts with `.`) |
| `is_wildcard` | `bool` | `from x import *` |

### ParsedTestCase

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Test function/class name |
| `block_type` | `str` | `"describe"` / `"it"` / `"test"` / `"class"` |
| `parent_describe` | `str \| None` | Enclosing Test class name |
| `parametrize_count` | `int` | Number of @pytest.mark.parametrize variants (0 = not parametrized) |

### ParsedRoute

| Field | Type | Description |
|-------|------|-------------|
| `method` | `str` | HTTP method (`GET`, `POST`, etc.) |
| `path` | `str` | Route path (`/api/users/:id`) |
| `handler_name` | `str` | Handler function name |
| `middleware` | `list[str]` | Middleware names |

### ParsedFile

Container for all entities parsed from a single source file.

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | Relative file path |
| `language` | `str` | Language (python, typescript, javascript, markdown) |
| `classes` | `list[ParsedClass]` | Classes defined in file |
| `functions` | `list[ParsedFunction]` | Functions defined in file |
| `imports` | `list[ParsedImport]` | Import statements |
| `routes` | `list[ParsedRoute]` | HTTP routes |
| `test_cases` | `list[ParsedTestCase]` | Test cases |
| `module_docstring` | `str \| None` | Module-level docstring |
| `line_count` | `int` | Total lines |
| `is_test_file` | `bool` | True if in test directory |
| `is_documentation` | `bool` | True if file is in docs/, design/, stories/, examples/, fixtures/, mocks/ directory |
| `react_directive` | `str` | "use client" or "use server" if detected in file, else empty string |
| `todos` | `list[str]` | TODO/FIXME/HACK comments |
| `env_var_refs` | `list[str]` | Environment variable references |
| `auth_middleware_paths` | `list[str]` | Auth middleware path patterns |

### ParsedEnvVar

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Env var name (e.g. `DATABASE_URL`) |
| `source_file` | `str` | File where defined/referenced |
| `default_value` | `str \| None` | Default value if set |
| `required` | `bool` | True for vars without defaults in `.env.example` |

### ParsedConfigFile

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | File path |
| `config_type` | `str` | `package` / `tsconfig` / `dockerfile` / `compose` / `ci` / `env_template` |
| `properties` | `dict[str, str]` | Config-specific properties (e.g. `config_base_image`, `config_scripts`) |
| `env_vars` | `list[ParsedEnvVar]` | Env vars defined in this config file |
| `line_count` | `int` | Line count |

### ParsedDocument (Markdown)

| Field | Type | Description |
|-------|------|-------------|
| `path` | `str` | File path |
| `title` | `str` | First H1 or filename |
| `doc_type` | `str` | `readme` / `changelog` / `architecture` / `guide` / `adr` / `other` |
| `sections` | `list[DocumentSection]` | Heading-delimited sections |
| `code_references` | `list[CodeReference]` | References to code entities |

### ParsedModel (Schema Extraction)

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Model/table name |
| `qualified_name` | `str` | `file_path::name` |
| `file_path` | `str` | Source file path |
| `line_start` / `line_end` | `int` | Source location |
| `orm` | `str` | `prisma` / `drizzle` / `typeorm` / etc. |
| `table_name` | `str \| None` | Explicit DB table name |
| `primary_key` | `str \| None` | PK field name(s) |
| `is_enum` | `bool` | True for enum definitions |
| `fields` | `list[ParsedModelField]` | Model columns |
| `relations` | `list[ParsedModelRelation]` | Relationships to other models |

### ParsedModelField

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Column name |
| `field_type` | `str` | Application type (string, number, boolean, Date) |
| `db_type` | `str \| None` | DB-level type (uuid, varchar, etc.) |
| `is_primary_key` | `bool` | PK field |
| `is_nullable` | `bool` | Nullable (default True, Prisma overrides to False) |
| `is_foreign_key` | `bool` | FK field |
| `references_model` | `str \| None` | FK target model |

### SchemaExtractionResult

| Field | Type | Description |
|-------|------|-------------|
| `models_found` | `int` | Total models detected |
| `fields_found` | `int` | Total fields across all models |
| `relations_found` | `int` | Total relations detected |
| `nodes_created` | `int` | Graph nodes written |
| `relationships_created` | `int` | Graph edges written |

---

## Language Parsers

All parsers extend `LanguageParser` (abstract base in `parsers/base.py`) and are dispatched by file extension through `ParserRegistry`.

### Python Parser (`parsers/python.py`)

**Extensions:** `.py`, `.pyi`

**Extracts:**
- **Imports:** `import x`, `from x import y`, relative imports, aliases
- **Classes:** name, bases, methods, docstring, decorators, nested classes inside functions
- **Functions:** name, signature, parameters, decorators, docstring, return type, calls, complexity
- **Routes:** FastAPI/Flask/Django route decorators with path extraction (`@app.get("/users")` -> `path="/users"`, `@route`, `@api_view`, `@websocket`)
- **Tests:** `test_*` functions as `ParsedTestCase`, `Test*` classes as test groups
- **Parametrize:** `@pytest.mark.parametrize` variant counts
- **Fixtures:** `@pytest.fixture` detection
- **Entry points:** `main()`, `@pytest.fixture`, `__init__`, Django views, `@click.command`/`@typer.command`, `@app.get()`/`@router.post()` route handlers, `Depends()`/`@inject` dependency injection — each sets `entry_point_reason`
- **TODOs:** `TODO`, `FIXME`, `HACK`, `XXX`, `BUG`, `WARN` comments
- **Visibility:** `public` (normal), `protected` (`_prefix`), `private` (`__prefix`), dunders are public
- **Complexity:** Cyclomatic complexity via branch counting (`if`, `elif`, `for`, `while`, `except`, `and`, `or`)
- **Exports:** `__all__` declaration parsing — functions/classes listed in `__all__` are marked `is_exported=True`

**Call extraction:** Walks the function body AST for `call` nodes. Resolves `self.method` to `ClassName.method` within the parser itself.

**Callback detection:** Identifies function references passed as arguments to known higher-order patterns. Detects: `map(fn, iter)`, `filter(fn, iter)`, `sorted(iter, key=fn)`, `.connect(fn)`, `.on(event, fn)`, `.add_middleware(fn)`, `.add_route(path, fn)`. Each produces a `(callee_name, context)` tuple on `callback_refs`.

### TypeScript/JavaScript Parser (`parsers/typescript.py`)

**Extensions:** `.ts`, `.tsx` (TypeScript), `.js`, `.jsx` (JavaScript)

**Extracts:**
- **Imports:** ESM `import { x } from 'y'`, default imports, namespace imports, dynamic `import()` and `require()` calls
- **Exports:** `export function`, `export default`, `export { x }`, re-exports
- **Classes:** ES6 classes, abstract classes
- **Functions:** Regular, arrow, async, generators
- **Interfaces, Types, Enums:** TypeScript-specific constructs
- **Components:** React components (PascalCase functions returning JSX)
- **Routes:** Express/Hono/Fastify route patterns (`router.get()`, `app.post()`), Supabase/Deno edge functions (`serve()`)
- **Re-exports:** Barrel file patterns (`export { X } from`, `export * from`, `export { default as Y } from`)
- **Tests:** Jest/Mocha/Vitest patterns (`describe`, `it`, `test`, `beforeAll`, etc.)
- **Next.js:** App router detection (page, route, layout, loading, error files)
- **Supabase/Deno:** `serve()` / `Deno.serve()` entry point detection and route extraction
- **Entry points:** Next.js pages, Storybook stories, React components, serverless handlers (`handler` export), barrel-only hooks (`use*` in `index.ts`), `main()` exports, route handlers — each sets `entry_point_reason`
- **Module docstrings:** Leading JSDoc blocks, `@module`/`@fileoverview` tags, or `//` comments extracted as `module_docstring`
- **Auth middleware paths:** Detects `app.use('/path', authMiddleware)` patterns where middleware names contain auth keywords (auth, jwt, clerk, guard, verify, etc.). Extracted as `ParsedFile.auth_middleware_paths` for route auth detection in the pipeline.
- **Callback detection:** Identifies function references passed as arguments to known patterns: `.use(fn)` (middleware), `.get/.post(path, fn)` (route_handler), `.on/.addEventListener(event, fn)` (callback), `.then/.catch(fn)` (callback), `.map/.filter/.forEach/.reduce/.sort(fn)` (array_method). Each produces a `(callee_name, context)` tuple on `callback_refs`.
- **JSX callback detection:** React event handler props (`onClick`, `onChange`, `onSubmit`, etc.) are extracted as callback references with context `jsx_callback`. Only `on*` attributes with PascalCase suffix are captured; inline arrow functions are ignored.

### Config Parser (`parsers/config.py`)

Not a language parser (doesn't extend `LanguageParser`). Dispatches by exact filename or path pattern rather than extension.

**Supported files:** `package.json`, `tsconfig.json`, `Dockerfile`, `docker-compose.yml`/`compose.yaml`, `.github/workflows/*.yml`, `.env.example`/`.env.template`/`.env.sample`, `requirements.txt`, `pyproject.toml`

**Extracts per file type:**
- **package.json:** scripts, engines -> `config_scripts`, `config_engines`
- **tsconfig.json:** target, module, paths -> `config_target`, `config_module`, `config_paths`
- **Dockerfile:** base image, exposed ports, ENV/ARG directives -> `config_base_image`, `config_exposed_ports`, `ParsedEnvVar` list
- **docker-compose.yml:** service names -> `config_services`
- **CI workflows:** triggers, job names -> `config_triggers`, `config_jobs`
- **.env templates:** variable names, defaults, required flags -> `ParsedEnvVar` list

### Env Var Scanner (`parsers/env_vars.py`)

Regex-based env var reference detection. Integrated into Python, TypeScript, and JavaScript parsers to populate `ParsedFile.env_var_refs`.

**Python patterns:** `os.environ["X"]`, `os.environ.get("X")`, `os.getenv("X")`
**TS/JS patterns:** `process.env.X`, `process.env["X"]`, `Deno.env.get("X")`

### Markdown Parser (`parsers/markdown.py`)

**Extracts:**
- Headings (H1-H6) and section boundaries
- Inline code references (backtick-delimited)
- File path references (`src/`, `lib/`, `app/`, etc.)
- Markdown links to source files
- PascalCase identifiers, dotted names, React hooks
- Document type classification (readme, changelog, ADR, architecture, guide)

---

## Ingestion Pipeline

The pipeline (`ingestion/pipeline.py`, ~2000 lines) is the core of Gristle. It runs in three phases plus a config phase, each using a `BatchCollector` (`ingestion/batch.py`) to group writes into batched Cypher `UNWIND` queries rather than individual round-trips:

### Phase 1: Parse & Build Nodes

1. `walk_repo()` discovers all supported files (respecting `.gitignore`, size limits, excluded dirs).
2. Each file is parsed by the appropriate language parser.
3. For each parsed file, the pipeline creates:
   - A `File` node
   - `Class` nodes with `CONTAINS` edges from the file
   - `Function` nodes with `CONTAINS` edges from the file (or class for methods)
   - `Import` nodes with `CONTAINS` edges
   - `Route` nodes with `CONTAINS` edges
   - `TestCase` nodes with `CONTAINS` edges
4. In-memory maps are populated for Phase 2:
   - `_qualified_map`: `qualified_name -> node_id` (unique)
   - `_short_to_candidates`: `name -> [node_ids]` (for global name resolution)
   - `_file_entities`: `file_path -> {name -> node_id}` (all entities in a file)
   - `_exported_file_entities`: same but only exported entities
   - `_path_to_id`, `_stem_to_id`, `_pymodule_to_id`: path-based lookups for import resolution
   - `_class_methods`: `class_id -> {method_name -> func_id}` (for inheritance resolution)
   - `_fixture_map`: `fixture_name -> func_id` (for fixture edges)

### Phase 2: Resolve Cross-File Edges

This phase creates all relationship edges that require cross-file knowledge:

1. **Detect Python source roots** (e.g., `src/`, `lib/`) for module path resolution.
2. **Register stripped module keys** so `from mypackage.utils import x` resolves correctly.
3. **Resolve CALLS edges** for every function's `calls` list using a 6-step strategy (see below).
4. **Resolve PASSED_TO edges** for every function's `callback_refs` list using the same 6-step resolution. Each edge carries a `context` property (middleware, route_handler, callback, array_method, argument). Target functions are marked `is_callback=true` via batch update.
5. **Resolve INHERITS_FROM edges** between classes, populating `_class_bases` for MRO walking.
6. **Resolve IMPORTS edges** (File -> File) based on import statements. Each import is tracked as `resolved=True` (internal) or `resolved=False` (external/unresolved).
7. **Resolve TESTS edges** (test File -> production File) by matching import paths.
8. **Resolve USES_FIXTURE edges** (test Function -> fixture Function) by parameter name matching.
9. **Resolve TESTS_FUNCTION edges** (test Function -> production Function) by walking test functions' CALLS edges to depth 2, with import-based depth-3 fallback for JS/TS, then updating `tested_by_count` on production functions.
10. **Resolve USES_DEPENDENCY edges** for external packages.

### Config Phase: Config Files & Environment Variables

Runs after Phase 2 (so source file entities exist for USES_ENV edges):

1. `walk_config_files()` discovers config files by filename/path pattern (separate from source walker).
2. Each config file is parsed by `parse_config_file()`.
3. Creates `File` nodes with `config_type` property and config-specific properties.
4. Creates `EnvVar` nodes for env vars defined in config files, with `DEFINED_IN` edges.
5. For source files with `env_var_refs`, creates `EnvVar` nodes (if not already created) and `USES_ENV` edges.
6. Deduplication: `EnvVar` nodes are keyed by name, so a var defined in `.env.example` and referenced in source creates only one node.

### Schema Phase: ORM Model Detection

Runs after Config Phase (needs `INHERITS_FROM` edges for ORM base class detection):

1. `SchemaExtractor` receives walked files and `_path_to_id` map from pipeline.
2. **Prisma DSL** (`.prisma` files): Regex-based parser extracts `model` and `enum` blocks with brace-counting.
3. **Drizzle ORM** (`.ts`/`.js` files): Detects `pgTable`/`mysqlTable`/`sqliteTable` calls, extracts columns and FK references.
4. **ORM class promoter** (P1/P2 stub): Framework placeholder for TypeORM, SQLAlchemy, Django, etc.
5. Creates `Model` and `ModelField` nodes, plus `CONTAINS`, `HAS_MODEL_FIELD`, `REFERENCES`, `RELATED_TO`, and `PROMOTED_FROM` edges.
6. Creates `File` nodes for `.prisma` files (not created by Phase 1 since no parser registered).

### Phase 3: Process Documentation

1. Walk the repo again for `.md` / `.mdx` files.
2. Parse each with `MarkdownParser`.
3. Create `Document` and `DocumentSection` nodes.
4. Resolve `REFERENCES` edges from doc sections to code entities.

### Call Resolution Strategy (6-step priority)

When resolving a call like `validate_email` or `self.dump()`:

1. **Exact qualified name** — If the call is already fully qualified (`src/db/client.ts::query`), look it up directly.
2. **File-scoped qualified name** — Try `{file_path}::{call_name}` in the qualified map.
3. **Dotted calls** — Handle `self.method`, `this.method`, `ClassName.method`, `obj.method`:
   - `self.*` / `this.*`: resolve to the enclosing class's method.
   - `ClassName.method`: check file-scoped class methods.
   - If not found on the class, **walk the inheritance chain** (MRO) via `_resolve_inherited_method`.
4. **Import-aware** — Check what the file imports, resolve through imported entities. This includes resolution through barrel files (Python `__init__.py` and TS/JS `index.ts`) — re-exported names are followed to their original definition.
5. **Same-file entity** — Check if the name exists in the same file's entity map.
6. **Single-candidate global** — If exactly one function globally has this name, use it as fallback.

### Inheritance-Aware Resolution

When a method like `self.dump()` is called in `MySchema(Schema)` but `MySchema` doesn't define `dump`:

1. The parser rewrites `self.dump()` to `MySchema.dump`.
2. Phase 2 finds `MySchema.dump` has no direct match.
3. `_resolve_inherited_method` walks `MySchema`'s bases (`Schema`), checks `Schema`'s methods, and finds `Schema.dump`.
4. The CALLS edge points to `func::src/schema.py::Schema.dump`.

### Barrel File (Re-export) Resolution

Both Python and TypeScript/JavaScript use "barrel files" that re-export entities from sibling modules:

- **Python:** `__init__.py` with `from .schema import Schema` makes `Schema` available via the package namespace.
- **TypeScript/JavaScript:** `index.ts` with `export { Button } from './Button'` or `export * from './utils'` makes those names importable from the directory.

During Phase 2, `_build_init_reexport_maps()` scans all barrel files and uses fixed-point iteration (up to 5 passes) to resolve multi-level barrel chains (barrel -> barrel -> definition). This enables `import { Button } from './components'` to resolve through `components/index.ts` to the actual `Button.tsx` definition, even when barrel files re-export from other barrel files.

Supported re-export patterns (TS/JS):
- Named: `export { Foo, Bar } from './module'`
- Wildcard: `export * from './module'`
- Aliased: `export { default as Foo } from './module'`
- Type: `export type { Baz } from './module'`

### Fixture Resolution

pytest injects fixtures by matching test function parameter names to fixture names:

1. During Phase 1, functions decorated with `@pytest.fixture` are recorded in `_fixture_map`.
2. During Phase 2, for every test function, each parameter name is checked against `_fixture_map`.
3. Matches produce `USES_FIXTURE` edges.

### TESTS_FUNCTION Resolution

Function-level test coverage is derived from the call graph:

1. During CALLS edge resolution, an in-memory adjacency map (`caller_id -> [callee_id]`) and a set of test function IDs are built.
2. For each test function (`is_test = true`), the pipeline walks its CALLS edges to depth 2.
3. Any non-test `Function` reached gets a `TESTS_FUNCTION` edge with a `depth` property (1 = direct call, 2 = via helper).
4. Direct coverage (depth 1) takes priority — if a test calls a function both directly and indirectly, only the depth-1 edge is created.
5. **Import-based fallback (depth 3, JS/TS only):** For test functions in non-Python test files that have no depth 1-2 coverage, the pipeline checks what production files the test file imports and creates depth-3 edges to exported functions in those files. This handles TS/JS test helpers that use `import { validate } from '../validate'` without explicit calls visible in the AST.
6. `tested_by_count` is computed for each production function and written to the graph via a batched `UNWIND` update.

### Route Auth Detection

Route nodes include a `has_auth` boolean property computed from three sources:

1. **Per-route middleware:** Middleware names on the `ParsedRoute` are checked for auth keywords (`auth`, `jwt`, `protect`, `guard`, `verify`, `session`, etc.).
2. **Handler decorators:** If the route's handler function has decorators containing auth keywords (e.g., `@login_required`, `@jwt_required`).
3. **App-level auth middleware:** The TS parser extracts `app.use('/path', authMiddleware)` patterns. During route building, route paths are matched against these patterns. Same-file `*` wildcards apply to all routes in that file; cross-file `*` wildcards are ignored (sub-router scoping). Explicit path patterns like `/api/admin/*` apply across files.

### Unlinked Route Handler Resolution

During Phase 1, route handler resolution checks the global ID map and file-scoped names. If a handler can't be resolved (e.g. Supabase edge functions where the handler is imported from a shared module), the route is tracked as "unlinked". In Phase 2, after all import maps are built, unlinked routes are resolved via `_get_imported_entities()` — the same import-aware resolution used for CALLS edges.

### Import Resolution Tracking

Each import is tracked as resolved or unresolved during Phase 2. When an import's module path resolves to an internal file via the path/stem/module lookup maps, it's marked `resolved=True`. External/unresolved imports get `resolved=False`. This property is written to Import nodes via a batched UNWIND update.

### Dependency Version Extraction

After Phase 1 and before Phase 2, the pipeline reads version information from manifest files:

- `package.json`: `dependencies` + `devDependencies`
- `requirements.txt`: packages with version specifiers (e.g., `flask==2.3.0`)
- `pyproject.toml`: `[project] dependencies` array

The extracted versions are stored in `_dependency_versions` and attached to `Dependency` nodes as the `version` property during Phase 2.

---

## Batch Collector

`ingestion/batch.py` provides `BatchCollector`, a write buffer that groups node and relationship creation by label/type and flushes them via `UNWIND` queries in configurable chunks.

```
BatchCollector(graph, batch_size=200)
  |-- add_node(label, properties)           -> buffers into dict[label, list[dict]]
  |-- add_relationship(rel_type, ...)       -> buffers CREATE rels by type
  |-- add_merge_relationship(rel_type, ...) -> buffers MERGE rels by type
  +-- flush() -> dict[str, int]             -> flushes nodes first, then rels, chunked by batch_size
```

Flush order matters: nodes are created before relationships so that `MATCH` clauses in relationship queries find the target nodes. Each phase of the pipeline creates its own `BatchCollector` and flushes once at the end, reducing a 500-file repo from ~15,000 round-trips to ~2,500.

---

## Error Handling

All database and I/O operations use targeted exception types rather than bare `except Exception`:

| Module | Exception | Scenario |
|--------|-----------|----------|
| `graph/client.py` | `ResponseError` | FalkorDB query failures |
| `graph/client.py` | `ConnectionError` | FalkorDB unreachable |
| `graph/schema.py` | `ResponseError` | Index already exists |
| `ingestion/pipeline.py` | `OSError`, `UnicodeDecodeError` | File read/parse failures |
| `ingestion/watcher.py` | `OSError`, `UnicodeDecodeError` | File change processing |
| `ingestion/walker.py` | `OSError` | `.gitignore` read failure |
| `search/embeddings.py` | `ResponseError` | Vector index/search failures |

Errors are logged with context (file path, operation, error message) via the structured logging system.

---

## File Walker

`ingestion/walker.py` discovers files in a repo:

- `walk_repo()`: discovers source files filtered by parser-supported extensions
- `walk_config_files()`: discovers config files by filename/path pattern (Dockerfile, package.json, CI workflows, etc.)
- Respects `.gitignore` patterns (parsed via `pathspec`)
- Skips configured excluded directories
- Filters by file size (default max 500KB)
- Detects binary files (null-byte heuristic)
- Returns forward-slash-normalized relative paths

---

## Graph Client

`graph/client.py` wraps FalkorDB with per-repo isolation:

- Each repo gets its own graph: `gristle_{sanitized_repo_id}`
- `repo_id` is either user-provided or a SHA-256 hash of the repo path
- Single-item methods: `execute(cypher)`, `create_node(label, props)`, `create_relationship(from, to, type)`, `merge_relationship(from, to, type)`, `clear()`, `drop()`
- Batch methods (Cypher `UNWIND`): `batch_create_nodes(label, items)`, `batch_create_relationships(rel_type, items)`, `batch_merge_relationships(rel_type, items)` — used by the ingestion pipeline for bulk writes
- Returns `QueryResult` objects with `records` (list of dicts) and `summary` (stats)
- Exception handling: `ResponseError` for FalkorDB/Redis failures, `ConnectionError` for network issues

---

## Query Engine

`query/engine.py` provides 30+ pre-built Cypher query methods:

| Method | Description |
|--------|-------------|
| `get_function_context(name)` | Function + callers + callees + class + source code |
| `get_class_structure(name)` | Class + methods + full inheritance chain |
| `get_file_overview(path)` | All entities in file + routes + test coverage |
| `get_callers(name, depth)` | Transitive callers up to N hops |
| `get_callees(name, depth)` | Transitive callees up to N hops |
| `impact_analysis(name)` | Blast radius: callers, affected files, test coverage, routes |
| `get_impact_analysis(name, include_source)` | Enhanced impact analysis with scoring (0-100) and risk levels |
| `search(term, type)` | Search by name, docstring, or both |
| `get_repo_overview()` | Node/edge stats, file list, languages, most-called functions |
| `get_docs_for_entity(name)` | Documentation referencing an entity |
| `get_doc_staleness()` | Docs with unresolved references |
| `get_doc_overview()` | Doc type counts, reference stats |
| `get_routes(method?)` | All HTTP routes, optional method filter |
| `get_components(limit)` | React components with usage counts |
| `get_tests_for_entity(name)` | Tests exercising an entity (TESTS_FUNCTION edges + CALLS fallback + file coverage) |
| `get_function_coverage(name)` | Detailed coverage for a function: tested_by_count + which tests at what depth |
| `get_untested_functions()` | Exported functions with no test coverage (uses `tested_by_count`) |
| `get_untested_critical(limit)` | Exported functions with callers but zero test coverage |
| `get_todos(limit)` | Files with TODO counts |
| `infer_conventions()` | Project patterns: languages, routes, components, tests, imports, layer violations |
| `get_dependencies(limit)` | External packages ranked by usage |
| `get_dependency_users(name)` | Files/functions using a specific package |
| `get_env_vars()` | All env vars with definitions and usage |
| `get_config_files()` | Config files with types and properties |
| `get_setup_requirements()` | Full setup checklist: env vars, config files, dependencies |
| `detect_layer_violations(config?)` | Architectural layer violations from IMPORTS edges |
| `detect_dead_exports()` | Exported entities never imported (dead public API surface) |
| `detect_import_cycles(max_length?)` | Circular import dependencies, grouped by cycle length |
| `get_public_api(include_internal?)` | All public API entities with documentation stats |
| `get_data_contract(name)` | Input/output data contract for a function (ACCEPTS/RETURNS edges + fields) |
| `get_type_usage(name)` | All usage of a type: accepted_by, returned_by, referenced_in_fields |
| `detect_security_issues()` | Functions with security findings (secrets, SQL injection, unsafe calls, LLM risks) |
| `detect_unauthenticated_routes()` | Routes lacking auth decorators or middleware |
| `get_security_overview()` | Combined security overview: code findings + unauthenticated routes + vulnerable deps |
| `get_outdated_dependencies(severity)` | Outdated dependencies with optional vulnerability filtering |
| `find_path(from, to, hops)` | Call paths between two entities |

---

## Semantic Search (Optional)

`search/embeddings.py` provides vector-based code search using `sentence-transformers/all-MiniLM-L6-v2` (384 dimensions, ~22MB, CPU-friendly).

- `CodeEmbedder`: generates embeddings from function/class names + signatures + docstrings
- `SemanticIndex`: creates FalkorDB vector indexes, indexes all entities, runs similarity queries
- Enabled via `pip install gristle[search]`, then `gristle_embed` tool call

---

## File Watcher

`ingestion/watcher.py` provides incremental re-indexing:

- Uses `watchfiles.awatch()` for efficient file monitoring
- Debounces changes (configurable, default 2s)
- Deduplicates changes per file
- Calls `pipeline.update_file()` for each changed file
- State tracked in `_active_watchers` dict, manageable via `gristle_watch` tool

---

## Logging

`logging.py` provides structured logging with two formatters:

- **`JSONFormatter`** — Machine-readable JSON lines for production (auto-selected when transport is `streamable-http`). Fields: `ts`, `level`, `logger`, `msg`, plus any extra keys.
- **`TextFormatter`** — Coloured, human-readable output for development (auto-selected when transport is `stdio`).

`configure_logging(transport)` is called at startup and auto-detects the format from the transport mode. Override with `GRISTLE_LOG_FORMAT=json` or `GRISTLE_LOG_FORMAT=text`.

A `Timer` context manager is used in the MCP server to measure ingestion duration, emitting `duration_ms` in log entries and tool responses.

---

## Key Design Decisions

1. **Graph over vectors.** Structural relationships (who calls what, what inherits from where) are first-class citizens, not lost in embedding space.
2. **Per-repo isolation.** Each repository gets its own FalkorDB graph namespace, preventing cross-contamination.
3. **Three-phase ingestion.** Nodes must exist before edges can reference them. Docs are processed last because they reference code entities.
4. **Import-aware call resolution.** A call to `query()` is resolved differently depending on what the calling file imports, not just global name matching.
5. **Inheritance-aware MRO walking.** `self.method()` correctly resolves through the inheritance chain when the method isn't defined on the immediate class.
6. **Barrel file resolution.** Both Python `__init__.py` and TS/JS `index.ts` re-exports are followed to their original definitions, so imports through barrel files produce correct CALLS edges.
7. **Tree-sitter for AST.** Fast, incremental, language-agnostic parsing framework. No need to execute or type-check the code.
8. **MCP as the interface.** Tools are exposed via the Model Context Protocol, making Gristle usable by any MCP-compatible AI agent.
9. **Optional semantic search.** The core graph is useful without embeddings. Semantic search is an add-on for natural language queries.
10. **Batched writes.** The ingestion pipeline buffers all graph writes per phase and flushes them in `UNWIND` chunks, reducing network round-trips by ~80% for remote FalkorDB instances.
11. **Targeted exception handling.** All `except` blocks catch specific exception types (`ResponseError`, `OSError`, `ConnectionError`) rather than bare `Exception`, with structured logging for diagnostics.
