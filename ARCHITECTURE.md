# Gristle Architecture

Graph-based code intelligence for AI agents. Gristle parses source repositories into a FalkorDB graph database, preserving structural relationships (calls, imports, inheritance, data flow) so AI agents can query code structure through graph traversal rather than vector search over chunked text.

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
                                          | (19 tools + 2 resources) |
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
    schema.py              # Index creation (24 property indexes, 2 full-text)
  parsers/
    base.py                # Abstract LanguageParser base class
    registry.py            # Extension-based parser dispatch
    python.py              # Python parser (tree-sitter)
    typescript.py          # TypeScript + JavaScript parsers (tree-sitter)
    markdown.py            # Markdown document parser (regex-based)
    config.py              # Config file parser (package.json, Dockerfile, CI, .env)
    env_vars.py            # Regex-based env var reference detection
  ingestion/
    walker.py              # .gitignore-aware file discovery (source + config walkers)
    pipeline.py            # Three-phase + config graph builder (~2000 lines, core logic)
    batch.py               # BatchCollector for UNWIND-based bulk writes
    watcher.py             # Async file watcher for incremental updates
  query/
    engine.py              # 25+ Cypher query templates for code analysis
  search/
    embeddings.py          # Optional semantic search (sentence-transformers)
  logging.py               # Structured logging (JSON for prod, coloured text for dev)
  mcp/
    server.py              # MCP server, 19 tools + 2 resources

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
```

---

## Configuration

All settings use the `GRISTLE_` env prefix. Defined in `src/gristle/config.py` with Pydantic field validators:

| Setting | Default | Description |
|---------|---------|-------------|
| `GRISTLE_FALKORDB_HOST` | `localhost` | FalkorDB host |
| `GRISTLE_FALKORDB_PORT` | `6390` | FalkorDB port (validated: 1–65535) |
| `GRISTLE_FALKORDB_PASSWORD` | *(none)* | FalkorDB password (optional) |
| `GRISTLE_MAX_FILE_SIZE_BYTES` | `512000` | Skip files larger than this (validated: >= 1) |
| `GRISTLE_REPO_STORAGE_PATH` | `./repos` | Where cloned repos are stored |
| `GRISTLE_WATCHER_DEBOUNCE_SECONDS` | `2.0` | File watcher debounce delay |
| `GRISTLE_INGESTION_BATCH_SIZE` | `200` | Nodes/edges per batched UNWIND query (validated: >= 1) |
| `GRISTLE_TRANSPORT` | `stdio` | MCP transport: `stdio` or `streamable-http` (validated) |
| `GRISTLE_HTTP_HOST` | `0.0.0.0` | Bind address for HTTP transport |
| `GRISTLE_HTTP_PORT` | `8080` | HTTP port (Railway overrides via `PORT`) (validated: 1–65535) |
| `GRISTLE_API_KEY` | *(none)* | Bearer token for auth; unset = no auth |
| `GRISTLE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GRISTLE_LOG_FORMAT` | *(auto)* | `json` for structured, `text` for human-readable; auto-detected from transport if unset |

Excluded directories (always skipped): `node_modules`, `.git`, `__pycache__`, `dist`, `build`, `.venv`, `venv`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `egg-info`, `.eggs`.

---

## Data Models

All models are defined in `src/gristle/models.py` as `@dataclass(slots=True)`.

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
| `is_exported` | `bool` | Exported from module (Python: `__all__`; JS: `export`) |
| `is_component` | `bool` | Returns JSX (React component) |
| `is_test` | `bool` | test_ prefix or test framework function |
| `is_entry_point` | `bool` | Route handler, main(), page default export |
| `entry_point_reason` | `str \| None` | Why it's an entry point (e.g. `"route_handler"`, `"react_component"`, `"nextjs_page"`, `"pytest_fixture"`, `"cli_command"`) |
| `is_fixture` | `bool` | @pytest.fixture |
| `visibility` | `str` | `"public"` / `"protected"` / `"private"` |
| `return_type` | `str \| None` | Return type annotation |
| `complexity` | `int` | Cyclomatic complexity (default 1) |
| `calls` | `list[str]` | Raw call names extracted from body |
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

| Field | Type |
|-------|------|
| `path` | `str` |
| `language` | `str` |
| `classes` | `list[ParsedClass]` |
| `functions` | `list[ParsedFunction]` |
| `imports` | `list[ParsedImport]` |
| `routes` | `list[ParsedRoute]` |
| `test_cases` | `list[ParsedTestCase]` |
| `module_docstring` | `str \| None` |
| `line_count` | `int` |
| `is_test_file` | `bool` |
| `todos` | `list[str]` |
| `env_var_refs` | `list[str]` |

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

---

## Graph Schema

### Node Types

| Label | Key Properties | Purpose |
|-------|---------------|---------|
| `File` | `id`, `path`, `language`, `line_count`, `is_test_file`, `todo_count`, `config_type` | Source or config file |
| `Function` | `id`, `name`, `qualified_name`, `file_path`, `start_line`, `signature`, `docstring`, `is_async`, `is_test`, `is_exported`, `is_component`, `is_entry_point`, `entry_point_reason`, `is_fixture`, `complexity`, `decorators`, `visibility`, `return_type`, `tested_by_count` | Function or method |
| `Class` | `id`, `name`, `qualified_name`, `file_path`, `start_line`, `signature`, `docstring`, `bases`, `is_abstract`, `is_exported`, `kind` | Class, interface, type, or enum |
| `Import` | `id`, `file_path`, `line`, `module_path`, `imported_names`, `is_relative` | Import statement |
| `Route` | `id`, `method`, `path`, `handler_name`, `file_path`, `line`, `middleware` | HTTP endpoint |
| `TestCase` | `id`, `name`, `block_type`, `file_path`, `start_line`, `parent_describe`, `parametrize_count` | Test block |
| `Document` | `id`, `path`, `title`, `doc_type`, `line_count`, `reference_count` | Markdown file |
| `DocumentSection` | `id`, `file_path`, `heading`, `level`, `start_line`, `end_line` | Doc section |
| `Dependency` | `id`, `name`, `version` | External package |
| `EnvVar` | `id`, `name`, `default_value`, `required` | Environment variable |

### Edge Types

| Type | From | To | Description |
|------|------|----|-------------|
| `CONTAINS` | File, Class | Function, Class, Import, Route, TestCase | Container relationship |
| `DEFINED_IN` | Function, Class | File | Reverse of CONTAINS |
| `EXPORTS` | File | Function, Class | Module exports |
| `CALLS` | Function | Function | Function call (with `depth` property) |
| `USES_HOOK` | Function | Function | React hook usage (subset of CALLS) |
| `INHERITS_FROM` | Class | Class | Class inheritance |
| `IMPORTS` | File | File | File-level import dependency |
| `TESTS` | File | File | Test file covers production file |
| `TESTS_FUNCTION` | Function | Function | Test function exercises production function (with `depth` property: 1=direct, 2=via helper) |
| `USES_FIXTURE` | Function | Function | Test uses pytest fixture (by parameter name) |
| `USES_DEPENDENCY` | Function | Dependency | Uses external package |
| `DEPENDS_ON` | File | Dependency | File-level external dependency |
| `REFERENCES` | DocumentSection | Function, Class, File | Doc references code |
| `HAS_SECTION` | Document | DocumentSection | Doc contains section |
| `HANDLES` | Route | Function | Route handler |
| `DEFINED_IN` | EnvVar | File | Env var defined in config file |
| `USES_ENV` | File | EnvVar | Source file references env var |

### Indexes

24 property indexes on node `id`, `name`, `qualified_name`, `file_path`, `path`, `module_path`, `method`, `doc_type`. Two full-text indexes on `Function.docstring` and `Class.docstring`.

---

## Language Parsers

All parsers extend `LanguageParser` (abstract base in `parsers/base.py`) and are dispatched by file extension through `ParserRegistry`.

### Python Parser (`parsers/python.py`)

**Extensions:** `.py`, `.pyi`

**Extracts:**
- **Imports:** `import x`, `from x import y`, relative imports, aliases
- **Classes:** name, bases, methods, docstring, decorators, nested classes inside functions
- **Functions:** name, signature, parameters, decorators, docstring, return type, calls, complexity
- **Routes:** FastAPI/Flask/Django route decorators with path extraction (`@app.get("/users")` → `path="/users"`, `@route`, `@api_view`, `@websocket`)
- **Tests:** `test_*` functions as `ParsedTestCase`, `Test*` classes as test groups
- **Parametrize:** `@pytest.mark.parametrize` variant counts
- **Fixtures:** `@pytest.fixture` detection
- **Entry points:** `main()`, `@pytest.fixture`, `__init__`, Django views, `@click.command`/`@typer.command`, `@app.get()`/`@router.post()` route handlers, `Depends()`/`@inject` dependency injection — each sets `entry_point_reason`
- **TODOs:** `TODO`, `FIXME`, `HACK`, `XXX`, `BUG`, `WARN` comments
- **Visibility:** `public` (normal), `protected` (`_prefix`), `private` (`__prefix`), dunders are public
- **Complexity:** Cyclomatic complexity via branch counting (`if`, `elif`, `for`, `while`, `except`, `and`, `or`)

**Call extraction:** Walks the function body AST for `call` nodes. Resolves `self.method` to `ClassName.method` within the parser itself.

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

### Config Parser (`parsers/config.py`)

Not a language parser (doesn't extend `LanguageParser`). Dispatches by exact filename or path pattern rather than extension.

**Supported files:** `package.json`, `tsconfig.json`, `Dockerfile`, `docker-compose.yml`/`compose.yaml`, `.github/workflows/*.yml`, `.env.example`/`.env.template`/`.env.sample`, `requirements.txt`, `pyproject.toml`

**Extracts per file type:**
- **package.json:** scripts, engines → `config_scripts`, `config_engines`
- **tsconfig.json:** target, module, paths → `config_target`, `config_module`, `config_paths`
- **Dockerfile:** base image, exposed ports, ENV/ARG directives → `config_base_image`, `config_exposed_ports`, `ParsedEnvVar` list
- **docker-compose.yml:** service names → `config_services`
- **CI workflows:** triggers, job names → `config_triggers`, `config_jobs`
- **.env templates:** variable names, defaults, required flags → `ParsedEnvVar` list

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
4. **Resolve INHERITS_FROM edges** between classes, populating `_class_bases` for MRO walking.
5. **Resolve IMPORTS edges** (File -> File) based on import statements.
6. **Resolve TESTS edges** (test File -> production File) by matching import paths.
7. **Resolve USES_FIXTURE edges** (test Function -> fixture Function) by parameter name matching.
8. **Resolve TESTS_FUNCTION edges** (test Function -> production Function) by walking test functions' CALLS edges to depth 2, then updating `tested_by_count` on production functions.
9. **Resolve USES_DEPENDENCY edges** for external packages.

### Config Phase: Config Files & Environment Variables

Runs after Phase 2 (so source file entities exist for USES_ENV edges):

1. `walk_config_files()` discovers config files by filename/path pattern (separate from source walker).
2. Each config file is parsed by `parse_config_file()`.
3. Creates `File` nodes with `config_type` property and config-specific properties.
4. Creates `EnvVar` nodes for env vars defined in config files, with `DEFINED_IN` edges.
5. For source files with `env_var_refs`, creates `EnvVar` nodes (if not already created) and `USES_ENV` edges.
6. Deduplication: `EnvVar` nodes are keyed by name, so a var defined in `.env.example` and referenced in source creates only one node.

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

During Phase 2, `_build_init_reexport_maps()` scans all barrel files and uses fixed-point iteration (up to 5 passes) to resolve multi-level barrel chains (barrel → barrel → definition). This enables `import { Button } from './components'` to resolve through `components/index.ts` to the actual `Button.tsx` definition, even when barrel files re-export from other barrel files.

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

1. During CALLS edge resolution, an in-memory adjacency map (`caller_id → [callee_id]`) and a set of test function IDs are built.
2. For each test function (`is_test = true`), the pipeline walks its CALLS edges to depth 2.
3. Any non-test `Function` reached gets a `TESTS_FUNCTION` edge with a `depth` property (1 = direct call, 2 = via helper).
4. Direct coverage (depth 1) takes priority — if a test calls a function both directly and indirectly, only the depth-1 edge is created.
5. `tested_by_count` is computed for each production function and written to the graph via a batched `UNWIND` update.

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
  ├── add_node(label, properties)           → buffers into dict[label, list[dict]]
  ├── add_relationship(rel_type, ...)       → buffers CREATE rels by type
  ├── add_merge_relationship(rel_type, ...) → buffers MERGE rels by type
  └── flush() → dict[str, int]             → flushes nodes first, then rels, chunked by batch_size
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

`query/engine.py` provides 20+ pre-built Cypher query methods:

| Method | Description |
|--------|-------------|
| `get_function_context(name)` | Function + callers + callees + class + source code |
| `get_class_structure(name)` | Class + methods + full inheritance chain |
| `get_file_overview(path)` | All entities in file + routes + test coverage |
| `get_callers(name, depth)` | Transitive callers up to N hops |
| `get_callees(name, depth)` | Transitive callees up to N hops |
| `impact_analysis(name)` | Blast radius: callers, affected files, test coverage, routes |
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
| `find_path(from, to, hops)` | Call paths between two entities |

---

## MCP Tools

The MCP server (`mcp/server.py`) exposes these tools to AI agents:

### `gristle_ingest(repo_path, repo_id?)`
Index a local repository. Parses all files, builds the graph using batched writes.
Returns: files_processed, nodes_created, relationships_created, test_cases_found, duration_ms, etc.

### `gristle_ingest_github(repo_url, github_token?, repo_id?)`
Clone and index a GitHub repository. Supports private repos via personal access token. Clones to `GRISTLE_REPO_STORAGE_PATH`, then runs full ingestion. Returns clone and ingestion timing.

### `gristle_drop(repo_id)`
Remove a repo's graph from FalkorDB entirely. Frees memory and storage for repos no longer needed.

### `gristle_watch(action, repo_id?)`
Control the file watcher for incremental re-indexing. Actions: `start`, `stop`, `status`.

### `gristle_explore(entity, repo_id?)`
Explore a code entity (function, class, or file). Auto-detects type. Falls back to search.

### `gristle_impact(entity_name, repo_id?)`
Analyze blast radius of changing a function or class. Returns direct callers, transitive callers, affected files, test coverage, routes.

### `gristle_trace(from_entity, to_entity, max_hops?, repo_id?)`
Find call paths between two functions. Useful for understanding data flow and execution paths.

### `gristle_search(query, search_type?, limit?, repo_id?)`
Search for functions, classes, or files by name or docstring. Types: `name`, `docstring`, `all`.

### `gristle_docs(entity?, mode?, repo_id?)`
Query documentation. Modes: `find` (docs for entity), `staleness` (stale docs), `overview` (stats).

### `gristle_routes(method?, repo_id?)`
List all HTTP routes/API endpoints. Optional method filter.

### `gristle_components(limit?, repo_id?)`
List React/UI components with usage counts.

### `gristle_deps(name?, limit?, repo_id?)`
Query dependencies. Without name: list all ranked by usage. With name: show all users.

### `gristle_tests(entity?, mode?, repo_id?)`
Test queries. Modes: `find` (tests for entity), `coverage` (untested exported functions), `coverage_detail` (function-level coverage with depth info), `untested_critical` (exported functions with callers but no tests).

### `gristle_conventions(repo_id?)`
Infer project patterns: file structure, routes, components, test locations, entry points, most-imported files, visibility distribution, architectural layer violations. **Use this first on unfamiliar codebases.**

### `gristle_config(mode?, repo_id?)`
Config and environment variable queries. Modes: `env_vars` (all env vars with definitions and usage), `config_files` (config files with types), `setup_requirements` (full setup checklist).

### `gristle_embed(repo_id?)`
Build semantic search index. Requires `pip install gristle[search]` (sentence-transformers).

### `gristle_semantic_search(query, limit?, repo_id?)`
Natural language search over code. Requires `gristle_embed` to have been run first.

### MCP Resources

- `gristle://repos` — List of all ingested repositories
- `gristle://repos/{repo_id}/overview` — Statistics for a specific repo

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

## Running Gristle

### Prerequisites

```bash
# Start FalkorDB (Redis-compatible graph database)
docker run -d -p 6390:6379 falkordb/falkordb
```

### Installation

```bash
cd gristle
pip install -e ".[dev]"
# Optional: pip install -e ".[search]" for semantic search
```

### As MCP Server (for AI agents)

Add to your MCP client configuration (e.g., Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "gristle": {
      "command": "gristle"
    }
  }
}
```

Then the agent can call:
1. `gristle_ingest(repo_path="/path/to/repo")` to index
2. `gristle_conventions()` to understand project structure
3. `gristle_explore("function_name")` to inspect entities
4. `gristle_impact("function_name")` before making changes

### Remote (Streamable HTTP)

For remote access, run with Streamable HTTP transport:

```bash
GRISTLE_TRANSPORT=streamable-http GRISTLE_HTTP_PORT=8080 gristle
```

MCP clients connect via URL:

```json
{
  "mcpServers": {
    "gristle": {
      "type": "streamable-http",
      "url": "https://your-host/mcp",
      "headers": {
        "Authorization": "Bearer YOUR_API_KEY"
      }
    }
  }
}
```

Set `GRISTLE_API_KEY` to enable bearer token auth. When unset, no authentication is required (suitable for private networks).

### Docker

Multi-stage Dockerfile included. Build and run:

```bash
docker build -t gristle .
docker run -p 8080:8080 \
  -e GRISTLE_FALKORDB_HOST=host.docker.internal \
  gristle
```

Health check endpoint at `GET /health` (no auth required).

### Railway

Gristle includes `railway.toml` for one-click Railway deployment. Deploy as a service in the same project as your FalkorDB instance:

| Env Variable | Value |
|-------------|-------|
| `GRISTLE_FALKORDB_HOST` | `falkordb.railway.internal` |
| `GRISTLE_FALKORDB_PORT` | `6390` |
| `GRISTLE_API_KEY` | *(your token)* |

Railway injects `PORT` automatically. The Dockerfile sets `GRISTLE_TRANSPORT=streamable-http` by default.

### Running Tests

```bash
pytest tests/
```

Tests use mock graph clients and do not require a running FalkorDB instance.

---

## Validated Against

Gristle has been tested against these real-world repositories:

| Repository | Language | Files | Nodes | Relationships | Test Cases | Fixtures |
|-----------|----------|-------|-------|--------------|------------|----------|
| marshmallow | Python | 38 | 2,151 | 5,758 | 656 | 19 |
| httpx | Python | 60 | — | — | 541 | 7 |
| Flask | Python | 83 | — | — | 399 | 24 |
| Django REST Framework | Python | 158 | — | — | 1,038 | 0 |
| pig-knuckle | TypeScript | 365 | 28,791 | 49,814 | 0 | 0 |

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
