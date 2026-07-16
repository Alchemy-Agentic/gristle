# Gristle

Graph-based code intelligence for AI agents. Gristle parses repositories into a [FalkorDB](https://www.falkordb.com/) graph database, preserving structural relationships — function calls, imports, inheritance, routes, and data models — so AI agents can query code the way humans think about it.

It traces a request from **HTTP route → handler → function → database model** end-to-end, scores the **blast radius** of a change *before* you make it, and renders any slice of the graph as a **diagram** — all exposed natively over MCP so an agent can call it mid-task.

## Why graphs instead of vectors?

Vector search over chunked code loses structure. "Function A calls function B which inherits from class C" becomes three unrelated text chunks. Gristle keeps these relationships as first-class edges in a graph, enabling queries like:

- **Impact analysis** — "What breaks if I change this function?"
- **Call tracing** — "How does a request flow from the API handler toward the database?"
- **Convention inference** — "What patterns does this project follow?"
- **Visualization** — render a route's full path to the database, or a function's blast radius, as a node-link diagram (`gristle_subgraph` over MCP, or a self-contained HTML file via `gristle viz`).

## Who is Gristle for?

Gristle serves two consumers equally — **AI coding agents** (via MCP) and the **developers driving them**. Three primary beneficiaries:

- **AI coding agents (and the developer driving them) — the headline audience.** An agent can't hold a large repo in context, and vector-chunking destroys the very relationships it needs to reason about a change. Before editing, an agent asks Gristle *"what breaks if I change this?"* (`gristle_impact_score`), *"which tests cover it?"* (`gristle_tests`), and *"how does this route reach the database?"* (`gristle_subgraph`) — turning a confident guess into a grounded edit. Gristle is the only code graph of its kind that's **MCP-native**.
- **Developers onboarding to an unfamiliar codebase.** They need a *map*, not taint analysis. `gristle_conventions` plus a `request_trace` subgraph turn a wall of files into a picture of the request surface — build-free, in seconds.
- **Security / API auditors doing a first-pass structural review.** *Which routes have no auth? What's the middleware chain? Any hardcoded secrets or CVE-laden dependencies?* — surface enumeration across the whole repo (`gristle_security`, `gristle_unauthenticated_routes`, `gristle_dependency_health`). A first-pass structural audit, explicitly **not** a taint/dataflow proof.

**In one line:** Gristle is the connected route→handler→DB code graph that AI agents query over MCP — so the agent knows the blast radius and the request-to-database path *before* it writes a diff. Deeper than ctags/tree-sitter-only tools, lighter and broader than type-resolved indexers, and the only one designed to be *called by an agent* rather than browsed in an IDE.

**Probably not for you if** you need type-exact resolution (use a SCIP/LSIF indexer such as Sourcegraph), dataflow/taint proofs (CodeQL), a language outside Python/TS/JS (+ Vue/Svelte/Astro), or zero infrastructure (Gristle needs FalkorDB running). Its edges are high-coverage navigation aids, not proofs — see the boundary below.

## Scope (and what it isn't)

Gristle is a **fast, build-free, framework-aware *structural* graph** built with tree-sitter — multiple languages in one queryable graph, exposed natively over MCP for agents. That's its niche, and it's worth being clear about the boundary:

- **Call/import resolution is name- and heuristic-based**, not type-resolved. It's high-coverage and great for navigation and architecture, but it can miss or mis-link edges that a type-aware indexer (SCIP/LSIF, Sourcegraph) would get exactly. Edges are best-effort, not proofs.
- **It is not a dataflow/taint engine** (CodeQL/Glean). It models structural and framework relationships (calls, routes→handlers, code→model, tests→code, deps), not value-level data flow.
- Coverage is strongest on the supported frameworks below; constructs outside them (and languages without a parser) are simply not represented.

## Supported languages

| Language | Functions | Classes | Imports | Routes | Components | Tests |
|----------|-----------|---------|---------|--------|------------|-------|
| Python | Yes | Yes | Yes | FastAPI, Flask, Django | - | pytest |
| TypeScript | Yes | Yes | Yes | Express, Hono, Fastify | React | jest, vitest |
| JavaScript | Yes | Yes | Yes | Express, Hono, Fastify | React | jest, vitest |
| Vue / Svelte / Astro | Yes | Yes | Yes | - | - | - |

Vue/Svelte/Astro single-file components are parsed by extracting the embedded
`<script>` block (or Astro `---` frontmatter) and analyzing it with the TypeScript
parser, so the script's functions, classes, imports, and variables become graph
nodes alongside the rest of the codebase.

## Quick start

**Prerequisites:** Python 3.11+ and Docker (with Docker Compose). FalkorDB runs in Docker; everything else is pip-installed.

### 1. Install

> Once published, the fastest path will be `uvx gristle` / `pipx install gristle`, or `docker run ghcr.io/alchemy-agentic/gristle`.

For now (and for development), install from source:

```bash
git clone https://github.com/Alchemy-Agentic/gristle
cd gristle
pip install -e .
```

### 2. Start FalkorDB and check your setup

```bash
docker compose up -d falkordb   # start FalkorDB (exposes localhost:6390)
gristle doctor                  # verify FalkorDB, parsers, and config
```

### 3. Index a repo and explore it — from the terminal

```bash
gristle ingest examples/sample-app --repo-id demo
gristle overview --repo-id demo
gristle explore register --repo-id demo
gristle query "MATCH (f:Function)-[:CALLS]->(g:Function) RETURN f.name, g.name LIMIT 10" --repo-id demo
gristle viz --repo-id demo --view request_trace --out demo.html   # render route→DB as a self-contained HTML diagram
gristle repos                   # list every indexed graph (path, freshness, size)
```

Point `gristle ingest` at your own project to index it. See [examples/](examples/) for a guided walkthrough.

### 4. Wire it into your AI client (MCP)

Run Gristle as a local MCP server (`gristle serve`, or just `gristle`) and add it to your client config — e.g. Claude Desktop's `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "gristle": {
      "command": "gristle",
      "args": ["serve"],
      "env": {
        "GRISTLE_FALKORDB_HOST": "localhost",
        "GRISTLE_FALKORDB_PORT": "6390"
      }
    }
  }
}
```

> FalkorDB must be running (`docker compose up -d falkordb`) before the first tool call.

Or with the Claude Code CLI: `claude mcp add gristle --env GRISTLE_FALKORDB_PORT=6390 -- gristle serve`.

Then ask your agent to ingest a repo (`gristle_ingest`) and explore the code, trace calls, or run an impact analysis.

### Use as a library

Gristle's pipeline and query engine are usable directly from Python:

```python
from gristle.graph.client import GraphClient
from gristle.ingestion.pipeline import IngestionPipeline
from gristle.parsers.registry import ParserRegistry

