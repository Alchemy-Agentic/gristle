# Ziggy Integration

> How Gristle serves as Ziggy's code intelligence sidecar.

## Overview

[Ziggy](https://github.com/your-org/ziggy) is a Node.js AI agent platform that orchestrates specialist agents for security scanning, architecture analysis, test gap detection, and developer onboarding. Gristle provides the structural code analysis layer — Ziggy triggers Gristle to ingest repositories, then queries the resulting FalkorDB graph directly.

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

**Key design:** Ziggy makes only 2 MCP calls per audit — `gristle_ingest_github` (to trigger ingestion) and `gristle_drop` (to clean up ephemeral graphs). All subsequent reads go directly to FalkorDB via Cypher queries, avoiding IPC overhead during agent analysis.

For the full MCP tool reference, graph schema, and configuration, see the [Integration Guide](integration-guide.md).

---

## Shared FalkorDB Instance

Both services must point at the **same FalkorDB instance**. Gristle writes code graphs; Ziggy reads them. Different instances = Ziggy sees empty graphs.

| Environment | Gristle Config | Ziggy Config |
|-------------|---------------|--------------|
| Local dev | `GRISTLE_FALKORDB_HOST=localhost`, `GRISTLE_FALKORDB_PORT=6390` | `FALKORDB_HOST=localhost`, `FALKORDB_PORT=6390` |
| Railway | `GRISTLE_FALKORDB_HOST=falkordb.railway.internal`, `GRISTLE_FALKORDB_PORT=16379` | `FALKORDB_HOST=falkordb.railway.internal`, `FALKORDB_PORT=16379` |

Graph namespaces are isolated:
- `ziggy` — Ziggy's operational graph (agents, conversations, findings, apps)
- `gristle_{repo_id}` — Code graphs written by Gristle, read by both

---

## What Ziggy Calls

### MCP Tools Used

| Tool | When | Parameters |
|------|------|------------|
| `gristle_ingest_github` | Audit start | `repo_url`, `github_token`, `repo_id` (optional — used for persistent graphs) |
| `gristle_drop` | Audit cleanup | `repo_id` (only for ephemeral graphs) |
| `gristle_stats` | After ingestion | `repo_id` (to cache stats on Ziggy's CodeGraph node) |
| `gristle_dead_exports` | (available, not used) | `repo_id` — Ziggy uses direct Cypher via `QUERY_DEAD_EXPORTS` instead |
| `gristle_cycles` | (available, not used) | `repo_id` — Ziggy uses direct Cypher via `QUERY_IMPORT_CYCLES_FULL` instead |
| `gristle_public_api` | (available, not used) | `repo_id` — Ziggy uses direct Cypher via `QUERY_PUBLIC_API` instead |

Ziggy's `GristleClient` (`src/agents/shared/code-graph.ts` in the Ziggy repo) wraps MCP calls for writes and direct FalkorDB queries for reads. The three code quality tools (`dead_exports`, `cycles`, `public_api`) are available via MCP but Ziggy agents use direct Cypher equivalents for consistency with the existing read pattern (no IPC overhead).

### Direct Cypher Queries

After ingestion, Ziggy's agents query the code graph directly via FalkorDB. These are the query patterns they depend on:

#### Architect Agent (cycles, coupling, god modules)
```cypher
-- Import cycles
MATCH path = (a:File)-[:IMPORTS*2..6]->(a)
WITH [n IN nodes(path) | n.path] AS cycle
WHERE cycle[0] <= cycle[1]
RETURN DISTINCT cycle

-- Coupling hotspots (most-imported files)
MATCH (f:File)<-[:IMPORTS]-(importer:File)
RETURN f.path, count(importer) AS import_count
ORDER BY import_count DESC

-- God modules (files with many functions)
MATCH (f:File)-[:CONTAINS]->(fn:Function)
RETURN f.path, count(fn) AS function_count
ORDER BY function_count DESC
```

#### Pathfinder Agent (test gaps, dead code)
```cypher
-- Untested exported functions (uses tested_by_count when available)
MATCH (f:Function {is_exported: true, is_test: false})
MATCH (f)-[:DEFINED_IN]->(file:File {is_test_file: false})
OPTIONAL MATCH (testFile:File {is_test_file: true})-[:TESTS]->(file)
WITH f, testFile
WHERE CASE
  WHEN f.tested_by_count IS NOT NULL THEN f.tested_by_count = 0
  ELSE testFile IS NULL
END
RETURN f.name, f.qualified_name, f.file_path, f.complexity

-- Dead code (checks both CALLS and PASSED_TO edges)
MATCH (f:Function)
WHERE f.is_test = false AND f.is_entry_point = false AND f.is_fixture = false
OPTIONAL MATCH (caller:Function)-[:CALLS]->(f)
OPTIONAL MATCH (route:Route)-[:HANDLES]->(f)
OPTIONAL MATCH (cls:Class)-[:CONTAINS]->(f)
OPTIONAL MATCH (passer:Function)-[:PASSED_TO]->(f)
WITH f WHERE caller IS NULL AND route IS NULL AND cls IS NULL AND passer IS NULL
RETURN f.name, f.qualified_name, f.file_path

-- Impact analysis (traverses CALLS + PASSED_TO)
MATCH (caller:Function)-[:CALLS|PASSED_TO*1..3]->(target)
WHERE target.name = $name
RETURN caller.qualified_name, caller.file_path
```

#### Cartographer Agent (onboarding guides + public API)
```cypher
-- Entry points with reason
MATCH (fn:Function {is_entry_point: true})
MATCH (fn)-[:DEFINED_IN]->(file:File)
RETURN fn.name, file.path,
  CASE WHEN fn.entry_point_reason IS NOT NULL THEN fn.entry_point_reason
       WHEN fn.name IN ['main', 'app', 'server'] THEN 'main'
       ELSE 'exported'
  END AS type

-- Routes with handlers
MATCH (r:Route)-[:HANDLES]->(fn:Function)
RETURN r.method, r.path, fn.name, fn.file_path

-- Dependencies with versions
MATCH (d:Dependency)
RETURN d.name, d.import_count, d.version
ORDER BY d.import_count DESC

-- Public API surface (doc coverage %)
MATCH (file:File)-[:EXPORTS]->(entity)
WHERE NOT file.is_test_file
  AND (entity.visibility IS NULL OR entity.visibility = 'public')
  AND NOT file.path CONTAINS '__'
  AND NOT file.path CONTAINS '/internal/'
  AND NOT file.path CONTAINS '/_'
RETURN entity.qualified_name, entity.name, file.path,
       labels(entity)[0] AS entityType, entity.docstring
```

#### Architect Agent (dead exports + improved cycles)
```cypher
-- Dead exports (exported but never imported)
MATCH (file:File)-[:EXPORTS]->(entity)
WHERE NOT EXISTS {
  MATCH (other:File)-[:IMPORTS]->(file)
  WHERE other.path <> file.path
}
AND (entity.is_entry_point IS NULL OR entity.is_entry_point = false)
AND file.is_test_file = false
RETURN entity.qualified_name, entity.name, file.path, labels(entity)[0]

-- Import cycles (configurable depth, dedup in TypeScript)
MATCH path = (a:File)-[:IMPORTS*2..${maxLen}]->(a)
WHERE a.is_test_file = false
WITH [n IN nodes(path) | n.path] AS files, length(path) AS length
RETURN files, length
ORDER BY length ASC
```

#### Sentinel Agent (security enrichment)
```cypher
-- Enrich findings with blast radius (uses tested_by_count when available)
MATCH (fn:Function {file_path: $filePath, name: $functionName})
OPTIONAL MATCH (caller:Function)-[:CALLS|PASSED_TO*1..3]->(fn)
RETURN fn.qualified_name,
       count(DISTINCT caller) AS caller_count,
       CASE WHEN fn.tested_by_count IS NOT NULL THEN fn.tested_by_count > 0
            ELSE EXISTS { MATCH (tf:File {is_test_file: true})-[:TESTS]->(pf:File {path: fn.file_path}) }
       END AS has_tests
```

---

## Node Properties Ziggy Depends On

These properties are queried by Ziggy agents. **Renaming or removing them is a breaking change.**

### Function Node
| Property | Used By | How |
|----------|---------|-----|
| `name` | All agents | Display, matching |
| `qualified_name` | Pathfinder, Sentinel | Unique identification |
| `file_path` | All agents | File grouping, display |
| `is_exported` | Pathfinder | Filter for testable/dead code |
| `is_test` | Pathfinder | Exclude test functions from analysis |
| `is_entry_point` | Pathfinder, Cartographer | Entry point detection, dead code exclusion |
| `is_fixture` | Pathfinder | Dead code exclusion |
| `is_component` | Cartographer | React component detection |
| `signature` | Cartographer | Display in onboarding guides |
| `start_line`, `end_line` | Sentinel | Source location for findings |
| `complexity` | Architect, Pathfinder | Complexity hotspot detection, test gap prioritization |
| `entry_point_reason` | Cartographer, Pathfinder | **Phase A** — categorized reason: `"react_component"`, `"route_handler"`, `"pytest_fixture"`, etc. |
| `tested_by_count` | Pathfinder, Sentinel (enrichment) | **Phase A** — number of test functions exercising this function (via `TESTS_FUNCTION` edges) |
| `visibility` | Cartographer | Public API surface filtering (`IS NULL OR 'public'`) |
| `docstring` | Cartographer | Function documentation for abstraction descriptions and public API doc coverage |
| `security_findings` | Sentinel | List of security finding tags (e.g. `"unsafe_call:eval"`, `"llm_output_risk:exec"`) |
| `security_finding_count` | Sentinel | Number of security findings for prioritization |

### Dependency Node
| Property | Used By | How |
|----------|---------|-----|
| `name` | Cartographer, Exodus | Dependency identification |
| `version` | Cartographer, Exodus | **Phase A** — declared version string from lockfiles |
| `import_count` | Cartographer | Usage frequency |
| `latest_version` | Exodus | Latest version from npm/PyPI registry |
| `is_outdated` | Exodus | Whether declared < latest |
| `vulnerability_count` | Exodus | Number of known CVEs (from OSV.dev + PyPI) |
| `vulnerabilities` | Exodus | List of CVE/GHSA IDs |
| `checked_at` | Exodus | ISO timestamp of last check |

### File Node
| Property | Used By | How |
|----------|---------|-----|
| `path` | All agents | File identification |
| `language` | Cartographer, Architect | Language breakdown |
| `line_count` | Architect | File size analysis |
| `is_test_file` | Pathfinder, Architect, Cartographer | Test vs production file classification, cycle/dead export/public API filtering |

### Class Node
| Property | Used By | How |
|----------|---------|-----|
| `name` | Architect, Cartographer | Display, abstraction mapping |
| `qualified_name` | Architect | Unique identification |
| `bases` | Cartographer | Inheritance chain display |

### Import Node
| Property | Used By | How |
|----------|---------|-----|
| `module_path` | Architect | Import source identification |
| `imported_names` | Architect | Named imports |
| `is_relative` | Architect | Relative vs absolute import |
| `resolved` | Architect | Whether the import resolves to an internal file (true) or is external/unresolved (false) |

### Route Node
| Property | Used By | How |
|----------|---------|-----|
| `method` | Cartographer | API surface mapping |
| `path` | Cartographer, Sentinel | Route listing, security analysis |
| `handler_name` | Cartographer | Handler → function linking |
| `has_auth` | Sentinel | Whether route has auth middleware/decorators/app-level auth. Checks per-route middleware, handler decorators, and app-level `app.use('/path', authMiddleware)` patterns |

### TypeField Node
| Property | Used By | How |
|----------|---------|-----|
| `name` | Architect | Field name in interface/type/class |
| `type_annotation` | Architect | Type string (e.g., "string", "User") |
| `is_optional` | Architect | Whether field is optional |
| `default_value` | Architect | Default value if any |

### Edge Types
| Edge | Used By | How |
|------|---------|-----|
| `CALLS` | Pathfinder, Sentinel, Architect | Call chains, blast radius, dead code |
| `PASSED_TO` | Pathfinder, Sentinel, Architect | **Phase D** — callback/handler detection (middleware, event handlers, array method callbacks). Traversed alongside `CALLS` in impact analysis, dead code, call paths. |
| `IMPORTS` | Architect | Cycle detection (configurable depth), coupling analysis |
| `EXPORTS` | Architect, Cartographer | Dead export detection, public API surface mapping |
| `TESTS` | Pathfinder, Sentinel | Test coverage (file-level, fallback) |
| `TESTS_FUNCTION` | Pathfinder | **Phase A** — function-level test coverage (test Function → prod Function, depth 1=direct, 2=via helper, 3=import-based JS/TS fallback) |
| `INHERITS_FROM` | Cartographer | Abstraction hierarchy |
| `CONTAINS` | All agents | File → entity traversal |
| `HANDLES` | Cartographer | Route → handler linking |
| `DEFINED_IN` | Architect, Pathfinder | Entity → file reverse lookup |
| `USES_DEPENDENCY` | Cartographer | Function → dependency tracking |
| `DEPENDS_ON` | Cartographer | File → dependency tracking |
| `HAS_FIELD` | Architect | Class/Interface → TypeField (type structure) |
| `RETURNS` | Architect | Function → Class (return type resolution) |
| `ACCEPTS` | Architect | Function → Class (parameter type, has `param_name` property) |

---

## Persistent vs Ephemeral Graphs

### Ephemeral (default)
- Created per audit with a hash-based `repo_id`
- Graph name: `gristle_{sha256_hash}`
- Dropped by Ziggy after agents finish (`gristle_drop`)

### Persistent (registered apps)
- Ziggy passes `repo_id=appId` to `gristle_ingest_github`
- Graph name: `gristle_{sanitized_app_id}` (e.g., `pig-knuckle` → `gristle_pig_knuckle`)
- **Not dropped** after audits — survives across analyses
- Re-ingested via GitHub webhook push events (Ziggy triggers `gristle_ingest_github` again with same `repo_id`)
- Ziggy tracks graph metadata in a `CodeGraph` node in the `ziggy` graph (status, cached stats, updatedAt)

**Important for Gristle:** When `repo_id` is provided to `gristle_ingest_github`, the resulting graph uses that ID for naming. Re-ingesting with the same `repo_id` overwrites the previous graph. This is the intended behavior for persistent graphs — Ziggy relies on deterministic naming.

---

## Gristle Improvements (All Phases Complete)

All planned improvements from the spec at `../Ziggy/docs/specs/gristle-improvements.md` have been implemented. Ziggy agents now leverage these features with backwards-compatible `CASE WHEN` fallback patterns.

### Phase A (Complete) — Function-Level Coverage + Entry Points
1. ✅ **Framework-aware entry points** — Express/Hono/Fastify handlers, React components, Next.js pages, pytest fixtures, etc. marked `is_entry_point` with `entry_point_reason`
2. ✅ **Module metadata** — LOC and complexity aggregation, `File.description` from leading comments
3. ✅ **Dependency version resolution** — `Dependency.version` parsed from lockfiles
4. ✅ **Granular test coverage** — `TESTS_FUNCTION` edges from test functions to production functions, `tested_by_count` cached on Function nodes

### Phase B (Complete) — Config & Environment
5. ✅ **Config & env file extraction** — `ConfigFile` properties on File nodes, `EnvVar` nodes with `USES_ENV` edges
6. ✅ **Layer violation detection** — Convention-based rules (routes → services → adapters)

### Phase C (Complete, Ziggy-Side) — Audit Intelligence
7. ✅ **Finding deduplication** — Match keys, status lifecycle (`new → open → fixed → regressed`), `CONFIRMED_BY` relationships
8. ✅ **Cross-audit trending** — `AuditTrend` nodes with coverage/cycle/growth deltas

### Phase D (Complete) — Call Graph Completeness
9. ✅ **Callback/handler detection** — `PASSED_TO` edges for middleware, event handlers, array method callbacks. All Ziggy queries now traverse `[:CALLS|PASSED_TO]`

### Additional Features (Beyond Original Spec)
10. ✅ **Dead export detection** — `gristle_dead_exports` MCP tool. Finds exported entities never imported by other files. Excludes entry points (route handlers, React components, etc.) to avoid false positives.
11. ✅ **Import cycle detection** — `gristle_cycles` MCP tool. Detects circular import chains with configurable `max_length` (default 10). Deduplicates by normalizing cycles to lexicographically smallest start node.
12. ✅ **Public API surface** — `gristle_public_api` MCP tool. Maps all public exported entities excluding test/internal files. Returns entity names, types, file paths, and documentation percentage.

---

## Health & Diagnostics

Ziggy has admin endpoints for debugging the integration:

| Ziggy Endpoint | Purpose |
|---------------|---------|
| `GET /v1/admin/gristle/health` | Calls Gristle's `/health` endpoint |
| `POST /v1/admin/gristle/test-ingest` | Test ingestion with direct token |
| `POST /v1/admin/gristle/test-audit-flow` | Test full credential store → ingest flow |
| `GET /v1/admin/gristle/query-test/:graphName` | Test if Ziggy can query a specific code graph |

Gristle's own health check: `GET /health` (no auth) returns `{ status, server, version, repos_loaded }`.

---

## Deployment Checklist

When deploying Gristle alongside Ziggy:

1. [ ] Both services point to the same FalkorDB instance
2. [ ] `GRISTLE_FALKORDB_HOST` and `GRISTLE_FALKORDB_PORT` match Ziggy's `FALKORDB_HOST` and `FALKORDB_PORT`
3. [ ] `GRISTLE_API_KEY` is set and matches Ziggy's `GRISTLE_URL` auth header
4. [ ] Ziggy's `GRISTLE_URL` points to Gristle's HTTP endpoint (e.g., `http://gristle.railway.internal:8080`)
5. [ ] `GET /health` returns 200 from Gristle
6. [ ] Test ingestion works: Ziggy admin → `POST /v1/admin/gristle/test-ingest`
