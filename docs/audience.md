# Gristle — Target Audience Exercise

*A grounded positioning analysis based on Gristle's actual current capabilities (as of the `[Unreleased]` changelog). No marketing fluff — every claim is tied to a shipped feature.*

---

## 0. What Gristle actually is (the honest one-paragraph baseline)

Gristle parses a repository with tree-sitter into a **FalkorDB graph** where structural relationships — calls, imports, inheritance, routes, data models, tests, docs, config, env vars — are **first-class edges**, then exposes that graph over **MCP** (for AI agents) and a **CLI** (for humans). It is **structural + heuristic** code intelligence: high-coverage, build-free, framework-aware, multi-language in one queryable graph. It now traces **route → handler → function → DB model end-to-end** (across Python/TS/JS, Express/Hono/Fastify/NestJS/FastAPI/Flask/Django + Prisma/Drizzle/TypeORM/SQLAlchemy), surfaces blast-radius/impact, dead code, import cycles, unauthenticated routes, data contracts, error-flow, and renders connected subgraphs (`gristle_subgraph`).

It is explicitly **NOT**:
- a **type-resolved** indexer (SCIP/LSIF, Sourcegraph) — call/import resolution is name- and heuristic-based, now with a `resolution` confidence label per CALLS edge, but still best-effort, not proofs;
- a **dataflow / taint** engine (CodeQL/Glean) — it models structural and framework relationships, not value-level data flow.

The two consumer classes matter **equally**: **AI coding agents** (MCP tools) and **human developers** (CLI + `gristle_subgraph` visualization). That dual-consumer design is itself a differentiator and shapes the whole exercise below.

---

## 1. Primary beneficiaries (who gets the most value, and why)

### Persona A — The AI Coding Agent (and the developer driving it)

**This is the headline audience.** Gristle is "graph-based code intelligence *for AI agents*" by its own first line, and it's the only consumer that is **MCP-native**. An agent (Claude Code, Cursor, Claude Desktop, a custom Agent SDK loop) is the consumer that most needs structured, queryable, *connected* facts about a codebase — because it cannot hold a 200k-line repo in context and vector-chunking destroys the very relationships it needs to reason about a change.

**Jobs-to-be-done:**
- *"Before I edit this, what breaks?"* — `gristle_impact` / `gristle_impact_score` (0–100 blast-radius score + risk level) give the agent a safety check it can run *before* writing a diff. This is the single most valuable agent workflow: it converts "confident hallucination" into "grounded edit."
- *"What tests must I run after this change?"* — `gristle_tests(entity, mode="find")` returns the covering tests (TESTS_FUNCTION edges, depth 1–3). The agent runs exactly those, not the whole suite.
- *"How does this request reach the database?"* — `gristle_trace` + `gristle_subgraph(view="request_trace")` return route → handler → function → `USES_MODEL` → Model in one connected payload. The agent reasons over the whole vertical slice without reading 15 files.
- *"What does this codebase's convention look like before I add code?"* — `gristle_conventions` is the recommended first call; the agent learns where things live and what frameworks are in play before generating anything.
- *"Is this type's contract what I think it is?"* — `gristle_data_contract` / `gristle_type_usage` (now with nested-generic unwrapping: `Promise<UserEntity[]>` → `UserEntity`).

**Tools/views that serve them:** essentially the whole MCP surface, but the load-bearing ones are `gristle_impact(_score)`, `gristle_trace`, `gristle_subgraph`, `gristle_conventions`, `gristle_explore`, `gristle_tests`, `gristle_data_contract`. The **`resolution` confidence on CALLS** is specifically an agent affordance — an agent can weight or discount a heuristic (`dotted`) edge vs. a reliable (`exact`/`import`) one, which is exactly the kind of uncertainty-handling a good agent loop wants.

**Why Gristle specifically:** the connected route→handler→DB graph delivered *as MCP tools* is the differentiated thing. SCIP/Sourcegraph is human-IDE-shaped and type-heavy; CodeQL is a query language for security researchers. Neither is a turnkey MCP server an agent can call mid-task.

