"""Tests for BOM-aware source reading (UTF-16/UTF-8-SIG/UTF-8)."""

from pathlib import Path

from gristle.ingestion.textio import decode_text, is_wide_bom, read_text_file

SAMPLE = "export interface Database {\n  public: { Tables: { users: {} } }\n}\n"


class TestDecodeText:
    def test_plain_utf8(self):
        assert decode_text(SAMPLE.encode("utf-8")) == SAMPLE

    def test_utf16_le_with_bom_is_decoded_and_bom_stripped(self):
        raw = SAMPLE.encode("utf-16-le")
        raw = b"\xff\xfe" + raw  # LE BOM
        out = decode_text(raw)
        assert out == SAMPLE
        assert not out.startswith("﻿")  # BOM not left in the text

    def test_utf16_be_with_bom(self):
        raw = b"\xfe\xff" + SAMPLE.encode("utf-16-be")
        assert decode_text(raw) == SAMPLE

    def test_utf16_via_pythons_own_bom_writer(self):
        # `"...".encode("utf-16")` prepends a platform BOM — the common real shape.
        assert decode_text(SAMPLE.encode("utf-16")) == SAMPLE

    def test_utf8_sig_bom_is_stripped(self):
        raw = b"\xef\xbb\xbf" + SAMPLE.encode("utf-8")
        out = decode_text(raw)
        assert out == SAMPLE
        assert not out.startswith("﻿")

    def test_utf32_with_bom(self):
        # UTF-32LE's BOM (ff fe 00 00) starts with the UTF-16LE BOM — the 4-byte form
        # must be detected first, or it decodes as interleaved-NUL garbage.
        assert decode_text(SAMPLE.encode("utf-32")) == SAMPLE
        assert decode_text(b"\xff\xfe\x00\x00" + SAMPLE.encode("utf-32-le")) == SAMPLE

    def test_crlf_normalized_to_lf(self):
        # Matches Path.read_text's universal-newline behavior the old readers relied on,
        # so parser line/column offsets are unchanged for CRLF files.
        assert decode_text(b"a\r\nb\rc\n") == "a\nb\nc\n"
        assert decode_text(b"\xff\xfe" + "a\r\nb\n".encode("utf-16-le")) == "a\nb\n"

    def test_empty_and_malformed_do_not_raise(self):
        assert decode_text(b"") == ""
        # lone continuation bytes: replaced, never raised
        assert isinstance(decode_text(b"\xff\xfe\x00\xd8"), str)
        assert isinstance(decode_text(b"\x80\x81\x82"), str)


class TestIsWideBom:
    def test_utf16_and_utf32_boms_are_wide(self):
        assert is_wide_bom(b"\xff\xfe...")  # UTF-16LE
        assert is_wide_bom(b"\xfe\xff...")  # UTF-16BE
        assert is_wide_bom(b"\xff\xfe\x00\x00...")  # UTF-32LE
        assert is_wide_bom(b"\x00\x00\xfe\xff...")  # UTF-32BE

    def test_utf8_bom_is_not_wide(self):
        # A UTF-8 BOM does not license NUL bytes — a UTF-8-BOM file with NULs is binary.
        assert not is_wide_bom(b"\xef\xbb\xbf...")

    def test_plain_utf8_is_not_wide(self):
        assert not is_wide_bom(b"export const x = 1")

    def test_short_input(self):
        assert not is_wide_bom(b"")
        assert not is_wide_bom(b"\xff")


class TestReadTextFile:
    def test_reads_utf16_file(self, tmp_path: Path):
        # Mirrors `supabase gen types typescript > types_db.ts` under PowerShell.
        p = tmp_path / "types_db.ts"
        p.write_bytes(b"\xff\xfe" + SAMPLE.encode("utf-16-le"))
        assert read_text_file(p) == SAMPLE

    def test_reads_plain_utf8_file(self, tmp_path: Path):
        p = tmp_path / "plain.ts"
        p.write_text(SAMPLE, encoding="utf-8")
        assert read_text_file(p) == SAMPLE
