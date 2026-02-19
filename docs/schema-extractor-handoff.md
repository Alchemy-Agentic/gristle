# Gristle Schema Extractor — Ziggy Handoff

> **What shipped:** First-class `Model` and `ModelField` nodes in Gristle's code graph, parsed from Prisma schemas and Drizzle table definitions. Ziggy can now query definitive data model information instead of inferring it from Class/TypeField heuristics.
>
> **Spec:** [gristle-schema-extractor.md](gristle-schema-extractor.md)
> **Status:** P0 complete (Prisma + Drizzle). P1 stubs in place (ORM class promoter returns `[]`).

---

## What Changed in the Graph

### New Node Types

#### Model

Represents a database table, collection, or document type.

```
(:Model {
  id: "model::{file_path}::{name}",
  name: "User",
  qualified_name: "prisma/schema.prisma::User",
  file_path: "prisma/schema.prisma",
  line_start: 5,
  line_end: 15,
  orm: "prisma",            // "prisma" | "drizzle" (P1: "typeorm" | "sqlalchemy" | "django" | "mongoose" | "sequelize")
  table_name: "users",      // Actual DB table name (from @@map or inferred)
  is_junction: false,       // True for many-to-many join tables
  is_enum: false,           // True for Prisma enum definitions
  primary_key: "id",        // PK field name(s), comma-separated if composite
  field_count: 6,           // Number of ModelField nodes
  docstring: null            // From /// comments (Prisma) or JSDoc (future)
})
```

**Important:** Enum definitions (Prisma `enum Role { ... }`) are also stored as Model nodes with `is_enum: true`. Filter them out with `WHERE NOT m.is_enum` when querying for table models.

#### ModelField

Represents a column/field in a database model.

```
(:ModelField {
  id: "mf::{model_id}::{name}",
  name: "email",
  field_type: "string",        // Normalized app type: string, number, boolean, Date, object, bytes, enum_member
  db_type: "varchar(255)",     // DB-specific type if annotated (e.g., @db.VarChar(255)), null otherwise
  is_primary_key: false,
  is_nullable: false,          // Prisma defaults to NOT NULL; Drizzle defaults to nullable
  is_unique: true,
  is_indexed: false,           // True if covered by @index, @unique, or similar
  has_default: false,
  default_value: null,         // String representation (e.g., "uuid()", "now()", "false")
  is_foreign_key: false,
  references_model: null,      // Model name this FK points to (e.g., "User")
  references_field: null,      // Field in referenced model (e.g., "id")
  line: null                   // Line number in source (null for most fields currently)
})
```

**Enum members** are stored as ModelField with `field_type: "enum_member"`.

### New Edge Types

| Edge | Direction | Properties | Meaning |
|------|-----------|------------|---------|
| `HAS_MODEL_FIELD` | Model → ModelField | none | Model contains this field/column |
| `REFERENCES` | ModelField → Model | none | FK relationship (only when `is_foreign_key: true`) |
| `RELATED_TO` | Model → Model | `relation_type`, `foreign_key_field`, `through_model`, `source_field`, `orm_hint` | High-level relationship between models |
| `PROMOTED_FROM` | Model → Class | none | Links Model back to its source Class node (for ORM class promoter, not used by Prisma/Drizzle) |
| `CONTAINS` | File → Model | none | Standard file containment (same pattern as File → Function) |

#### RELATED_TO Edge Properties

```
relation_type: "one-to-one" | "one-to-many" | "many-to-one" | "many-to-many"
foreign_key_field: "authorId"        // FK field on source model (null for inverse relations)
through_model: null                  // Junction table name for many-to-many (future)
source_field: "author"               // Relation field name in ORM (e.g., the virtual field in Prisma)
orm_hint: "prisma_relation" | "drizzle_reference"
```

**Relation type semantics:**
- `many-to-one`: Model has a FK field pointing to another model (e.g., Post.authorId → User)
- `one-to-one`: Same as many-to-one but FK field has `@unique` constraint
- `one-to-many`: Inverse side — model has an array relation field (e.g., User.posts: Post[])
- `many-to-many`: Two FKs as composite PK in a junction table (future)

### New Graph Indexes

6 new property indexes (total now 33):
- `Model.id`, `Model.name`, `Model.file_path`, `Model.orm`
- `ModelField.id`, `ModelField.name`

---

## What Changed in MCP

### New MCP Tools

#### `gristle_models(repo_id?)`

Lists all database models (excluding enums) with fields and relationships.

