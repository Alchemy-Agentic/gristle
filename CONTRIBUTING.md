# Contributing to Gristle

Thanks for your interest in improving Gristle. This guide covers local setup, the
checks your change needs to pass, and the conventions the codebase follows.

## Development setup

**Prerequisites:** Python 3.11+ and Docker (with Docker Compose).

```bash
git clone https://github.com/Alchemy-Agentic/gristle
cd gristle
docker compose up -d falkordb   # start FalkorDB on localhost:6390
pip install -e ".[dev]"         # install Gristle + dev tooling
```

The test suite uses mock graph clients, so **FalkorDB is not required to run the
tests** — only to actually ingest a repository.

## Checks

All three gates run in CI ([.github/workflows/ci.yml](.github/workflows/ci.yml)) and
must pass before a PR is merged:

```bash
pytest                    # run the test suite
ruff check src/ tests/    # lint
ruff format src/ tests/   # format (use --check in CI)
mypy src/                 # type check
```

Please add or update tests for any behavior change.

## Conventions

- **Readability first** — clear names over cleverness; functions do one thing.
- **AST parsing uses tree-sitter** for Python/TS/JS (Markdown is the regex exception).
- **All config is via Pydantic Settings** with the `GRISTLE_` env prefix — no hardcoded config.
- **Graph writes go through `BatchCollector`** (`UNWIND`-based bulk writes), not
  per-node/edge calls in pipeline loops.
- **Each repo gets its own FalkorDB namespace** (`gristle_{repo_id}`) — never write
  cross-graph queries.
- Line length is 120 (see `pyproject.toml`).

See [ARCHITECTURE.md](ARCHITECTURE.md) for the data model, parsers, and the
three-phase ingestion pipeline before changing the pipeline or graph schema.

## Graph schema changes

The graph schema is a public contract for any agent that queries it with Cypher:

- New node types, properties, and edge types are **additive** (safe).
- Renaming or removing properties/edges is **breaking** — call it out in the PR and
  in [CHANGELOG.md](CHANGELOG.md).

## Pull requests

1. Branch off `main`.
2. Make the change with tests; keep it focused.
3. Ensure `pytest`, `ruff check`, `ruff format --check`, and `mypy src/` all pass.
4. Describe the change and any schema impact in the PR.

## Reporting bugs and security issues

Open an issue for bugs and feature requests. For security vulnerabilities, please
follow [SECURITY.md](SECURITY.md) instead of opening a public issue.
