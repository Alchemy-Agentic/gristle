# Changelog

All notable changes to Gristle are documented here. This file is intended for consuming applications to track what's new, what changed, and what might break.

---

## [Unreleased]

## [0.5.0] - 2026-07-16

### Added
- **Supabase data-layer extraction.** Repos whose data layer is the Supabase
  client (no ORM) previously produced zero `Model` nodes, so the route→handler→DB
  story was empty exactly where Supabase apps live. Two new sources fix that:
  - The generated types file (`supabase gen types typescript` output) is parsed
    into `Model`/`ModelField` nodes — every table *and view* with columns,
    nullability, and FK relationships (`REFERENCES` + `RELATED_TO` edges,
    `orm: "supabase"`, views marked `docstring: "Supabase view"`). Duplicate
    copies of the generated file are deduplicated: one Model per table,
    preferring the most complete copy.
  - `supabase.from('table').select()/insert()/update()/upsert()/delete()` chains
    now create `USES_MODEL` edges with read/write access. Precision guards:
    string-literal tables only, matched *only* via the chain descriptor (a
    lowercase table name like `users` never links from an ordinary variable such
    as `users.filter(...)`), and the storage API (`.storage.from('bucket')`,
    including the `const storage = supabase.storage` idiom) is excluded.
  Scope: TS/JS clients (`@supabase/supabase-js`); `.rpc('fn')` calls and the
  Python client are not yet linked.

### Changed
- **`gristle_models` is now capped for agent consumption.** Supabase generated
  types put ~200 tables in one repo, and the uncapped list view measured 134k
  tokens on a real app. The list now returns at most 50 models
  (`models_omitted` records the cut; `count` stays exact) with at most 10
  inline `fields`/`relations` per model (`fields_omitted`/`relations_omitted`
  siblings; `fieldCount` still carries the full count), and drops null/false
  field attributes plus `""`-coerced relation props from the list view
  (~11k tokens on the same repo). `gristle_model_detail` keeps the complete,
  uncompacted shape for a single model.

### Fixed
- **Phantom relation on models without relationships.** `gristle_models` and
  `gristle_model_detail` reported one all-null relation entry (`{targetModel:
  null, ...}`) for every model with no `RELATED_TO` edges — 92 of 188 models on
  a real repo. Same FalkorDB behavior as the phantom-caller fix in 0.3.0: a map
  literal inside `collect()` survives an `OPTIONAL MATCH` that matched nothing.
  All-null entries are now filtered from `fields`, `relations`, and
  incoming/outgoing relations.

## [0.4.0] - 2026-07-16

### Added
- **Worktree-aware repo identity.** A git worktree is a checkout of a repository,
  not a separate repository — but identity was a hash of the ingest path, so an
  agent running `gristle_ingest` from each worktree of one repo created one full,
  near-identical graph per worktree (a repo with ~90 worktrees ≈ millions of
  duplicate nodes, unidentifiable afterwards). Worktrees now take their identity
  from the main working tree and share its graph; re-ingesting from any worktree
  refreshes it. Submodules and orphaned (pruned) worktree dirs keep their own
  identity. Passing an explicit `repo_id` still isolates deliberately.
- **`gristle_repos` tool + `gristle repos` / `gristle drop` CLI commands** — the
  graph lifecycle story. Lists every Gristle graph on the server with its source
  `repo_path`, `last_ingested_at`, and node count (read from each graph's own
  ingest snapshot), so stale or orphaned graphs are identifiable and removable
  instead of opaque `gristle_<hash>` names. The `gristle://repos` resource now
  carries the same metadata. (34 MCP tools total.)

### Changed
- **Tool outputs are now capped for agent consumption.** Dogfooding against a
  2,335-file monorepo measured single calls at 28k–68k tokens (`infer_conventions`
  listed all ~1,200 entry points; a hub function's impact returned 588 callers
  three ways) — enough to flood a calling agent's context. Unbounded list fields
  are now capped (callers/files/tests at 25; changeset unions at 50; entry points
  at 20; repo file list at 100; dead exports and env-var listings at 50) with a
  `<field>_omitted: N` sibling whenever items were cut. **All counts and scores are
  computed from the full data** — capping happens only at the projection, so
  `*_count` fields and changeset unions remain exact. `recommendation` now leads
  the change-impact payloads. Measured effect: the audit's worst offenders shrank
  92% (55.5k → 2.8k tokens for hub-function impact) with no information an agent
  acts on lost.