```json
{
  "models": [
    {
      "name": "User",
      "orm": "prisma",
      "tableName": "users",
      "filePath": "prisma/schema.prisma",
      "fieldCount": 6,
      "primaryKey": "id",
      "isJunction": false,
      "isEnum": false,
      "fields": [
        {"name": "id", "fieldType": "string", "dbType": null, "isPrimaryKey": true, "isNullable": false, "isUnique": false, "isForeignKey": false, "referencesModel": null},
        {"name": "email", "fieldType": "string", "dbType": null, "isPrimaryKey": false, "isNullable": false, "isUnique": true, "isForeignKey": false, "referencesModel": null}
      ],
      "relations": [
        {"targetModel": "Post", "relationType": "one-to-many", "foreignKeyField": null, "throughModel": null}
      ]
    }
  ],
  "count": 5
}
```

#### `gristle_model_detail(model_name, repo_id?)`

Full details for a single model including incoming + outgoing relationships.

```json
{
  "name": "Post",
  "orm": "prisma",
  "tableName": null,
  "filePath": "prisma/schema.prisma",
  "lineStart": 12,
  "lineEnd": 22,
  "primaryKey": "id",
  "docstring": null,
  "fields": [
    {"name": "id", "fieldType": "string", "dbType": null, "isPrimaryKey": true, "isNullable": false, "isUnique": false, "isIndexed": false, "hasDefault": true, "defaultValue": "uuid(", "isForeignKey": false, "referencesModel": null, "referencesField": null, "line": null},
    {"name": "authorId", "fieldType": "string", "dbType": null, "isPrimaryKey": false, "isNullable": false, "isUnique": false, "isIndexed": false, "hasDefault": false, "defaultValue": null, "isForeignKey": true, "referencesModel": "User", "referencesField": "id", "line": null}
  ],
  "outgoingRelations": [
    {"targetModel": "User", "relationType": "many-to-one", "foreignKeyField": "authorId", "throughModel": null}
  ],
  "incomingRelations": [
    {"sourceModel": "Tag", "relationType": "one-to-many", "foreignKeyField": null}
  ]
}
```

### Updated MCP Tool Returns

`gristle_ingest` and `gristle_ingest_github` now return three additional fields:

```json
{
  "models_found": 5,
  "model_fields_found": 28,
  "model_relations_found": 6
}
```

These appear alongside existing fields (`functions`, `classes`, `routes`, etc.).

### `gristle_conventions` / Overview

Model and ModelField nodes automatically appear in the label-count overview returned by `gristle_conventions` — no code changes needed since it uses `labels(n)[0]` counting.

---

## Cypher Queries for Ziggy

These are the queries Ziggy agents should use. All run against `gristle_{appId}` graphs.

### All models with fields (Domain Research)

```cypher
MATCH (m:Model)
WHERE NOT m.is_enum
OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
       m.field_count AS fieldCount, m.primary_key AS primaryKey,
       collect({
         name: f.name, fieldType: f.field_type, dbType: f.db_type,
         isPK: f.is_primary_key, isNullable: f.is_nullable,
         isFK: f.is_foreign_key, referencesModel: f.references_model
       }) AS fields
ORDER BY m.name
```

### All model relationships

```cypher
MATCH (a:Model)-[r:RELATED_TO]->(b:Model)
RETURN a.name AS source, b.name AS target,
       r.relation_type AS relationType,
       r.foreign_key_field AS foreignKeyField,
       r.through_model AS throughModel
ORDER BY a.name, b.name
```

### Single model with full field details

```cypher
MATCH (m:Model {name: $name})
OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
RETURN m.name AS name, m.orm AS orm, m.table_name AS tableName,
       m.field_count AS fieldCount, m.primary_key AS primaryKey,
       collect({
         name: f.name, fieldType: f.field_type, dbType: f.db_type,
         isPK: f.is_primary_key, isNullable: f.is_nullable,
         isUnique: f.is_unique, isIndexed: f.is_indexed,
         isFK: f.is_foreign_key, referencesModel: f.references_model,
         referencesField: f.references_field
       }) AS fields
```

### FK chain traversal (find all models reachable from a starting model)

```cypher
MATCH path = (start:Model {name: $name})-[:RELATED_TO*1..5]->(end:Model)
RETURN [n IN nodes(path) | n.name] AS chain,
       [r IN relationships(path) | r.relation_type] AS relationTypes
```

### Model count (for CodeGraph stats)

```cypher
MATCH (m:Model) WHERE NOT m.is_enum RETURN count(m) AS modelCount
```

### Enums

