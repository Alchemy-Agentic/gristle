"""Tests for the file system walker."""

from pathlib import Path

from gristle.ingestion.walker import walk_repo


class TestWalker:
    def test_finds_python_files(self, sample_python_dir: Path):
        files = walk_repo(sample_python_dir, frozenset({"py"}))
        paths = {f.relative_path for f in files}
        assert "models.py" in paths
        assert "services.py" in paths
        assert "utils.py" in paths

    def test_filters_by_extension(self, sample_python_dir: Path):
        files = walk_repo(sample_python_dir, frozenset({"txt"}))
        assert len(files) == 0

    def test_all_files_have_extension(self, sample_python_dir: Path):
        files = walk_repo(sample_python_dir, frozenset({"py"}))
        for f in files:
            assert f.extension == "py"

    def test_uses_forward_slashes(self, sample_python_dir: Path):
        files = walk_repo(sample_python_dir, frozenset({"py"}))
        for f in files:
            assert "\\" not in f.relative_path
