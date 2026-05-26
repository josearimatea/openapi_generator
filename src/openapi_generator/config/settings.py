"""
Settings — values pulled from .env, no side effects beyond load_dotenv().

The package is intended to be reusable: callers may override the LLM and the
retriever via build_openapi_gen_graph(llm=..., retriever=...). When nothing is
injected, these settings provide the defaults.

Filesystem paths live in `paths.py` and are re-exported here for convenience.
"""

import os

from dotenv import load_dotenv

from .paths import *  # noqa: F401,F403 — re-export ROOT, DATA_DIR, TEST_*_PATH, etc.

load_dotenv()

# ── LLM ───────────────────────────────────────────────────────────
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
MODEL = os.getenv("OPENAPI_GEN_MODEL", "gpt-4.1-mini")
TEMPERATURE = float(os.getenv("OPENAPI_GEN_TEMPERATURE", "0"))

# ── Qdrant (RAG — optional) ───────────────────────────────────────
QDRANT_HOST = os.getenv("QDRANT_HOST", "localhost")
QDRANT_PORT = int(os.getenv("QDRANT_PORT", "6333"))
QDRANT_COLLECTION = os.getenv("QDRANT_COLLECTION", "3gpp_rel18_28")
OPENAPI_COLLECTION_NAME = os.getenv("OPENAPI_COLLECTION_NAME", "openapi_reference")

# ── Embeddings ────────────────────────────────────────────────────
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
EMBEDDING_DIM = 384

# ── Retrieval ─────────────────────────────────────────────────────
DEFAULT_RETRIEVE_K = 5
OPENAPI_REFERENCE_RETRIEVE_CHUNKS = 5
QDRANT_TIMEOUT = 30  # seconds — kept short so a down Qdrant fails fast

# ── Section parsing (utils.parsers.parse_sections) ────────────────
# When True, drop sections whose title contains no 3+ letter word
# (cover-page tables in 3GPP specs render as titles like '+---+---+').
FILTER_SYMBOLIC_TITLES = True
