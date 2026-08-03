"""Feature-flag extraction phase — creates Flag nodes and GATES edges.

Runs after SchemaExtractor (Function nodes must already exist so GATES can point at
them). Three inputs merge into one Flag node per key:

* **registry** definitions (``parse_flag_registry``) — key + default + doc.
* **DB** definitions (``parse_flag_table_migrations``) — INSERT seeds, DELETE
  retires; netted across migrations.
* **check sites** — the ``flag('KEY')`` descriptors the TS parser left in each
  function's ``calls_with_args``; each becomes a ``Flag-[:GATES]->Function`` edge.

A key that is checked but defined by neither surface is an *orphan* (still gets a
Flag node, flagged). The precision boundary is the configured check-function
allowlist and flag-table names (``settings.flag_*``) — nothing is guessed.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from gristle.config import settings
from gristle.ingestion.batch import BatchCollector
from gristle.ingestion.textio import read_text_file
from gristle.models import FlagExtractionResult, ParsedFlag

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient
    from gristle.ingestion.walker import WalkedFile
    from gristle.models import ParsedFile, ParsedFunction

logger = logging.getLogger(__name__)

# `flag('KEY')` descriptor emitted by the TS parser at each check site.
_FLAG_DESCRIPTOR_RE = re.compile(r"^flag\('([^']+)'\)$")
_TS_EXTS = ("ts", "tsx", "js", "jsx", "mts", "mjs", "cts", "cjs")
# Reversal scripts (a `rollbacks/` dir, `*_rollback.sql`, `*.down.sql`, …) invert a
# migration, so their DELETE/INSERT is the OPPOSITE of the forward state. Excluding
# them keeps a live flag from being read as retired (and vice versa). The reliable
# signal is a reversal DIRECTORY or an unambiguous suffix — NOT a filename substring,
# because forward migrations legitimately carry verbs like "revert" in the FEATURE
# name (e.g. `..._refinement_revert.sql` adds a revert-to-revision feature).
_ROLLBACK_SEGMENTS = frozenset({"rollback", "rollbacks", "revert", "reverts", "undo", "undos", "down", "downs"})
_ROLLBACK_SUFFIXES = ("_rollback.sql", ".rollback.sql", ".down.sql", "_undo.sql")


def _is_reversal_sql(relative_path: str) -> bool:
    parts = relative_path.lower().replace("\\", "/").split("/")
    if any(seg in _ROLLBACK_SEGMENTS for seg in parts):
        return True
    return parts[-1].endswith(_ROLLBACK_SUFFIXES)


class FlagExtractor:
    """Post-SchemaExtractor processor that creates Flag nodes + GATES edges."""

    def __init__(self, graph: GraphClient, file_path_to_id: dict[str, str]) -> None:
        self.graph = graph
        self._file_path_to_id = file_path_to_id

    def extract(
        self, walked_files: list[WalkedFile], parsed_files: list[ParsedFile] | None = None
    ) -> FlagExtractionResult:
        if not settings.flag_detection_enabled:
            return FlagExtractionResult()

        from gristle.parsers.feature_flags import parse_flag_registry, parse_flag_table_migrations

        registry_defs: list[ParsedFlag] = []
        db_defs: list[ParsedFlag] = []
        for wf in walked_files:
            if wf.extension in _TS_EXTS and settings.flag_registry_symbols:
                content = self._read_file(wf)
                if content is not None:
                    registry_defs.extend(parse_flag_registry(wf.relative_path, content, settings.flag_registry_symbols))
            elif wf.extension == "sql" and settings.flag_tables and not _is_reversal_sql(wf.relative_path):
                content = self._read_file(wf)
                if content is not None:
                    db_defs.extend(parse_flag_table_migrations(wf.relative_path, content, settings.flag_tables))

        gates = self._collect_check_sites(parsed_files or [])
        return self._write_flags(registry_defs, db_defs, gates)

    def _collect_check_sites(self, parsed_files: list[ParsedFile]) -> dict[str, set[str]]:
        """Map flag key -> set of func_ids that check it, from ``flag('KEY')``
        descriptors in each function's (and method's) ``calls_with_args``."""
        gates: dict[str, set[str]] = {}
        for pf in parsed_files:
            for func in pf.functions:
                self._collect_func_gates(func, gates)
            for cls in pf.classes:
                for method in cls.methods:
                    self._collect_func_gates(method, gates)
        return gates

    @staticmethod
    def _collect_func_gates(func: ParsedFunction, gates: dict[str, set[str]]) -> None:
        func_id = f"func::{func.qualified_name}"
        for descriptor in func.calls_with_args:
            m = _FLAG_DESCRIPTOR_RE.match(descriptor)
            if m:
                gates.setdefault(m.group(1), set()).add(func_id)

    def _write_flags(
        self,
        registry_defs: list[ParsedFlag],
        db_defs: list[ParsedFlag],
        gates: dict[str, set[str]],
    ) -> FlagExtractionResult:
        # Registry: one entry per key (first wins — a repo has a single registry).
        registry: dict[str, ParsedFlag] = {}
        for d in registry_defs:
            registry.setdefault(d.key, d)

        # DB net: INSERT adds, DELETE retires; retire wins the current-state flag.
        db_inserted: dict[str, ParsedFlag] = {}
        db_retired: set[str] = set()
        for d in db_defs:
            if d.retired:
                db_retired.add(d.key)
            else:
                db_inserted.setdefault(d.key, d)

        all_keys = set(registry) | set(db_inserted) | db_retired | set(gates)

        batch = BatchCollector(self.graph, settings.ingestion_batch_size)
        orphan_count = 0
        gates_created = 0
        for key in sorted(all_keys):
            reg = registry.get(key)
            ins = db_inserted.get(key)
            in_registry = reg is not None
            in_db = key in db_inserted and key not in db_retired
            retired = key in db_retired
            defined = in_registry or in_db
            func_ids = gates.get(key, set())
            orphan = bool(func_ids) and not defined and not retired
            if orphan:
                orphan_count += 1

            flag_id = f"flag::{key}"
            batch.add_node(
                "Flag",
                {
                    "id": flag_id,
                    "key": key,
                    "in_registry": in_registry,
                    "in_db": in_db,
                    "retired": retired,
                    "orphan": orphan,
                    "registry_default": reg.default if reg else None,
                    "db_seeded_enabled": ins.default if ins else None,
                    "gates_count": len(func_ids),
                    "description": reg.description if reg else None,
                    "line": reg.line if reg else None,
                },
            )

            # Home the flag in its defining file (registry first, else first migration).
            home = (reg.file_path if reg else None) or (ins.file_path if ins else None)
            if home:
                file_id = self._file_path_to_id.get(home)
                if file_id:
                    batch.add_relationship("CONTAINS", file_id, flag_id)

            for func_id in func_ids:
                batch.add_merge_relationship("GATES", flag_id, func_id, {})
                gates_created += 1

        counts = batch.flush()
        return FlagExtractionResult(
            flags_found=len(all_keys),
            gates_created=gates_created,
            orphan_checks=orphan_count,
            nodes_created=counts["nodes_created"],
            relationships_created=counts["relationships_created"],
        )

    @staticmethod
    def _read_file(wf: WalkedFile) -> str | None:
        try:
            return read_text_file(wf.absolute_path)
        except OSError:
            logger.warning("Flag extractor: cannot read %s", wf.relative_path)
            return None
