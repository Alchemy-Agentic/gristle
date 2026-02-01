# Gristle MCP Usage Guide

This guide is for AI agents that have Gristle configured as an MCP server. It explains when to call each tool, what to pass, what you get back, and how to chain tools together for common workflows.

---

## Setup

Before any other tool works, you must ingest a repository:

```
gristle_ingest(repo_path="/absolute/path/to/repo")
```

This returns a `repo_id` (a short hash). All other tools accept an optional `repo_id` parameter — if omitted, they default to the most recently ingested repo.

Ingestion parses all source files, builds a graph of functions, classes, imports, and their relationships, then processes documentation. It only needs to be run once per repo. Re-running it rebuilds the graph from scratch.

---

## Tool Reference

### Recommended First Call: `gristle_conventions`

When you start working on an unfamiliar codebase, call this first. It returns:
- Language breakdown (Python, TypeScript, etc.)
- File structure patterns (where components live, where tests live)
- Route methods and entry points
- Most-imported files (core modules)
- Visibility distribution

This gives you the mental model of the project before you start exploring specifics.

**Example response (abbreviated):**
```json
{
  "project_overview": {
    "nodes": {"File": 38, "Function": 928, "Class": 161, "Import": 167, "Route": 5},
    "relationships": {"CALLS": 1546, "INHERITS_FROM": 62, "IMPORTS": 58, "TESTS": 27},
    "languages": ["python"],
    "most_called_functions": [
      {"name": "src/marshmallow/schema.py::Schema.dump", "caller_count": 89},
      {"name": "src/marshmallow/schema.py::Schema.load", "caller_count": 72}
    ]
  },
  "conventions": {
    "languages": {"python": 38},
    "test_locations": {"tests": 27},
    "entry_points": [],
    "most_imported_files": [
      {"path": "src/marshmallow/schema.py", "imports": 12},
      {"path": "src/marshmallow/fields.py", "imports": 8}
    ]
  }
}
```

---

### `gristle_explore(entity)`

The general-purpose "tell me about this" tool. Pass a function name, class name, qualified name, or file path. Gristle auto-detects the type and returns the appropriate detail.

**When to use:** You know the name of something and want to understand it — its signature, where it lives, what it calls, what calls it.

**Entity name formats (from most specific to least):**
| Format | Example | Precision |
|--------|---------|-----------|
| Qualified name | `src/marshmallow/schema.py::Schema.validate` | Exact match |
| Class.method | `Schema.validate` | Usually unique |
| Short name | `validate` | May match multiple entities |
| File path | `src/marshmallow/schema.py` | Returns file overview |

**Prefer qualified names** when you already know the file path. Use short names for discovery.

**Example — exploring a function:**
```
gristle_explore(entity="Schema.validate")
```
```json
{
  "type": "function",
  "name": "validate",
  "qualified_name": "src/marshmallow/schema.py::Schema.validate",
  "signature": "def validate(self, data, *, many=None, partial=None) -> dict[str, list[str]]",
  "docstring": "Validate data against the schema...",
  "file_path": "src/marshmallow/schema.py",
  "start_line": 811,
  "end_line": 838,
  "class_name": "Schema",
  "is_async": false,
  "complexity": 3,
  "callers": [
    "tests/test_schema.py::test_multiple_errors_can_be_stored_for_a_given_index",
    "tests/test_schema.py::TestRequiredFields.test_allow_none_custom_message"
  ],
  "callees": ["src/marshmallow/schema.py::Schema._do_load"],
  "source_code": "def validate(self, data, *, many=None, partial=None):\n    ..."
}
```

**Example — exploring a class:**
```
gristle_explore(entity="Schema")
```
```json
{
  "type": "class",
  "name": "Schema",
  "qualified_name": "src/marshmallow/schema.py::Schema",
  "bases": [],
  "file_path": "src/marshmallow/schema.py",
  "methods": [
    {"name": "dump", "signature": "def dump(self, obj, *, many=None)", "visibility": "public"},
    {"name": "load", "signature": "def load(self, data, *, many=None, partial=None)", "visibility": "public"},
    {"name": "_do_load", "visibility": "protected"}
  ],
  "hierarchy": ["Schema"]
}
```