- **Impact payload counts now reconcile with their lists.** Every `*_count` counts
  the same-named list field (`len(field) + field_omitted == field_count`), and
  `gristle_impact` gained the count fields outright. Previously
  `affected_files_count` counted the *transitive* file union while the
  `affected_files` list held only direct-caller files; `gristle_change_impact`'s
  `affected_files` is now that transitive union (the full blast surface — what its
  recommendation always counted), and `gristle_impact_score` reports the union
  separately as `total_affected_files` / `total_affected_files_count`.
- **`gristle ingest` (CLI) now canonicalizes the path before hashing.** Previously
  the raw string was hashed, so `gristle ingest .` produced the *same* repo_id for
  every repository it was run in (silent graph collision between different repos).
  Any CLI-created graph whose ingest path wasn't already in fully-resolved form
  (relative paths, `.`/`..`, different drive-letter case, symlinks) gets a fresh
  graph on next ingest; use `gristle repos` to find and drop the old one. The MCP
  tool already resolved paths, so its graph identities only change for worktrees.
- **`gristle://repos` resource entries for unloaded graphs** now carry
  `repo_id`/`graph`/`repo_path`/`last_ingested_at`/`nodes` (from
  `describe_gristle_graphs`) instead of the old bare `graph_name` key.
- **`gristle_drop` evicts loaded engines by graph name**, so dropping via the
  sanitized repo_id shown by `gristle_repos` (e.g. `my_app` for a repo ingested as
  `my-app`) can no longer leave a stale in-memory engine that resurrects the graph
  on its next query. The CLI no longer tracebacks when FalkorDB is down — every
  command prints the "start it with docker compose" hint instead.

## [0.3.0] - 2026-06-30

### Added
- **Call confidence in impact tools.** Gristle's call resolution is name/heuristic-based,
  not type-resolved, so CALLS edges vary in reliability — every edge has recorded *how*
  it was resolved since 0.1.0, but the tools never exposed it. Now `gristle_impact`,
  `gristle_impact_score` and `gristle_change_impact` return `direct_callers_detail`
  (`[{caller, resolution, confidence}]`) and `low_confidence_callers`, and
  `gristle_changeset_impact` aggregates `low_confidence_callers` across the diff. The
  `recommendation` calls out weakly-resolved edges. `confidence` buckets the resolution
  strategy as `high` (`exact`/`file_scoped`/`typed_receiver`), `medium` (`import`/`dotted`),
  `low` (`same_file`/`unique_global`), or `unknown` (pre-`resolution` graphs). For a
  transitive path it's the weakest edge on the best route — a path is only as reliable as
  its weakest link. Lets an agent act on `high` edges and verify `low` ones instead of
  treating every edge as fact. Existing fields (`direct_callers`, …) are unchanged — this
  is purely additive.
- **Call confidence in `gristle_explore`** — exploring a function now also returns
  `callers_detail` / `callees_detail` (`[{name, resolution, confidence}]`) alongside the
  existing plain `callers` / `callees` name lists, so every tool that reports call edges
  now reports how far to trust them.

## [0.2.0] - 2026-06-30

### Added
- **`gristle_change_impact` tool** — a one-call pre-edit safety check for agents.
  Bundles the scored blast radius + risk level (`get_impact_analysis`) with the
  exact covering tests to run (`get_tests_for_entity`) and a one-line
  recommendation, so an agent can answer "what breaks if I change this, and what
  must I run?" in a single call instead of chaining `gristle_impact_score` +
  `gristle_tests`. (32 MCP tools total.)
