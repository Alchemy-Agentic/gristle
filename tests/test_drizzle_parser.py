"""Tests for gristle.parsers.drizzle."""

from pathlib import Path

from gristle.parsers.drizzle import is_drizzle_schema, parse_drizzle_schema

FIXTURES_DIR = Path(__file__).parent / "fixtures"


# ---------------------------------------------------------------------------
# 1. Column types
# ---------------------------------------------------------------------------


class TestColumnTypes:
    def test_parses_common_column_types(self):
        content = """\
import { pgTable, uuid, varchar, text, integer, boolean, timestamp } from 'drizzle-orm/pg-core';

export const items = pgTable('items', {
  id: uuid('id').primaryKey(),
  name: varchar('name', { length: 255 }).notNull(),
  description: text('description'),
  quantity: integer('quantity'),
  active: boolean('active'),
  createdAt: timestamp('created_at'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        assert len(models) == 1
        model = models[0]
        assert model.name == "items"
        assert model.orm == "drizzle"

        fields_by_name = {f.name: f for f in model.fields}
        assert fields_by_name["id"].field_type == "string"
        assert fields_by_name["id"].db_type == "uuid"
        assert fields_by_name["name"].field_type == "string"
        assert fields_by_name["name"].db_type == "varchar"
        assert fields_by_name["description"].field_type == "string"
        assert fields_by_name["description"].db_type == "text"
        assert fields_by_name["quantity"].field_type == "number"
        assert fields_by_name["quantity"].db_type == "integer"
        assert fields_by_name["active"].field_type == "boolean"
        assert fields_by_name["active"].db_type == "boolean"
        assert fields_by_name["created_at"].field_type == "Date"
        assert fields_by_name["created_at"].db_type == "timestamp"


# ---------------------------------------------------------------------------
# 2. Primary key
# ---------------------------------------------------------------------------


class TestPrimaryKey:
    def test_primary_key_detected(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  name: varchar('name'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        model = models[0]
        assert model.primary_key == "id"

        id_field = next(f for f in model.fields if f.name == "id")
        assert id_field.is_primary_key is True

        name_field = next(f for f in model.fields if f.name == "name")
        assert name_field.is_primary_key is False


# ---------------------------------------------------------------------------
# 3. Not null
# ---------------------------------------------------------------------------


class TestNotNull:
    def test_not_null_sets_is_nullable_false(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  email: varchar('email').notNull(),
  nickname: varchar('nickname'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        fields_by_name = {f.name: f for f in models[0].fields}

        assert fields_by_name["email"].is_nullable is False
        assert fields_by_name["nickname"].is_nullable is True


# ---------------------------------------------------------------------------
# 4. Unique
# ---------------------------------------------------------------------------


class TestUnique:
    def test_unique_detected(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  email: varchar('email').notNull().unique(),
  name: varchar('name'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        fields_by_name = {f.name: f for f in models[0].fields}

        assert fields_by_name["email"].is_unique is True
        assert fields_by_name["name"].is_unique is False


# ---------------------------------------------------------------------------
# 5. Defaults
# ---------------------------------------------------------------------------


class TestDefault:
    def test_default_literal(self):
        content = """\
import { pgTable, uuid, boolean } from 'drizzle-orm/pg-core';

