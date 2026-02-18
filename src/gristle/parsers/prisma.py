"""Prisma schema parser — extracts model and enum definitions from .prisma files."""

from __future__ import annotations

import logging
import re

from gristle.models import ParsedModel, ParsedModelField, ParsedModelRelation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Regex patterns
# ---------------------------------------------------------------------------

_MODEL_START_RE = re.compile(r"^(model|enum)\s+(\w+)\s*\{", re.MULTILINE)

_FIELD_RE = re.compile(
    r"^\s+(\w+)\s+"  # field name
    r"(\w+(?:\[\])?(?:\?)?)"  # type (with optional [] or ?)
    r"(.*?)$",  # attributes (rest of line)
    re.MULTILINE,
)

# Order-independent @relation parsing: extract full block, then search within
_RELATION_BLOCK_RE = re.compile(r"@relation\(([^)]*)\)", re.DOTALL)
_RELATION_FIELDS_RE = re.compile(r"fields:\s*\[([^\]]+)\]")
_RELATION_REFS_RE = re.compile(r"references:\s*\[([^\]]+)\]")
_RELATION_NAME_RE = re.compile(r'name:\s*"([^"]*)"')

_MAP_RE = re.compile(r'@@map\("([^"]+)"\)')
_FIELD_MAP_RE = re.compile(r'@map\("([^"]+)"\)')
_DEFAULT_RE = re.compile(r"@default\(([^)]+)\)")
_DB_TYPE_RE = re.compile(r"@db\.(\w+(?:\([^)]*\))?)")
_INDEX_RE = re.compile(r"@@index\(\[([^\]]+)\]\)")
_UNIQUE_RE = re.compile(r"@@unique\(\[([^\]]+)\]\)")
_COMPOSITE_ID_RE = re.compile(r"@@id\(\[([^\]]+)\]\)")
_DOCSTRING_RE = re.compile(r"///\s*(.*)")
_IGNORE_RE = re.compile(r"@ignore\b")
_MODEL_IGNORE_RE = re.compile(r"@@ignore\b")

# ---------------------------------------------------------------------------
# Type mapping: Prisma scalar → normalised type
# ---------------------------------------------------------------------------

