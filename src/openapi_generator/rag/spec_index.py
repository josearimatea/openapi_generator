"""
Index the specification being processed into a collection that lasts one run.

The permanent collections hold the 3GPP corpus and the OpenAPI reference —
background knowledge, the same for every run. This one holds the document at
hand, and exists because some questions can only be answered from it: what the
specification calls the service it defines, where it says the service is
hosted. Those answers are stated once, in prose, somewhere among hundreds of
sections, and nothing about their position is predictable.

Searching the text for them directly does not work, and the ways it fails are
instructive. The short name a run is given appears mostly inside URI templates
and mapping tables, which identify the service without ever expanding it; the
full name appears in the prose of the clauses that define it, rarely in the
same sentence. A pattern tuned until it finds one specification's phrasing is
tuned to that specification, and the next one words it differently — which is
the failure that looks like success in testing.

So the document is indexed and queried by meaning, like any other corpus. The
collection is created per run under a unique name and dropped when the run
ends; nothing persists, and two runs never share one.

What is indexed is the text the Loader has already reduced to normative
sections — no contents listing, no annexes. That matters more here than
anywhere else in the pipeline: a 3GPP specification closes with the finished
OpenAPI documents, and a similarity search for a service name would rank that
annex's `title:` line first of all. The generator would then retrieve its own
target and report it as a reading of the specification.
"""

import uuid
from typing import Any, Dict, List

from openapi_generator.config import get_logger
from openapi_generator.config.settings import (
    EMBEDDING_DIM,
    SPEC_CHUNK_OVERLAP,
    SPEC_CHUNK_SIZE,
    SPEC_COLLECTION_PREFIX,
    SPEC_RETRIEVE_CHUNKS,
)
from openapi_generator.rag.qdrant_factory import _get_embeddings, _get_qdrant_client

logger = get_logger(__name__)


def _store(collection_name: str) -> Any:
    """A vector store bound to one of this run's collections.

    Built here rather than through `get_vector_store` because that one also
    declares a sparse vector, which the permanent collections happen to
    tolerate. These collections are created a few lines above with a dense
    vector only, so they are opened the same way.
    """
    from langchain_qdrant import QdrantVectorStore

    return QdrantVectorStore(
        client=_get_qdrant_client(),
        collection_name=collection_name,
        embedding=_get_embeddings(),
        vector_name="text-dense",
    )


def index_spec_sections(sections: List[Dict[str, Any]]) -> str:
    """Index parsed sections into a fresh collection. Returns its name.

    Returns "" when Qdrant is unreachable or nothing could be indexed, which
    callers treat as "no spec RAG" — the same degradation the permanent
    collections already have.
    """
    if not sections:
        return ""

    from langchain_core.documents import Document
    from langchain_text_splitters import RecursiveCharacterTextSplitter
    from qdrant_client.models import Distance, VectorParams

    collection_name = f"{SPEC_COLLECTION_PREFIX}{uuid.uuid4().hex[:12]}"
    try:
        client = _get_qdrant_client()
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
        client.create_collection(
            collection_name=collection_name,
            vectors_config={
                "text-dense": VectorParams(
                    size=EMBEDDING_DIM, distance=Distance.COSINE
                )
            },
        )

        # The title travels with the body of its own chunk. A clause names the
        # service in its heading as often as in its text, and a chunk carrying
        # only the text loses that.
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=SPEC_CHUNK_SIZE, chunk_overlap=SPEC_CHUNK_OVERLAP
        )
        documents: List[Any] = []
        for section in sections:
            title = (section.get("title") or "").strip()
            content = (section.get("content") or "").strip()
            if not content:
                continue
            for chunk in splitter.split_text(content):
                documents.append(
                    Document(
                        page_content=f"{title}\n\n{chunk}" if title else chunk,
                        metadata={
                            "section_id": section.get("section_id", ""),
                            "title": title,
                        },
                    )
                )

        if not documents:
            client.delete_collection(collection_name)
            return ""

        _store(collection_name).add_documents(documents)

        logger.info(
            f"Indexed {len(documents)} chunk(s) from {len(sections)} section(s) "
            f"into '{collection_name}'"
        )
        return collection_name
    except Exception as e:
        logger.warning(
            f"Spec indexing failed ({type(e).__name__}: {e}) — "
            "the run continues without spec RAG."
        )
        return ""


def search_spec(
    query: str, collection_name: str, k: int = SPEC_RETRIEVE_CHUNKS
) -> str:
    """Passages of this specification most relevant to `query`.

    Returns them joined for prompt injection, or "" when there is no
    collection to search — a caller then works with what it already has
    rather than failing.
    """
    if not collection_name or not query.strip():
        return ""
    try:
        client = _get_qdrant_client()
        if not client.collection_exists(collection_name):
            logger.warning(
                f"Spec collection '{collection_name}' is gone — skipping spec RAG."
            )
            return ""
        docs = _store(collection_name).similarity_search(query=query, k=k)
        return "\n\n---\n\n".join(d.page_content for d in docs)
    except Exception as e:
        logger.warning(f"Spec search failed ({type(e).__name__}: {e})")
        return ""


def drop_spec_collection(collection_name: str) -> None:
    """Remove the run's collection. Never raises: cleanup must not fail a run."""
    if not collection_name:
        return
    try:
        client = _get_qdrant_client()
        if client.collection_exists(collection_name):
            client.delete_collection(collection_name)
            logger.info(f"Dropped spec collection '{collection_name}'")
    except Exception as e:
        logger.warning(f"Could not drop '{collection_name}' ({type(e).__name__}: {e})")
