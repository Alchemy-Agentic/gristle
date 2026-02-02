# Gristle

Graph-based code intelligence for AI agents. Gristle parses repositories into a [FalkorDB](https://www.falkordb.com/) graph database, preserving structural relationships — function calls, imports, inheritance, data flow — so AI agents can query code the way humans think about it.

## Why graphs instead of vectors?

Vector search over chunked code loses structure. "Function A calls function B which inherits from class C" becomes three unrelated text chunks. Gristle keeps these relationships as first-class edges in a graph, enabling queries like:

- **Impact analysis** — "What breaks if I change this function?"
- **Call tracing** — "How does data flow from the API handler to the database?"
- **Convention inference** — "What patterns does this project follow?"

## Supported languages

| Language | Functions | Classes | Imports | Routes | Components | Tests |
|----------|-----------|---------|---------|--------|------------|-------|
| Python | Yes | Yes | Yes | FastAPI, Flask, Django | - | pytest |
| TypeScript | Yes | Yes | Yes | Express, Hono, Fastify | React | jest, vitest |
| JavaScript | Yes | Yes | Yes | Express, Hono, Fastify | React | jest, vitest |

## Quick start

### Local (stdio transport)

Start FalkorDB, then run Gristle as a local MCP server:

```bash
docker compose up -d          # start FalkorDB
pip install -e ".[dev]"       # install Gristle
gristle                       # start MCP server (stdio)
```

Add to your MCP client config (e.g. Claude Desktop `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "gristle": {
      "command": "gristle",
      "env": {
        "GRISTLE_FALKORDB_HOST": "localhost",
        "GRISTLE_FALKORDB_PORT": "6390"
      }
    }
  }
}
```

### Remote (HTTP transport)

Run as an HTTP server for shared or cloud deployments:

```bash
GRISTLE_TRANSPORT=streamable-http \
GRISTLE_API_KEY=your-secret-key \
gristle
```

Connect from your MCP client:

```json
{
  "mcpServers": {
    "gristle": {
      "url": "https://gristle-production.up.railway.app/mcp",
      "headers": {
        "Authorization": "Bearer your-secret-key"
      }
    }
  }
}
```

### Railway

Gristle is production-ready on Railway. Deploy it alongside a FalkorDB instance and set these environment variables:

| Variable | Value |
|----------|-------|
| `GRISTLE_FALKORDB_HOST` | Internal hostname of your FalkorDB service |
| `GRISTLE_FALKORDB_PORT` | `6390` |
| `GRISTLE_FALKORDB_PASSWORD` | Your FalkorDB password (if set) |
| `GRISTLE_API_KEY` | A secret token for auth |

Railway auto-injects `PORT` which Gristle picks up. The transport defaults to `streamable-http` in the Docker image.

## MCP tools

Once connected, the agent has access to these tools:

| Tool | Description |
|------|-------------|
| `gristle_ingest` | Index a local repository into the code graph |
| `gristle_ingest_github` | Clone and index a GitHub repo (supports private repos with token) |
| `gristle_explore` | Look up a function, class, or file with full context |
| `gristle_impact` | Blast radius analysis — what breaks if you change something |
| `gristle_impact_score` | Enhanced impact analysis with blast radius scoring (0-100) and risk levels |
| `gristle_trace` | Find call paths between two functions |
| `gristle_search` | Search by name or docstring |
| `gristle_docs` | Find documentation referencing code entities |
| `gristle_routes` | List all HTTP endpoints |
| `gristle_components` | List React/UI components with usage counts |
| `gristle_deps` | Query external dependency usage |
| `gristle_tests` | Find tests for an entity, list untested functions, or get function-level coverage detail |
| `gristle_conventions` | Infer project patterns, structure, and architectural layer violations |
| `gristle_config` | Query config files, environment variables, and setup requirements |
| `gristle_dead_exports` | Find exported entities that are never imported (dead public API surface) |
| `gristle_cycles` | Detect circular import dependencies |
| `gristle_public_api` | List all public API entities (exported functions/classes) |
| `gristle_watch` | Start/stop file watching for incremental re-indexing |
| `gristle_drop` | Remove a repo's graph from FalkorDB |
| `gristle_stats` | Repository statistics — file counts, node counts, language breakdown |
| `gristle_overview` | High-level codebase summary with key entry points |
| `gristle_embed` | Generate embeddings for semantic code search (requires `[search]` extra) |
| `gristle_semantic_search` | Search code by natural language description (requires `[search]` extra) |

### MCP resources

| Resource URI | Description |
|-------------|-------------|
| `gristle://repos` | List all ingested repositories |
| `gristle://repos/{repo_id}/overview` | Statistics and overview for a specific repo |

### Example workflow

```
Agent: gristle_ingest_github("owner/repo")
  → 847 files, 12,340 nodes, 8,921 relationships

Agent: gristle_conventions()
  → FastAPI project, pytest, src/ layout, 42 routes

Agent: gristle_impact("create_user")
  → 3 direct callers, 7 transitive, 4 test files affected

Agent: gristle_trace("api_handler", "send_email")
  → api_handler → create_user → notify_user → send_email
```

## Configuration

All settings are configured via environment variables with the `GRISTLE_` prefix. See [.env.example](.env.example) for the full list.

| Variable | Default | Description |
|----------|---------|-------------|
| `GRISTLE_FALKORDB_HOST` | `localhost` | FalkorDB hostname |
| `GRISTLE_FALKORDB_PORT` | `6390` | FalkorDB port (1–65535) |
| `GRISTLE_FALKORDB_PASSWORD` | - | FalkorDB password (optional) |
| `GRISTLE_TRANSPORT` | `stdio` | `stdio` or `streamable-http` (validated) |
| `GRISTLE_API_KEY` | - | Bearer token for HTTP auth (optional) |
| `GRISTLE_INGESTION_BATCH_SIZE` | `200` | Nodes/edges per batched Cypher query (>= 1) |
| `GRISTLE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GRISTLE_LOG_FORMAT` | auto | `json` for structured, `text` for human-readable |
| `GRISTLE_MAX_FILE_SIZE_BYTES` | `512000` | Skip files larger than this in bytes (>= 1) |
| `GRISTLE_WATCHER_DEBOUNCE_SECONDS` | `2.0` | Debounce interval for file watcher |

## Performance

Ingestion uses batched Cypher `UNWIND` queries to minimize FalkorDB round-trips. Instead of one query per node or edge, Gristle groups writes by label/type and flushes them in configurable chunks (default 200). For a 500-file repo this reduces network round-trips from ~15,000 to ~2,500.

Tune with `GRISTLE_INGESTION_BATCH_SIZE` — larger values use more memory but fewer round-trips.

## Error handling

All database and I/O operations use targeted exception handling — `ResponseError` for FalkorDB/Redis failures, `OSError` for filesystem issues, `UnicodeDecodeError` for encoding problems. Errors are logged with context (file path, operation) rather than silently swallowed, making production issues easier to diagnose.

## Observability

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

## Development

```bash
pip install -e ".[dev]"
pytest                    # run tests (642 tests)
ruff check src/ tests/    # lint
ruff format src/ tests/   # format
mypy src/                 # type check
```

### Docker

Run both FalkorDB and Gristle together:

```bash
docker compose up -d
```

This starts FalkorDB on port 6390 and Gristle on port 8080 with streamable-http transport.

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design — graph schema, ingestion pipeline, call resolution strategy, and query templates.
