"""Tests for gristle.graph.schema — index creation."""

from __future__ import annotations

from unittest.mock import MagicMock, call

from redis.exceptions import ResponseError

from gristle.graph.schema import _FULLTEXT_INDEXES, _INDEXES, ensure_schema


def _make_mock_client():
    return MagicMock()


class TestEnsureSchema:
    def test_creates_all_regular_indexes(self):
        client = _make_mock_client()
        ensure_schema(client)
        # Count CREATE INDEX calls
        index_calls = [
            c for c in client.execute.call_args_list if "CREATE INDEX" in str(c)
        ]
        assert len(index_calls) == len(_INDEXES)

    def test_creates_fulltext_indexes(self):
        client = _make_mock_client()
        ensure_schema(client)
        ft_calls = [
            c for c in client.execute.call_args_list if "fulltext.createNodeIndex" in str(c)
        ]
        assert len(ft_calls) == len(_FULLTEXT_INDEXES)

    def test_total_execute_calls(self):
        client = _make_mock_client()
        ensure_schema(client)
        assert client.execute.call_count == len(_INDEXES) + len(_FULLTEXT_INDEXES)

    def test_suppresses_response_error_on_regular_index(self):
        client = _make_mock_client()
        client.execute.side_effect = ResponseError("Index already exists")
        # Should not raise
        ensure_schema(client)

    def test_continues_after_error(self):
        """Even if one index creation fails, the rest should be attempted."""
        client = _make_mock_client()
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise ResponseError("already exists")

        client.execute.side_effect = side_effect
        ensure_schema(client)
        # All indexes should still be attempted
        assert call_count == len(_INDEXES) + len(_FULLTEXT_INDEXES)

    def test_index_query_format(self):
        client = _make_mock_client()
        ensure_schema(client)
        first_call = client.execute.call_args_list[0]
        query = first_call[0][0]
        label, prop = _INDEXES[0]
        assert f"CREATE INDEX FOR (n:{label}) ON (n.{prop})" == query

    def test_fulltext_query_format(self):
        client = _make_mock_client()
        ensure_schema(client)
        # Fulltext indexes come after regular indexes
        ft_idx = len(_INDEXES)
        ft_call = client.execute.call_args_list[ft_idx]
        query = ft_call[0][0]
        idx_name, label, prop = _FULLTEXT_INDEXES[0]
        assert f"CALL db.idx.fulltext.createNodeIndex('{label}', '{prop}')" == query
