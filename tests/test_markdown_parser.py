"""Tests for the Markdown parser."""

from gristle.parsers.markdown import MarkdownParser


class TestTitleExtraction:
    def test_extracts_h1_title(self):
        parser = MarkdownParser()
        result = parser.parse("README.md", "# My Project\n\nSome content.\n")
        assert result.title == "My Project"

    def test_falls_back_to_filename(self):
        parser = MarkdownParser()
        result = parser.parse("docs/guide.md", "Some content without a heading.\n")
        assert result.title == "guide"

    def test_ignores_h2_for_title(self):
        parser = MarkdownParser()
        result = parser.parse("notes.md", "## Subsection\n\nContent.\n")
        assert result.title == "notes"

    def test_strips_trailing_hashes(self):
        parser = MarkdownParser()
        result = parser.parse("README.md", "# My Title ##\n")
        assert result.title == "My Title"


class TestDocTypeClassification:
    def test_readme(self):
        parser = MarkdownParser()
        result = parser.parse("README.md", "# Project\n")
        assert result.doc_type == "readme"

    def test_changelog(self):
        parser = MarkdownParser()
        result = parser.parse("CHANGELOG.md", "# Changelog\n")
        assert result.doc_type == "changelog"

    def test_adr(self):
        parser = MarkdownParser()
        result = parser.parse("docs/adr/001-use-redis.md", "# ADR 001\n")
        assert result.doc_type == "adr"

    def test_architecture(self):
        parser = MarkdownParser()
        result = parser.parse("docs/architecture.md", "# System Design\n")
        assert result.doc_type == "architecture"

    def test_contributing(self):
        parser = MarkdownParser()
        result = parser.parse("CONTRIBUTING.md", "# Contributing\n")
        assert result.doc_type == "contributing"

    def test_api_docs(self):
        parser = MarkdownParser()
        result = parser.parse("docs/api.md", "# API Reference\n")
        assert result.doc_type == "api"

    def test_guide(self):
        parser = MarkdownParser()
        result = parser.parse("docs/getting-started.md", "# Getting Started\n")
        assert result.doc_type == "guide"

    def test_other(self):
        parser = MarkdownParser()
        result = parser.parse("docs/notes.md", "# Random Notes\n")
        assert result.doc_type == "other"


class TestSectionExtraction:
    def test_extracts_sections(self):
        parser = MarkdownParser()
        content = "# Title\n\nIntro.\n\n## Section One\n\nContent.\n\n## Section Two\n\nMore.\n"
        result = parser.parse("doc.md", content)
        assert len(result.sections) == 3
        assert result.sections[0].heading == "Title"
        assert result.sections[0].level == 1
        assert result.sections[1].heading == "Section One"
        assert result.sections[1].level == 2
        assert result.sections[2].heading == "Section Two"
        assert result.sections[2].level == 2

    def test_section_end_lines(self):
        parser = MarkdownParser()
        content = "# Title\n\nLine 2.\n\n## Next\n\nLine 6.\n"
        result = parser.parse("doc.md", content)
        # First section ends before second section starts
        assert result.sections[0].end_line < result.sections[1].start_line

    def test_no_sections_in_empty_doc(self):
        parser = MarkdownParser()
        result = parser.parse("doc.md", "Just some text.\n")
        assert len(result.sections) == 0

    def test_deep_nesting(self):
        parser = MarkdownParser()
        content = "# H1\n\n## H2\n\n### H3\n\n#### H4\n"
        result = parser.parse("doc.md", content)
        assert len(result.sections) == 4
        assert result.sections[3].level == 4


