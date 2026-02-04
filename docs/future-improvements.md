# Gristle Future Improvements

This document outlines potential enhancements to Gristle based on how Ziggy's AI agents use the code graph. Ideas are prioritized by impact vs. effort.

**Status Legend:**
- ✅ **COMPLETED** - Implemented and tested
- 🚧 **IN PROGRESS** - Currently being worked on
- 📋 **TODO** - Not yet started

---

## Quick Wins (< 1 week, High Impact)

### 1. Dead Export Detection ✅ **COMPLETED**
**Effort:** ~1 day
**Impact:** High (Pathfinder)
**Completed:** Phase D implementation

**Problem:** Pathfinder reports dead code but misses "exported but never imported" functions — a common source of API bloat in libraries and barrel files.

**Solution:** Query to find exported entities with no corresponding imports from other files.

**Implementation:**
```python
def detect_dead_exports(self) -> dict:
    """Find exported entities that are never imported by other files."""
    query = """
    MATCH (file:File)-[:EXPORTS]->(entity)
    WHERE NOT EXISTS {
        MATCH (other:File)-[:IMPORTS]->(target:File)
        WHERE target.path = file.path
          AND other.path <> file.path
    }
    AND NOT entity.is_entry_point
    RETURN entity.qualified_name AS name,
           entity.name AS short_name,
           file.path AS file,
           labels(entity)[0] AS type
    ORDER BY file.path, entity.name
    """
    result = self.graph.execute(query)
    exports = [
        {
            "qualified_name": r["name"],
            "name": r["short_name"],
            "file": r["file"],
            "type": r["type"],
        }
        for r in result.records
    ]
    return {"total": len(exports), "dead_exports": exports}
```

**MCP Tool:**
```python
@server.call_tool()
async def gristle_dead_exports(repo_id: str) -> list[types.TextContent]:
    """Find exported functions/classes that are never imported."""
    engine = get_engine(repo_id)
    result = engine.detect_dead_exports()
    return format_result(result)
```

**Benefit:** Pathfinder can report: "You export `validateEmail` from `utils/index.ts`, but no file imports it. Consider removing or marking as internal."