_PRISMA_TYPE_MAP: dict[str, str] = {
    "String": "string",
    "Int": "number",
    "Float": "number",
    "Decimal": "number",
    "BigInt": "number",
    "Boolean": "boolean",
    "DateTime": "Date",
    "Json": "object",
    "Bytes": "bytes",
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def parse_prisma_schema(file_path: str, content: str) -> list[ParsedModel]:
    """Parse a .prisma file and return model definitions."""
    models: list[ParsedModel] = []
    # Build set of all model names for relation detection
    model_names = {m.group(2) for m in _MODEL_START_RE.finditer(content) if m.group(1) == "model"}

    for match in _MODEL_START_RE.finditer(content):
        block_type = match.group(1)  # "model" or "enum"
        name = match.group(2)
        try:
            body = _extract_block_body(content, match.end() - 1)
            line_start = content[: match.start()].count("\n") + 1
            line_end = content[: match.start() + len(match.group()) + len(body)].count("\n") + 1

            docstring = _extract_docstring(content, match.start())

            if block_type == "enum":
                model = _parse_enum(name, file_path, body, line_start, line_end, docstring)
            else:
                if _MODEL_IGNORE_RE.search(body):
                    continue
                model = _parse_model(
                    name,
                    file_path,
                    body,
                    line_start,
                    line_end,
                    docstring,
                    model_names,
                )

            models.append(model)
        except Exception:
            logger.warning(
                "Prisma parser: failed to parse %s '%s' in %s",
                block_type,
                name,
                file_path,
            )

    return models


# ---------------------------------------------------------------------------
# Block / docstring extraction
# ---------------------------------------------------------------------------


def _extract_block_body(content: str, brace_pos: int) -> str:
    """Extract the body between braces using brace-counting."""
    depth = 0
    for i in range(brace_pos, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[brace_pos + 1 : i]
    return content[brace_pos + 1 :]


def _extract_docstring(content: str, block_start: int) -> str | None:
    """Collect consecutive /// comment lines immediately above a block."""
    lines = content[:block_start].rstrip().splitlines()
    doc_lines: list[str] = []
    for line in reversed(lines):
        m = _DOCSTRING_RE.match(line.strip())
        if m:
            doc_lines.append(m.group(1))
        else:
            break
    if not doc_lines:
        return None
    doc_lines.reverse()
    return "\n".join(doc_lines)


# ---------------------------------------------------------------------------
# Enum parsing
# ---------------------------------------------------------------------------


def _parse_enum(
    name: str,
    file_path: str,
    body: str,
    line_start: int,
    line_end: int,
    docstring: str | None,
) -> ParsedModel:
    fields: list[ParsedModelField] = []
    for line in body.splitlines():
        token = line.strip()
        if not token or token.startswith("//"):
            continue
        member = token.split()[0]
        if member.isidentifier():
            fields.append(
                ParsedModelField(name=member, field_type="enum_member", is_nullable=False),
            )
    return ParsedModel(
        name=name,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        orm="prisma",
        is_enum=True,
        docstring=docstring,
        fields=fields,
    )


# ---------------------------------------------------------------------------
# Model parsing
# ---------------------------------------------------------------------------


def _parse_model(
    name: str,
    file_path: str,
    body: str,
    line_start: int,
    line_end: int,
    docstring: str | None,
    model_names: set[str],
) -> ParsedModel:
    fields: list[ParsedModelField] = []
    relations: list[ParsedModelRelation] = []
    # Map field name → ParsedModelField for FK back-patching
    field_map: dict[str, ParsedModelField] = {}
    primary_key: str | None = None

    # Collect parsed field lines for two-pass processing.
    # Pass 1: scalar fields.  Pass 2: relation fields (need field_map populated).
    parsed_lines: list[tuple[str, str, str, bool, bool]] = []  # (name, base_type, attrs, nullable, array)

    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("@@") or stripped.startswith("//"):
            continue
        if _IGNORE_RE.search(stripped):
            continue

        m = _FIELD_RE.match(line)
        if not m:
            continue

        f_name = m.group(1)
        raw_type = m.group(2)
        attrs = m.group(3)

        base_type = raw_type.rstrip("?").rstrip("[]").rstrip("?")
        is_nullable = raw_type.endswith("?") or raw_type.endswith("[]?")
        is_array = "[]" in raw_type

        parsed_lines.append((f_name, base_type, attrs, is_nullable, is_array))

    # Pass 1: scalar fields
    for f_name, base_type, attrs, is_nullable, _is_array in parsed_lines:
        if base_type not in _PRISMA_TYPE_MAP:
            continue
        pf = ParsedModelField(
            name=f_name,
            field_type=_PRISMA_TYPE_MAP[base_type],
            is_nullable=is_nullable,
        )
        if "@id" in attrs:
            pf.is_primary_key = True
            primary_key = f_name
        if "@unique" in attrs:
            pf.is_unique = True
        dm = _DEFAULT_RE.search(attrs)
        if dm:
            pf.has_default = True
            pf.default_value = dm.group(1)
        dbm = _DB_TYPE_RE.search(attrs)
        if dbm:
            pf.db_type = dbm.group(1).lower()

        fields.append(pf)
        field_map[f_name] = pf

    # Pass 2: relation fields (virtual — not stored as columns)
    for f_name, base_type, attrs, _is_nullable, is_array in parsed_lines:
        if base_type in _PRISMA_TYPE_MAP:
            continue
        if is_array:
            relations.append(
                ParsedModelRelation(
                    target_model=base_type,
                    relation_type="one-to-many",
                    source_field=f_name,
                    orm_hint="prisma_relation",
                ),
            )
        else:
            rel_block = _RELATION_BLOCK_RE.search(attrs)
            fk_field: str | None = None
            ref_field: str | None = None
            if rel_block:
                inner = rel_block.group(1)
                fm = _RELATION_FIELDS_RE.search(inner)
                rm = _RELATION_REFS_RE.search(inner)
                if fm:
                    fk_field = fm.group(1).strip()
                if rm:
                    ref_field = rm.group(1).strip()

            rel_type = "many-to-one"
            if fk_field and fk_field in field_map:
                field_map[fk_field].is_foreign_key = True
                field_map[fk_field].references_model = base_type
                field_map[fk_field].references_field = ref_field

            relations.append(
                ParsedModelRelation(
                    target_model=base_type,
                    relation_type=rel_type,
                    foreign_key_field=fk_field,
                    source_field=f_name,
                    orm_hint="prisma_relation",
                ),
            )

    # --- Second pass: model-level attributes ---
    mm = _MAP_RE.search(body)
    table_name = mm.group(1) if mm else None

    for idx_match in _INDEX_RE.finditer(body):
        for col in idx_match.group(1).split(","):
            col = col.strip()
            if col in field_map:
                field_map[col].is_indexed = True

    for uq_match in _UNIQUE_RE.finditer(body):
        for col in uq_match.group(1).split(","):
            col = col.strip()
            if col in field_map:
                field_map[col].is_unique = True

    cid = _COMPOSITE_ID_RE.search(body)
    if cid:
        parts = [c.strip() for c in cid.group(1).split(",")]
        for col in parts:
            if col in field_map:
                field_map[col].is_primary_key = True
        primary_key = ",".join(parts)

    # Upgrade many-to-one → one-to-one when FK has @unique
    for rel in relations:
        if rel.relation_type == "many-to-one" and rel.foreign_key_field:
            fk = field_map.get(rel.foreign_key_field)
            if fk and fk.is_unique:
                rel.relation_type = "one-to-one"

    return ParsedModel(
        name=name,
        qualified_name=f"{file_path}::{name}",
        file_path=file_path,
        line_start=line_start,
        line_end=line_end,
        orm="prisma",
        table_name=table_name,
        primary_key=primary_key,
        docstring=docstring,
        fields=fields,
        relations=relations,
    )