- **`gristle_changeset_impact` tool** — the pre-edit safety check for a whole diff.
  Pass every function/class an edit touches and get one aggregated, deduplicated
  view: `external_callers` (callers *outside* the changeset — co-edited symbols are
  excluded, so this is the real surface the edit might break), the de-duplicated
  union of covering `tests_to_run`, `affected_files` (excluding the files being
  edited), and the worst-case `overall_risk_level` / `max_blast_radius_score`.
  Vets a multi-symbol change in a single call. (33 MCP tools total.)

### Fixed
- **`gristle_conventions` errored on every Next.js repo.** The framework-detection
  queries (Next.js API routes / middleware, CSS modules, Supabase edge functions)
  used the Cypher regex operator `=~`, which FalkorDB does not support — so
  `infer_conventions` raised on any repo where the Next.js/Supabase branch ran,
  taking down the whole tool for a core audience. Rewritten to use FalkorDB's
  supported `CONTAINS` / `ENDS WITH` predicates (verified: ai-chatbot now reports
  its 11 `app/api/**/route.ts` endpoints correctly). A mock-only suite can't catch
  this class of bug, so a CI guard (`tests/test_cypher_dialect.py`) now fails fast
  if a FalkorDB-incompatible operator reappears in the shipped Cypher.

## [0.1.1] - 2026-06-29

### Fixed
- **Duplicate-id nodes inflated impact analysis.** Nodes are written with `CREATE`,
  so multiple same-named entities in one file (e.g. several local `create_app`
  helpers, which collide on `qualified_name`) produced duplicate nodes sharing one
  id — and `MERGE` relationships then matched the Cartesian product of those
  duplicate endpoints, fanning one logical edge into many (a `CALLS` pair seen up to
  9×). This inflated `gristle_impact`/`gristle_impact_score` caller counts.
  `BatchCollector` now enforces the id-uniqueness invariant: an already-seen node id
  is dropped (first write wins). On flask: `CALLS` 1090 → 1036 edges with no
  duplicate pairs; node id-duplicates eliminated.
- **Unreachable FalkorDB during engine rehydration** no longer escapes as an
  unhandled `ConnectionError` (which crashed the `repo_overview` resource); an
  unreachable backend now degrades to "repo unavailable."

## [0.1.0] - 2026-06-26

First public release.

### Added
- **Vue / Svelte / Astro single-file components** — `.vue`, `.svelte`, and `.astro`
  files are now parsed (previously skipped entirely). The parser locates the
  embedded `<script>` block (or Astro `---` frontmatter) and analyzes it with the
  existing TypeScript parser, so the script's functions, classes, imports, and
  module variables become first-class graph nodes. The TS/JS is parsed by
  tree-sitter; only the SFC container is scanned to find the block, and non-script
  regions are blanked so line numbers map to the SFC file. No new dependencies. On
  real apps: a Vue RealWorld repo's 22 components yield functions/composables/imports
  linked to their files; a SvelteKit RealWorld repo's components parse cleanly.
- **`gristle_subgraph` MCP tool** — returns a `{nodes, edges, meta}` subgraph for a
  code-visualization *view*, so consumers can SEE relationships, not just list
  them. Three views: `call_hierarchy` (who calls X / what X calls), `blast_radius`
  (what breaks if X changes — callers + covering tests + routes), and
  `request_trace` (HTTP route → handler → functions → DB model, end to end). The
  JSON is directly renderable. Read-only over existing node/edge types — **no
  schema change**. Node `id` is the business id and `label` is the real node label
  (never id-prefix-decoded); edges never dangle; node props are trimmed to a
  per-label allowlist; results cap at `GRISTLE_VIZ_MAX_NODES` (default 300) with
  `meta.truncated`. On a real Express+Prisma app, `request_trace` returns the whole
  surface in one shot: 20 routes + 45 functions + 3 models, 70 edges
  (HANDLES/CALLS/USES_MODEL), zero dangling. New `GRISTLE_VIZ_MAX_NODES` /
  `GRISTLE_VIZ_DEFAULT_DEPTH` / `GRISTLE_VIZ_OUTPUT_PATH` settings.
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
- **Traversable middleware** — route middleware (`app.get('/x', requireAuth, handler)`)
  was a string list on the Route node; it now also gets `USES_MIDDLEWARE` edges
  (Route → the middleware function/class), resolved same-file and cross-file via
  imports, so you can traverse which middleware guards which routes.
