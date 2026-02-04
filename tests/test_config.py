"""Tests for config file parsing, env var extraction, and pipeline integration."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.parsers.config import (
    classify_config_file,
    parse_config_file,
)
from gristle.parsers.env_vars import extract_env_var_refs

# ======================================================================
# classify_config_file
# ======================================================================


class TestClassifyConfigFile:
    def test_package_json(self):
        assert classify_config_file("package.json") == "package"

    def test_nested_package_json(self):
        assert classify_config_file("frontend/package.json") == "package"

    def test_tsconfig(self):
        assert classify_config_file("tsconfig.json") == "tsconfig"

    def test_dockerfile(self):
        assert classify_config_file("Dockerfile") == "dockerfile"

    def test_compose_yml(self):
        assert classify_config_file("docker-compose.yml") == "compose"

    def test_compose_yaml(self):
        assert classify_config_file("compose.yaml") == "compose"

    def test_requirements_txt(self):
        assert classify_config_file("requirements.txt") == "package"

    def test_pyproject_toml(self):
        assert classify_config_file("pyproject.toml") == "package"

    def test_ci_workflow(self):
        assert classify_config_file(".github/workflows/ci.yml") == "ci"

    def test_ci_workflow_yaml(self):
        assert classify_config_file(".github/workflows/deploy.yaml") == "ci"

    def test_env_example(self):
        assert classify_config_file(".env.example") == "env_template"

    def test_env_template(self):
        assert classify_config_file(".env.template") == "env_template"

    def test_env_sample(self):
        assert classify_config_file(".env.sample") == "env_template"

    def test_unknown_file(self):
        assert classify_config_file("src/main.py") is None

    def test_random_json(self):
        assert classify_config_file("data.json") is None


# ======================================================================
# parse_config_file
# ======================================================================


class TestParseConfigFile:
    def test_returns_none_for_unknown(self):
        assert parse_config_file("src/main.py", "print('hi')") is None

    def test_package_json_scripts(self):
        content = '{"scripts": {"build": "tsc", "test": "jest"}, "engines": {"node": ">=18"}}'
        result = parse_config_file("package.json", content)
        assert result is not None
        assert result.config_type == "package"
        assert "config_scripts" in result.properties
        assert "config_engines" in result.properties

    def test_package_json_malformed(self):
        result = parse_config_file("package.json", "not valid json {{{")
        assert result is not None
        assert result.config_type == "package"
        assert result.properties == {}

    def test_tsconfig_compiler_options(self):
        content = '{"compilerOptions": {"target": "es2022", "module": "esnext", "paths": {"@/*": ["src/*"]}}}'
        result = parse_config_file("tsconfig.json", content)
        assert result is not None
        assert result.properties["config_target"] == "es2022"
        assert result.properties["config_module"] == "esnext"
        assert "config_paths" in result.properties

    def test_dockerfile_base_image_and_env(self):
        content = """\
FROM node:18-alpine AS builder
WORKDIR /app
EXPOSE 3000
ENV NODE_ENV=production
ENV API_KEY
ARG BUILD_VERSION
FROM node:18-alpine
COPY --from=builder /app /app
EXPOSE 8080
"""
        result = parse_config_file("Dockerfile", content)
        assert result is not None
        assert result.config_type == "dockerfile"
        assert result.properties["config_base_image"] == "node:18-alpine"
        assert "3000" in result.properties["config_exposed_ports"]
        assert "8080" in result.properties["config_exposed_ports"]
        # ENV directives become env vars
        assert len(result.env_vars) == 2
        names = {ev.name for ev in result.env_vars}
        assert "NODE_ENV" in names
        assert "API_KEY" in names
        # NODE_ENV has a default
        node_env = next(ev for ev in result.env_vars if ev.name == "NODE_ENV")
        assert node_env.default_value == "production"

    def test_env_template(self):
        content = """\
