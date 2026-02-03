# Gristle Context

> Current state of the Gristle codebase. Read this at the start of every conversation.

## What Is Gristle

Graph-based code intelligence for AI agents. Gristle parses repositories into a FalkorDB graph database, preserving structural relationships (calls, imports, inheritance, data flow) so AI agents can query code structure through graph traversal rather than vector search.

**Primary consumer:** [Ziggy](../Ziggy/) — an AI agent platform that uses Gristle as a sidecar service for structural code analysis. See [docs/ziggy-integration.md](docs/ziggy-integration.md).

---

## What's Built

### Parsers (tree-sitter based)
- **Python** (`.py`, `.pyi`) — imports, classes, functions, FastAPI/Flask/Django routes, pytest fixtures, parametrize, complexity, visibility, TODOs
- **TypeScript/JavaScript** (`.ts`, `.tsx`, `.js`, `.jsx`) — imports, exports, classes, functions, React components, Express/Hono/Fastify/Next.js routes, Supabase/Deno edge functions, Jest/Vitest tests, barrel file re-exports
- **Markdown** (`.md`, `.mdx`) — headings, sections, code references, doc type classification

### Graph Schema
**11 node types:** File, Function, Class, Import, Route, TestCase, Document, DocumentSection, Dependency, EnvVar, TypeField

**20 edge types:** CONTAINS, DEFINED_IN, EXPORTS, CALLS, PASSED_TO, USES_HOOK, INHERITS_FROM, IMPORTS, TESTS, TESTS_FUNCTION, USES_FIXTURE, USES_DEPENDENCY, DEPENDS_ON, USES_ENV, REFERENCES, HAS_SECTION, HANDLES, HAS_FIELD, RETURNS, ACCEPTS

**26 property indexes** + 2 full-text indexes (Function.docstring, Class.docstring)

### Ingestion Pipeline (3 phases)
1. **Parse & Build Nodes** — walk repo, parse files, create nodes + in-memory maps
2. **Resolve Cross-File Edges** — CALLS (6-step resolution), INHERITS_FROM (MRO walking), IMPORTS, TESTS, USES_FIXTURE, USES_DEPENDENCY, RETURNS, ACCEPTS (type flow)
3. **Process Documentation** — Document/DocumentSection nodes, REFERENCES edges

### MCP Server
- 27 tools: ingest, ingest_github, drop, watch, explore, impact, impact_score, trace, search, docs, routes, components, deps, tests, conventions, config, embed, semantic_search, stats, overview, dead_exports, cycles, public_api, data_contract, type_usage, security, unauthenticated_routes
- 2 resources: `gristle://repos`, `gristle://repos/{repo_id}/overview`
- Transports: stdio (local) + streamable-http (remote/Railway)
- Optional bearer token auth (`GRISTLE_API_KEY`)
- Full tool reference: [docs/integration-guide.md](docs/integration-guide.md)

### Query Engine
- 20+ pre-built Cypher query templates
- Impact analysis, call tracing, convention inference, test coverage, dependency analysis
- Code quality queries: dead exports, import cycles, public API surface

### Infrastructure
- FalkorDB graph database (per-repo isolation: `gristle_{repo_id}`)
- BatchCollector for UNWIND-based bulk writes (~80% round-trip reduction)
- .gitignore-aware file walker
- Incremental file watcher
- Optional semantic search (sentence-transformers)
- Structured logging (JSON for prod, text for dev)
- 642 tests (mock graph clients, no FalkorDB needed for CI)

---

## How Ziggy Uses Gristle

Ziggy is a Node.js AI agent platform. It calls Gristle via MCP HTTP for ingestion, then queries the resulting FalkorDB graph directly (no IPC overhead for reads).

```
                  MCP HTTP (ingest + drop only)
  Ziggy (Node.js) ─────────────────────→ Gristle (Python)
       │                                      │
       │  Cypher reads                        │  Cypher writes
       │  (ziggy + gristle_*)                 │  (gristle_*)
       ▼                                      ▼
  ┌─────────────────────────────────────────────────┐
  │              FalkorDB (shared instance)          │
  │  graph: ziggy         graph: gristle_{repo_id}   │
  └─────────────────────────────────────────────────┘
```

**Key design:** Ziggy only makes 2 MCP calls to Gristle per audit (`gristle_ingest_github` and `gristle_drop`). All reads go directly to FalkorDB via Cypher.

**Ziggy agents that query code graphs:**
- **Sentinel** (security) — enriches findings with caller count, test coverage, blast radius
- **Architect** — detects import cycles, coupling hotspots, god modules, orphan modules
- **Pathfinder** — finds untested exported functions, dead code (no callers)
- **Cartographer** — maps entry points, key abstractions, conventions for onboarding guides

