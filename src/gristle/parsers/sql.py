"""SQL migration/function parser (tree-sitter-sql).

Parses ``.sql`` files for Postgres ``CREATE FUNCTION`` definitions and extracts the
tables each function BODY reads and writes. This is where a stored procedure's real
table mutations live — invisible to the generated types (which carry only the
callable signature) and to the code-side ``.rpc()`` call. Linking a function's
body-table access to the existing ``DBFunction`` node completes the
``route -> handler -> CALLS_RPC -> DBFunction -> USES_MODEL -> Model`` chain, so a
table written only by an RPC is no longer invisible as a write target.

Names are schema-stripped (``public.deduct_credits`` -> ``deduct_credits``) to match
the bare names used by ``DBFunction`` / ``Model`` nodes (the generated types and
``.from('table')`` / ``.rpc('name')`` all use bare names). A table reference qualified
to a NON-public schema (``auth.users``, ``storage.objects``) is skipped — it is a
different physical table than a same-named public Model.

Known limitations (all UNDER-report — a missing edge, never a wrong one — and stem
from tree-sitter-sql's incomplete plpgsql coverage; the common direct-DML shape is
handled): the first statement inside a ``FOR rec IN SELECT ... LOOP`` cursor loop,
``RETURN QUERY SELECT ...`` read sources, ``DELETE ... USING`` sources, and
``TRUNCATE``/``MERGE`` targets are not captured; and a function whose header combines
``SECURITY DEFINER SET search_path`` with an ``IF ... RAISE ... END IF`` guard may
fail to be recognized as a function at all. These leave a table edge-less, never
mis-attributed.
"""

from __future__ import annotations

import re

import tree_sitter_sql as tssql
from tree_sitter import Language, Node, Parser

from gristle.models import ParsedSQLFunction

_SQL_LANGUAGE = Language(tssql.language())

# Byte offsets of every `CREATE [OR REPLACE] FUNCTION` in the file. Used to bound
# each function's table extraction — NOT to parse SQL. tree-sitter-sql's plpgsql
# coverage is incomplete, so an unparseable body error-recovers and can swallow the
# NEXT definition entirely (one create_function node spanning two functions). The
# AST-node bound then fails (no second node -> no bound), and function A would
# inherit function B's tables — fabricating a write edge. Scanning the raw text for
# the next CREATE FUNCTION keyword bounds A's walk regardless of how the tree merged.
_CREATE_FUNCTION_RE = re.compile(rb"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\b", re.IGNORECASE)


def parse_sql_schema(file_path: str, content: str) -> list[ParsedSQLFunction]:
    """Parse a ``.sql`` file, returning one :class:`ParsedSQLFunction` per
    ``CREATE FUNCTION`` (with the tables its body reads/writes)."""
    src = content.encode()
    parser = Parser(_SQL_LANGUAGE)
    root = parser.parse(src).root_node

    fn_boundaries = [m.start() for m in _CREATE_FUNCTION_RE.finditer(src)]
    create_fns = [n for n in _iter_descendants(root) if n.type == "create_function"]

    functions: list[ParsedSQLFunction] = []
    for node in create_fns:
        name = _function_name(node)
        body = node.child_by_field_name("body") or _first_child(node, "function_body")
        if not name or body is None:
            continue
        bound = next((b for b in fn_boundaries if b > node.start_byte), None)
        reads: set[str] = set()
        writes: set[str] = set()
        _collect_accesses(body, reads, writes, bound)
        # A CTE (`WITH x AS (...)`) referenced in FROM parses exactly like a real
        # table; drop CTE-defined names so a CTE named after a Model isn't a false
        # read. Then a written table is a write (drop it from reads).
        cte_names = _cte_names(body, bound)
        reads -= cte_names
        writes -= cte_names
        reads -= writes
        functions.append(
            ParsedSQLFunction(
                name=name,
                file_path=file_path,
                line=node.start_point[0] + 1,
                reads=reads,
                writes=writes,
            )
        )
    return functions


