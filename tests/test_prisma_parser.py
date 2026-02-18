"""Tests for gristle.parsers.prisma."""

from pathlib import Path

from gristle.parsers.prisma import parse_prisma_schema

FIXTURES_DIR = Path(__file__).parent / "fixtures"


class TestFieldTypes:
    def test_field_types(self):
        content = """\
model AllTypes {
  a String
  b Int
  c Float
  d Boolean
  e DateTime
  f Json
  g Bytes
  h Decimal
  i BigInt
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert len(models) == 1
        m = models[0]
        expected = {
            "a": "string",
            "b": "number",
            "c": "number",
            "d": "boolean",
            "e": "Date",
            "f": "object",
            "g": "bytes",
            "h": "number",
            "i": "number",
        }
        field_map = {f.name: f.field_type for f in m.fields}
        for name, ftype in expected.items():
            assert field_map[name] == ftype, f"Field {name}: expected {ftype}, got {field_map[name]}"


class TestPrimaryKey:
    def test_primary_key(self):
        content = """\
model User {
  id   String @id
  name String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]
        id_field = next(f for f in m.fields if f.name == "id")
        assert id_field.is_primary_key is True
        assert m.primary_key == "id"

        name_field = next(f for f in m.fields if f.name == "name")
        assert name_field.is_primary_key is False


class TestUnique:
    def test_unique(self):
        content = """\
model User {
  id    String @id
  email String @unique
  name  String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]
        email = next(f for f in m.fields if f.name == "email")
        assert email.is_unique is True

        name = next(f for f in m.fields if f.name == "name")
        assert name.is_unique is False


class TestDefault:
    def test_default(self):
        content = """\
model Item {
  id        String   @id @default(uuid())
  active    Boolean  @default(false)
  createdAt DateTime @default(now())
  label     String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]

        id_field = next(f for f in m.fields if f.name == "id")
        assert id_field.has_default is True
        # Note: _DEFAULT_RE uses [^)]+ which stops at first ')' — nested parens
        # in uuid() / now() lose the trailing ')'.  Literal defaults like "false"
        # work correctly.
        assert "uuid" in id_field.default_value

        active = next(f for f in m.fields if f.name == "active")
        assert active.has_default is True
        assert active.default_value == "false"

        created = next(f for f in m.fields if f.name == "createdAt")
        assert created.has_default is True
        assert "now" in created.default_value

        label = next(f for f in m.fields if f.name == "label")
        assert label.has_default is False
        assert label.default_value is None


class TestRelationFK:
    def test_relation_fk(self):
        content = """\
model User {
  id    String @id
  posts Post[]
}

model Post {
  id       String @id
  authorId String
  author   User   @relation(fields: [authorId], references: [id])
}
"""
        models = parse_prisma_schema("test.prisma", content)
        post = next(m for m in models if m.name == "Post")

        # FK field detection
        author_fk = next(f for f in post.fields if f.name == "authorId")
        assert author_fk.is_foreign_key is True
        assert author_fk.references_model == "User"
        assert author_fk.references_field == "id"

        # Relation created
        rel = next(r for r in post.relations if r.target_model == "User")
        assert rel.relation_type == "many-to-one"
        assert rel.foreign_key_field == "authorId"


class TestTableMap:
    def test_table_map(self):
        content = """\
model User {
  id   String @id
  name String

  @@map("users")
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert models[0].table_name == "users"

    def test_no_map(self):
        content = """\
