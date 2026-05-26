"""
LLM provider — lazy default with override support.

Two consumption patterns:
  1. Default — get_llm() returns a ChatOpenAI built from settings, cached so
     subsequent calls reuse the same client.
  2. Override — build_openapi_gen_graph(llm=...) passes a pre-built client
     into the node closures; get_llm() is never called.
"""

from functools import lru_cache
from typing import Any

from openapi_generator.config import get_logger
from openapi_generator.config.settings import MODEL, OPENAI_API_KEY, TEMPERATURE

logger = get_logger(__name__)


@lru_cache(maxsize=1)
def get_llm() -> Any:
    """Return the default ChatOpenAI client, built on first call and reused after."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Define it in .env or pass an LLM "
            "explicitly via build_openapi_gen_graph(llm=...)."
        )

    from langchain_openai import ChatOpenAI

    logger.info(f"Default LLM ready: model={MODEL} temperature={TEMPERATURE}")
    return ChatOpenAI(
        model=MODEL,
        temperature=TEMPERATURE,
        api_key=OPENAI_API_KEY,
    )
