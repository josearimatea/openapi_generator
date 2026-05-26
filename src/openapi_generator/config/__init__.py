"""
Lightweight config entry point.

Only exposes get_logger — no heavy deps (torch, langchain) at import time.
Import heavier pieces directly from their modules:
    from openapi_generator.config.settings import OPENAI_API_KEY, QDRANT_HOST, ...
    from openapi_generator.config.llm_config import get_llm
    from openapi_generator.config.hardware import get_device
"""

_logging_configured = False


def get_logger(name: str):
    """Configure logging globally on first call, return a named logger."""
    global _logging_configured
    if not _logging_configured:
        from . import logging_config  # noqa: F401 — runs basicConfig
        _logging_configured = True

    import logging
    return logging.getLogger(name)


__all__ = ["get_logger"]
