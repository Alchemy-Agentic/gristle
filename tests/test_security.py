"""Tests for security pattern detection — secrets, SQL injection, unsafe calls, LLM risks."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from gristle.graph.client import QueryResult
from gristle.parsers.security import (
    detect_hardcoded_secrets,
    detect_llm_output_risks,
    detect_sql_injection,
    detect_unsafe_calls,
)
from gristle.query.engine import QueryEngine


def _qr(records: list[dict]) -> QueryResult:
    return QueryResult(records=records, summary={})


def _empty() -> QueryResult:
    return _qr([])


def _make_engine():
    mock_graph = MagicMock()
    mock_graph.repo_id = "test"
    engine = QueryEngine(mock_graph)
    return engine, mock_graph


# ======================================================================
# detect_hardcoded_secrets
# ======================================================================


class TestHardcodedSecrets:
    def test_aws_access_key(self):
        code = 'key = "AKIAIOSFODNN7ABCDEFG"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].category == "hardcoded_secret"
        assert findings[0].detail == "AWS_ACCESS_KEY"
        assert findings[0].severity == "high"
        assert findings[0].line == 1

    def test_github_token(self):
        code = 'token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].detail == "GITHUB_TOKEN"

    def test_openai_key(self):
        code = 'api_key = "sk-ABCDEFGHIJKLMNOPQRSTUVWXYZab"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].detail == "OPENAI_KEY"

    def test_stripe_secret_key(self):
        code = 'key = "sk_live_ABCDEFGHIJKLMNOPQRSTUVWXYZabc"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].detail == "STRIPE_SECRET_KEY"

    def test_openai_not_triggered_by_stripe(self):
        """OPENAI_KEY pattern should not match Stripe keys (both start with sk-)."""
        code = 'key = "sk_test_ABCDEFGHIJKLMNOPQRSTUVWXYZabc"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].detail == "STRIPE_SECRET_KEY"

    def test_private_key_header(self):
        code = "-----BEGIN RSA PRIVATE KEY-----"
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].detail == "PRIVATE_KEY"

    def test_generic_secret_assignment(self):
        code = 'API_KEY = "a1b2c3d4e5f6g7h8i9j0k1l2m3n4"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 1
        assert findings[0].detail == "GENERIC_SECRET"
        assert findings[0].severity == "medium"

    def test_ignores_env_var_lookups(self):
        code = 'key = os.environ["AWS_KEY"]'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 0

    def test_ignores_process_env(self):
        code = "const key = process.env.API_KEY;"
        findings = detect_hardcoded_secrets(code, "typescript")
        assert len(findings) == 0

    def test_ignores_comments(self):
        code = '# key = "AKIAIOSFODNN7ABCDEFG"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 0

    def test_ignores_placeholders(self):
        code = 'API_KEY = "your-api-key-here-placeholder"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 0

    def test_generic_ignores_short_values(self):
        code = 'password = "short"'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 0

    def test_multiple_findings_different_lines(self):
        code = 'key1 = "AKIAIOSFODNN7ABCDEFG"\nkey2 = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"\n'
        findings = detect_hardcoded_secrets(code, "python")
        assert len(findings) == 2
        assert findings[0].line == 1
        assert findings[1].line == 2


# ======================================================================
# detect_sql_injection
# ======================================================================


class TestSQLInjection:
    def test_python_fstring_with_execute(self):
        code = 'def query(user_id):\n    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
        findings = detect_sql_injection(code, "python")
        assert len(findings) == 1
        assert findings[0].category == "sql_injection"
        assert findings[0].detail == "dynamic_query"
        assert findings[0].severity == "high"

    def test_python_percent_format(self):
        code = 'def query(name):\n    cursor.execute("SELECT * FROM users WHERE name = %s" % name)\n'
        findings = detect_sql_injection(code, "python")
        assert len(findings) == 1

    def test_python_dot_format(self):
        code = 'def query(name):\n    db.execute("SELECT * FROM users WHERE name = {}".format(name))\n'
        findings = detect_sql_injection(code, "python")
        assert len(findings) == 1

    def test_ts_template_literal(self):
        code = "function query(id: string) {\n    db.query(`SELECT * FROM users WHERE id = ${id}`);\n}\n"
        findings = detect_sql_injection(code, "typescript")
        assert len(findings) == 1

    def test_string_concat(self):
        code = 'def query(name):\n    cursor.execute("SELECT * FROM users WHERE name = " + name)\n'
        findings = detect_sql_injection(code, "python")
        assert len(findings) == 1

    def test_no_executor_no_findings(self):
        """Files without SQL executor calls should not be flagged."""
        code = 'sql_str = f"SELECT * FROM users WHERE id = {user_id}"'
        findings = detect_sql_injection(code, "python")
        assert len(findings) == 0

    def test_ignores_comments(self):
        code = (
            "def query():\n"
            '    # cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")\n'
            '    cursor.execute("SELECT 1")\n'
        )
        findings = detect_sql_injection(code, "python")
        assert len(findings) == 0


# ======================================================================
# detect_unsafe_calls
# ======================================================================


class TestUnsafeCalls:
    def test_eval(self):
        tags = detect_unsafe_calls(["eval", "print", "len"])
        assert tags == ["unsafe_call:eval"]

    def test_exec(self):
        tags = detect_unsafe_calls(["exec"])
        assert tags == ["unsafe_call:exec"]

    def test_pickle_loads(self):
        tags = detect_unsafe_calls(["pickle.loads", "json.loads"])
        assert tags == ["unsafe_call:pickle.loads"]

    def test_os_system(self):
        tags = detect_unsafe_calls(["os.system"])
        assert tags == ["unsafe_call:os.system"]

    def test_subprocess(self):
        tags = detect_unsafe_calls(["subprocess.call", "subprocess.run"])
        assert tags == ["unsafe_call:subprocess.call", "unsafe_call:subprocess.run"]

    def test_child_process(self):
        tags = detect_unsafe_calls(["child_process.exec", "child_process.execSync"])
        assert len(tags) == 2

    def test_safe_calls_ignored(self):
        tags = detect_unsafe_calls(["json.loads", "print", "len", "str"])
        assert tags == []

    def test_tag_format(self):
        tags = detect_unsafe_calls(["eval"])
        assert tags[0] == "unsafe_call:eval"


# ======================================================================
# detect_llm_output_risks
# ======================================================================


class TestLLMOutputRisks:
    def test_openai_plus_eval(self):
        calls = ["completions.create", "eval"]
        tags = detect_llm_output_risks(calls)
        assert "llm_output_risk:eval" in tags

    def test_langchain_plus_execute(self):
        calls = ["chain.invoke", "cursor.execute"]
        tags = detect_llm_output_risks(calls)
        assert "llm_output_risk:cursor.execute" in tags

    def test_anthropic_plus_exec(self):
        calls = ["messages.create", "exec"]
        tags = detect_llm_output_risks(calls)
        assert "llm_output_risk:exec" in tags

    def test_llm_only_no_sink(self):
        calls = ["completions.create", "print", "json.dumps"]
        tags = detect_llm_output_risks(calls)
        assert tags == []

    def test_sink_only_no_llm(self):
        """eval without LLM source is not an LLM risk (covered by unsafe_call)."""
        calls = ["eval", "json.loads"]
        tags = detect_llm_output_risks(calls)
        assert tags == []

    def test_gemini_plus_os_system(self):
        calls = ["model.generate_content", "os.system"]
        tags = detect_llm_output_risks(calls)
        assert "llm_output_risk:os.system" in tags

    def test_multiple_sinks(self):
        calls = ["completions.create", "eval", "exec"]
        tags = detect_llm_output_risks(calls)
        assert len(tags) == 2
        assert "llm_output_risk:eval" in tags
        assert "llm_output_risk:exec" in tags


# ======================================================================
# Parser integration
# ======================================================================


class TestPythonParserSecurity:
    def test_unsafe_call_detected_on_function(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "danger.py",
            """
