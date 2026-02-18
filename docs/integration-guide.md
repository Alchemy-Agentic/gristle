# Gristle Integration Guide

For AI agents and applications that consume Gristle via MCP. This is the single reference for graph schema, tool usage, configuration, and deployment.

For internal architecture (parsers, pipeline, call resolution), see [ARCHITECTURE.md](../ARCHITECTURE.md).

---

## Setup

Before any other tool works, you must ingest a repository:

```
gristle_ingest(repo_path="/absolute/path/to/repo")
```

This returns a `repo_id` (a short hash). All other tools accept an optional `repo_id` parameter — if omitted, they default to the most recently ingested repo.

Ingestion parses all source files, builds a graph of functions, classes, imports, and their relationships, then processes documentation. It only needs to be run once per repo. Re-running it rebuilds the graph from scratch.

For GitHub repos (including private ones), use `gristle_ingest_github` instead:

```
gristle_ingest_github(repo_url="https://github.com/owner/repo", github_token="ghp_...")
```

This clones the repo and runs full ingestion in one step.

---

## Graph Schema

### Node Types

| Label | Key Properties | Purpose |
|-------|---------------|---------|
| `File` | `id`, `path`, `language`, `line_count`, `is_test_file`, `todo_count`, `config_type` | Source or config file |
| `Function` | `id`, `name`, `qualified_name`, `file_path`, `start_line`, `signature`, `docstring`, `is_async`, `is_test`, `is_exported`, `is_component`, `is_entry_point`, `entry_point_reason`, `is_fixture`, `complexity`, `decorators`, `visibility`, `return_type`, `tested_by_count`, `is_callback` | Function or method |
| `Class` | `id`, `name`, `qualified_name`, `file_path`, `start_line`, `signature`, `docstring`, `bases`, `is_abstract`, `is_exported`, `kind` | Class, interface, type, or enum |
| `Import` | `id`, `file_path`, `line`, `module_path`, `imported_names`, `is_relative`, `resolved` | Import statement |
| `Route` | `id`, `method`, `path`, `handler_name`, `file_path`, `line`, `middleware`, `has_auth` | HTTP endpoint |
| `TestCase` | `id`, `name`, `block_type`, `file_path`, `start_line`, `parent_describe`, `parametrize_count` | Test block |
| `Document` | `id`, `path`, `title`, `doc_type`, `line_count`, `reference_count` | Markdown file |
| `DocumentSection` | `id`, `file_path`, `heading`, `level`, `start_line`, `end_line` | Doc section |
| `Dependency` | `id`, `name`, `version`, `latest_version`, `is_outdated`, `vulnerability_count`, `vulnerabilities`, `checked_at` | External package |
| `EnvVar` | `id`, `name`, `default_value`, `required` | Environment variable |
| `Model` | `id`, `name`, `qualified_name`, `file_path`, `line_start`, `line_end`, `orm`, `table_name`, `is_junction`, `is_enum`, `primary_key`, `field_count`, `docstring` | Database model/table definition (Prisma, Drizzle, ORM class) |
| `ModelField` | `id`, `name`, `field_type`, `db_type`, `is_primary_key`, `is_nullable`, `is_unique`, `is_indexed`, `has_default`, `default_value`, `is_foreign_key`, `references_model`, `references_field`, `line` | Column/field in a database model |

### Edge Types

| Type | From | To | Description |
|------|------|----|-------------|
| `CONTAINS` | File, Class | Function, Class, Import, Route, TestCase | Container relationship |
| `DEFINED_IN` | Function, Class | File | Reverse of CONTAINS |
| `EXPORTS` | File | Function, Class | Module exports |
| `CALLS` | Function | Function | Function call (with `depth` property) |
| `PASSED_TO` | Function | Function | Function reference passed as argument (with `context` property: middleware, route_handler, callback, array_method, argument, jsx_callback) |
| `USES_HOOK` | Function | Function | React hook usage (subset of CALLS) |
| `INHERITS_FROM` | Class | Class | Class inheritance |
| `IMPORTS` | File | File | File-level import dependency |
| `TESTS` | File | File | Test file covers production file |
| `TESTS_FUNCTION` | Function | Function | Test function exercises production function (with `depth` property: 1=direct, 2=via helper, 3=import-based JS/TS fallback) |
| `USES_FIXTURE` | Function | Function | Test uses pytest fixture (by parameter name) |
| `USES_DEPENDENCY` | Function | Dependency | Uses external package |
| `DEPENDS_ON` | File | Dependency | File-level external dependency |
| `REFERENCES` | DocumentSection | Function, Class, File | Doc references code |
| `HAS_SECTION` | Document | DocumentSection | Doc contains section |
| `HANDLES` | Route | Function | Route handler |
| `DEFINED_IN` | EnvVar | File | Env var defined in config file |
| `USES_ENV` | File | EnvVar | Source file references env var |
| `HAS_MODEL_FIELD` | Model | ModelField | Model's column/field |
| `REFERENCES` | ModelField | Model | FK relationship (when `is_foreign_key: true`) |
| `RELATED_TO` | Model | Model | High-level relationship (with `relation_type`, `foreign_key_field`, `through_model`, `orm_hint` properties) |
| `PROMOTED_FROM` | Model | Class | Link to source Class node (ORM class promoter) |

