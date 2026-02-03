# Claude Code Instructions

## About the User

**Role**: Solo founder - handling product, design, and development with AI assistance.

**Technical Level**: Highly technical but not a developer by trade. Understands architecture, can read code, debug issues, and make informed technical decisions. Relies on AI tools for implementation.

**What this means**:
- Don't oversimplify - user can handle technical concepts
- Don't assume knowledge of language-specific idioms or framework internals
- Explain the "why" behind architectural decisions
- User may not catch subtle bugs or anti-patterns on their own

---

## Conversation Initialization

**IMPORTANT: Read CONTEXT.md at the start of every conversation.**

[CONTEXT.md](CONTEXT.md) tells you:
- What's been built (parsers, pipeline, MCP tools)
- Non-negotiable architecture rules
- Current focus and upcoming work
- How Gristle integrates with Ziggy

This prevents:
- Suggesting features that already exist
- Violating architecture decisions
- Wasting time rediscovering what's built

**After reading CONTEXT.md:**
- Check recent commits for latest changes (already in git context)
- Read [ARCHITECTURE.md](ARCHITECTURE.md) before touching the ingestion pipeline or parsers
- Read [docs/integration-guide.md](docs/integration-guide.md) for the MCP tool reference and graph schema
- Read [docs/ziggy-integration.md](docs/ziggy-integration.md) before any change that affects the graph schema or MCP tools

---

## Communication Preferences

### Explanations
- **Routine tasks**: Brief context, then execute
- **New concepts**: Teach - explain what's happening and why
- **Complex decisions**: Walk through tradeoffs before implementing
- When introducing something new (pattern, library, technique), explain it

### Decision Making
- **Big decisions** (architecture, new dependencies, significant refactors): Ask first, present options
- **Small decisions** (implementation details, naming, minor refactors): Use best judgment
- **When uncertain**: Default to asking

### Tone
- Direct and efficient
- No unnecessary praise or caveats
- Technical accuracy over politeness

---

## Code Quality Standards

### Readability First
- Code should be self-documenting through clear naming
- Prefer explicit over clever
- Break complex logic into well-named functions

### Simplicity
- Solve the actual problem, not hypothetical future ones
- Avoid over-engineering and premature abstraction
- Three similar lines of code is often better than a premature abstraction
- Don't add features, options, or flexibility that wasn't requested

### Documentation
- Document the "why", not the "what"
- Update relevant docs when making significant changes
- Type hints serve as documentation - keep them accurate

### Clean Code
- Single responsibility - functions do one thing
- DRY, but not at the cost of readability
- Consistent patterns across the codebase
- Remove dead code, don't comment it out

---

## Working Style

### Before Making Changes
- Read the relevant code first - understand before modifying
- Check for existing patterns in the codebase and follow them
- Look for related documentation

### Understand Before Fixing
- Research first, implement second
- Map the full flow - trace data through the entire pipeline
- Don't assume - read the code rather than guessing
- Present findings before recommending fixes

### Following Specs
- Always follow specs exactly - implement faithfully
- No silent deviations - don't leave things out or adjust without discussing
- Ask before diverging - raise concerns before implementing differently
- Spec is source of truth

### Implementation Discipline

**Before writing ANY code:**
1. Read the entire spec - not skimming
2. Verify against all cases
3. Match scope to placement - decide if feature is shared or specific

**During implementation:**
4. When reality diverges from expectation, STOP and ASK
5. Follow the spec's implementation details exactly

**Meta-rule:** Slow down at the start, speed up during execution.

### Implementation Approach
- Prefer editing existing files over creating new ones
- Match existing code style and patterns
- Keep changes focused - don't refactor adjacent code unless asked
- Test changes work before considering done

### Confidence Before Release
- Never release code you're not confident in
- Verify assumptions - confirm fields/properties/APIs exist
- Don't guess at data shapes - check actual types
- Flag uncertainties proactively
- Investigate, don't speculate

### Dependencies
- Prefer established, well-maintained libraries
- Ask before adding new dependencies

### Git & Commits
- Only commit when explicitly asked
- Write clear commit messages
- Don't amend commits that have been pushed

---

## Automation & Tooling

### Proactive Automation
- Run commands when possible (pip install, pytest, ruff, etc.)
- Minimize manual steps - only leave truly interactive tasks
- Suggest tool expansion when hitting limitations

### What to Automate
- Dependency installation and builds
- Running tests and linters
- Docker operations (when safe)

### What Stays Manual
- Initial authentication/login flows
- Setting secrets
- Destructive operations without explicit confirmation
- Deployments to production

---

## Model Split Strategy (Opus + Haiku)

For multi-file implementations (5+ files), split work between models.

