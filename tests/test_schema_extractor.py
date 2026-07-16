"""Tests for SchemaExtractor orchestrator."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gristle.ingestion.schema_extractor import SchemaExtractor
from gristle.ingestion.walker import WalkedFile


def _make_graph_mock() -> MagicMock:
    mock = MagicMock()
    mock.batch_create_nodes.return_value = 0
    mock.batch_create_relationships.return_value = 0
    mock.batch_merge_relationships.return_value = 0
    return mock


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def prisma_file(tmp_path):
    content = """\
model User {
  id    String @id @default(uuid())
  email String @unique
  name  String?
}
"""
    p = tmp_path / "schema.prisma"
    p.write_text(content)
    return WalkedFile(
        relative_path="schema.prisma",
        absolute_path=str(p),
        extension="prisma",
    )


@pytest.fixture()
def prisma_file_with_relations(tmp_path):
    content = """\
model User {
  id    String @id @default(uuid())
  email String @unique
  posts Post[]
}

model Post {
  id       String @id @default(uuid())
  title    String
  authorId String
  author   User   @relation(fields: [authorId], references: [id])
}
"""
    p = tmp_path / "schema.prisma"
    p.write_text(content)
    return WalkedFile(
        relative_path="schema.prisma",
        absolute_path=str(p),
        extension="prisma",
    )


@pytest.fixture()
def drizzle_file(tmp_path):
    content = """\
import { pgTable, uuid, varchar, boolean } from 'drizzle-orm/pg-core';