### Indexes

33 property indexes on node `id`, `name`, `qualified_name`, `file_path`, `path`, `module_path`, `method`, `doc_type`, `orm`. Two full-text indexes on `Function.docstring` and `Class.docstring`.

---

## MCP Tools Reference

### Recommended First Call: `gristle_conventions`

When you start working on an unfamiliar codebase, call this first. It returns:
- Language breakdown (Python, TypeScript, etc.)
- File structure patterns (where components live, where tests live)
- Route methods and entry points
- Most-imported files (core modules)
- Visibility distribution
- `frameworks` object with detected frameworks and their conventions
- `production_components` and `documentation_components` counts

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

### `gristle_impact_score(entity_name, include_source?)`

Enhanced impact analysis with blast radius scoring (0-100) and risk classification. Returns:
- `blast_radius_score` (0-100): Combined impact metric
- `risk_level`: low/medium/high/critical classification
- `direct_impact_score`: Based on direct callers, callbacks, routes, entry points
- `transitive_impact_score`: Based on transitive callers, affected files, test coverage

Higher scores indicate more risk. Critical (85+) changes require extra care.

---

### `gristle_data_contract(entity_name, repo_id?)`

Returns the input/output data contract for a function — what types it accepts and returns, with field details.

- `entity`: qualified name of the function
- `signature`: full function signature
- `inputs`: list of `{param_name, type, kind, fields}` — each accepted type with its fields
- `output`: `{type, kind, fields}` — the return type with its fields, or `null`

Useful for: understanding API boundaries, validating data flow between functions, architecture reviews.

---

### `gristle_type_usage(type_name, repo_id?)`

Returns all usage of a type across the codebase — where it's accepted, returned, and referenced as a field.

- `type`: name of the type/interface/class
- `kind`: interface, class, type, dataclass, etc.
- `fields`: list of fields on the type
- `accepted_by`: functions that accept this type as a parameter
- `returned_by`: functions that return this type
- `referenced_in_fields`: other types that reference this type in their fields

Useful for: understanding type dependencies, finding all consumers/producers of a data type.

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

**Mode `"coverage_detail"`** — function-level coverage with depth info.

**Mode `"untested_critical"`** — exported functions with callers but no tests.

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

### `gristle_components(limit?, include_docs?)`

For React/TypeScript projects. Lists components (PascalCase functions returning JSX) with usage counts. Not relevant for pure Python repos.

**Parameters:**
- `limit` — Maximum number of components to return
- `include_docs` (bool, default False) — Include components in documentation/mockup directories (docs/, design/, stories/, etc.)

---

### `gristle_config(mode?)`

Config and environment variable queries.

- **`mode="env_vars"`** — all env vars with definitions and usage
- **`mode="config_files"`** — config files with types (package.json, Dockerfile, CI, etc.)
- **`mode="setup_requirements"`** — full setup checklist: env vars, config files, dependencies

---

### `gristle_dead_exports()`

Find exported functions/classes that are never imported by other files. Identifies unused public API surface — useful for finding dead code in barrel files and libraries. Excludes entry points.

---

### `gristle_cycles(max_length?)`

Detect circular import dependencies. Returns cycle paths as file path lists, grouped by cycle length. Cycles are deduplicated.

---

### `gristle_public_api(include_internal?)`

List all public API entities (exported functions and classes). Returns total count, entities list, counts by type/file, and documentation percentage. Excludes test files and internal paths by default.

---

### `gristle_security(repo_id?)`

