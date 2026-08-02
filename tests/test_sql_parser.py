"""Tests for the SQL migration/function parser (DBFunction body table access)."""

from __future__ import annotations

from gristle.parsers.sql import parse_sql_schema


def _fn(sql: str):
    fns = parse_sql_schema("m.sql", sql)
    assert len(fns) == 1, f"expected 1 function, got {[f.name for f in fns]}"
    return fns[0]


class TestFunctionBodyAccess:
    def test_plpgsql_update_and_insert(self):
        fn = _fn(
            "CREATE OR REPLACE FUNCTION public.deduct(uid uuid, amt int) RETURNS void "
            "LANGUAGE plpgsql AS $$\nBEGIN\n"
            "  UPDATE public.user_credits SET balance = balance - amt WHERE user_id = uid;\n"
            "  INSERT INTO public.credit_log(user_id, delta) VALUES (uid, amt);\n"
            "END; $$;"
        )
        assert fn.name == "deduct"
        assert fn.writes == {"user_credits", "credit_log"}
        assert fn.reads == set()

    def test_language_sql_select_body(self):
        fn = _fn(
            "CREATE FUNCTION public.has_role(_uid uuid) RETURNS boolean LANGUAGE SQL STABLE AS $$\n"
            "  SELECT EXISTS (SELECT 1 FROM public.user_roles WHERE user_id = _uid)\n$$;"
        )
        assert fn.name == "has_role"
        assert fn.reads == {"user_roles"}
        assert fn.writes == set()

    def test_delete_is_a_write(self):
        fn = _fn(
            "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$\n  DELETE FROM public.sessions WHERE expired\n$$;"
        )
        assert fn.writes == {"sessions"}
        assert fn.reads == set()

    def test_select_join_are_reads(self):
        fn = _fn(
            "CREATE FUNCTION f() RETURNS int LANGUAGE sql AS $$\n"
            "  SELECT count(*) FROM public.orders o JOIN public.users u ON u.id = o.uid\n$$;"
        )
        assert fn.reads == {"orders", "users"}
        assert fn.writes == set()

    def test_insert_select_splits_read_and_write(self):
        fn = _fn(
            "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$\n"
            "  INSERT INTO public.audit(uid) SELECT id FROM public.events\n$$;"
        )
        assert fn.writes == {"audit"}
        assert fn.reads == {"events"}

    def test_write_dominates_when_read_and_written(self):
        # A table both read and written in the same function is classified write.
        fn = _fn(
            "CREATE FUNCTION f() RETURNS void LANGUAGE plpgsql AS $$\nBEGIN\n"
            "  UPDATE public.t SET x = (SELECT max(x) FROM public.t);\n"
            "END; $$;"
        )
        assert "t" in fn.writes
        assert "t" not in fn.reads

    def test_public_schema_qualifier_is_stripped(self):
        fn = _fn(
            "CREATE FUNCTION public.rollup() RETURNS void LANGUAGE sql AS $$\n"
            "  INSERT INTO public.daily SELECT * FROM public.events\n$$;"
        )
        assert fn.name == "rollup"  # public.rollup -> rollup
        assert fn.writes == {"daily"}
        assert fn.reads == {"events"}

    def test_non_public_schema_tables_are_skipped(self):
        # auth.users / storage.objects are different physical tables than a same-named
        # public Model, so they must NOT be captured (would conflate on the bare name).
        fn = _fn(
            "CREATE FUNCTION public.current_email() RETURNS text LANGUAGE sql AS $$\n"
            "  SELECT email FROM auth.users WHERE id = auth.uid()\n$$;"
        )
        assert fn.reads == set()  # auth.users skipped, not read as public 'users'
        assert fn.writes == set()

    def test_public_kept_when_mixed_with_non_public(self):
        fn = _fn(
            "CREATE FUNCTION public.sync() RETURNS void LANGUAGE plpgsql AS $$\nBEGIN\n"
            "  UPDATE public.profiles SET seen = (SELECT count(*) FROM auth.sessions);\n"
            "END; $$;"
        )
        assert fn.writes == {"profiles"}  # public.profiles kept
        assert "sessions" not in fn.reads  # auth.sessions skipped

    def test_cte_name_is_not_a_read(self):
        # A CTE referenced in FROM parses like a table; a CTE named after a Model must
        # not become a false read.
        fn = _fn(
            "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$\n"
            "  WITH profiles AS (SELECT 1) SELECT * FROM profiles\n$$;"
        )
        assert "profiles" not in fn.reads

    def test_cte_body_real_table_read_is_kept(self):
        fn = _fn(
            "CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$\n"
            "  WITH recent AS (SELECT * FROM events) SELECT * FROM recent\n$$;"
        )
        assert fn.reads == {"events"}  # real table kept, CTE name 'recent' excluded

    def test_unqualified_names(self):
        fn = _fn("CREATE FUNCTION f() RETURNS void LANGUAGE sql AS $$\n  UPDATE credits SET bal = 0\n$$;")
        assert fn.writes == {"credits"}

    def test_full_merge_does_not_fabricate_a_write(self):
        # An unparseable body (`INSERT ... DEFAULT VALUES`) can swallow the next
        # function into one create_function node. The text-scan bound must stop
        # fn_a from being credited with fn_b's UPDATE target (the HIGH-severity bug).
        sql = (
            "CREATE FUNCTION fn_a() RETURNS void AS $$ BEGIN INSERT INTO audit_log DEFAULT VALUES; END; $$ LANGUAGE plpgsql;\n"
            "CREATE FUNCTION fn_b() RETURNS void AS $$ BEGIN UPDATE settings SET v = 1; END; $$ LANGUAGE plpgsql;\n"
        )
        by_name = {f.name: f for f in parse_sql_schema("m.sql", sql)}
        assert "settings" not in by_name["fn_a"].writes  # no fabricated write


