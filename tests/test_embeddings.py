"""Tests for gristle.search.embeddings — CodeEmbedder and SemanticIndex."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import numpy as np
import pytest
from redis.exceptions import ResponseError

from gristle.graph.client import QueryResult

# ------------------------------------------------------------------
# CodeEmbedder
# ------------------------------------------------------------------


class TestCodeEmbedder:
    """Tests for CodeEmbedder using a mocked SentenceTransformer."""

    def _make_embedder(self):
        """Create a CodeEmbedder with mocked internals (no real model loaded)."""
        from gristle.search.embeddings import CodeEmbedder

        embedder = CodeEmbedder.__new__(CodeEmbedder)
        embedder._model = MagicMock()
        embedder._dim = 384
        return embedder

    def test_dimension_property(self):
        embedder = self._make_embedder()
        assert embedder.dimension == 384

    def test_embed_text(self):
        embedder = self._make_embedder()
        fake_embedding = np.array([0.1, 0.2, 0.3])
        embedder._model.encode.return_value = fake_embedding
        result = embedder.embed_text("def foo(): pass")
        assert result == [0.1, 0.2, 0.3]
        embedder._model.encode.assert_called_once_with("def foo(): pass", convert_to_tensor=False)

    def test_embed_batch(self):
        embedder = self._make_embedder()
        fake_embeddings = [np.array([0.1, 0.2]), np.array([0.3, 0.4])]
        embedder._model.encode.return_value = fake_embeddings
        result = embedder.embed_batch(["text1", "text2"])
        assert result == [[0.1, 0.2], [0.3, 0.4]]
        embedder._model.encode.assert_called_once_with(
            ["text1", "text2"], batch_size=64, show_progress_bar=False, convert_to_tensor=False
        )

    def test_import_error_without_sentence_transformers(self):
        """CodeEmbedder.__init__ raises ImportError if sentence-transformers is missing."""
        from gristle.search.embeddings import CodeEmbedder

        with (
            patch.dict("sys.modules", {"sentence_transformers": None}),
            patch("builtins.__import__", side_effect=ImportError("No module")),
            pytest.raises(ImportError, match="sentence-transformers is required"),
        ):
            CodeEmbedder()


class TestCodeEmbedderStaticMethods:
    def test_build_function_text_with_docstring(self):
        from gristle.search.embeddings import CodeEmbedder

        result = CodeEmbedder.build_function_text("foo", "def foo(x: int) -> str", "Does stuff")
        assert result == "def foo(x: int) -> str\nDoes stuff"

    def test_build_function_text_without_docstring(self):
        from gristle.search.embeddings import CodeEmbedder

        result = CodeEmbedder.build_function_text("foo", "def foo()", None)
        assert result == "def foo()"

    def test_build_class_text_with_docstring(self):
        from gristle.search.embeddings import CodeEmbedder

        result = CodeEmbedder.build_class_text("Bar", "class Bar(Base)", "A class")
        assert result == "class Bar(Base)\nA class"

    def test_build_class_text_without_docstring(self):
        from gristle.search.embeddings import CodeEmbedder

        result = CodeEmbedder.build_class_text("Bar", "class Bar", None)
        assert result == "class Bar"


# ------------------------------------------------------------------
# SemanticIndex
# ------------------------------------------------------------------


def _make_semantic_index():
    """Create a SemanticIndex with mocked graph and embedder."""
    graph = MagicMock()
    embedder = MagicMock()
    embedder.dimension = 384
    embedder.embed_text.return_value = [0.1] * 384
    embedder.embed_batch.return_value = [[0.1] * 384, [0.2] * 384]

    from gristle.search.embeddings import SemanticIndex

    return SemanticIndex(graph, embedder), graph, embedder


class TestSemanticIndexCreateIndexes:
    def test_creates_function_and_class_indexes(self):
        idx, graph, embedder = _make_semantic_index()
        idx.create_indexes()
        assert graph.execute.call_count == 2
        calls = [c[0][0] for c in graph.execute.call_args_list]
        assert any("Function" in c and "VECTOR INDEX" in c for c in calls)
        assert any("Class" in c and "VECTOR INDEX" in c for c in calls)

    def test_uses_embedder_dimension(self):
        idx, graph, embedder = _make_semantic_index()
        embedder.dimension = 768
        idx.create_indexes()
        query = graph.execute.call_args_list[0][0][0]
        assert "768" in query

    def test_suppresses_response_error(self):
        idx, graph, _ = _make_semantic_index()
        graph.execute.side_effect = ResponseError("Index already exists")
        idx.create_indexes()  # Should not raise


class TestSemanticIndexIndexAll:
    def test_indexes_functions_and_classes(self):
        idx, graph, embedder = _make_semantic_index()
        # First call: Function query, second: Function SET, third: Class query, fourth: Class SET
        graph.execute.side_effect = [
            # Function MATCH query
            QueryResult(
                records=[{"id": "f1", "name": "foo", "signature": "def foo()", "docstring": "Does foo"}],
                summary={},
            ),
            # Function SET embedding
            QueryResult(records=[], summary={}),
            # Class MATCH query
            QueryResult(
                records=[{"id": "c1", "name": "Bar", "signature": "class Bar", "docstring": None}],
                summary={},
            ),
            # Class SET embedding
            QueryResult(records=[], summary={}),
        ]
        embedder.embed_batch.return_value = [[0.1] * 384]

        counts = idx.index_all()
        assert counts["Function"] == 1
        assert counts["Class"] == 1

    def test_no_nodes_returns_zero_counts(self):
        idx, graph, _ = _make_semantic_index()
        graph.execute.side_effect = [
            QueryResult(records=[], summary={}),
            QueryResult(records=[], summary={}),
        ]
        counts = idx.index_all()
        assert counts["Function"] == 0
        assert counts["Class"] == 0

    def test_batches_by_batch_size(self):
        idx, graph, embedder = _make_semantic_index()
        # 3 function nodes, batch_size=2 → 2 embed_batch calls
        records = [{"id": f"f{i}", "name": f"fn{i}", "signature": f"def fn{i}()", "docstring": None} for i in range(3)]
        graph.execute.side_effect = [
            QueryResult(records=records, summary={}),
            # 3 individual SET calls for functions
            QueryResult(records=[], summary={}),
            QueryResult(records=[], summary={}),
            QueryResult(records=[], summary={}),
            # Class query: empty
            QueryResult(records=[], summary={}),
        ]
        embedder.embed_batch.side_effect = [
            [[0.1] * 384, [0.2] * 384],  # first batch of 2
            [[0.3] * 384],  # second batch of 1
        ]

        counts = idx.index_all(batch_size=2)
        assert counts["Function"] == 3
        assert embedder.embed_batch.call_count == 2

    def test_uses_name_as_fallback_when_signature_is_none(self):
        idx, graph, embedder = _make_semantic_index()
        graph.execute.side_effect = [
            QueryResult(
                records=[{"id": "f1", "name": "foo", "signature": None, "docstring": None}],
                summary={},
            ),
            QueryResult(records=[], summary={}),
            QueryResult(records=[], summary={}),
        ]
        embedder.embed_batch.return_value = [[0.1] * 384]
        embedder.build_function_text = lambda name, sig, doc: f"{sig}"

        idx.index_all()
        # The text_builder should receive name as signature fallback
        call_args = embedder.embed_batch.call_args[0][0]
        # signature is None so rec["signature"] or rec["name"] → "foo"
        assert any("foo" in t for t in call_args)


class TestSemanticIndexSearch:
    def test_search_returns_sorted_results(self):
        idx, graph, embedder = _make_semantic_index()
        graph.execute.side_effect = [
            # Function results
            QueryResult(
                records=[
                    {
                        "id": "f1",
                        "name": "validate",
                        "signature": "def validate()",
                        "docstring": None,
                        "file_path": "a.py",
                        "score": 0.3,
                    },
                ],
                summary={},
            ),
            # Class results
            QueryResult(
                records=[
                    {
                        "id": "c1",
                        "name": "Validator",
                        "signature": "class Validator",
                        "docstring": None,
                        "file_path": "b.py",
                        "score": 0.1,
                    },
                ],
                summary={},
            ),
        ]

        results = idx.search("validation logic", limit=10)
        assert len(results) == 2
        # Should be sorted by score ascending
        assert results[0]["score"] == 0.1
        assert results[1]["score"] == 0.3
        # Label should be added
        assert results[0]["label"] == "Class"
        assert results[1]["label"] == "Function"

    def test_search_respects_limit(self):
        idx, graph, embedder = _make_semantic_index()
        graph.execute.side_effect = [
            QueryResult(
                records=[
                    {
                        "id": f"f{i}",
                        "name": f"fn{i}",
                        "signature": "",
                        "docstring": None,
                        "file_path": "a.py",
                        "score": float(i),
                    }
                    for i in range(5)
                ],
                summary={},
            ),
            QueryResult(records=[], summary={}),
        ]
        results = idx.search("query", limit=3)
        assert len(results) == 3

    def test_search_custom_labels(self):
        idx, graph, embedder = _make_semantic_index()
        graph.execute.return_value = QueryResult(records=[], summary={})
        idx.search("test", labels=["Function"])
        # Should only query Function, not Class
        assert graph.execute.call_count == 1
        query = graph.execute.call_args[0][0]
        assert "'Function'" in query

    def test_search_handles_response_error(self):
        idx, graph, embedder = _make_semantic_index()
        graph.execute.side_effect = ResponseError("No vector index")
        results = idx.search("query")
        assert results == []

    def test_search_embeds_query(self):
        idx, graph, embedder = _make_semantic_index()
        graph.execute.return_value = QueryResult(records=[], summary={})
        idx.search("find validators")
        embedder.embed_text.assert_called_once_with("find validators")
