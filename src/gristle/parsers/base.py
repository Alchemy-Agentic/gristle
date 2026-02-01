"""Abstract base class for language-specific parsers."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from gristle.models import ParsedFile


class LanguageParser(ABC):
    """Interface that every language parser must implement."""

    @property
    @abstractmethod
    def language_name(self) -> str:
        """Canonical language name, e.g. ``'python'``."""

    @property
    @abstractmethod
    def file_extensions(self) -> list[str]:
        """Extensions handled by this parser (without dot), e.g. ``['py', 'pyi']``."""

    @abstractmethod
    def parse_file(self, file_path: str, content: str) -> ParsedFile:
        """Parse *content* (source code) and return structured entities."""
