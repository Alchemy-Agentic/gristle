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

tree-sitter-sql's plpgsql coverage is incomplete, so table access is extracted two ways
(:func:`_function_table_access`). When tree-sitter DID model a ``create_function``
node, its body is walked directly — the full parse resolves complex bodies (nested CTEs
inside ``FOR`` loops, multi-statement queries) far better than re-parsing in isolation.
When it did NOT (no node at all — most commonly a ``SECURITY DEFINER SET search_path =
...`` header combined with ``IF ... RAISE ... END IF`` guards), the function's
dollar-quoted body is re-parsed ON ITS OWN, where individual DML statements parse
cleanly; signature params, ``DECLARE``/loop variables, and CTE names are then subtracted
(a standalone body has no declaration context).

Both paths mask string/comment content so it is never read as live SQL: a table named
inside a ``RAISE``/``EXECUTE`` message (``RAISE EXCEPTION $m$cannot delete from
orders$m$`` — tree-sitter error-recovers the nested dollar-quote into SQL) is skipped,
and definition boundaries are found on a masked copy so a ``CREATE FUNCTION`` inside a
block comment (a commented-out old definition) or a dynamic-SQL string is not mistaken
for a real one. The downstream linker is name-gated to real Models as a final backstop.

Known limitations (all UNDER-report — a missing edge, never a wrong one — and stem
from tree-sitter-sql's incomplete plpgsql coverage; the common direct-DML shape is
handled): the first statement inside a ``FOR rec IN SELECT ... LOOP`` cursor loop,
``RETURN QUERY SELECT ...`` read sources, ``DELETE ... USING`` sources, dynamic
``EXECUTE``'d statements, and ``TRUNCATE``/``MERGE`` targets are not captured; a
function whose body is not dollar-quoted (``LANGUAGE sql AS 'SELECT ...'`` or the
SQL-standard ``RETURN`` form) yields nothing (tree-sitter treats a single-quoted body
as an opaque string); a non-ASCII function name is skipped by the text-recovery path;
and a parameter named after a real table is subtracted, dropping that (rare) edge.
These leave a table edge-less, never mis-attributed.
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
# The header up to the opening paren, capturing the (schema-stripped) function name.
_FN_HEADER_RE = re.compile(
    rb"\bCREATE\s+(?:OR\s+REPLACE\s+)?FUNCTION\s+(?:\"?\w+\"?\.)?\"?(\w+)\"?\s*\(", re.IGNORECASE
)
# Opening (and matching-tag-forming) dollar-quote delimiter: `$$` or `$tag$`.
_DOLLAR_OPEN = re.compile(rb"\$\w*\$")
# A plpgsql `DECLARE ... BEGIN` variable section (non-greedy to the first BEGIN).
_DECLARE_RE = re.compile(rb"\bDECLARE\b(.*?)\bBEGIN\b", re.IGNORECASE | re.DOTALL)
# Loop variables: `FOR rec IN ...`, `FOREACH elem IN ARRAY ...`.
_FOR_VAR_RE = re.compile(rb"\b(?:FOR|FOREACH)\s+(\w+)", re.IGNORECASE)
_IDENT_RE = re.compile(r"[A-Za-z_]\w*")


def parse_sql_schema(file_path: str, content: str) -> list[ParsedSQLFunction]:
    """Parse a ``.sql`` file, returning one :class:`ParsedSQLFunction` per
    ``CREATE FUNCTION`` (with the tables its body reads/writes)."""
    src = content.encode()
    parser = Parser(_SQL_LANGUAGE)
    root = parser.parse(src).root_node

    # Boundaries are found on a copy with comments and string/dollar-quoted literals
    # blanked (length preserved), so a `CREATE FUNCTION` sitting inside a block comment
    # (a commented-out old definition) or a string literal (dynamic DDL via EXECUTE)
    # is never mistaken for a real definition — which would otherwise fabricate a
    # fallback function and truncate the real one's table walk.
    masked = _mask_noncode(src)
    fn_boundaries = [m.start() for m in _CREATE_FUNCTION_RE.finditer(masked)]
    create_fns = [n for n in _iter_descendants(root) if n.type == "create_function"]

    functions: list[ParsedSQLFunction] = []
    parsed_starts: list[int] = []
    for node in create_fns:
        name = _function_name(node)
        body = node.child_by_field_name("body") or _first_child(node, "function_body")
        if not name or body is None:
            continue
        parsed_starts.append(node.start_byte)
        bound = next((b for b in fn_boundaries if b > node.start_byte), None)
        access = _function_table_access(src, node.start_byte, bound, body, parser, masked)
        if access is None:  # body is not None here, so this never fires — narrows for the type checker
            continue
        reads, writes = access
        functions.append(
            ParsedSQLFunction(
                name=name,
                file_path=file_path,
                line=node.start_point[0] + 1,
                reads=reads,
                writes=writes,
            )
        )

    functions.extend(_fallback_functions(src, file_path, fn_boundaries, parsed_starts, parser))
    return functions