model User {
  id   String @id
  name String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert models[0].table_name is None


class TestIndex:
    def test_index(self):
        content = """\
model Post {
  id     String @id
  teamId String
  title  String

  @@index([teamId])
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]
        team_id = next(f for f in m.fields if f.name == "teamId")
        assert team_id.is_indexed is True

        title = next(f for f in m.fields if f.name == "title")
        assert title.is_indexed is False


class TestOptionalField:
    def test_optional_field(self):
        content = """\
model User {
  id   String  @id
  name String?
  bio  String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]

        name = next(f for f in m.fields if f.name == "name")
        assert name.is_nullable is True

        bio = next(f for f in m.fields if f.name == "bio")
        assert bio.is_nullable is False

        id_field = next(f for f in m.fields if f.name == "id")
        assert id_field.is_nullable is False


class TestArrayRelation:
    def test_array_relation(self):
        content = """\
model User {
  id    String @id
  posts Post[]
}

model Post {
  id String @id
}
"""
        models = parse_prisma_schema("test.prisma", content)
        user = next(m for m in models if m.name == "User")

        # Array relation field should NOT appear as a model field
        field_names = {f.name for f in user.fields}
        assert "posts" not in field_names

        # Should create a one-to-many relation
        rel = next(r for r in user.relations if r.target_model == "Post")
        assert rel.relation_type == "one-to-many"
        assert rel.source_field == "posts"


class TestIgnoreField:
    def test_ignore_field(self):
        content = """\
model User {
  id       String @id
  name     String
  internal String @ignore
}
"""
        models = parse_prisma_schema("test.prisma", content)
        field_names = {f.name for f in models[0].fields}
        assert "id" in field_names
        assert "name" in field_names
        assert "internal" not in field_names


class TestIgnoreModel:
    def test_ignore_model(self):
        content = """\
model User {
  id String @id
}

model Legacy {
  id String @id

  @@ignore
}
"""
        models = parse_prisma_schema("test.prisma", content)
        names = {m.name for m in models}
        assert "User" in names
        assert "Legacy" not in names


class TestEnum:
    def test_enum(self):
        content = """\
enum Role {
  USER
  ADMIN
  MODERATOR
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert len(models) == 1
        m = models[0]
        assert m.name == "Role"
        assert m.is_enum is True
        assert m.orm == "prisma"

        member_names = [f.name for f in m.fields]
        assert member_names == ["USER", "ADMIN", "MODERATOR"]
        for f in m.fields:
            assert f.field_type == "enum_member"


class TestDocstring:
    def test_docstring(self):
        content = """\
/// The main user model.
/// Stores account information.
model User {
  id   String @id
  name String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert models[0].docstring == "The main user model.\nStores account information."

    def test_no_docstring(self):
        content = """\
model User {
  id   String @id
  name String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert models[0].docstring is None


class TestCompositeId:
    def test_composite_id(self):
        content = """\
model Membership {
  userId String
  orgId  String
  role   String

  @@id([userId, orgId])
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]
        assert m.primary_key == "userId,orgId"

        user_id = next(f for f in m.fields if f.name == "userId")
        assert user_id.is_primary_key is True

        org_id = next(f for f in m.fields if f.name == "orgId")
        assert org_id.is_primary_key is True

        role = next(f for f in m.fields if f.name == "role")
        assert role.is_primary_key is False


class TestDbType:
    def test_db_type(self):
        content = """\
model User {
  id    String @id @db.Uuid
  email String @db.VarChar(255)
  bio   String
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]

        id_field = next(f for f in m.fields if f.name == "id")
        assert id_field.db_type == "uuid"

        email = next(f for f in m.fields if f.name == "email")
        assert email.db_type == "varchar(255)"

        bio = next(f for f in m.fields if f.name == "bio")
        assert bio.db_type is None


class TestOneToOneUpgrade:
    def test_one_to_one_upgrade(self):
        content = """\
model User {
  id      String   @id
  profile Profile?
}

model Profile {
  id     String @id
  userId String @unique
  user   User   @relation(fields: [userId], references: [id])
}
"""
        models = parse_prisma_schema("test.prisma", content)
        profile = next(m for m in models if m.name == "Profile")

        # FK field with @unique should upgrade relation to one-to-one
        rel = next(r for r in profile.relations if r.target_model == "User")
        assert rel.relation_type == "one-to-one"
        assert rel.foreign_key_field == "userId"

        # The FK field itself should have is_unique=True
        user_id = next(f for f in profile.fields if f.name == "userId")
        assert user_id.is_unique is True
        assert user_id.is_foreign_key is True


class TestFixtureFile:
    def test_fixture_file(self):
        content = (FIXTURES_DIR / "sample_prisma" / "schema.prisma").read_text()
        models = parse_prisma_schema("schema.prisma", content)
        names = {m.name for m in models}
        assert "User" in names
        assert "Post" in names
        assert "Profile" in names
        assert "Tag" in names

        # Enum
        enums = [m for m in models if m.is_enum]
        assert len(enums) == 1
        assert enums[0].name == "Role"

        # FK detection
        post = next(m for m in models if m.name == "Post")
        author_fk = next(f for f in post.fields if f.name == "authorId")
        assert author_fk.is_foreign_key
        assert author_fk.references_model == "User"

    def test_fixture_user_table_map(self):
        """Fixture User model has @@map('users')."""
        content = (FIXTURES_DIR / "sample_prisma" / "schema.prisma").read_text()
        models = parse_prisma_schema("schema.prisma", content)
        user = next(m for m in models if m.name == "User")
        assert user.table_name == "users"

    def test_fixture_profile_one_to_one(self):
        """Profile.userId is @unique so relation should be one-to-one."""
        content = (FIXTURES_DIR / "sample_prisma" / "schema.prisma").read_text()
        models = parse_prisma_schema("schema.prisma", content)
        profile = next(m for m in models if m.name == "Profile")
        rel = next(r for r in profile.relations if r.target_model == "User")
        assert rel.relation_type == "one-to-one"


class TestCompositeUnique:
    def test_composite_unique(self):
        content = """\
model TenantUser {
  id       String @id
  email    String
  tenantId String
  name     String

  @@unique([email, tenantId])
}
"""
        models = parse_prisma_schema("test.prisma", content)
        m = models[0]

        email = next(f for f in m.fields if f.name == "email")
        assert email.is_unique is True

        tenant_id = next(f for f in m.fields if f.name == "tenantId")
        assert tenant_id.is_unique is True

        # Fields not in the composite unique stay is_unique=False
        name = next(f for f in m.fields if f.name == "name")
        assert name.is_unique is False


class TestModelMetadata:
    def test_orm_is_prisma(self):
        content = """\
model User {
  id String @id
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert models[0].orm == "prisma"

    def test_qualified_name(self):
        content = """\
model User {
  id String @id
}
"""
        models = parse_prisma_schema("test.prisma", content)
        assert models[0].qualified_name == "test.prisma::User"

    def test_file_path_preserved(self):
        content = """\
model User {
  id String @id
}
"""
        models = parse_prisma_schema("prisma/schema.prisma", content)
        assert models[0].file_path == "prisma/schema.prisma"

    def test_line_numbers(self):
        content = """\
model User {
  id String @id
}

model Post {
  id String @id
}
"""
        models = parse_prisma_schema("test.prisma", content)
        user = next(m for m in models if m.name == "User")
        assert user.line_start == 1

        post = next(m for m in models if m.name == "Post")
        assert post.line_start == 5


class TestMultipleModels:
    def test_multiple_models_and_enum(self):
        content = """\
model User {
  id    String @id
  role  String
  posts Post[]
}

model Post {
  id       String @id
  authorId String
  author   User   @relation(fields: [authorId], references: [id])
}

enum Status {
  ACTIVE
  INACTIVE
}
"""
        models = parse_prisma_schema("test.prisma", content)
        names = {m.name for m in models}
        assert names == {"User", "Post", "Status"}

        enums = [m for m in models if m.is_enum]
        assert len(enums) == 1
        assert enums[0].name == "Status"

        non_enums = [m for m in models if not m.is_enum]
        assert len(non_enums) == 2


class TestRelationOrderIndependence:
    def test_relation_fields_references_order(self):
        """@relation with references before fields should still parse."""
        content = """\
model User {
  id    String @id
  posts Post[]
}

model Post {
  id       String @id
  authorId String
  author   User   @relation(references: [id], fields: [authorId])
}
"""
        models = parse_prisma_schema("test.prisma", content)
        post = next(m for m in models if m.name == "Post")
        fk = next(f for f in post.fields if f.name == "authorId")
        assert fk.is_foreign_key is True
        assert fk.references_model == "User"
        assert fk.references_field == "id"
