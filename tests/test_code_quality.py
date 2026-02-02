"""Tests for code quality analysis — dead exports, cycles, public API."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.query.engine import QueryEngine

# ======================================================================
# Dead Export Detection
# ======================================================================


class TestDeadExportDetection:
    def _make_engine(
        self,
        exports: list[tuple[str, str, str, str]],  # (file, entity_qn, entity_name, type)
        imports: list[tuple[str, str]],  # (importer_file, imported_file)
        entry_points: set[str] | None = None,
    ):
        """Create a QueryEngine with mock EXPORTS and IMPORTS edges."""
        mock_graph = MagicMock()
        mock_graph.repo_id = "test"

        # Build result records
        result_records = []
        entry_points = entry_points or set()

        for file_path, qn, name, entity_type in exports:
            # Check if this export is imported
            is_imported = any(
                imported == file_path and importer != file_path
                for importer, imported in imports
            )
            is_entry = qn in entry_points

            # Only include if not imported and not entry point
            if not is_imported and not is_entry:
                result_records.append({
                    "qualified_name": qn,
                    "name": name,
                    "file": file_path,
                    "type": entity_type,
                })

        result = MagicMock()
        result.records = result_records
        mock_graph.execute.return_value = result
        return QueryEngine(mock_graph)

    def test_exported_but_never_imported(self):
        """Functions exported but never imported should be flagged."""
        engine = self._make_engine(
            exports=[
                ("src/utils.ts", "src/utils.ts::unused", "unused", "Function"),
            ],
            imports=[],
        )
        result = engine.detect_dead_exports()
        assert result["total"] == 1
        assert result["dead_exports"][0]["name"] == "unused"

    def test_exported_and_imported_not_flagged(self):
        """Exported entities that are imported should not be flagged."""
        engine = self._make_engine(
            exports=[
                ("src/utils.ts", "src/utils.ts::used", "used", "Function"),
            ],
            imports=[
                ("src/app.ts", "src/utils.ts"),
            ],
        )
        result = engine.detect_dead_exports()
        assert result["total"] == 0

    def test_entry_point_not_flagged(self):
        """Entry points should not be flagged even if not imported."""
        engine = self._make_engine(
            exports=[
                ("src/main.ts", "src/main.ts::main", "main", "Function"),
            ],
            imports=[],
            entry_points={"src/main.ts::main"},
        )
        result = engine.detect_dead_exports()
        assert result["total"] == 0

    def test_multiple_dead_exports(self):
        """Multiple dead exports should all be reported."""
        engine = self._make_engine(
            exports=[
                ("src/utils.ts", "src/utils.ts::dead1", "dead1", "Function"),
                ("src/utils.ts", "src/utils.ts::dead2", "dead2", "Function"),
                ("src/helpers.ts", "src/helpers.ts::deadClass", "deadClass", "Class"),
            ],
            imports=[],
        )
        result = engine.detect_dead_exports()
        assert result["total"] == 3
        names = {e["name"] for e in result["dead_exports"]}
        assert names == {"dead1", "dead2", "deadClass"}

    def test_mixed_used_and_unused(self):
        """Should only flag the unused ones."""
        engine = self._make_engine(
            exports=[
                ("src/utils.ts", "src/utils.ts::used", "used", "Function"),
                ("src/utils.ts", "src/utils.ts::unused", "unused", "Function"),
            ],
            imports=[
                ("src/app.ts", "src/utils.ts"),  # Imports utils.ts (has 'used')
            ],
        )
        # Note: The current implementation checks file-level imports, not entity-level
        # So if ANY file imports utils.ts, none of its exports are flagged
        # This is a limitation — we'd need entity-level import tracking for perfect accuracy
        result = engine.detect_dead_exports()
        # Both are considered "used" because utils.ts is imported
        assert result["total"] == 0


# ======================================================================
# Import Cycle Detection
# ======================================================================


class TestImportCycleDetection:
    def _make_engine(self, import_pairs: list[tuple[str, str]]):
        """Create a QueryEngine with mock cyclic IMPORTS edges."""
        mock_graph = MagicMock()
        mock_graph.repo_id = "test"

        # Build cycle paths manually for testing
        # In real graph, Cypher does this traversal
        result_records = []

        # Simple 2-file cycle: a->b->a
        if ("a.ts", "b.ts") in import_pairs and ("b.ts", "a.ts") in import_pairs:
            result_records.append({
                "cycle_files": ["a.ts", "b.ts", "a.ts"],
                "cycle_length": 2,
            })

        # 3-file cycle: a->b->c->a
        if (("a.ts", "b.ts") in import_pairs and
            ("b.ts", "c.ts") in import_pairs and
            ("c.ts", "a.ts") in import_pairs):
            result_records.append({
                "cycle_files": ["a.ts", "b.ts", "c.ts", "a.ts"],
                "cycle_length": 3,
            })

        result = MagicMock()
        result.records = result_records
        mock_graph.execute.return_value = result
        return QueryEngine(mock_graph)

    def test_simple_two_file_cycle(self):
        """a->b->a should be detected."""
        engine = self._make_engine([
            ("a.ts", "b.ts"),
            ("b.ts", "a.ts"),
        ])
        result = engine.detect_import_cycles()
        assert result["total"] == 1
        assert result["cycles"][0]["length"] == 2
        assert "a.ts" in result["cycles"][0]["files"]
        assert "b.ts" in result["cycles"][0]["files"]

    def test_three_file_cycle(self):
        """a->b->c->a should be detected."""
        engine = self._make_engine([
            ("a.ts", "b.ts"),
            ("b.ts", "c.ts"),
            ("c.ts", "a.ts"),
        ])
        result = engine.detect_import_cycles()
        assert result["total"] >= 1
        # Find the length-3 cycle
        cycle_3 = [c for c in result["cycles"] if c["length"] == 3]
        assert len(cycle_3) >= 1

    def test_no_cycles(self):
        """Linear imports should not produce cycles."""
        engine = self._make_engine([])
        result = engine.detect_import_cycles()
        assert result["total"] == 0
        assert result["cycles"] == []

    def test_by_length_grouping(self):
        """Cycles should be grouped by length."""
        engine = self._make_engine([
            ("a.ts", "b.ts"),
            ("b.ts", "a.ts"),
        ])
        result = engine.detect_import_cycles()
        assert 2 in result["by_length"]
        assert result["by_length"][2] == 1


# ======================================================================
# Public API Surface Mapping
# ======================================================================


class TestPublicApiMapping:
    def _make_engine(
        self,
        exports: list[tuple[str, str, str, str, bool, bool]],
        # (file, qn, name, type, has_docs, is_test_file)
    ):
        """Create a QueryEngine with mock public API exports."""
        mock_graph = MagicMock()
        mock_graph.repo_id = "test"

        result_records = []
        for file_path, qn, name, entity_type, has_docs, is_test_file in exports:
            # Filter based on rules
            if is_test_file:
                continue
            if "__" in file_path or "/internal/" in file_path or "/_" in file_path:
                continue  # Excluded by default

            result_records.append({
                "qualified_name": qn,
                "name": name,
                "file": file_path,
                "type": entity_type,
                "docstring": "docs here" if has_docs else None,
            })

        result = MagicMock()
        result.records = result_records
        mock_graph.execute.return_value = result
        return QueryEngine(mock_graph)

    def test_basic_public_api(self):
        """Public exports from regular files should be returned."""
        engine = self._make_engine([
            ("src/api.ts", "src/api.ts::getUser", "getUser", "Function", True, False),
            ("src/models.ts", "src/models.ts::User", "User", "Class", True, False),
        ])
        result = engine.get_public_api()
        assert result["total"] == 2
        assert result["by_type"]["Function"] == 1
        assert result["by_type"]["Class"] == 1

    def test_excludes_test_files(self):
        """Entities in test files should be excluded."""
        engine = self._make_engine([
            ("src/api.ts", "src/api.ts::getUser", "getUser", "Function", True, False),
            ("src/api.test.ts", "src/api.test.ts::testHelper", "testHelper", "Function", False, True),
        ])
        result = engine.get_public_api()
        assert result["total"] == 1
        assert result["entities"][0]["name"] == "getUser"

    def test_excludes_internal_paths(self):
        """Entities in internal paths should be excluded."""
        engine = self._make_engine([
            ("src/api.ts", "src/api.ts::getUser", "getUser", "Function", True, False),
            ("src/internal/helper.ts", "src/internal/helper.ts::internalFn", "internalFn", "Function", False, False),
            ("src/__private/util.ts", "src/__private/util.ts::privateFn", "privateFn", "Function", False, False),
        ])
        result = engine.get_public_api()
        # internal/helper.ts and __private/util.ts should be filtered
        assert result["total"] == 1
        assert result["entities"][0]["name"] == "getUser"

    def test_documentation_percentage(self):
        """Should calculate documentation percentage correctly."""
        engine = self._make_engine([
            ("src/api.ts", "src/api.ts::documented", "documented", "Function", True, False),
            ("src/api.ts", "src/api.ts::undocumented", "undocumented", "Function", False, False),
        ])
        result = engine.get_public_api()
        assert result["total"] == 2
        assert result["documented_count"] == 1
        assert result["doc_percentage"] == 50

    def test_by_file_grouping(self):
        """Should group counts by file."""
        engine = self._make_engine([
            ("src/api.ts", "src/api.ts::fn1", "fn1", "Function", True, False),
            ("src/api.ts", "src/api.ts::fn2", "fn2", "Function", True, False),
            ("src/models.ts", "src/models.ts::User", "User", "Class", True, False),
        ])
        result = engine.get_public_api()
        assert result["by_file"]["src/api.ts"] == 2
        assert result["by_file"]["src/models.ts"] == 1

    def test_empty_api(self):
        """Empty result should not crash."""
        engine = self._make_engine([])
        result = engine.get_public_api()
        assert result["total"] == 0
        assert result["doc_percentage"] == 0
