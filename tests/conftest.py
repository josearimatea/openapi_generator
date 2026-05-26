"""
Shared pytest fixtures for all test suites.

qdrant fixture (session-scoped):
  Ensures Qdrant is reachable on QDRANT_HOST:QDRANT_PORT before RAG tests run.
  If a local 'qdrant-local' Docker container exists and Qdrant is not already
  up, the fixture starts it and stops it on teardown. If Qdrant is already
  running (managed externally), the fixture leaves it alone.

  Tests that don't need Qdrant should not request this fixture.
"""

import socket
import subprocess
import time

import pytest

from openapi_generator.config.settings import QDRANT_HOST, QDRANT_PORT

QDRANT_CONTAINER = "qdrant-local"
STARTUP_TIMEOUT = 30  # seconds to wait for Qdrant to accept connections


def _is_qdrant_up() -> bool:
    try:
        with socket.create_connection((QDRANT_HOST, QDRANT_PORT), timeout=2):
            return True
    except OSError:
        return False


def _try_docker_start() -> bool:
    """Try to start the qdrant-local container. Returns True on success."""
    try:
        subprocess.run(
            ["docker", "start", QDRANT_CONTAINER],
            check=True,
            capture_output=True,
        )
        return True
    except (subprocess.CalledProcessError, FileNotFoundError):
        return False


@pytest.fixture(scope="session")
def qdrant():
    """Yield once Qdrant is up on QDRANT_HOST:QDRANT_PORT. Skip if unreachable."""
    already_running = _is_qdrant_up()
    started_here = False

    if not already_running:
        if not _try_docker_start():
            pytest.skip(
                f"Qdrant unreachable at {QDRANT_HOST}:{QDRANT_PORT} and no "
                f"'{QDRANT_CONTAINER}' container available to start."
            )
        started_here = True

        deadline = time.time() + STARTUP_TIMEOUT
        while not _is_qdrant_up():
            if time.time() > deadline:
                pytest.skip(
                    f"Qdrant container '{QDRANT_CONTAINER}' did not become "
                    f"ready within {STARTUP_TIMEOUT}s."
                )
            time.sleep(1)

    yield

    if started_here:
        subprocess.run(
            ["docker", "stop", QDRANT_CONTAINER],
            check=False,
            capture_output=True,
        )
