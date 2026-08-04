"""Tests for feature-flag detection: TS check-site descriptors, definition parsers
(registry + SQL migrations), and the FlagExtractor (Flag nodes + GATES edges)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gristle.ingestion.flag_extractor import FlagExtractor, _is_reversal_sql
from gristle.ingestion.walker import WalkedFile
from gristle.models import ParsedClass, ParsedFile, ParsedFunction
from gristle.parsers.feature_flags import parse_flag_registry, parse_flag_table_migrations
from gristle.parsers.typescript import TypeScriptParser


def _flag_keys(src: str) -> list[str]:
    """Flag keys the TS parser emits as `flag('K')` descriptors for a snippet."""
    import re

    pf = TypeScriptParser().parse_file("t.ts", src)
    out: list[str] = []
    for fn in pf.functions:
        for c in fn.calls_with_args:
            m = re.match(r"^flag\('([^']+)'\)$", c)
            if m:
                out.append(m.group(1))
    return out


# ---------------------------------------------------------------------------
# TS check-site descriptors
# ---------------------------------------------------------------------------


class TestFlagCheckDescriptors:
    def test_client_hook(self):
        assert "ENABLE_X" in _flag_keys("function f(){ const a = useFeatureFlag('ENABLE_X'); }")

    def test_server_check_key_at_arg1(self):
        # isFeatureFlagEnabled(supabase, 'K', userId) — key is the second arg.
        keys = _flag_keys("function f(){ isFeatureFlagEnabled(supabase, 'ENABLE_WEB_RESEARCH', user.id); }")
        assert keys == ["ENABLE_WEB_RESEARCH"]

    def test_member_receiver_does_not_break_key(self):
        # `this.env` receiver used to defeat a bare-identifier regex; tree-sitter is fine.
        keys = _flag_keys("function f(){ isFlagEnabledForUser(this.env, 'DIRECTION_CORE', p.userId); }")
        assert keys == ["DIRECTION_CORE"]

    def test_non_key_string_argument_is_not_taken_as_key(self):
        # Key is the const at arg 1; 'autocapture' is a 4th-arg log tag, not the key.
        keys = _flag_keys("function f(){ isFlagEnabledForUser(supabase, AUTO_CAPTURE_FLAG, userId, 'autocapture'); }")
        assert keys == []

    def test_member_read_captures_wrapper_accessor(self):
        # `isXEnabled()` helpers read the cache — this surface keeps them from looking dead.
        keys = _flag_keys("function isXEnabled(){ return runtimeFlagCache.ENABLE_X ?? featureFlags.ENABLE_X; }")
        assert keys == ["ENABLE_X"]

    def test_member_read_ignores_non_flag_property(self):
        assert _flag_keys("function f(){ return featureFlags.length; }") == []

    def test_table_id_read(self):
        src = "async function f(){ await supabase.from('feature_flags').select('enabled').eq('id','SOURCE_CORPUS_CAPTURE').maybeSingle(); }"
        assert _flag_keys(src) == ["SOURCE_CORPUS_CAPTURE"]

    def test_table_id_read_wrong_table_ignored(self):
        src = "async function f(){ await supabase.from('profiles').eq('id','NOT_A_FLAG').single(); }"
        assert _flag_keys(src) == []

    def test_unconfigured_function_ignored(self):
        assert _flag_keys("function f(){ foo('BAR_NOT_A_FLAG'); }") == []

    def test_const_bound_key_is_resolved(self):
        # A key passed as a const at the key position resolves to the const's value.
        src = "const QC = 'ENABLE_X';\nfunction f(){ isFeatureFlagEnabled(supabase, QC, userId); }"
        assert _flag_keys(src) == ["ENABLE_X"]

    def test_const_binding_does_not_resolve_non_key_positions(self):
        # `URL` const is never a flag key here — the key position holds a real literal.
        src = "const URL = 'https://x';\nfunction f(){ useFeatureFlag('ENABLE_X'); }"
        assert _flag_keys(src) == ["ENABLE_X"]


# ---------------------------------------------------------------------------
# Registry definition parser
# ---------------------------------------------------------------------------


class TestRegistryParser:
    REG = """\
