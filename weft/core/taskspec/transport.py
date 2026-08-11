"""TaskSpec transport validation and bundle provenance.

Spec: docs/specifications/02-TaskSpec.md [TS-1].
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from weft._constants import TASKSPEC_BUNDLE_ROOT_FIELD

from .model import TaskSpec


def validate_taskspec_payload(
    payload: Mapping[str, Any],
    *,
    bundle_root: str | Path | None = None,
    template: bool = False,
    resolved_tid: str | None = None,
    inherited_weft_context: str | None = None,
) -> TaskSpec:
    """Strictly validate a TaskSpec payload with explicit bundle provenance."""
    context: dict[str, Any] = {}
    if template:
        context.update(template=True, auto_expand=False)
    if resolved_tid is not None:
        context["resolved_tid"] = resolved_tid
    if inherited_weft_context is not None:
        context["inherited_weft_context"] = inherited_weft_context

    taskspec = TaskSpec.model_validate(copy.deepcopy(dict(payload)), context=context)
    taskspec.set_bundle_root(bundle_root)
    return taskspec


def decode_taskspec_transport_payload(
    payload: Mapping[str, Any],
    *,
    template: bool = False,
    resolved_tid: str | None = None,
    inherited_weft_context: str | None = None,
) -> TaskSpec:
    """Decode a TaskSpec mapping from a queue or process boundary."""
    candidate = copy.deepcopy(dict(payload))
    bundle_root = candidate.pop(TASKSPEC_BUNDLE_ROOT_FIELD, None)
    return validate_taskspec_payload(
        candidate,
        bundle_root=bundle_root,
        template=template,
        resolved_tid=resolved_tid,
        inherited_weft_context=inherited_weft_context,
    )


def encode_taskspec_transport_payload(taskspec: TaskSpec) -> dict[str, Any]:
    """Encode a TaskSpec for a queue or process boundary."""
    payload = taskspec.model_dump(mode="json")
    bundle_root = taskspec.get_bundle_root()
    if bundle_root is not None:
        payload[TASKSPEC_BUNDLE_ROOT_FIELD] = bundle_root
    return payload


__all__ = [
    "decode_taskspec_transport_payload",
    "encode_taskspec_transport_payload",
    "validate_taskspec_payload",
]