**Example — exploring a file:**
```
gristle_explore(entity="src/marshmallow/fields.py")
```
```json
{
  "type": "file",
  "path": "src/marshmallow/fields.py",
  "language": "python",
  "line_count": 1872,
  "classes": [
    {"name": "Field", "start_line": 52},
    {"name": "String", "start_line": 310},
    {"name": "Integer", "start_line": 380}
  ],
  "functions": [],
  "tested_by": ["tests/test_deserialization.py", "tests/test_serialization.py"]
}
```

**Fallback behavior:** If no exact match is found, Gristle automatically runs a search and returns matching results.

---

### `gristle_impact(entity_name)`

**When to use:** BEFORE modifying any function or class. This tells you the blast radius — everything that would break if you change this entity.

```
gristle_impact(entity_name="Schema.dump")
```
```json
{
  "target": "src/marshmallow/schema.py::Schema.dump",
  "target_type": "Function",
  "target_file": "src/marshmallow/schema.py",
  "direct_callers": [
    "tests/test_serialization.py::test_nested_field_many_serializing_generator",
    "tests/test_serialization.py::TestSchemaSerialization.test_serialize_with_missing_param_callable"
  ],
  "affected_files": ["tests/test_serialization.py", "tests/test_schema.py"],
  "transitive_callers": ["..."],
  "total_affected_files": ["tests/test_serialization.py", "tests/test_schema.py", "tests/test_registry.py"],
  "test_files": ["tests/test_serialization.py"],
  "test_functions": [
    {"test_name": "test_nested_field_many_serializing_generator", "via": "calls"}
  ]
}
```

Key fields to check:
- `direct_callers` — functions that call this directly (will break first)
- `total_affected_files` — every file that transitively depends on this
- `test_files` / `test_functions` — what tests cover this (run these after your change)

---

### `gristle_search(query, search_type?, limit?)`

**When to use:** You don't know the exact name. You're looking for something related to a concept.

**Search types:**
- `"name"` — match against function/class/file names (fast, precise)
- `"docstring"` — match against docstrings (finds by description)
- `"all"` — match both name and docstring (default)

```
gristle_search(query="serialize", search_type="name", limit=5)
```
```json
{
  "query": "serialize",
  "count": 5,
  "results": [
    {"type": "Function", "name": "serialize", "qualified_name": "src/marshmallow/schema.py::Schema.serialize", "file_path": "src/marshmallow/schema.py"},
    {"type": "Function", "name": "_serialize", "qualified_name": "src/marshmallow/fields.py::Field._serialize"},
    {"type": "Class", "name": "TestSchemaSerialization", "qualified_name": "tests/test_serialization.py::TestSchemaSerialization"}
  ]
}
```

**Tips:**
- Search is case-sensitive for names. Use `"all"` type if unsure.
- Search uses `CONTAINS` matching, not prefix. `"serial"` matches `"serialize"` and `"_serialize"`.
- After finding what you need, use `gristle_explore` on the qualified name for full detail.

---

### `gristle_trace(from_entity, to_entity, max_hops?)`

**When to use:** You want to understand how two pieces of code are connected. "How does data flow from the HTTP handler to the database?"

```
gristle_trace(from_entity="create_order", to_entity="validate", max_hops=5)
```
```json
{
  "from": "create_order",
  "to": "validate",
  "paths": [
    {"path": ["OrderService.create_order", "Schema.load", "Schema._do_load", "Schema.validate"], "hops": 3}
  ]
}
```

If no path is found, the entities may not be connected through call relationships within the hop limit. Try increasing `max_hops` or check if the connection is through imports/inheritance rather than calls.

---

### `gristle_tests(entity?, mode?)`

**Mode `"find"`** — find tests that exercise a specific entity:
```
gristle_tests(entity="Schema.dump", mode="find")
```
Returns test functions that call this entity (directly or transitively up to 3 hops), plus test files that cover the entity's file.

**Mode `"coverage"`** — find untested code:
```
gristle_tests(mode="coverage")
```
Returns exported, non-test functions with no test callers, ordered by complexity (most complex untested functions first).

---

### `gristle_routes(method?)`

**When to use:** Understanding the API surface. Finding which handler to modify for a specific endpoint.

