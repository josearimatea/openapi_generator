"""
Hardware detection for embeddings. torch is an optional dep (only needed when
RAG is actually used), so the import is guarded.
"""

from openapi_generator.config import get_logger

logger = get_logger(__name__)

_device_cache: str | None = None


def get_device() -> str:
    """Return 'cuda' if a GPU is available, else 'cpu'. 'cpu' if torch missing."""
    global _device_cache
    if _device_cache is not None:
        return _device_cache

    try:
        import torch

        torch.cuda.empty_cache()
        _device_cache = "cuda" if torch.cuda.is_available() else "cpu"
    except ImportError:
        logger.warning("torch not installed — defaulting embedding device to 'cpu'")
        _device_cache = "cpu"

    logger.info(f"Embedding device: {_device_cache}")
    return _device_cache