# Database settings
DATABASE_URL=postgres://localhost:5432/mydb
SECRET_KEY=
API_TOKEN
# Comment
NOT_UPPER_case=skip
"""
        result = parse_config_file(".env.example", content)
        assert result is not None
        assert result.config_type == "env_template"
        names = {ev.name for ev in result.env_vars}
        assert "DATABASE_URL" in names
        assert "SECRET_KEY" in names
        assert "API_TOKEN" in names
        assert "NOT_UPPER_case" not in names  # Doesn't match pattern
        # DATABASE_URL has a default, so not required
        db_url = next(ev for ev in result.env_vars if ev.name == "DATABASE_URL")
        assert db_url.default_value == "postgres://localhost:5432/mydb"
        assert not db_url.required
        # API_TOKEN is bare key, so required
        api_token = next(ev for ev in result.env_vars if ev.name == "API_TOKEN")
        assert api_token.required

    def test_line_count(self):
        content = "line1\nline2\nline3"
        result = parse_config_file("package.json", content)
        assert result is not None
        assert result.line_count == 3


# ======================================================================
# extract_env_var_refs
# ======================================================================


class TestExtractEnvVarRefs:
    def test_python_os_environ_bracket(self):
        code = 'db_url = os.environ["DATABASE_URL"]'
        assert extract_env_var_refs(code, "python") == ["DATABASE_URL"]

    def test_python_os_environ_get(self):
        code = 'debug = os.environ.get("DEBUG", "false")'
        assert extract_env_var_refs(code, "python") == ["DEBUG"]

    def test_python_os_getenv(self):
        code = 'key = os.getenv("API_KEY")'
        assert extract_env_var_refs(code, "python") == ["API_KEY"]

    def test_python_multiple(self):
        code = """\