```
gristle_routes()                    # All routes
gristle_routes(method="POST")       # Only POST endpoints
```
```json
{
  "count": 5,
  "routes": [
    {"method": "ALL", "path": "/new_quote", "handler": "new_quote", "file_path": "examples/flask_example.py", "line": 28}
  ]
}
```

**Supported route patterns:**
- **Python:** FastAPI/Flask/Django decorators (`@app.get("/path")`, `@router.post()`)
- **TypeScript/JavaScript:** Express/Hono/Fastify method calls (`app.get('/path', handler)`)
- **Next.js:** App router file conventions (`app/api/users/route.ts` → `GET /api/users`)
- **Supabase/Deno:** Edge functions (`supabase/functions/<name>/index.ts` → `POST /<name>`)

---

### `gristle_deps(name?, limit?)`

**Without `name`** — list all external dependencies ranked by usage:
```json
{
  "dependencies": [
    {"name": "typing", "file_count": 15, "function_count": 42},
    {"name": "datetime", "file_count": 8, "function_count": 23}
  ]
}
```

**With `name`** — drill into a specific package:
```
gristle_deps(name="datetime")
```
```json
{
  "dependency": "datetime",
  "files": ["src/marshmallow/fields.py", "src/marshmallow/utils.py"],
  "functions": [
    {"name": "_serialize", "qualified_name": "src/marshmallow/fields.py::DateTime._serialize", "is_test": false}
  ],
  "file_count": 2,
  "function_count": 5
}
```

---

### `gristle_docs(entity?, mode?)`

**Mode `"find"`** — find docs that reference a code entity:
```
gristle_docs(entity="Schema", mode="find")
```

**Mode `"staleness"`** — find docs with potentially stale code references.

**Mode `"overview"`** — doc statistics (counts by type, most-referenced entities).

---

### `gristle_components(limit?)`

For React/TypeScript projects. Lists components (PascalCase functions returning JSX) with usage counts. Not relevant for pure Python repos.

---

### `gristle_watch(action, repo_id?)`

Start incremental re-indexing so the graph stays up to date as files change:
```
gristle_watch(action="start")    # Begin watching
gristle_watch(action="status")   # Check if watching
gristle_watch(action="stop")     # Stop watching
```

---

### `gristle_embed(repo_id?)` + `gristle_semantic_search(query, limit?)`

Optional. Requires `pip install gristle[search]`.

`gristle_embed` builds vector embeddings for all functions and classes. After that, `gristle_semantic_search` accepts natural language:
```
gristle_semantic_search(query="validates email addresses")
```

This is useful when you don't know the name but know what the code does.

---

## Common Workflows

### 1. "I'm new to this codebase"

```
gristle_ingest(repo_path="/path/to/repo")
gristle_conventions()                        # Understand structure
gristle_explore(entity="<main_module>.py")   # Explore core files
```

### 2. "I need to modify function X"

```
gristle_explore(entity="X")                  # Understand what X does
gristle_impact(entity_name="X")              # See what breaks if you change X
gristle_tests(entity="X", mode="find")       # Find tests to run after
```

### 3. "Where is feature Y implemented?"

```
gristle_search(query="Y")                    # Find by name
gristle_search(query="Y", search_type="docstring")  # Find by description
gristle_explore(entity="<result>")           # Drill into the match
```

### 4. "How does data flow from A to B?"

```
gristle_trace(from_entity="A", to_entity="B")
```

### 5. "What's the API surface?"

```
gristle_routes()                             # All endpoints
gristle_explore(entity="<handler_name>")     # Explore a specific handler
```

### 6. "What needs tests?"

```
gristle_tests(mode="coverage")               # Untested exported functions
```

### 7. "What depends on external package X?"

```
gristle_deps(name="redis")                   # All code using redis
```

### 8. "What Supabase edge functions exist?"

```
gristle_routes(method="POST")               # All edge functions show as POST routes
gristle_explore(entity="<handler_name>")     # Explore a specific handler
gristle_impact(entity_name="<handler>")      # See what depends on it
```

Supabase edge functions at `supabase/functions/<name>/index.ts` are automatically detected as `POST /<name>` routes when they contain `serve()` or `Deno.serve()` calls.