### Opus Tasks (Complex Logic)
- Parser logic (tree-sitter AST traversal)
- Call resolution strategy changes
- Pipeline phase modifications
- Ingestion edge-case handling
- Graph schema evolution

### Haiku Tasks (Mechanical/Pattern-Following)
- Adding new node properties (follow existing patterns)
- New MCP tool wrappers (follow existing tool structure)
- Test scaffolding (follow existing test patterns)
- Config additions (follow Pydantic settings pattern)

### Keys to Success
1. Give Haiku explicit patterns to follow
2. Run multiple Haiku agents concurrently for independent tasks
3. Opus handles integration after Haiku scaffolds
4. Verify Haiku output - check imports and references

---

## Red Flags to Avoid

- Over-engineering simple solutions as long as the simple solution doesn't equate to just being lazy
- Leaving debugging code or print statements
- Implementing features that weren't requested
- Making changes outside the scope of what was asked
- Skipping error handling for edge cases
- Inventing workarounds when data doesn't match expectations
- Putting shared functionality in specific modules
- Starting to code before reading the full spec

---

## Gristle Architecture Rules

### Non-Negotiable Decisions

| Component | Required | NOT Allowed |
|-----------|----------|-------------|
| Graph DB | FalkorDB for all graph data | No SQL databases, no other graph DBs |
| AST parsing | tree-sitter | No regex-based parsing for Python/TS/JS (Markdown is the exception) |
| Protocol | MCP (Model Context Protocol) | No custom REST APIs for tool exposure |
| Config | Pydantic Settings with `GRISTLE_` prefix | No hardcoded config, no YAML config files |
| Batching | `UNWIND`-based bulk writes via BatchCollector | No individual node/edge creation in pipeline loops |

### Graph Schema Changes

**Critical:** Any change to Gristle's graph schema (new node types, new properties, new edge types) directly affects Ziggy's agents. Before modifying the schema:

1. Read [docs/ziggy-integration.md](docs/ziggy-integration.md) to understand what Ziggy queries
2. New nodes/properties are additive (safe) - Ziggy agents can opt in
3. Renaming or removing properties is **breaking** - Ziggy's Cypher queries will fail
4. New edge types are additive (safe) - existing queries won't break
5. Document all schema changes in ARCHITECTURE.md and docs/integration-guide.md

### Pipeline Invariants

The three-phase ingestion order is load-bearing:
1. **Phase 1** creates nodes - edges need nodes to exist first
2. **Phase 2** resolves edges - requires in-memory maps from Phase 1
3. **Phase 3** processes docs - references code entities from Phases 1-2

Don't merge phases or reorder them. The `BatchCollector` flush at the end of each phase ensures nodes are committed before edges reference them.

### Per-Repo Graph Isolation

Each repository gets its own FalkorDB graph namespace: `gristle_{sanitized_repo_id}`. This is critical for:
- Multi-tenant safety (Ziggy runs graphs for many apps)
- Clean lifecycle (drop one graph without affecting others)
- Query scope (queries only scan the target repo's graph)

Never write cross-graph queries. Never use a shared graph for multiple repos.

### Testing

- All tests use mock graph clients - no FalkorDB dependency for CI
- Test parsers with real code snippets in fixture files
- Test pipeline phases independently where possible
- Run `pytest` to verify, `ruff check src/ tests/` to lint, `ruff format src/ tests/` to format

```bash
pytest                    # run all tests
ruff check src/ tests/    # lint
ruff format src/ tests/   # format
mypy src/                 # type check
```

### Project Structure

```
src/gristle/
  config.py                # Pydantic settings (GRISTLE_ prefix)
  models.py                # Parsed data models (dataclasses)
  graph/
    client.py              # FalkorDB wrapper, per-repo isolation
    schema.py              # Index creation (26 property + 2 full-text)
  parsers/
    base.py                # Abstract LanguageParser
    registry.py            # Extension-based dispatch
    python.py              # Python parser (tree-sitter)
    typescript.py          # TypeScript + JavaScript parser (tree-sitter)
    markdown.py            # Markdown parser (regex)
  ingestion/
    walker.py              # .gitignore-aware file discovery
    pipeline.py            # Three-phase graph builder (~1700 lines)
    batch.py               # BatchCollector for UNWIND writes
    watcher.py             # Incremental file watching
  query/
    engine.py              # 15+ Cypher query templates
  search/
    embeddings.py          # Optional semantic search
  logging.py               # Structured logging (JSON/text)
  mcp/
    server.py              # MCP server (18 tools + 2 resources)
    auth.py                # Bearer token auth

tests/                     # 520+ tests, mock graph clients
```
