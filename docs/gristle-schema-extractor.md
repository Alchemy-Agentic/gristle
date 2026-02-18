# Gristle Schema Extractor — Implementation Spec

> **Goal:** Add first-class database schema parsing to Gristle so that `Model`, `ModelField`, and `ModelRelation` nodes appear in the code graph alongside existing `Function`, `Class`, `Route`, etc. nodes.
> **Audience:** Claude Code working in the `D:\projects\gristle` repo
> **Why this exists:** Ziggy's Domain Research Phase (Phase 3 onboarding) needs to understand an app's data model to generate workflow intelligence, context schemas, and graph-based trigger recommendations. Currently Gristle parses code *structure* but not data *schema* — so Ziggy has to guess at models from TypeField/Class heuristics. This spec adds definitive schema intelligence.
> **Consumer:** Ziggy queries these nodes via direct Cypher against `gristle_{appId}` graphs. See [domain-research-phase-plan.md](domain-research-phase-plan.md) Task 9.7.

---

## Table of Contents

1. [Architecture Decision](#architecture-decision)
2. [New Graph Nodes](#new-graph-nodes)
3. [Detection Strategy](#detection-strategy)
4. [Task Breakdown](#task-breakdown)
5. [Prisma Parser Detail](#prisma-parser-detail)
6. [Drizzle Extractor Detail](#drizzle-extractor-detail)
7. [ORM Class Promoter Detail](#orm-class-promoter-detail)
8. [Ziggy-Side Changes](#ziggy-side-changes)
9. [Testing Strategy](#testing-strategy)
10. [Execution Order](#execution-order)

---

## Architecture Decision

### Why a post-processing phase, not new parsers

Gristle's parsers (`PythonParser`, `TypeScriptParser`, `JavaScriptParser`) are **language-level** — they use tree-sitter to extract structural code entities from any file in that language. Schema detection is **framework-level** — it requires recognizing that a class inheriting `BaseModel` (SQLAlchemy) or a `.prisma` file follows a specific ORM convention.

Additionally, Gristle already extracts 80% of the raw data we need:

| What we need | What Gristle already has |
|---|---|
| Model names | `Class` nodes with `kind: "class"/"interface"` |
| Model fields + types | `TypeField` nodes linked via `HAS_FIELD` edges |
| Class inheritance (STI) | `INHERITS_FROM` edges between Class nodes |
| Enums | `Class` nodes with `kind: "enum"` + enum member `TypeField` nodes |
| Type annotations | `TypeField.type_annotation` property |

What's **missing** is the semantic layer — knowing that `User extends BaseEntity` is a *data model* and that `userId: string` is a *foreign key* to the `User` model.

### Where it fits in the pipeline

```
Phase 1: Parse files → File, Function, Class, TypeField, Route nodes
Phase 2: Resolve cross-file calls, imports, inheritance, PASSED_TO
  ↕ INHERITS_FROM edges now exist
Config Phase: EnvVar nodes, USES_ENV edges
Schema Phase: NEW — Model, ModelField, ModelRelation nodes    ← THIS SPEC
Phase 3: Documentation
```

The schema phase runs **after Phase 2** (needs `INHERITS_FROM` edges for ORM base class detection) and **after Config Phase** (some schema info comes from config files). It reads existing Class/TypeField nodes from the graph + raw file content for DSL parsing (Prisma).

### What it does NOT do

- **Does not replace parsers.** Existing Class/TypeField extraction stays. The schema extractor *promotes* certain classes to Model status.
- **Does not parse SQL DDL.** We're targeting ORM definitions in application code, not raw SQL migrations. (Migration files are usually in `.sql` or generated code — low signal, high noise.)
- **Does not detect runtime query patterns.** Things like `supabase.from('players').select('*')` reveal table names but not schema structure. That's a future enhancement.

---

## New Graph Nodes

### Model

Represents a database table, collection, or document type as defined by an ORM/schema DSL.

```
(:Model {
  id: "model::{file_path}::{name}",
  name: "Player",                        // Model/table name
  qualified_name: "src/schema.prisma::Player",
  file_path: "src/schema.prisma",
  line_start: 42,
  line_end: 58,
  orm: "prisma",                         // "prisma" | "drizzle" | "mongoose" | "typeorm" | "sequelize" | "sqlalchemy" | "django" | "supabase"
  table_name: "players",                 // Actual DB table name (if different from model name)
  is_junction: false,                    // True for many-to-many join tables
  is_enum: false,                        // True for enum definitions (filter with WHERE NOT m.is_enum)
  primary_key: "id",                     // PK field name(s), comma-separated if composite
  field_count: 12,                       // Number of fields
  docstring: null                        // Model-level comment/docstring
})
```

**Edges:**
- `(:File)-[:CONTAINS]->(:Model)` — file containing the model definition
- `(:Model)-[:HAS_MODEL_FIELD]->(:ModelField)` — model's fields
- `(:Model)-[:PROMOTED_FROM]->(:Class)` — link back to the Class node it was derived from (when applicable — not for Prisma DSL)
- `(:Model)-[:RELATED_TO {relation_type, through_model}]->(:Model)` — high-level relationship

### ModelField

Represents a column, field, or property in a database model.

```
(:ModelField {
  id: "mf::{model_id}::{name}",
  name: "teamId",
  field_type: "string",                  // Application-level type (string, number, boolean, Date, etc.)
  db_type: "uuid",                       // Database-level type if specified (uuid, varchar(255), text, etc.) — null if not explicit
  is_primary_key: false,
  is_nullable: true,
  is_unique: false,
  is_indexed: false,                     // True if @index, @unique, or similar
  has_default: true,
  default_value: "uuid()",               // String representation of default
  is_foreign_key: true,
  references_model: "Team",              // Model name this FK points to (null if not FK)
  references_field: "id",               // Field in referenced model (null if not FK)
  line: 47                               // Line number in source
})
```

**Edges:**
- `(:ModelField)-[:REFERENCES]->(:Model)` — FK relationship (only when `is_foreign_key: true`)

### ModelRelation

Represents a high-level relationship between two models, derived from FK analysis and ORM relation decorators.

This is NOT a separate node — it's an edge property on `(:Model)-[:RELATED_TO]->(:Model)`:

```
(:Model {name: "Player"})-[:RELATED_TO {
  relation_type: "many-to-one",          // "one-to-one" | "one-to-many" | "many-to-one" | "many-to-many"
  foreign_key_field: "teamId",           // The FK field on the source model
  through_model: null,                   // Junction table name for many-to-many
  source_field: null,                    // Explicit relation field name in ORM (e.g., Prisma @relation)
  orm_hint: "prisma_relation"            // How this was detected
}]->(:Model {name: "Team"})
```

### Graph Schema Indexes

Add to `graph/schema.py`:

```python
("Model", "id"),
("Model", "name"),
("Model", "file_path"),
("Model", "orm"),
("ModelField", "id"),
("ModelField", "name"),
```

---

## Detection Strategy

### Framework Priority

| Priority | Framework | Language | Detection Method | Prevalence |
|----------|-----------|----------|-----------------|------------|
| **P0** | Prisma | TS/JS | DSL file parser (`.prisma`) | ~40% of Node.js apps |
| **P0** | Drizzle | TS | `pgTable`/`mysqlTable`/`sqliteTable` call pattern | Growing fast, modern stack |
| **P1** | Mongoose | TS/JS | `new Schema({})` / `mongoose.model()` | ~25% of Node.js apps |
| **P1** | TypeORM | TS | `@Entity()` + `@Column()` decorators on Class | Enterprise TS |
| **P1** | SQLAlchemy | Python | Class inheriting `Base` (declarative) | ~60% of Python apps |
| **P2** | Django | Python | Class inheriting `models.Model` | ~30% of Python apps |
| **P2** | Sequelize | TS/JS | `sequelize.define()` / `Model.init()` | Legacy, declining |

**This spec implements P0 (Prisma + Drizzle) and the ORM class promoter framework that P1/P2 plug into.**

### Three Detection Approaches

**1. DSL File Parser (Prisma)**
Prisma uses its own `.prisma` schema DSL — not TypeScript. This requires a dedicated regex-based parser (not tree-sitter, since there's no tree-sitter grammar for Prisma that's production-ready). The parser reads the `.prisma` file directly and creates `Model` + `ModelField` nodes without going through the existing `ParsedFile` pipeline.

**2. Call Pattern Extractor (Drizzle)**
Drizzle defines schemas as function calls: `export const users = pgTable('users', { ... })`. The TypeScript parser already extracts these as `Function` nodes (variable declarations with `pgTable` calls). The schema extractor reads the raw file content, finds `pgTable`/`mysqlTable`/`sqliteTable` calls via regex, and extracts the column definitions from the object literal.

**3. ORM Class Promoter (TypeORM, SQLAlchemy, Django, Mongoose, Sequelize)**
These ORMs define models as classes (with decorators or base class inheritance). Gristle already creates `Class` nodes with `INHERITS_FROM` edges and `TypeField` nodes via `HAS_FIELD`. The promoter queries the graph for Class nodes that match ORM patterns, then creates `Model` nodes linked back to the Class via `PROMOTED_FROM`.

---

## Task Breakdown

### Task 1: Add Model data models

**File:** `src/gristle/models.py`

Add four new dataclasses:

```python
@dataclass(slots=True)
class ParsedModel:
    """A database model/table definition detected from ORM or schema DSL."""
    name: str
    qualified_name: str
    file_path: str
    line_start: int
    line_end: int
    orm: str  # "prisma" | "drizzle" | "mongoose" | "typeorm" | "sqlalchemy" | "django" | "sequelize"
    table_name: str | None = None  # Explicit table name override (null = inferred from model name)
    primary_key: str | None = None  # PK field name(s)
    is_junction: bool = False
    is_enum: bool = False  # True for enum definitions (Prisma enums, TS enums, etc.)
    docstring: str | None = None
    fields: list[ParsedModelField] = field(default_factory=list)
    relations: list[ParsedModelRelation] = field(default_factory=list)
    source_class_qualified_name: str | None = None  # For ORM class promoter: links back to Class node


@dataclass(slots=True)
class ParsedModelField:
    """A field/column in a database model."""
    name: str
    field_type: str  # Application type: "string", "number", "boolean", "Date", etc.
    db_type: str | None = None  # DB type if explicit: "uuid", "varchar(255)", "text"
    is_primary_key: bool = False
    is_nullable: bool = True  # Default to nullable; ORMs override
    is_unique: bool = False
    is_indexed: bool = False
    has_default: bool = False
    default_value: str | None = None
    is_foreign_key: bool = False
    references_model: str | None = None  # FK target model name
    references_field: str | None = None  # FK target field (usually "id")
    line: int = 0


@dataclass(slots=True)
class ParsedModelRelation:
    """A relationship between two models."""
    target_model: str  # Name of the related model
    relation_type: str  # "one-to-one" | "one-to-many" | "many-to-one" | "many-to-many"
    foreign_key_field: str | None = None  # FK field on this model (for many-to-one)
    through_model: str | None = None  # Junction table (for many-to-many)
    source_field: str | None = None  # ORM relation field name (e.g., Prisma relation field)
    orm_hint: str = ""  # How detected: "prisma_relation", "fk_inference", "decorator"


@dataclass(slots=True)
class SchemaExtractionResult:
    """Result of schema extraction phase."""
    models_found: int = 0
    fields_found: int = 0
    relations_found: int = 0
    nodes_created: int = 0
    relationships_created: int = 0
```

**Acceptance:** Dataclasses importable. No circular imports.

---

### Task 2: Add Prisma schema parser

**File:** `src/gristle/parsers/prisma.py` (new)

This is a standalone parser for `.prisma` files. It does NOT extend `LanguageParser` (Prisma is a DSL, not a programming language). It's called directly by the schema extraction phase.

**Input:** File path + content string for any file ending in `.prisma`
**Output:** `list[ParsedModel]`

**What to parse:**

```prisma
model Player {
  id        String   @id @default(uuid())
  name      String
  teamId    String
  team      Team     @relation(fields: [teamId], references: [id])
  createdAt DateTime @default(now())

  @@index([teamId])
  @@map("players")
}

enum Position {
  ATTACK
  MIDFIELD
  DEFENSE
  GOALIE
}
```

**Error handling:** If a `.prisma` file has syntax errors, the parser should return a partial result (models parsed before the error) and log a warning, rather than raising an exception that fails the entire pipeline. Wrap each model block parse in a try/except and skip malformed blocks.

**Parsing strategy — regex-based, NOT tree-sitter:**

Prisma's syntax is regular enough for regex:

1. **Split into blocks:** Use brace-counting to extract top-level `model`/`enum` blocks rather than `[^}]*` (which fails if a field default contains `}`). Scan for `^(model|enum)\s+(\w+)\s*\{` at line start, then count `{`/`}` to find the matching close brace. This is more robust than `re.compile(r'(model|enum)\s+(\w+)\s*\{([^}]*)\}')`.

   **Note on `type` blocks:** Prisma also has `type` blocks (composite types for embedded documents). These are NOT database tables. For P0, skip `type` blocks entirely — only match `model` and `enum`. Document this as a known limitation for MongoDB-style composite types.
2. **For each model block:**
   a. Extract fields: Each non-empty line that doesn't start with `@@` is a field
   b. Parse field: `(\w+)\s+(\w+)(\??)\s*(.*)` → name, type, optional, attributes
   c. Parse attributes: `@id`, `@default(...)`, `@unique`, `@relation(fields: [...], references: [...])`, `@map("...")`, `@db.Uuid`, etc.
   d. Parse model-level attributes: `@@index([...])`, `@@unique([...])`, `@@map("...")`
3. **Two-pass logic for model-level attributes:** After parsing all fields in a model block, apply `@@index`, `@@unique`, and `@@id` back to the field objects:
   - `@@index([teamId])` → set `is_indexed: true` on the `teamId` `ParsedModelField`
   - `@@unique([email, tenantId])` → set `is_unique: true` on both fields (note: this is a composite unique, which is slightly different from per-field `@unique`, but marking both fields is a reasonable P0 approximation)
   - `@@id([fieldA, fieldB])` → set `is_primary_key: true` on both fields + set `primary_key: "fieldA,fieldB"` on the model

4. **For each enum block:**
   a. Create a `ParsedModel` with `orm: "prisma"`, `is_enum: true`, and each member as a `ParsedModelField` with `field_type: "enum_member"`
   b. This is an alternative to the existing Class node with `kind: "enum"` — but since Prisma enums aren't TypeScript code, they don't get parsed by the TS parser
   c. The `is_enum` flag lets queries filter: `MATCH (m:Model) WHERE NOT m.is_enum` to get only table models

**FK and relation detection:**

```prisma
teamId    String                                    // ← This is the FK field
team      Team     @relation(fields: [teamId], references: [id])   // ← This is the relation
```

When we see `@relation(fields: [X], references: [Y])`:
- Mark the field named `X` as `is_foreign_key: true, references_model: "Team", references_field: "Y"`
- Create a `ParsedModelRelation` with `target_model: "Team", relation_type: "many-to-one", foreign_key_field: "X"`
- The relation field itself (`team`) is NOT a DB column — it's a virtual relation. Don't create a `ModelField` for it.

**Relation type inference:**

| Prisma pattern | Relation type |
|---|---|
| `field Team @relation(fields: [teamId], references: [id])` (scalar FK on this model) | `many-to-one` |
| `field Team @relation(fields: [teamId], references: [id])` + `@unique` on FK | `one-to-one` |
| `field Player[]` (no FK on this model) | `one-to-many` (inverse side) |
| Two models both referencing a third model with compound FKs | `many-to-many` (junction) |

**Handle edge cases:**
- Multi-field `@@id([fieldA, fieldB])` → `primary_key: "fieldA,fieldB"`
- `@@map("table_name")` → `table_name` property
- `@map("column_name")` → store as `db_column_name` on the field (informational)
- `@db.VarChar(255)` → `db_type: "varchar(255)"`
- Comments (`//`, `///`) → `///` comments above a model are docstrings
- `@ignore` attribute → skip the field
- `@@ignore` attribute → skip the model entirely

**Acceptance:** Parses real-world Prisma schemas (test with LaxFlow's `schema.prisma` if available, else use the example above). Produces correct `ParsedModel` with FK detection and relation inference. Handles multiline `@relation()`. Handles enums.

---

### Task 3: Add Drizzle schema extractor

**File:** `src/gristle/parsers/drizzle.py` (new)

Drizzle defines tables as function calls in TypeScript:

```typescript
import { pgTable, varchar, uuid, timestamp, boolean, index } from 'drizzle-orm/pg-core';

export const players = pgTable('players', {
  id: uuid('id').primaryKey().defaultRandom(),
  name: varchar('name', { length: 255 }).notNull(),
  teamId: uuid('team_id').notNull().references(() => teams.id),
  isActive: boolean('is_active').default(true),
  createdAt: timestamp('created_at').defaultNow(),
}, (table) => ({
  teamIdx: index('team_idx').on(table.teamId),
}));
```

**Input:** File path + content string for TypeScript files that import from `drizzle-orm`
**Output:** `list[ParsedModel]`

**Detection:** A file is a Drizzle schema file if it imports from `drizzle-orm/*-core` (pg-core, mysql-core, sqlite-core) AND contains calls to `pgTable`/`mysqlTable`/`sqliteTable`.

**Parsing strategy — regex on raw content:**

1. **Detect table definitions:** Match `(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*(pgTable|mysqlTable|sqliteTable)\(\s*['"](\w+)['"]` → variable name, dialect, table name
2. **Extract column block:** From the opening `{` after table name string to the matching closing `}` (brace counting)
3. **Parse each column:** `(\w+)\s*:\s*(\w+)\(([^)]*)\)(.*)` → field name, type function, type args, chained methods
4. **Parse chained methods:**
   - `.primaryKey()` → `is_primary_key: true`
   - `.notNull()` → `is_nullable: false`
   - `.unique()` → `is_unique: true`
   - `.default(...)` / `.defaultRandom()` / `.defaultNow()` → `has_default: true, default_value: ...`
   - `.references(() => TABLE.FIELD)` → `is_foreign_key: true, references_model: TABLE, references_field: FIELD`
5. **Parse index block** (optional third argument to `pgTable`): Extract index definitions

**Type mapping (Drizzle type function → field_type):**

| Drizzle | field_type | db_type |
|---------|-----------|---------|
| `uuid` | `string` | `uuid` |
| `varchar` | `string` | `varchar(N)` |
| `text` | `string` | `text` |
| `integer` | `number` | `integer` |
| `serial` | `number` | `serial` |
| `bigint` | `number` | `bigint` |
| `boolean` | `boolean` | `boolean` |
| `timestamp` | `Date` | `timestamp` |
| `date` | `Date` | `date` |
| `json` / `jsonb` | `object` | `json`/`jsonb` |
| `real` / `doublePrecision` | `number` | `real`/`double precision` |
| `numeric` / `decimal` | `number` | `numeric` |

**Relation detection:**

Drizzle uses a separate `relations()` call for explicit relations:
```typescript
export const playersRelations = relations(players, ({ one, many }) => ({
  team: one(teams, { fields: [players.teamId], references: [teams.id] }),
  assessments: many(assessments),
}));
```

If present, parse it for richer relation metadata. But the `.references(() => teams.id)` on the column itself is sufficient for FK detection — the `relations()` call is optional enhancement.

**Acceptance:** Parses Drizzle `pgTable` definitions. Detects FK via `.references()`. Maps types. Handles multi-column indexes. Gracefully ignores files that import drizzle-orm but don't define tables.

---

### Task 4: Add ORM class promoter framework

**File:** `src/gristle/ingestion/schema_extractor.py` (new)

This is the main schema extraction orchestrator. It runs after Phase 2 and:
1. Calls the Prisma parser on `.prisma` files
2. Calls the Drizzle extractor on Drizzle schema files
3. Runs the ORM class promoter on existing Class nodes in the graph
4. Writes all `Model`, `ModelField` nodes and `RELATED_TO` / `REFERENCES` edges

**Important:** `WalkedFile` does NOT carry file content — it only has `relative_path`, `absolute_path`, and `extension`. The extractor reads file content via `Path(wf.absolute_path).read_text()`, matching the pattern used in `_parse_and_build`.

```python
class SchemaExtractor:
    """Post-Phase 2 processor that creates Model/ModelField/ModelRelation nodes."""

    def __init__(self, graph: GraphClient, file_path_to_id: dict[str, str]):
        """
        Args:
            graph: The graph client for this repo.
            file_path_to_id: Map of relative file paths to File node IDs,
                passed from the pipeline's internal maps to avoid N+1 graph queries.
        """
        self.graph = graph
        self._file_path_to_id = file_path_to_id

    def extract(
        self,
        walked_files: list[WalkedFile],
    ) -> SchemaExtractionResult:
        """Run all schema detection strategies and write to graph."""
        models: list[ParsedModel] = []

        # 1. Prisma DSL parsing
        for wf in walked_files:
            if wf.extension == "prisma":
                content = self._read_file(wf)
                if content is not None:
                    from gristle.parsers.prisma import parse_prisma_schema
                    models.extend(parse_prisma_schema(wf.relative_path, content))

        # 2. Drizzle extraction (check .ts/.js files for drizzle-orm imports)
        for wf in walked_files:
            if wf.extension in ("ts", "js", "mts", "mjs"):
                content = self._read_file(wf)
                if content is not None:
                    from gristle.parsers.drizzle import is_drizzle_schema, parse_drizzle_schema
                    if is_drizzle_schema(content):
                        models.extend(parse_drizzle_schema(wf.relative_path, content))

        # 3. ORM class promotion (future: TypeORM, SQLAlchemy, Django, etc.)
        models.extend(self._promote_orm_classes())

        # 4. Write to graph
        return self._write_models(models)

    @staticmethod
    def _read_file(wf: WalkedFile) -> str | None:
        """Read file content, returning None on error (matches _parse_and_build pattern)."""
        try:
            return Path(wf.absolute_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Schema extractor: cannot read %s", wf.relative_path)
            return None
```

**ORM class promoter (P1/P2 — stub for now, framework only):**

The promoter queries the graph for Class nodes whose `bases` match known ORM patterns:

```python
_ORM_BASE_PATTERNS: dict[str, list[str]] = {
    "typeorm": ["BaseEntity", "Entity"],           # + has @Entity() decorator
    "sqlalchemy": ["Base", "DeclarativeBase"],     # + declarative_base() pattern
    "django": ["models.Model", "Model"],           # in models.py files
    "mongoose": [],                                 # detected by Schema() call, not inheritance
    "sequelize": ["Model"],                        # + sequelize.define() pattern
}
```

For P0, this method returns an empty list. The framework is in place for P1 to fill in.

**Graph write logic (`_write_models`):**

```python
def _write_models(self, models: list[ParsedModel]) -> SchemaExtractionResult:
    batch = BatchCollector(self.graph, settings.ingestion_batch_size)
    model_name_to_id: dict[str, str] = {}

    # Phase A: Create Model nodes
    for m in models:
        model_id = f"model::{m.file_path}::{m.name}"
        model_name_to_id[m.name] = model_id
        batch.add_node("Model", {
            "id": model_id,
            "name": m.name,
            "qualified_name": m.qualified_name,
            "file_path": m.file_path,
            "line_start": m.line_start,
            "line_end": m.line_end,
            "orm": m.orm,
            "table_name": m.table_name or self._infer_table_name(m.name),
            "is_junction": m.is_junction,
            "is_enum": m.is_enum,
            "primary_key": m.primary_key,
            "field_count": len(m.fields),
            "docstring": m.docstring,
        })

        # File containment edge (use the file_path_to_id map passed from pipeline)
        file_id = self._file_path_to_id.get(m.file_path)
        if not file_id:
            # Prisma files won't have File nodes from Phase 1 (no parser registered).
            # Create a minimal File node for them so CONTAINS edges have a source.
            file_id = f"file::{m.file_path}"
            batch.add_node("File", {
                "id": file_id,
                "path": m.file_path,
                "language": "prisma",
                "line_count": 0,  # Could be computed from content if needed
            })
            self._file_path_to_id[m.file_path] = file_id
        batch.add_relationship("CONTAINS", file_id, model_id)

        # Link back to Class node (for ORM class promoter)
        if m.source_class_qualified_name:
            # Graph query fallback — class IDs follow "class::{qualified_name}" pattern
            class_id = f"class::{m.source_class_qualified_name}"
            batch.add_relationship("PROMOTED_FROM", model_id, class_id)

    # Phase B: Create ModelField nodes + REFERENCES edges
    for m in models:
        model_id = model_name_to_id.get(m.name)
        if not model_id:
            continue
        for f in m.fields:
            field_id = f"mf::{model_id}::{f.name}"
            batch.add_node("ModelField", {
                "id": field_id,
                "name": f.name,
                "field_type": f.field_type,
                "db_type": f.db_type,
                "is_primary_key": f.is_primary_key,
                "is_nullable": f.is_nullable,
                "is_unique": f.is_unique,
                "is_indexed": f.is_indexed,
                "has_default": f.has_default,
                "default_value": f.default_value,
                "is_foreign_key": f.is_foreign_key,
                "references_model": f.references_model,
                "references_field": f.references_field,
                "line": f.line,
            })
            batch.add_relationship("HAS_MODEL_FIELD", model_id, field_id)

            # FK reference edge
            if f.is_foreign_key and f.references_model:
                target_id = model_name_to_id.get(f.references_model)
                if target_id:
                    batch.add_relationship("REFERENCES", field_id, target_id)

    # Phase C: Create RELATED_TO edges between models
    for m in models:
        model_id = model_name_to_id.get(m.name)
        if not model_id:
            continue
        for r in m.relations:
            target_id = model_name_to_id.get(r.target_model)
            if target_id and target_id != model_id:
                batch.add_merge_relationship("RELATED_TO", model_id, target_id, {
                    "relation_type": r.relation_type,
                    "foreign_key_field": r.foreign_key_field,
                    "through_model": r.through_model,
                    "source_field": r.source_field,
                    "orm_hint": r.orm_hint,
                })

    counts = batch.flush()
    return SchemaExtractionResult(
        models_found=len(models),
        fields_found=sum(len(m.fields) for m in models),
        relations_found=sum(len(m.relations) for m in models),
        nodes_created=counts["nodes_created"],
        relationships_created=counts["relationships_created"],
    )
```

**Acceptance:** Orchestrator calls Prisma parser + Drizzle extractor. Writes Model/ModelField nodes with correct edges. RELATED_TO edges have relation metadata. `PROMOTED_FROM` links to Class nodes for ORM-detected models. Returns accurate counts.

---

### Task 5: Wire schema extraction into the ingestion pipeline

**File:** `src/gristle/ingestion/pipeline.py`

Add schema extraction as a new phase between Config Phase and Phase 3 (docs):

```python
# In ingest_repo(), after config phase and before Phase 3:

# Schema phase: Detect ORM models, Prisma schemas, Drizzle tables
with Timer() as schema_phase:
    from gristle.ingestion.schema_extractor import SchemaExtractor
    # Build file_path_to_id map from pipeline's internal _id_map
    file_path_to_id = {
        path: node_id
        for path, node_id in self._id_map.items()
        if node_id.startswith("file::")
    }
    extractor = SchemaExtractor(self.graph, file_path_to_id)
    schema_result = extractor.extract(files)
    result.nodes_created += schema_result.nodes_created
    result.relationships_created += schema_result.relationships_created
    result.models_found = schema_result.models_found
    result.model_fields_found = schema_result.fields_found
    result.model_relations_found = schema_result.relations_found

logger.info(
    "Schema phase complete: %d models, %d fields, %d relations",
    schema_result.models_found,
    schema_result.fields_found,
    schema_result.relations_found,
    extra={
        "event": "schema_phase_done",
        "duration_ms": schema_phase.ms,
        "repo_id": self.graph.repo_id,
    },
)
```

**Also update:**
- `IngestionResult` dataclass: add `models_found: int = 0`, `model_fields_found: int = 0`, `model_relations_found: int = 0`
- `WalkedFile` collection: The existing walker filters by `supported_extensions` (from `ParserRegistry`). Prisma files (`.prisma`) aren't in that set. **Note:** `WalkedFile` only carries `relative_path`, `absolute_path`, and `extension` — NOT file content. Content is read on demand.

  **Recommendation:** Update the `walk_repo()` call in `ingest_repo()` to include `"prisma"` in the extensions set:
  ```python
  schema_extensions = frozenset({"prisma"})
  files = walk_repo(repo_path, self.registry.supported_extensions | schema_extensions)
  ```
  The parser registry returns `None` for `.prisma` files (no parser registered), so `_parse_and_build` skips them. The schema extractor picks them up from the walked file list and reads content itself via `Path(wf.absolute_path).read_text()`.

**Acceptance:** Schema extraction runs in the pipeline. `.prisma` files are collected. IngestionResult includes model counts. Existing pipeline phases unaffected.

---

### Task 6: Add graph schema indexes

**File:** `src/gristle/graph/schema.py`

Add to `_INDEXES`:

```python
("Model", "id"),
("Model", "name"),
("Model", "file_path"),
("Model", "orm"),
("ModelField", "id"),
("ModelField", "name"),
```

**Acceptance:** Indexes created on `ensure_schema()`. No errors on re-creation (idempotent via `contextlib.suppress`).

---

### Task 7: Add MCP query tools for schema data

**File:** `src/gristle/mcp/server.py`

Add two new MCP tools:

```python
@mcp.tool()
async def gristle_models(repo_id: str | None = None) -> dict:
    """List all database models with their fields and relationships.

    Returns models detected from Prisma schemas, Drizzle table definitions,
    and ORM class patterns (TypeORM, SQLAlchemy, Django, etc.).

    Each model includes: name, ORM framework, table name, fields with types
    and constraints, and relationships to other models.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.graph.execute("""
        MATCH (m:Model)
        WHERE NOT m.is_enum
        OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
        WITH m, collect({
            name: f.name,
            fieldType: f.field_type,
            dbType: f.db_type,
            isPrimaryKey: f.is_primary_key,
            isNullable: f.is_nullable,
            isUnique: f.is_unique,
            isForeignKey: f.is_foreign_key,
            referencesModel: f.references_model
        }) AS fields
        OPTIONAL MATCH (m)-[r:RELATED_TO]->(other:Model)
        WITH m, fields, collect({
            targetModel: other.name,
            relationType: r.relation_type,
            foreignKeyField: r.foreign_key_field,
            throughModel: r.through_model
        }) AS relations
        RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
               m.file_path AS filePath, m.field_count AS fieldCount,
               m.primary_key AS primaryKey, m.is_junction AS isJunction,
               m.is_enum AS isEnum, fields, relations
        ORDER BY m.name
    """)
    return {"models": result.records, "count": len(result.records)}


@mcp.tool()
async def gristle_model_detail(model_name: str, repo_id: str | None = None) -> dict:
    """Get detailed information about a specific database model.

    Returns the model definition including all fields with full constraint
    details, all relationships (incoming and outgoing), and which functions
    read/write this model's data.
    """
    engine = _resolve_engine(repo_id)
    if engine is None:
        return {"error": "No repository ingested. Call gristle_ingest first."}

    result = engine.graph.execute("""
        MATCH (m:Model {name: $name})
        OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
        WITH m, collect({
            name: f.name,
            fieldType: f.field_type,
            dbType: f.db_type,
            isPrimaryKey: f.is_primary_key,
            isNullable: f.is_nullable,
            isUnique: f.is_unique,
            isIndexed: f.is_indexed,
            hasDefault: f.has_default,
            defaultValue: f.default_value,
            isForeignKey: f.is_foreign_key,
            referencesModel: f.references_model,
            referencesField: f.references_field,
            line: f.line
        }) AS fields
        OPTIONAL MATCH (m)-[out:RELATED_TO]->(outModel:Model)
        WITH m, fields, collect({
            targetModel: outModel.name,
            relationType: out.relation_type,
            foreignKeyField: out.foreign_key_field,
            throughModel: out.through_model
        }) AS outgoing
        OPTIONAL MATCH (inModel:Model)-[inc:RELATED_TO]->(m)
        WITH m, fields, outgoing, collect({
            sourceModel: inModel.name,
            relationType: inc.relation_type,
            foreignKeyField: inc.foreign_key_field
        }) AS incoming
        RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
               m.file_path AS filePath, m.line_start AS lineStart,
               m.line_end AS lineEnd, m.primary_key AS primaryKey,
               m.docstring AS docstring,
               fields, outgoing AS outgoingRelations, incoming AS incomingRelations
    """, {"name": model_name})

    if not result.records:
        return {"error": f"Model '{model_name}' not found."}
    return result.records[0]
```

**Also add to `QueryEngine`** (`src/gristle/query/engine.py`):

```python
QUERY_MODELS = """
    MATCH (m:Model)
    WHERE NOT m.is_enum
    OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
    RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
           m.field_count AS fieldCount, m.primary_key AS primaryKey,
           collect(f.name) AS fieldNames
    ORDER BY m.name
"""

QUERY_MODEL_RELATIONSHIPS = """
    MATCH (a:Model)-[r:RELATED_TO]->(b:Model)
    RETURN a.name AS sourceModel, b.name AS targetModel,
           r.relation_type AS relationType,
           r.foreign_key_field AS foreignKeyField,
           r.through_model AS throughModel
    ORDER BY a.name, b.name
"""

QUERY_MODEL_FIELDS = """
    MATCH (m:Model {name: $name})-[:HAS_MODEL_FIELD]->(f:ModelField)
    RETURN f.name AS name, f.field_type AS fieldType,
           f.db_type AS dbType, f.is_primary_key AS isPrimaryKey,
           f.is_nullable AS isNullable, f.is_unique AS isUnique,
           f.is_foreign_key AS isForeignKey,
           f.references_model AS referencesModel,
           f.references_field AS referencesField
    ORDER BY f.is_primary_key DESC, f.name
"""
```

**Acceptance:** Both MCP tools return correct data. Query engine templates work. Empty results for repos without models (graceful degradation).

---

### Task 8: Update IngestionResult and MCP stats

**Files:** `src/gristle/mcp/server.py`, `src/gristle/ingestion/pipeline.py`

**Note:** There is no `gristle_stats` tool. The relevant tools are:
- `gristle_conventions` — calls `engine.get_repo_overview()` which uses `labels(n)[0]` counting. New `Model`/`ModelField` nodes will appear automatically in the overview without code changes.
- `gristle_ingest` — returns fields from `IngestionResult`. Add model counts to the return dict.
- `gristle_ingest_github` — same return pattern, also needs model counts.

```python
# In gristle_ingest return dict (after the existing fields):
"models_found": result.models_found,
"model_fields_found": result.model_fields_found,
"model_relations_found": result.model_relations_found,

# Same additions in gristle_ingest_github return dict.
```

**Acceptance:** `gristle_ingest` and `gristle_ingest_github` return model counts. `gristle_conventions` overview includes Model/ModelField counts automatically via label counting.

---

### Task 9: Update documentation

**Files:** `CONTEXT.md`, `ARCHITECTURE.md`, `docs/integration-guide.md`, `docs/ziggy-integration.md`

Per the CLAUDE.md rules, any new node types, properties, or edge types must be documented. Update:

1. **`CONTEXT.md`**: Add `Model` and `ModelField` to the node type list (currently 12 types). Add `HAS_MODEL_FIELD`, `REFERENCES`, `RELATED_TO`, `PROMOTED_FROM` to the edge type list.
2. **`ARCHITECTURE.md`**: Document the Schema Phase in the pipeline section (between Config Phase and Phase 3). Document the `SchemaExtractor` class and its role.
3. **`docs/integration-guide.md`**: Add `Model` and `ModelField` to the graph schema reference. Document `gristle_models` and `gristle_model_detail` MCP tools.
4. **`docs/ziggy-integration.md`**: Document the new nodes and edges that Ziggy can query. Add example Cypher queries for model discovery.

**Acceptance:** All four docs updated. New node/edge types documented. No stale references.

---

## Prisma Parser Detail

### File: `src/gristle/parsers/prisma.py`

**Public API:**

```python
def parse_prisma_schema(file_path: str, content: str) -> list[ParsedModel]:
    """Parse a .prisma file and return model definitions."""
```

**Internal structure:**

```python
# Regex patterns
# Note: Do NOT use [^}]* for block extraction — use brace-counting instead.
# The _MODEL_START_RE finds the opening of each block; a brace-counting loop
# extracts the body. Only match model and enum (skip type for P0).
_MODEL_START_RE = re.compile(
    r'^(model|enum)\s+(\w+)\s*\{',
    re.MULTILINE
)

_FIELD_RE = re.compile(
    r'^\s+(\w+)\s+'           # field name
    r'(\w+(?:\[\])?(?:\?)?)'  # type (with optional [] or ?)
    r'(.*?)$',                # attributes (rest of line)
    re.MULTILINE
)

# Note: Prisma allows fields/references/name in any order inside @relation().
# Instead of a single regex that assumes fields-before-references, extract the
# full @relation(...) content first, then find fields: and references: within it.
_RELATION_BLOCK_RE = re.compile(r'@relation\(([^)]*)\)', re.DOTALL)
_RELATION_FIELDS_RE = re.compile(r'fields:\s*\[([^\]]+)\]')
_RELATION_REFS_RE = re.compile(r'references:\s*\[([^\]]+)\]')
_RELATION_NAME_RE = re.compile(r'name:\s*"([^"]*)"')
# Usage: match _RELATION_BLOCK_RE, then search _RELATION_FIELDS_RE and
# _RELATION_REFS_RE within the captured group. This handles any argument order.

_MAP_RE = re.compile(r'@@map\("([^"]+)"\)')
_FIELD_MAP_RE = re.compile(r'@map\("([^"]+)"\)')
_DEFAULT_RE = re.compile(r'@default\(([^)]+)\)')
_DB_TYPE_RE = re.compile(r'@db\.(\w+(?:\([^)]*\))?)')
_INDEX_RE = re.compile(r'@@index\(\[([^\]]+)\]\)')
_UNIQUE_RE = re.compile(r'@@unique\(\[([^\]]+)\]\)')
_ID_RE = re.compile(r'@@id\(\[([^\]]+)\]\)')
_DOCSTRING_RE = re.compile(r'///\s*(.*)')
```

**Prisma type → field_type mapping:**

| Prisma type | field_type |
|---|---|
| `String` | `string` |
| `Int` | `number` |
| `Float` | `number` |
| `Decimal` | `number` |
| `BigInt` | `number` |
| `Boolean` | `boolean` |
| `DateTime` | `Date` |
| `Json` | `object` |
| `Bytes` | `bytes` |

If the type name matches another model name, it's a **relation field** (virtual — not a DB column). These don't get `ModelField` nodes but DO contribute to `ParsedModelRelation`.

**Nullable detection:** `String?` (trailing `?`) → `is_nullable: true`. Prisma defaults to NOT NULL, so absence of `?` means `is_nullable: false`.

**Array fields:** `Player[]` means one-to-many relation (virtual, not a column).

---

## Drizzle Extractor Detail

### File: `src/gristle/parsers/drizzle.py`

**Public API:**

```python
def is_drizzle_schema(content: str) -> bool:
    """Check if file content imports from drizzle-orm and defines tables."""

def parse_drizzle_schema(file_path: str, content: str) -> list[ParsedModel]:
    """Parse Drizzle pgTable/mysqlTable/sqliteTable definitions."""
```

**Detection heuristic:**

```python
_DRIZZLE_IMPORT_RE = re.compile(
    r"from\s+['\"]drizzle-orm/(?:pg|mysql|sqlite)-core['\"]"
)
_TABLE_DEF_RE = re.compile(
    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*"
    r"(pgTable|mysqlTable|sqliteTable)\(\s*['\"](\w+)['\"]"
)
```

**Column parsing approach:**

After finding a `pgTable(...)` call, extract the column definition object. Each property in that object is a column:

```typescript
id: uuid('id').primaryKey().defaultRandom(),
//   ↑ type   ↑ db col name    ↑ chained methods
```

Parse with regex: `(\w+)\s*:\s*(\w+)\(\s*['"]([\w]+)['"](?:\s*,\s*\{([^}]*)\})?\s*\)([\w\s().,'"/]*?)(?:,|$)`

Then parse the chained method calls for constraints.

**Relation detection via `.references()`:**

```typescript
teamId: uuid('team_id').notNull().references(() => teams.id),
```

Regex: `.references\(\s*\(\)\s*=>\s*(\w+)\.(\w+)\s*\)` → `references_model: "teams"` (need to map variable name to model name), `references_field: "id"`

**Variable-to-model mapping:** The Drizzle variable name (`teams`) maps to the table name passed to `pgTable('teams', ...)`. Build a `var_name → table_name` map from all detected table definitions, then resolve FK references through it.

**Two-pass approach required:**
1. **First pass:** Collect all `const X = pgTable('Y', ...)` definitions → build `{variable_name: table_name}` map
2. **Second pass:** Resolve `.references(() => X.field)` using that map → `references_model` gets the table name, not the variable name

**Cross-file FK resolution — out of scope for P0:** Drizzle projects commonly split schemas across multiple files (e.g., `users.ts`, `posts.ts`). In P0, the parser processes each file independently, so `.references(() => users.id)` where `users` is imported from another file will NOT resolve — the variable name `users` will be stored as-is. P1 should build a cross-file variable map from the walked file list to resolve these. For P0, the FK `references_model` will contain the variable name (which is usually the same as the table name by convention).

**Error handling:** Like the Prisma parser, wrap each table definition parse in a try/except. Log warnings for malformed definitions and continue with remaining tables.

---

## ORM Class Promoter Detail

### Part of: `src/gristle/ingestion/schema_extractor.py`

**P0 implementation (this spec): empty stub that returns `[]`.**

The framework is designed so P1 implementations can add individual promoters:

```python
def _promote_orm_classes(self) -> list[ParsedModel]:
    """Query graph for Class nodes that match ORM patterns and promote to Models.

    P0: Returns empty list (framework only).
    P1: Will add TypeORM (decorator-based) and SQLAlchemy (inheritance-based).
    P2: Will add Django (models.Model) and Sequelize (Model.init).
    """
    models: list[ParsedModel] = []

    # Future: each promoter queries graph for matching Class nodes
    # Example (TypeORM):
    #   MATCH (c:Class) WHERE ANY(d IN c.decorators WHERE d STARTS WITH 'Entity')
    #   OPTIONAL MATCH (c)-[:HAS_FIELD]->(f:TypeField)
    #   RETURN c, collect(f)
    #
    # Then convert Class+TypeField → ParsedModel+ParsedModelField

    return models
```

The key design principle: promoters read from the graph (Class nodes already exist from Phase 1), then create NEW `Model` nodes linked via `PROMOTED_FROM`. They don't modify existing Class nodes — the schema layer is additive.

---

## Ziggy-Side Changes

After Gristle ships the schema extractor, Ziggy needs these updates:

### 1. Update `code-graph-types.ts`

Add types matching the new nodes:

```typescript
export interface GristleModel {
  name: string;
  qualifiedName: string;
  filePath: string;
  orm: string;
  tableName: string;
  primaryKey: string | null;
  fieldCount: number;
  isJunction: boolean;
  docstring: string | null;
}

export interface GristleModelField {
  name: string;
  fieldType: string;
  dbType: string | null;
  isPrimaryKey: boolean;
  isNullable: boolean;
  isUnique: boolean;
  isIndexed: boolean;
  isForeignKey: boolean;
  referencesModel: string | null;
  referencesField: string | null;
}

export interface GristleModelRelation {
  sourceModel: string;
  targetModel: string;
  relationType: string;
  foreignKeyField: string | null;
  throughModel: string | null;
}
```

### 2. Update `code-graph-queries.ts`

Add queries for domain research:

```typescript
export const QUERY_MODELS_WITH_FIELDS = `
  MATCH (m:Model)
  OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
  RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
         m.field_count AS fieldCount, m.primary_key AS primaryKey,
         collect({
           name: f.name, fieldType: f.field_type, dbType: f.db_type,
           isPK: f.is_primary_key, isNullable: f.is_nullable,
           isFK: f.is_foreign_key, referencesModel: f.references_model
         }) AS fields
  ORDER BY m.name
`;

export const QUERY_MODEL_RELATIONSHIPS = `
  MATCH (a:Model)-[r:RELATED_TO]->(b:Model)
  RETURN a.name AS source, b.name AS target,
         r.relation_type AS relationType,
         r.foreign_key_field AS foreignKeyField,
         r.through_model AS throughModel
  ORDER BY a.name, b.name
`;
```

### 3. Update `code-graph.ts` (GristleClient)

Add convenience methods:

```typescript
async getModels(graphName: string): Promise<GristleModel[]>
async getModelRelationships(graphName: string): Promise<GristleModelRelation[]>
async getModelWithFields(graphName: string, modelName: string): Promise<{ model: GristleModel; fields: GristleModelField[] } | null>
```

### 4. Update domain-research-phase-plan.md Task 9.7

Replace the inference-based query helpers with direct Model/ModelField/ModelRelation queries. See the [companion update](#companion-update-to-domain-research-spec) section below.

### 5. Update CodeGraph node stats

The `CodeGraph` node in Ziggy's graph (tracked via `(:App)-[:HAS_CODE_GRAPH]->(:CodeGraph)`) should include `modelCount` alongside existing `functionCount`, `classCount`, etc.

---

## Testing Strategy

### Test fixtures needed

**Prisma fixture:** `tests/fixtures/sample_prisma/schema.prisma`
```prisma
datasource db {
  provider = "postgresql"
  url      = env("DATABASE_URL")
}

model User {
  id        String   @id @default(uuid())
  email     String   @unique
  name      String?
  posts     Post[]
  profile   Profile?
  createdAt DateTime @default(now())

  @@map("users")
}

model Post {
  id        String   @id @default(uuid())
  title     String
  content   String?
  published Boolean  @default(false)
  authorId  String
  author    User     @relation(fields: [authorId], references: [id])
  tags      Tag[]
  createdAt DateTime @default(now())
}

model Profile {
  id     String @id @default(uuid())
  bio    String?
  userId String @unique
  user   User   @relation(fields: [userId], references: [id])
}

model Tag {
  id    String @id @default(uuid())
  name  String @unique
  posts Post[]
}

enum Role {
  USER
  ADMIN
  MODERATOR
}
```

**Drizzle fixture:** `tests/fixtures/sample_drizzle/schema.ts`
```typescript
import { pgTable, varchar, uuid, timestamp, boolean, text, index } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: varchar('email', { length: 255 }).notNull().unique(),
  name: varchar('name', { length: 255 }),
  createdAt: timestamp('created_at').defaultNow(),
});

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey().defaultRandom(),
  title: varchar('title', { length: 255 }).notNull(),
  content: text('content'),
  published: boolean('published').default(false),
  authorId: uuid('author_id').notNull().references(() => users.id),
  createdAt: timestamp('created_at').defaultNow(),
}, (table) => ({
  authorIdx: index('author_idx').on(table.authorId),
}));
```

### Test cases

**`tests/test_prisma_parser.py`:**
1. Parses model with all field types (String, Int, Float, Boolean, DateTime, Json, Bytes)
2. Detects `@id` → primary key
3. Detects `@unique` → is_unique
4. Detects `@default(...)` → has_default + default_value
5. Detects `@relation(fields: [...], references: [...])` → FK + relation
6. Detects `@@map("table_name")` → table_name
7. Detects `@@index([fields])` → is_indexed on fields
8. Handles optional fields (`String?`) → is_nullable
9. Handles array relations (`Post[]`) → one-to-many
10. Skips `@ignore` fields and `@@ignore` models
11. Parses enums as models with `orm: "prisma"` and enum member fields
12. Handles `///` doc comments → docstring
13. Handles composite `@@id([fieldA, fieldB])` → composite PK
14. Handles `@db.VarChar(255)` → db_type
15. Handles multi-line `@relation(...)` attributes

**`tests/test_drizzle_parser.py`:**
1. Parses `pgTable` definition with column types
2. Maps Drizzle types to field_type (uuid→string, varchar→string, timestamp→Date, etc.)
3. Detects `.primaryKey()` → is_primary_key
4. Detects `.notNull()` → is_nullable: false
5. Detects `.unique()` → is_unique
6. Detects `.default(...)` → has_default
7. Detects `.references(() => table.field)` → FK
8. Handles index definitions in third argument
9. `is_drizzle_schema()` returns true for Drizzle files, false for non-Drizzle
10. Handles `mysqlTable` and `sqliteTable` in addition to `pgTable`

**`tests/test_schema_extractor.py`:**
1. Full pipeline test: Prisma file → Model/ModelField nodes in graph
2. Full pipeline test: Drizzle file → Model/ModelField nodes in graph
3. `REFERENCES` edges created for FK fields
4. `RELATED_TO` edges created between models
5. `CONTAINS` edge from File to Model
6. Empty result when no schema files present
7. Mixed: Prisma + Drizzle files in same repo
8. IngestionResult includes model counts

---

## Execution Order

```
Task 1: Data models (ParsedModel, ParsedModelField, ParsedModelRelation,     ← no deps
         SchemaExtractionResult)
Task 6: Graph schema indexes (Model, ModelField)                              ← no deps

Task 2: Prisma parser                            ← depends on Task 1
Task 3: Drizzle extractor                        ← depends on Task 1
Task 4: Schema extractor orchestrator            ← depends on Tasks 1, 2, 3

Task 5: Pipeline wiring                          ← depends on Task 4
Task 7: MCP tools + QueryEngine                  ← depends on Task 6
Task 8: Stats/ingest result updates              ← depends on Task 5
Task 9: Documentation updates                    ← depends on Tasks 6, 7 (needs final node/edge list)

Tests: Can run after each task for unit tests, full integration after Task 5
```

**Parallelism:**
- Tasks 1 + 6 can run together
- Tasks 2 + 3 can run in parallel (independent parsers, both depend on Task 1)
- Task 7 can run in parallel with Tasks 4/5 (different files)
- Task 9 can run in parallel with Task 8 (different files)

---

## Companion Update to Domain Research Spec

After this Gristle spec is implemented, `docs/specs/domain-research-phase-plan.md` Task 9.7 should be rewritten to use the new Model nodes directly instead of inferring data models from TypeField/Class workarounds. The key change:

**Before (inference):** Query Class nodes with ORM-sounding bases + TypeField nodes, then send to LLM for "guess which of these are database models"

**After (direct):** Query Model/ModelField/ModelRelation nodes directly — definitive, no guessing

```typescript
// NEW Task 9.7 queries (replacing the 5 inference helpers)

export async function queryModels(client: GristleClient, graphName: string): Promise<ModelWithFields[]> {
  // MATCH (m:Model) OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
  // RETURN m.name, m.orm, m.table_name, collect({name, fieldType, isPK, isFK, referencesModel})
}

export async function queryModelRelationships(client: GristleClient, graphName: string): Promise<ModelRelationship[]> {
  // MATCH (a:Model)-[r:RELATED_TO]->(b:Model)
  // RETURN a.name, b.name, r.relation_type, r.foreign_key_field
}
```

Sub-phase C (Data Model Intelligence) gets dramatically better input: instead of "here are some classes that might be models," the LLM gets "here are the definitive models with their FKs and relationships — now identify graph opportunities beyond what these relational FK links provide."