def _fallback_functions(
    src: bytes,
    file_path: str,
    fn_boundaries: list[int],
    parsed_starts: list[int],
    parser: Parser,
) -> list[ParsedSQLFunction]:
    """Recover ``CREATE FUNCTION`` definitions tree-sitter produced no
    ``create_function`` node for (incomplete plpgsql coverage). Each missed
    function's dollar-quoted body is re-parsed on its own — individual DML
    statements parse cleanly even when the whole definition does not."""
    out: list[ParsedSQLFunction] = []
    for i, start in enumerate(fn_boundaries):
        end = fn_boundaries[i + 1] if i + 1 < len(fn_boundaries) else len(src)
        if any(start <= ps < end for ps in parsed_starts):
            continue  # already modelled by the AST path
        header = _FN_HEADER_RE.match(src, start)
        if header is None:
            continue
        access = _function_table_access(src, start, end, None, parser)
        if access is None:
            continue  # no dollar-quoted body to recover from
        reads, writes = access
        out.append(
            ParsedSQLFunction(
                name=header.group(1).decode("utf-8", "replace").strip('"'),
                file_path=file_path,
                line=src.count(b"\n", 0, start) + 1,
                reads=reads,
                writes=writes,
            )
        )
    return out


def _function_table_access(
    src: bytes,
    start: int,
    bound: int | None,
    ast_body: Node | None,
    parser: Parser,
    masked: bytes | None = None,
) -> tuple[set[str], set[str]] | None:
    """The tables a function reads/writes, as ``(reads, writes)``.

    When tree-sitter produced a body node (``ast_body``), walk it directly — the full
    parse resolves complex bodies (nested CTEs inside ``FOR`` loops, multi-statement
    queries) far better than re-parsing the body in isolation. ``object_reference``s
    whose byte offset falls in a masked region are skipped, so a table named inside a
    ``RAISE``/``EXECUTE`` message string (``RAISE EXCEPTION $m$cannot delete from
    orders$m$``) is not read as live SQL.

    When there is no body node (the fallback path — tree-sitter modelled no
    ``create_function`` at all, e.g. a ``SECURITY DEFINER SET search_path`` +
    ``IF ... RAISE`` shape), re-parse the dollar-quoted body ALONE (individual DML
    statements parse cleanly) on a masked copy, and subtract the function's signature
    params / ``DECLARE`` / loop vars / CTE names — a standalone body has no declaration
    context. Returns ``None`` when neither path can extract (no dollar-quoted body)."""
    reads: set[str] = set()
    writes: set[str] = set()
    region_end = bound if bound is not None else len(src)
    if ast_body is not None:
        # Mask nested string/comment literals WITHIN the body (not the file-level mask,
        # which blanks the whole dollar-quoted body) so a table named in a dollar-quoted
        # RAISE/EXECUTE message isn't read as live SQL, while real DML stays visible.
        span = _dollar_body_span(src, start, region_end)
        body_mask, mask_offset = (_mask_noncode(span[0]), span[1]) if span is not None else (None, 0)
        _collect_accesses(ast_body, reads, writes, bound, body_mask, mask_offset)
        cte = _cte_names(ast_body, bound)
        reads -= cte | writes
        writes -= cte
        return reads, writes
    body_bytes = _dollar_body(src[start:region_end])
    if body_bytes is None:
        return None
    body_root = parser.parse(_mask_noncode(body_bytes)).root_node
    _collect_accesses(body_root, reads, writes)
    locals_ = _cte_names(body_root, None) | _plpgsql_local_names(src, start, body_bytes)
    reads -= locals_ | writes
    writes -= locals_
    return reads, writes


