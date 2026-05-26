"""
Logging configuration — basicConfig applied once, lazily, via get_logger.
"""

import logging
import os

from dotenv import load_dotenv

load_dotenv()

log_level = os.getenv("LOG_LEVEL", "INFO").upper()
level = getattr(logging, log_level, logging.INFO)

logging.basicConfig(
    level=level,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[logging.StreamHandler()],
)

# Quiet noisy SDKs
for noisy in ("httpx", "httpcore", "openai", "openai._base_client"):
    logging.getLogger(noisy).setLevel(logging.WARNING)
