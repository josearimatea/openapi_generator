"""
Factory for HuggingFace embeddings + QdrantVectorStore.

Lazy: the embedder and the Qdrant client are constructed on first use and
cached, so importing this module does not touch the network or load torch.
"""

from functools import lru_cache
from typing import Any

from openapi_generator.config import get_logger
from openapi_generator.config.hardware import get_device
from openapi_generator.config.settings import (
    EMBEDDING_MODEL,
    QDRANT_COLLECTION,
    QDRANT_HOST,
    QDRANT_PORT,
    QDRANT_TIMEOUT,
)

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def _get_qdrant_client() -> Any:
    from qdrant_client import QdrantClient

    logger.info(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}")
    return QdrantClient(host=QDRANT_HOST, port=QDRANT_PORT, timeout=QDRANT_TIMEOUT)


@lru_cache(maxsize=1)
def _get_embeddings() -> Any:
    from langchain_huggingface import HuggingFaceEmbeddings

    device = get_device()
    logger.info(f"Loading embeddings: {EMBEDDING_MODEL} on {device}")
    return HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL,
        model_kwargs={"device": device},
        encode_kwargs={"normalize_embeddings": True},
    )


def get_vector_store(collection_name: str = QDRANT_COLLECTION) -> Any:
    """Build a QdrantVectorStore bound to the given collection."""
    from langchain_qdrant import QdrantVectorStore

    return QdrantVectorStore(
        client=_get_qdrant_client(),
        collection_name=collection_name,
        embedding=_get_embeddings(),
        vector_name="text-dense",
        sparse_vector_name="text-sparse",
    )


def collection_available(collection_name: str = QDRANT_COLLECTION) -> bool:
    """Check Qdrant is reachable and the collection exists. Never raises."""
    try:
        client = _get_qdrant_client()
        return client.collection_exists(collection_name)
    except Exception as e:
        logger.warning(f"Qdrant unavailable ({type(e).__name__}: {e})")
        return False
