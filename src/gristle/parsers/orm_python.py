"""Detect SQLAlchemy and Django ORM models from Python source.

The general Python parser captures class structure but not class-body field
assignments (``email = Column(...)`` / ``name = models.CharField(...)``), which
is exactly where ORM schema lives. This focused pass re-parses Python files that
look like they contain models and emits ``ParsedModel`` records for the schema
phase, mirroring the Prisma/Drizzle extractors.
"""

from __future__ import annotations

import re

import tree_sitter_python as tspython
from tree_sitter import Language, Node, Parser

from gristle.models import ParsedModel, ParsedModelField, ParsedModelRelation

_parser = Parser(Language(tspython.language()))

# Cheap pre-filter: only parse files that plausibly contain ORM models.
_ORM_HINTS = ("__tablename__", "DeclarativeBase", "declarative_base", "mapped_column", "Column(", "models.Model")

_PK_RE = re.compile(r"primary_key\s*=\s*True")
_UNIQUE_RE = re.compile(r"unique\s*=\s*True")
_SA_NULLABLE_FALSE_RE = re.compile(r"nullable\s*=\s*False")
_DJ_NULL_FALSE_RE = re.compile(r"\bnull\s*=\s*False")
_SA_TYPE_RE = re.compile(r"(?:Column|mapped_column)\(\s*(\w+)")
_MAPPED_RE = re.compile(r"Mapped\[\s*([\w.]+)")
_FK_TARGET_RE = re.compile(r"""ForeignKey\(\s*["']?([A-Za-z_][\w.]*)""")
_SA_REL_TARGET_RE = re.compile(r"""relationship\(\s*["']?([A-Za-z_]\w*)""")
_DJ_FIELD_RE = re.compile(r"models\.(\w+)")
_DJ_REL_TARGET_RE = re.compile(r"""models\.\w+\(\s*["']?([A-Za-z_]\w*)""")

_DJ_RELATION_FIELDS = {
    "ForeignKey": "many-to-one",
    "OneToOneField": "one-to-one",
    "ManyToManyField": "many-to-many",
}


def extract_python_orm_models(file_path: str, content: str) -> list[ParsedModel]:
    """Return SQLAlchemy/Django models found in a Python file (empty if none)."""
    if not any(hint in content for hint in _ORM_HINTS):
        return []
    src = content.encode("utf-8")
    root = _parser.parse(src).root_node
    models: list[ParsedModel] = []
    for node in _iter_class_definitions(root):
        model = _parse_class_model(node, src, file_path)
        if model is not None:
            models.append(model)
    return models


def _iter_class_definitions(root: Node):
    stack = list(root.children)
    while stack:
        node = stack.pop()
        if node.type == "class_definition":
            yield node
        stack.extend(node.children)


def _text(node: Node, src: bytes) -> str:
    return src[node.start_byte : node.end_byte].decode("utf-8", "replace")


def _bases(class_node: Node, src: bytes) -> list[str]:
    args = class_node.child_by_field_name("superclasses")
    return [_text(c, src) for c in args.named_children] if args else []


def _detect_orm(bases: list[str]) -> str | None:
    if any(b == "models.Model" or b.endswith(".Model") for b in bases):
        return "django"
    if any(b in ("Base", "DeclarativeBase") or b.endswith("Base") for b in bases):
        return "sqlalchemy"
    return None


def _parse_class_model(class_node: Node, src: bytes, file_path: str) -> ParsedModel | None:
    name_node = class_node.child_by_field_name("name")
    body = class_node.child_by_field_name("body")
    if name_node is None or body is None:
        return None
    orm = _detect_orm(_bases(class_node, src))
    if orm is None:
        return None

    name = _text(name_node, src)
    table_name: str | None = None
    fields: list[ParsedModelField] = []
    relations: list[ParsedModelRelation] = []

    for stmt in body.named_children:
        assign = stmt.children[0] if stmt.type == "expression_statement" and stmt.children else None
        if assign is None or assign.type != "assignment":
            continue
        left = assign.child_by_field_name("left")
        right = assign.child_by_field_name("right")
        if left is None or left.type != "identifier" or right is None:
            continue
        fname = _text(left, src)
        rtext = _text(right, src)

        if fname == "__tablename__":
            table_name = _text(right, src).strip("'\"")
            continue

        if orm == "sqlalchemy":
            field, relation = _sqlalchemy_member(fname, assign, rtext, src)
        else:
            field, relation = _django_member(fname, rtext)
        if field is not None:
            fields.append(field)
        if relation is not None:
            relations.append(relation)

    # Django models always extend models.Model; SQLAlchemy needs a table or columns.
    if orm == "sqlalchemy" and not fields and table_name is None:
        return None

    return ParsedModel(
        name=name,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line_start=class_node.start_point[0] + 1,
        line_end=class_node.end_point[0] + 1,
        orm=orm,
        table_name=table_name,
        fields=fields,
        relations=relations,
    )


def _sqlalchemy_member(
    fname: str, assign: Node, rtext: str, src: bytes
) -> tuple[ParsedModelField | None, ParsedModelRelation | None]:
    if "relationship(" in rtext:
        target = _SA_REL_TARGET_RE.search(rtext)
        rel = ParsedModelRelation(
            target_model=target.group(1) if target else "",
            relation_type="one-to-many",
            source_field=fname,
            orm_hint="sqlalchemy",
        )
        return None, rel
    if "Column(" not in rtext and "mapped_column(" not in rtext:
        return None, None

    field_type = ""
    type_node = assign.child_by_field_name("type")
    if type_node is not None:
        mapped = _MAPPED_RE.search(_text(type_node, src))
        if mapped:
            field_type = mapped.group(1)
    if not field_type:
        m = _SA_TYPE_RE.search(rtext)
        field_type = m.group(1) if m else "unknown"

    fk = _FK_TARGET_RE.search(rtext)
    fk_target = fk.group(1).split(".") if fk else None
    return (
        ParsedModelField(
            name=fname,
            field_type=field_type,
            is_primary_key=bool(_PK_RE.search(rtext)),
            is_unique=bool(_UNIQUE_RE.search(rtext)),
            is_nullable=not bool(_SA_NULLABLE_FALSE_RE.search(rtext)),
            is_foreign_key=bool(fk),
            references_model=fk_target[0] if fk_target else None,
            references_field=fk_target[1] if fk_target and len(fk_target) > 1 else None,
        ),
        None,
    )


def _django_member(fname: str, rtext: str) -> tuple[ParsedModelField | None, ParsedModelRelation | None]:
    m = _DJ_FIELD_RE.search(rtext)
    if m is None:
        return None, None
    kind = m.group(1)
    target = _DJ_REL_TARGET_RE.search(rtext)
    target_model = target.group(1) if target else None
    is_fk = kind in _DJ_RELATION_FIELDS

    field = ParsedModelField(
        name=fname,
        field_type=kind,
        is_primary_key=bool(_PK_RE.search(rtext)),
        is_unique=bool(_UNIQUE_RE.search(rtext)),
        is_nullable=not bool(_DJ_NULL_FALSE_RE.search(rtext)),
        is_foreign_key=is_fk,
        references_model=target_model if is_fk else None,
    )
    relation = None
    if is_fk:
        relation = ParsedModelRelation(
            target_model=target_model or "",
            relation_type=_DJ_RELATION_FIELDS[kind],
            foreign_key_field=fname,
            source_field=fname,
            orm_hint="django",
        )
    return field, relation