class TestNonFunctionStatementsIgnored:
    def test_create_table_and_policy_do_not_produce_functions(self):
        # Only CREATE FUNCTION yields a ParsedSQLFunction; DDL/RLS are ignored.
        sql = (
            "CREATE TABLE public.profiles (id uuid PRIMARY KEY, email text);\n"
            "ALTER TABLE public.profiles ENABLE ROW LEVEL SECURITY;\n"
            'CREATE POLICY "p" ON public.profiles FOR SELECT USING (true);\n'
        )
        assert parse_sql_schema("m.sql", sql) == []

    def test_multiple_functions_in_one_file(self):
        sql = (
            "CREATE FUNCTION a() RETURNS void LANGUAGE sql AS $$ UPDATE t1 SET x = 1 $$;\n"
            "CREATE FUNCTION b() RETURNS void LANGUAGE sql AS $$ DELETE FROM t2 $$;\n"
        )
        fns = {f.name: f for f in parse_sql_schema("m.sql", sql)}
        assert set(fns) == {"a", "b"}
        assert fns["a"].writes == {"t1"}
        assert fns["b"].writes == {"t2"}

    def test_unparseable_statement_does_not_bleed_into_next_function(self):
        # `INSERT ... DEFAULT VALUES` isn't parsed by the grammar and can, via error
        # recovery, spill past the closing $$. The byte-bound must stop function a
        # from inheriting function b's table (t2).
        sql = (
            "CREATE FUNCTION a() RETURNS void LANGUAGE sql AS $$ INSERT INTO t1 DEFAULT VALUES $$;\n"
            "CREATE FUNCTION b() RETURNS void LANGUAGE sql AS $$ DELETE FROM t2 $$;\n"
        )
        fns = {f.name: f for f in parse_sql_schema("m.sql", sql)}
        assert "t2" not in fns["a"].writes  # no cross-function bleed
        assert fns["b"].writes == {"t2"}

    def test_empty_and_garbage_input(self):
        assert parse_sql_schema("m.sql", "") == []
        assert parse_sql_schema("m.sql", "-- just a comment\n") == []