---

### Persona B — The developer onboarding to / comprehending an unfamiliar codebase

The human who just inherited a service, joined a team, or is auditing a contractor's repo. They don't need taint analysis; they need a **map**.

**Jobs-to-be-done:**
- *"What is this codebase, in one shot?"* — `gristle_conventions` + the `overview` resource: languages, frameworks, where components/tests/routes live, most-imported (core) files.
- *"Show me the request surface end-to-end."* — `gristle_subgraph(view="request_trace")` renders every route → handler → function → model as a node-link graph (on a real Express+Prisma app: 20 routes + 45 functions + 3 models, 70 edges, in one call). This is the visualization that turns a wall of files into a picture.
- *"What's the public API / what's safe to touch?"* — `gristle_public_api`, `gristle_dead_exports`.
- *"What external services does this thing depend on?"* — `gristle_services` (categorized: db/auth/payments/email/AI/storage/...), `gristle_config(mode="setup_requirements")` for the env-var + config checklist needed to run it.

**Tools/views:** `gristle_conventions`, `gristle_subgraph` (request_trace / call_hierarchy), `gristle_explore`, `gristle_services`, `gristle_config`, `gristle_routes`, `gristle_models`/`gristle_model_detail`.

**Why Gristle:** it's **build-free and fast** — point it at a repo, no compile, no language server warm-up, and you get a queryable + *drawable* model. ctags/tree-sitter-only tools give you symbols but not the connected route→DB picture; full indexers need a build and target an IDE, not a "draw me the request flow" question.

---

### Persona C — The security / API auditor doing a focused structural review

Not a CodeQL replacement — but for the **"obvious structural exposure"** layer, Gristle is fast and broad.

