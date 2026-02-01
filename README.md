# Gristle

Graph-based code intelligence for AI agents. Gristle parses repositories into a
queryable [FalkorDB](https://www.falkordb.com/) graph, preserving structural
relationships — function calls, imports, inheritance, test coverage — so AI
agents can reason about code architecture instead of searching over chunks.

## Key features

- **Multi-language parsing** — Python, TypeScript, JavaScript via tree-sitter
- **Import-aware call resolution** — 6-step strategy including inheritance
  walking, barrel-file re-exports, and fixture mapping
- **13 MCP tools** — explore, impact analysis, call tracing, route discovery,
  component listing, dependency mapping, test coverage, conventions, and more
- **Semantic search** — optional vector embeddings via sentence-transformers
- **Incremental updates** — file watcher for live graph re-indexing
- **Remote or local** — stdio transport for local dev, streamable-http for
  production (Railway, Docker)

## Quick start

### 1. Start FalkorDB

```bash
docker compose up -d
```

### 2. Install Gristle

```bash
pip install -e .

# Optional: enable semantic search
pip install -e ".[search]"
```

### 3. Run as a local MCP server (stdio)

```bash
gristle
```

Or add it to your MCP client config (e.g. Claude Desktop):

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

### 4. Run as an HTTP server (production)

```bash
GRISTLE_TRANSPORT=streamable-http \
GRISTLE_FALKORDB_HOST=your-falkordb-host \
GRISTLE_API_KEY=your-secret \
gristle
```

## Usage

Once connected, an AI agent calls tools in this order:

```
1. gristle_ingest(repo_path="/path/to/repo")   # Build the graph
2. gristle_conventions()                         # Learn project structure
3. gristle_explore(entity="UserService")         # Dive into specifics
4. gristle_impact(entity_name="authenticate")    # Check blast radius
```

### MCP tools

| Tool | Purpose |
|---|---|
| `gristle_ingest` | Index a local repository |
| `gristle_ingest_github` | Clone and index a GitHub repo |
| `gristle_watch` | Start/stop incremental file watching |
| `gristle_explore` | Explore a function, class, or file |
| `gristle_impact` | Blast radius analysis |
| `gristle_trace` | Find call paths between two entities |
| `gristle_search` | Search by name or docstring |
| `gristle_docs` | Query documentation relationships |
| `gristle_routes` | List HTTP endpoints |
| `gristle_components` | List React/UI components |
| `gristle_deps` | External dependency analysis |
| `gristle_tests` | Test coverage queries |
| `gristle_conventions` | Infer project patterns and structure |
| `gristle_embed` | Build semantic search index |
| `gristle_semantic_search` | Find code by natural language |
| `gristle_drop` | Remove a repo's graph |

## Configuration

All settings use the `GRISTLE_` prefix and can be set via environment variables
or a `.env` file.

| Variable | Default | Description |
|---|---|---|
| `GRISTLE_FALKORDB_HOST` | `localhost` | FalkorDB hostname |
| `GRISTLE_FALKORDB_PORT` | `6380` | FalkorDB port |
| `GRISTLE_FALKORDB_PASSWORD` | — | FalkorDB password (optional) |
| `GRISTLE_TRANSPORT` | `stdio` | `stdio` or `streamable-http` |
| `GRISTLE_HTTP_HOST` | `0.0.0.0` | HTTP bind address |
| `GRISTLE_HTTP_PORT` | `8080` | HTTP port (Railway overrides via `PORT`) |
| `GRISTLE_API_KEY` | — | Bearer token auth (optional) |
| `GRISTLE_MAX_FILE_SIZE_BYTES` | `512000` | Skip files larger than this |
| `GRISTLE_REPO_STORAGE_PATH` | `./repos` | Temp storage for cloned repos |
| `GRISTLE_WATCHER_DEBOUNCE_SECONDS` | `2.0` | File watcher debounce |

## Docker

```bash
# Build
docker build -t gristle .

# Run with external FalkorDB
docker run -p 8080:8080 \
  -e GRISTLE_FALKORDB_HOST=host.docker.internal \
  -e GRISTLE_API_KEY=your-secret \
  gristle
```

## Deploy to Railway

The repo includes a `railway.toml` ready for deployment. Add a FalkorDB service
to your Railway project and set `GRISTLE_FALKORDB_HOST` to the internal hostname.

## Development

```bash
pip install -e ".[dev]"
pytest
```

## Architecture

Gristle builds a code graph with these node types:

- **File** — source files and documents
- **Function** — functions, methods, test cases
- **Class** — classes, interfaces, type aliases
- **Import** — import statements
- **Route** — HTTP endpoints
- **Document** — markdown documentation

Connected by relationships like `CALLS`, `IMPORTS`, `DEFINED_IN`, `CONTAINS`,
`INHERITS`, `TESTS`, `DOCUMENTS`, `DEPENDS_ON`, and more.

See [ARCHITECTURE.md](ARCHITECTURE.md) for the full data model and design
decisions, [gristle-spec.md](gristle-spec.md) for the detailed specification,
and [MCP_USAGE_GUIDE.md](MCP_USAGE_GUIDE.md) for the tool reference.

## License

Proprietary — Alchemy Agentic
