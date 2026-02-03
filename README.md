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

Gristle exposes 28 tools and 2 resources via MCP. See the [Integration Guide](docs/integration-guide.md) for the full reference with examples, workflows, and tips.

Key tools: `gristle_ingest`, `gristle_explore`, `gristle_impact`, `gristle_trace`, `gristle_search`, `gristle_conventions`, `gristle_tests`, `gristle_routes`, `gristle_config`, `gristle_dead_exports`, `gristle_cycles`, `gristle_data_contract`, `gristle_type_usage`, `gristle_security`, `gristle_unauthenticated_routes`, `gristle_dependency_health`.

## Development

```bash
pip install -e ".[dev]"
pytest                    # run tests
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

## Documentation

| Document | Audience | Content |
|----------|----------|---------|
| [Integration Guide](docs/integration-guide.md) | AI agents, consuming apps | Graph schema, tool reference, configuration, deployment, workflows |
| [Architecture](ARCHITECTURE.md) | Contributors | Data models, parsers, ingestion pipeline, call resolution, design decisions |
| [Ziggy Integration](docs/ziggy-integration.md) | Ziggy developers | Cypher query patterns, property dependencies, deployment checklist |
| [Changelog](CHANGELOG.md) | Everyone | Version history, what's new, breaking changes |
| [Roadmap](docs/future-improvements.md) | Product planning | Upcoming features, prioritization |
