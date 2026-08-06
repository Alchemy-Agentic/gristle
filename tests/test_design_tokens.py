"""Tests for design-token extraction: the CSS custom-property parser (category
inference, @theme vs :root, var() references, comment handling), the TokenExtractor
(Token nodes + File-[:CONTAINS]->Token, per-name dedupe), and the get_tokens query."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.ingestion.token_extractor import TokenExtractor
from gristle.ingestion.walker import WalkedFile
from gristle.parsers.design_tokens import parse_css_tokens

# ---------------------------------------------------------------------------
# CSS custom-property parser
# ---------------------------------------------------------------------------


class TestCssTokenParser:
    _CSS = """
    :root {
      --primary: 215 75% 25%;              /* hsl triplet -> color */
      --brand-hex: #3b82f6;                /* hex -> color */
      --overlay: rgba(0, 0, 0, 0.5);       /* rgba -> color */
      --radius: 0.5rem;                    /* name -> radius */
      --shadow-sm: 0 1px 2px #0000001a;    /* name -> shadow */
      --font-sans: Inter, sans-serif;      /* name -> typography */
      --gap-4: 1rem;                       /* name -> spacing */
      --z-modal: 50;                       /* name + int -> z_index */
      --ease-out: cubic-bezier(0.4, 0, 0.2, 1);  /* name -> animation */
      --misc: 42;                          /* nothing -> other */
    }
    @theme inline {
      --color-primary: hsl(var(--primary));
    }
    """

    def _cats(self):
        return {t.name: t.category for t in parse_css_tokens("x.css", self._CSS)}

    def test_category_inference(self):
        c = self._cats()
        assert c["primary"] == "color"
        assert c["brand-hex"] == "color"
        assert c["overlay"] == "color"
        assert c["radius"] == "radius"
        assert c["shadow-sm"] == "shadow"
        assert c["font-sans"] == "typography"
        assert c["gap-4"] == "spacing"
        assert c["z-modal"] == "z_index"
        assert c["ease-out"] == "animation"
        assert c["misc"] == "other"

    def test_source_kind_root_vs_theme(self):
        by_name = {t.name: t for t in parse_css_tokens("x.css", self._CSS)}
        assert by_name["primary"].source_kind == "css_root"
        assert by_name["color-primary"].source_kind == "css_theme"

    def test_var_references_captured(self):
        by_name = {t.name: t for t in parse_css_tokens("x.css", self._CSS)}
        assert by_name["color-primary"].references == ["--primary"]
        assert by_name["primary"].references == []

    def test_names_strip_double_dash_and_keep_value(self):
        by_name = {t.name: t for t in parse_css_tokens("x.css", self._CSS)}
        assert by_name["primary"].value == "215 75% 25%"
        assert "--" not in by_name["primary"].name

    def test_comment_declarations_ignored_and_lines_aligned(self):
        css = "/*\n--ghost: red;\n*/\n:root {\n  --real: #fff;\n}\n"
        toks = parse_css_tokens("x.css", css)
        names = {t.name for t in toks}
        assert "ghost" not in names  # a `--x` inside a comment is not a token
        assert names == {"real"}
        assert toks[0].line == 5  # comment-blanking preserves newlines -> real line

    def test_empty_and_non_css_safe(self):
        assert parse_css_tokens("x.css", "") == []
        assert parse_css_tokens("x.css", "body { color: red; }") == []  # no custom props

    def test_url_data_uri_value_not_truncated(self):
        # a `;` inside a data-URI must not end the value early
        css = ":root{--icon: url(data:image/svg+xml;base64,PHN2Zy8+);}"
        by_name = {t.name: t for t in parse_css_tokens("x.css", css)}
        assert by_name["icon"].value == "url(data:image/svg+xml;base64,PHN2Zy8+)"

    def test_no_token_fabricated_from_inside_string(self):
        # a `--foo` living inside an inline-SVG string is NOT a real token, and the real
        # `--icon`/`--real` declarations survive the embedded braces
        css = ':root{--icon: url("<svg><style>.a{--foo:red;}</style></svg>"); --real: blue;}'
        names = {t.name for t in parse_css_tokens("x.css", css)}
        assert "foo" not in names
        assert {"icon", "real"} <= names

    def test_comment_marker_inside_string_survives(self):
        css = ':root{--a: "/*"; --b: blue; --c: "*/";}'
        assert {t.name for t in parse_css_tokens("x.css", css)} == {"a", "b", "c"}

    def test_shadow_color_name_is_color(self):
        c = {t.name: t.category for t in parse_css_tokens("x.css", ":root{--shadow-color: #000;}")}
        assert c["shadow-color"] == "color"

    def test_box_shadow_value_is_shadow(self):
        css = ":root{--elevation-1: 0 1px 2px rgba(0,0,0,0.1);}"
        c = {t.name: t.category for t in parse_css_tokens("x.css", css)}
        assert c["elevation-1"] == "shadow"

    def test_zoom_not_misread_as_zindex(self):
        c = {t.name: t.category for t in parse_css_tokens("x.css", ":root{--zoom: 2;}")}
        assert c["zoom"] == "other"


# ---------------------------------------------------------------------------
# TokenExtractor (mock graph client)
# ---------------------------------------------------------------------------


def _graph_mock() -> MagicMock:
    mock = MagicMock()
    mock.batch_create_nodes.return_value = 0
    mock.batch_create_relationships.return_value = 0
    mock.batch_merge_relationships.return_value = 0
    return mock


def _walked(tmp_path, name, content):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return WalkedFile(relative_path=name, absolute_path=str(p), extension="css")


def _walked_rel(tmp_path, rel, content):
    """WalkedFile with an arbitrary relative_path (for path-based filtering/dedupe tests),
    backed by a distinct temp file so overlapping names don't clobber each other."""
    p = tmp_path / rel.replace("/", "_")
    p.write_text(content, encoding="utf-8")
    return WalkedFile(relative_path=rel, absolute_path=str(p), extension="css")