- **Error-flow edges** — functions now record raised/thrown and caught exception
  types (`raises`/`catches` properties), and get `RAISES`/`CATCHES` edges to
  locally-defined exception classes (Python `raise`/`except <Type>`, JS/TS
  `throw new X()`). Re-raised variables (`raise exc`) are excluded — only
  PascalCase types are recorded. On real repos: rw-fastapi 51 RAISES + 19 CATCHES
  edges to custom exceptions; builtins (`ValueError`, …) stay in the property.
- **`USES_VARIABLE` edge** — links a function to an **imported** module-level
  `Variable` it calls a method on (`config.get()`, `schema.parse()`,
  `logger.info()`), making the previously-island `Variable` nodes queryable
  ("which functions use this config / Zod schema / registry?"). Deliberately
  scoped for precision: imported names only (resolved via the file's import map)
  and the function's own parameters are excluded — a measurement across real repos
  showed module-variable names collide with parameter names up to ~30% of the time
  (Python singletons like `app`/`config`/`logger`/`settings`), so a broad
  identifier match would be noisy; import-resolution + parameter-exclusion sidesteps
  it. On real repos: ai-chatbot links Zod schemas / registries / model lists,
  rw-fastapi links auth/cache/service singletons, flask links context-locals — all
  precise. Same-file variable use is not linked (lower value, higher shadowing risk).
- **DRF `permission_classes` on classes** — Django REST Framework class-based
  views now record their `permission_classes = (IsAuthenticated, ...)` attribute
  as a `permission_classes` list on the `Class` node. Class-based-view routes link
  `(:Route)-[:HANDLES]->(:Class)`, so joining through it surfaces a CBV route's auth
  posture (e.g. `AllowAny` vs `IsAuthenticated`) — previously invisible. On a real
  Django REST app: all 11 view classes annotated. Additive data only; it does **not**
  change unauthenticated-route flagging (DRF global defaults are invisible to static
  analysis, so flagging CBVs would risk false positives).
- **`has_error_handling` on functions** — a boolean Function property, true when
  the body contains a `try`/`except` (Python) or `try`/`catch` (JS/TS). Unlike the
  `catches` list it covers bare `except:`, `try`/`finally`, and **all** JS/TS catch
  clauses (which can't name a type), so it's the only error-handling signal for
  TypeScript. On real repos: rw-fastapi 75/659 functions (8 with no named catch),
  ai-chatbot 45/520 (all 45 invisible to `catches`). (Additive boolean property.)
- **`Variable` node type** — module-level `const`/`let`/`var` (TS/JS) and module
  assignments (Python) that aren't functions or classes — config objects,
  validation schemas (Zod), handler/route registries, React contexts, constants —
  are now nodes (`kind`, `value_kind`, `is_exported`) with `CONTAINS`/`EXPORTS`
  edges, instead of being dropped. They register as resolvable entities so imports
  can point at them. (Additive: existing queries are unaffected.)
- **Packaging** — tag-triggered PyPI + GHCR release workflow, single-source
  version (hatch dynamic), and `examples/sample-app`.

### Changed
- **RETURNS/ACCEPTS resolve nested generic return/param types** — type-flow
  edges now peel nested wrappers (`Promise<UserEntity[]>` → `UserEntity`,
  `list[dict[str, User]]` → `User`) and the `X | None` Optional shorthand,
  instead of unwrapping a single layer. Ambiguous multi-type unions
  (`User | Comment`) are left intact (no edge). On a real NestJS+TypeORM app:
  RETURNS edges 28 → 32 (the `Promise<Entity[]>` service signatures), zero new
  false edges.
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

## [0.0.1] - 2026-02-03

Initial internal scaffold (never published to PyPI; its contents ship as part of
the first public `0.1.0` release above).

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
