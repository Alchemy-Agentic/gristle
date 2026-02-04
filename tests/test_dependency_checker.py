"""Tests for dependency staleness and vulnerability checking."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import httpx
import pytest

from gristle.graph.client import QueryResult
from gristle.ingestion.dependency_checker import (
    _fetch_npm_latest,
    _fetch_osv_batch,
    _fetch_pypi_latest,
    _is_outdated,
    _strip_version_range,
    check_dependencies,
    clear_cache,
)
from gristle.query.engine import QueryEngine


def _qr(records: list[dict]) -> QueryResult:
    return QueryResult(records=records, summary={})


def _empty() -> QueryResult:
    return _qr([])


# ======================================================================
# Version utilities
# ======================================================================


class TestStripVersionRange:
    def test_caret(self):
        assert _strip_version_range("^18.2.0") == "18.2.0"

    def test_tilde(self):
        assert _strip_version_range("~2.3.0") == "2.3.0"

    def test_gte(self):
        assert _strip_version_range(">=1.0.0") == "1.0.0"

    def test_exact(self):
        assert _strip_version_range("==2.0.0") == "2.0.0"

    def test_comma_range(self):
        assert _strip_version_range(">=1.0.0,<2") == "1.0.0"

    def test_plain_version(self):
        assert _strip_version_range("3.11.0") == "3.11.0"


class TestIsOutdated:
    def test_npm_outdated(self):
        assert _is_outdated("17.0.0", "18.2.0", "npm") is True

    def test_npm_current(self):
        assert _is_outdated("18.2.0", "18.2.0", "npm") is False

    def test_python_outdated(self):
        assert _is_outdated("==2.0.0", "2.31.0", "PyPI") is True

    def test_python_current(self):
        assert _is_outdated(">=2.31.0", "2.31.0", "PyPI") is False

    def test_invalid_version_returns_false(self):
        assert _is_outdated("not-a-version", "1.0.0", "npm") is False

    def test_empty_latest_returns_false(self):
        assert _is_outdated("1.0.0", "", "npm") is False

    def test_caret_stripped(self):
        assert _is_outdated("^17.0.0", "18.2.0", "npm") is True


# ======================================================================
# API fetchers (all mocked)
# ======================================================================


class TestFetchNpmLatest:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"dist-tags": {"latest": "18.2.0"}}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = mock_response

        result = _fetch_npm_latest(mock_client, "react")
        assert result == "18.2.0"
        mock_client.get.assert_called_once()

    def test_network_error(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = httpx.ConnectError("Connection refused")

        result = _fetch_npm_latest(mock_client, "react")
        assert result is None

    def test_scoped_package(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"dist-tags": {"latest": "1.0.0"}}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = mock_response

        _fetch_npm_latest(mock_client, "@hono/zod-validator")
        call_args = mock_client.get.call_args
        assert "%2f" in call_args[0][0]


class TestFetchPypiLatest:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "info": {"version": "2.31.0"},
            "vulnerabilities": [],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = mock_response

        version, vulns = _fetch_pypi_latest(mock_client, "requests")
        assert version == "2.31.0"
        assert vulns == []

    def test_with_vulnerabilities(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "info": {"version": "2.31.0"},
            "vulnerabilities": [
                {"id": "PYSEC-2023-001"},
                {"id": "CVE-2023-1234"},
            ],
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.return_value = mock_response

        version, vulns = _fetch_pypi_latest(mock_client, "requests")
        assert version == "2.31.0"
        assert vulns == ["PYSEC-2023-001", "CVE-2023-1234"]

    def test_network_error(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.get.side_effect = httpx.ConnectError("timeout")

        version, vulns = _fetch_pypi_latest(mock_client, "requests")
        assert version is None
        assert vulns == []


class TestFetchOsvBatch:
    def test_success(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "results": [
                {"vulns": [{"id": "GHSA-abc-123"}]},
                {"vulns": []},
            ]
        }
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response

        packages = [
            ("lodash", "4.17.20", "npm"),
            ("express", "4.18.0", "npm"),
        ]
        result = _fetch_osv_batch(mock_client, packages)
        assert result == {"lodash": ["GHSA-abc-123"]}

    def test_empty_results(self):
        mock_response = MagicMock()
        mock_response.json.return_value = {"results": [{"vulns": []}, {"vulns": []}]}
        mock_response.raise_for_status = MagicMock()

        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.return_value = mock_response

        result = _fetch_osv_batch(mock_client, [("express", "4.18.0", "npm")])
        assert result == {}

    def test_network_error(self):
        mock_client = MagicMock(spec=httpx.Client)
        mock_client.post.side_effect = httpx.ConnectError("timeout")

        result = _fetch_osv_batch(mock_client, [("express", "4.18.0", "npm")])
        assert result == {}


# ======================================================================
# Integration — check_dependencies
# ======================================================================


class TestCheckDependencies:
    def setup_method(self):
        clear_cache()

    @patch("gristle.ingestion.dependency_checker._fetch_osv_batch")
    @patch("gristle.ingestion.dependency_checker._fetch_pypi_latest")
    @patch("gristle.ingestion.dependency_checker._fetch_npm_latest")
    @patch("gristle.ingestion.dependency_checker.httpx.Client")
    def test_mixed_ecosystems(self, mock_client_cls, mock_npm, mock_pypi, mock_osv):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_npm.return_value = "18.2.0"
        mock_pypi.return_value = ("2.31.0", [])
        mock_osv.return_value = {}

        deps = [
            ("react", "^17.0.0", "npm"),
            ("requests", ">=2.0.0", "PyPI"),
        ]
        results = check_dependencies(deps, timeout=1.0)

        assert "react" in results
        assert results["react"].is_outdated is True
        assert results["react"].latest_version == "18.2.0"

        assert "requests" in results
        assert results["requests"].is_outdated is True
        assert results["requests"].latest_version == "2.31.0"

    def test_disabled(self):
        results = check_dependencies(
            [("react", "^17.0.0", "npm")],
            enabled=False,
        )
        assert results == {}

    @patch("gristle.ingestion.dependency_checker._fetch_osv_batch")
    @patch("gristle.ingestion.dependency_checker._fetch_npm_latest")
    @patch("gristle.ingestion.dependency_checker.httpx.Client")
    def test_all_fetches_fail(self, mock_client_cls, mock_npm, mock_osv):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_npm.return_value = None
        mock_osv.return_value = {}

        results = check_dependencies([("react", "^17.0.0", "npm")])
        assert "react" in results
        assert results["react"].latest_version == ""
        assert results["react"].is_outdated is False

    @patch("gristle.ingestion.dependency_checker._fetch_osv_batch")
    @patch("gristle.ingestion.dependency_checker._fetch_npm_latest")
    @patch("gristle.ingestion.dependency_checker.httpx.Client")
    def test_cache_hit(self, mock_client_cls, mock_npm, mock_osv):
        mock_client_cls.return_value.__enter__ = MagicMock(return_value=MagicMock())
        mock_client_cls.return_value.__exit__ = MagicMock(return_value=False)

        mock_npm.return_value = "18.2.0"
        mock_osv.return_value = {}

        deps = [("react", "^17.0.0", "npm")]

        # First call — populates cache
        results1 = check_dependencies(deps, timeout=1.0)
        assert results1["react"].latest_version == "18.2.0"

        # Second call — should use cache, not call APIs again
        mock_npm.reset_mock()
        results2 = check_dependencies(deps, timeout=1.0)
        assert results2["react"].latest_version == "18.2.0"
        mock_npm.assert_not_called()

    def test_empty_deps(self):
        results = check_dependencies([])
        assert results == {}


# ======================================================================
# Pipeline integration
# ======================================================================


class TestPipelineDependencyEnrichment:
    def test_dependency_node_with_enrichment(self):
        """Pipeline should set health properties on Dependency nodes."""
        from gristle.ingestion.pipeline import IngestionPipeline

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        pipeline = IngestionPipeline(mock_graph)

        # Simulate ecosystem tracking
        pipeline._dependency_versions["react"] = "^17.0.0"
        pipeline._dependency_ecosystems["react"] = "npm"

        assert pipeline._dependency_ecosystems["react"] == "npm"

    def test_dependency_ecosystems_cleared(self):
        """Pipeline._reset() should clear ecosystem map."""
        from gristle.ingestion.pipeline import IngestionPipeline

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        pipeline = IngestionPipeline(mock_graph)

        pipeline._dependency_ecosystems["react"] = "npm"
        pipeline._dependency_versions.clear()
        pipeline._dependency_ecosystems.clear()
        assert pipeline._dependency_ecosystems == {}

    def test_ecosystem_detection_package_json(self):
        """package.json deps should get ecosystem 'npm'."""
        import json
        import tempfile
        from pathlib import Path

        from gristle.ingestion.pipeline import IngestionPipeline

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        pipeline = IngestionPipeline(mock_graph)

        with tempfile.TemporaryDirectory() as tmp:
            pkg_json = Path(tmp) / "package.json"
            pkg_json.write_text(json.dumps({"dependencies": {"react": "^18.2.0", "express": "^4.18.0"}}))
            pipeline._extract_dependency_versions(tmp)

        assert pipeline._dependency_ecosystems.get("react") == "npm"
        assert pipeline._dependency_ecosystems.get("express") == "npm"
        assert pipeline._dependency_versions.get("react") == "^18.2.0"

    def test_ecosystem_detection_requirements_txt(self):
        """requirements.txt deps should get ecosystem 'PyPI'."""
        import tempfile
        from pathlib import Path

        from gristle.ingestion.pipeline import IngestionPipeline

        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        pipeline = IngestionPipeline(mock_graph)

        with tempfile.TemporaryDirectory() as tmp:
            req_file = Path(tmp) / "requirements.txt"
            req_file.write_text("requests>=2.28.0\nflask==2.3.0\n")
            pipeline._extract_dependency_versions(tmp)

        assert pipeline._dependency_ecosystems.get("requests") == "PyPI"
        assert pipeline._dependency_ecosystems.get("flask") == "PyPI"


# ======================================================================
# Query engine
# ======================================================================


class TestQueryDependencyHealth:
    def _make_engine(self):
        mock_graph = MagicMock()
        mock_graph.repo_id = "test"
        engine = QueryEngine(mock_graph)
        return engine, mock_graph

    def test_get_outdated_dependencies_all(self):
        engine, mock_graph = self._make_engine()
        mock_graph.execute.return_value = _qr(
            [
                {
                    "name": "react",
                    "declared_version": "^17.0.0",
                    "latest_version": "18.2.0",
                    "vulnerability_count": 0,
                    "vulnerabilities": [],
                    "checked_at": "2026-01-01T00:00:00",
                    "file_count": 5,
                },
                {
                    "name": "lodash",
                    "declared_version": "^4.17.20",
                    "latest_version": "4.17.21",
                    "vulnerability_count": 2,
                    "vulnerabilities": ["CVE-2021-23337", "CVE-2020-28500"],
                    "checked_at": "2026-01-01T00:00:00",
                    "file_count": 3,
                },
            ]
        )

        result = engine.get_outdated_dependencies(severity="all")
        assert result["total"] == 2
        assert len(result["outdated"]) == 2
        assert result["vulnerable_count"] == 1

    def test_get_outdated_dependencies_vulnerable_only(self):
        engine, mock_graph = self._make_engine()
        mock_graph.execute.return_value = _qr(
            [
                {"name": "react", "vulnerability_count": 0, "file_count": 5},
                {"name": "lodash", "vulnerability_count": 2, "file_count": 3},
            ]
        )

        result = engine.get_outdated_dependencies(severity="vulnerable")
        assert result["total"] == 1
        assert result["outdated"][0]["name"] == "lodash"

    def test_get_dependency_users_with_health(self):
        engine, mock_graph = self._make_engine()
        mock_graph.execute.side_effect = [
            # Files query
            _qr([{"file_path": "src/app.ts"}]),
            # Functions query
            _qr(
                [
                    {
                        "name": "handler",
                        "qualified_name": "app.handler",
                        "file_path": "src/app.ts",
                        "start_line": 10,
                        "is_test": False,
                    }
                ]
            ),
            # Health info query
            _qr(
                [
                    {
                        "version": "^17.0.0",
                        "latest_version": "18.2.0",
                        "is_outdated": True,
                        "vulnerability_count": 0,
                        "vulnerabilities": [],
                    }
                ]
            ),
        ]

        result = engine.get_dependency_users("react")
        assert result["version"] == "^17.0.0"
        assert result["latest_version"] == "18.2.0"
        assert result["is_outdated"] is True

    def test_security_overview_includes_deps(self):
        engine, mock_graph = self._make_engine()
        # detect_security_issues, detect_unauthenticated_routes, get_outdated_dependencies
        mock_graph.execute.side_effect = [
            _empty(),  # security issues
            _empty(),  # unauthenticated routes
            _qr([{"name": "lodash", "vulnerability_count": 1, "vulnerabilities": ["CVE-2021-23337"]}]),  # outdated deps
        ]

        result = engine.get_security_overview()
        assert "vulnerable_dependencies" in result
        assert result["vulnerable_dependencies"]["total"] == 1


# ======================================================================
# MCP tools
# ======================================================================


class TestMcpDependencyHealth:
    @pytest.mark.asyncio
    async def test_no_repo_returns_error(self):
        from gristle.mcp.server import gristle_dependency_health

        with patch("gristle.mcp.server._resolve_engine", return_value=None):
            result = await gristle_dependency_health()
            assert "error" in result

    @pytest.mark.asyncio
    async def test_returns_data(self):
        from gristle.mcp.server import gristle_dependency_health

        mock_engine = MagicMock()
        mock_engine.get_outdated_dependencies.return_value = {
            "total": 1,
            "outdated": [{"name": "react"}],
            "vulnerable_count": 0,
            "summary": {"total_outdated": 1, "with_vulnerabilities": 0},
        }

        with patch("gristle.mcp.server._resolve_engine", return_value=mock_engine):
            result = await gristle_dependency_health(severity="all")
            assert result["total"] == 1
            mock_engine.get_outdated_dependencies.assert_called_once_with(severity="all")

    @pytest.mark.asyncio
    async def test_security_includes_vuln_deps(self):
        from gristle.mcp.server import gristle_security

        mock_engine = MagicMock()
        mock_engine.get_security_overview.return_value = {
            "total_issues": 1,
            "code_findings": {"total": 0},
            "unauthenticated_routes": {"total": 0},
            "vulnerable_dependencies": {"total": 1},
        }

        with patch("gristle.mcp.server._resolve_engine", return_value=mock_engine):
            result = await gristle_security()
            assert "vulnerable_dependencies" in result
            assert result["vulnerable_dependencies"]["total"] == 1
