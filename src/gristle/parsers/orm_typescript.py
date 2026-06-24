"""Detect TypeORM entities from TypeScript source.

Reuses the TypeScript parser's tree-sitter setup and decorator extraction.
Class decorators (@Entity) are preceding siblings; field decorators (@Column,
@ManyToOne, ...) are children of the field definition.
"""

from __future__ import annotations

import re

from gristle.models import ParsedModel, ParsedModelField, ParsedModelRelation
from gristle.parsers.typescript import TypeScriptParser

_parser = TypeScriptParser()

_COLUMN_DECORATORS = {
    "Column",
    "PrimaryColumn",
    "PrimaryGeneratedColumn",
    "CreateDateColumn",
    "UpdateDateColumn",
    "DeleteDateColumn",
    "VersionColumn",
    "ObjectIdColumn",
}
_PK_DECORATORS = {"PrimaryColumn", "PrimaryGeneratedColumn", "ObjectIdColumn"}
_RELATION_DECORATORS = {
    "ManyToOne": "many-to-one",
    "OneToMany": "one-to-many",
    "OneToOne": "one-to-one",
    "ManyToMany": "many-to-many",
}
_ENTITY_DECORATORS = {"Entity", "ViewEntity"}

_REL_TARGET_RE = re.compile(r"=>\s*([A-Za-z_]\w*)")
_ENTITY_ARG_RE = re.compile(r"""\(\s*['"]([^'"]+)['"]""")


def extract_typeorm_models(file_path: str, content: str) -> list[ParsedModel]:
    """Return TypeORM @Entity models found in a TS file (empty if none)."""
    if "@Entity" not in content:
        return []
    src = content.encode("utf-8")
    root = _parser._ts_parser.parse(src).root_node
    models: list[ParsedModel] = []
    for node in _parser._iter_descendants(root):
        if node.type not in ("class_declaration", "abstract_class_declaration"):
            continue
        decorators = _parser._extract_decorators(node, src)
        entity_deco = next((d for d in decorators if d.split("(", 1)[0].strip() in _ENTITY_DECORATORS), None)
        if entity_deco is None:
            continue
        table_match = _ENTITY_ARG_RE.search(entity_deco)
        table_name = table_match.group(1) if table_match else None

        name_node = node.child_by_field_name("name")
        if name_node is None:
            continue
        fields, relations = _entity_members(node, src)
        models.append(
            ParsedModel(
                name=_parser._text(name_node, src),
                qualified_name=f"{file_path}::{_parser._text(name_node, src)}",
                file_path=file_path,
                line_start=node.start_point[0] + 1,
                line_end=node.end_point[0] + 1,
                orm="typeorm",
                table_name=table_name,
                fields=fields,
                relations=relations,
            )
        )
    return models


def _entity_members(class_node, src: bytes) -> tuple[list[ParsedModelField], list[ParsedModelRelation]]:
    fields: list[ParsedModelField] = []
    relations: list[ParsedModelRelation] = []
    body = class_node.child_by_field_name("body")
    if body is None:
        return fields, relations

    for member in body.children:
        if member.type != "public_field_definition":
            continue
        member_decos = [_parser._text(c, src).lstrip("@").strip() for c in member.children if c.type == "decorator"]
        if not member_decos:
            continue
        name_node = member.child_by_field_name("name")
        if name_node is None:
            continue
        fname = _parser._text(name_node, src)
        type_node = member.child_by_field_name("type")
        ftype = _parser._text(type_node, src).lstrip(": ").strip() if type_node else ""

        for deco in member_decos:
            dname = deco.split("(", 1)[0].strip()
            if dname in _RELATION_DECORATORS:
                target = _REL_TARGET_RE.search(deco)
                relations.append(
                    ParsedModelRelation(
                        target_model=target.group(1) if target else ftype,
                        relation_type=_RELATION_DECORATORS[dname],
                        source_field=fname,
                        orm_hint="typeorm",
                    )
                )
                break
            if dname in _COLUMN_DECORATORS:
                fields.append(
                    ParsedModelField(
                        name=fname,
                        field_type=ftype or "unknown",
                        is_primary_key=dname in _PK_DECORATORS,
                        is_unique="unique:true" in deco.replace(" ", ""),
                    )
                )
                break
    return fields, relations