**Jobs-to-be-done:**
- *"Which routes have no auth?"* — `gristle_unauthenticated_routes` / `gristle_security`. Auth detection now spans per-route middleware, handler decorators, app-level `app.use('/path', authMiddleware)`, inline `auth()` calls (Next.js), and **DRF `permission_classes`** on class-based views (join `(:Route)-[:HANDLES]->(:Class)` to read the CBV's posture). The changelog is honest that DRF *global* defaults stay invisible — a deliberate false-positive-avoidance choice that this persona should know about.
- *"Where's the middleware chain on this route?"* — `USES_MIDDLEWARE` edges make the guard chain traversable, not just a string list.
- *"Any hardcoded secrets / eval / SQL-injection / insecure LLM output handling?"* — `gristle_security` (OWASP LLM05 included).
- *"Any dependencies with known CVEs?"* — `gristle_dependency_health` (npm/PyPI + OSV.dev).
- *"Where does error handling exist (or not)?"* — `RAISES`/`CATCHES` edges + `has_error_handling` (the only error-handling signal for TS, since JS/TS catches name no type).

**Tools/views:** `gristle_security`, `gristle_unauthenticated_routes`, `gristle_dependency_health`, plus `gristle_subgraph(view="request_trace")` to *see* which routes touch which models.

**Why Gristle (with the caveat stated):** it answers "what's the attack *surface* and its shape" cheaply and across the whole repo. It does **not** answer "can tainted input reach this sink" — that's CodeQL's job. Position this persona as "first-pass structural audit," not "security proof."

---

## 2. Top use cases (ranked by strength of fit)

1. **AI-agent-assisted refactoring with blast-radius safety.** `gristle_impact_score` (0–100 + risk level) + `gristle_tests(mode="find")` before/after a diff. This is the sharpest, most defensible use case — it directly counters the #1 failure mode of coding agents (editing without knowing downstream impact). Backed by the full call graph + TESTS_FUNCTION edges.

2. **Route → handler → DB tracing (vertical-slice comprehension).** `gristle_subgraph(view="request_trace")` + `USES_MODEL`. This is the *newly* differentiated capability — the `[Unreleased]` work (inline handler synthesis, Django URLconf + transitive ORM bases, TypeORM repository USES_MODEL, dotted-call resolution via receiver type) is what made it actually connect end-to-end on real Express/Hono/NestJS/Django apps (e.g. NestJS USES_MODEL 1→32; Django routes 0→11, USES_MODEL 0→26; Hono route-handler linkage ~0.5%→93.8%). Before this, it was a promise; now it's the product.

3. **Onboarding / codebase comprehension.** `gristle_conventions` → `gristle_subgraph` → `gristle_explore` → `gristle_services`/`gristle_config`. High value, low setup, both humans and agents.

4. **Impact analysis before any change (human or agent).** `gristle_impact` standalone, as a pre-merge / pre-edit gate.

5. **Security & route auditing (first-pass, structural).** `gristle_security` + `gristle_unauthenticated_routes` + `gristle_dependency_health` + DRF `permission_classes` + `USES_MIDDLEWARE`. Strong for surface enumeration; explicitly not taint analysis.

6. **Dead-code & import-cycle cleanup / architectural hygiene.** `gristle_dead_exports`, `gristle_cycles`, `detect_layer_violations`, `gristle_public_api`. Classic structural-graph wins; reliable because they lean on IMPORTS/EXPORTS edges (high-confidence) rather than heuristic CALLS.

7. **Data-contract / type-flow review.** `gristle_data_contract`, `gristle_type_usage` (nested-generic aware). Useful at API boundaries; weaker than a type-resolved tool, so rank it lower.

---

## 3. Secondary audiences (benefit, but not the core)

- **Tech leads / architects** doing periodic health checks: cycles, layer violations, dead exports, dependency staleness. Real value, but episodic rather than daily — they're consumers of the same code-quality tools, not a distinct need.
- **Platform / DX teams building internal "ask-the-codebase" tooling** on top of Gristle as a library (the Python pipeline + query engine are importable) or as a shared remote HTTP MCP server. They're a *distribution channel* more than an end audience.
- **Documentation / DevRel** maintaining accuracy: `gristle_docs(mode="staleness")` flags docs whose code references no longer resolve. Niche but genuinely useful.
- **Code reviewers** wanting `gristle_changelog`-style "what structurally changed since last ingest" deltas. Adjacent to the agent-refactoring case.

---

## 4. Who it's NOT for / where it falls short (be direct)

- **Anyone needing type-exact resolution.** Calls/imports are name- and heuristic-based. The new `resolution` confidence label is honest about this — `dotted` edges can be wrong; heuristic-heavy repos are dominated by them. If you need *guaranteed* "this call binds to exactly that definition" (rename refactors with zero misses, precise go-to-definition across overloads/generics), use a SCIP/LSIF indexer. Gristle's edges are navigation aids, not proofs.
- **Security teams needing dataflow/taint proofs.** No value-level data flow. Gristle tells you a route is unauthenticated or that a model is touched; it cannot prove tainted input reaches a sink. That's CodeQL/Glean territory — don't position against them.
- **Languages/frameworks outside the supported set.** Only Python/TS/JS + Vue/Svelte/Astro SFCs (plus Markdown, config, Prisma/Drizzle). Go, Rust, Java, Ruby, C#, PHP, etc. are simply not represented. Constructs outside the recognized frameworks aren't modeled.
- **Runtime / dynamic behavior.** No dynamic dispatch, monkey-patching, runtime reflection, dynamic attribute assignment, or value-level types beyond annotations. Documented explicitly under "It does NOT track."
- **Teams who want zero infrastructure.** It needs FalkorDB running (Docker). Lightweight relative to a build-based indexer, but not zero-dependency like a single ctags binary.
- **DRF global-auth shops expecting auth flagging to be exhaustive.** By design, global DRF auth defaults are invisible to static analysis, so CBVs aren't flagged as unauthenticated (avoids false positives). Good engineering, but the auditor must know the boundary.

---

## 5. The single sharpest positioning statement

> **Gristle is the connected route→handler→DB code graph that AI coding agents (and the humans driving them) query over MCP — so an agent knows the blast radius and the request-to-database path *before* it writes a diff.** It goes deeper than ctags/tree-sitter-only tools and is lighter and broader than type-resolved indexers, and it's the only one of them designed to be *called by an agent* rather than browsed in an IDE.

(One-line fallback: *"Impact-aware, route-to-database code intelligence, MCP-native for agents."*)

The one thing it does better than alternatives: **delivering a multi-language, framework-aware, end-to-end structural graph as agent-callable tools** — nobody else pairs the connected route→handler→model graph with MCP-native access. Everything else (impact, cycles, dead code) is table-stakes that the graph makes easy; the *connectedness + MCP delivery* is the moat.

---

## 6. How the target audience should drive the new-parser roadmap

The audience choice is the tiebreaker for parser priority. Each candidate format unlocks a *different* persona, so rank by which persona you're betting on.

| Candidate parser | Primarily unlocks | What it adds to the graph | Strategic read |
|---|---|---|---|
| **Vue / Svelte / Astro SFCs** | Persona B (comprehension) + the agent persona, on **frontend-heavy / full-stack JS teams** | Components, routes, props/handlers in the `<script>` blocks — extends the existing React component model to the rest of the JS ecosystem | **Highest leverage for the agent + onboarding audience.** Gristle already does React; SFCs are the obvious gap that excludes a huge slice of the TS/JS market it already targets. Pick this if the bet is "AI agents on modern web apps." |
| **SQL DDL** | Persona C (audit) + Persona B; **backend / data-heavy teams** | `Model`/`ModelField`/relations for the *many* apps using raw SQL / migrations instead of an ORM — completes the "DB" end of route→DB tracing where there's no Prisma/Drizzle/ORM class | **Highest leverage for the route→DB story** specifically. The `USES_MODEL` work assumes an ORM; SQL DDL covers the apps that don't have one. Pick this if route→DB tracing is *the* headline feature. |
| **OpenAPI** | Persona C (API/security audit) + Persona B | First-class API contract → can cross-check declared routes vs. implemented routes, enrich `Route` nodes | Strong for the auditor and for "does the spec match the code." Good fit if API-surface auditing becomes a primary use case. |
| **GraphQL SDL** | Persona B + the agent persona, on **GraphQL-API teams** | Schema types/resolvers as a typed surface analogous to routes | Narrower than REST; valuable but only for the GraphQL slice. Secondary unless the target market is GraphQL-first. |
| **Protobuf / gRPC** | Persona B/C on **microservice / RPC-heavy backends** | Service/message definitions; cross-service contract surface | Most specialized; unlocks the smallest (but high-value, infra-heavy) audience. Lowest priority unless explicitly targeting microservice platforms. |

**Recommended ordering, given the dual primary audience (AI agents + comprehension on modern full-stack web apps):**
1. **Vue/Svelte/Astro SFCs** — ✅ **shipped (2026-06-26)** — closed the biggest gap in the audience Gristle *already* serves (TS/JS web), with the least conceptual stretch (extends React handling).
2. **SQL DDL** — completes the differentiated route→DB story for the (large) non-ORM backend world; directly amplifies the #2 use case. *(Note: needs a SQL parser decision — no `tree_sitter_sql` dep yet — and a dedup strategy since ORM migrations duplicate existing Models.)*
3. **OpenAPI** — turns the auditor persona into a stronger second-tier audience (spec-vs-code). *(Dependency-free: `pyyaml` is already available.)*
4. GraphQL SDL, then Protobuf — narrower audiences; pursue when targeting those specific markets.

**The decision rule:** if the strategic bet is *"AI agents working on full-stack web apps,"* SFCs lead. If the bet is *"the route→database trace is the wedge,"* SQL DDL leads. If the bet is *"structural security/API auditing,"* OpenAPI rises. The parser backlog should be re-ranked the moment that audience bet is made, not before.
