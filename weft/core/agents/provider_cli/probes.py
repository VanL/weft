"""Synthetic delegated-provider probes kept out of the startup validation path.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5], [AR-7]
"""

from __future__ import annotations

import subprocess
from functools import cache

from weft._constants import (
    PROVIDER_CLI_OPENCODE_RUN_PROBE_TIMEOUT_SECONDS,
    PROVIDER_CLI_VERSION_PROBE_TIMEOUT_SECONDS,
)


def probe_provider_cli_version(executable: str, *, provider_name: str) -> str:
    """Return the provider CLI version string or raise a compact runtime error."""
    try:
        completed = subprocess.run(
            [executable, "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROVIDER_CLI_VERSION_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError(
            f"Unable to execute provider CLI '{executable}': {exc}"
        ) from exc
    if completed.returncode != 0:
        detail = _compact_process_detail(completed)
        raise RuntimeError(
            f"Provider CLI probe failed for '{executable}' ({provider_name}): {detail}"
        )
    return (completed.stdout or completed.stderr or "").strip()


def ensure_opencode_run_support(executable: str) -> None:
    """Raise when the OpenCode CLI lacks non-interactive `run` support."""
    if _cached_opencode_run_support(executable):
        return
    raise RuntimeError(
        "opencode CLI does not support 'run'; install a version with "
        "non-interactive run support"
    )


@cache
def _cached_opencode_run_support(executable: str) -> bool:
    try:
        probe = subprocess.run(
            [executable, "run", "--help"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROVIDER_CLI_OPENCODE_RUN_PROBE_TIMEOUT_SECONDS,
            check=False,
        )
    except OSError:
        return False
    combined_output = f"{probe.stdout or ''}\n{probe.stderr or ''}".lower()
    return probe.returncode == 0 and (
        "opencode run [message" in combined_output
        or "run opencode with a message" in combined_output
    )


def _compact_process_detail(completed: subprocess.CompletedProcess[str]) -> str:
    parts: list[str] = []
    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()
    if stdout:
        parts.append(f"stdout={stdout[:200]}")
    if stderr:
        parts.append(f"stderr={stderr[:200]}")
    return "; ".join(parts)


__all__ = [
    "ensure_opencode_run_support",
    "probe_provider_cli_version",
]
