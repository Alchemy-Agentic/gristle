"""Tests for QueryEngine.get_subgraph and the gristle_subgraph MCP tool.

All against the mock graph client (no FalkorDB). get_subgraph issues exactly one
execute() call, so the tests mock its return and assert the shaping/serializer
contract (§13 of docs/graph-visualization-spec.md).
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gristle.graph.client import QueryResult
from gristle.query.engine import QueryEngine


def _qr(records: list[dict]) -> QueryResult:
    return QueryResult(records=records, summary={})


def _make_engine(execute_result: QueryResult | None = None):
    graph = MagicMock()
    graph.repo_id = "test"
    if execute_result is not None:
        graph.execute.return_value = execute_result
    return QueryEngine(graph), graph


def _node(id_: str, label: str = "Function", **props) -> dict:
    props.setdefault("name", id_.split("::")[-1])
    return {"id": id_, "label": label, "props": props}


# ======================================================================
# Input validation / dispatch
# ======================================================================


class TestSubgraphValidation:
    def test_unknown_view_errors_without_querying(self):
        engine, graph = _make_engine()
        result = engine.get_subgraph("not_a_view", center="func::x")
        assert "error" in result
        graph.execute.assert_not_called()

    def test_non_integer_depth_errors(self):
        engine, graph = _make_engine()
        result = engine.get_subgraph("call_hierarchy", center="func::x", depth="2")  # type: ignore[arg-type]
        assert "error" in result
        graph.execute.assert_not_called()

    def test_bool_depth_rejected(self):
        # bool is an int subclass — must not be accepted as a depth.
        engine, graph = _make_engine()
        result = engine.get_subgraph("call_hierarchy", center="func::x", depth=True)  # type: ignore[arg-type]
        assert "error" in result


# ======================================================================
# Query construction (depth clamp, param safety, edge whitelist)
# ======================================================================


class TestSubgraphQuery:
    def test_depth_clamped_and_interpolated(self):
        engine, graph = _make_engine(_qr([{"nodes": [], "edges": []}]))
        engine.get_subgraph("call_hierarchy", center="func::x", depth=99)
        query = graph.execute.call_args.args[0]
        assert "*1..4" in query  # clamped to MAX_DEPTH
        assert "*1..99" not in query

    def test_depth_floor(self):
        engine, graph = _make_engine(_qr([{"nodes": [], "edges": []}]))
        engine.get_subgraph("call_hierarchy", center="func::x", depth=0)
        assert "*1..1" in graph.execute.call_args.args[0]

    def test_center_passed_as_param_never_interpolated(self):
        engine, graph = _make_engine(_qr([{"nodes": [], "edges": []}]))
        nasty = "func::x' OR 1=1 //"
        engine.get_subgraph("call_hierarchy", center=nasty)
        query, params = graph.execute.call_args.args[0], graph.execute.call_args.args[1]
        assert params["center"] == nasty
        assert nasty not in query  # the literal must not reach the query text

    def test_default_edge_types_per_view(self):
        engine, graph = _make_engine(_qr([{"nodes": [], "edges": []}]))
        engine.get_subgraph("blast_radius", center="func::x")
        params = graph.execute.call_args.args[1]
        assert params["edge_types"] == ["CALLS", "TESTS_FUNCTION", "HANDLES"]

    def test_edge_types_override_respected(self):
        engine, graph = _make_engine(_qr([{"nodes": [], "edges": []}]))
        engine.get_subgraph("request_trace", center=None, edge_types=["HANDLES"])
        assert graph.execute.call_args.args[1]["edge_types"] == ["HANDLES"]


# ======================================================================
# Serializer / finalize contract
# ======================================================================


class TestSubgraphFinalize:
    def test_props_trimmed_to_allowlist(self):
        node = _node("func::a.py::f", complexity=3, secret_internal="x", name="f")
        engine, _ = _make_engine(_qr([{"nodes": [node], "edges": []}]))
        out = engine.get_subgraph("call_hierarchy", center="func::a.py::f")
        props = out["nodes"][0]["props"]
        assert "name" in props and "complexity" in props
        assert "secret_internal" not in props  # not in Function allowlist

    def test_label_from_returned_label_not_prefix(self):
        # Variable nodes are absent from the id-prefix map; the label must come from
        # the Cypher labels(n)[0] value we pass through, not prefix decoding.
        node = {"id": "var::config.ts::cfg", "label": "Variable", "props": {"name": "cfg", "kind": "const", "bogus": 1}}
        engine, _ = _make_engine(_qr([{"nodes": [node], "edges": []}]))
        out = engine.get_subgraph("call_hierarchy", center="x")
        assert out["nodes"][0]["label"] == "Variable"
        assert "bogus" not in out["nodes"][0]["props"]  # trimmed to Variable allowlist
        assert out["nodes"][0]["props"]["kind"] == "const"

    def test_isolated_center_no_edges(self):
        node = _node("func::a.py::f")
        engine, _ = _make_engine(_qr([{"nodes": [node], "edges": []}]))
        out = engine.get_subgraph("call_hierarchy", center="func::a.py::f")
        assert len(out["nodes"]) == 1
        assert out["edges"] == []
        assert out["meta"]["truncated"] is False

    def test_empty_records_returns_empty_graph(self):
        engine, _ = _make_engine(_qr([]))  # center not found -> no records
        out = engine.get_subgraph("call_hierarchy", center="func::missing")
        assert out["nodes"] == []
        assert out["edges"] == []
        assert out["meta"]["node_count"] == 0

    def test_null_edge_metadata_dropped(self):
        nodes = [_node("func::a"), _node("func::b")]
        edges = [
            {
                "source": "func::a",
                "target": "func::b",
                "type": "CALLS",
                "resolution": None,
                "access": None,
                "context": None,
            },
            {
                "source": "func::b",
                "target": "func::a",
                "type": "CALLS",
                "resolution": "exact",
                "access": None,
                "context": None,
            },
        ]
        engine, _ = _make_engine(_qr([{"nodes": nodes, "edges": edges}]))
        out = engine.get_subgraph("call_hierarchy", center="func::a")
        e0, e1 = out["edges"]
        assert "resolution" not in e0  # was null -> dropped
        assert e1["resolution"] == "exact"  # present -> kept
        assert all("access" not in e for e in out["edges"])

    def test_meta_shape(self):
        engine, _ = _make_engine(_qr([{"nodes": [_node("func::a")], "edges": []}]))
        meta = engine.get_subgraph("request_trace", center=None)["meta"]
        for key in (
            "view",
            "kind",
            "repo_id",
            "center",
            "depth",
            "edge_types",
            "node_count",
            "edge_count",
            "truncated",
            "limit",
            "layout_hint",
            "generated_with",
        ):
            assert key in meta
        assert meta["view"] == "request_trace"
        assert meta["kind"] == "node_link"
        assert meta["repo_id"] == "test"
        assert meta["layout_hint"] == "dagre-lr"


# ======================================================================
# Truncation (§4.8)
# ======================================================================


class TestSubgraphTruncation:
    def test_truncation_keeps_center_and_drops_dangling_edges(self):
        # 4 nodes, limit 2. Center has degree 0; two hubs have edges. Center must
        # survive (forced-keep) and surviving edges must reference only kept nodes.
        nodes = [_node("func::center"), _node("func::hub1"), _node("func::hub2"), _node("func::leaf")]
        edges = [
            {"source": "func::hub1", "target": "func::hub2", "type": "CALLS"},
            {"source": "func::hub2", "target": "func::leaf", "type": "CALLS"},
            {"source": "func::leaf", "target": "func::hub1", "type": "CALLS"},
        ]
        engine, _ = _make_engine(_qr([{"nodes": nodes, "edges": edges}]))
        out = engine.get_subgraph("call_hierarchy", center="func::center", limit=2)
        ids = {n["id"] for n in out["nodes"]}
        assert out["meta"]["truncated"] is True
        assert len(out["nodes"]) == 2
        assert "func::center" in ids  # center force-kept
        for e in out["edges"]:
            assert e["source"] in ids and e["target"] in ids  # no dangling edges

    def test_truncation_pins_routes_and_models(self):
        # request_trace must not truncate away the Routes/Models it exists to show,
        # even though they are low-degree vs hub functions. limit=3.
        nodes = [
            _node("route::GET/a", label="Route", path="/a"),
            _node("model::User", label="Model"),
            _node("func::hub1"),
            _node("func::hub2"),
            _node("func::hub3"),
        ]
        edges = [
            {"source": "func::hub1", "target": "func::hub2", "type": "CALLS"},
            {"source": "func::hub2", "target": "func::hub3", "type": "CALLS"},
            {"source": "func::hub3", "target": "func::hub1", "type": "CALLS"},
            {"source": "route::GET/a", "target": "func::hub1", "type": "HANDLES"},
            {"source": "func::hub1", "target": "model::User", "type": "USES_MODEL"},
        ]
        engine, _ = _make_engine(_qr([{"nodes": nodes, "edges": edges}]))
        out = engine.get_subgraph("request_trace", center=None, limit=3)
        ids = {n["id"] for n in out["nodes"]}
        assert out["meta"]["truncated"] is True
        assert len(out["nodes"]) == 3
        assert "route::GET/a" in ids  # anchor pinned despite degree 1
        assert "model::User" in ids  # anchor pinned despite degree 1

    def test_no_truncation_under_limit(self):
        nodes = [_node("func::a"), _node("func::b")]
        engine, _ = _make_engine(_qr([{"nodes": nodes, "edges": []}]))
        out = engine.get_subgraph("call_hierarchy", center="func::a", limit=10)
        assert out["meta"]["truncated"] is False
        assert len(out["nodes"]) == 2


# ======================================================================
# MCP tool
# ======================================================================


@pytest.fixture(autouse=True)
def _clean_mcp_state():
    import gristle.mcp.server as srv

    orig = srv._engines.copy()
    srv._engines.clear()
    yield
    srv._engines.clear()
    srv._engines.update(orig)


class TestMCPSubgraph:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_subgraph

        result = await gristle_subgraph(view="call_hierarchy", center="func::x")
        assert "error" in result

    @pytest.mark.asyncio
    async def test_delegates_to_engine(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_subgraph

        engine = MagicMock()
        engine.get_subgraph.return_value = {"meta": {"view": "call_hierarchy"}, "nodes": [], "edges": []}
        srv._engines["r1"] = engine
        result = await gristle_subgraph(view="call_hierarchy", center="func::x", depth=3)
        assert result["meta"]["view"] == "call_hierarchy"
        engine.get_subgraph.assert_called_once_with(view="call_hierarchy", center="func::x", depth=3, edge_types=None)
