# Changelog

All notable changes to Gristle are documented here. This file is intended for consuming applications to track what's new, what changed, and what might break.

---

## [Unreleased]

### Added
- **`gristle` CLI** — `ingest`, `overview`, `explore`, `query`, `doctor`, and
  `serve` subcommands (bare `gristle` still starts the MCP server).
- **Engine rehydration** — tools work against previously-ingested repos after a
  server restart (rebuilt from the FalkorDB graph) instead of re-ingesting.
- **`/ready` endpoint** — pings FalkorDB; `/health` is now liveness-only.
- **Parsers** — TS/JS decorator extraction, NestJS controller routes, tsconfig
  `paths`/`baseUrl` import resolution, and SQLAlchemy/Django/TypeORM model
  detection (Model/ModelField/relation nodes).
- **Code → data edges** — new `USES_MODEL` edge (Function → Model, with a
  read/write `access` property) links code that queries a model to it. Covers
  method-chain access (Django/SQLAlchemy/Prisma, e.g. `User.objects.filter()`)
  **and** model/table passed as a call argument — Drizzle `db.insert(chat)` /
  `db.select().from(chat)` and SQLAlchemy `session.query(User)` / `select(User)`.
  Precise by design: an edge requires a read/write verb in the call's method name
  (never from an argument), so incidental name reuse doesn't create false edges.
  Also covers the TypeORM/NestJS repository pattern: a field typed
  `Repository<ArticleEntity>` (constructor param-property) maps
  `this.articleRepository.findOne()` to the `ArticleEntity` model via the field's
  type. On a real NestJS+TypeORM app: USES_MODEL 1 → 32, enabling route → controller
  → service → entity tracing.
- **Inline route handlers** — Express/Hono routes whose handler is an inline
  arrow/function (`app.get('/x', (c) => …)`) now synthesize an entry-point
  Function node, so the route gets a `HANDLES` edge and route→handler→callee/
  model tracing works (previously the callback was anonymous and unlinked).
  On a real Hono repo: routes with a handler went from ~0.5% to 93.8%.
- **Django support** — two fixes that take Gristle from nearly-blind to fully
  connected on Django apps:
  - *URLconf routes*: `urls.py` `path()`/`re_path()`/`url()` and DRF
    `router.register()` now become `Route` nodes (method `ALL`; `include()` and
    `admin.site.urls` mounts are skipped). For class-based views the `HANDLES`
    edge targets the view `Class`.
  - *Transitive model bases*: ORM models are detected even when they subclass a
    custom base (`class Article(TimestampedModel)`) defined in another file;
    abstract Django bases (`class Meta: abstract = True`) are excluded.
    On a real Django REST app: routes 0→11, models 1→5, USES_MODEL 0→26, with
    route→view→model tracing end-to-end.
- **`Variable` node type** — module-level `const`/`let`/`var` (TS/JS) and module
  assignments (Python) that aren't functions or classes — config objects,
  validation schemas (Zod), handler/route registries, React contexts, constants —
  are now nodes (`kind`, `value_kind`, `is_exported`) with `CONTAINS`/`EXPORTS`
  edges, instead of being dropped. They register as resolvable entities so imports
  can point at them. (Additive: existing queries are unaffected.)
- **Packaging** — tag-triggered PyPI + GHCR release workflow, single-source
  version (hatch dynamic), and `examples/sample-app`.

### Changed
- **CALLS edges carry a `resolution` confidence property** — `exact`,
  `file_scoped`, `import`, `typed_receiver`, `dotted`, `same_file`, or
  `unique_global` — so consumers can weight/filter call edges by how reliably the
  callee was resolved (e.g. ai-chatbot is ~94% exact/import; heuristic-heavy repos
  are dominated by `dotted`). Multi-site edges keep their highest-confidence label.
- Dotted calls `obj.method()` resolve via the receiver's type annotation —
  whether `obj` is a parameter (`def h(svc: UserService): svc.create()`) or a
  field of the calling method's class (`this.userService.create()`, where
  `userService` is constructor-injected). This precise, annotation-driven step
  runs *before* the weak bare-method-name fallback, fixing a mis-resolution where
  `this.articleService.findAll()` bound to a same-named method on the caller's own
  class (a false self-edge) instead of the service. On a real NestJS app this
  connects controller → service correctly, enabling route → controller → service
  → entity tracing end-to-end.
- Relationship writes are label-scoped so FalkorDB uses the id index instead of
  a full-scan Cartesian product (~2× faster ingest on large repos).
- GitHub ingests keep the clone under `GRISTLE_REPO_STORAGE_PATH` so source
  loading works (removed by `gristle_drop`).

### Fixed
- Ingest no longer aborts on a null `RELATED_TO` relation property; enrichment
  phases (config/schema/docs) are isolated so one failure degrades gracefully.
- MCP tools return a structured `{"error": ...}` (with an actionable message
  when FalkorDB is unreachable) instead of leaking raw exceptions.
