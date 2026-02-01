"""Code embeddings for semantic search over the code graph.

Uses sentence-transformers to embed function/class signatures and docstrings,
then stores embeddings in FalkorDB's native vector index for fast similarity
search.

Requires the optional ``search`` dependency group::

    pip install gristle[search]
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from redis.exceptions import ResponseError

if TYPE_CHECKING:
    from gristle.graph.client import GraphClient

logger = logging.getLogger(__name__)

# Default model: lightweight, CPU-friendly, 384 dimensions, ~22MB
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


class CodeEmbedder:
    """Generates embeddings from code signatures and docstrings."""

    def __init__(self, model_name: str = DEFAULT_MODEL) -> None:
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as err:
            raise ImportError(
                "sentence-transformers is required for semantic search. Install it with: pip install gristle[search]"
            ) from err
        self._model = SentenceTransformer(model_name)
        self._dim = self._model.get_sentence_embedding_dimension()

    @property
    def dimension(self) -> int:
        return self._dim

    def embed_text(self, text: str) -> list[float]:
        """Embed a single text string."""
        return self._model.encode(text, convert_to_tensor=False).tolist()

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts (more efficient than one-by-one)."""
        embeddings = self._model.encode(texts, batch_size=64, show_progress_bar=False, convert_to_tensor=False)
        return [e.tolist() for e in embeddings]

    @staticmethod
    def build_function_text(name: str, signature: str, docstring: str | None) -> str:
        """Build the text to embed for a function node."""
        parts = [signature]
        if docstring:
            parts.append(docstring)
        return "\n".join(parts)

    @staticmethod
    def build_class_text(name: str, signature: str, docstring: str | None) -> str:
        """Build the text to embed for a class node."""
        parts = [signature]
        if docstring:
            parts.append(docstring)
        return "\n".join(parts)


class SemanticIndex:
    """Manages vector indexes and search over the code graph."""

    def __init__(self, graph: GraphClient, embedder: CodeEmbedder) -> None:
        self._graph = graph
        self._embedder = embedder

    def create_indexes(self) -> None:
        """Create vector indexes on Function and Class nodes."""
        dim = self._embedder.dimension
        for label in ("Function", "Class"):
            try:
                self._graph.execute(
                    f"CREATE VECTOR INDEX FOR (n:{label}) ON (n.embedding) "
                    f"OPTIONS {{dimension: {dim}, similarityFunction: 'cosine'}}"
                )
                logger.info("Created vector index on %s.embedding (dim=%d)", label, dim)
            except ResponseError:
                # Index may already exist
                pass

    def index_all(self, batch_size: int = 200) -> dict[str, int]:
        """Embed and index all Function and Class nodes that lack embeddings.

        Returns counts of nodes indexed per label.
        """
        counts: dict[str, int] = {}

        for label, text_builder in [
            ("Function", CodeEmbedder.build_function_text),
            ("Class", CodeEmbedder.build_class_text),
        ]:
            # Fetch nodes without embeddings
            result = self._graph.execute(
                f"MATCH (n:{label}) "
                f"WHERE n.embedding IS NULL "
                f"RETURN n.id AS id, n.name AS name, n.signature AS signature, "
                f"n.docstring AS docstring"
            )

            if not result.records:
                counts[label] = 0
                continue

            # Build texts and embed in batches
            ids: list[str] = []
            texts: list[str] = []
            for rec in result.records:
                ids.append(rec["id"])
                texts.append(
                    text_builder(
                        rec["name"],
                        rec["signature"] or rec["name"],
                        rec["docstring"] if rec["docstring"] else None,
                    )
                )

            total = len(texts)
            indexed = 0

            for i in range(0, total, batch_size):
                batch_texts = texts[i : i + batch_size]
                batch_ids = ids[i : i + batch_size]
                embeddings = self._embedder.embed_batch(batch_texts)

                for node_id, embedding in zip(batch_ids, embeddings, strict=True):
                    self._graph.execute(
                        f"MATCH (n:{label} {{id: $id}}) SET n.embedding = vecf32($embedding)",
                        {"id": node_id, "embedding": embedding},
                    )
                    indexed += 1

            counts[label] = indexed
            logger.info("Indexed %d %s nodes with embeddings", indexed, label)

        return counts

    def search(
        self,
        query: str,
        limit: int = 10,
        labels: list[str] | None = None,
    ) -> list[dict]:
        """Semantic search across code entities.

        Args:
            query: Natural language query (e.g. "validates email addresses").
            limit: Maximum results to return.
            labels: Which node types to search. Defaults to ["Function", "Class"].

        Returns:
            List of dicts with keys: id, name, signature, docstring, file_path,
            label, score (lower = more similar for cosine distance).
        """
        query_vec = self._embedder.embed_text(query)
        search_labels = labels or ["Function", "Class"]
        all_results: list[dict] = []

        for label in search_labels:
            try:
                result = self._graph.execute(
                    f"CALL db.idx.vector.queryNodes("
                    f"'{label}', 'embedding', $limit, vecf32($qvec)"
                    f") YIELD node, score "
                    f"RETURN node.id AS id, node.name AS name, "
                    f"node.signature AS signature, node.docstring AS docstring, "
                    f"node.file_path AS file_path, score "
                    f"ORDER BY score ASC",
                    {"limit": limit, "qvec": query_vec},
                )
                for rec in result.records:
                    rec["label"] = label
                    all_results.append(rec)
            except ResponseError as e:
                logger.warning("Vector search on %s failed: %s", label, e)

        # Sort by score (cosine distance — lower is more similar)
        all_results.sort(key=lambda r: r["score"])
        return all_results[:limit]
