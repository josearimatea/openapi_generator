"""
LLM provider — lazy default with override support.

Two consumption patterns:
  1. Default — get_llm() returns a ChatOpenAI built from settings, cached so
     subsequent calls reuse the same client.
  2. Override — build_openapi_gen_graph(llm=...) passes a pre-built client
     into the node closures; get_llm() is never called.

OpenAI and OpenRouter both speak the OpenAI chat-completions protocol, so a
single ChatOpenAI client serves both: OPENAPI_GEN_PROVIDER only decides which
key, base URL and headers it is built with.
"""

from functools import lru_cache
from typing import Any

from langchain_core.callbacks import BaseCallbackHandler
from langchain_core.outputs import LLMResult

from openapi_generator.config import get_logger
from openapi_generator.config.settings import (
    LLM_MAX_RETRIES,
    LLM_REQUEST_TIMEOUT,
    MODEL,
    OPENAI_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_APP_NAME,
    OPENROUTER_BASE_URL,
    OPENROUTER_SITE_URL,
    PROVIDER,
    REASONING_EFFORT,
    TEMPERATURE,
)

logger = get_logger(__name__)


class UsageTracker(BaseCallbackHandler):
    """Totals the tokens and money every LLM call in this process spends.

    A run calls the model from the Loader, the Planner, the Patcher, the
    Reflector and the Validator — and the Planner and Reflector call it once per
    group or per operation, so the count is not fixed. A per-call tally at each
    site would be repetitive and easy to forget in the next node added; a
    callback on the shared client sees them all.

    OpenRouter returns `cost` in US dollars inside token_usage, already summed
    over prompt and completion. OpenAI does not, and the total then stays 0.0
    while the token counts remain accurate. Cached prompt tokens are billed
    differently, so they are reported apart rather than folded into the total.
    """

    def __init__(self) -> None:
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.reasoning_tokens = 0
        self.cached_tokens = 0
        self.cost = 0.0

    def on_llm_end(self, response: LLMResult, **kwargs: Any) -> None:
        usage = (response.llm_output or {}).get("token_usage") or {}
        if not usage:
            # Structured-output calls report usage on the generation instead.
            for generation in (response.generations or []):
                for item in generation:
                    meta = getattr(
                        getattr(item, "message", None), "response_metadata", {}
                    ) or {}
                    usage = meta.get("token_usage") or {}
                    if usage:
                        break
                if usage:
                    break
        if not usage:
            return
        self.calls += 1
        self.prompt_tokens += usage.get("prompt_tokens", 0) or 0
        self.completion_tokens += usage.get("completion_tokens", 0) or 0
        self.cost += usage.get("cost", 0.0) or 0.0
        self.reasoning_tokens += (
            (usage.get("completion_tokens_details") or {}).get("reasoning_tokens", 0) or 0
        )
        self.cached_tokens += (
            (usage.get("prompt_tokens_details") or {}).get("cached_tokens", 0) or 0
        )

    def as_dict(self) -> dict[str, Any]:
        return {
            "llm_calls": self.calls,
            "prompt_tokens": self.prompt_tokens,
            "completion_tokens": self.completion_tokens,
            "total_tokens": self.prompt_tokens + self.completion_tokens,
            "reasoning_tokens": self.reasoning_tokens,
            "cached_prompt_tokens": self.cached_tokens,
            "cost_usd": round(self.cost, 6),
        }


# One tracker per process — the run is the process, and the Assembler reports
# the totals so far each time it writes the document.
usage_tracker = UsageTracker()


def _openrouter_kwargs() -> dict:
    """Key, base URL, headers and reasoning payload for the OpenRouter client."""
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Define it in .env, switch "
            "OPENAPI_GEN_PROVIDER to 'openai', or pass an LLM explicitly via "
            "build_openapi_gen_graph(llm=...)."
        )

    headers = {"X-Title": OPENROUTER_APP_NAME} if OPENROUTER_APP_NAME else {}
    if OPENROUTER_SITE_URL:
        headers["HTTP-Referer"] = OPENROUTER_SITE_URL

    kwargs: dict[str, Any] = {
        "api_key": OPENROUTER_API_KEY,
        "base_url": OPENROUTER_BASE_URL,
    }
    if headers:
        kwargs["default_headers"] = headers

    # OpenRouter takes reasoning as a body field rather than the OpenAI-style
    # top-level `reasoning_effort`. "none" means OMIT the parameter, exactly as
    # it does for OpenAI below — not {"enabled": False}.
    #
    # openapi_rulesbank measured the difference: sending the disable flag was
    # worse than leaving it out, on the same prompt and schema at temperature 0.
    # Omitting it lets the model reason as it would by default; the flag turns
    # that off, and the two are not the same thing.
    if REASONING_EFFORT and REASONING_EFFORT != "none":
        kwargs["extra_body"] = {"reasoning": {"effort": REASONING_EFFORT}}
    return kwargs


def _openai_kwargs() -> dict:
    """Key and reasoning parameter for the direct-OpenAI client."""
    if not OPENAI_API_KEY:
        raise RuntimeError(
            "OPENAI_API_KEY is not set. Define it in .env, switch "
            "OPENAPI_GEN_PROVIDER to 'openrouter', or pass an LLM explicitly "
            "via build_openapi_gen_graph(llm=...)."
        )

    kwargs: dict[str, Any] = {"api_key": OPENAI_API_KEY}
    # OpenAI has no "reasoning off" switch — the parameter is simply omitted for
    # models without a reasoning mode, which is also what "none" asks for here.
    if REASONING_EFFORT and REASONING_EFFORT != "none":
        kwargs["reasoning_effort"] = REASONING_EFFORT
    return kwargs


@lru_cache(maxsize=1)
def get_llm() -> Any:
    """Return the default ChatOpenAI client, built on first call and reused after."""
    if PROVIDER == "openrouter":
        provider_kwargs = _openrouter_kwargs()
    elif PROVIDER == "openai":
        provider_kwargs = _openai_kwargs()
    else:
        raise RuntimeError(
            f"Unknown OPENAPI_GEN_PROVIDER={PROVIDER!r}. Use 'openai' or 'openrouter'."
        )

    from langchain_openai import ChatOpenAI

    logger.info(
        f"Default LLM ready: provider={PROVIDER} model={MODEL} "
        f"temperature={TEMPERATURE}"
        + (f" reasoning_effort={REASONING_EFFORT}" if REASONING_EFFORT else "")
    )
    return ChatOpenAI(
        model=MODEL,
        temperature=TEMPERATURE,
        timeout=LLM_REQUEST_TIMEOUT,
        max_retries=LLM_MAX_RETRIES,
        # Attached here rather than at each call site: every node shares this
        # client, so the tally covers the whole run without a node having to
        # remember it.
        callbacks=[usage_tracker],
        **provider_kwargs,
    )