def _nodes_written(graph, label):
    """All node-property dicts written for a given label across flush chunks."""
    rows = []
    for call in graph.batch_create_nodes.call_args_list:
        if call.args[0] == label:
            rows.extend(call.args[1])
    return rows


class TestTokenExtractor:
    def test_writes_token_nodes_file_and_contains(self, tmp_path):
        graph = _graph_mock()
        wf = _walked(tmp_path, "theme.css", ":root{--primary:#fff;--radius:0.5rem;}")
        # no File node exists for a .css file -> extractor must create one
        result = TokenExtractor(graph, file_path_to_id={}).extract([wf], [])

        assert result.tokens_found == 2
        tokens = _nodes_written(graph, "Token")
        assert {t["name"] for t in tokens} == {"primary", "radius"}
        primary = next(t for t in tokens if t["name"] == "primary")
        assert primary["id"] == "token::primary"
        assert primary["raw_name"] == "--primary"
        assert primary["category"] == "color"
        # a minimal File node was synthesized for the unparsed .css file
        files = _nodes_written(graph, "File")
        assert any(f["path"] == "theme.css" and f["language"] == "css" for f in files)
        # CONTAINS edge from that file to each token
        rel_calls = [c for c in graph.batch_create_relationships.call_args_list if c.args[0] == "CONTAINS"]
        contained = [r for call in rel_calls for r in call.args[1]]
        assert len(contained) == 2

    def test_dedupe_first_definition_wins(self, tmp_path):
        graph = _graph_mock()
        # same token name defined light then dark -> one node, light value kept
        wf = _walked(tmp_path, "t.css", ":root{--bg: white;}\n.dark{--bg: black;}")
        result = TokenExtractor(graph, file_path_to_id={}).extract([wf], [])
        tokens = _nodes_written(graph, "Token")
        assert result.tokens_found == 1
        assert len(tokens) == 1
        assert tokens[0]["value"] == "white"

    def test_reuses_existing_file_node(self, tmp_path):
        graph = _graph_mock()
        wf = _walked(tmp_path, "t.css", ":root{--x: #000;}")
        TokenExtractor(graph, file_path_to_id={"t.css": "file::t.css"}).extract([wf], [])
        # File node already known -> none synthesized
        assert _nodes_written(graph, "File") == []

    def test_non_css_files_ignored(self, tmp_path):
        graph = _graph_mock()
        p = tmp_path / "a.ts"
        p.write_text(":root{--x: #000;}", encoding="utf-8")
        wf = WalkedFile(relative_path="a.ts", absolute_path=str(p), extension="ts")
        result = TokenExtractor(graph, file_path_to_id={}).extract([wf], [])
        assert result.tokens_found == 0

    def test_generated_and_vendor_css_skipped(self, tmp_path):
        graph = _graph_mock()
        skipped = [
            _walked_rel(tmp_path, "dist/output.css", ":root{--tw-x: 1;}"),
            _walked_rel(tmp_path, "vendor/bootstrap.min.css", ":root{--bs-y: 2;}"),
        ]
        result = TokenExtractor(graph, file_path_to_id={}).extract(skipped, [])
        assert result.tokens_found == 0

    def test_dedupe_prefers_shallower_path_deterministically(self, tmp_path):
        graph = _graph_mock()
        deep = _walked_rel(tmp_path, "docs/a/b/theme.css", ":root{--c: deep;}")
        shallow = _walked_rel(tmp_path, "src/index.css", ":root{--c: shallow;}")
        # deep first: winner must still be the shallower path, not the walk-order first
        TokenExtractor(graph, file_path_to_id={}).extract([deep, shallow], [])
        tokens = _nodes_written(graph, "Token")
        assert len(tokens) == 1
        assert tokens[0]["value"] == "shallow"

    def test_disabled_via_settings(self, tmp_path, monkeypatch):
        from gristle.config import settings

        monkeypatch.setattr(settings, "design_tokens_enabled", False)
        graph = _graph_mock()
        wf = _walked(tmp_path, "t.css", ":root{--x: #000;}")
        result = TokenExtractor(graph, file_path_to_id={}).extract([wf], [])
        assert result.tokens_found == 0
        graph.batch_create_nodes.assert_not_called()