Combined security overview: code findings + unauthenticated routes + vulnerable dependencies. Detects hardcoded secrets, SQL injection risks, unsafe calls (eval, exec, pickle), LLM insecure output handling (OWASP LLM05), routes lacking authentication, and dependencies with known CVEs.

Returns `total_issues`, `code_findings` (grouped by category), `unauthenticated_routes`, and `vulnerable_dependencies`.

---

### `gristle_unauthenticated_routes(repo_id?)`

Find HTTP routes whose handlers lack authentication decorators or middleware. Checks for common auth patterns (`login_required`, `jwt`, `protect`, `verify`, etc.) and middleware presence. Useful for focused auth audits — call `gristle_security` for the full picture.

---

### `gristle_dependency_health(severity?, repo_id?)`

Check dependency staleness and known vulnerabilities. Compares declared versions against latest releases from npm/PyPI registries and reports known CVEs from OSV.dev.

**Parameters:**
- `severity`: `"all"` (all outdated, default), `"vulnerable"` (CVEs only), `"safe"` (outdated but no CVEs)

Returns `total`, `outdated` (list with name, declared_version, latest_version, vulnerability_count, vulnerabilities), `vulnerable_count`, and `summary`.

**Config:** Set `GRISTLE_DEPENDENCY_CHECK_ENABLED=false` to disable API calls (CI, air-gapped). `GRISTLE_DEPENDENCY_TIMEOUT_SECONDS` (default 5.0) and `GRISTLE_DEPENDENCY_CONCURRENCY` (default 20) control fetch behavior.

---

### `gristle_stats()`

Repository statistics — file counts, node counts, language breakdown. Quick way to verify ingestion worked.

---

### `gristle_overview()`

High-level codebase summary with key entry points, most-called functions, and relationship counts.

---

### `gristle_watch(action)`

Start incremental re-indexing so the graph stays up to date as files change:
```
gristle_watch(action="start")    # Begin watching
gristle_watch(action="status")   # Check if watching
gristle_watch(action="stop")     # Stop watching
```

---

### `gristle_ingest_github(repo_url, github_token?)`

Clone and index a GitHub repository in one step.

**When to use:** The repo is on GitHub and you don't have a local clone. Works with private repos if you provide a `github_token`.

```
gristle_ingest_github(repo_url="https://github.com/owner/repo")
```
```json
{
  "repo_id": "a1b2c3d4e5f6",
  "clone_duration_ms": 1234,
  "files_processed": 847,
  "nodes_created": 12340,
  "relationships_created": 8921,
  "duration_ms": 4231
}
```

For private repos:
```
gristle_ingest_github(repo_url="https://github.com/owner/private-repo", github_token="ghp_xxxx")
```

---

### `gristle_drop(repo_id)`

Remove a repo's graph from FalkorDB entirely.

**When to use:** You no longer need a repo's graph and want to free memory/storage.

```
gristle_drop(repo_id="a1b2c3d4e5f6")
```

This is irreversible — re-ingestion is required to restore the graph.

---

### `gristle_embed(repo_id?)` + `gristle_semantic_search(query, limit?)`

Optional. Requires `pip install gristle[search]`.

`gristle_embed` builds vector embeddings for all functions and classes. After that, `gristle_semantic_search` accepts natural language:
```
gristle_semantic_search(query="validates email addresses")
```

This is useful when you don't know the name but know what the code does.

---

### `gristle_services(repo_id?)`

**When to use:** You want to understand what external services and integrations are used by the codebase. Classifies dependencies into categories: database, auth, payments, email, AI, storage, analytics, UI, forms, and state management.

```
gristle_services()                    # All services
gristle_services(repo_id="a1b2c3d4")  # For a specific repo
```
```json
{
  "database": [
    {"name": "postgresql", "file_count": 12, "usage_count": 45}
  ],
  "auth": [
    {"name": "firebase", "file_count": 8, "usage_count": 23}
  ],
  "payments": [
    {"name": "stripe", "file_count": 5, "usage_count": 15}
  ],
  "email": [
    {"name": "sendgrid", "file_count": 3, "usage_count": 8}
  ],
  "ai": [
    {"name": "openai", "file_count": 4, "usage_count": 12}
  ],
  "storage": [
    {"name": "s3", "file_count": 6, "usage_count": 18}
  ],
  "analytics": [],
  "ui": [
    {"name": "react", "file_count": 24, "usage_count": 156}
  ],
  "forms": [],
  "state_management": [
    {"name": "redux", "file_count": 8, "usage_count": 32}
  ]
}
```

