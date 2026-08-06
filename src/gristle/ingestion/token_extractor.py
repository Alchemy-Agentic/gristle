"""Design-token extraction phase — creates Token nodes (and File-[:CONTAINS]->Token).

Runs as an isolated, best-effort phase near the end of ``IngestionPipeline`` (like the
schema and flag phases). Reads ``.css`` files (whitelisted into the walk via the
pipeline's ``schema_extensions``), parses their custom-property definitions with
``parsers.design_tokens.parse_css_tokens``, dedupes per name (first definition wins —
the ``:root``/light default usually precedes a ``.dark`` override), and writes one
``Token`` node per distinct name plus a ``CONTAINS`` edge from its defining file.

Token USAGE (component -> token) is a separate, later slice (USES_TOKEN edges); this
phase only inventories the definitions.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from gristle.config import settings
from gristle.ingestion.batch import BatchCollector
from gristle.ingestion.textio import read_text_file
from gristle.models import ParsedToken, TokenExtractionResult

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient
    from gristle.ingestion.walker import WalkedFile
    from gristle.models import ParsedFile

logger = logging.getLogger(__name__)

# Committed build output / vendored CSS: a compiled stylesheet holds the full expanded
# token set (every `--tw-*` internal), so it sprays noise and — via cross-file dedupe —
# can shadow the real source. Skipped by path segment or a minified suffix.
_SKIP_CSS_SEGMENTS = frozenset(
    {"dist", "build", "out", ".next", ".output", ".nuxt", "vendor", "coverage", "storybook-static"}
)


def _is_generated_css(relative_path: str) -> bool:
    p = relative_path.lower().replace("\\", "/")
    if p.endswith(".min.css"):
        return True
    return any(seg in _SKIP_CSS_SEGMENTS for seg in p.split("/"))


class TokenExtractor:
    """Post-schema processor that creates Token nodes from CSS custom properties."""

    def __init__(self, graph: GraphClient, file_path_to_id: dict[str, str]) -> None:
        self.graph = graph
        self._file_path_to_id = file_path_to_id

    def extract(
        self, walked_files: list[WalkedFile], parsed_files: list[ParsedFile] | None = None
    ) -> TokenExtractionResult:
        if not settings.design_tokens_enabled:
            return TokenExtractionResult()

        from gristle.parsers.design_tokens import parse_css_tokens

        css_files = [wf for wf in walked_files if wf.extension == "css" and not _is_generated_css(wf.relative_path)]
        # Deterministic dedupe (first wins): shallower path wins — `src/index.css` beats a
        # deep `docs/.../copy.css` — then lexicographic, so the winner never depends on
        # filesystem walk order.
        css_files.sort(key=lambda wf: (wf.relative_path.count("/"), wf.relative_path))

        defs: list[ParsedToken] = []
        for wf in css_files:
            content = self._read_file(wf)
            if content is not None:
                defs.extend(parse_css_tokens(wf.relative_path, content))

        return self._write_tokens(defs)

    def _write_tokens(self, defs: list[ParsedToken]) -> TokenExtractionResult:
        # Dedupe per name — first definition wins (:root/light default precedes .dark).
        by_name: dict[str, ParsedToken] = {}
        for d in defs:
            by_name.setdefault(d.name, d)

        batch = BatchCollector(self.graph, settings.ingestion_batch_size)
        for name, tok in sorted(by_name.items()):
            token_id = f"token::{name}"
            batch.add_node(
                "Token",
                {
                    "id": token_id,
                    "name": name,
                    "raw_name": f"--{name}",
                    "value": tok.value,
                    "category": tok.category,
                    "source_kind": tok.source_kind,
                    "references": tok.references,
                    "file_path": tok.file_path,
                    "line": tok.line,
                },
            )
            if tok.file_path:
                batch.add_relationship("CONTAINS", self._file_id_for(tok.file_path, batch), token_id)

        counts = batch.flush()
        return TokenExtractionResult(
            tokens_found=len(by_name),
            nodes_created=counts["nodes_created"],
            relationships_created=counts["relationships_created"],
        )

    def _file_id_for(self, file_path: str, batch: BatchCollector) -> str:
        """File node id for a CSS file, creating a minimal File node the first time — a
        `.css` file has no parser, so Phase 1 built no File node for it (same as the
        Prisma path in SchemaExtractor)."""
        file_id = self._file_path_to_id.get(file_path)
        if not file_id:
            file_id = f"file::{file_path}"
            batch.add_node("File", {"id": file_id, "path": file_path, "language": "css", "line_count": 0})
            self._file_path_to_id[file_path] = file_id
        return file_id

    @staticmethod
    def _read_file(wf: WalkedFile) -> str | None:
        try:
            return read_text_file(wf.absolute_path)
        except OSError:
            logger.warning("Token extractor: cannot read %s", wf.relative_path)
            return None
