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

Ziggy's `GristleClient` (`src/agents/shared/code-graph.ts` in the Ziggy repo) wraps these MCP calls via a lightweight JSON-RPC-over-HTTP client.

### Direct Cypher Queries

After ingestion, Ziggy's agents query the code graph directly via FalkorDB. These are the query patterns they depend on:

#### Architect Agent (cycles, coupling, god modules)
```cypher
-- Import cycles
MATCH (a:File)-[:IMPORTS]->(b:File)-[:IMPORTS]->(a)
RETURN a.path, b.path

-- Coupling hotspots (most-imported files)
MATCH (f:File)<-[:IMPORTS]-(importer:File)
RETURN f.path, count(importer) AS import_count
ORDER BY import_count DESC

-- God modules (files with many functions)
MATCH (f:File)-[:CONTAINS]->(fn:Function)
RETURN f.path, count(fn) AS function_count
ORDER BY function_count DESC

-- Orphan modules (no importers, no importees)
MATCH (f:File)
WHERE NOT (f)<-[:IMPORTS]-() AND NOT (f)-[:IMPORTS]->()
RETURN f.path
```

#### Pathfinder Agent (test gaps, dead code)
```cypher
-- Untested exported functions
MATCH (fn:Function)
WHERE fn.is_exported = true AND fn.is_test = false
AND NOT EXISTS {
  MATCH (tf:File)-[:TESTS]->(pf:File)-[:CONTAINS]->(fn)
}
RETURN fn.name, fn.qualified_name, fn.file_path

-- Dead code (no callers, not entry points, not tests)
MATCH (fn:Function)
WHERE fn.is_exported = true
AND fn.is_test = false
AND fn.is_entry_point = false
AND fn.is_fixture = false
AND NOT EXISTS { MATCH ()-[:CALLS]->(fn) }
RETURN fn.name, fn.qualified_name, fn.file_path

-- Caller counts for prioritization
MATCH (fn:Function)<-[:CALLS]-(caller:Function)
RETURN fn.qualified_name, count(caller) AS caller_count
```

#### Cartographer Agent (onboarding guides)
```cypher
-- Entry points
MATCH (fn:Function)
WHERE fn.is_entry_point = true
RETURN fn.name, fn.file_path, fn.signature

-- Routes with handlers
MATCH (r:Route)-[:HANDLES]->(fn:Function)
RETURN r.method, r.path, fn.name, fn.file_path

-- Key abstractions (most-used classes)
MATCH (c:Class)<-[:INHERITS_FROM]-(sub:Class)
RETURN c.name, c.qualified_name, count(sub) AS subclass_count
ORDER BY subclass_count DESC

-- Project conventions
MATCH (f:File)
RETURN f.language, count(f) AS file_count
```

#### Sentinel Agent (security enrichment)
```cypher
-- Enrich findings with blast radius
MATCH (fn:Function {file_path: $filePath, name: $functionName})
OPTIONAL MATCH (caller:Function)-[:CALLS]->(fn)
OPTIONAL MATCH (tf:File)-[:TESTS]->(pf:File {path: fn.file_path})
RETURN fn.qualified_name,
       count(DISTINCT caller) AS caller_count,
       count(DISTINCT tf) > 0 AS has_tests
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
| `complexity` | Architect | Complexity hotspot detection |

### File Node
| Property | Used By | How |
|----------|---------|-----|
| `path` | All agents | File identification |
| `language` | Cartographer, Architect | Language breakdown |
| `line_count` | Architect | File size analysis |
| `is_test_file` | Pathfinder | Test vs production file classification |

### Class Node
| Property | Used By | How |
|----------|---------|-----|
| `name` | Architect, Cartographer | Display, abstraction mapping |
| `qualified_name` | Architect | Unique identification |
| `bases` | Cartographer | Inheritance chain display |

### Route Node
| Property | Used By | How |
|----------|---------|-----|
| `method` | Cartographer | API surface mapping |
| `path` | Cartographer, Sentinel | Route listing, security analysis |
| `handler_name` | Cartographer | Handler → function linking |

### Edge Types
| Edge | Used By | How |
|------|---------|-----|
| `CALLS` | Pathfinder, Sentinel, Architect | Call chains, blast radius, dead code |
| `IMPORTS` | Architect | Cycle detection, coupling analysis |
| `TESTS` | Pathfinder, Sentinel | Test coverage (file-level) |
| `INHERITS_FROM` | Cartographer | Abstraction hierarchy |
| `CONTAINS` | All agents | File → entity traversal |
| `HANDLES` | Cartographer | Route → handler linking |
| `DEFINED_IN` | Architect | Entity → file reverse lookup |

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

## What Ziggy's Agents Need (Improvement Roadmap)

These are gaps identified by analyzing how Ziggy agents query the graph. Full spec at `../Ziggy/docs/specs/gristle-improvements.md`.

### Current Gaps

| Gap | Affected Agent | Impact |
|-----|---------------|--------|
| **Binary test coverage** — file-level only (`TESTS` edge: test File → prod File), no function-level | Pathfinder | 40-60% false positive dead code (function appears untested but test file imports the module) |
| **Missing framework entry points** — Express handler callbacks, event listeners not marked `is_entry_point` | Pathfinder | Dead code false positives (handlers appear uncalled) |
| **No config file extraction** — env vars, package.json scripts, Dockerfiles not in graph | Cartographer | Can't generate setup instructions |
| **No module-level metrics** — LOC and complexity not aggregated per file | Architect | Heuristic god-module detection instead of metric-based |
| **No dependency versions** — `Dependency` node has name but no version | Exodus (migration) | Can't assess version currency without re-parsing files |
| **No callback/event detection** — `addEventListener`, `EventEmitter.on()`, `app.use()` not tracked | Pathfinder, Architect | Missed call paths through event system |

### Planned Improvements (Gristle-Side)

**Phase A:**
1. Framework-aware entry points — mark Express/Hono/Fastify handler callbacks as `is_entry_point`
2. Module metadata — aggregate LOC and complexity per file on the `File` node
3. Dependency version resolution — parse lockfiles, add `version` to `Dependency` nodes
4. Granular test coverage — `TESTS_FUNCTION` edges from test functions to production functions

**Phase B:**
5. Config & env file extraction — new `ConfigFile` and `EnvVar` nodes
6. Layer violation detection — new Cypher query in `engine.py`

**Phase D:**
7. Callback/event handler detection — new `EventHandler` nodes and `HANDLES_EVENT` edges

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