class TestCodeReferenceExtraction:
    def test_extracts_inline_code_entity(self):
        parser = MarkdownParser()
        content = "# API\n\nUse the `UserService` to manage users.\n"
        result = parser.parse("doc.md", content)
        refs = result.code_references
        texts = [r.raw_text for r in refs]
        assert "UserService" in texts

    def test_extracts_camel_case_reference(self):
        parser = MarkdownParser()
        content = "# Guide\n\nCall `getUserById` to fetch a user.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert "getUserById" in texts

    def test_extracts_snake_case_reference(self):
        parser = MarkdownParser()
        content = "# Guide\n\nThe `get_user_by_id` function returns a user.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert "get_user_by_id" in texts

    def test_extracts_dotted_name(self):
        parser = MarkdownParser()
        content = "# Guide\n\nCall `router.navigate` to change routes.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert "router.navigate" in texts

    def test_extracts_react_hook(self):
        parser = MarkdownParser()
        content = "# Hooks\n\nUse `useAuth` for authentication state.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert "useAuth" in texts

    def test_extracts_file_path_in_inline_code(self):
        parser = MarkdownParser()
        content = "# Setup\n\nEdit `src/config/settings.ts` to configure.\n"
        result = parser.parse("doc.md", content)
        refs = [r for r in result.code_references if r.ref_type == "file_path"]
        assert any("src/config/settings.ts" in r.raw_text for r in refs)

    def test_extracts_source_link(self):
        parser = MarkdownParser()
        content = "# Files\n\nSee [config](src/config.ts) for details.\n"
        result = parser.parse("doc.md", content)
        refs = [r for r in result.code_references if r.ref_type == "link"]
        assert any("src/config.ts" in r.raw_text for r in refs)

    def test_ignores_external_links(self):
        parser = MarkdownParser()
        content = "# Links\n\nVisit [docs](https://example.com) for more.\n"
        result = parser.parse("doc.md", content)
        refs = [r for r in result.code_references if r.ref_type == "link"]
        assert len(refs) == 0

    def test_ignores_shell_commands(self):
        parser = MarkdownParser()
        content = "# Install\n\nRun `$ npm install` to set up.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert "$ npm install" not in texts

    def test_ignores_fenced_code_blocks(self):
        parser = MarkdownParser()
        content = (
            "# Example\n\n"
            "```typescript\n"
            "const x = new UserService();\n"
            "```\n\n"
            "Use `UserService` directly.\n"
        )
        result = parser.parse("doc.md", content)
        # Only the inline `UserService` after the block should be captured
        entity_refs = [r for r in result.code_references if r.raw_text == "UserService"]
        assert len(entity_refs) == 1

    def test_deduplicates_references(self):
        parser = MarkdownParser()
        content = "# Guide\n\nUse `UserService` here and `UserService` there.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert texts.count("UserService") == 1

    def test_bare_file_path_in_prose(self):
        parser = MarkdownParser()
        content = "# Structure\n\nThe main entry is src/index.ts and config in src/config.ts.\n"
        result = parser.parse("doc.md", content)
        refs = [r for r in result.code_references if r.ref_type == "file_path"]
        paths = [r.raw_text for r in refs]
        assert "src/index.ts" in paths
        assert "src/config.ts" in paths

    def test_preamble_refs_extracted(self):
        parser = MarkdownParser()
        content = "Use `UserService` for users.\n\n# Section\n\nContent.\n"
        result = parser.parse("doc.md", content)
        texts = [r.raw_text for r in result.code_references]
        assert "UserService" in texts


class TestLineCount:
    def test_counts_lines(self):
        parser = MarkdownParser()
        result = parser.parse("doc.md", "Line 1\nLine 2\nLine 3\n")
        assert result.line_count == 3

    def test_empty_doc(self):
        parser = MarkdownParser()
        result = parser.parse("doc.md", "")
        assert result.line_count == 0  # splitlines on empty gives []


class TestEdgeCases:
    def test_empty_document(self):
        parser = MarkdownParser()
        result = parser.parse("empty.md", "")
        assert result.title == "empty"
        assert result.doc_type == "other"
        assert len(result.sections) == 0

    def test_heading_only(self):
        parser = MarkdownParser()
        result = parser.parse("doc.md", "# Just a Title\n")
        assert result.title == "Just a Title"
        assert len(result.sections) == 1
