"""Synthetic delegated-provider probes kept out of the startup validation path.

Spec references:
- docs/specifications/13-Agent_Runtime.md [AR-5], [AR-7]
"""

from __future__ import annotations

import subprocess

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


def probe_opencode_run_help(executable: str) -> dict[str, object]:
    """Return process facts for an explicit OpenCode help diagnostic."""
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
    except subprocess.TimeoutExpired as exc:
        return {
            "attempted": True,
            "timed_out": True,
            "returncode": None,
            "detail": f"timed out after {exc.timeout} seconds",
        }
    except OSError as exc:
        return {
            "attempted": True,
            "timed_out": False,
            "returncode": None,
            "detail": f"execution failed: {exc}",
        }
    return {
        "attempted": True,
        "timed_out": False,
        "returncode": probe.returncode,
        "detail": _compact_process_detail(probe),
    }


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
    "probe_opencode_run_help",
    "probe_provider_cli_version",
]
