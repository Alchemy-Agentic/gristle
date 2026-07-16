"""Tests for gristle.graph.client — GraphClient and QueryResult."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from redis.exceptions import ResponseError

from gristle.graph.client import GraphClient, QueryResult

# ------------------------------------------------------------------
# QueryResult
# ------------------------------------------------------------------


class TestQueryResult:
    def test_basic_attributes(self):
        qr = QueryResult(records=[{"a": 1}], summary={"nodes_created": 1})
        assert qr.records == [{"a": 1}]
        assert qr.summary == {"nodes_created": 1}

    def test_empty(self):
        qr = QueryResult(records=[], summary={})
        assert qr.records == []
        assert qr.summary == {}


# ------------------------------------------------------------------
# Helpers — mock FalkorDB to avoid real connections
# ------------------------------------------------------------------


def _make_client(repo_id: str = "test-repo") -> tuple[GraphClient, MagicMock]:
    """Create a GraphClient with a fully mocked FalkorDB backend."""
    with patch("gristle.graph.client.FalkorDB") as MockDB:
        mock_db = MockDB.return_value
        mock_graph = MagicMock()
        mock_db.select_graph.return_value = mock_graph
        client = GraphClient(host="localhost", port=6379, repo_id=repo_id)
    return client, mock_graph


def _fake_query_result(
    headers: list[str],
    rows: list[list],
    nodes_created: int = 0,
    relationships_created: int = 0,
    nodes_deleted: int = 0,
    relationships_deleted: int = 0,
):
    """Build a mock FalkorDB result object."""
    result = MagicMock()
    result.header = [[0, h] for h in headers]  # FalkorDB headers are [type_code, name]
    result.result_set = rows
    result.nodes_created = nodes_created
    result.relationships_created = relationships_created
    result.nodes_deleted = nodes_deleted
    result.relationships_deleted = relationships_deleted
    return result


def _empty_result(nodes_created=0, relationships_created=0, nodes_deleted=0, relationships_deleted=0):
    result = MagicMock()
    result.header = []
    result.result_set = []
    result.nodes_created = nodes_created
    result.relationships_created = relationships_created
    result.nodes_deleted = nodes_deleted
    result.relationships_deleted = relationships_deleted
    return result


# ------------------------------------------------------------------
# GraphClient construction
# ------------------------------------------------------------------


class TestGraphClientInit:
    def test_graph_name_derived_from_repo_id(self):
        client, _ = _make_client("my-repo")
        assert client.graph_name == "gristle_my_repo"
        assert client.repo_id == "my-repo"

    def test_password_forwarded(self):
        with patch("gristle.graph.client.FalkorDB") as MockDB:
            GraphClient(host="h", port=1234, repo_id="r", password="pw")
            MockDB.assert_called_once_with(host="h", port=1234, password="pw")


# ------------------------------------------------------------------
# _sanitize_id
# ------------------------------------------------------------------


class TestSanitizeId:
    def test_simple_slug(self):
        assert GraphClient._sanitize_id("my-repo") == "my_repo"

    def test_strips_leading_trailing_underscores(self):
        assert GraphClient._sanitize_id("---foo---") == "foo"

    def test_lowercases(self):
        assert GraphClient._sanitize_id("MyRepo") == "myrepo"

    def test_empty_string_uses_hash(self):
        result = GraphClient._sanitize_id("")
        # Should be a 12-char hex digest
        assert len(result) == 12

    def test_special_chars_only_uses_hash(self):
        result = GraphClient._sanitize_id("!!!")
        assert len(result) == 12

    def test_long_slug_truncated(self):
        long_name = "a" * 100
        result = GraphClient._sanitize_id(long_name)
        assert len(result) <= 48  # 36 + 1 + 8 = 45


# ------------------------------------------------------------------
# repo_id_from_path
# ------------------------------------------------------------------


class TestRepoIdFromPath:
    def test_returns_12_char_hex(self):
        result = GraphClient.repo_id_from_path("/home/user/project")
        assert len(result) == 12
        assert all(c in "0123456789abcdef" for c in result)

    def test_deterministic(self):
        a = GraphClient.repo_id_from_path("/some/path")
        b = GraphClient.repo_id_from_path("/some/path")
        assert a == b

    def test_different_paths_differ(self):
        a = GraphClient.repo_id_from_path("/path/a")
        b = GraphClient.repo_id_from_path("/path/b")
        assert a != b


# ------------------------------------------------------------------
# canonical_repo_path (worktree-aware identity)
# ------------------------------------------------------------------


class TestCanonicalRepoPath:
    """Worktrees map to the main repo's identity; everything else stays itself.

    Without this, an agent running gristle_ingest from each worktree of one repo
    creates one near-identical full graph per worktree, with no cleanup story.
    """

    def test_normal_repo_resolves_to_itself(self, tmp_path):
        repo = tmp_path / "repo"
        (repo / ".git").mkdir(parents=True)  # normal repo: .git is a directory
        assert GraphClient.canonical_repo_path(str(repo)) == str(repo.resolve())

    def test_plain_directory_resolves_to_itself(self, tmp_path):
        assert GraphClient.canonical_repo_path(str(tmp_path)) == str(tmp_path.resolve())

    def test_worktree_maps_to_main_repo(self, tmp_path):
        main = tmp_path / "main-repo"
        (main / ".git" / "worktrees" / "wt-feature").mkdir(parents=True)
        wt = tmp_path / "wt-feature"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'wt-feature'}\n")

        assert GraphClient.canonical_repo_path(str(wt)) == str(main.resolve())
        # ...and therefore the same repo_id / graph as the main checkout
        assert GraphClient.repo_id_from_path(GraphClient.canonical_repo_path(str(wt))) == GraphClient.repo_id_from_path(
            GraphClient.canonical_repo_path(str(main))
        )

    def test_relative_gitdir_resolves_against_worktree(self, tmp_path):
        main = tmp_path / "main-repo"
        (main / ".git" / "worktrees" / "wt").mkdir(parents=True)
        wt = tmp_path / "wt"
        wt.mkdir()
        (wt / ".git").write_text("gitdir: ../main-repo/.git/worktrees/wt\n")

        assert GraphClient.canonical_repo_path(str(wt)) == str(main.resolve())

    def test_submodule_keeps_its_own_identity(self, tmp_path):
        # A submodule's .git file points at .git/modules/<name> — a genuinely
        # different repository, so it must NOT map to the parent.
        parent = tmp_path / "parent"
        (parent / ".git" / "modules" / "sub").mkdir(parents=True)
        sub = parent / "sub"
        sub.mkdir()
        (sub / ".git").write_text(f"gitdir: {parent / '.git' / 'modules' / 'sub'}\n")

        assert GraphClient.canonical_repo_path(str(sub)) == str(sub.resolve())

    def test_pruned_worktree_dir_keeps_its_own_identity(self, tmp_path):
        # Main repo was deleted: the gitdir target no longer exists, so we can't
        # attribute the checkout to anything — fall back to its own path.
        wt = tmp_path / "orphan-wt"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {tmp_path / 'gone' / '.git' / 'worktrees' / 'orphan-wt'}\n")

        assert GraphClient.canonical_repo_path(str(wt)) == str(wt.resolve())

    def test_malformed_git_file_falls_back(self, tmp_path):
        wt = tmp_path / "weird"
        wt.mkdir()
        (wt / ".git").write_text("not a gitdir line at all\n")

        assert GraphClient.canonical_repo_path(str(wt)) == str(wt.resolve())

    def test_worktree_named_worktrees_still_maps(self, tmp_path):
        # gitdir ends .../.git/worktrees/worktrees — matching the LAST bare
        # "worktrees" component would land on the worktree's own name and fail;
        # the scan must find the ".git"/"worktrees" adjacent pair.
        main = tmp_path / "main-repo"
        (main / ".git" / "worktrees" / "worktrees").mkdir(parents=True)
        wt = tmp_path / "worktrees"
        wt.mkdir()
        (wt / ".git").write_text(f"gitdir: {main / '.git' / 'worktrees' / 'worktrees'}\n")

        assert GraphClient.canonical_repo_path(str(wt)) == str(main.resolve())

    def test_undecodable_git_file_falls_back(self, tmp_path):
        # A .git file with invalid UTF-8 must not crash ingestion — the checkout
        # just keeps its own identity.
        wt = tmp_path / "binary"
        wt.mkdir()
        (wt / ".git").write_bytes(b"gitdir: \xff\xfe\x00broken")

        assert GraphClient.canonical_repo_path(str(wt)) == str(wt.resolve())


# ------------------------------------------------------------------
# describe_gristle_graphs (graph lifecycle listing)
# ------------------------------------------------------------------


class TestDescribeGristleGraphs:
    def _result(self, rows):
        r = MagicMock()
        r.result_set = rows
        return r

    def test_reads_snapshot_metadata_per_graph(self):
        client, _ = _make_client()
        client._db.list_graphs.return_value = ["gristle_alpha", "other_graph"]
        g = MagicMock()
        g.query.side_effect = [
            self._result([["D:/projects/alpha", "2026-06-30T10:00:00"]]),  # snapshot
            self._result([[1234]]),  # node count
        ]
        client._db.select_graph = MagicMock(return_value=g)

        entries = client.describe_gristle_graphs()
        assert entries == [
            {
                "repo_id": "alpha",
                "graph": "gristle_alpha",
                "repo_path": "D:/projects/alpha",
                "last_ingested_at": "2026-06-30T10:00:00",
                "nodes": 1234,
            }
        ]

    def test_unreadable_graph_does_not_hide_the_rest(self):
        client, _ = _make_client()
        client._db.list_graphs.return_value = ["gristle_bad", "gristle_good"]

        def select(name):
            g = MagicMock()
            if name == "gristle_bad":
                g.query.side_effect = RuntimeError("boom")
            else:
                g.query.side_effect = [
                    self._result([["D:/projects/good", "2026-06-30T10:00:00"]]),
                    self._result([[7]]),
                ]
            return g

        client._db.select_graph = MagicMock(side_effect=select)

        entries = client.describe_gristle_graphs()
        assert [e["repo_id"] for e in entries] == ["bad", "good"]
        assert entries[0]["repo_path"] is None  # unreadable -> nulls, not an error
        assert entries[1]["nodes"] == 7

    def test_pre_snapshot_graph_reports_nulls(self):
        client, _ = _make_client()
        client._db.list_graphs.return_value = ["gristle_old"]
        g = MagicMock()
        g.query.side_effect = [self._result([]), self._result([[42]])]  # no Snapshot node
        client._db.select_graph = MagicMock(return_value=g)

        entries = client.describe_gristle_graphs()
        assert entries[0]["repo_path"] is None
        assert entries[0]["last_ingested_at"] is None
        assert entries[0]["nodes"] == 42


# ------------------------------------------------------------------
# execute
# ------------------------------------------------------------------


class TestExecute:
    def test_execute_with_results(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _fake_query_result(
            headers=["n.id", "n.name"],
            rows=[["id1", "foo"], ["id2", "bar"]],
            nodes_created=2,
        )
        qr = client.execute("MATCH (n) RETURN n.id, n.name")
        assert len(qr.records) == 2
        assert qr.records[0] == {"n.id": "id1", "n.name": "foo"}
        assert qr.summary["nodes_created"] == 2

    def test_execute_empty_result(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        qr = client.execute("MATCH (n) RETURN n")
        assert qr.records == []

    def test_execute_passes_params(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.execute("MATCH (n) WHERE n.id = $id RETURN n", {"id": "abc"})
        mock_graph.query.assert_called_once_with("MATCH (n) WHERE n.id = $id RETURN n", {"id": "abc"})

    def test_execute_default_params(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.execute("MATCH (n) RETURN n")
        mock_graph.query.assert_called_once_with("MATCH (n) RETURN n", {})

    def test_execute_plain_string_headers(self):
        """FalkorDB may return headers as plain strings instead of [type, name] lists."""
        client, mock_graph = _make_client()
        result = MagicMock()
        result.header = ["col_a", "col_b"]
        result.result_set = [[1, 2]]
        result.nodes_created = 0
        result.relationships_created = 0
        result.nodes_deleted = 0
        result.relationships_deleted = 0
        mock_graph.query.return_value = result
        qr = client.execute("RETURN 1 AS col_a, 2 AS col_b")
        assert qr.records[0] == {"col_a": 1, "col_b": 2}


# ------------------------------------------------------------------
# create_node
# ------------------------------------------------------------------


class TestCreateNode:
    def test_create_node_returns_id(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _fake_query_result(headers=["n.id"], rows=[["file_1"]], nodes_created=1)
        result = client.create_node("File", {"id": "file_1", "path": "a.py"})
        assert result == "file_1"
        call_args = mock_graph.query.call_args
        assert "CREATE (n:File" in call_args[0][0]

    def test_create_node_no_result(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        result = client.create_node("File", {"id": "x"})
        assert result is None


# ------------------------------------------------------------------
# create_relationship
# ------------------------------------------------------------------


class TestCreateRelationship:
    def test_create_relationship_basic(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=1)
        client.create_relationship("id_a", "id_b", "CALLS")
        query = mock_graph.query.call_args[0][0]
        assert "CALLS" in query
        assert "CREATE" in query
        params = mock_graph.query.call_args[0][1]
        assert params["from_id"] == "id_a"
        assert params["to_id"] == "id_b"

    def test_create_relationship_with_properties(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=1)
        client.create_relationship("a", "b", "IMPORTS", properties={"line": 5})
        params = mock_graph.query.call_args[0][1]
        assert params["line"] == 5
        query = mock_graph.query.call_args[0][0]
        assert "line:" in query


# ------------------------------------------------------------------
# merge_relationship
# ------------------------------------------------------------------


class TestMergeRelationship:
    def test_merge_relationship_basic(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.merge_relationship("a", "b", "CALLS")
        query = mock_graph.query.call_args[0][0]
        assert "MERGE" in query
        assert "CALLS" in query

    def test_merge_relationship_with_properties(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.merge_relationship("a", "b", "USES", properties={"kind": "direct"})
        params = mock_graph.query.call_args[0][1]
        assert params["kind"] == "direct"


# ------------------------------------------------------------------
# Batch operations
# ------------------------------------------------------------------


class TestBatchCreateNodes:
    def test_empty_items_returns_zero(self):
        client, _ = _make_client()
        assert client.batch_create_nodes("File", []) == 0

    def test_creates_unwind_query(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(nodes_created=3)
        items = [{"id": "1", "name": "a"}, {"id": "2", "name": "b"}, {"id": "3", "name": "c"}]
        result = client.batch_create_nodes("File", items)
        assert result == 3
        query = mock_graph.query.call_args[0][0]
        assert "UNWIND" in query
        assert "File" in query


class TestBatchCreateRelationships:
    def test_empty_items_returns_zero(self):
        client, _ = _make_client()
        assert client.batch_create_relationships("CALLS", []) == 0

    def test_creates_unwind_create(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=2)
        items = [
            {"from_id": "a", "to_id": "b"},
            {"from_id": "c", "to_id": "d"},
        ]
        result = client.batch_create_relationships("CALLS", items)
        assert result == 2
        query = mock_graph.query.call_args[0][0]
        assert "CREATE" in query
        assert "UNWIND" in query

    def test_with_extra_properties(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=1)
        items = [{"from_id": "a", "to_id": "b", "line": 10}]
        client.batch_create_relationships("CALLS", items)
        query = mock_graph.query.call_args[0][0]
        assert "line:" in query


class TestBatchMergeRelationships:
    def test_empty_items_returns_zero(self):
        client, _ = _make_client()
        assert client.batch_merge_relationships("CALLS", []) == 0

    def test_creates_unwind_merge(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=2)
        items = [
            {"from_id": "a", "to_id": "b"},
            {"from_id": "c", "to_id": "d"},
        ]
        result = client.batch_merge_relationships("CALLS", items)
        assert result == 2
        query = mock_graph.query.call_args[0][0]
        assert "MERGE" in query
        assert "UNWIND" in query


# ------------------------------------------------------------------
# clear / drop
# ------------------------------------------------------------------


class TestClear:
    def test_clear_executes_detach_delete(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(nodes_deleted=5)
        client.clear()
        query = mock_graph.query.call_args[0][0]
        assert "DETACH DELETE" in query


class TestDrop:
    def test_drop_calls_graph_delete(self):
        client, mock_graph = _make_client()
        client.drop()
        mock_graph.delete.assert_called_once()

    def test_drop_swallows_response_error(self):
        client, mock_graph = _make_client()
        mock_graph.delete.side_effect = ResponseError("no such graph")
        client.drop()  # Should not raise

    def test_drop_swallows_connection_error(self):
        client, mock_graph = _make_client()
        mock_graph.delete.side_effect = ConnectionError("unreachable")
        client.drop()  # Should not raise


class TestPing:
    def test_ping_true_when_reachable(self):
        client, _ = _make_client()
        assert client.ping() is True

    def test_ping_false_when_connection_raises(self):
        client, _ = _make_client()
        client._db.connection.ping.side_effect = ConnectionError("refused")
        assert client.ping() is False


class TestRelationshipLabeling:
    """Relationship writes label endpoints by id prefix so FalkorDB uses the
    id index instead of an unlabeled Cartesian-product scan."""

    def test_batch_create_labels_endpoints_from_prefix(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=1)
        client.batch_create_relationships("CONTAINS", [{"from_id": "file::a", "to_id": "func::b"}])
        query = mock_graph.query.call_args[0][0]
        assert "MATCH (a:File), (b:Function)" in query

    def test_unknown_prefix_falls_back_to_unlabeled(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.batch_merge_relationships("CALLS", [{"from_id": "weird::x", "to_id": "y"}])
        query = mock_graph.query.call_args[0][0]
        assert "MATCH (a), (b)" in query

    def test_mixed_endpoint_labels_split_into_separate_queries(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=1)
        result = client.batch_create_relationships(
            "CALLS",
            [
                {"from_id": "func::a", "to_id": "func::b"},
                {"from_id": "func::c", "to_id": "class::d"},
            ],
        )
        assert mock_graph.query.call_count == 2  # one query per (from,to) label group
        assert result == 2

    def test_single_edge_create_is_labeled(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.create_relationship("class::a", "class::b", "INHERITS_FROM")
        query = mock_graph.query.call_args[0][0]
        assert "MATCH (a:Class), (b:Class)" in query


class TestMergeSetsProperties:
    """MERGE must apply props via SET, not in the relationship pattern (where
    FalkorDB treats them as match criteria and mis-binds across UNWIND rows)."""

    def test_batch_merge_uses_set_not_pattern_props(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result(relationships_created=1)
        client.batch_merge_relationships("USES_MODEL", [{"from_id": "func::a", "to_id": "model::M", "access": "read"}])
        q = mock_graph.query.call_args[0][0]
        assert "MATCH (a:Function), (b:Model)" in q  # labels live in the MATCH
        assert "MERGE (a)-[r:USES_MODEL]->(b)" in q
        assert "SET r.access = rel.access" in q
        assert "USES_MODEL {" not in q  # props must NOT be in the MERGE pattern

    def test_single_merge_uses_set(self):
        client, mock_graph = _make_client()
        mock_graph.query.return_value = _empty_result()
        client.merge_relationship("func::a", "model::M", "USES_MODEL", properties={"access": "write"})
        q = mock_graph.query.call_args[0][0]
        assert "SET r.access = $access" in q


class TestGraphDiscovery:
    def test_graph_exists_true(self):
        client, _ = _make_client("my-repo")
        client._db.list_graphs.return_value = [client.graph_name, "gristle_other"]
        assert client.graph_exists() is True

    def test_graph_exists_false(self):
        client, _ = _make_client("my-repo")
        client._db.list_graphs.return_value = ["gristle_other"]
        assert client.graph_exists() is False

    def test_list_gristle_graphs_filters_prefix(self):
        client, _ = _make_client()
        client._db.list_graphs.return_value = ["gristle_a", "gristle_b", "ziggy", "telemetry"]
        assert client.list_gristle_graphs() == ["gristle_a", "gristle_b"]