export const featureFlags = {
  // Leading doc for A.
  ENABLE_A: true, // trailing note A
  // Leading doc for B.
  ENABLE_B: false,
} as const;
"""

    def test_keys_and_defaults(self):
        flags = {f.key: f for f in parse_flag_registry("f.ts", self.REG, {"featureFlags"})}
        assert flags["ENABLE_A"].default is True
        assert flags["ENABLE_B"].default is False

    def test_trailing_comment_attributed_to_its_own_key(self):
        flags = {f.key: f for f in parse_flag_registry("f.ts", self.REG, {"featureFlags"})}
        assert "trailing note A" in (flags["ENABLE_A"].description or "")
        # ...and NOT leaked onto the next key.
        assert "trailing note A" not in (flags["ENABLE_B"].description or "")
        assert "Leading doc for B" in (flags["ENABLE_B"].description or "")

    def test_unconfigured_symbol_ignored(self):
        assert parse_flag_registry("f.ts", self.REG, {"someOtherObject"}) == []


# ---------------------------------------------------------------------------
# SQL migration definition parser
# ---------------------------------------------------------------------------


class TestMigrationParser:
    def test_insert_multi_row_with_semicolon_in_description(self):
        sql = """\
INSERT INTO public.feature_flags (id, enabled, description)
VALUES
  ('ENABLE_A', false, 'desc; with a semicolon inside'),
  ('ENABLE_B', true, 'second row');
"""
        flags = parse_flag_table_migrations("m.sql", sql, {"feature_flags"})
        keys = {f.key: f for f in flags if not f.retired}
        assert set(keys) == {"ENABLE_A", "ENABLE_B"}  # semicolon didn't truncate row 2
        assert keys["ENABLE_B"].default is True

    def test_delete_marks_retired(self):
        sql = "DELETE FROM feature_flags WHERE id = 'ENABLE_OLD';"
        flags = parse_flag_table_migrations("m.sql", sql, {"feature_flags"})
        assert any(f.key == "ENABLE_OLD" and f.retired for f in flags)

    def test_cleanup_array_marks_retired(self):
        sql = """\
DO $$
DECLARE
  flags_to_remove TEXT[] := ARRAY['ENABLE_X', 'ENABLE_Y'];
BEGIN
  DELETE FROM feature_flags WHERE id = ANY(flags_to_remove);
