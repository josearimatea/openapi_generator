"""
Optional semantic retrieval from the 3GPP Qdrant collection.

Design:
  - If Qdrant is unreachable OR the collection does not exist, retrieval
    returns []. The calling node decides what to do with no context.
  - Two retrieval modes:
      * filters provided → vector similarity with metadata filter pinning
                           release/series/spec.
      * no filters       → plain vector similarity over the whole collection.

Both modes go through qdrant_3gpp_rag.retrieve, the search every repository
shares: hybrid (dense + BM25) when the collection holds the sparse vector, and
filter keys resolved against the payload's "metadata." prefix. No LLM-driven
SelfQuery here (llm is never passed).

The Retriever protocol below is what build_openapi_gen_graph(retriever=...)
accepts when callers want to plug a different vector store entirely.
"""

from typing import Any, Dict, List, Optional, Protocol

from openapi_generator.config import get_logger
from openapi_generator.config.settings import DEFAULT_RETRIEVE_K, QDRANT_COLLECTION
from openapi_generator.rag.qdrant_factory import collection_available, get_factory

logger = get_logger(__name__)


class Retriever(Protocol):
    """Minimal retriever contract — anything callable with this signature works."""

    def __call__(
        self,
        query: str,
        k: int = DEFAULT_RETRIEVE_K,
        filters: Optional[Dict[str, Any]] = None,
    ) -> List[str]: ...


def _format_doc(doc: Any) -> str:
    md = getattr(doc, "metadata", None) or {}
    header = (
        f"release:     {md.get('release', 'unknown')}\n"
        f"series:      {md.get('series', 'unknown')}\n"
        f"spec:        {md.get('spec', 'unknown')}\n"
        f"chunk_index: {md.get('chunk_index', 'unknown')}\n\n"
    )
    return header + (doc.page_content or "").strip()


def get_relevant_chunks(
    query: str,
    k: int = DEFAULT_RETRIEVE_K,
    filters: Optional[Dict[str, Any]] = None,
    llm: Optional[Any] = None,
) -> List[str]:
    """
    Retrieve formatted 3GPP chunks for a query. Returns [] if RAG is unavailable.

    Args:
        query: natural-language search string.
        k: number of chunks to return.
        filters: optional metadata filter dict, e.g. {"release": "Rel-18"}.
        llm: accepted for API uniformity with other retrievers, but ignored —
             this retriever never calls an LLM (no SelfQuery).
    """
    if not collection_available(QDRANT_COLLECTION):
        logger.info(
            f"RAG skipped: collection '{QDRANT_COLLECTION}' unavailable. "
            "Returning [] — node will proceed without context."
        )
        return []

    try:
        from qdrant_3gpp_rag import retrieve

        docs = retrieve(
            query, factory=get_factory(), collection_name=QDRANT_COLLECTION,
            k=k, filters=filters or None,
        ).docs
    except Exception as e:
        logger.warning(f"RAG query failed ({type(e).__name__}: {e}) — returning []")
        return []

    logger.info(f"RAG retrieved {len(docs)} chunks for query={query!r}")
    return [_format_doc(doc) for doc in docs]
