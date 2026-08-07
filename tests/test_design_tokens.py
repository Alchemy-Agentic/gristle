"""Tests for design-token extraction: the CSS custom-property parser (category
inference, @theme vs :root, var() references, comment handling), the TokenExtractor
(Token nodes + File-[:CONTAINS]->Token, per-name dedupe), and the get_tokens query."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.ingestion.token_extractor import TokenExtractor
from gristle.ingestion.walker import WalkedFile
from gristle.models import ParsedFile, ParsedFunction
from gristle.parsers.design_tokens import parse_css_tokens, resolve_utility_class
from gristle.parsers.typescript import TypeScriptParser

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
        # execute order: by_category, by_source, files, tokens, total, total_uses, unused
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
                        "used_by": 12,
                    }
                ]
            ),
            _qr([{"c": 4}]),  # total (direct count, robust to null-category nodes)
            _qr([{"c": 30}]),  # total_uses
            _qr([{"c": 1}]),  # unused_count
        ]
        report = _engine(results).get_tokens()
        assert report["total_tokens"] == 4
        assert report["total_uses"] == 30
        assert report["unused_count"] == 1
        assert report["by_category"] == {"color": 3, "radius": 1}
        assert report["by_source_kind"] == {"css_root": 3, "css_theme": 1}
        assert report["files"] == ["src/index.css"]
        assert report["count"] == 1
        assert report["tokens"][0]["references"] == []  # null coalesced to []
        assert report["tokens"][0]["used_by"] == 12


# ---------------------------------------------------------------------------
# USES_TOKEN — className/var capture, class->token resolution, edge emission
# ---------------------------------------------------------------------------


class TestStyleUsageCapture:
    def test_parser_captures_literal_classes_and_vars_skips_dynamic(self):
        src = (
            "export function Card() {\n"
            '  const s = { color: "var(--accent)" };\n'
            '  return <div className="bg-primary text-muted-foreground p-4" style={s}>\n'
            '    <span className={cn("dynamic", x)}>skip</span>\n'
            '    <p className="rounded-lg">hi</p>\n'
            "  </div>;\n"
            "}\n"
        )
        fn = TypeScriptParser().parse_file("Card.tsx", src).functions[0]
        assert fn.style_class_uses == ["bg-primary", "text-muted-foreground", "p-4", "rounded-lg"]
        assert fn.token_var_uses == ["--accent"]  # dynamic className={cn(...)} not captured

    def test_var_in_comment_not_captured(self):
        src = (
            "export function F() {\n"
            "  // switch to var(--ghost) later\n"
            '  const s = { color: "var(--real)" };\n'
            "  return <div style={s} />;\n"
            "}\n"
        )
        fn = TypeScriptParser().parse_file("F.tsx", src).functions[0]
        assert fn.token_var_uses == ["--real"]  # var() inside a comment is ignored


class TestResolveUtilityClass:
    _TOKENS = {"color-primary", "primary", "color-muted-foreground", "text-lg", "border", "radius-lg"}

    def test_color_utility_resolves_to_theme_token(self):
        assert resolve_utility_class("bg-primary", self._TOKENS) == "color-primary"
        assert resolve_utility_class("text-muted-foreground", self._TOKENS) == "color-muted-foreground"

    def test_text_ambiguity_resolves_by_which_token_exists(self):
        assert resolve_utility_class("text-lg", self._TOKENS) == "text-lg"  # font-size token
        assert resolve_utility_class("text-primary", self._TOKENS) == "color-primary"  # color token

    def test_variant_opacity_important_stripped(self):
        assert resolve_utility_class("hover:bg-primary", self._TOKENS) == "color-primary"
        assert resolve_utility_class("dark:md:bg-primary/50", self._TOKENS) == "color-primary"
        assert resolve_utility_class("!bg-primary", self._TOKENS) == "color-primary"

    def test_bare_utility_is_not_a_token_use(self):
        # `border` (width utility) must NOT match the `--border` color token
        assert resolve_utility_class("border", self._TOKENS) is None
        assert resolve_utility_class("rounded", self._TOKENS) is None

    def test_arbitrary_value_and_stock_classes_match_nothing(self):
        assert resolve_utility_class("bg-[#fff]", self._TOKENS) is None  # drift, not a token
        assert resolve_utility_class("flex", self._TOKENS) is None
        assert resolve_utility_class("items-center", self._TOKENS) is None
        assert resolve_utility_class("bg-nonexistent", self._TOKENS) is None  # name-gated

    def test_fraction_utility_is_not_a_spacing_token(self):
        # `w-1/2` is a 50% width ratio, NOT a use of `--spacing-1`
        assert resolve_utility_class("w-1/2", {"spacing-1"}) is None
        assert resolve_utility_class("h-1/3", {"spacing-1"}) is None

    def test_opacity_modifier_still_resolves_base(self):
        assert resolve_utility_class("bg-primary/50", self._TOKENS) == "color-primary"

    def test_non_color_prefix_does_not_hit_bare_color_token(self):
        # a non-color suffix must not collide with a bare `:root` color token
        assert resolve_utility_class("font-primary", {"primary"}) is None
        assert resolve_utility_class("p-4", {"primary"}) is None


def _usage_fn(name, classes=None, vars=None):
    return ParsedFunction(
        name=name,
        qualified_name=f"app.tsx::{name}",
        file_path="app.tsx",
        start_line=1,
        end_line=2,
        signature="",
        style_class_uses=classes or [],
        token_var_uses=vars or [],
    )


def _merged(graph, rel_type):
    return [
        r for call in graph.batch_merge_relationships.call_args_list if call.args[0] == rel_type for r in call.args[1]
    ]


class TestUsesTokenExtraction:
    def test_emits_uses_token_name_gated(self, tmp_path):
        graph = _graph_mock()
        css = _walked(
            tmp_path,
            "t.css",
            ":root{--primary: 1 2% 3%; --accent: red;}\n@theme{--color-primary: hsl(var(--primary));}",
        )
        fn = _usage_fn("Card", classes=["bg-primary", "flex", "p-4", "bg-[#000]"], vars=["--accent"])
        pf = ParsedFile(path="app.tsx", language="typescript", functions=[fn])
        result = TokenExtractor(graph, file_path_to_id={}).extract([css], [pf])

        edges = _merged(graph, "USES_TOKEN")
        targets = {e["to_id"] for e in edges}
        # bg-primary -> color-primary; var(--accent) -> accent. flex/p-4/bg-[#000] name-gated out.
        assert targets == {"token::color-primary", "token::accent"}
        assert all(e["from_id"] == "func::app.tsx::Card" for e in edges)
        assert result.uses_created == 2

    def test_no_edges_without_tokens(self, tmp_path):
        graph = _graph_mock()
        fn = _usage_fn("Card", classes=["bg-primary"])
        pf = ParsedFile(path="app.tsx", language="typescript", functions=[fn])
        # no css -> no tokens -> name-gating yields nothing
        result = TokenExtractor(graph, file_path_to_id={}).extract([], [pf])
        assert result.uses_created == 0
        assert _merged(graph, "USES_TOKEN") == []


# ---------------------------------------------------------------------------
# Drift — hardcoded colors / off-scale arbitraries / inline styles
# ---------------------------------------------------------------------------


class TestStyleDriftCapture:
    def test_captures_colors_offscale_inline_excludes_variants_and_var(self):
        src = (
            "export function Card() {\n"
            '  return <div className="bg-primary text-[10px] bg-[#3b82f6] '
            'data-[state=open]:bg-x h-[300px] text-[hsl(var(--x))]"\n'
            '              style={{ color: "#fff", ring: "hsl(var(--success))" }}>\n'
            "    x\n"
            "  </div>;\n"
            "}\n"
        )
        d = TypeScriptParser().parse_file("Card.tsx", src).functions[0].style_drift
        assert "#3b82f6" in d.hardcoded_colors  # arbitrary color class
        assert "#fff" in d.hardcoded_colors  # inline style
        assert all("var(" not in c for c in d.hardcoded_colors)  # var() is a token ref, not hardcoded
        assert set(d.off_scale_values) == {"text-[10px]", "h-[300px]"}
        assert "data-[state=open]:bg-x" not in d.off_scale_values  # state variant, not drift
        assert d.inline_style_count == 1

    def test_no_drift_for_plain_classes(self):
        src = "export function P(){ return <div className='flex items-center bg-primary p-4'>x</div>; }\n"
        d = TypeScriptParser().parse_file("P.tsx", src).functions[0].style_drift
        assert d.hardcoded_colors == [] and d.off_scale_values == [] and d.inline_style_count == 0

    def test_trailing_modifier_stripped_and_colors_normalized(self):
        src = (
            "export function C() {\n"
            "  return <div className='bg-[#3B82F6]/50 text-[10px]/6'\n"
            "    style={{ a: 'hsl(18, 100%, 60%)', b: 'hsl(18,100%,60%)' }}>x</div>;\n"
            "}\n"
        )
        d = TypeScriptParser().parse_file("C.tsx", src).functions[0].style_drift
        assert "#3b82f6" in d.hardcoded_colors  # trailing /50 stripped, hex lowercased
        assert d.hardcoded_colors.count("hsl(18,100%,60%)") == 1  # whitespace normalized -> one
        assert "text-[10px]" in d.off_scale_values  # trailing /6 stripped

    def test_offscale_strictness_and_inline_object_only(self):
        src = (
            "export function C(props) {\n"
            "  return <div className='aspect-[16/9] bg-[url(/img2.png)] grid-cols-[repeat(3,1fr)] h-[300px]'\n"
            "    style={props.style}>x</div>;\n"
            "}\n"
        )
        d = TypeScriptParser().parse_file("C.tsx", src).functions[0].style_drift
        assert d.off_scale_values == ["h-[300px]"]  # url/aspect/grid-template excluded
        assert d.inline_style_count == 0  # style={props.style} passthrough is not drift


class TestGetDrift:
    def test_shape(self):
        # execute order: total_colors, total_offscale, total_inline, affected, recurring, worst
        results = [
            _qr([{"c": 60}]),
            _qr([{"c": 218}]),
            _qr([{"c": 42}]),
            _qr([{"c": 122}]),
            _qr([{"color": "#fff", "c": 14}, {"color": "#000", "c": 3}]),
            _qr([{"fp": "Home.tsx", "hc": 20, "os": 25, "ins": 2, "d": 47}]),
        ]
        d = _engine(results).get_drift()
        assert d["summary"] == {
            "hardcoded_colors": 60,
            "off_scale_values": 218,
            "inline_styles": 42,
            "functions_affected": 122,
        }
        assert d["recurring_colors"][0] == {"color": "#fff", "count": 14}
        assert d["worst_files"][0]["file"] == "Home.tsx"
        assert d["worst_files"][0]["drift"] == 47