export const settings = pgTable('settings', {
  id: uuid('id').primaryKey(),
  enabled: boolean('enabled').default(false),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        field = next(f for f in models[0].fields if f.name == "enabled")
        assert field.has_default is True
        assert field.default_value == "false"

    def test_default_random(self):
        content = """\
import { pgTable, uuid } from 'drizzle-orm/pg-core';

export const items = pgTable('items', {
  id: uuid('id').primaryKey().defaultRandom(),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        field = models[0].fields[0]
        assert field.has_default is True
        assert field.default_value == "random()"

    def test_default_now(self):
        content = """\
import { pgTable, uuid, timestamp } from 'drizzle-orm/pg-core';

export const events = pgTable('events', {
  id: uuid('id').primaryKey(),
  createdAt: timestamp('created_at').defaultNow(),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        field = next(f for f in models[0].fields if f.name == "created_at")
        assert field.has_default is True
        assert field.default_value == "now()"

    def test_no_default(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const items = pgTable('items', {
  id: uuid('id').primaryKey(),
  name: varchar('name'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        field = next(f for f in models[0].fields if f.name == "name")
        assert field.has_default is False
        assert field.default_value is None


# ---------------------------------------------------------------------------
# 6. References (foreign key)
# ---------------------------------------------------------------------------


class TestReferences:
    def test_references_detected(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  name: varchar('name'),
});

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey(),
  authorId: uuid('author_id').notNull().references(() => users.id),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        posts = next(m for m in models if m.name == "posts")
        fk_field = next(f for f in posts.fields if f.name == "author_id")

        assert fk_field.is_foreign_key is True
        assert fk_field.references_model == "users"
        assert fk_field.references_field == "id"

    def test_references_create_relation(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
});

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey(),
  authorId: uuid('author_id').references(() => users.id),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        posts = next(m for m in models if m.name == "posts")

        assert len(posts.relations) == 1
        rel = posts.relations[0]
        assert rel.target_model == "users"
        assert rel.relation_type == "many-to-one"
        assert rel.foreign_key_field == "author_id"
        assert rel.orm_hint == "drizzle_reference"


# ---------------------------------------------------------------------------
# 7. Index block
# ---------------------------------------------------------------------------


class TestIndexBlock:
    def test_index_on_field(self):
        content = """\
import { pgTable, uuid, varchar, index } from 'drizzle-orm/pg-core';

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey(),
  slug: varchar('slug').notNull(),
}, (table) => ({
  slugIdx: index('slug_idx').on(table.slug),
}));
"""
        models = parse_drizzle_schema("schema.ts", content)
        field = next(f for f in models[0].fields if f.name == "slug")
        assert field.is_indexed is True

    def test_non_indexed_field_stays_false(self):
        content = """\
import { pgTable, uuid, varchar, index } from 'drizzle-orm/pg-core';

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey(),
  slug: varchar('slug').notNull(),
  title: varchar('title'),
}, (table) => ({
  slugIdx: index('slug_idx').on(table.slug),
}));
"""
        models = parse_drizzle_schema("schema.ts", content)
        title_field = next(f for f in models[0].fields if f.name == "title")
        assert title_field.is_indexed is False


# ---------------------------------------------------------------------------
# 8. is_drizzle_schema detection
# ---------------------------------------------------------------------------


class TestIsDrizzleSchema:
    def test_returns_true_for_pg_core_import(self):
        content = "import { pgTable } from 'drizzle-orm/pg-core';\n"
        assert is_drizzle_schema(content) is True

    def test_returns_true_for_mysql_core_import(self):
        content = "import { mysqlTable } from 'drizzle-orm/mysql-core';\n"
        assert is_drizzle_schema(content) is True

    def test_returns_true_for_sqlite_core_import(self):
        content = "import { sqliteTable } from 'drizzle-orm/sqlite-core';\n"
        assert is_drizzle_schema(content) is True

    def test_returns_false_for_regular_typescript(self):
        content = """\
import express from 'express';

const app = express();
app.get('/', (req, res) => res.send('hello'));
"""
        assert is_drizzle_schema(content) is False

    def test_returns_false_for_empty_string(self):
        assert is_drizzle_schema("") is False


# ---------------------------------------------------------------------------
# 9. MySQL table
# ---------------------------------------------------------------------------


class TestMysqlTable:
    def test_parses_mysql_table(self):
        content = """\
import { mysqlTable, varchar, int } from 'drizzle-orm/mysql-core';

export const products = mysqlTable('products', {
  id: int('id').primaryKey(),
  name: varchar('name', { length: 100 }).notNull(),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        assert len(models) == 1
        model = models[0]
        assert model.name == "products"
        assert model.table_name == "products"
        assert model.orm == "drizzle"
        assert len(model.fields) == 2


# ---------------------------------------------------------------------------
# 10. SQLite table
# ---------------------------------------------------------------------------


class TestSqliteTable:
    def test_parses_sqlite_table(self):
        content = """\
import { sqliteTable, text, integer } from 'drizzle-orm/sqlite-core';

export const notes = sqliteTable('notes', {
  id: integer('id').primaryKey(),
  body: text('body'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        assert len(models) == 1
        model = models[0]
        assert model.name == "notes"
        assert model.table_name == "notes"
        assert model.orm == "drizzle"
        assert len(model.fields) == 2


# ---------------------------------------------------------------------------
# 11. Variable-to-table resolution
# ---------------------------------------------------------------------------


class TestVarToTableResolution:
    def test_fk_resolves_through_var_to_table_map(self):
        """FK references use the JS variable name — parser should resolve to the
        actual table name via the var→table map."""
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

const orgTable = pgTable('organizations', {
  id: uuid('id').primaryKey(),
  name: varchar('name').notNull(),
});

const memberTable = pgTable('members', {
  id: uuid('id').primaryKey(),
  orgId: uuid('org_id').references(() => orgTable.id),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        members = next(m for m in models if m.name == "members")
        fk_field = next(f for f in members.fields if f.name == "org_id")

        # Should resolve "orgTable" → "organizations"
        assert fk_field.is_foreign_key is True
        assert fk_field.references_model == "organizations"
        assert fk_field.references_field == "id"

        # Relation should also use the resolved table name
        assert len(members.relations) == 1
        assert members.relations[0].target_model == "organizations"


# ---------------------------------------------------------------------------
# 12. Fixture file integration test
# ---------------------------------------------------------------------------


class TestFixtureFile:
    def test_fixture_file(self):
        content = (FIXTURES_DIR / "sample_drizzle" / "schema.ts").read_text()
        models = parse_drizzle_schema("schema.ts", content)
        names = {m.name for m in models}
        assert "users" in names
        assert "posts" in names

        # FK detection
        posts = next(m for m in models if m.name == "posts")
        author_fk = next(f for f in posts.fields if f.name == "author_id")
        assert author_fk.is_foreign_key
        assert author_fk.references_model == "users"
        assert author_fk.references_field == "id"

    def test_fixture_file_index_detected(self):
        content = (FIXTURES_DIR / "sample_drizzle" / "schema.ts").read_text()
        models = parse_drizzle_schema("schema.ts", content)
        posts = next(m for m in models if m.name == "posts")

        # authorId has an index in the fixture
        author_field = next(f for f in posts.fields if f.name == "author_id")
        assert author_field.is_indexed is True

    def test_fixture_file_defaults(self):
        content = (FIXTURES_DIR / "sample_drizzle" / "schema.ts").read_text()
        models = parse_drizzle_schema("schema.ts", content)

        users = next(m for m in models if m.name == "users")
        id_field = next(f for f in users.fields if f.name == "id")
        assert id_field.has_default is True
        assert id_field.default_value == "random()"

        created_field = next(f for f in users.fields if f.name == "created_at")
        assert created_field.has_default is True
        assert created_field.default_value == "now()"


# ---------------------------------------------------------------------------
# 13. Empty / non-Drizzle file
# ---------------------------------------------------------------------------


class TestEmptyFile:
    def test_non_drizzle_typescript_returns_empty(self):
        content = """\
import { Router } from 'express';

const router = Router();
router.get('/health', (req, res) => res.json({ ok: true }));
export default router;
"""
        models = parse_drizzle_schema("routes.ts", content)
        assert models == []

    def test_empty_string_returns_empty(self):
        models = parse_drizzle_schema("empty.ts", "")
        assert models == []


# ---------------------------------------------------------------------------
# Additional edge-case tests
# ---------------------------------------------------------------------------


class TestModelMetadata:
    def test_qualified_name_format(self):
        content = """\
import { pgTable, uuid } from 'drizzle-orm/pg-core';

export const tasks = pgTable('tasks', {
  id: uuid('id').primaryKey(),
});
"""
        models = parse_drizzle_schema("src/db/schema.ts", content)
        assert models[0].qualified_name == "src/db/schema.ts::tasks"

    def test_file_path_preserved(self):
        content = """\
import { pgTable, uuid } from 'drizzle-orm/pg-core';

export const tasks = pgTable('tasks', {
  id: uuid('id').primaryKey(),
});
"""
        models = parse_drizzle_schema("src/db/schema.ts", content)
        assert models[0].file_path == "src/db/schema.ts"

    def test_line_numbers_set(self):
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  name: varchar('name'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        model = models[0]
        assert model.line_start >= 1
        assert model.line_end >= model.line_start


class TestMultipleTables:
    def test_parses_multiple_tables(self):
        content = """\
import { pgTable, uuid, varchar, text } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey(),
  name: varchar('name'),
});

export const posts = pgTable('posts', {
  id: uuid('id').primaryKey(),
  title: varchar('title'),
  body: text('body'),
});

export const comments = pgTable('comments', {
  id: uuid('id').primaryKey(),
  text: text('text'),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        assert len(models) == 3
        names = {m.name for m in models}
        assert names == {"users", "posts", "comments"}


class TestChainedModifiers:
    def test_multiple_chained_methods(self):
        """A column with multiple chained modifiers should all be detected."""
        content = """\
import { pgTable, uuid, varchar } from 'drizzle-orm/pg-core';

export const accounts = pgTable('accounts', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: varchar('email', { length: 255 }).notNull().unique(),
});
"""
        models = parse_drizzle_schema("schema.ts", content)
        fields_by_name = {f.name: f for f in models[0].fields}

        id_field = fields_by_name["id"]
        assert id_field.is_primary_key is True
        assert id_field.has_default is True

        email_field = fields_by_name["email"]
        assert email_field.is_nullable is False
        assert email_field.is_unique is True
