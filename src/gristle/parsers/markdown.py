"""Markdown document parser — extracts headings, code references, and file paths."""

from __future__ import annotations

import re
from pathlib import PurePosixPath

from gristle.models import CodeReference, DocumentSection, ParsedDocument

# Patterns for extracting code references from markdown
_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
_FENCED_BLOCK_RE = re.compile(r"^```(\w*)", re.MULTILINE)
_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)(?:\s*#*\s*)?$", re.MULTILINE)
_LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)]+)\)")
_FILE_PATH_RE = re.compile(
    r"(?:^|[\s(\"'])("
    r"(?:src|lib|app|pages|components|hooks|utils|types|services|config|styles)"
    r"/[\w/.@-]+"
    r"\.(?:ts|tsx|js|jsx|py|css|scss|json|yaml|yml|toml|sql)"
    r")",
    re.MULTILINE,
)

# Code entity patterns: PascalCase identifiers or dotted.names that look like code
_CODE_ENTITY_RE = re.compile(
    r"^(?:"
    r"[A-Z][a-zA-Z0-9]*(?:\.[a-zA-Z]\w*)*"  # PascalCase: UserService, Auth.Provider
    r"|[a-z]\w*(?:\.[a-z]\w*)+"              # dotted.name: router.navigate, req.body
    r"|use[A-Z]\w*"                           # React hooks: useAuth, useState
    r")$"
)

# Doc type classification by filename/path patterns
_DOC_TYPE_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("readme", re.compile(r"readme", re.IGNORECASE)),
    ("changelog", re.compile(r"changelog|changes|release.?notes", re.IGNORECASE)),
    ("adr", re.compile(r"adr|arch.*decision", re.IGNORECASE)),
    ("architecture", re.compile(r"architect|design|system", re.IGNORECASE)),
    ("contributing", re.compile(r"contribut", re.IGNORECASE)),
    ("api", re.compile(r"api|endpoint|openapi|swagger", re.IGNORECASE)),
    ("guide", re.compile(r"guide|tutorial|howto|getting.?started|onboard", re.IGNORECASE)),
]


class MarkdownParser:
    """Parses markdown files to extract structure and code references."""

    def parse(self, file_path: str, content: str) -> ParsedDocument:
        lines = content.splitlines()
        title = self._extract_title(lines, file_path)
        doc_type = self._classify_doc_type(file_path, title)
        sections = self._extract_sections(lines)
        all_refs: list[CodeReference] = []

        # Extract code references per section
        for section in sections:
            section_text = "\n".join(lines[section.start_line - 1 : section.end_line])
            refs = self._extract_code_references(section_text, section.start_line)
            section.code_references = refs
            all_refs.extend(refs)

        # Also extract refs from any content before the first heading
        if sections and sections[0].start_line > 1:
            preamble = "\n".join(lines[: sections[0].start_line - 1])
            preamble_refs = self._extract_code_references(preamble, 1)
            all_refs = preamble_refs + all_refs

        return ParsedDocument(
            path=file_path,
            title=title,
            doc_type=doc_type,
            sections=sections,
            code_references=all_refs,
            line_count=len(lines),
        )

    def _extract_title(self, lines: list[str], file_path: str) -> str:
        """Extract title from first H1 heading, or fall back to filename."""
        for line in lines[:20]:
            m = re.match(r"^#\s+(.+?)(?:\s*#*\s*)?$", line)
            if m:
                return m.group(1).strip()
        return PurePosixPath(file_path).stem

    def _classify_doc_type(self, file_path: str, title: str) -> str:
        combined = f"{file_path} {title}"
        for doc_type, pattern in _DOC_TYPE_PATTERNS:
            if pattern.search(combined):
                return doc_type
        return "other"

    def _extract_sections(self, lines: list[str]) -> list[DocumentSection]:
        """Extract heading-delimited sections."""
        sections: list[DocumentSection] = []
        for i, line in enumerate(lines):
            m = re.match(r"^(#{1,6})\s+(.+?)(?:\s*#*\s*)?$", line)
            if m:
                level = len(m.group(1))
                heading = m.group(2).strip()
                sections.append(DocumentSection(
                    heading=heading,
                    level=level,
                    start_line=i + 1,
                    end_line=len(lines),  # Will be adjusted below
                ))

        # Set end_line for each section (up to the next section at same or higher level)
        for i, section in enumerate(sections):
            if i + 1 < len(sections):
                section.end_line = sections[i + 1].start_line - 1
            # else: already set to len(lines)

        return sections

    def _extract_code_references(
        self, text: str, base_line: int
    ) -> list[CodeReference]:
        """Extract code entity references from a block of markdown text."""
        refs: list[CodeReference] = []
        seen: set[str] = set()
        lines = text.splitlines()
        in_fenced_block = False

        for i, line in enumerate(lines):
            actual_line = base_line + i

            # Track fenced code blocks — don't extract refs from inside them
            if line.strip().startswith("```"):
                in_fenced_block = not in_fenced_block
                continue
            if in_fenced_block:
                continue

            # Inline code spans: `someCode`
            for m in _INLINE_CODE_RE.finditer(line):
                raw = m.group(1).strip()
                if raw and raw not in seen:
                    ref_type = self._classify_inline_ref(raw)
                    if ref_type:
                        refs.append(CodeReference(
                            raw_text=raw,
                            ref_type=ref_type,
                            line=actual_line,
                        ))
                        seen.add(raw)

            # Markdown links to source files
            for m in _LINK_RE.finditer(line):
                url = m.group(2)
                if self._is_source_link(url):
                    if url not in seen:
                        refs.append(CodeReference(
                            raw_text=url,
                            ref_type="link",
                            line=actual_line,
                        ))
                        seen.add(url)

            # Bare file path references in prose
            for m in _FILE_PATH_RE.finditer(line):
                path = m.group(1)
                if path not in seen:
                    refs.append(CodeReference(
                        raw_text=path,
                        ref_type="file_path",
                        line=actual_line,
                    ))
                    seen.add(path)

        return refs

    @staticmethod
    def _classify_inline_ref(text: str) -> str | None:
        """Classify an inline code span as a code entity, file path, or noise."""
        # Skip shell commands, SQL, CSS, etc.
        if text.startswith(("$", "#", "-", ">", "SELECT", "INSERT", "CREATE")):
            return None
        # Skip very short or very long spans
        if len(text) < 2 or len(text) > 100:
            return None
        # Skip things with spaces (likely prose in backticks or commands)
        if " " in text and not text.startswith(("import ", "from ", "export ")):
            return None

        # File paths
        if "/" in text and "." in text.rsplit("/", 1)[-1]:
            return "file_path"

        # Code entities (PascalCase, dotted names, hooks)
        if _CODE_ENTITY_RE.match(text):
            return "inline_code"

        # camelCase identifiers
        if re.match(r"^[a-z][a-zA-Z0-9]*$", text) and any(c.isupper() for c in text):
            return "inline_code"

        # snake_case identifiers (common in Python docs)
        if re.match(r"^[a-z]\w*(?:_\w+)+$", text):
            return "inline_code"

        return None

    @staticmethod
    def _is_source_link(url: str) -> bool:
        """Check if a markdown link URL points to a source file."""
        if url.startswith(("http://", "https://", "mailto:", "#")):
            return False
        source_exts = {
            ".ts", ".tsx", ".js", ".jsx", ".py", ".css", ".scss",
            ".json", ".yaml", ".yml", ".toml", ".sql",
        }
        for ext in source_exts:
            if ext in url:
                return True
        return False
