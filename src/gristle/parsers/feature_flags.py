"""Feature-flag DEFINITION parsers.

Two definition surfaces, mirroring how flags are declared in a Supabase/homegrown
app:

* **Client registry** — a ``const featureFlags = { KEY: true, ... } as const``
  object of boolean defaults with doc comments. Parsed with tree-sitter (TS is
  never regex-parsed) via the shared TypeScript setup.
* **DB migrations** — ``INSERT INTO feature_flags (id, enabled, …) VALUES ('KEY',
  true, …)`` seeds a flag; ``DELETE FROM feature_flags WHERE id = ANY(ARRAY['KEY'])``
  retires it. Parsed with targeted regex over ``.sql`` (SQL, not code — consistent
  with the SQL parser's own regex fallbacks). The row shape ``('KEY', <bool>,`` is
  matched directly so descriptions (which contain semicolons and stray tokens)
  never produce a false row.

Check SITES are handled separately by the TS parser's ``flag('KEY')`` call
descriptor; this module only covers DEFINITIONS. The FlagExtractor merges both.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from gristle.models import ParsedFlag
from gristle.parsers.typescript import TypeScriptParser

if TYPE_CHECKING:
    from tree_sitter import Node

_parser = TypeScriptParser()

# A feature_flags row: `('KEY', true/false, …)`. The boolean second element is the
# `enabled` column — descriptions (the 3rd element) can hold semicolons and prose,
# so keying off the `'KEY', <bool>,` shape avoids parsing them as rows.
_INSERT_ROW_RE = re.compile(r"\(\s*'([A-Z][A-Z0-9_]{2,})'\s*,\s*(true|false)\b", re.IGNORECASE)
# Any SCREAMING_SNAKE quoted token inside a DELETE / cleanup array.
_QUOTED_KEY_RE = re.compile(r"'([A-Z][A-Z0-9_]{2,})'")


def parse_flag_registry(file_path: str, content: str, symbols: frozenset[str] | set[str]) -> list[ParsedFlag]:
    """Return flag definitions from a ``const <symbol> = { KEY: bool } as const``
    registry object (empty if the file has no configured registry symbol)."""
    if not any(sym in content for sym in symbols):
        return []
    src = content.encode("utf-8")
    root = _parser._ts_parser.parse(src).root_node
    obj = _find_registry_object(root, src, symbols)
    if obj is None:
        return []

    flags: list[ParsedFlag] = []
    pending_comments: list[str] = []  # own-line comments leading the NEXT pair
    last_idx: int | None = None  # index of the most recent flag (for trailing comments)
    last_row = -1
    for child in obj.children:
        if child.type == "comment":
            text = _clean_comment(_parser._text(child, src) or "")
            # A comment on the same line as the previous pair trails THAT key
            # (e.g. `KEY: false, // DB-controlled`), not the next one.
            if last_idx is not None and child.start_point[0] == last_row:
                if text:
                    prior = flags[last_idx].description
                    flags[last_idx].description = f"{prior} {text}".strip() if prior else text
            elif text:
                pending_comments.append(text)
            continue
        if child.type != "pair":
            continue
        key = _property_name(child.child_by_field_name("key"), src)
        default = _bool_value(child.child_by_field_name("value"), src)
        if key and default is not None:
            doc = " ".join(pending_comments).strip() or None
            flags.append(
                ParsedFlag(
                    key=key,
                    source="registry",
                    default=default,
                    description=doc,
                    file_path=file_path,
                    line=child.start_point[0] + 1,
                )
            )
            last_idx = len(flags) - 1
            last_row = child.end_point[0]
        pending_comments = []
    return flags


def parse_flag_table_migrations(file_path: str, content: str, tables: frozenset[str] | set[str]) -> list[ParsedFlag]:
    """Return flag definitions from a ``.sql`` migration that seeds or retires rows in
    a configured flag table. INSERT -> a definition (with the seeded ``enabled``);
    DELETE -> the same key marked ``retired``. The FlagExtractor nets these across
    migrations (a key inserted then later deleted is retired)."""
    low = content.lower()
    if not any(t in low for t in tables):
        return []
    flags: list[ParsedFlag] = []

    inserts_here = re.search(r"insert\s+into\s+(?:public\.)?(?:" + "|".join(re.escape(t) for t in tables) + r")\b", low)
    if inserts_here:
        for m in _INSERT_ROW_RE.finditer(content):
            flags.append(
                ParsedFlag(
                    key=m.group(1),
                    source="db",
                    default=m.group(2).lower() == "true",
                    file_path=file_path,
                    line=content.count("\n", 0, m.start()) + 1,
                )
            )

    # DELETE FROM <table> ... (single id or ARRAY[...] of ids) — mark retired.
    for dm in re.finditer(r"delete\s+from\s+(?:public\.)?(?:" + "|".join(re.escape(t) for t in tables) + r")\b", low):
        segment = content[dm.start() : dm.start() + 1200]
        for km in _QUOTED_KEY_RE.finditer(segment):
            flags.append(ParsedFlag(key=km.group(1), source="db", retired=True, file_path=file_path))
    # Cleanup arrays: `flags_to_remove TEXT[] := ARRAY['A','B', …]`. Skip past the
    # `TEXT[]` type decl to the ARRAY literal (a plain `[^\[]*\[` would stop on the
    # empty brackets of `TEXT[]`).
    for am in re.finditer(r"flags?_to_remove\b[^;]*?ARRAY\s*\[([^\]]+)\]", content, re.IGNORECASE):
        for km in _QUOTED_KEY_RE.finditer(am.group(1)):
            flags.append(ParsedFlag(key=km.group(1), source="db", retired=True, file_path=file_path))
    return flags


# --------------------------------------------------------------------------- helpers


def _find_registry_object(root: Node, src: bytes, symbols: frozenset[str] | set[str]) -> Node | None:
    """Locate the object literal of ``const <symbol> = { … } (as const)?``."""
    stack = [root]
    while stack:
        node = stack.pop()
        if node.type == "variable_declarator":
            name = node.child_by_field_name("name")
            if name is not None and (_parser._text(name, src) or "") in symbols:
                return _unwrap_to_object(node.child_by_field_name("value"))
        stack.extend(node.children)
    return None


def _unwrap_to_object(node: Node | None) -> Node | None:
    """Peel ``as const`` / parentheses / satisfies wrappers to the object literal."""
    seen = 0
    while node is not None and seen < 6:
        seen += 1
        if node.type == "object":
            return node
        if node.type in ("as_expression", "satisfies_expression", "parenthesized_expression"):
            inner = node.child_by_field_name("object") or (node.named_children[0] if node.named_children else None)
            node = inner
            continue
        return None
    return None


def _property_name(node: Node | None, src: bytes) -> str | None:
    if node is None:
        return None
    text = _parser._text(node, src) or ""
    return text.strip("'\"") or None


def _bool_value(node: Node | None, src: bytes) -> bool | None:
    if node is None:
        return None
    if node.type == "true":
        return True
    if node.type == "false":
        return False
    return None


def _clean_comment(raw: str) -> str:
    """Strip ``//`` and ``/* */`` markers from a comment's text."""
    text = raw.strip()
    if text.startswith("//"):
        return text[2:].strip()
    if text.startswith("/*"):
        return text[2:].removesuffix("*/").strip().lstrip("*").strip()
    return text