END $$;
"""
        retired = {f.key for f in parse_flag_table_migrations("m.sql", sql, {"feature_flags"}) if f.retired}
        assert retired == {"ENABLE_X", "ENABLE_Y"}

    def test_non_flag_table_ignored(self):
        sql = "INSERT INTO profiles (id, enabled) VALUES ('SOME_ID', true);"
        assert parse_flag_table_migrations("m.sql", sql, {"feature_flags"}) == []


class TestReversalDetection:
    @pytest.mark.parametrize(
        "path,expected",
        [
            ("supabase/migrations/x.sql", False),
            ("supabase/migrations/normal_feature_flag.sql", False),
            # A forward migration for a "revert" FEATURE must NOT be excluded.
            ("supabase/migrations/20260422210000_refinement_revert.sql", False),
            ("supabase/rollbacks/x_rollback.sql", True),  # reversal directory
            ("supabase/migrations/x_rollback.sql", True),  # co-located reversal suffix
            ("db/down/001.down.sql", True),
        ],
    )
    def test_is_reversal(self, path, expected):
        assert _is_reversal_sql(path) is expected


# ---------------------------------------------------------------------------
# FlagExtractor
# ---------------------------------------------------------------------------


def _make_graph_mock() -> MagicMock:
    mock = MagicMock()
    mock.batch_create_nodes.return_value = 0
    mock.batch_create_relationships.return_value = 0
    mock.batch_merge_relationships.return_value = 0
    return mock


def _walked(tmp_path, name, content, ext):
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return WalkedFile(relative_path=name, absolute_path=str(p), extension=ext)


def _fn(name, calls_with_args):
    return ParsedFunction(
        name=name,
        qualified_name=f"app.ts::{name}",
        file_path="app.ts",
        start_line=1,
        end_line=2,
        signature="",
        calls_with_args=calls_with_args,
    )


class TestFlagExtractor:
    def test_flag_nodes_and_gates(self, tmp_path):
        registry = _walked(
            tmp_path,
            "featureFlags.ts",
            "export const featureFlags = { ENABLE_A: true, ENABLE_B: false } as const;",
            "ts",
        )
        migration = _walked(
            tmp_path,
            "seed.sql",
            "INSERT INTO feature_flags (id, enabled) VALUES ('ENABLE_A', true), ('SERVER_ONLY', false);",
            "sql",
        )
        pf = ParsedFile(
            path="app.ts",
            language="typescript",
            functions=[
                _fn("gateA", ["flag('ENABLE_A')"]),
                _fn("gateServer", ["flag('SERVER_ONLY')"]),
                _fn("gateOrphan", ["flag('ORPHAN_KEY')"]),  # checked, defined nowhere
            ],
            classes=[],
            imports=[],
            line_count=2,
        )

        graph = _make_graph_mock()
        result = FlagExtractor(graph, file_path_to_id={"featureFlags.ts": "file::featureFlags.ts"}).extract(
            [registry, migration], [pf]
        )

        nodes = {
            n["key"]: n
            for call in graph.batch_create_nodes.call_args_list
            for n in call.args[1]
            if call.args[0] == "Flag"
        }
        # Universe = registry {A,B} + db {A, SERVER_ONLY} + check {A, SERVER_ONLY, ORPHAN}
        assert set(nodes) == {"ENABLE_A", "ENABLE_B", "SERVER_ONLY", "ORPHAN_KEY"}
        assert nodes["ENABLE_A"]["in_registry"] and nodes["ENABLE_A"]["in_db"]
        assert nodes["ENABLE_B"]["in_registry"] and not nodes["ENABLE_B"]["in_db"]
        assert nodes["SERVER_ONLY"]["in_db"] and not nodes["SERVER_ONLY"]["in_registry"]
        assert nodes["ORPHAN_KEY"]["orphan"] and not nodes["ORPHAN_KEY"]["in_registry"]
        assert not nodes["ENABLE_A"]["orphan"]

        gates = {
            (i["from_id"], i["to_id"])
            for call in graph.batch_merge_relationships.call_args_list
            for i in call.args[1]
            if call.args[0] == "GATES"
        }
        assert ("flag::ENABLE_A", "func::app.ts::gateA") in gates
        assert ("flag::ORPHAN_KEY", "func::app.ts::gateOrphan") in gates
        assert result.gates_created == 3

    def test_gates_from_class_methods(self, tmp_path):
        pf = ParsedFile(
            path="app.ts",
            language="typescript",
            functions=[],
            classes=[
                ParsedClass(
                    name="C",
                    qualified_name="app.ts::C",
                    file_path="app.ts",
                    start_line=1,
                    end_line=5,
                    signature="class C",
                    methods=[_fn("m", ["flag('ENABLE_A')"])],
                )
            ],
            imports=[],
            line_count=5,
        )
        graph = _make_graph_mock()
        FlagExtractor(graph, file_path_to_id={}).extract([], [pf])
        gates = {
            (i["from_id"], i["to_id"])
            for call in graph.batch_merge_relationships.call_args_list
            for i in call.args[1]
            if call.args[0] == "GATES"
        }
        assert ("flag::ENABLE_A", "func::app.ts::m") in gates

    def test_rollback_delete_does_not_retire(self, tmp_path):
        """A DELETE in a rollback script must NOT mark a live flag retired."""
        seed = _walked(
            tmp_path, "seed.sql", "INSERT INTO feature_flags (id, enabled) VALUES ('ENABLE_A', true);", "sql"
        )
        rollback = _walked(tmp_path, "seed_rollback.sql", "DELETE FROM feature_flags WHERE id = 'ENABLE_A';", "sql")
        graph = _make_graph_mock()
        FlagExtractor(graph, file_path_to_id={}).extract([seed, rollback], [])
        nodes = {
            n["key"]: n
            for call in graph.batch_create_nodes.call_args_list
            for n in call.args[1]
            if call.args[0] == "Flag"
        }
        assert nodes["ENABLE_A"]["in_db"] is True
        assert nodes["ENABLE_A"]["retired"] is False


# ---------------------------------------------------------------------------
# Query surface: analyze_flags + flag_reach
# ---------------------------------------------------------------------------


def _qr(records):
    from gristle.graph.client import QueryResult

    return QueryResult(records=records, summary={})


def _engine(side_effect):
    from gristle.query.engine import QueryEngine

    graph = MagicMock()
    graph.execute.side_effect = side_effect
    return QueryEngine(graph, repo_path=None)


class TestAnalyzeFlags:
    def _rows(self):
        def row(key, **kw):
            base = {
                "key": key,
                "in_registry": False,
                "in_db": False,
                "retired": False,
                "orphan": False,
                "registry_default": None,
                "gates_count": 0,
                "description": "",
            }
            base.update(kw)
            return base

        return [
            row("DEAD", in_registry=True, registry_default=False, gates_count=0),
            row("ORPH", orphan=True, gates_count=1),
            row("GAP", in_registry=True, registry_default=False, gates_count=2),
            row("OLD", retired=True),
            row("CHILD", in_registry=True, in_db=True, gates_count=1, description="Requires PARENT to be on"),
            row("FEAT", in_registry=True, in_db=True, gates_count=1),
            row("FEAT_V2", in_registry=True, in_db=True, gates_count=1),
        ]

    def test_categorization(self):
        engine = _engine([_qr(self._rows())])
        r = engine.analyze_flags()
        assert r["dead_candidates"] == ["DEAD"]
        assert [o["key"] for o in r["orphan_checks"]] == ["ORPH"]
        assert [c["key"] for c in r["config_gap"]] == ["GAP"]
        assert r["retired"] == ["OLD"]
        assert r["interactions"]["CHILD"] == ["PARENT"]
        assert r["superseded_families"].get("FEAT") == ["FEAT", "FEAT_V2"]

    def test_retired_is_not_dead(self):
        # A retired flag with 0 gates must not also be a dead candidate.
        engine = _engine(
            [
                _qr(
                    [
                        {
                            "key": "OLD",
                            "in_registry": True,
                            "in_db": False,
                            "retired": True,
                            "orphan": False,
                            "registry_default": False,
                            "gates_count": 0,
                            "description": "",
                        }
                    ]
                )
            ]
        )
        r = engine.analyze_flags()
        assert r["dead_candidates"] == []
        assert r["retired"] == ["OLD"]


class TestFlagReach:
    def test_reach_merges_and_shapes(self):
        engine = _engine(
            [
                _qr([{"f.key": "F"}]),  # existence
                _qr([{"function": "gateFn", "file": "a.ts"}]),  # gated
                _qr([{"method": "GET", "path": "/x"}]),  # routes
                _qr([{"model": "User", "access": "read"}]),  # direct models
                _qr([{"model": "User", "access": "write"}]),  # rpc models (write beats read)
                _qr([{"flag": "OTHER", "shared_functions": 1}]),  # co-gating
            ]
        )
        r = engine.flag_reach("F")
        assert r["gated_functions_count"] == 1
        assert r["routes_behind_flag"] == [{"method": "GET", "path": "/x"}]
        assert r["models_touched"] == [{"model": "User", "access": "write"}]  # write won
        assert r["co_gating_flags"] == [{"flag": "OTHER", "shared_functions": 1}]

    def test_reach_missing_flag(self):
        engine = _engine([_qr([])])  # existence returns nothing
        assert "error" in engine.flag_reach("NOPE")
