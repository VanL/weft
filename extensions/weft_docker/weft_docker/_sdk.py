"""Shared Docker SDK helpers for the Weft Docker extension.

Spec references:
- docs/specifications/02-TaskSpec.md [TS-1.3]
- docs/specifications/13-Agent_Runtime.md [AR-7]
"""

from __future__ import annotations

import time
from collections.abc import Iterator, Mapping
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


def wait_for_container_runtime_start(
    container: Any | None,
    *,
    process: Any | None = None,
    timeout: float,
    interval: float,
) -> None:
    """Wait until a Docker container leaves the transient ``created`` state."""

    if container is None:
        return

    deadline = time.monotonic() + timeout
    while True:
        try:
            container.reload()
        except Exception:  # pragma: no cover - defensive Docker API guard
            return

        if _container_status(container) != "created":
            return

        poll = getattr(process, "poll", None)
        if callable(poll) and poll() is not None:
            return
        if time.monotonic() >= deadline:
            return
        time.sleep(interval)


def _container_status(container: Any) -> str | None:
    attrs = getattr(container, "attrs", None)
    if not isinstance(attrs, Mapping):
        return None
    state = attrs.get("State")
    if not isinstance(state, Mapping):
        return None
    status = state.get("Status")
    if isinstance(status, str) and status:
        return status
    return None