export const users = pgTable('users', {
  id: uuid('id').primaryKey().defaultRandom(),
  email: varchar('email', { length: 255 }).notNull().unique(),
  active: boolean('active').default(true),
});
"""
    p = tmp_path / "schema.ts"
    p.write_text(content)
    return WalkedFile(
        relative_path="schema.ts",
        absolute_path=str(p),
        extension="ts",
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSchemaExtractor:
    def test_prisma_to_nodes(self, prisma_file):
        """Prisma file with models creates Model and ModelField nodes."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        result = extractor.extract([prisma_file])

        assert result.models_found > 0

        labels_created = {call.args[0] for call in graph.batch_create_nodes.call_args_list}
        assert "Model" in labels_created
        assert "ModelField" in labels_created

    def test_drizzle_to_nodes(self, drizzle_file):
        """Drizzle .ts file creates Model and ModelField nodes."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={"schema.ts": "file::schema.ts"})

        result = extractor.extract([drizzle_file])

        assert result.models_found > 0

        labels_created = {call.args[0] for call in graph.batch_create_nodes.call_args_list}
        assert "Model" in labels_created
        assert "ModelField" in labels_created

    def test_references_edges(self, prisma_file_with_relations):
        """Prisma model with FK calls batch_create_relationships with REFERENCES."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        extractor.extract([prisma_file_with_relations])

        rel_types_created = {call.args[0] for call in graph.batch_create_relationships.call_args_list}
        assert "REFERENCES" in rel_types_created

    def test_related_to_edges(self, prisma_file_with_relations):
        """Prisma model with relations calls batch_merge_relationships with RELATED_TO."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        extractor.extract([prisma_file_with_relations])

        merge_rel_types = {call.args[0] for call in graph.batch_merge_relationships.call_args_list}
        assert "RELATED_TO" in merge_rel_types

    def test_links_functions_to_models(self):
        """A function whose call chain hits a model name + read/write verb gets a
        USES_MODEL edge with the right access; verb-less name reuse is ignored."""
        from gristle.models import ParsedFile, ParsedFunction, ParsedModel

        def _fn(name, calls):
            return ParsedFunction(
                name=name,
                qualified_name=f"views.py::{name}",
                file_path="views.py",
                start_line=1,
                end_line=2,
                signature="",
                calls=calls,
            )

        pf = ParsedFile(
            path="views.py",
            language="python",
            functions=[
                _fn("create_article", ["Article.objects.create"]),
                _fn("list_articles", ["Article.objects.filter"]),
                _fn("render", ["Article"]),  # no verb -> no edge
            ],
            classes=[],
            imports=[],
            line_count=2,
        )
        model = ParsedModel(
            name="Article",
            qualified_name="models.py::Article",
            file_path="models.py",
            line_start=1,
            line_end=2,
            orm="django",
        )

        graph = _make_graph_mock()
        ext = SchemaExtractor(graph, file_path_to_id={})
        ext._write_models([model], [pf])

        uses = [c for c in graph.batch_merge_relationships.call_args_list if c.args[0] == "USES_MODEL"]
        assert uses, "expected USES_MODEL edges"
        items = {(i["from_id"], i["access"]) for call in uses for i in call.args[1]}
        assert ("func::views.py::create_article", "write") in items
        assert ("func::views.py::list_articles", "read") in items
        assert not any(fid.endswith("::render") for fid, _ in items)  # verb-less ignored

    def test_links_functions_to_models_via_args(self):
        """A model/table passed as a call *argument* (Drizzle ``db.insert(chat)``,
        SQLAlchemy ``session.query(User)``) gets a USES_MODEL edge. A verb that
        appears only in an argument (``can(create, Document)``) does not."""
        from gristle.models import ParsedFile, ParsedFunction, ParsedModel

        def _fn(name, calls_with_args):
            return ParsedFunction(
                name=name,
                qualified_name=f"db.ts::{name}",
                file_path="db.ts",
                start_line=1,
                end_line=2,
                signature="",
                calls_with_args=calls_with_args,
            )

        pf = ParsedFile(
            path="db.ts",
            language="typescript",
            functions=[
                _fn("saveChat", ["db.insert(chat)"]),  # write verb + arg model
                _fn("loadUser", ["session.query(User)"]),  # read verb + arg model
                _fn("authorize", ["can(create, Document)"]),  # verb is an arg -> no edge
            ],
            classes=[],
            imports=[],
            line_count=2,
        )

        def _model(name):
            return ParsedModel(
                name=name,
                qualified_name=f"schema.ts::{name}",
                file_path="schema.ts",
                line_start=1,
                line_end=2,
                orm="drizzle",
            )

        models = [_model("Chat"), _model("User"), _model("Document")]

        graph = _make_graph_mock()
        ext = SchemaExtractor(graph, file_path_to_id={})
        ext._write_models(models, [pf])

        uses = [c for c in graph.batch_merge_relationships.call_args_list if c.args[0] == "USES_MODEL"]
        items = {(i["from_id"], i["access"]) for call in uses for i in call.args[1]}
        assert ("func::db.ts::saveChat", "write") in items
        assert ("func::db.ts::loadUser", "read") in items
        # The Document model name appears, but the only verb ("create") is an
        # argument, not part of the method name -> no spurious edge.
        assert not any(fid.endswith("::authorize") for fid, _ in items)

    def test_links_typeorm_repository_fields_to_models(self):
        """A method calling ``<field>.<verb>()`` where the field is typed
        ``Repository<Entity>`` links to that entity (by the field's type, not the
        call). A field typed as a non-model class produces no edge."""
        from gristle.models import ParsedClass, ParsedFile, ParsedFunction, ParsedModel

        def _m(name, calls=None, typed_parameters=None):
            return ParsedFunction(
                name=name,
                qualified_name=f"svc.ts::ArticleService.{name}",
                file_path="svc.ts",
                start_line=1,
                end_line=2,
                signature="",
                calls=calls or [],
                typed_parameters=typed_parameters or [],
            )

        cls = ParsedClass(
            name="ArticleService",
            qualified_name="svc.ts::ArticleService",
            file_path="svc.ts",
            start_line=1,
            end_line=20,
            signature="",
            methods=[
                _m(
                    "constructor",
                    typed_parameters=[("articleRepository", "Repository<ArticleEntity>"), ("svc", "SomeService")],
                ),
                _m("findOne", calls=["articleRepository.findOne"]),  # read (camelCase)
                _m("create", calls=["articleRepository.save", "svc.create"]),  # write; svc is not a model
            ],
        )
        pf = ParsedFile(path="svc.ts", language="typescript", classes=[cls], functions=[], imports=[], line_count=20)
        model = ParsedModel(
            name="ArticleEntity",
            qualified_name="entity.ts::ArticleEntity",
            file_path="entity.ts",
            line_start=1,
            line_end=2,
            orm="typeorm",
        )

        graph = _make_graph_mock()
        ext = SchemaExtractor(graph, file_path_to_id={})
        ext._write_models([model], [pf])

        uses = [c for c in graph.batch_merge_relationships.call_args_list if c.args[0] == "USES_MODEL"]
        items = {(i["from_id"], i["access"]) for call in uses for i in call.args[1]}
        assert ("func::svc.ts::ArticleService.findOne", "read") in items
        assert ("func::svc.ts::ArticleService.create", "write") in items
        # svc.create -> SomeService is not a model, so no model edge for that field
        assert ("func::svc.ts::ArticleService.create", "read") not in items

    def test_related_to_props_have_no_nulls(self, prisma_file_with_relations):
        """RELATED_TO props must never be None — FalkorDB cannot MERGE on a null
        property value (a one-to-many relation has no FK/through/source field)."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        extractor.extract([prisma_file_with_relations])

        for call in graph.batch_merge_relationships.call_args_list:
            if call.args[0] != "RELATED_TO":
                continue
            for item in call.args[1]:
                assert None not in item.values(), f"null prop in RELATED_TO item: {item}"

    def test_contains_edges(self, prisma_file):
        """Every model gets a CONTAINS edge from its File node."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        extractor.extract([prisma_file])

        rel_types_created = {call.args[0] for call in graph.batch_create_relationships.call_args_list}
        assert "CONTAINS" in rel_types_created

        # Verify the CONTAINS edge connects a file to the model
        contains_calls = [
            call for call in graph.batch_create_relationships.call_args_list if call.args[0] == "CONTAINS"
        ]
        for call in contains_calls:
            for rel in call.args[1]:
                assert rel["from_id"].startswith("file::")
                assert rel["to_id"].startswith("model::")

    def test_empty_result(self, tmp_path):
        """No schema files in walked_files returns all zeros."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        # A Python file is not a schema file
        p = tmp_path / "main.py"
        p.write_text("print('hello')")
        non_schema = WalkedFile(relative_path="main.py", absolute_path=str(p), extension="py")

        result = extractor.extract([non_schema])

        assert result.models_found == 0
        assert result.fields_found == 0
        assert result.relations_found == 0
        assert result.nodes_created == 0
        assert result.relationships_created == 0

    def test_mixed_prisma_drizzle(self, prisma_file, drizzle_file):
        """Both Prisma and Drizzle files combine models in result."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={"schema.ts": "file::schema.ts"})

        result = extractor.extract([prisma_file, drizzle_file])

        # Prisma contributes User, Drizzle contributes users — at least 2 models
        assert result.models_found >= 2

        # Both should produce Model nodes
        model_node_calls = [call for call in graph.batch_create_nodes.call_args_list if call.args[0] == "Model"]
        total_model_nodes = sum(len(call.args[1]) for call in model_node_calls)
        assert total_model_nodes >= 2

    def test_supabase_types_to_nodes(self, tmp_path):
        """A Supabase generated-types file creates Model/ModelField nodes."""
        content = """\