db = os.environ["DATABASE_URL"]
key = os.getenv("API_KEY")
debug = os.environ.get("DEBUG")
db2 = os.environ["DATABASE_URL"]  # duplicate
"""
        result = extract_env_var_refs(code, "python")
        assert result == ["API_KEY", "DATABASE_URL", "DEBUG"]

    def test_typescript_process_env_dot(self):
        code = "const key = process.env.API_KEY;"
        assert extract_env_var_refs(code, "typescript") == ["API_KEY"]

    def test_typescript_process_env_bracket(self):
        code = 'const key = process.env["SECRET_KEY"];'
        assert extract_env_var_refs(code, "typescript") == ["SECRET_KEY"]

    def test_javascript_same_as_typescript(self):
        code = "const url = process.env.DATABASE_URL;"
        assert extract_env_var_refs(code, "javascript") == ["DATABASE_URL"]

    def test_deno_env_get(self):
        code = 'const port = Deno.env.get("PORT");'
        assert extract_env_var_refs(code, "typescript") == ["PORT"]

    def test_no_matches(self):
        code = 'const x = 42; const name = "hello";'
        assert extract_env_var_refs(code, "typescript") == []


# ======================================================================
# Pipeline integration (config files + EnvVar nodes)
# ======================================================================


def _make_graph_mock() -> MagicMock:
    mock = MagicMock()
    mock.repo_id = "test"
    mock.batch_create_nodes.return_value = 0
    mock.batch_create_relationships.return_value = 0
    mock.batch_merge_relationships.return_value = 0
    return mock


def _extract_batch_nodes(mock_graph: MagicMock, label: str) -> list[dict]:
    """Extract nodes of a given label from batch_create_nodes calls."""
    nodes = []
    for call in mock_graph.batch_create_nodes.call_args_list:
        if call[0][0] == label:
            nodes.extend(call[0][1])
    return nodes


def _extract_batch_merge_rels(mock_graph: MagicMock, rel_type: str) -> list[dict]:
    """Extract merge relationships of a given type."""
    rels = []
    for call in mock_graph.batch_merge_relationships.call_args_list:
        if call[0][0] == rel_type:
            rels.extend(call[0][1])
    return rels


class TestPipelineConfigIntegration:
    """Test that the pipeline creates correct config File nodes and EnvVar nodes."""

    def test_config_file_creates_file_node_with_config_type(self):
        """Config files should create File nodes with config_type property."""
        from gristle.ingestion.batch import BatchCollector

        mock_graph = _make_graph_mock()
        batch = BatchCollector(mock_graph, 1000)

        parsed = parse_config_file("Dockerfile", "FROM python:3.11\nEXPOSE 8080\nENV APP_PORT=8080")
        assert parsed is not None

        file_id = "file::Dockerfile"
        props = {
            "id": file_id,
            "path": "Dockerfile",
            "language": "config",
            "line_count": parsed.line_count,
            "is_test_file": False,
            "config_type": parsed.config_type,
        }
        for key, value in parsed.properties.items():
            props[key] = value
        batch.add_node("File", props)

        for env_var in parsed.env_vars:
            env_id = f"envvar::{env_var.name}"
            batch.add_node(
                "EnvVar",
                {
                    "id": env_id,
                    "name": env_var.name,
                    "default_value": env_var.default_value or "",
                    "required": env_var.required,
                },
            )
            batch.add_merge_relationship("DEFINED_IN", env_id, file_id)

        batch.flush()

        # Verify File node was created with config_type
        file_nodes = _extract_batch_nodes(mock_graph, "File")
        assert len(file_nodes) == 1
        assert file_nodes[0]["config_type"] == "dockerfile"
        assert file_nodes[0]["config_base_image"] == "python:3.11"

        # Verify EnvVar node was created
        env_nodes = _extract_batch_nodes(mock_graph, "EnvVar")
        assert len(env_nodes) == 1
        assert env_nodes[0]["name"] == "APP_PORT"

        # Verify DEFINED_IN edge
        defined_in = _extract_batch_merge_rels(mock_graph, "DEFINED_IN")
        assert len(defined_in) == 1
        assert defined_in[0]["from_id"] == "envvar::APP_PORT"
        assert defined_in[0]["to_id"] == "file::Dockerfile"

    def test_env_var_refs_create_uses_env_edges(self):
        """Source files with env_var_refs should create USES_ENV edges."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.models import ParsedFile

        mock_graph = _make_graph_mock()
        batch = BatchCollector(mock_graph, 1000)

        # Simulate a source file that references env vars
        pf = ParsedFile(
            path="src/config.py",
            language="python",
            env_var_refs=["DATABASE_URL", "API_KEY"],
        )

        env_var_ids: dict[str, str] = {}
        file_id = f"file::{pf.path}"

        for var_name in pf.env_var_refs:
            env_id = f"envvar::{var_name}"
            if var_name not in env_var_ids:
                batch.add_node(
                    "EnvVar",
                    {
                        "id": env_id,
                        "name": var_name,
                        "default_value": "",
                        "required": False,
                    },
                )
                env_var_ids[var_name] = env_id
            batch.add_merge_relationship("USES_ENV", file_id, env_id)

        batch.flush()

        env_nodes = _extract_batch_nodes(mock_graph, "EnvVar")
        assert len(env_nodes) == 2
        names = {n["name"] for n in env_nodes}
        assert names == {"DATABASE_URL", "API_KEY"}

        uses_env = _extract_batch_merge_rels(mock_graph, "USES_ENV")
        assert len(uses_env) == 2

    def test_dedup_env_var_nodes(self):
        """EnvVar nodes should be deduplicated across config and source files."""
        from gristle.ingestion.batch import BatchCollector
        from gristle.models import ParsedFile

        mock_graph = _make_graph_mock()
        batch = BatchCollector(mock_graph, 1000)
        env_var_ids: dict[str, str] = {}

        # Config file defines DATABASE_URL
        config = parse_config_file(".env.example", "DATABASE_URL=postgres://localhost/db")
        assert config is not None
        for ev in config.env_vars:
            env_id = f"envvar::{ev.name}"
            if ev.name not in env_var_ids:
                batch.add_node("EnvVar", {"id": env_id, "name": ev.name})
                env_var_ids[ev.name] = env_id

        # Source file also references DATABASE_URL
        pf = ParsedFile(path="src/db.py", language="python", env_var_refs=["DATABASE_URL"])
        for var_name in pf.env_var_refs:
            env_id = f"envvar::{var_name}"
            if var_name not in env_var_ids:
                batch.add_node("EnvVar", {"id": env_id, "name": var_name})
                env_var_ids[var_name] = env_id

        batch.flush()

        # Only one EnvVar node should be created
        env_nodes = _extract_batch_nodes(mock_graph, "EnvVar")
        assert len(env_nodes) == 1
        assert env_nodes[0]["name"] == "DATABASE_URL"