**Persistent vs ephemeral graphs:**
- Registered Ziggy apps get persistent graphs: `gristle_{sanitized_app_id}` (survives across audits)
- One-off audits get ephemeral graphs: `gristle_{hash}` (dropped after analysis)
- GitHub webhook push events trigger re-ingestion of persistent graphs

See [docs/ziggy-integration.md](docs/ziggy-integration.md) for the full integration reference.

---

## Non-Negotiable Rules

1. **FalkorDB for all graph data** — no SQL, no other graph DBs
2. **tree-sitter for AST parsing** — no regex for Python/TS/JS (Markdown is the exception)
3. **MCP as the interface** — tools exposed via Model Context Protocol, not custom REST
4. **Pydantic Settings** — all config via `GRISTLE_` env prefix, validated
5. **BatchCollector for writes** — no individual node/edge creation in pipeline loops
6. **Per-repo graph isolation** — each repo gets its own FalkorDB namespace
7. **Three-phase pipeline order** — nodes → edges → docs (load-bearing, don't reorder)
8. **Schema changes affect Ziggy** — new properties/edges are additive (safe); renames/removals are breaking

---

## Current Focus

### Gristle Improvements — All Phases Complete ✅

All planned improvements from `../Ziggy/docs/specs/gristle-improvements.md` are implemented:

**Phase A** ✅ (Gristle-side):
- Framework-aware entry points (`is_entry_point` + `entry_point_reason`)
- Module metadata (LOC, complexity, `File.description`)
- Dependency version resolution (`Dependency.version` from lockfiles)
- Granular test coverage (`TESTS_FUNCTION` edges, `tested_by_count` on Function nodes)

**Phase B** ✅ (Gristle-side):
- Config & env file extraction (`config_type` on Files, EnvVar nodes, USES_ENV edges)
- Layer violation detection (convention-based rules in query engine)

**Phase C** ✅ (Ziggy-side):
- Finding deduplication across audits (match keys, status lifecycle, CONFIRMED_BY)
- Cross-audit trending (AuditTrend nodes with delta metrics)

**Phase D** ✅ (Gristle-side):
- Callback/handler detection (`PASSED_TO` edges for middleware, event handlers, array method callbacks)
- 69 test cases covering TS/JS and Python callback patterns

**Additional Features** ✅ (beyond original spec):
- Dead export detection (`gristle_dead_exports` tool) — finds exported entities never imported by other files, excludes entry points
- Import cycle detection (`gristle_cycles` tool) — detects circular import chains with configurable max length, deduplicated by normalized cycle start
- Public API surface mapping (`gristle_public_api` tool) — maps all public exported entities excluding test/internal files, includes documentation percentage

---

## Key Files

| File | Purpose |
|------|---------|
| `src/gristle/config.py` | All settings (GRISTLE_ prefix, Pydantic) |
| `src/gristle/models.py` | 8 dataclasses for parsed entities |
| `src/gristle/mcp/server.py` | MCP server (21 tools + 2 resources) |
| `src/gristle/mcp/auth.py` | Bearer token auth |
| `src/gristle/ingestion/pipeline.py` | Three-phase graph builder (~1700 lines, core logic) |
| `src/gristle/ingestion/batch.py` | BatchCollector for UNWIND writes |
| `src/gristle/ingestion/walker.py` | .gitignore-aware file discovery |
| `src/gristle/ingestion/watcher.py` | Incremental file watcher |
| `src/gristle/parsers/python.py` | Python parser (~900 lines) |
| `src/gristle/parsers/typescript.py` | TS/JS parser (~1500 lines) |
| `src/gristle/parsers/markdown.py` | Markdown parser (~200 lines) |
| `src/gristle/parsers/registry.py` | Extension-based dispatch |
| `src/gristle/query/engine.py` | 20+ Cypher query templates |
| `src/gristle/graph/client.py` | FalkorDB wrapper, per-repo isolation |
| `src/gristle/graph/schema.py` | Index creation (22 + 2 full-text) |

---

## Development

```bash
pip install -e ".[dev]"       # install with dev deps
pytest                        # run 642 tests
ruff check src/ tests/        # lint
ruff format src/ tests/       # format
mypy src/                     # type check
docker compose up -d          # start FalkorDB locally
```

---

## Deployment

| Environment | Transport | FalkorDB | Auth |
|-------------|-----------|----------|------|
| Local dev | stdio | localhost:6390 | None |
| Docker | streamable-http | falkordb:6379 | Optional |
| Railway | streamable-http | falkordb.railway.internal:6390 | GRISTLE_API_KEY |

**Critical for Ziggy integration:** Both Ziggy and Gristle must point at the **same FalkorDB instance**. Gristle writes code graphs; Ziggy reads them. Different instances = Ziggy sees empty graphs.
