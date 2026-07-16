"""Tests for the gristle CLI."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from gristle import cli


def test_no_command_starts_server():
    with patch("gristle.mcp.server.main") as serve:
        assert cli.main([]) == 0
        serve.assert_called_once()


def test_serve_subcommand_starts_server():
    with patch("gristle.mcp.server.main") as serve:
        assert cli.main(["serve"]) == 0
        serve.assert_called_once()


def test_doctor_ok_when_reachable():
    g = MagicMock()
    g.ping.return_value = True
    g.list_gristle_graphs.return_value = ["gristle_a"]
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["doctor"]) == 0


def test_doctor_fails_when_unreachable():
    g = MagicMock()
    g.ping.return_value = False
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["doctor"]) == 1


def test_ingest_reports_and_returns_zero(capsys):
    g = MagicMock()
    g.ping.return_value = True
    g.graph_name = "gristle_demo"
    result = MagicMock(
        files_processed=3,
        nodes_created=10,
        relationships_created=20,
        routes_found=1,
        components_found=0,
        test_cases_found=1,
        dependencies_found=2,
        errors=[],
    )
    with (
        patch("gristle.cli._build_graph", return_value=g),
        patch("gristle.ingestion.pipeline.IngestionPipeline") as pipeline_cls,
        patch("gristle.parsers.registry.ParserRegistry"),
    ):
        pipeline_cls.return_value.ingest_repo.return_value = result
        rc = cli.main(["ingest", "/some/path", "--repo-id", "demo"])
    assert rc == 0
    assert "Indexed 3 files" in capsys.readouterr().out


def test_ingest_falkordb_down_returns_one():
    g = MagicMock()
    g.ping.return_value = False
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["ingest", "/some/path", "--repo-id", "demo"]) == 1


def test_repos_lists_graphs(capsys):
    g = MagicMock()
    g.ping.return_value = True
    g.describe_gristle_graphs.return_value = [
        {
            "repo_id": "alpha",
            "graph": "gristle_alpha",
            "repo_path": "D:/projects/alpha",
            "last_ingested_at": "2026-06-30T10:00:00",
            "nodes": 1234,
        },
        {"repo_id": "old", "graph": "gristle_old", "repo_path": None, "last_ingested_at": None, "nodes": None},
    ]
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["repos"]) == 0
    out = capsys.readouterr().out
    assert "alpha" in out and "D:/projects/alpha" in out
    assert "2 graph(s)" in out


def test_repos_empty_server(capsys):
    g = MagicMock()
    g.ping.return_value = True
    g.describe_gristle_graphs.return_value = []
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["repos"]) == 0
    assert "No Gristle graphs" in capsys.readouterr().out


def test_drop_removes_existing_graph(capsys):
    g = MagicMock()
    g.ping.return_value = True
    g.graph_exists.return_value = True
    g.graph_name = "gristle_alpha"
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["drop", "alpha"]) == 0
    g.drop.assert_called_once()
    assert "Dropped graph 'gristle_alpha'" in capsys.readouterr().out


def test_drop_unknown_repo_id_errors():
    g = MagicMock()
    g.ping.return_value = True
    g.graph_exists.return_value = False
    with patch("gristle.cli._build_graph", return_value=g):
        assert cli.main(["drop", "nope"]) == 1
    g.drop.assert_not_called()
