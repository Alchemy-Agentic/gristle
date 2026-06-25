"""Schema extraction phase — creates Model/ModelField nodes from ORM schemas."""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING

from gristle.config import settings
from gristle.ingestion.batch import BatchCollector
from gristle.models import ParsedModel, SchemaExtractionResult

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient
    from gristle.ingestion.walker import WalkedFile
    from gristle.models import ParsedClass, ParsedFile, ParsedFunction

# Verbs in a call chain that indicate writing vs reading a model/table.
_WRITE_VERBS = frozenset(
    {"create", "insert", "save", "add", "update", "delete", "remove", "bulk_create", "upsert", "put", "write"}
)
_READ_VERBS = frozenset(
    {"query", "select", "get", "find", "filter", "all", "first", "one", "fetch", "read", "exists", "count", "list"}
)
_IDENT_SPLIT = re.compile(r"[^A-Za-z0-9_]+")
# Split a camelCase/PascalCase identifier into words (findOne -> find, one).
_CAMEL_SPLIT = re.compile(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+")

logger = logging.getLogger(__name__)


class SchemaExtractor:
    """Post-Phase 2 processor that creates Model/ModelField/ModelRelation nodes."""

    def __init__(self, graph: GraphClient, file_path_to_id: dict[str, str]) -> None:
        self.graph = graph
        self._file_path_to_id = file_path_to_id

    def extract(
        self, walked_files: list[WalkedFile], parsed_files: list[ParsedFile] | None = None
    ) -> SchemaExtractionResult:
        """Run all schema detection strategies and write to graph.

        ``parsed_files`` (optional) lets the extractor link code to the data
        layer via USES_MODEL edges (Function -> Model).
        """
        models: list[ParsedModel] = []

        # 1. Prisma DSL parsing
        for wf in walked_files:
            if wf.extension == "prisma":
                content = self._read_file(wf)
                if content is not None:
                    from gristle.parsers.prisma import parse_prisma_schema

                    models.extend(parse_prisma_schema(wf.relative_path, content))

        # 2. Drizzle extraction (check .ts/.js files for drizzle-orm imports)
        for wf in walked_files:
            if wf.extension in ("ts", "js", "mts", "mjs"):
                content = self._read_file(wf)
                if content is not None:
                    from gristle.parsers.drizzle import (
                        is_drizzle_schema,
                        parse_drizzle_schema,
                    )

                    if is_drizzle_schema(content):
                        models.extend(parse_drizzle_schema(wf.relative_path, content))

                    from gristle.parsers.orm_typescript import extract_typeorm_models

                    models.extend(extract_typeorm_models(wf.relative_path, content))

        # 3. Python ORM detection (SQLAlchemy declarative + Django models).
        # Collected across all files so model base classes resolve transitively
        # (a model subclassing a custom base defined in another file).
        py_files: list[tuple[str, str]] = []
        for wf in walked_files:
            if wf.extension in ("py", "pyi"):
                content = self._read_file(wf)
                if content is not None:
                    py_files.append((wf.relative_path, content))
        if py_files:
            from gristle.parsers.orm_python import extract_python_orm_models_from_files

            models.extend(extract_python_orm_models_from_files(py_files))

        # 4. Write to graph
        return self._write_models(models, parsed_files)

    @staticmethod
    def _read_file(wf: WalkedFile) -> str | None:
        """Read file content, returning None on error."""
        try:
            return Path(wf.absolute_path).read_text(encoding="utf-8", errors="replace")
        except OSError:
            logger.warning("Schema extractor: cannot read %s", wf.relative_path)
            return None

    @staticmethod
    def _infer_table_name(name: str) -> str:
        """Simple table name inference from model name."""
        return name.lower() + "s"

    def _write_models(
        self, models: list[ParsedModel], parsed_files: list[ParsedFile] | None = None
    ) -> SchemaExtractionResult:
        batch = BatchCollector(self.graph, settings.ingestion_batch_size)
        model_name_to_id: dict[str, str] = {}

        # Phase A: Create Model nodes
        for m in models:
            model_id = f"model::{m.file_path}::{m.name}"
            model_name_to_id[m.name] = model_id
            batch.add_node(
                "Model",
                {
                    "id": model_id,
                    "name": m.name,
                    "qualified_name": m.qualified_name,
                    "file_path": m.file_path,
                    "line_start": m.line_start,
                    "line_end": m.line_end,
                    "orm": m.orm,
                    "table_name": m.table_name or self._infer_table_name(m.name),
                    "is_junction": m.is_junction,
                    "is_enum": m.is_enum,
                    "primary_key": m.primary_key,
                    "field_count": len(m.fields),
                    "docstring": m.docstring,
                },
            )

            # File containment edge
            file_id = self._file_path_to_id.get(m.file_path)
            if not file_id:
                # Prisma files won't have File nodes from Phase 1 (no parser registered).
                # Create a minimal File node so CONTAINS edges have a source.
                file_id = f"file::{m.file_path}"
                batch.add_node(
                    "File",
                    {
                        "id": file_id,
                        "path": m.file_path,
                        "language": "prisma",
                        "line_count": 0,
                    },
                )
                self._file_path_to_id[m.file_path] = file_id
            batch.add_relationship("CONTAINS", file_id, model_id)

            # Link back to Class node (for ORM class promoter)
            if m.source_class_qualified_name:
                class_id = f"class::{m.source_class_qualified_name}"
                batch.add_relationship("PROMOTED_FROM", model_id, class_id)

        # Phase B: Create ModelField nodes + REFERENCES edges
        for m in models:
            model_id = model_name_to_id.get(m.name, "")
            if not model_id:
                continue
            for f in m.fields:
                field_id = f"mf::{model_id}::{f.name}"
                batch.add_node(
                    "ModelField",
                    {
                        "id": field_id,
                        "name": f.name,
                        "field_type": f.field_type,
                        "db_type": f.db_type,
                        "is_primary_key": f.is_primary_key,
                        "is_nullable": f.is_nullable,
                        "is_unique": f.is_unique,
                        "is_indexed": f.is_indexed,
                        "has_default": f.has_default,
                        "default_value": f.default_value,
                        "is_foreign_key": f.is_foreign_key,
                        "references_model": f.references_model,
                        "references_field": f.references_field,
                        "line": f.line,
                    },
                )
                batch.add_relationship("HAS_MODEL_FIELD", model_id, field_id)

                # FK reference edge
                if f.is_foreign_key and f.references_model:
                    target_id = model_name_to_id.get(f.references_model)
                    if target_id:
                        batch.add_relationship("REFERENCES", field_id, target_id)

        # Phase C: Create RELATED_TO edges between models
        for m in models:
            model_id = model_name_to_id.get(m.name, "")
            if not model_id:
                continue
            for r in m.relations:
                target_id = model_name_to_id.get(r.target_model)
                if target_id and target_id != model_id:
                    batch.add_merge_relationship(
                        "RELATED_TO",
                        model_id,
                        target_id,
                        {
                            # FalkorDB cannot MERGE on null property values, so
                            # coerce optional relation fields to empty strings.
                            "relation_type": r.relation_type or "",
                            "foreign_key_field": r.foreign_key_field or "",
                            "through_model": r.through_model or "",
                            "source_field": r.source_field or "",
                            "orm_hint": r.orm_hint or "",
                        },
                    )

        # Phase D: Link code to the data layer (Function -> Model)
        if parsed_files and model_name_to_id:
            self._link_code_to_models(parsed_files, model_name_to_id, batch)

        counts = batch.flush()
        return SchemaExtractionResult(
            models_found=len(models),
            fields_found=sum(len(m.fields) for m in models),
            relations_found=sum(len(m.relations) for m in models),
            nodes_created=counts["nodes_created"],
            relationships_created=counts["relationships_created"],
        )

    def _link_code_to_models(
        self,
        parsed_files: list[ParsedFile],
        model_name_to_id: dict[str, str],
        batch: BatchCollector,
    ) -> None:
        """Create USES_MODEL edges (Function -> Model) from a function's call chains.

        Precise by design: an edge is created only when a call references BOTH a
        known model name AND a read/write verb. Two call shapes are recognised:

        - Method chains where the model is in the chain — ``User.objects.filter()``,
          ``Article.objects.create()``, ``user.save()``, ``prisma.user.create()``
          (from ``func.calls``).
        - Calls passing the model/table as an argument — Drizzle ``db.insert(chat)``,
          SQLAlchemy ``session.query(User)``, and Drizzle reads
          ``db.select().from(chat)`` (the parser emits ``"select.from(chat)"`` for
          these) — all from ``func.calls_with_args``.

        Also catches the TypeORM/NestJS repository pattern: a class field typed
        ``Repository<ArticleEntity>`` (usually a constructor param-property) is
        mapped to the model, so ``this.articleRepository.findOne()`` links the
        method to ``ArticleEntity`` (here the model name comes from the field's
        *type*, not the call).

        The verb must appear in the *method-name* portion of the call, never in an
        argument — so ``can(create, Document)`` (an action constant + a model) does
        not create a spurious edge. Requiring the verb avoids false positives from
        incidental name reuse (a UI component referencing a ``Document`` *type*).
        The ``access`` property is "read" or "write".
        """
        token_to_model = {name.lower(): mid for name, mid in model_name_to_id.items()}

        # Best access per (func_id, model_id): write outranks read.
        edges: dict[tuple[str, str], str] = {}

        for pf in parsed_files:
            for func in pf.functions:
                self._collect_model_edges(func, token_to_model, {}, edges)
            for cls in pf.classes:
                field_to_model = self._repo_field_models(cls, token_to_model)
                for method in cls.methods:
                    self._collect_model_edges(method, token_to_model, field_to_model, edges)

        for (func_id, model_id), access in edges.items():
            batch.add_merge_relationship("USES_MODEL", func_id, model_id, {"access": access})

    @staticmethod
    def _record_edge(edges: dict[tuple[str, str], str], func_id: str, model_id: str, access: str) -> None:
        key = (func_id, model_id)
        if access == "write" or key not in edges:  # write outranks read
            edges[key] = access

    def _collect_model_edges(
        self,
        func: ParsedFunction,
        token_to_model: dict[str, str],
        field_to_model: dict[str, str],
        edges: dict[tuple[str, str], str],
    ) -> None:
        func_id = f"func::{func.qualified_name}"
        for call in (*func.calls, *func.calls_with_args):
            match = self._match_call(call, token_to_model)
            if match is not None:
                model_id, access = match
                self._record_edge(edges, func_id, model_id, access)

        # Repository/entity field access: ``<field>.<verb>()`` (``this.`` stripped
        # by the parser) where the field's declared type names a known model.
        if field_to_model:
            for call in func.calls:
                parts = call.split(".")
                if len(parts) < 2:
                    continue
                field_model = field_to_model.get(parts[-2])
                if field_model is None:
                    continue
                field_access = self._method_access(parts[-1])
                if field_access:
                    self._record_edge(edges, func_id, field_model, field_access)

    @staticmethod
    def _repo_field_models(cls: ParsedClass, token_to_model: dict[str, str]) -> dict[str, str]:
        """Map a class's fields to the model their declared type references.

        Covers the TypeORM/NestJS repository pattern (``Repository<ArticleEntity>``
        constructor param-properties) and entity-typed fields. Only fields whose
        type names exactly one known model are mapped (stay precise).
        """
        typed: list[tuple[str, str | None]] = []
        for m in cls.methods:
            if m.name in ("constructor", "__init__"):
                typed.extend(m.typed_parameters)
        typed.extend((f.name, f.type_annotation) for f in cls.fields)

        field_to_model: dict[str, str] = {}
        for fname, ftype in typed:
            if not ftype:
                continue
            ids = {token_to_model[t.lower()] for t in _IDENT_SPLIT.split(ftype) if t.lower() in token_to_model}
            if len(ids) == 1:
                field_to_model[fname] = next(iter(ids))
        return field_to_model

    @staticmethod
    def _method_access(method: str) -> str | None:
        """Classify a method name as read/write, splitting camelCase so TypeORM
        names resolve (``findOne`` → read, ``softDelete`` → write)."""
        words = [w.lower() for w in _CAMEL_SPLIT.findall(method)]
        if any(w in _WRITE_VERBS for w in words):
            return "write"
        if any(w in _READ_VERBS for w in words):
            return "read"
        return None

    @staticmethod
    def _match_call(call: str, token_to_model: dict[str, str]) -> tuple[str, str] | None:
        """Match one call against the model table, returning ``(model_id, access)``.

        ``call`` is either a method chain (``"prisma.user.create"``) or a descriptor
        with arguments (``"db.insert(chat)"``). The verb is read only from the
        method-name part (before ``(``); the model may be in either part.
        """
        head, sep, rest = call.partition("(")
        verb_segments = [s.lower() for s in _IDENT_SPLIT.split(head) if s]
        arg_segments = [s.lower() for s in _IDENT_SPLIT.split(rest.rstrip(")")) if s] if sep else []

        matched = next((token_to_model[s] for s in (*verb_segments, *arg_segments) if s in token_to_model), None)
        if matched is None:
            return None
        if any(s in _WRITE_VERBS for s in verb_segments):
            return matched, "write"
        if any(s in _READ_VERBS for s in verb_segments):
            return matched, "read"
        return None  # name match without a read/write verb — skip (avoid noise)
