"""
RAG over the OpenAPI 3.0 reference collection in Qdrant.

Read-only consumer: the collection (OPENAPI_COLLECTION_NAME) is populated
externally — typically by openapi_rulesbank.tools.rag_tools.index_openapi_reference().
This module only queries it.

Opened through the shared QdrantFactory (rag.qdrant_factory), like the 3GPP
collections: same embedding model, hybrid search when the collection holds the
sparse vector; the collections stay isolated by name.
"""

from openapi_generator.config import get_logger
from openapi_generator.config.settings import (
    OPENAPI_COLLECTION_NAME,
    OPENAPI_REFERENCE_RETRIEVE_CHUNKS,
)
from openapi_generator.rag.qdrant_factory import _get_qdrant_client, get_factory

logger = get_logger(__name__)


def _get_client():
    return _get_qdrant_client()


def search_openapi_reference(
    query: str,
    k: int = OPENAPI_REFERENCE_RETRIEVE_CHUNKS,
) -> str:
    """
    Return the top-k OpenAPI 3.0 reference chunks for `query`, joined by
    `---` separators and ready to inject into a prompt. Returns "" (with a
    warning) when the collection is missing — caller proceeds without it.
    """
    try:
        client = _get_client()
        if not client.collection_exists(OPENAPI_COLLECTION_NAME):
            logger.warning(
                f"Collection '{OPENAPI_COLLECTION_NAME}' not found. "
                "Populate it via openapi_rulesbank.tools.rag_tools.index_openapi_reference() "
                "before running the Patcher."
            )
            return ""
        from qdrant_3gpp_rag import retrieve

        docs = retrieve(query, factory=get_factory(),
                        collection_name=OPENAPI_COLLECTION_NAME, k=k).docs
    except Exception as e:
        logger.warning(f"OpenAPI reference RAG failed ({type(e).__name__}: {e}) — returning ''")
        return ""

    if not docs:
        return ""

    texts = [doc.page_content for doc in docs]
    logger.debug(f"Retrieved {len(texts)} OpenAPI chunk(s) for: '{query[:60]}'")
    return "\n\n---\n\n".join(texts)
