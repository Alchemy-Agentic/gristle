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

    def test_utf16_source_file_is_not_skipped_as_binary(self, tmp_path: Path):
        # UTF-16 text is full of NUL bytes but is source, not binary (e.g. a Supabase
        # types file generated via a PowerShell redirect). It must be walked, not
        # skipped — otherwise its models/functions are silently missing.
        (tmp_path / "types_db.ts").write_bytes(b"\xff\xfe" + "export const x = 1\n".encode("utf-16-le"))
        (tmp_path / "plain.ts").write_text("export const y = 2\n", encoding="utf-8")
        paths = {f.relative_path for f in walk_repo(tmp_path, frozenset({"ts"}))}
        assert "types_db.ts" in paths
        assert "plain.ts" in paths

    def test_true_binary_still_skipped(self, tmp_path: Path):
        # A real binary (NUL bytes, no text BOM) is still excluded.
        (tmp_path / "blob.ts").write_bytes(b"\x00\x01\x02\x03\x00\xff")
        paths = {f.relative_path for f in walk_repo(tmp_path, frozenset({"ts"}))}
        assert "blob.ts" not in paths

    def test_utf8_bom_binary_still_skipped(self, tmp_path: Path):
        # A UTF-8 BOM does not license NUL bytes — a UTF-8-BOM file with NULs is binary
        # and must still be skipped (only UTF-16/UTF-32 BOMs bypass the NUL check).
        (tmp_path / "blob.ts").write_bytes(b"\xef\xbb\xbf\x00\x01\x00\x02")
        paths = {f.relative_path for f in walk_repo(tmp_path, frozenset({"ts"}))}
        assert "blob.ts" not in paths
