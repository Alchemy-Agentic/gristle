"""Parser registry: maps file extensions to language parsers."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gristle.models import ParsedFile
    from gristle.parsers.base import LanguageParser


class ParserRegistry:
    """Registry that dispatches files to the correct language parser."""

    def __init__(self) -> None:
        self._parsers: dict[str, LanguageParser] = {}
        self._extension_map: dict[str, str] = {}

    def register(self, parser: LanguageParser) -> None:
        self._parsers[parser.language_name] = parser
        for ext in parser.file_extensions:
            self._extension_map[ext] = parser.language_name

    def get_parser(self, file_path: str) -> LanguageParser | None:
        ext = file_path.rsplit(".", 1)[-1] if "." in file_path else ""
        language = self._extension_map.get(ext)
        return self._parsers.get(language) if language else None

    def parse_file(self, file_path: str, content: str) -> ParsedFile | None:
        parser = self.get_parser(file_path)
        if parser is None:
            return None
        return parser.parse_file(file_path, content)

    @property
    def supported_extensions(self) -> frozenset[str]:
        return frozenset(self._extension_map)

    def build_default(self) -> ParserRegistry:
        """Register all built-in parsers and return self for chaining."""
        from gristle.parsers.python import PythonParser
        from gristle.parsers.typescript import JavaScriptParser, TypeScriptParser

        self.register(PythonParser())
        self.register(TypeScriptParser())
        self.register(JavaScriptParser())
        return self