---

## Entity Name Resolution

Gristle tools accept entity names in several formats. Here is how resolution works, from most to least specific:

| You pass | Gristle matches | Notes |
|----------|----------------|-------|
| `src/marshmallow/schema.py::Schema.validate` | Exact qualified name | Always unambiguous |
| `Schema.validate` | Name search | Usually unique, may match multiple classes |
| `validate` | Short name search | May match many functions named "validate" |
| `src/marshmallow/schema.py` | File path | Returns file overview in `gristle_explore` |

**Best practice:** Start with short names for discovery. Once you find the right entity, use the qualified name from the response for follow-up queries.

**Qualified name format:**
- Functions: `file_path::function_name`
- Methods: `file_path::ClassName.method_name`
- Classes: `file_path::ClassName`
- Files: just the relative path (forward slashes)

---

## What Gristle Knows

### It tracks:
- All functions and methods (signature, parameters, docstring, return type, complexity, decorators)
- All classes (bases, methods, inheritance chain)
- All imports (module path, named imports, aliases, relative imports, dynamic `import()` and `require()` calls)
- Barrel file re-exports — `export { X } from './module'` and `export * from './module'` in TS/JS `index.ts` files (and Python `__init__.py`) are followed to the original definition, including multi-level barrel chains (barrel → barrel → definition)
- Call relationships (which function calls which, resolved across files, through inheritance, and through barrel file re-exports)
- HTTP routes (method, path, handler) — including Express, Hono, Fastify, Next.js, FastAPI, Flask, Django, and Supabase/Deno edge functions
- Test coverage (which test files cover which production files, which tests call which functions)
- pytest fixtures and which tests use them
- External dependencies and which code uses them
- Documentation and its references to code entities
- TODO/FIXME/HACK comments
- React components and their usage (JS/TS)
- React hook usage (USES_HOOK edges for custom hooks like `useAuth`, `useMetrics`)

### It does NOT track:
- Runtime behavior or dynamic dispatch
- Variable types or type inference (only annotated return types)
- Configuration files (YAML, TOML, JSON) beyond pyproject.toml for source root detection
- Database schemas or SQL queries
- Environment variables
- Conditional imports (`if TYPE_CHECKING:` imports are tracked but not distinguished)
- Monkey-patching or dynamic attribute assignment

### Languages supported:
- **Python** (`.py`, `.pyi`) — full support including pytest patterns
- **TypeScript** (`.ts`, `.tsx`) — full support including React/Next.js
- **JavaScript** (`.js`, `.jsx`) — full support including React
- **Markdown** (`.md`, `.mdx`) — documentation parsing and code reference extraction

---

## Tips

1. **Call `gristle_conventions` first** on any new repo. It gives you the lay of the land in one call.

2. **Use `gristle_impact` before modifying code.** It tells you the blast radius and which tests to run.

3. **Chain explore + impact.** First understand what something does (`explore`), then understand what depends on it (`impact`).

4. **Qualified names are in the response.** Every `gristle_explore` and `gristle_search` result includes `qualified_name`. Use that for precise follow-up queries.

5. **Search falls back gracefully.** `gristle_explore` automatically falls back to search if the exact name isn't found.

6. **File paths use forward slashes** regardless of OS. Always use `src/marshmallow/schema.py`, not backslashes.

7. **The graph persists.** Once ingested, the graph survives server restarts (FalkorDB stores it). But the MCP server's in-memory engine map is reset on restart — you'll need to call `gristle_ingest` again to re-register the repo (this is fast if the graph already exists, though it rebuilds from scratch).

8. **Multiple repos are supported.** Each gets its own graph. Pass `repo_id` to target a specific one, or omit it to use the most recent.

9. **Barrel file imports resolve correctly.** If a project uses `index.ts` barrel files (e.g., `export { Button } from './Button'`), Gristle follows the re-export chain — even through multiple levels of barrel files. `import { Button } from './components'` correctly resolves to the actual `Button.tsx` definition, not just the barrel file.

10. **Supabase edge functions are routes.** Each `supabase/functions/<name>/index.ts` file with a `serve()` call appears as a `POST /<name>` route in `gristle_routes()`.