---

### `gristle_changelog(repo_id?)`

**When to use:** You want to see what changed since the last ingestion. Compares graph snapshots to show count deltas for files, functions, classes, routes, tests, components, dependencies, and edges.

```
gristle_changelog()                   # Since last ingestion
gristle_changelog(repo_id="a1b2c3d4") # For a specific repo
```
```json
{
  "timestamp": "2025-02-03T10:30:45Z",
  "compared_to": "2025-02-02T14:15:22Z",
  "deltas": {
    "files": {"added": 3, "removed": 1, "modified": 12, "total": 234},
    "functions": {"added": 15, "removed": 2, "modified": 8, "total": 1245},
    "classes": {"added": 2, "removed": 0, "modified": 3, "total": 156},
    "routes": {"added": 1, "removed": 0, "modified": 2, "total": 45},
    "tests": {"added": 8, "removed": 1, "modified": 4, "total": 289},
    "components": {"added": 5, "removed": 2, "modified": 6, "total": 98},
    "dependencies": {"added": 2, "removed": 0, "modified": 1, "total": 67},
    "edges": {"added": 45, "removed": 8, "modified": 0, "total": 4521}
  },
  "summary": "45 new calls, 8 edges removed. 3 new files with 15 functions added. 1 route modified."
}
```

### `gristle_models(repo_id?)`

**When to use:** You want to see all database models detected in the codebase. Returns models from Prisma schemas, Drizzle table definitions, and ORM class patterns.

```
gristle_models()
gristle_models(repo_id="a1b2c3d4")
```
```json
{
  "models": [
    {
      "name": "User",
      "orm": "prisma",
      "tableName": "users",
      "filePath": "prisma/schema.prisma",
      "fieldCount": 6,
      "primaryKey": "id",
      "fields": [{"name": "id", "fieldType": "string", "isPrimaryKey": true}, ...],
      "relations": [{"targetModel": "Post", "relationType": "one-to-many"}]
    }
  ],
  "count": 5
}
```

### `gristle_model_detail(model_name, repo_id?)`

**When to use:** You want full details about a specific model including all field constraints and both incoming/outgoing relationships.

```
gristle_model_detail(model_name="User")
```
```json
{
  "name": "User",
  "orm": "prisma",
  "tableName": "users",
  "filePath": "prisma/schema.prisma",
  "lineStart": 5,
  "lineEnd": 15,
  "primaryKey": "id",
  "fields": [
    {"name": "id", "fieldType": "string", "isPrimaryKey": true, "isNullable": false},
    {"name": "email", "fieldType": "string", "isUnique": true, "isNullable": false},
    {"name": "name", "fieldType": "string", "isNullable": true}
  ],
  "outgoingRelations": [{"targetModel": "Post", "relationType": "one-to-many"}],
  "incomingRelations": [{"sourceModel": "Profile", "relationType": "one-to-one"}]
}
```

---
## MCP Resources

| Resource URI | Description |
|-------------|-------------|
| `gristle://repos` | List all ingested repositories |
| `gristle://repos/{repo_id}/overview` | Statistics and overview for a specific repo |

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

## Common Workflows

### 1. "I'm new to this codebase"