class TestFallbackRecovery:
    """Functions tree-sitter-sql produces no ``create_function`` node for (incomplete
    plpgsql coverage) are recovered by re-parsing the dollar-quoted body alone."""

    def test_security_definer_with_guards_is_recovered(self):
        # SECURITY DEFINER + `SET search_path = ...` + compound IF guards make
        # tree-sitter emit no create_function node at all — the exact real-repo shape.
        fn = _fn(
            "CREATE OR REPLACE FUNCTION save_preferred_transform_mode(p_mode TEXT)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "BEGIN\n"
            "  IF auth.uid() IS NULL THEN RAISE EXCEPTION 'x'; END IF;\n"
            "  IF p_mode IS NOT NULL AND p_mode NOT IN ('auto','guided','expert') THEN\n"
            "    RAISE EXCEPTION 'y';\n"
            "  END IF;\n"
            "  UPDATE profiles SET preferred_transform_mode = p_mode WHERE id = auth.uid();\n"
            "END; $$;"
        )
        assert fn.name == "save_preferred_transform_mode"
        assert fn.writes == {"profiles"}
        assert fn.reads == set()

    def test_signature_params_are_not_tables(self):
        # A standalone body has no signature context; parameters referenced in the body
        # can surface as pseudo-tables and must be subtracted.
        fn = _fn(
            "CREATE OR REPLACE FUNCTION deduct(p_user_id uuid, p_amount int)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "BEGIN\n"
            "  IF p_amount IS NULL OR p_amount NOT IN (1, 2, 3) THEN RAISE EXCEPTION 'bad'; END IF;\n"
            "  UPDATE profiles SET credits = credits - p_amount WHERE id = p_user_id;\n"
            "  INSERT INTO credit_ledger(user_id, delta) VALUES (p_user_id, p_amount);\n"
            "END; $$;"
        )
        assert fn.writes == {"profiles", "credit_ledger"}
        assert "p_user_id" not in (fn.reads | fn.writes)
        assert "p_amount" not in (fn.reads | fn.writes)

    def test_declare_variables_are_not_tables(self):
        fn = _fn(
            "CREATE OR REPLACE FUNCTION f(p_id uuid)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "DECLARE\n  v_total int;\n  v_row record;\n"
            "BEGIN\n"
            "  IF p_id IS NULL THEN RAISE EXCEPTION 'x'; END IF;\n"
            "  IF v_total IS NULL OR v_total NOT IN (1, 2, 3) THEN RAISE EXCEPTION 'y'; END IF;\n"
            "  UPDATE usage_history SET total = v_total WHERE user_id = p_id;\n"
            "END; $$;"
        )
        assert fn.writes == {"usage_history"}
        assert "v_total" not in (fn.reads | fn.writes)
        assert "v_row" not in (fn.reads | fn.writes)

    def test_for_loop_variable_is_not_a_table(self):
        # A loop variable named after a real table (`FOR profiles IN ...`) must not
        # become a false read; the FOR..IN SELECT source table is the real read.
        fn = _fn(
            "CREATE OR REPLACE FUNCTION f(p_x text)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "BEGIN\n"
            "  IF p_x IS NULL OR p_x NOT IN ('a', 'b') THEN RAISE EXCEPTION 'x'; END IF;\n"
            "  FOR profiles IN SELECT * FROM knuckles_gift_limits LOOP\n"
            "    NULL;\n"
            "  END LOOP;\n"
            "END; $$;"
        )
        assert fn.reads == {"knuckles_gift_limits"}
        assert "profiles" not in (fn.reads | fn.writes)

    def test_non_public_schema_still_skipped_in_fallback(self):
        fn = _fn(
            "CREATE OR REPLACE FUNCTION f(p_x text)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "BEGIN\n"
            "  IF p_x IS NULL OR p_x NOT IN ('a','b') THEN RAISE EXCEPTION 'x'; END IF;\n"
            "  UPDATE auth.users SET email = p_x WHERE id = auth.uid();\n"
            "END; $$;"
        )
        assert fn.writes == set()  # auth.users is not a public Model
        assert fn.reads == set()

    def test_ast_parsed_function_is_not_double_recovered(self):
        # A file with one clean (AST-parsed) function and one fallback-only function
        # must yield exactly one of each — the fallback skips AST-covered boundaries.
        sql = (
            "CREATE FUNCTION clean() RETURNS void LANGUAGE sql AS $$ UPDATE t1 SET x = 1 $$;\n"
            "CREATE OR REPLACE FUNCTION guarded(p_v text)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "BEGIN\n"
            "  IF p_v IS NULL OR p_v NOT IN ('a','b') THEN RAISE EXCEPTION 'x'; END IF;\n"
            "  UPDATE t2 SET y = 2;\n"
            "END; $$;"
        )
        fns = parse_sql_schema("m.sql", sql)
        names = [f.name for f in fns]
        assert names.count("clean") == 1
        assert names.count("guarded") == 1
        by_name = {f.name: f for f in fns}
        assert by_name["clean"].writes == {"t1"}
        assert by_name["guarded"].writes == {"t2"}

    def test_create_function_in_block_comment_is_ignored(self):
        # A commented-out prior definition (a common migration idiom) must not be
        # scanned as a boundary — otherwise the fallback fabricates a second entry
        # from dead code that displaces the real one in the downstream dedup.
        sql = (
            "/* Old version:\n"
            "CREATE FUNCTION foo() RETURNS void AS $$\n"
            "BEGIN INSERT INTO old_table VALUES (1); END;\n"
            "$$ LANGUAGE plpgsql;\n"
            "*/\n"
            "CREATE FUNCTION foo() RETURNS void AS $$\n"
            "BEGIN INSERT INTO accounts (id) VALUES (1); END;\n"
            "$$ LANGUAGE plpgsql;"
        )
        fns = parse_sql_schema("m.sql", sql)
        assert [f.name for f in fns] == ["foo"]
        assert fns[0].writes == {"accounts"}  # not old_table from the comment

    def test_table_name_in_raise_message_is_not_an_edge(self):
        # A table named inside a dollar-quoted RAISE message must not be parsed as live
        # SQL. Postgres requires a distinct tag to nest, so $m$...$m$ is the only legal
        # form — and the verb in the prose ("delete from") must not drive a false edge.
        fn = _fn(
            "CREATE OR REPLACE FUNCTION enforce_policy(p_id uuid)\n"
            "RETURNS void LANGUAGE plpgsql SECURITY DEFINER SET search_path = public AS $$\n"
            "BEGIN\n"
            "  IF p_id IS NULL THEN RAISE EXCEPTION 'missing id'; END IF;\n"
            "  RAISE EXCEPTION $m$Cannot delete from user_sessions: active lock$m$;\n"
            "  UPDATE audit_log SET touched = now() WHERE id = p_id;\n"
            "END; $$;"
        )
        assert fn.writes == {"audit_log"}
        assert "user_sessions" not in (fn.reads | fn.writes)

    def test_raise_message_table_on_ast_path_is_not_an_edge(self):
        # The same protection on the primary (AST) path — a plain plpgsql function whose
        # body tree-sitter DOES model, but which contains a dollar-quoted RAISE message.
        fn = _fn(
            "CREATE FUNCTION g() RETURNS void LANGUAGE plpgsql AS $$\n"
            "BEGIN\n"
            "  RAISE EXCEPTION $m$Cannot delete from user_sessions$m$;\n"
            "  UPDATE audit_log SET x = 1;\n"
            "END; $$;"
        )
        assert fn.writes == {"audit_log"}
        assert "user_sessions" not in (fn.reads | fn.writes)

    def test_create_function_in_string_literal_is_ignored(self):
        # Dynamic DDL: a CREATE FUNCTION inside an EXECUTE string must not fabricate a
        # function nor truncate the enclosing function's table walk.
        sql = (
            "CREATE FUNCTION make_it() RETURNS void AS $$\n"
            "BEGIN\n"
            "  EXECUTE 'CREATE FUNCTION bar() RETURNS void AS $inner$ "
            "INSERT INTO spurious VALUES(1); $inner$ LANGUAGE plpgsql';\n"
            "  INSERT INTO accounts (id) VALUES (1);\n"
            "END;\n"
            "$$ LANGUAGE plpgsql;"
        )
        fns = parse_sql_schema("m.sql", sql)
        assert [f.name for f in fns] == ["make_it"]
        assert fns[0].writes == {"accounts"}  # not spurious, and accounts not lost
