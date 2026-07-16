"""Supabase generated-types parser — extracts table/view models from
``supabase gen types typescript`` output.

The generated file declares one big ``Database`` type whose shape is
``Database.<schema>.Tables.<table>.{Row, Insert, Update, Relationships}``.
``Row`` gives the columns (with ``| null`` marking nullable ones) and
``Relationships`` gives the foreign keys, so a single generated file yields
the full table schema — no ORM required. Views appear under ``Views`` with a
``Row`` but no ``Relationships``.

Reuses the TypeScript parser's tree-sitter setup (same pattern as
``orm_typescript``).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from gristle.models import ParsedModel, ParsedModelField, ParsedModelRelation
from gristle.parsers.typescript import TypeScriptParser

if TYPE_CHECKING:
    from tree_sitter import Node

_parser = TypeScriptParser()

# `Database["public"]["Enums"]["app_role"]` in a column type → just `app_role`.
_ENUM_REF_RE = re.compile(r'Database\["[^"]+"\]\["Enums"\]\["([^"]+)"\]')
# Generated columns are `T | null` with null always the trailing branch.
_NULLABLE_RE = re.compile(r"\s*\|\s*null\s*$")

# Sections of a schema we turn into models. Views are queryable via
# `.from('view')` just like tables, so they get Model nodes too.
_MODEL_SECTIONS = (("Tables", False), ("Views", True))


def is_supabase_types(content: str) -> bool:
    """Cheap sniff for ``supabase gen types typescript`` output.

    False positives are harmless: :func:`parse_supabase_types` returns ``[]``
    unless the full ``Database → schema → Tables → table → Row`` structure is
    actually present.
    """
    return "Database" in content and "Tables:" in content and "Row:" in content


def parse_supabase_types(file_path: str, content: str) -> list[ParsedModel]:
    """Return table/view models from a Supabase generated-types file (empty if none)."""
    src = content.encode("utf-8")
    root = _parser._ts_parser.parse(src).root_node
    database = _find_database_type(root, src)
    if database is None:
        return []

    models: list[ParsedModel] = []
    seen_tables: set[str] = set()
    for schema_name, schema_node in _prop_entries(database, src):
        if schema_name == "__InternalSupabase":
            continue
        sections = dict(_prop_entries(schema_node, src))
        for section_name, is_view in _MODEL_SECTIONS:
            section = sections.get(section_name)
            if section is None:
                continue
            for table_name, table_node in _prop_entries(section, src):
                if table_name in seen_tables:
                    continue  # same table exposed by two schemas: first wins
                parts = dict(_prop_entries(table_node, src))
                row = parts.get("Row")
                if row is None:
                    continue
                seen_tables.add(table_name)
                fields = _row_fields(row, src)
                relations = _relationships(parts.get("Relationships"), src, fields)
                models.append(
                    ParsedModel(
                        name=table_name,
                        qualified_name=f"{file_path}::{schema_name}.{table_name}",
                        file_path=file_path,
                        line_start=table_node.start_point[0] + 1,
                        line_end=table_node.end_point[0] + 1,
                        orm="supabase",
                        table_name=table_name,
                        docstring="Supabase view" if is_view else None,
                        fields=fields,
                        relations=relations,
                    )
                )
    return models


def _find_database_type(root: Node, src: bytes) -> Node | None:
    """Locate the ``Database`` declaration body (type alias or interface)."""
    for child in root.named_children:
        decl = child
        if child.type == "export_statement" and child.named_child_count:
            decl = child.named_children[0]
        if decl.type == "type_alias_declaration":
            body = decl.child_by_field_name("value")
        elif decl.type == "interface_declaration":
            body = decl.child_by_field_name("body")
        else:
            continue
        name = decl.child_by_field_name("name")
        if name is not None and _parser._text(name, src) == "Database" and body is not None:
            return body
    return None


def _prop_entries(node: Node, src: bytes) -> list[tuple[str, Node]]:
    """Yield ``(name, type_node)`` for each property signature of an object/interface body."""
    entries: list[tuple[str, Node]] = []
    for child in node.named_children:
        if child.type != "property_signature":
            continue
        name_node = child.child_by_field_name("name")
        annotation = child.child_by_field_name("type")
        if name_node is None or annotation is None or not annotation.named_child_count:
            continue
        name = (_parser._text(name_node, src) or "").strip("'\"")
        if name:
            entries.append((name, annotation.named_children[0]))
    return entries


def _row_fields(row_node: Node, src: bytes) -> list[ParsedModelField]:
    fields: list[ParsedModelField] = []
    for name, type_node in _prop_entries(row_node, src):
        raw = (_parser._text(type_node, src) or "").strip()
        is_nullable = bool(_NULLABLE_RE.search(raw))
        base = _NULLABLE_RE.sub("", raw).strip()
        base = _ENUM_REF_RE.sub(r"\1", base)
        fields.append(
            ParsedModelField(
                name=name,
                field_type=base,
                is_nullable=is_nullable,
                line=type_node.start_point[0] + 1,
            )
        )
    return fields


def _relationships(rel_node: Node | None, src: bytes, fields: list[ParsedModelField]) -> list[ParsedModelRelation]:
    """Turn the ``Relationships`` tuple into relations, marking FK fields in place."""
    if rel_node is None:
        return []
    field_by_name = {f.name: f for f in fields}
    relations: list[ParsedModelRelation] = []
    for entry in rel_node.named_children:
        if entry.type != "object_type":
            continue
        props = dict(_prop_entries(entry, src))
        fk_columns = _literal_strings(props.get("columns"), src)
        target = _first_literal_string(props.get("referencedRelation"), src)
        referenced = _literal_strings(props.get("referencedColumns"), src)
        if not fk_columns or not target:
            continue
        is_one_to_one = props.get("isOneToOne") is not None and _parser._text(props["isOneToOne"], src) == "true"
        fk_field = fk_columns[0]
        matched = field_by_name.get(fk_field)
        if matched is not None:
            matched.is_foreign_key = True
            matched.references_model = target
            matched.references_field = referenced[0] if referenced else None
        relations.append(
            ParsedModelRelation(
                target_model=target,
                relation_type="one-to-one" if is_one_to_one else "many-to-one",
                foreign_key_field=fk_field,
                orm_hint="supabase_fk",
            )
        )
    return relations


def _literal_strings(node: Node | None, src: bytes) -> list[str]:
    """String values of a tuple of literal types: ``["user_id"]`` → ``["user_id"]``."""
    if node is None or node.type != "tuple_type":
        return []
    values: list[str] = []
    for child in node.named_children:
        value = _literal_string_value(child, src)
        if value:
            values.append(value)
    return values


def _first_literal_string(node: Node | None, src: bytes) -> str | None:
    """String value of a single literal type: ``"users"`` → ``users``."""
    if node is None:
        return None
    return _literal_string_value(node, src)


def _literal_string_value(node: Node, src: bytes) -> str | None:
    if node.type != "literal_type" or not node.named_child_count:
        return None
    inner = node.named_children[0]
    if inner.type != "string":
        return None
    fragments = [c for c in inner.children if c.type == "string_fragment"]
    if len(fragments) != 1:
        return None
    return _parser._text(fragments[0], src)
