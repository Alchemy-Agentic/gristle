"""Command-line interface for Gristle.

Gives developers a terminal path to value without wiring up an MCP client:

    gristle ingest .                 # index the current repo
    gristle overview --repo-id .     # see the indexed graph
    gristle explore myFunc --repo-id .
    gristle query "MATCH (f:Function) RETURN count(f)" --repo-id .
    gristle doctor                   # check the local setup
    gristle serve                    # start the MCP server (default)

Running ``gristle`` with no subcommand starts the MCP server, so existing MCP
client configs that invoke ``gristle`` keep working.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any

from gristle.config import settings


def _build_graph(repo_id: str):  # noqa: ANN202 - returns GraphClient (imported lazily)
    from gristle.graph.client import GraphClient

    return GraphClient(
        host=settings.falkordb_host,
        port=settings.falkordb_port,
        repo_id=repo_id,
        password=settings.falkordb_password,
    )


def _falkordb_down_message() -> str:
    return (
        f"error: cannot reach FalkorDB at {settings.falkordb_host}:{settings.falkordb_port}. "
        "Start it with: docker compose up -d falkordb"
    )


def _dump(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _cmd_ingest(args: argparse.Namespace) -> int:
    from gristle.graph.client import GraphClient
    from gristle.ingestion.pipeline import IngestionPipeline
    from gristle.parsers.registry import ParserRegistry

    rid = args.repo_id or GraphClient.repo_id_from_path(args.path)
    graph = _build_graph(rid)
    if not graph.ping():
        print(_falkordb_down_message(), file=sys.stderr)
        return 1

    pipeline = IngestionPipeline(graph, ParserRegistry().build_default())
    result = pipeline.ingest_repo(args.path)

    print(f"Indexed {result.files_processed} files into graph '{graph.graph_name}' (repo_id={rid})")
    print(f"  {result.nodes_created} nodes, {result.relationships_created} relationships")
    print(
        f"  {result.routes_found} routes, {result.components_found} components, "
        f"{result.test_cases_found} tests, {result.dependencies_found} dependencies"
    )
    if result.errors:
        print(f"  {len(result.errors)} non-fatal errors (first: {result.errors[0]})", file=sys.stderr)
    print(f"\nNext: gristle overview --repo-id {rid}")
    return 0


def _cmd_overview(args: argparse.Namespace) -> int:
    from gristle.query.engine import QueryEngine

    graph = _build_graph(args.repo_id)
    if not graph.ping():
        print(_falkordb_down_message(), file=sys.stderr)
        return 1
    _dump(QueryEngine(graph).get_repo_overview())
    return 0


def _cmd_explore(args: argparse.Namespace) -> int:
    from gristle.query.engine import QueryEngine

    graph = _build_graph(args.repo_id)
    if not graph.ping():
        print(_falkordb_down_message(), file=sys.stderr)
        return 1
    engine = QueryEngine(graph)
    result = engine.get_function_context(args.entity) or engine.get_class_structure(args.entity)
    if result is None:
        print(f"No function or class named '{args.entity}' found in repo '{args.repo_id}'.", file=sys.stderr)
        return 1
    _dump(result)
    return 0


def _cmd_query(args: argparse.Namespace) -> int:
    graph = _build_graph(args.repo_id)
    if not graph.ping():
        print(_falkordb_down_message(), file=sys.stderr)
        return 1
    _dump(graph.execute(args.cypher).records)
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    ok = True
    print("gristle doctor\n")

    graph = _build_graph("doctor")
    reachable = graph.ping()
    print(
        f"  [{'OK ' if reachable else 'FAIL'}] FalkorDB reachable at {settings.falkordb_host}:{settings.falkordb_port}"
    )
    if not reachable:
        print("         fix: docker compose up -d falkordb")
        ok = False

    try:
        from gristle.parsers.registry import ParserRegistry

        exts = sorted(ParserRegistry().build_default().supported_extensions)
        print(f"  [OK ] Parsers available: {', '.join(exts)}")
    except Exception as e:  # noqa: BLE001 - diagnostic output
        print(f"  [FAIL] Parser registry failed: {e}")
        ok = False

    print("\n  Config:")
    print(f"    transport         = {settings.transport}")
    print(f"    falkordb          = {settings.falkordb_host}:{settings.falkordb_port}")
    print(f"    repo_storage_path = {settings.repo_storage_path}")
    print(f"    api_key           = {'set' if settings.api_key else 'unset'}")

    if reachable:
        graphs = graph.list_gristle_graphs()
        print(f"\n  Ingested graphs: {len(graphs)}")
        for g in graphs:
            print(f"    - {g}")

    print("\n" + ("All checks passed." if ok else "Issues found — see above."))
    return 0 if ok else 1


def _cmd_serve(args: argparse.Namespace) -> int:
    from gristle.mcp.server import main as serve_main

    if getattr(args, "http", False):
        settings.transport = "streamable-http"
    serve_main()
    return 0


def _cmd_viz(args: argparse.Namespace) -> int:
    from pathlib import Path

    from gristle.query.engine import QueryEngine
    from gristle.viz import render_html

    graph = _build_graph(args.repo_id)
    if not graph.ping():
        print(_falkordb_down_message(), file=sys.stderr)
        return 1
    data = QueryEngine(graph).get_subgraph(
        view=args.view,
        center=args.center,
        depth=args.depth,
        limit=args.limit,
        models_only=args.models_only,
    )
    if "error" in data:
        print(f"error: {data['error']}", file=sys.stderr)
        return 1

    out = Path(args.out) if args.out else Path(settings.viz_output_path)
    out.write_text(render_html(data, title=f"{args.repo_id} · {args.view}"), encoding="utf-8")
    m = data["meta"]
    print(f"Wrote {out}  ({m['node_count']} nodes, {m['edge_count']} edges, view={m['view']})")
    if m.get("truncated"):
        print(f"  note: truncated to {m['limit']} nodes — raise --limit or narrow --center")
    if m["node_count"] == 0:
        print("  note: 0 nodes — check --center / --view (request_trace with no --center spans all routes)")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gristle", description="Graph-based code intelligence for AI agents.")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="Index a local repository into the code graph.")
    p_ingest.add_argument("path", help="Path to the repository root to index.")
    p_ingest.add_argument("--repo-id", default=None, help="Identifier for this repo (defaults to a hash of the path).")
    p_ingest.set_defaults(func=_cmd_ingest)

    p_overview = sub.add_parser("overview", help="Print a high-level overview of an indexed repo.")
    p_overview.add_argument("--repo-id", required=True, help="Identifier used at ingest time.")
    p_overview.set_defaults(func=_cmd_overview)

    p_explore = sub.add_parser("explore", help="Show structure/callers for a function or class.")
    p_explore.add_argument("entity", help="Function or class name (or qualified name).")
    p_explore.add_argument("--repo-id", required=True, help="Identifier used at ingest time.")
    p_explore.set_defaults(func=_cmd_explore)

    p_query = sub.add_parser("query", help="Run a raw Cypher query against a repo's graph.")
    p_query.add_argument("cypher", help="Cypher query string.")
    p_query.add_argument("--repo-id", required=True, help="Identifier used at ingest time.")
    p_query.set_defaults(func=_cmd_query)

    p_doctor = sub.add_parser("doctor", help="Check the local Gristle/FalkorDB setup.")
    p_doctor.set_defaults(func=_cmd_doctor)

    p_serve = sub.add_parser("serve", help="Start the MCP server (this is the default with no subcommand).")
    p_serve.add_argument("--http", action="store_true", help="Use streamable-http transport instead of stdio.")
    p_serve.set_defaults(func=_cmd_serve)

    p_viz = sub.add_parser("viz", help="Export an interactive self-contained HTML visualization of a view.")
    p_viz.add_argument("--repo-id", required=True, help="Identifier used at ingest time.")
    p_viz.add_argument(
        "--view",
        default="call_hierarchy",
        choices=["call_hierarchy", "blast_radius", "request_trace"],
        help="Which subgraph view to render (default: call_hierarchy).",
    )
    p_viz.add_argument("--center", default=None, help="Focal function/route — id, qualified_name, or route path.")
    p_viz.add_argument("--depth", type=int, default=2, help="Traversal depth, clamped to 1..4 (default: 2).")
    p_viz.add_argument(
        "--limit", type=int, default=None, help="Max nodes before truncation (default: GRISTLE_VIZ_MAX_NODES)."
    )
    p_viz.add_argument("--models-only", action="store_true", help="request_trace only: prune to route->DB-model paths.")
    p_viz.add_argument("--out", default=None, help="Output HTML path (default: GRISTLE_VIZ_OUTPUT_PATH).")
    p_viz.set_defaults(func=_cmd_viz)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        # No subcommand: start the MCP server so `gristle` keeps working in MCP
        # client configs that invoke the bare command.
        from gristle.mcp.server import main as serve_main

        serve_main()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