def run_code(code):
    result = eval(code)
    return result
""",
        )
        func = result.functions[0]
        assert any("unsafe_call:eval" in f for f in func.security_findings)

    def test_secret_attributed_to_function(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "config.py",
            """
def get_client():
    key = "AKIAIOSFODNN7ABCDEFG"
    return Client(key)
""",
        )
        func = result.functions[0]
        assert any("hardcoded_secret" in f for f in func.security_findings)

    def test_test_files_skip_security(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "tests/test_config.py",
            """
def test_client():
    key = "AKIAIOSFODNN7ABCDEFG"
    assert key is not None
""",
        )
        # Test files should not have file-level security findings
        assert len(result.security_findings) == 0

    def test_sql_injection_attributed(self):
        from gristle.parsers.python import PythonParser

        parser = PythonParser()
        result = parser.parse_file(
            "db.py",
            """
def query_user(user_id):
    cursor.execute(f"SELECT * FROM users WHERE id = {user_id}")
""",
        )
        func = result.functions[0]
        assert any("sql_injection" in f for f in func.security_findings)


class TestTypeScriptParserSecurity:
    def test_unsafe_call_detected(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "danger.ts",
            """
function runCode(code: string) {
    return eval(code);
}
""",
        )
        func = result.functions[0]
        assert any("unsafe_call:eval" in f for f in func.security_findings)

    def test_secret_detected(self):
        from gristle.parsers.typescript import TypeScriptParser

        parser = TypeScriptParser()
        result = parser.parse_file(
            "config.ts",
            """