**Edge Cases:**
- Exclude `is_entry_point=true` entities (they're meant to be external)
- For library codebases, this flags public API bloat
- For apps, this catches forgotten re-exports in barrel files

---

### 2. Circular Dependency Detection with Path Visualization ✅ **COMPLETED**
**Effort:** ~2 days
**Impact:** High (Pathfinder)
**Completed:** Phase D implementation

**Problem:** Import cycles exist but Gristle doesn't explicitly detect or visualize them. Ziggy has to manually traverse IMPORTS edges to find cycles.

**Solution:** Add `detect_import_cycles()` query that returns cycle paths, not just presence.

**Implementation:**
```python
def detect_import_cycles(self, max_length: int = 10) -> dict:
    """Find all import cycles up to max_length.

    Returns cycles as file path lists, grouped by length.
    """
    query = """
    MATCH path = (a:File)-[:IMPORTS*1..{max_len}]->(a)
    WHERE ALL(r IN relationships(path) WHERE type(r) = 'IMPORTS')
    RETURN [n IN nodes(path) | n.path] AS cycle_files,
           length(path) AS cycle_length
    ORDER BY cycle_length ASC
    """
    result = self.graph.execute(query, {"max_len": max_length})

    cycles = []
    by_length = {}
    for r in result.records:
        files = r["cycle_files"]
        length = r["cycle_length"]
        cycles.append({"files": files, "length": length})
        by_length[length] = by_length.get(length, 0) + 1

    return {
        "total": len(cycles),
        "cycles": cycles,
        "by_length": by_length,
    }
```

**Benefit:** Pathfinder reports: "3 import cycles detected: `auth.ts → user.ts → auth.ts` (length 2), `a.ts → b.ts → c.ts → a.ts` (length 3)."

**Optimization:** For large repos, limit `max_length` to avoid expensive traversals. Start at 5, increase if needed.

---

### 3. Public API Surface Mapping ✅ **COMPLETED**
**Effort:** ~2-3 days
**Impact:** High (Cartographer)
**Completed:** Phase D implementation

**Problem:** For libraries/SDKs, Ziggy needs to document the public API surface, but Gristle doesn't distinguish "public API" from "internal implementation."

**Solution:** Classify exported entities by visibility and create a dedicated query.

**Implementation:**
```python
def get_public_api(self, include_internal: bool = False) -> dict:
    """Return all public API entities (exported, non-test, non-internal).

    Args:
        include_internal: If True, include entities in paths containing 'internal', '__', or '_private'.
    """
    internal_filter = ""
    if not include_internal:
        internal_filter = """
        AND NOT file.path CONTAINS '__'
        AND NOT file.path CONTAINS '/internal/'
        AND NOT file.path CONTAINS '/_'
        """

    query = f"""
    MATCH (file:File)-[:EXPORTS]->(entity)
    WHERE NOT file.is_test_file
      AND entity.visibility = 'public'
      {internal_filter}
    RETURN entity.qualified_name AS name,
           entity.name AS short_name,
           file.path AS file,
           labels(entity)[0] AS type,
           entity.docstring AS doc
    ORDER BY type, entity.name
    """
    result = self.graph.execute(query)

    entities = []
    by_type = {}
    for r in result.records:
        entity = {
            "qualified_name": r["name"],
            "name": r["short_name"],
            "file": r["file"],
            "type": r["type"],
            "has_docs": bool(r["doc"]),
        }
        entities.append(entity)
        by_type[r["type"]] = by_type.get(r["type"], 0) + 1

    return {
        "total": len(entities),
        "entities": entities,
        "by_type": by_type,
    }
```

**Benefit:** Cartographer generates: "Public API: 47 functions, 12 classes across 8 modules. 85% documented."

**Extension:** Add `is_deprecated` flag detection (via `@deprecated` decorator/JSDoc) to flag old API surface.

---

## Short-Term (1-2 weeks, High Value)

### 4. Change Impact Scoring ✅ **COMPLETED**
**Effort:** ~3-4 days
**Impact:** High (Architect)
**Completed:** Latest session

**Problem:** Architect knows *what's* impacted by a change (via `get_impact_analysis`), but can't prioritize — is changing `utils/format.ts` high-risk or low-risk?

**Solution:** Add a **blast radius score** based on transitive dependents + criticality.

**Implementation:**
```python
def score_change_impact(self, entity_id: str) -> dict:
    """Score the blast radius of changing an entity.

    Scoring factors:
    - Direct dependents (1 point each)
    - Transitive dependents via CALLS (0.5 points each, depth 1-2)
    - Entry point dependents (5x multiplier)
    - Test dependents (0.3x multiplier — tests breaking is lower risk)

    Risk levels:
    - 0-10: LOW
    - 11-50: MEDIUM
    - 51+: HIGH
    """
    query = """
    MATCH (entity {id: $entity_id})

    // Direct dependents (imports or calls)
    OPTIONAL MATCH (direct)-[:CALLS|IMPORTS]->(entity)

    // Transitive dependents (depth 1-2)
    OPTIONAL MATCH path = (transitive)-[:CALLS*1..2]->(entity)
    WHERE transitive.id <> entity.id

    WITH entity,
         collect(DISTINCT direct) AS directs,
         collect(DISTINCT transitive) AS transitives

    UNWIND directs AS dep
    RETURN dep.id AS dependent_id,
           dep.qualified_name AS name,
           dep.is_entry_point AS is_entry,
           dep.is_test AS is_test,
           'direct' AS dependency_type

    UNION

    MATCH (entity {id: $entity_id})
    OPTIONAL MATCH path = (transitive)-[:CALLS*1..2]->(entity)
    WHERE transitive.id <> entity.id
    UNWIND nodes(path) AS dep
    WHERE dep.id <> entity.id
    RETURN dep.id AS dependent_id,
           dep.qualified_name AS name,
           dep.is_entry_point AS is_entry,
           dep.is_test AS is_test,
           'transitive' AS dependency_type
    """
    result = self.graph.execute(query, {"entity_id": entity_id})

    score = 0
    dependents = []
    entry_point_count = 0

    for r in result.records:
        weight = 1.0 if r["dependency_type"] == "direct" else 0.5
        if r["is_test"]:
            weight *= 0.3
        if r["is_entry"]:
            weight *= 5.0
            entry_point_count += 1

        score += weight
        dependents.append({
            "name": r["name"],
            "type": r["dependency_type"],
            "is_entry_point": r["is_entry"],
            "is_test": r["is_test"],
        })

    if score <= 10:
        risk = "LOW"
    elif score <= 50:
        risk = "MEDIUM"
    else:
        risk = "HIGH"

    return {
        "score": int(score),
        "risk_level": risk,
        "dependent_count": len(dependents),
        "entry_point_dependents": entry_point_count,
        "dependents": dependents[:20],  # Cap at 20 for readability
    }
```

**Benefit:** Architect reports: "Changing `auth.verifyToken` affects 34 functions (⚠️ HIGH RISK: score 127). This includes 3 entry points."

---

### 5. Type Flow Analysis ✅ **COMPLETED**
**Effort:** ~5-7 days
**Impact:** Very High (Architect)

**Problem:** Ziggy's Architect agent needs to understand data contracts between services/modules, but currently has no visibility into what data shapes flow through the system.

**Solution:** Track TypeScript interfaces/types and Python type hints as first-class nodes, then create `RETURNS` and `ACCEPTS` edges.

**Implementation Plan:**

1. **New Node Types:**
   - `Type` — interfaces, type aliases, dataclasses, Pydantic models
   - `TypeField` — properties/fields of types

2. **New Edges:**
   - `(Function)-[:RETURNS]->(Type)`
   - `(Function)-[:ACCEPTS {param_name}]->(Type)`
   - `(Type)-[:HAS_FIELD]->(TypeField)`
   - `(Type)-[:EXTENDS]->(Type)` — inheritance

3. **Parser Changes:**
   - **TypeScript:** Extract `interface`, `type`, `class` definitions with fields
   - **Python:** Extract dataclasses, Pydantic models, TypedDict

4. **Example Query:**
```python
def get_data_contract(self, func_qualified_name: str) -> dict:
    """Get the input/output data contract for a function."""
    query = """
    MATCH (f:Function {qualified_name: $qn})
    OPTIONAL MATCH (f)-[:RETURNS]->(ret:Type)
    OPTIONAL MATCH (ret)-[:HAS_FIELD]->(ret_field:TypeField)
    OPTIONAL MATCH (f)-[:ACCEPTS]->(param:Type)
    OPTIONAL MATCH (param)-[:HAS_FIELD]->(param_field:TypeField)
    RETURN f.signature AS signature,
           ret.name AS return_type,
           collect(DISTINCT {name: ret_field.name, type: ret_field.type}) AS return_fields,
           collect(DISTINCT {name: param_field.name, type: param_field.type}) AS param_fields
    """
    result = self.graph.execute(query, {"qn": func_qualified_name})
    # ... format result
```

**Benefit:** Architect can:
- Detect breaking changes: "This API changed from returning `User` to `UserDTO`"
- Suggest DTOs for coupling reduction: "Routes shouldn't accept `DatabaseUser`, create a DTO"
- Validate data contracts: "Function expects `{id: string}` but receives `{userId: number}`"

**Challenges:**
- TypeScript generic types (e.g., `Array<User>`, `Promise<Result<T>>`) require recursive parsing
- Python runtime types (e.g., `list[str]`) need special handling
- Inline types (`{name: string}`) vs named types

---

## Medium-Term (3-4 weeks, Specialized Value)

### 6. Security Pattern Detection ✅ **COMPLETED**
**Effort:** ~6-8 days
**Impact:** High (New Use Case for Pathfinder)

**Problem:** Pathfinder flags dead code and test gaps but can't detect security anti-patterns like SQL injection risks, hardcoded secrets, or missing auth checks.

**Solution:** Add AST-based security pattern detection during parsing.

**Patterns to Detect:**

1. **SQL Injection Risk:** String concatenation/f-strings in SQL queries
   ```python
   # RISKY
   query = f"SELECT * FROM users WHERE id = {user_id}"
   # SAFE
   query = "SELECT * FROM users WHERE id = ?"
   ```

2. **Hardcoded Secrets:** String literals matching patterns
   ```typescript
   // RISKY
   const apiKey = "sk_live_abc123def456";
   const password = "admin123";
   ```

3. **Missing Auth:** Route handlers without auth middleware/decorators
   ```python
   # RISKY
   @app.get("/admin/users")
   def list_users():
       ...

   # SAFE
   @app.get("/admin/users")
   @require_auth
   def list_users():
       ...
   ```

4. **Unsafe Deserialization:** `pickle.loads()`, `eval()`, `exec()`

**Implementation:**
- Add `security_issues: list[str]` field to `ParsedFunction`
- Detect patterns during AST walk in parsers
- Create `SecurityIssue` node type with `(SecurityIssue)-[:FOUND_IN]->(Function)` edges

**Query:**
```python
def get_security_issues(self, severity: str | None = None) -> dict:
    """Get all detected security issues."""
    query = """
    MATCH (issue:SecurityIssue)-[:FOUND_IN]->(func:Function)
    WHERE $severity IS NULL OR issue.severity = $severity
    RETURN issue.pattern AS pattern,
           issue.severity AS severity,
           func.qualified_name AS function,
           func.file_path AS file,
           func.start_line AS line
    ORDER BY
        CASE issue.severity
            WHEN 'HIGH' THEN 1
            WHEN 'MEDIUM' THEN 2
            WHEN 'LOW' THEN 3
        END,
        func.file_path
    """
```

**Benefit:** Pathfinder reports: "⚠️ 3 SQL injection risks found in `db/queries.py:42, 67, 102`."

---

### 7. Framework Convention Detection 📋 **TODO**
**Effort:** ~4-6 days per framework
**Impact:** Medium (Cartographer)

**Problem:** Cartographer needs to explain "how does this app work?" but relies on generic graph structure. Framework-specific patterns (Next.js routes, FastAPI dependency injection, Django models) are invisible.

**Solution:** Add framework-specific metadata as node properties during parsing.

**Examples:**

**Next.js:**
- `is_server_component`, `is_client_component` (detect `"use client"` directive)
- `route_type`: page / api / layout / middleware / loading / error
- `dynamic_route`: bool (detect `[param]` in path)

**FastAPI:**
- `dependencies`: list of `Depends()` function names
- `response_model`: extracted from decorator
- `auth_required`: detected via `Depends(get_current_user)` pattern

**Django:**
- `model_fields`: extracted from Model class definitions
- `admin_registered`: detected via `admin.site.register()`

**Implementation:** Tree-sitter pattern matching for framework-specific constructs, add properties to existing nodes.

**Benefit:** Cartographer generates framework-aware documentation: "This Next.js app has 12 server components, 3 client components, 8 API routes, and uses app router with 2 dynamic routes."

---

### 8. Dependency Staleness Tracking ✅ **COMPLETED**
**Effort:** ~3-4 days
**Impact:** Medium (Pathfinder)

**Problem:** Pathfinder sees dependencies but can't tell if they're outdated or vulnerable.

**Solution:** During ingestion, fetch latest versions from npm/PyPI and compare.

**Implementation:**
```python
# In pipeline after extracting dependencies:
import semver
import requests

def fetch_latest_version(name: str, ecosystem: str) -> str | None:
    if ecosystem == "npm":
        resp = requests.get(f"https://registry.npmjs.org/{name}/latest")
        return resp.json()["version"] if resp.ok else None
    elif ecosystem == "pypi":
        resp = requests.get(f"https://pypi.org/pypi/{name}/json")
        return resp.json()["info"]["version"] if resp.ok else None
    return None

for dep in parsed_dependencies:
    latest = fetch_latest_version(dep.name, dep.ecosystem)
    if latest:
        dep.is_outdated = semver.lt(dep.version, latest)
        dep.latest_version = latest
```

**Benefit:** Pathfinder reports: "12 outdated dependencies: `react@17.0.2` (latest: 18.2.0), `pytest@7.0.0` (latest: 8.1.0)."

**Optimization:** Cache results (24h TTL) to avoid repeated API calls.

---

## Nice-to-Have (Lower Priority)

### 9. Mocking Recommendations 📋 **TODO**
**Effort:** ~2-3 days
**Impact:** Low-Medium (Pathfinder)

**Solution:** Detect functions that call external APIs/databases and recommend mocking.

```python
def suggest_test_mocks(self, func_id: str) -> list[str]:
    """Find callees that should be mocked (HTTP, DB, filesystem)."""
    query = """
    MATCH (f:Function {id: $func_id})-[:CALLS]->(callee:Function)
    WHERE callee.name IN ['fetch', 'axios', 'prisma', 'db.query', 'fs.readFile']
       OR callee.qualified_name CONTAINS 'http'
       OR callee.qualified_name CONTAINS 'database'
    RETURN DISTINCT callee.name, callee.qualified_name
    """
```

**Benefit:** Pathfinder suggests: "To test `getUser`, consider mocking: `db.query`, `fetch`."

---

### 10. Changelog Generation from Commits 📋 **TODO**
**Effort:** ~5-7 days
**Impact:** Medium (Cartographer)

**Solution:** If Gristle runs on a git repo, diff the previous graph snapshot against current and generate a structured changelog.

**Implementation:**
- Store graph snapshots with commit hash
- On re-ingest, compare current vs. previous snapshot
- Detect: new functions, removed functions, signature changes, test coverage delta

**Output:**
```markdown
## Changes Since Last Audit (3 days ago)

### New Features (12 functions added)
- `src/api/users.ts::createUser` — Route handler for POST /api/users
- `src/services/email.ts::sendWelcome` — Sends welcome email

### Breaking Changes (2 functions removed)
- `src/api/legacy.ts::oldHandler` — REMOVED

### Test Coverage
- +47 new tests
- Coverage: 73% → 81%
```

**Benefit:** Cartographer can explain what changed between audits.

---

## Summary: Priority Matrix

| Improvement | Effort | Impact | Priority | Status |
|-------------|--------|--------|----------|--------|
| Dead Export Detection | 1 day | High | Immediate | ✅ **DONE** |
| Circular Dependency Detection | 2 days | High | Immediate | ✅ **DONE** |
| Public API Surface Mapping | 2-3 days | High | Immediate | ✅ **DONE** |
| Change Impact Scoring | 3-4 days | High | Short-term | ✅ **DONE** |
| Type Flow Analysis | 5-7 days | Very High | Short-term | ✅ **DONE** |
| Security Pattern Detection | 6-8 days | High (new use case) | Medium-term | ✅ COMPLETED |
| Framework Convention Detection | 4-6 days/framework | Medium | Medium-term | 📋 TODO |
| Dependency Staleness | 3-4 days | Medium | Medium-term | ✅ COMPLETED |
| Mocking Recommendations | 2-3 days | Low-Medium | Nice-to-have | 📋 TODO |
| Changelog Generation | 5-7 days | Medium | Nice-to-have | 📋 TODO |

---

## Completed Features

### ✅ Phase 1: Code Quality (Completed)
1. **Dead Export Detection** - Find exported entities never imported elsewhere
2. **Circular Dependency Detection** - Detect import cycles with path visualization
3. **Public API Surface Mapping** - List all public API entities with documentation percentage

### ✅ Phase 2: Impact Analysis (Completed)
4. **Change Impact Scoring** - Blast radius scoring (0-100) with risk classification

**Results:**
- Added 3 new query methods: `detect_dead_exports()`, `detect_import_cycles()`, `get_public_api()`
- Added 4 new MCP tools: `gristle_dead_exports`, `gristle_cycles`, `gristle_public_api`, `gristle_impact_score`
- Enhanced impact analysis with scoring algorithm
- 791 tests passing

---

### ✅ Phase 3: Graph Depth Improvements (Completed)

Improvements to graph accuracy and depth, verified against live FalkorDB with both Gristle (Python) and Ziggy (TypeScript) repos:

- **Route `has_auth` detection** — checks per-route middleware, handler decorators, and app-level auth middleware (`app.use('/path', authMiddleware)`)
- **Import `resolved` property** — tracks whether each import resolves to an internal file
- **Import-based test edges (JS/TS)** — depth-3 `TESTS_FUNCTION` fallback for test functions that import production files but lack direct call coverage (Ziggy: 104→368 TESTS_FUNCTION edges)
- **Python `__all__` export detection** — functions/classes in `__all__` get `is_exported=True`, creating EXPORTS edges
- **App-level auth middleware** — TS parser detects `app.use('/path', authMiddleware)` patterns, pipeline matches route paths against auth middleware path patterns

**Results:**
- 791 tests passing
- Ziggy test coverage: 3.1% → 6.7% (via import-based test linking)
- Route `has_auth` populated for all routes (was `None`)
- Import resolved: 67.9% for Ziggy, 39.2% for Gristle

---

## Next Steps

**Recommended next priorities:**

1. **Framework Convention Detection** (~4-6 days/framework, Medium Impact)
   - Next.js: server/client components, route types, dynamic routes
   - FastAPI: dependencies, response models, auth patterns
   - Django: model fields, admin registration

2. **Mocking Recommendations** (~2-3 days, Low-Medium Impact)
   - Detect functions calling external APIs/databases
   - Suggest what to mock in tests

3. **Changelog Generation** (~5-7 days, Medium Impact)
   - Diff graph snapshots between commits
   - Generate structured changelogs
