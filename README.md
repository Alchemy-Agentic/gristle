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
        "GRISTLE_FALKORDB_PORT": "6379"
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
| `GRISTLE_FALKORDB_PORT` | `6379` |
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
| `gristle_trace` | Find call paths between two functions |
| `gristle_search` | Search by name or docstring |
| `gristle_docs` | Find documentation referencing code entities |
| `gristle_routes` | List all HTTP endpoints |
| `gristle_components` | List React/UI components with usage counts |
| `gristle_deps` | Query external dependency usage |
| `gristle_tests` | Find tests for an entity or list untested functions |
| `gristle_conventions` | Infer project patterns and structure |
| `gristle_watch` | Start/stop file watching for incremental re-indexing |
| `gristle_drop` | Remove a repo's graph from FalkorDB |

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
| `GRISTLE_FALKORDB_PORT` | `6380` | FalkorDB port |
| `GRISTLE_FALKORDB_PASSWORD` | - | FalkorDB password (optional) |
| `GRISTLE_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `GRISTLE_API_KEY` | - | Bearer token for HTTP auth (optional) |
| `GRISTLE_LOG_LEVEL` | `INFO` | `DEBUG`, `INFO`, `WARNING`, `ERROR` |
| `GRISTLE_LOG_FORMAT` | auto | `json` for structured, `text` for human-readable |

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
pytest                    # run tests
```

## Architecture

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full design — graph schema, ingestion pipeline, call resolution strategy, and query templates.