# ======================================================================
# Query engine — config methods
# ======================================================================


class TestQueryEngineConfig:
    def _make_engine(self, records_map: dict[str, list[dict]] | None = None):
        """Create a QueryEngine with a mock graph that returns configured results."""
        from gristle.query.engine import QueryEngine

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"

        if records_map is None:
            records_map = {}

        call_count = [0]

        def mock_execute(query, params=None):
            call_count[0] += 1
            result = MagicMock()
            # Match based on query content
            for key, recs in records_map.items():
                if key in query:
                    result.records = recs
                    return result
            result.records = []
            return result

        mock_graph.execute.side_effect = mock_execute
        return QueryEngine(mock_graph)

    def test_get_env_vars(self):
        engine = self._make_engine(
            {
                "EnvVar": [
                    {
                        "name": "DATABASE_URL",
                        "default_value": "postgres://localhost/db",
                        "required": False,
                        "defined_in": [".env.example"],
                        "used_by": ["src/db.py"],
                    },
                    {
                        "name": "API_KEY",
                        "default_value": "",
                        "required": True,
                        "defined_in": [".env.example"],
                        "used_by": ["src/auth.py", "src/api.py"],
                    },
                ],
            }
        )
        result = engine.get_env_vars()
        assert result["total"] == 2
        assert len(result["env_vars"]) == 2

    def test_get_config_files(self):
        engine = self._make_engine(
            {
                "config_type": [
                    {"path": "Dockerfile", "config_type": "dockerfile", "line_count": 15},
                    {"path": "docker-compose.yml", "config_type": "compose", "line_count": 30},
                ],
            }
        )
        result = engine.get_config_files()
        assert result["total"] == 2

    def test_get_setup_requirements(self):
        engine = self._make_engine(
            {
                "EnvVar": [
                    {"name": "DB_URL", "required": True, "defined_in": [], "used_by": [], "default_value": ""},
                    {"name": "DEBUG", "required": False, "defined_in": [], "used_by": [], "default_value": "false"},
                ],
                "config_type": [
                    {"path": "Dockerfile", "config_type": "dockerfile", "line_count": 10},
                ],
                "Dependency": [{"total": 15}],
            }
        )
        result = engine.get_setup_requirements()
        assert len(result["required_env_vars"]) == 1
        assert len(result["optional_env_vars"]) == 1
        assert len(result["config_files"]) == 1
        assert result["dependency_count"] == 15


# ======================================================================
# Walker — config file discovery
# ======================================================================


class TestWalkConfigFiles:
    def test_walk_finds_config_files(self, tmp_path):
        """walk_config_files should find known config files."""
        from gristle.ingestion.walker import walk_config_files

        # Create some config files
        (tmp_path / "package.json").write_text('{"name": "test"}')
        (tmp_path / "Dockerfile").write_text("FROM node:18")
        (tmp_path / ".env.example").write_text("DATABASE_URL=")
        # Create a source file (should NOT be found)
        (tmp_path / "main.py").write_text("print('hi')")
        # Create a nested CI workflow
        gh_dir = tmp_path / ".github" / "workflows"
        gh_dir.mkdir(parents=True)
        (gh_dir / "ci.yml").write_text("on: push")

        results = walk_config_files(tmp_path)
        paths = {r.relative_path for r in results}
        assert "package.json" in paths
        assert "Dockerfile" in paths
        assert ".env.example" in paths
        assert ".github/workflows/ci.yml" in paths
        assert "main.py" not in paths

    def test_walk_skips_node_modules(self, tmp_path):
        """Config files inside excluded dirs should be skipped."""
        from gristle.ingestion.walker import walk_config_files

        nm = tmp_path / "node_modules" / "pkg"
        nm.mkdir(parents=True)
        (nm / "package.json").write_text('{"name": "dep"}')
        (tmp_path / "package.json").write_text('{"name": "root"}')

        results = walk_config_files(tmp_path)
        paths = {r.relative_path for r in results}
        assert "package.json" in paths
        assert "node_modules/pkg/package.json" not in paths


