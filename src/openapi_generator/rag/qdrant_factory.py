"""
Access to Qdrant through qdrant_3gpp_rag, the package every repository shares.

The client, the embedding model and the vector stores all come from its
QdrantFactory, so the generator embeds queries with the model the shared
collections were built with, and searches them hybrid (dense + BM25) when they
hold the sparse vector.

Lazy: the factory is built on first use and cached, so importing this module
does not touch the network or load torch.
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
def get_factory() -> Any:
    """The shared QdrantFactory for this process (client + embeddings)."""
    from qdrant_3gpp_rag import QdrantFactory

    device = get_device()
    logger.info(f"Connecting to Qdrant at {QDRANT_HOST}:{QDRANT_PORT}; "
                f"embeddings: {EMBEDDING_MODEL} on {device}")
    return QdrantFactory.get_instance(
        host=QDRANT_HOST, port=QDRANT_PORT, timeout=QDRANT_TIMEOUT,
        model_name=EMBEDDING_MODEL, device=device,
    )


def _get_qdrant_client() -> Any:
    return get_factory().client


def _get_embeddings() -> Any:
    return get_factory().embeddings


def get_vector_store(collection_name: str = QDRANT_COLLECTION) -> Any:
    """A QdrantVectorStore bound to the given collection — hybrid when the
    collection holds the sparse vector, dense-only otherwise."""
    return get_factory().get_vector_store(collection_name)


def collection_available(collection_name: str = QDRANT_COLLECTION) -> bool:
    """Check Qdrant is reachable and the collection exists. Never raises."""
    try:
        client = _get_qdrant_client()
        return client.collection_exists(collection_name)
    except Exception as e:
        logger.warning(f"Qdrant unavailable ({type(e).__name__}: {e})")
        return False