- Incremental watch path passed the wrong type to call resolution
  (`AttributeError`); now uses a `BatchCollector`.
- `MERGE` relationship writes mis-bound per-row properties (a property map in a
  MERGE pattern is match criteria in FalkorDB); now applied with `SET`. Affected
  `RELATED_TO` and any merged edge with properties.
- Drizzle foreign keys/relations on multi-line column chains were dropped
  (line-based parsing detached `.references()`); now split on top-level commas
  (e.g. ai-chatbot: 0 → 6 model relations).
- `gristle_impact` double-counted transitive callers (one row per path); now
  one row per node (min depth).
- `detect_unauthenticated_routes` flagged routes that authenticate via an inline
  `auth()` call (e.g. Next.js); now honors a `calls_auth` signal + auth callees.
- Python `is_exported` only fired on `__all__`, leaving `gristle_public_api` and
  coverage empty for most repos; now public module-level names are exported.

---

## [0.1.0] - 2026-02-03

Initial release.

### Supported Languages

- **Python** (`.py`, `.pyi`) — full support including pytest patterns, FastAPI/Flask/Django routes
- **TypeScript** (`.ts`, `.tsx`) — full support including React/Next.js, Express/Hono/Fastify routes
- **JavaScript** (`.js`, `.jsx`) — full support including React, Express/Hono/Fastify routes
- **Markdown** (`.md`, `.mdx`) — documentation parsing and code reference extraction
- **ORM schemas** — Prisma (`.prisma`) and Drizzle (`.ts`/`.js`) model/field extraction

### Graph Schema

- **14 node types:** File, Function, Class, Import, Route, TestCase, Document, DocumentSection, Dependency, EnvVar, TypeField, Model, ModelField, Snapshot
- **24 edge types:** CONTAINS, DEFINED_IN, EXPORTS, CALLS, PASSED_TO, USES_HOOK, INHERITS_FROM, IMPORTS, TESTS, TESTS_FUNCTION, USES_FIXTURE, USES_DEPENDENCY, DEPENDS_ON, USES_ENV, REFERENCES, HAS_SECTION, HANDLES, HAS_FIELD, RETURNS, ACCEPTS, HAS_MODEL_FIELD, RELATED_TO, PROMOTED_FROM, USES_MODEL
- **33 property indexes + 2 full-text indexes** (Function.docstring, Class.docstring)

### MCP Tools (30)

| Category | Tools |
|----------|-------|
| Ingestion | `gristle_ingest`, `gristle_ingest_github` |
| Exploration | `gristle_explore`, `gristle_search`, `gristle_conventions`, `gristle_semantic_search`, `gristle_embed` |
| Analysis | `gristle_impact`, `gristle_impact_score`, `gristle_trace`, `gristle_tests`, `gristle_type_usage`, `gristle_data_contract` |
| API surface | `gristle_routes`, `gristle_components`, `gristle_deps`, `gristle_docs`, `gristle_public_api`, `gristle_models`, `gristle_model_detail` |
| Code quality | `gristle_dead_exports`, `gristle_cycles` |
| Security | `gristle_security`, `gristle_unauthenticated_routes` |
| Dependencies & services | `gristle_dependency_health`, `gristle_services` |
| Config & history | `gristle_config`, `gristle_changelog` |
| Lifecycle | `gristle_drop`, `gristle_watch` |

### MCP Resources (2)

- `gristle://repos` — list all ingested repositories
- `gristle://repos/{repo_id}/overview` — statistics for a specific repo

### Deployment Options

- **Local** — stdio transport for local MCP clients
- **Remote** — Streamable HTTP transport with optional bearer token auth
- **Docker** — multi-stage Dockerfile with health check
- **Railway** — `railway.toml` included, one-click deploy

### Key Features

- Three-phase ingestion pipeline with batched `UNWIND` writes
- 6-step call resolution with inheritance-aware MRO walking
- Barrel file (re-export) resolution for Python `__init__.py` and TS/JS `index.ts`
- Framework-aware entry point detection (`is_entry_point` + `entry_point_reason`)
- Function-level test coverage (`TESTS_FUNCTION` edges, `tested_by_count`)
- Callback/handler detection (`PASSED_TO` edges with context)
- Config file parsing and env var extraction (`EnvVar` nodes, `USES_ENV` edges)
- Dead export detection, import cycle detection, public API surface mapping
- Type/data-contract flow (`RETURNS`/`ACCEPTS`/`HAS_FIELD` edges, `TypeField` nodes)
- Security pattern detection and unauthenticated-route flagging
- External service mapping and dependency staleness/CVE checks (npm/PyPI/OSV)
- ORM schema extraction (Prisma, Drizzle) into `Model`/`ModelField` nodes
- Graph snapshots with cross-run changelog diffing
- Per-repo graph isolation (`gristle_{repo_id}`)
- Optional semantic search via sentence-transformers
- Incremental file watching
