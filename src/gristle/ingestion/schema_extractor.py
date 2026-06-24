"""Schema extraction phase — creates Model/ModelField nodes from ORM schemas."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from gristle.config import settings
from gristle.ingestion.batch import BatchCollector
from gristle.models import ParsedModel, SchemaExtractionResult

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient
    from gristle.ingestion.walker import WalkedFile

logger = logging.getLogger(__name__)


class SchemaExtractor:
    """Post-Phase 2 processor that creates Model/ModelField/ModelRelation nodes."""

    def __init__(self, graph: GraphClient, file_path_to_id: dict[str, str]) -> None:
        self.graph = graph
        self._file_path_to_id = file_path_to_id

    def extract(self, walked_files: list[WalkedFile]) -> SchemaExtractionResult:
        """Run all schema detection strategies and write to graph."""
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

        # 3. Python ORM detection (SQLAlchemy declarative + Django models)
        for wf in walked_files:
            if wf.extension in ("py", "pyi"):
                content = self._read_file(wf)
                if content is not None:
                    from gristle.parsers.orm_python import extract_python_orm_models

                    models.extend(extract_python_orm_models(wf.relative_path, content))

        # 4. Write to graph
        return self._write_models(models)

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

    def _write_models(self, models: list[ParsedModel]) -> SchemaExtractionResult:
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

        counts = batch.flush()
        return SchemaExtractionResult(
            models_found=len(models),
            fields_found=sum(len(m.fields) for m in models),
            relations_found=sum(len(m.relations) for m in models),
            nodes_created=counts["nodes_created"],
            relationships_created=counts["relationships_created"],
        )