graph = GraphClient(host="localhost", port=6390, repo_id="myrepo")
IngestionPipeline(graph, ParserRegistry().build_default()).ingest_repo("/path/to/your/repo")
rows = graph.execute("MATCH (f:Function) RETURN count(f) AS functions").records
print(rows)  # e.g. [{'functions': 1234}]
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
      "url": "https://your-gristle-host/mcp",
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

Gristle exposes 34 tools and 2 resources via MCP. See the [Integration Guide](docs/integration-guide.md) for the full reference with examples, workflows, and tips. Outputs are sized for agent context windows: unbounded lists are capped with `<field>_omitted` markers, while counts and scores always reflect the full data.

Key tools: `gristle_ingest`, `gristle_explore`, `gristle_impact`, `gristle_impact_score`, `gristle_change_impact`, `gristle_changeset_impact`, `gristle_trace`, `gristle_subgraph`, `gristle_search`, `gristle_conventions`, `gristle_tests`, `gristle_routes`, `gristle_models`, `gristle_config`, `gristle_dead_exports`, `gristle_cycles`, `gristle_data_contract`, `gristle_type_usage`, `gristle_security`, `gristle_unauthenticated_routes`, `gristle_dependency_health`. `gristle_subgraph` returns a `{nodes, edges, meta}` subgraph for the `call_hierarchy`, `blast_radius`, and `request_trace` views — the same data `gristle viz` renders to HTML.

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
| [Changelog](CHANGELOG.md) | Everyone | Version history, what's new, breaking changes |
| [Audience](docs/audience.md) | Product / positioning | Who Gristle benefits most — personas, use cases, and how that drives the parser roadmap |
| [Roadmap](docs/future-improvements.md) | Product planning | Upcoming features, prioritization |

## Contributing

Contributions are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md) for setup, testing, and the pull-request checklist. To report a security issue, see [SECURITY.md](SECURITY.md).

## License

Gristle is released under the [MIT License](LICENSE).

Gristle connects to — but does not bundle — [FalkorDB](https://www.falkordb.com/), which is distributed under its own license.
