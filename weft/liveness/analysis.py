"""Authority-aware point-in-time liveness evidence reduction.

Spec references:
- docs/specifications/01-Core_Components.md [CC-3.2]
- docs/specifications/07-System_Invariants.md [LIVENESS.R2], [LIVENESS.R5]
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any

from weft._constants import LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS
from weft.ext import RunnerHandle

from .host import inspect_host_process
from .models import LivenessObservation, RuntimeLiveness
from .registry import attempt_runtime_liveness_from_registered_probe


def _handle_from_snapshot(snapshot: Mapping[str, Any]) -> RunnerHandle | None:
    payload = snapshot.get("runtime_handle")
    if not isinstance(payload, Mapping):
        return None
    try:
        return RunnerHandle.from_dict(payload)
    except (TypeError, ValueError):
        return None


def runtime_generation(snapshot: Mapping[str, Any]) -> str:
    """Return the canonical runtime-generation fingerprint for one mapping row."""

    handle = _handle_from_snapshot(snapshot)
    if handle is None:
        canonical: dict[str, Any] = {
            "runner": None,
            "kind": None,
            "id": None,
            "authority": None,
            "host_processes": [],
            "liveness_provider": None,
            "terminal": snapshot.get("terminal") is True,
        }
    else:
        provider = handle.observations.get("liveness_provider")
        canonical = {
            "runner": handle.runner,
            "kind": handle.kind,
            "id": handle.id,
            "authority": handle.control.get("authority"),
            "host_processes": [
                {"pid": pid, "create_time": create_time}
                for pid, create_time in sorted(handle.scoped_host_processes())
            ],
            "liveness_provider": (
                provider.strip()
                if isinstance(provider, str) and provider.strip()
                else None
            ),
            "terminal": snapshot.get("terminal") is True,
        }
    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _observation(
    tid: str,
    snapshot: Mapping[str, Any],
    evidence: RuntimeLiveness,
    reason: str,
    *,
    attempted: bool = True,
) -> LivenessObservation:
    return LivenessObservation(
        tid=tid,
        evidence=evidence,
        reason=reason,
        attempted=attempted,
        generation=runtime_generation(snapshot),
    )


def analyze_liveness(
    tid: str,
    snapshot: Mapping[str, Any],
    *,
    timeout_seconds: float = LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
) -> LivenessObservation:
    """Reduce one mapping snapshot into authority-aware liveness evidence."""

    terminal = snapshot.get("terminal") is True
    handle = _handle_from_snapshot(snapshot)
    if handle is None:
        return _observation(
            tid,
            snapshot,
            "stale" if terminal else "unknown",
            "terminal_hint" if terminal else "missing_or_invalid_runtime_handle",
        )

    authority = handle.control.get("authority")
    attempted = True
    if authority == "host-pid":
        identities = handle.scoped_host_processes()
        if not identities:
            evidence: RuntimeLiveness = "unknown"
            reason = "missing_exact_host_identity"
        else:
            observations = [
                inspect_host_process(pid, create_time, expected_tid=tid)
                for pid, create_time in identities
            ]
            if any(item.evidence == "live" for item in observations):
                evidence = "live"
                reason = "host_identity_live"
            elif any(item.evidence == "unknown" for item in observations):
                evidence = "unknown"
                reason = "host_identity_unresolved"
            else:
                evidence = "stale"
                reason = "host_identities_stale"
    elif authority in {"runner", "external-supervisor"}:
        probe_evidence = attempt_runtime_liveness_from_registered_probe(
            handle,
            timeout_seconds=timeout_seconds,
        )
        if probe_evidence is None:
            evidence = "unknown"
            reason = "extension_probe_failed"
            attempted = False
        else:
            evidence = probe_evidence
            reason = f"extension_{evidence}"
            attempted = True
    else:
        evidence = "unknown"
        reason = "unknown_control_authority"
        attempted = True

    if evidence != "live" and terminal:
        evidence = "stale"
        reason = "terminal_hint"
        attempted = True
    return _observation(tid, snapshot, evidence, reason, attempted=attempted)
