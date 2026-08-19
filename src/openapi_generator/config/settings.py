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
# Reasoning models reject function-calling tools on /v1/chat/completions unless
# reasoning is switched off, and every node here uses structured output via
# function calling. Empty means "omit the parameter", which is what models
# without a reasoning mode expect.
REASONING_EFFORT = os.getenv("OPENAPI_GEN_REASONING_EFFORT", "")

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
# The Patcher asks this collection for several OpenAPI constructs at once (see
# nodes.patcher._openapi_reference_query), so the budget is per-query rather
# than per-construct: 5 slots shared by 3+ constructs crowd each other out.
OPENAPI_REFERENCE_RETRIEVE_CHUNKS = int(
    os.getenv("OPENAPI_REFERENCE_RETRIEVE_CHUNKS", "10")
)
QDRANT_TIMEOUT = 30  # seconds — kept short so a down Qdrant fails fast

# ── Rule gating (nodes.patcher) ───────────────────────────────────
# A rules_bank rule the bank's Validator rejected is still force-included by its
# Builder at MAX_ITERATIONS, so the Patcher decides what to do with it from the
# Reflector's own assessment. When the Reflector flagged the rule AND scored it
# below this threshold, the rule is dropped; otherwise it reaches the prompt
# marked as disputed. A null score means "not scored" and never condemns a rule.
RULE_RESCUE_CONFIDENCE = float(os.getenv("OPENAPI_GEN_RULE_RESCUE_CONFIDENCE", "0.7"))

# ── Section parsing (utils.parsers.parse_sections) ────────────────
# When True, drop sections whose title contains no 3+ letter word
# (cover-page tables in 3GPP specs render as titles like '+---+---+').
FILTER_SYMBOLIC_TITLES = True
