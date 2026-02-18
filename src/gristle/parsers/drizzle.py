"""Drizzle ORM schema parser — extracts table definitions from TypeScript files."""

from __future__ import annotations

import logging
import re

from gristle.models import ParsedModel, ParsedModelField, ParsedModelRelation

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------

_DRIZZLE_IMPORT_RE = re.compile(r"from\s+['\"]drizzle-orm/(?:pg|mysql|sqlite)-core['\"]")

# ---------------------------------------------------------------------------
# Table definition
# ---------------------------------------------------------------------------

_TABLE_DEF_RE = re.compile(
    r"(?:export\s+)?(?:const|let)\s+(\w+)\s*=\s*"
    r"(pgTable|mysqlTable|sqliteTable)\(\s*['\"](\w+)['\"]"
)

# ---------------------------------------------------------------------------
# Column parsing
# ---------------------------------------------------------------------------

_COLUMN_RE = re.compile(r"(\w+)\s*:\s*(\w+)\(")

# Chained method detectors
_PRIMARY_KEY_RE = re.compile(r"\.primaryKey\(\)")
_NOT_NULL_RE = re.compile(r"\.notNull\(\)")
_UNIQUE_RE = re.compile(r"\.unique\(\)")
_DEFAULT_RE = re.compile(r"\.default\(([^)]*)\)")
_DEFAULT_RANDOM_RE = re.compile(r"\.defaultRandom\(\)")
_DEFAULT_NOW_RE = re.compile(r"\.defaultNow\(\)")
_REFERENCES_RE = re.compile(r"\.references\(\s*\(\)\s*=>\s*(\w+)\.(\w+)\s*\)")

# Index block
_INDEX_ON_RE = re.compile(r"\.on\(table\.(\w+)\)")

# DB column name inside the type function call: typeFunc('col_name' ...)
_DB_COL_NAME_RE = re.compile(r"\(\s*['\"](\w+)['\"]")

# ---------------------------------------------------------------------------
# Type mapping: drizzle type function → (application_type, db_type)
# ---------------------------------------------------------------------------

