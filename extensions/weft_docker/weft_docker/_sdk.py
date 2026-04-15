"""Shared Docker SDK helpers for the Weft Docker extension.

Spec references:
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any


def load_docker_sdk() -> Any:
    """Import the Docker SDK and raise a stable install hint on failure."""

    try:
        import docker  # type: ignore[import-untyped]
    except ImportError as exc:  # pragma: no cover - import guard
        raise RuntimeError(
            "Docker runner requires the Docker SDK for Python. Install weft[docker]."
        ) from exc
    return docker


def docker_client_from_env(*, timeout: int = 10) -> Any:
    """Return a Docker SDK client configured from the ambient environment."""

    docker = load_docker_sdk()
    return docker.from_env(version="auto", timeout=timeout)


@contextmanager
def docker_client(*, timeout: int = 10) -> Iterator[Any]:
    """Yield a Docker SDK client and close it afterwards."""

    client = docker_client_from_env(timeout=timeout)
    try:
        yield client
    finally:
        client.close()