def _cte_names(body: Node, bound: int | None) -> set[str]:
    """Names defined by ``WITH <name> AS (...)`` clauses in the body — query-local
    aliases, not tables."""
    names: set[str] = set()
    for n in _iter_descendants(body):
        if bound is not None and n.start_byte >= bound:
            continue
        if n.type == "cte":
            ident = _first_child(n, "identifier")
            if ident is not None:
                text = (ident.text or b"").decode("utf-8", "replace").strip('"')
                if text:
                    names.add(text)
    return names


def _iter_descendants(node: Node):
    stack = [node]
    while stack:
        n = stack.pop()
        yield n
        stack.extend(n.named_children)


def _first_child(node: Node, type_name: str) -> Node | None:
    for c in node.named_children:
        if c.type == type_name:
            return c
    return None


def _ref_parts(obj_ref: Node) -> tuple[str | None, str | None]:
    """``(schema, bare_name)`` for an ``object_reference``; ``schema`` is ``None``
    when unqualified. ``public.user_roles`` -> ``("public", "user_roles")``."""
    idents = [c for c in obj_ref.named_children if c.type == "identifier"]
    if idents:
        name = (idents[-1].text or b"").decode("utf-8", "replace").strip('"') or None
        schema = (idents[-2].text or b"").decode("utf-8", "replace").strip('"') if len(idents) >= 2 else ""
        return (schema or None, name)
    parts = (obj_ref.text or b"").decode("utf-8", "replace").split(".")
    name = parts[-1].strip('"') or None
    schema = parts[-2].strip('"') if len(parts) >= 2 else ""
    return (schema or None, name)


def _table_name(obj_ref: Node) -> str | None:
    """Bare table name for a table reference, or ``None`` if it is schema-qualified to
    a NON-public schema (``auth.users``, ``storage.objects``). Those are different
    physical tables than a same-named ``public`` Model, so must not be conflated.
    Bare and ``public.``-qualified names both resolve to the bare name."""
    schema, name = _ref_parts(obj_ref)
    if schema is not None and schema.lower() != "public":
        return None
    return name


def _function_name(create_function: Node) -> str | None:
    ref = _first_child(create_function, "object_reference")
    return _ref_parts(ref)[1] if ref is not None else None


def _collect_accesses(body: Node, reads: set[str], writes: set[str], bound: int | None = None) -> None:
    """Classify each table ``object_reference`` in a function body as read/write.

    A table reference is an ``object_reference`` under a ``relation`` (SELECT/JOIN/
    UPDATE target / UPDATE...FROM source) or directly under ``insert`` (INTO target)
    or ``from`` (a DELETE target — SELECT/UPDATE FROM wrap the table in a ``relation``,
    DELETE does not). Anything else (column refs like ``u.id``, function invocations)
    is ignored. ``bound`` (byte offset of the next CREATE FUNCTION) caps the walk so
    error-recovery spillover never attributes another function's tables to this one.
    """
    stack = [body]
    while stack:
        n = stack.pop()
        if bound is not None and n.start_byte >= bound:
            continue
        parent = n.parent
        if n.type == "object_reference" and parent is not None:
            if parent.type == "relation":
                tbl = _table_name(n)
                if tbl:
                    grandparent = parent.parent
                    if grandparent is not None and grandparent.type == "update":
                        writes.add(tbl)
                    else:
                        reads.add(tbl)
                continue  # don't descend into schema/name identifiers
            if parent.type == "insert":
                tbl = _table_name(n)
                if tbl:
                    writes.add(tbl)
                continue
            if parent.type == "from":
                # A bare object_reference under `from` (no `relation` wrapper) is a
                # DELETE target; SELECT/UPDATE FROM sources always wrap in `relation`.
                tbl = _table_name(n)
                if tbl:
                    writes.add(tbl)
                continue
        stack.extend(n.named_children)
