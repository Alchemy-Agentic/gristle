"""Tests for the self-contained HTML renderer (``gristle.viz.render``).

Pure function over a ``get_subgraph`` payload — no FalkorDB, runs in CI. Guards
the two things that must never regress: (1) exports are self-contained (no network
at view time) and (2) repo-derived strings cannot inject markup or break out of
the inline ``<script>``.
"""

from __future__ import annotations

import re

from gristle.viz import render_html


def _payload(nodes: list[dict], edges: list[dict], **meta: object) -> dict:
    m = {"view": "call_hierarchy", "kind": "node_link", "repo_id": "t", "layout_hint": "dagre-tb"}
    m.update(meta)
    return {"meta": m, "nodes": nodes, "edges": edges}


def _func(id_: str, name: str | None = None) -> dict:
    return {"id": id_, "label": "Function", "props": {"name": name or id_.split("::")[-1]}}


def test_self_contained_no_network():
    html = render_html(_payload([_func("func::a")], []))
    assert "cytoscape" in html.lower()
    assert "const DATA =" in html
    # nothing is fetched over the network when the file is opened
    assert not re.search(r'(?:src|href)\s*=\s*["\']https?://', html)


def test_renderer_assets_inlined():
    html = render_html(_payload([], []))
    assert "Cytoscape Consortium" in html  # cytoscape header present
    assert "dagre" in html.lower()


def test_script_breakout_is_escaped():
    # An identifier containing </script> must NOT terminate the inline <script>.
    nodes = [_func("func::x", "</script><img src=x onerror=alert(1)>")]
    html = render_html(_payload(nodes, []))
    data_block = html.split("const DATA =", 1)[1]
    assert "</script><img" not in data_block  # raw breakout sequence absent
    assert "<\\/script>" in data_block  # escaped form present instead
    # exactly the 4 legitimate closing tags (3 vendored scripts + 1 app script)
    assert html.count("</script>") == 4


def test_title_is_html_escaped():
    html = render_html(_payload([], []), title="<b>pwn</b>")
    assert "<title><b>pwn</b></title>" not in html
    assert "&lt;b&gt;pwn&lt;/b&gt;" in html


def test_data_roundtrips_into_document():
    html = render_html(_payload([_func("func::foo", "foo")], []))
    assert "func::foo" in html  # the node id survives into the inlined DATA


def test_handles_empty_graph():
    html = render_html(_payload([], []))
    assert "const DATA =" in html  # renders a valid (empty) document, no crash
