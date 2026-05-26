"""
RAG package — optional retrieval from a Qdrant collection.

If Qdrant is unreachable or the configured collection does not exist, the
retriever degrades gracefully: it returns an empty list and the calling node
proceeds without RAG context.
"""
