"""Single-file-component parser for Vue, Svelte, and Astro.

An SFC is a container: an embedded ``<script>`` block (Vue/Svelte) or ``---``
frontmatter (Astro) holding TS/JS, plus a template and styles. Rather than add a
new tree-sitter grammar per framework (none are maintained at the ABI this project
pins), the SFC parser **locates the embedded code block and parses it with the
existing TypeScript tree-sitter parser** — so the script's functions, classes,
imports, and module variables become first-class graph nodes.

Architecture note: the TS/JS is parsed by tree-sitter (the project rule). Only the
SFC *container* is string-scanned to find the script block — the same
"container with embedded code" exception already made for Markdown. Non-script
regions are blanked (newlines preserved) so tree-sitter reports the script's line
numbers in the SFC file's own coordinate space — no offset bookkeeping needed.
"""

from __future__ import annotations

from gristle.models import ParsedFile
from gristle.parsers.base import LanguageParser
from gristle.parsers.typescript import TypeScriptParser

_TAG_BOUNDARY = " \t\r\n>/"


class SFCParser(LanguageParser):
    """Parses Vue/Svelte/Astro single-file components via their embedded script."""

    def __init__(self) -> None:
        self._ts = TypeScriptParser()

    @property
    def language_name(self) -> str:
        return "sfc"

    @property
    def file_extensions(self) -> list[str]:
        return ["vue", "svelte", "astro"]

    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        ext = file_path.rsplit(".", 1)[-1].lower() if "." in file_path else ""
        regions = self._find_script_regions(content, ext)
        if not regions:
            # No embedded script (template/style only) — still a real file node.
            return ParsedFile(path=file_path, language=ext, line_count=content.count("\n") + 1)

        masked = self._mask(content, regions)
        # Delegate to the TS parser. file_path keeps the .vue/.svelte/.astro suffix,
        # so qualified names and the extension dispatch (-> TS, not TSX) are correct.
        parsed = self._ts.parse_file(file_path, masked)
        parsed.language = ext  # vue / svelte / astro (TS parser hardcodes "typescript")
        return parsed

    # ------------------------------------------------------------------
    # Embedded-code block location (container scan, not code parsing)
    # ------------------------------------------------------------------

    def _find_script_regions(self, content: str, ext: str) -> list[tuple[int, int]]:
        """Return (start, end) char ranges of embedded TS/JS code to keep."""
        if ext == "astro":
            regions = self._astro_frontmatter(content)
            regions.extend(self._tag_regions(content, "script"))  # client-side scripts
            return regions
        # Vue and Svelte: one or more <script> blocks (e.g. Vue <script setup> + <script>).
        return self._tag_regions(content, "script")

    @staticmethod
    def _tag_regions(content: str, tag: str) -> list[tuple[int, int]]:
        """Find the inner-content ranges of every ``<tag ...> ... </tag>`` block."""
        regions: list[tuple[int, int]] = []
        lower = content.lower()
        open_prefix = "<" + tag
        close_tag = "</" + tag
        pos = 0
        while True:
            i = lower.find(open_prefix, pos)
            if i == -1:
                break
            after = i + len(open_prefix)
            # Ensure this is the <tag> element, not <tagsomething>.
            if after < len(content) and content[after] not in _TAG_BOUNDARY:
                pos = after
                continue
            open_end = content.find(">", i)
            if open_end == -1:
                break
            close = lower.find(close_tag, open_end)
            if close == -1:
                break
            if close > open_end + 1:  # skip empty / self-closing (e.g. <script src=...>)
                regions.append((open_end + 1, close))
            pos = close + len(close_tag)
        return regions

    @staticmethod
    def _astro_frontmatter(content: str) -> list[tuple[int, int]]:
        """The Astro code fence: ``---`` … ``---`` at the top of the file."""
        stripped = content.lstrip()
        if not stripped.startswith("---"):
            return []
        open_fence = content.find("---")
        body_start = open_fence + 3
        # Closing fence is `---` at the start of a line.
        close = content.find("\n---", body_start)
        if close == -1:
            return []
        return [(body_start, close)]

    @staticmethod
    def _mask(content: str, regions: list[tuple[int, int]]) -> str:
        """Blank everything outside *regions* with spaces, preserving newlines (and
        thus line/column positions) so the delegated parser reports SFC-file lines."""
        keep = bytearray(len(content))
        for start, end in regions:
            for i in range(max(0, start), min(len(content), end)):
                keep[i] = 1
        out = [ch if (keep[idx] or ch == "\n") else " " for idx, ch in enumerate(content)]
        return "".join(out)