export type Database = {
  public: {
    Tables: {
      users: {
        Row: {
          id: string
          email: string | null
        }
        Relationships: []
      }
    }
  }
}
"""
        p = tmp_path / "database.types.ts"
        p.write_text(content)
        wf = WalkedFile(relative_path="database.types.ts", absolute_path=str(p), extension="ts")

        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={"database.types.ts": "file::database.types.ts"})
        result = extractor.extract([wf])

        assert result.models_found == 1
        assert result.fields_found == 2
        model_items = [
            i for call in graph.batch_create_nodes.call_args_list if call.args[0] == "Model" for i in call.args[1]
        ]
        assert model_items[0]["orm"] == "supabase"
        assert model_items[0]["table_name"] == "users"

    def test_supabase_duplicate_copies_deduped(self, tmp_path):
        """Repos often hold two copies of the generated file (src/types/ +
        src/integrations/supabase/). One Model per table — prefer the most
        complete copy, keep tables only the other copy has."""

        def _types(tables: list[str]) -> str:
            blocks = "".join(
                f"      {t}: {{\n        Row: {{\n          id: string\n        }}\n        Relationships: []\n      }}\n"
                for t in tables
            )
            return f"export type Database = {{\n  public: {{\n    Tables: {{\n{blocks}    }}\n  }}\n}}\n"

        old = tmp_path / "old.types.ts"
        old.write_text(_types(["users", "legacy_only"]))
        new = tmp_path / "new.types.ts"
        new.write_text(_types(["users", "executions", "profiles"]))
        wfs = [
            WalkedFile(relative_path="old.types.ts", absolute_path=str(old), extension="ts"),
            WalkedFile(relative_path="new.types.ts", absolute_path=str(new), extension="ts"),
        ]

        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})
        result = extractor.extract(wfs)

        # 3 from the bigger copy + legacy_only from the smaller; users NOT doubled.
        assert result.models_found == 4
        model_items = [
            i for call in graph.batch_create_nodes.call_args_list if call.args[0] == "Model" for i in call.args[1]
        ]
        names = [i["name"] for i in model_items]
        assert sorted(names) == ["executions", "legacy_only", "profiles", "users"]
        # users comes from the most complete copy
        users = next(i for i in model_items if i["name"] == "users")
        assert users["file_path"] == "new.types.ts"

    def test_links_functions_to_supabase_tables(self):
        """The quoted `verb.from('table')` descriptor links to the Supabase
        model with the right access; an unknown table links to nothing."""
        from gristle.models import ParsedFile, ParsedFunction, ParsedModel

        def _fn(name, calls_with_args):
            return ParsedFunction(
                name=name,
                qualified_name=f"api.ts::{name}",
                file_path="api.ts",
                start_line=1,
                end_line=2,
                signature="",
                calls_with_args=calls_with_args,
            )

        pf = ParsedFile(
            path="api.ts",
            language="typescript",
            functions=[
                _fn("loadExecutions", ["select.from('executions')"]),
                _fn("renameProfile", ["update.from('profiles')"]),
                _fn("dropRun", ["delete.from('runs')"]),
                _fn("touchUnknown", ["insert.from('not_a_table')"]),
            ],
            classes=[],
            imports=[],
            line_count=2,
        )

        def _table(name):
            return ParsedModel(
                name=name,
                qualified_name=f"database.types.ts::public.{name}",
                file_path="database.types.ts",
                line_start=1,
                line_end=2,
                orm="supabase",
                table_name=name,
            )

        graph = _make_graph_mock()
        ext = SchemaExtractor(graph, file_path_to_id={})
        ext._write_models([_table("executions"), _table("profiles"), _table("runs")], [pf])

        uses = [c for c in graph.batch_merge_relationships.call_args_list if c.args[0] == "USES_MODEL"]
        items = {(i["from_id"], i["to_id"], i["access"]) for call in uses for i in call.args[1]}
        assert ("func::api.ts::loadExecutions", "model::database.types.ts::executions", "read") in items
        assert ("func::api.ts::renameProfile", "model::database.types.ts::profiles", "write") in items
        assert ("func::api.ts::dropRun", "model::database.types.ts::runs", "write") in items
        assert not any(fid.endswith("::touchUnknown") for fid, _, _ in items)

    def test_supabase_tables_never_match_plain_calls(self):
        """THE precision guard: lowercase table names collide with ordinary
        variable names (`executions.filter(...)` on an array), so Supabase
        tables must never enter the general token map — only the quoted
        descriptor links them. Non-supabase models keep matching plain calls."""
        from gristle.models import ParsedFile, ParsedFunction, ParsedModel

        pf = ParsedFile(
            path="ui.tsx",
            language="typescript",
            functions=[
                ParsedFunction(
                    name="renderList",
                    qualified_name="ui.tsx::renderList",
                    file_path="ui.tsx",
                    start_line=1,
                    end_line=2,
                    signature="",
                    # An array variable that happens to share the table's name.
                    calls=["executions.filter", "executions.find"],
                    calls_with_args=["session.query(Article)"],
                ),
            ],
            classes=[],
            imports=[],
            line_count=2,
        )
        supabase_table = ParsedModel(
            name="executions",
            qualified_name="database.types.ts::public.executions",
            file_path="database.types.ts",
            line_start=1,
            line_end=2,
            orm="supabase",
            table_name="executions",
        )
        orm_model = ParsedModel(
            name="Article",
            qualified_name="models.py::Article",
            file_path="models.py",
            line_start=1,
            line_end=2,
            orm="django",
        )

        graph = _make_graph_mock()
        ext = SchemaExtractor(graph, file_path_to_id={})
        ext._write_models([supabase_table, orm_model], [pf])

        uses = [c for c in graph.batch_merge_relationships.call_args_list if c.args[0] == "USES_MODEL"]
        items = {(i["from_id"], i["to_id"]) for call in uses for i in call.args[1]}
        # The ORM model still links via the general path...
        assert ("func::ui.tsx::renderList", "model::models.py::Article") in items
        # ...but the array variable never links to the Supabase table.
        assert not any(tid.endswith("::executions") for _, tid in items)

    def test_counts_accuracy(self, prisma_file_with_relations):
        """Verify models_found, fields_found, relations_found match actual data."""
        graph = _make_graph_mock()
        extractor = SchemaExtractor(graph, file_path_to_id={})

        result = extractor.extract([prisma_file_with_relations])

        # The schema has 2 models: User and Post
        assert result.models_found == 2

        # User: id, email (scalars — 2 fields; posts is a relation, not a scalar)
        # Post: id, title, authorId (scalars — 3 fields; author is a relation)
        assert result.fields_found == 5

        # User has a one-to-many to Post (posts Post[]),
        # Post has a many-to-one to User (author User @relation(...))
        assert result.relations_found == 2

        # nodes_created: 2 Model + 5 ModelField + 1 File (synthetic for prisma) = 8
        assert result.nodes_created == 8

        # relationships_created:
        #   2 CONTAINS (file→model)
        #   + 5 HAS_MODEL_FIELD (model→field)
        #   + 1 REFERENCES (Post.authorId FK → User model)
        #   + 2 RELATED_TO (User→Post one-to-many, Post→User many-to-one)
        # Total: 2 + 5 + 1 + 2 = 10
        assert result.relationships_created == 10