def _mask_noncode(src: bytes) -> bytes:
    """Copy of ``src`` with SQL comments and string/dollar-quoted literals replaced by
    spaces (length preserved, so byte offsets still line up with the original). Used
    only to find real ``CREATE FUNCTION`` boundaries — a keyword inside a comment or a
    quoted string must not be mistaken for a definition."""
    out = bytearray(src)
    i, n = 0, len(src)
    while i < n:
        ch = src[i]
        if ch == 0x2D and i + 1 < n and src[i + 1] == 0x2D:  # -- line comment
            end = src.find(b"\n", i)
            end = n if end == -1 else end
        elif ch == 0x2F and i + 1 < n and src[i + 1] == 0x2A:  # /* block comment */ (nestable)
            depth, j = 1, i + 2
            while j < n and depth > 0:
                if src[j] == 0x2F and j + 1 < n and src[j + 1] == 0x2A:
                    depth, j = depth + 1, j + 2
                elif src[j] == 0x2A and j + 1 < n and src[j + 1] == 0x2F:
                    depth, j = depth - 1, j + 2
                else:
                    j += 1
            end = j
        elif ch == 0x27:  # '...' string ('' escapes a quote)
            j = i + 1
            while j < n:
                if src[j] == 0x27:
                    if j + 1 < n and src[j + 1] == 0x27:
                        j += 2
                        continue
                    j += 1
                    break
                j += 1
            end = j
        elif ch == 0x24 and (m := _DOLLAR_OPEN.match(src, i)) is not None:  # $tag$...$tag$
            close = src.find(m.group(0), m.end())
            end = n if close == -1 else close + len(m.group(0))
        else:
            i += 1
            continue
        for k in range(i, min(end, n)):
            out[k] = 0x20
        i = end
    return bytes(out)


def _dollar_body(region: bytes) -> bytes | None:
    """The bytes between a function's first ``$tag$`` and its matching close, or
    ``None`` if the region has no dollar-quoted body."""
    m = _DOLLAR_OPEN.search(region)
    if m is None:
        return None
    tag = m.group(0)
    body_start = m.end()
    close = region.find(tag, body_start)
    if close == -1:
        return None
    return region[body_start:close]


def _dollar_body_span(src: bytes, start: int, end: int) -> tuple[bytes, int] | None:
    """The dollar-quoted body content of the function at ``src[start:end]`` together with
    its absolute byte offset in ``src``, or ``None`` if there is no dollar-quoted body."""
    region = src[start:end]
    m = _DOLLAR_OPEN.search(region)
    if m is None:
        return None
    tag = m.group(0)
    body_start = m.end()
    close = region.find(tag, body_start)
    if close == -1:
        return None
    return region[body_start:close], start + body_start


def _plpgsql_local_names(src: bytes, start: int, body_bytes: bytes) -> set[str]:
    """Names that are function-local (signature parameters, ``DECLARE`` variables,
    ``FOR``/``FOREACH`` loop variables) and so must never be treated as tables when a
    body is parsed without its declaration context."""
    names = _signature_param_names(src, start)
    declare = _DECLARE_RE.search(body_bytes)
    if declare is not None:
        for decl in declare.group(1).split(b";"):
            tokens = decl.split()
            if tokens:
                names.add(tokens[0].decode("utf-8", "replace").strip('"'))
    for m in _FOR_VAR_RE.finditer(body_bytes):
        names.add(m.group(1).decode("utf-8", "replace"))
    return {n for n in names if _IDENT_RE.fullmatch(n)}