_DRIZZLE_TYPE_MAP: dict[str, tuple[str, str]] = {
    "uuid": ("string", "uuid"),
    "varchar": ("string", "varchar"),
    "text": ("string", "text"),
    "char": ("string", "char"),
    "integer": ("number", "integer"),
    "serial": ("number", "serial"),
    "bigint": ("number", "bigint"),
    "smallint": ("number", "smallint"),
    "boolean": ("boolean", "boolean"),
    "timestamp": ("Date", "timestamp"),
    "date": ("Date", "date"),
    "time": ("string", "time"),
    "json": ("object", "json"),
    "jsonb": ("object", "jsonb"),
    "real": ("number", "real"),
    "doublePrecision": ("number", "double precision"),
    "numeric": ("number", "numeric"),
    "decimal": ("number", "decimal"),
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_block_body(content: str, start: int) -> tuple[str, int]:
    """Extract text between the first ``{`` at/after *start* and its matching ``}``.

    Returns the body (excluding outer braces) and the index just past the
    closing brace.
    """
    idx = content.index("{", start)
    depth = 0
    for i in range(idx, len(content)):
        if content[i] == "{":
            depth += 1
        elif content[i] == "}":
            depth -= 1
            if depth == 0:
                return content[idx + 1 : i], i + 1
    # Unclosed brace — return what we have
    return content[idx + 1 :], len(content)


def _parse_column_line(
    line: str,
    var_to_table: dict[str, str],
    line_offset: int,
) -> tuple[str, ParsedModelField] | None:
    """Parse a single column definition line.

    Returns ``(js_field_name, field)`` so the caller can map JS property names
    (used in index blocks) back to the parsed field.
    """
    col_match = _COLUMN_RE.search(line)
    if not col_match:
        return None

    field_name = col_match.group(1)
    type_func = col_match.group(2)

    # Resolve application / db types
    app_type, db_type = _DRIZZLE_TYPE_MAP.get(type_func, ("unknown", type_func))

    # Try to extract the explicit DB column name from the type call
    db_col_match = _DB_COL_NAME_RE.search(line[col_match.start(2) :])
    db_col_name = db_col_match.group(1) if db_col_match else field_name

    # Defaults: Drizzle columns are nullable unless .notNull() is chained
    is_primary_key = bool(_PRIMARY_KEY_RE.search(line))
    is_nullable = not bool(_NOT_NULL_RE.search(line))
    is_unique = bool(_UNIQUE_RE.search(line))

    has_default = False
    default_value: str | None = None
    if _DEFAULT_RANDOM_RE.search(line):
        has_default, default_value = True, "random()"
    elif _DEFAULT_NOW_RE.search(line):
        has_default, default_value = True, "now()"
    else:
        default_match = _DEFAULT_RE.search(line)
        if default_match:
            has_default, default_value = True, default_match.group(1).strip()

    # FK references
    is_foreign_key = False
    references_model: str | None = None
    references_field: str | None = None
    ref_match = _REFERENCES_RE.search(line)
    if ref_match:
        is_foreign_key = True
        var_name = ref_match.group(1)
        references_field = ref_match.group(2)
        references_model = var_to_table.get(var_name, var_name)

    return field_name, ParsedModelField(
        name=db_col_name,
        field_type=app_type,
        db_type=db_type,
        is_primary_key=is_primary_key,
        is_nullable=is_nullable,
        is_unique=is_unique,
        is_indexed=False,  # Set later from index block
        has_default=has_default,
        default_value=default_value,
        is_foreign_key=is_foreign_key,
        references_model=references_model,
        references_field=references_field,
        line=line_offset,
    )


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def is_drizzle_schema(content: str) -> bool:
    """Check if file content imports from drizzle-orm and defines tables."""
    return bool(_DRIZZLE_IMPORT_RE.search(content))


def parse_drizzle_schema(file_path: str, content: str) -> list[ParsedModel]:
    """Parse Drizzle pgTable/mysqlTable/sqliteTable definitions."""
    models: list[ParsedModel] = []

    # First pass: build var_name → table_name map
    var_to_table: dict[str, str] = {}
    for match in _TABLE_DEF_RE.finditer(content):
        var_to_table[match.group(1)] = match.group(3)

    # Second pass: parse each table
    for match in _TABLE_DEF_RE.finditer(content):
        table_name = match.group(3)
        try:
            match_start = match.start()
            line_start = content.count("\n", 0, match_start) + 1

            # Extract column block (first { ... } after the table name)
            col_body, col_end = _extract_block_body(content, match.end())
            col_body_start_line = content.count("\n", 0, match.end()) + 1

            # Parse columns — track JS property name → field for index matching
            fields: list[ParsedModelField] = []
            js_name_to_field: dict[str, ParsedModelField] = {}
            primary_key: str | None = None
            for i, line in enumerate(col_body.splitlines()):
                result = _parse_column_line(line, var_to_table, col_body_start_line + i)
                if result is not None:
                    js_name, field = result
                    fields.append(field)
                    js_name_to_field[js_name] = field
                    if field.is_primary_key:
                        primary_key = field.name

            # Check for index block (optional third argument)
            remaining = content[col_end:].lstrip()
            if remaining.startswith(","):
                try:
                    idx_body, idx_end = _extract_block_body(content, col_end)
                    for js_name in _INDEX_ON_RE.findall(idx_body):
                        if js_name in js_name_to_field:
                            js_name_to_field[js_name].is_indexed = True
                    line_end = content.count("\n", 0, col_end + len(idx_body)) + 1
                except ValueError:
                    line_end = content.count("\n", 0, col_end) + 1
            else:
                line_end = content.count("\n", 0, col_end) + 1

            # Build relations from FK fields
            relations: list[ParsedModelRelation] = []
            for field in fields:
                if field.is_foreign_key and field.references_model:
                    relations.append(
                        ParsedModelRelation(
                            target_model=field.references_model,
                            relation_type="many-to-one",
                            foreign_key_field=field.name,
                            orm_hint="drizzle_reference",
                        )
                    )

            models.append(
                ParsedModel(
                    name=table_name,
                    qualified_name=f"{file_path}::{table_name}",
                    file_path=file_path,
                    line_start=line_start,
                    line_end=line_end,
                    orm="drizzle",
                    table_name=table_name,
                    primary_key=primary_key,
                    fields=fields,
                    relations=relations,
                )
            )
        except Exception:
            logger.warning(
                "Drizzle parser: failed to parse table '%s' in %s",
                table_name,
                file_path,
            )

    return models