```cypher
MATCH (m:Model)
WHERE m.is_enum
OPTIONAL MATCH (m)-[:HAS_MODEL_FIELD]->(f:ModelField)
RETURN m.name AS name, collect(f.name) AS members
ORDER BY m.name
```

---

## What Ziggy Needs To Do

Directly from the spec's "Ziggy-Side Changes" section:

### 1. Add TypeScript types (`code-graph-types.ts`)

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

### 2. Add Cypher query constants (`code-graph-queries.ts`)

Use the queries from the "Cypher Queries for Ziggy" section above.

### 3. Add GristleClient methods (`code-graph.ts`)

```typescript
async getModels(graphName: string): Promise<GristleModel[]>
async getModelRelationships(graphName: string): Promise<GristleModelRelation[]>
async getModelWithFields(graphName: string, modelName: string): Promise<{ model: GristleModel; fields: GristleModelField[] } | null>
```

### 4. Update Domain Research Phase (Task 9.7)

Replace the inference-based Class/TypeField heuristics with direct Model/ModelField queries. The data is now definitive — no guessing needed.

### 5. Update CodeGraph node stats

Add `modelCount` to the `(:App)-[:HAS_CODE_GRAPH]->(:CodeGraph)` node alongside existing `functionCount`, `classCount`, etc. Source: `MATCH (m:Model) WHERE NOT m.is_enum RETURN count(m)`.

---

## What's Supported (P0)

| ORM | File Types | Detection | Relation Types |
|-----|-----------|-----------|----------------|
| **Prisma** | `.prisma` | `model`/`enum` blocks | one-to-one, one-to-many, many-to-one |
| **Drizzle** | `.ts`, `.js`, `.mts`, `.mjs` | `pgTable`/`mysqlTable`/`sqliteTable` imports | many-to-one (via `.references()`) |

### Prisma features parsed
- All scalar types (String, Int, Float, Decimal, BigInt, Boolean, DateTime, Json, Bytes)
- `@id`, `@unique`, `@default(...)`, `@db.Type(...)`, `@relation(fields:..., references:...)`
- `@@map("table")`, `@@index([...])`, `@@unique([...])`, `@@id([...])` (composite PK)
- `@ignore` (field-level), `@@ignore` (model-level)
- `///` docstrings above models/enums
- Enum definitions as `Model` with `is_enum: true`
- Relation type upgrade: `many-to-one` → `one-to-one` when FK has `@unique`

### Drizzle features parsed
- `pgTable`, `mysqlTable`, `sqliteTable` calls
- Column types: uuid, varchar, text, integer, boolean, timestamp, serial, bigint, real, numeric, json, jsonb, date, time, char, smallint, bigserial, doublePrecision
- `.primaryKey()`, `.notNull()`, `.unique()`, `.default(...)`, `.defaultRandom()`, `.defaultNow()`
- `.references(() => table.field)` → FK detection
- Index block (third argument to table definition)
- Variable-to-table-name resolution for FK references within the same file

### Not yet supported (P1/P2)
- TypeORM (`@Entity()` + `@Column()` decorators)
- SQLAlchemy (declarative base classes)
- Django (`models.Model` inheritance)
- Mongoose (`new Schema({})`)
- Sequelize (`Model.init()`)
- Cross-file Drizzle FK resolution (`.references(() => importedTable.id)`)
- Many-to-many junction table detection

The ORM class promoter framework is in place (`SchemaExtractor._promote_orm_classes()`) — it currently returns `[]` and is ready for P1 implementations.

---

## Pipeline Position

```
Phase 1: Parse files → File, Function, Class, TypeField, Route nodes
Phase 2: Resolve cross-file calls, imports, inheritance, PASSED_TO
Config Phase: EnvVar nodes, USES_ENV edges
Schema Phase: Model, ModelField nodes, REFERENCES/RELATED_TO/HAS_MODEL_FIELD edges  ← NEW
Phase 3: Documentation
```

The Schema Phase runs after Phase 2 (needs `INHERITS_FROM` edges for future ORM base class detection) and after Config Phase. It reads raw file content for DSL parsing (Prisma/Drizzle) — it does NOT depend on Phase 1 parser output for these two ORMs.

---

## Test Coverage

911 total tests (66 new):
- 28 Prisma parser tests (`tests/test_prisma_parser.py`)
- 30 Drizzle parser tests (`tests/test_drizzle_parser.py`)
- 8 Schema extractor integration tests (`tests/test_schema_extractor.py`)

All tests use mock graph clients — no FalkorDB needed.
