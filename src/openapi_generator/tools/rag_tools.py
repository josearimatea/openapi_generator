"""
RAG over the OpenAPI 3.0 reference collection in Qdrant.

Read-only consumer: the collection (OPENAPI_COLLECTION_NAME) is populated
externally — typically by openapi_rulesbank.tools.rag_tools.index_openapi_reference().
This module only queries it.

Same embedding model and vector layout as the 3GPP RAG (dense "text-dense",
all-MiniLM-L6-v2, dim=384, cosine), so the two collections share infrastructure
but stay isolated by name.
"""

from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings
from langchain_qdrant import QdrantVectorStore
from qdrant_client import QdrantClient

from openapi_generator.config import get_logger
from openapi_generator.config.hardware import get_device
from openapi_generator.config.settings import (
    EMBEDDING_MODEL,
    OPENAPI_COLLECTION_NAME,
    OPENAPI_REFERENCE_RETRIEVE_CHUNKS,
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_TIMEOUT,
)

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_client() -> QdrantClient:
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=QDRANT_TIMEOUT)


@lru_cache(maxsize=1)
def _get_embeddings() -> HuggingFaceEmbeddings:
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": get_device()},
        encode_kwargs={"normalize_embeddings": True},
    )


def _get_vector_store() -> QdrantVectorStore:
    return QdrantVectorStore(
        client=_get_client(),
        collection_name=OPENAPI_COLLECTION_NAME,
        embedding=_get_embeddings(),
        vector_name="text-dense",
    )


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
        docs = _get_vector_store().similarity_search(query, k=k)
    except Exception as e:
        logger.warning(f"OpenAPI reference RAG failed ({type(e).__name__}: {e}) — returning ''")
        return ""

    if not docs:
        return ""

    texts = [doc.page_content for doc in docs]
    logger.debug(f"Retrieved {len(texts)} OpenAPI chunk(s) for: '{query[:60]}'")
    return "\n\n---\n\n".join(texts)
