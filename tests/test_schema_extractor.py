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