function getClient() {
    const key = "AKIAIOSFODNN7ABCDEFG";
    return new Client(key);
}
""",
        )
        func = result.functions[0]
        assert any("hardcoded_secret" in f for f in func.security_findings)


# ======================================================================
# Pipeline
# ======================================================================


class TestPipelineSecurity:
    def test_function_node_has_security_properties(self):
        """Pipeline should set security_finding_count and security_findings on Function nodes."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.ingestion.pipeline import IngestionPipeline
        from gristle.models import ParsedFunction

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        pipeline = IngestionPipeline(mock_graph)

        batch = BatchCollector(mock_graph, batch_size=100)
        func = ParsedFunction(
            name="run",
            qualified_name="mod.run",
            file_path="mod.py",
            start_line=1,
            end_line=5,
            signature="def run(code)",
            security_findings=["unsafe_call:eval", "llm_output_risk:eval"],
        )

        file_id = "file::mod.py"
        pipeline._build_function(file_id, None, func, batch)

        # Find the Function node in the batch
        assert "Function" in batch._nodes
        func_nodes = batch._nodes["Function"]
        assert len(func_nodes) >= 1
        props = func_nodes[0]  # dict of properties
        assert props["security_finding_count"] == 2
        assert props["security_findings"] == ["unsafe_call:eval", "llm_output_risk:eval"]


# ======================================================================
# Query engine
# ======================================================================