def _signature_param_names(src: bytes, start: int) -> set[str]:
    """Parameter names from a function signature — the identifier of each top-level
    argument between the outermost parentheses (skipping ``IN``/``OUT``/``INOUT``/
    ``VARIADIC`` direction keywords)."""
    open_paren = src.find(b"(", start)
    if open_paren == -1:
        return set()
    close_paren = _match_paren(src, open_paren)
    if close_paren == -1:
        return set()
    names: set[str] = set()
    for arg in _split_top_level(src[open_paren + 1 : close_paren]):
        tokens = arg.split()
        if tokens and tokens[0].upper() in (b"IN", b"OUT", b"INOUT", b"VARIADIC"):
            tokens = tokens[1:]
        if tokens:
            names.add(tokens[0].decode("utf-8", "replace").strip('"'))
    return names


def _match_paren(src: bytes, open_idx: int) -> int:
    """Index of the ``)`` matching the ``(`` at ``open_idx``, or ``-1``."""
    depth = 0
    for j in range(open_idx, len(src)):
        ch = src[j]
        if ch == 0x28:  # (
            depth += 1
        elif ch == 0x29:  # )
            depth -= 1
            if depth == 0:
                return j
    return -1


def _split_top_level(chunk: bytes) -> list[bytes]:
    """Split ``chunk`` on commas that are not nested inside parentheses (so a
    ``numeric(10, 2)`` type in one argument is not split into two)."""
    out: list[bytes] = []
    depth = 0
    cur = bytearray()
    for ch in chunk:
        if ch == 0x28:
            depth += 1
            cur.append(ch)
        elif ch == 0x29:
            depth -= 1
            cur.append(ch)
        elif ch == 0x2C and depth == 0:  # comma
            out.append(bytes(cur))
            cur = bytearray()
        else:
            cur.append(ch)
    if cur:
        out.append(bytes(cur))
    return out


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


def _collect_accesses(
    body: Node,
    reads: set[str],
    writes: set[str],
    bound: int | None = None,
    masked: bytes | None = None,
    mask_offset: int = 0,
) -> None:
    """Classify each table ``object_reference`` in a function body as read/write.

    A table reference is an ``object_reference`` under a ``relation`` (SELECT/JOIN/
    UPDATE target / UPDATE...FROM source) or directly under ``insert`` (INTO target)
    or ``from`` (a DELETE target — SELECT/UPDATE FROM wrap the table in a ``relation``,
    DELETE does not). Anything else (column refs like ``u.id``, function invocations)
    is ignored. ``bound`` (byte offset of the next CREATE FUNCTION) caps the walk so
    error-recovery spillover never attributes another function's tables to this one.
    When ``masked`` (a copy of the body content with comments and string literals blanked
    to spaces, positioned at ``mask_offset`` in the source) is given, an
    ``object_reference`` starting at a blanked byte is skipped — a table named inside a
    ``RAISE``/``EXECUTE`` message string that tree-sitter error-recovered into live SQL
    must not become an edge.

    Parent and grandparent node types are threaded DOWN the walk rather than read via
    ``Node.parent`` — that accessor is expensive in tree-sitter, and reading it per node
    made this O(n·depth) (an ingestion hang on large/deep function bodies).
    """
    stack: list[tuple[Node, str | None, str | None]] = [(body, None, None)]
    while stack:
        n, parent_type, grandparent_type = stack.pop()
        if bound is not None and n.start_byte >= bound:
            continue
        if (
            n.type == "object_reference"
            and parent_type is not None
            and not (
                masked is not None
                and 0 <= n.start_byte - mask_offset < len(masked)
                and masked[n.start_byte - mask_offset] == 0x20
            )
        ):
            if parent_type == "relation":
                tbl = _table_name(n)
                if tbl:
                    if grandparent_type == "update":
                        writes.add(tbl)
                    else:
                        reads.add(tbl)
                continue  # don't descend into schema/name identifiers
            if parent_type == "insert":
                tbl = _table_name(n)
                if tbl:
                    writes.add(tbl)
                continue
            if parent_type == "from":
                # A bare object_reference under `from` (no `relation` wrapper) is a
                # DELETE target; SELECT/UPDATE FROM sources always wrap in `relation`.
                tbl = _table_name(n)
                if tbl:
                    writes.add(tbl)
                continue
        for child in n.named_children:
            stack.append((child, n.type, parent_type))