# ======================================================================
# Layer violation detection
# ======================================================================


class TestLayerViolations:
    def _make_engine(self, import_pairs: list[tuple[str, str]]):
        """Create a QueryEngine with mock IMPORTS edges."""
        from gristle.query.engine import QueryEngine

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"

        result = MagicMock()
        result.records = [{"source": s, "target": t} for s, t in import_pairs]
        mock_graph.execute.return_value = result
        return QueryEngine(mock_graph)

    def test_presentation_to_data_is_violation(self):
        """Routes importing directly from db/ should be a violation."""
        engine = self._make_engine(
            [
                ("src/routes/users.py", "src/db/models.py"),
            ]
        )
        result = engine.detect_layer_violations()
        assert result["total"] == 1
        assert result["violations"][0]["source_layer"] == "presentation"
        assert result["violations"][0]["target_layer"] == "data"

    def test_presentation_to_business_is_allowed(self):
        """Routes importing from services/ is adjacent — no violation."""
        engine = self._make_engine(
            [
                ("src/routes/users.py", "src/services/user_service.py"),
            ]
        )
        result = engine.detect_layer_violations()
        assert result["total"] == 0

    def test_business_to_data_is_allowed(self):
        """Services importing from adapters/ is adjacent — no violation."""
        engine = self._make_engine(
            [
                ("src/services/user_service.py", "src/adapters/db.py"),
            ]
        )
        result = engine.detect_layer_violations()
        assert result["total"] == 0

    def test_cross_cutting_is_exempt(self):
        """Imports to/from utils/ should never be violations."""
        engine = self._make_engine(
            [
                ("src/routes/users.py", "src/utils/helpers.py"),
                ("src/utils/helpers.py", "src/db/models.py"),
            ]
        )
        result = engine.detect_layer_violations()
        assert result["total"] == 0

    def test_unclassified_files_ignored(self):
        """Files not matching any layer pattern should be ignored."""
        engine = self._make_engine(
            [
                ("src/config.py", "src/db/models.py"),
            ]
        )
        result = engine.detect_layer_violations()
        assert result["total"] == 0

    def test_multiple_violations_grouped_by_type(self):
        """Multiple violations should be grouped in by_type."""
        engine = self._make_engine(
            [
                ("src/routes/users.py", "src/db/models.py"),
                ("src/handlers/api.py", "src/repositories/user_repo.py"),
            ]
        )
        result = engine.detect_layer_violations()
        assert result["total"] == 2
        assert "presentation→data" in result["by_type"]
        assert result["by_type"]["presentation→data"] == 2

    def test_custom_layer_config(self):
        """Custom layer config should override defaults."""
        custom = {
            "api": (3, "api"),
            "core": (1, "core"),
        }
        engine = self._make_engine(
            [
                ("src/api/handler.py", "src/core/db.py"),
            ]
        )
        result = engine.detect_layer_violations(layer_config=custom)
        assert result["total"] == 1
        assert result["violations"][0]["violation_type"] == "api→core"

    def test_classify_layer_deepest_match(self):
        """_classify_layer should use the deepest matching directory."""
        from gristle.query.engine import QueryEngine

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        engine = QueryEngine(mock_graph)

        # "routes" is deeper than "src" (which isn't a layer)
        layer = engine._classify_layer("src/routes/users.py")
        assert layer == (3, "presentation")

        # No match
        assert engine._classify_layer("src/main.py") is None
