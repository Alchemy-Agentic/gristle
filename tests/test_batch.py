"""Tests for BatchCollector and GraphClient batch methods."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.ingestion.batch import BatchCollector


def _make_graph_mock() -> MagicMock:
    mock = MagicMock()
    mock.batch_create_nodes.return_value = 0
    mock.batch_create_relationships.return_value = 0
    mock.batch_merge_relationships.return_value = 0
    return mock


class TestBatchCollector:
    def test_empty_flush(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)
        counts = batch.flush()
        assert counts == {"nodes_created": 0, "relationships_created": 0}
        graph.batch_create_nodes.assert_not_called()
        graph.batch_create_relationships.assert_not_called()
        graph.batch_merge_relationships.assert_not_called()

    def test_groups_nodes_by_label(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)

        batch.add_node("Function", {"id": "f1", "name": "foo"})
        batch.add_node("Function", {"id": "f2", "name": "bar"})
        batch.add_node("Class", {"id": "c1", "name": "Baz"})

        counts = batch.flush()
        assert counts["nodes_created"] == 3

        # One call per label
        assert graph.batch_create_nodes.call_count == 2
        labels_called = {c.args[0] for c in graph.batch_create_nodes.call_args_list}
        assert labels_called == {"Function", "Class"}

    def test_chunks_at_batch_size(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=2)

        for i in range(5):
            batch.add_node("Function", {"id": f"f{i}", "name": f"fn{i}"})

        batch.flush()

        # 5 items / batch_size 2 = 3 calls (2, 2, 1)
        assert graph.batch_create_nodes.call_count == 3
        sizes = [len(c.args[1]) for c in graph.batch_create_nodes.call_args_list]
        assert sizes == [2, 2, 1]

    def test_flush_order_nodes_before_rels(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)

        batch.add_node("Function", {"id": "f1", "name": "foo"})
        batch.add_relationship("CONTAINS", "file1", "f1")

        call_order = []
        graph.batch_create_nodes.side_effect = lambda *a: call_order.append("nodes")
        graph.batch_create_relationships.side_effect = lambda *a: call_order.append("rels")

        batch.flush()
        assert call_order == ["nodes", "rels"]

    def test_create_vs_merge_relationships(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)

        batch.add_relationship("CONTAINS", "a", "b")
        batch.add_merge_relationship("CALLS", "c", "d")

        counts = batch.flush()
        assert counts["relationships_created"] == 2

        graph.batch_create_relationships.assert_called_once()
        graph.batch_merge_relationships.assert_called_once()

        create_args = graph.batch_create_relationships.call_args
        assert create_args.args[0] == "CONTAINS"
        assert create_args.args[1] == [{"from_id": "a", "to_id": "b"}]

        merge_args = graph.batch_merge_relationships.call_args
        assert merge_args.args[0] == "CALLS"
        assert merge_args.args[1] == [{"from_id": "c", "to_id": "d"}]

    def test_relationship_with_properties(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)

        batch.add_relationship("HANDLES", "r1", "f1", {"weight": 1})
        batch.flush()

        items = graph.batch_create_relationships.call_args.args[1]
        assert items == [{"from_id": "r1", "to_id": "f1", "weight": 1}]

    def test_pending_count(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)

        assert batch.pending_count == 0
        batch.add_node("Function", {"id": "f1"})
        batch.add_relationship("CONTAINS", "a", "b")
        batch.add_merge_relationship("CALLS", "c", "d")
        assert batch.pending_count == 3

        batch.flush()
        assert batch.pending_count == 0

    def test_flush_clears_buffers(self):
        graph = _make_graph_mock()
        batch = BatchCollector(graph, batch_size=100)

        batch.add_node("Function", {"id": "f1", "name": "foo"})
        batch.flush()

        # Second flush should be a no-op
        counts = batch.flush()
        assert counts == {"nodes_created": 0, "relationships_created": 0}
        # Only one call total (from first flush)
        assert graph.batch_create_nodes.call_count == 1