```
gristle_ingest(repo_path="/path/to/repo")          # Local repo
# OR
gristle_ingest_github(repo_url="owner/repo")        # GitHub repo

gristle_conventions()                                # Understand structure
gristle_explore(entity="<main_module>.py")           # Explore core files
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

## Configuration Reference

All settings use the `GRISTLE_` env prefix. Defined in `src/gristle/config.py` with Pydantic field validators:

| Setting | Default | Description |
|---------|---------|-------------|
| `GRISTLE_FALKORDB_HOST` | `localhost` | FalkorDB host |
| `GRISTLE_FALKORDB_PORT` | `6390` | FalkorDB port (validated: 1-65535) |
| `GRISTLE_FALKORDB_PASSWORD` | *(none)* | FalkorDB password (optional) |
| `GRISTLE_MAX_FILE_SIZE_BYTES` | `512000` | Skip files larger than this (validated: >= 1) |
| `GRISTLE_REPO_STORAGE_PATH` | `./repos` | Where cloned repos are stored |
| `GRISTLE_WATCHER_DEBOUNCE_SECONDS` | `2.0` | File watcher debounce delay |
| `GRISTLE_INGESTION_BATCH_SIZE` | `200` | Nodes/edges per batched UNWIND query (validated: >= 1) |
| `GRISTLE_TRANSPORT` | `stdio` | MCP transport: `stdio` or `streamable-http` (validated) |
| `GRISTLE_HTTP_HOST` | `0.0.0.0` | Bind address for HTTP transport |
| `GRISTLE_HTTP_PORT` | `8080` | HTTP port (Railway overrides via `PORT`) (validated: 1-65535) |
| `GRISTLE_API_KEY` | *(none)* | Bearer token for auth; unset = no auth |
| `GRISTLE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GRISTLE_LOG_FORMAT` | *(auto)* | `json` for structured, `text` for human-readable; auto-detected from transport if unset |

Excluded directories (always skipped): `node_modules`, `.git`, `__pycache__`, `dist`, `build`, `.venv`, `venv`, `.tox`, `.mypy_cache`, `.pytest_cache`, `.ruff_cache`, `egg-info`, `.eggs`.

---

## Deployment

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

### Local (stdio)

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

---

## Health & Diagnostics

Gristle outputs structured JSON logs in production (HTTP transport) and coloured human-readable logs in development (stdio). All ingestion operations include timing data:

```json
{"ts": "2026-01-31T14:22:03", "level": "INFO", "logger": "gristle.ingestion.pipeline",
 "msg": "Ingestion complete: 847 files, 12 docs, 12340 nodes, 8921 relationships in 4.2s",
 "event": "ingestion_done", "duration_ms": 4231.7, "repo_id": "a1b2c3d4e5f6",
 "files": 847, "nodes": 12340, "rels": 8921}
```

The `/health` endpoint returns server status without auth:

```bash
curl https://gristle-production.up.railway.app/health
# {"status": "ok", "server": "gristle", "version": "0.1.0", "repos_loaded": 2, ...}
```

---

## What Gristle Knows

### It tracks:
- All functions and methods (signature, parameters, docstring, return type, complexity, decorators)
- All classes (bases, methods, inheritance chain)
- All imports (module path, named imports, aliases, relative imports, dynamic `import()` and `require()` calls)
- Barrel file re-exports — `export { X } from './module'` and `export * from './module'` in TS/JS `index.ts` files (and Python `__init__.py`) are followed to the original definition, including multi-level barrel chains (barrel -> barrel -> definition)
- Call relationships (which function calls which, resolved across files, through inheritance, and through barrel file re-exports)
- HTTP routes (method, path, handler) — including Express, Hono, Fastify, Next.js, FastAPI, Flask, Django, and Supabase/Deno edge functions
- Test coverage (which test files cover which production files, which tests call which functions)
- pytest fixtures and which tests use them
- External dependencies and which code uses them
- Documentation and its references to code entities
- TODO/FIXME/HACK comments
- React components and their usage (JS/TS)
- React hook usage (USES_HOOK edges for custom hooks like `useAuth`, `useMetrics`)
- Config files and environment variables

### It does NOT track:
- Runtime behavior or dynamic dispatch
- Variable types or type inference (only annotated return types)
- Database schemas or SQL queries
- Conditional imports (`if TYPE_CHECKING:` imports are tracked but not distinguished)
- Monkey-patching or dynamic attribute assignment

### Languages supported:
- **Python** (`.py`, `.pyi`) — full support including pytest patterns
- **TypeScript** (`.ts`, `.tsx`) — full support including React/Next.js
- **JavaScript** (`.js`, `.jsx`) — full support including React
- **Markdown** (`.md`, `.mdx`) — documentation parsing and code reference extraction

---

## Validated Against

Gristle has been tested against these real-world repositories:

| Repository | Language | Files | Nodes | Relationships | Test Cases | Fixtures |
|-----------|----------|-------|-------|--------------|------------|----------|
| marshmallow | Python | 38 | 2,151 | 5,758 | 656 | 19 |
| httpx | Python | 60 | -- | -- | 541 | 7 |
| Flask | Python | 83 | -- | -- | 399 | 24 |
| Django REST Framework | Python | 158 | -- | -- | 1,038 | 0 |
| pig-knuckle | TypeScript | 365 | 28,791 | 49,814 | 0 | 0 |

---

## Tips for Effective Use

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
