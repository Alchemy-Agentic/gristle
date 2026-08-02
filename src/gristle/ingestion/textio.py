"""Encoding-aware reading of source files.

Source is not always UTF-8. The most common non-UTF-8 case in real repos is a
generated file written with a byte-order mark — e.g. ``supabase gen types typescript
> types_db.ts`` under PowerShell emits UTF-16LE with a BOM. Two things must handle it:
the walker's binary check (UTF-16/UTF-32 are full of NUL bytes but are text, not
binary) and the content readers (decoding those bytes as UTF-8 yields garbage). Both
defer here.

Limitation: only BOM-marked encodings are detected. UTF-16/UTF-32 WITHOUT a BOM still
trips the walker's NUL-byte heuristic and is skipped (a silent miss, never a
corruption). In practice generated files that use these encodings emit a BOM.
"""

from __future__ import annotations

from pathlib import Path

# BOMs, longest first (a UTF-32LE BOM `ff fe 00 00` starts with the UTF-16LE BOM
# `ff fe`, so the 4-byte forms must be tested before the 2-byte forms).
_UTF32_BOMS = (b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")
_UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_UTF8_BOM = b"\xef\xbb\xbf"


def _bom_encoding(prefix: bytes) -> str | None:
    """The Python codec named by a leading BOM, or ``None`` for no recognized BOM.
    The ``utf-16``/``utf-32`` codecs read the BOM to pick endianness and strip it."""
    if prefix[:4] in _UTF32_BOMS:
        return "utf-32"
    if prefix[:2] in _UTF16_BOMS:
        return "utf-16"
    if prefix[:3] == _UTF8_BOM:
        return "utf-8-sig"
    return None


def is_wide_bom(prefix: bytes) -> bool:
    """True if ``prefix`` starts with a UTF-16 or UTF-32 BOM — an encoding whose bytes
    legitimately contain NUL, so the file is text despite the walker's NUL heuristic.
    A UTF-8 BOM is deliberately excluded: real UTF-8 source has no NUL bytes, so a
    UTF-8-BOM file that DOES contain them is still binary and should be skipped."""
    return prefix[:4] in _UTF32_BOMS or prefix[:2] in _UTF16_BOMS


def decode_text(raw: bytes) -> str:
    """Decode source bytes to ``str``, honoring a leading BOM (UTF-32/UTF-16 LE/BE,
    UTF-8-SIG) and falling back to UTF-8. Malformed bytes are replaced, never raised — a
    single bad file must not abort ingestion. The BOM is stripped by the codec, and
    newlines are normalized to ``\\n`` to match ``Path.read_text``'s universal-newline
    behavior (the readers this replaces relied on it, so parser line/column offsets and
    downstream text stay identical for CRLF files)."""
    encoding = _bom_encoding(raw) or "utf-8"
    text = raw.decode(encoding, errors="replace")
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_text_file(path: str | Path) -> str:
    """Read a file and decode it with :func:`decode_text` (BOM-aware)."""
    return decode_text(Path(path).read_bytes())
