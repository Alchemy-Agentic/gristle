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

# Cheap pre-filter: only parse files that plausibly contain ORM models or their
# base classes. "models." catches Django field usage (models.CharField, ...) in
# files that subclass a custom base and never mention models.Model literally.
_ORM_HINTS = (
    "__tablename__",
    "DeclarativeBase",
    "declarative_base",
    "mapped_column",
    "Column(",
    "models.",
)

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
    """Return SQLAlchemy/Django models found in a single Python file."""
    return extract_python_orm_models_from_files([(file_path, content)])


def extract_python_orm_models_from_files(files: list[tuple[str, str]]) -> list[ParsedModel]:
    """Return SQLAlchemy/Django models across many files, resolving model base
    classes transitively.

    A model is rarely a *direct* subclass of ``models.Model`` / a SQLAlchemy
    ``Base`` — projects define a shared base (``class TimestampedModel(models.Model)``)
    in one file and subclass it elsewhere. A single-file pass misses those, so
    this collects every class first, seeds the ORM kind from the base-name
    heuristic, then propagates it to subclasses of any known model base until a
    fixpoint — across file boundaries.
    """
    # Pass 1: collect every class definition (name, node, bases) across all files.
    entries: list[tuple[str, str, Node, bytes, list[str]]] = []
    for file_path, content in files:
        if not any(hint in content for hint in _ORM_HINTS):
            continue
        src = content.encode("utf-8")
        root = _parser.parse(src).root_node
        for node in _iter_class_definitions(root):
            name_node = node.child_by_field_name("name")
            if name_node is None:
                continue
            entries.append((file_path, _text(name_node, src), node, src, _bases(node, src)))

    # Classify ORM kind per class name: seed from the base-name heuristic, then
    # propagate transitively to subclasses of any class already classified.
    orm_of: dict[str, str] = {}
    for _, name, _, _, bases in entries:
        kind = _detect_orm(bases)
        if kind:
            orm_of[name] = kind
    changed = True
    while changed:
        changed = False
        for _, name, _, _, bases in entries:
            if name in orm_of:
                continue
            for b in bases:
                base_kind = orm_of.get(b) or orm_of.get(b.rsplit(".", 1)[-1])
                if base_kind:
                    orm_of[name] = base_kind
                    changed = True
                    break

    # Pass 2: parse fields for every class classified as a model.
    models: list[ParsedModel] = []
    for file_path, name, node, src, _ in entries:
        orm = orm_of.get(name)
        if orm is None:
            continue
        model = _parse_class_model(node, src, file_path, orm)
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


def _is_abstract_django(body: Node, src: bytes) -> bool:
    """True if the class has a nested ``class Meta`` with ``abstract = True``.

    Abstract Django bases (e.g. a shared ``TimestampedModel``) define no table,
    so they should not become Model nodes — only their concrete subclasses do.
    """
    for stmt in body.named_children:
        if stmt.type != "class_definition":
            continue
        meta_name = stmt.child_by_field_name("name")
        meta_body = stmt.child_by_field_name("body")
        if (
            meta_name is not None
            and meta_body is not None
            and _text(meta_name, src) == "Meta"
            and re.search(r"\babstract\s*=\s*True\b", _text(meta_body, src))
        ):
            return True
    return False


def _parse_class_model(class_node: Node, src: bytes, file_path: str, orm: str) -> ParsedModel | None:
    name_node = class_node.child_by_field_name("name")
    body = class_node.child_by_field_name("body")
    if name_node is None or body is None:
        return None
    if orm == "django" and _is_abstract_django(body, src):
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