# ---------------------------------------------------------------------------
# get_tokens query (mock graph, sequential execute side_effect)
# ---------------------------------------------------------------------------


def _qr(records):
    from gristle.graph.client import QueryResult

    return QueryResult(records=records, summary={})


def _engine(side_effect):
    from gristle.query.engine import QueryEngine

    graph = MagicMock()
    graph.execute.side_effect = side_effect
    return QueryEngine(graph, repo_path=None)


class TestGetTokens:
    def test_inventory_shape(self):
        # execute order in get_tokens: by_category, by_source, files, tokens, total
        results = [
            _qr([{"category": "color", "c": 3}, {"category": "radius", "c": 1}]),
            _qr([{"source_kind": "css_root", "c": 3}, {"source_kind": "css_theme", "c": 1}]),
            _qr([{"fp": "src/index.css"}]),
            _qr(
                [
                    {
                        "name": "primary",
                        "category": "color",
                        "value": "215 75% 25%",
                        "source_kind": "css_root",
                        "references": None,
                        "filePath": "src/index.css",
                        "line": 4,
                    }
                ]
            ),
            _qr([{"c": 4}]),  # total (direct count, robust to null-category nodes)
        ]
        report = _engine(results).get_tokens()
        assert report["total_tokens"] == 4
        assert report["by_category"] == {"color": 3, "radius": 1}
        assert report["by_source_kind"] == {"css_root": 3, "css_theme": 1}
        assert report["files"] == ["src/index.css"]
        assert report["count"] == 1
        assert report["tokens"][0]["references"] == []  # null coalesced to []