class TestQuerySecurity:
    def test_detect_security_issues(self):
        engine, mock_graph = _make_engine()
        mock_graph.execute.return_value = _qr(
            [
                {
                    "qualified_name": "db.query_user",
                    "name": "query_user",
                    "file": "db.py",
                    "line": 42,
                    "findings": ["sql_injection:dynamic_query"],
                    "count": 1,
                },
                {
                    "qualified_name": "agent.run_code",
                    "name": "run_code",
                    "file": "agent.py",
                    "line": 15,
                    "findings": ["llm_output_risk:eval", "unsafe_call:eval"],
                    "count": 2,
                },
            ]
        )

        result = engine.detect_security_issues()
        assert result["total"] == 2
        assert result["by_category"]["sql_injection"] == 1
        assert result["by_category"]["llm_output_risk"] == 1
        assert result["by_category"]["unsafe_call"] == 1
        assert len(result["findings"]) == 2

    def test_detect_security_issues_empty(self):
        engine, mock_graph = _make_engine()
        mock_graph.execute.return_value = _empty()

        result = engine.detect_security_issues()
        assert result["total"] == 0
        assert result["by_category"] == {}

    def test_detect_unauthenticated_routes(self):
        engine, mock_graph = _make_engine()
        mock_graph.execute.return_value = _qr(
            [
                {
                    "method": "GET",
                    "path": "/api/public",
                    "handler": "get_public",
                    "qualified_name": "routes.get_public",
                    "decorators": [],
                    "file": "routes.py",
                },
            ]
        )

        result = engine.detect_unauthenticated_routes()
        assert result["total"] == 1
        assert result["unauthenticated_routes"][0]["path"] == "/api/public"

    def test_detect_unauthenticated_routes_empty(self):
        engine, mock_graph = _make_engine()
        mock_graph.execute.return_value = _empty()

        result = engine.detect_unauthenticated_routes()
        assert result["total"] == 0

    def test_get_security_overview(self):
        engine, mock_graph = _make_engine()
        # First call for code findings, second for unauthenticated routes, third for vulnerable deps
        mock_graph.execute.side_effect = [
            _qr(
                [
                    {
                        "qualified_name": "mod.run",
                        "name": "run",
                        "file": "mod.py",
                        "line": 5,
                        "findings": ["unsafe_call:eval"],
                        "count": 1,
                    }
                ]
            ),
            _qr(
                [
                    {
                        "method": "GET",
                        "path": "/health",
                        "handler": "health",
                        "qualified_name": "routes.health",
                        "decorators": [],
                        "file": "routes.py",
                    }
                ]
            ),
            _empty(),  # get_outdated_dependencies (no vulnerable deps)
        ]

        result = engine.get_security_overview()
        assert result["total_issues"] == 2
        assert result["code_findings"]["total"] == 1
        assert result["unauthenticated_routes"]["total"] == 1
        assert "vulnerable_dependencies" in result


# ======================================================================
# MCP tools
# ======================================================================


@pytest.fixture()
def _clean_mcp_state():
    """Ensure clean MCP engine state for each test."""
    import gristle.mcp.server as srv

    saved = dict(srv._engines)
    srv._engines.clear()
    yield
    srv._engines.clear()
    srv._engines.update(saved)


@pytest.mark.usefixtures("_clean_mcp_state")
class TestMCPSecurity:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_security

        result = await gristle_security()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_overview(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_security

        engine = MagicMock()
        engine.get_security_overview.return_value = {
            "total_issues": 3,
            "code_findings": {"total": 2, "by_category": {}, "findings": []},
            "unauthenticated_routes": {"total": 1, "unauthenticated_routes": []},
        }
        srv._engines["r1"] = engine
        result = await gristle_security()
        assert result["total_issues"] == 3
        engine.get_security_overview.assert_called_once()


@pytest.mark.usefixtures("_clean_mcp_state")
class TestMCPUnauthRoutes:
    @pytest.mark.asyncio
    async def test_no_repo(self):
        from gristle.mcp.server import gristle_unauthenticated_routes

        result = await gristle_unauthenticated_routes()
        assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_routes(self):
        import gristle.mcp.server as srv
        from gristle.mcp.server import gristle_unauthenticated_routes

        engine = MagicMock()
        engine.detect_unauthenticated_routes.return_value = {
            "total": 1,
            "unauthenticated_routes": [
                {
                    "method": "GET",
                    "path": "/open",
                    "handler": "handle_open",
                    "qualified_name": "r.handle_open",
                    "decorators": [],
                    "file": "r.py",
                },
            ],
        }
        srv._engines["r1"] = engine
        result = await gristle_unauthenticated_routes()
        assert result["total"] == 1
        engine.detect_unauthenticated_routes.assert_called_once()
