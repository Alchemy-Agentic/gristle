"""Tests for gristle.parsers.registry — ParserRegistry."""

from __future__ import annotations

from unittest.mock import MagicMock

from gristle.parsers.registry import ParserRegistry


def _make_mock_parser(language: str, extensions: list[str]):
    parser = MagicMock()
    parser.language_name = language
    parser.file_extensions = extensions
    return parser


class TestRegister:
    def test_register_single_parser(self):
        reg = ParserRegistry()
        parser = _make_mock_parser("python", ["py", "pyi"])
        reg.register(parser)
        assert reg.get_parser("foo.py") is parser
        assert reg.get_parser("stubs.pyi") is parser

    def test_register_multiple_parsers(self):
        reg = ParserRegistry()
        py = _make_mock_parser("python", ["py"])
        ts = _make_mock_parser("typescript", ["ts", "tsx"])
        reg.register(py)
        reg.register(ts)
        assert reg.get_parser("app.ts") is ts
        assert reg.get_parser("comp.tsx") is ts
        assert reg.get_parser("main.py") is py


class TestGetParser:
    def test_returns_none_for_unknown_extension(self):
        reg = ParserRegistry()
        reg.register(_make_mock_parser("python", ["py"]))
        assert reg.get_parser("file.rb") is None

    def test_returns_none_for_no_extension(self):
        reg = ParserRegistry()
        reg.register(_make_mock_parser("python", ["py"]))
        assert reg.get_parser("Makefile") is None

    def test_returns_none_when_empty_registry(self):
        reg = ParserRegistry()
        assert reg.get_parser("file.py") is None


class TestParseFile:
    def test_delegates_to_parser(self):
        reg = ParserRegistry()
        parser = _make_mock_parser("python", ["py"])
        parsed = MagicMock()
        parser.parse_file.return_value = parsed
        reg.register(parser)

        result = reg.parse_file("test.py", "def foo(): pass")
        assert result is parsed
        parser.parse_file.assert_called_once_with("test.py", "def foo(): pass")

    def test_returns_none_for_unsupported(self):
        reg = ParserRegistry()
        assert reg.parse_file("file.rb", "code") is None


class TestSupportedExtensions:
    def test_empty_registry(self):
        reg = ParserRegistry()
        assert reg.supported_extensions == frozenset()

    def test_returns_all_registered_extensions(self):
        reg = ParserRegistry()
        reg.register(_make_mock_parser("python", ["py", "pyi"]))
        reg.register(_make_mock_parser("typescript", ["ts", "tsx"]))
        assert reg.supported_extensions == frozenset({"py", "pyi", "ts", "tsx"})

    def test_is_frozenset(self):
        reg = ParserRegistry()
        assert isinstance(reg.supported_extensions, frozenset)


class TestBuildDefault:
    def test_registers_builtin_parsers(self):
        reg = ParserRegistry().build_default()
        assert reg.get_parser("file.py") is not None
        assert reg.get_parser("file.ts") is not None
        assert reg.get_parser("file.js") is not None
        assert reg.get_parser("file.tsx") is not None
        assert reg.get_parser("file.jsx") is not None

    def test_returns_self(self):
        reg = ParserRegistry()
        result = reg.build_default()
        assert result is reg
