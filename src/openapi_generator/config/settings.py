"""
Settings — values pulled from .env, no side effects beyond load_dotenv().

The package is intended to be reusable: callers may override the LLM and the
retriever via build_openapi_gen_graph(llm=..., retriever=...). When nothing is
injected, these settings provide the defaults.

Filesystem paths live in `paths.py` and are re-exported here for convenience.
"""

import os
import time

from dotenv import load_dotenv

from .paths import *  # noqa: F401,F403 — re-export ROOT, DATA_DIR, TEST_*_PATH, etc.

load_dotenv()

# ── Run clock ─────────────────────────────────────────────────────
# Set when settings first load, which every run does before anything else, so
# how long a generation took needs no clock started or stopped by hand.
STARTED_AT = time.perf_counter()


def runtime_seconds() -> float:
    """Seconds since this run began."""
    return time.perf_counter() - STARTED_AT

# ── LLM ───────────────────────────────────────────────────────────
# Both providers speak the OpenAI chat-completions protocol, so the only
# difference is which key and base URL the client is built with. "openai"
# leaves the base URL unset and lets the SDK use its own default.
PROVIDER = os.getenv("OPENAPI_GEN_PROVIDER", "openai").strip().lower()

OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")
OPENROUTER_BASE_URL = os.getenv(
    "OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1"
)
# OpenRouter attributes usage to an app when these are sent; both optional.
OPENROUTER_SITE_URL = os.getenv("OPENROUTER_SITE_URL", "")
OPENROUTER_APP_NAME = os.getenv("OPENROUTER_APP_NAME", "openapi-generator")

MODEL = os.getenv("OPENAPI_GEN_MODEL", "gpt-4.1-mini")
TEMPERATURE = float(os.getenv("OPENAPI_GEN_TEMPERATURE", "0"))
# "none" and empty both mean OMIT the reasoning parameter, letting the model do
# whatever it does by default. That is not the same as switching reasoning off:
# openapi_rulesbank measured the disable flag producing worse answers than
# leaving the parameter out, on the same prompt at temperature 0.
REASONING_EFFORT = os.getenv("OPENAPI_GEN_REASONING_EFFORT", "none").strip().lower()

# A node that hangs stalls the whole run, and a plan has one call per operation.
LLM_REQUEST_TIMEOUT = int(os.getenv("OPENAPI_GEN_LLM_TIMEOUT", "60"))
LLM_MAX_RETRIES = int(os.getenv("OPENAPI_GEN_LLM_MAX_RETRIES", "2"))

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

# ── Generated document ────────────────────────────────────────────
# OpenAPI version stamped on a document built from scratch. The 3GPP TS 28.532
# specs all declare 3.0.1, so a generated document matches its reference by
# default; override when targeting a different release.
OPENAPI_VERSION = os.getenv("OPENAPI_GEN_OPENAPI_VERSION", "3.0.1")

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
