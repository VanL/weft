"""TaskSpec transport provenance tests.

Spec: docs/specifications/02-TaskSpec.md [TS-1].
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from weft._constants import TASKSPEC_BUNDLE_ROOT_FIELD
from weft.core.taskspec import TaskSpec
from weft.core.taskspec.transport import (
    decode_taskspec_transport_payload,
    encode_taskspec_transport_payload,
    validate_taskspec_payload,
)

pytestmark = [pytest.mark.shared]


def _template_payload() -> dict[str, object]:
    return {
        "name": "bundle-task",
        "spec": {
            "type": "function",
            "function_target": "bundle_module:run",
        },
    }


def test_transport_round_trip_keeps_bundle_root_private(tmp_path: Path) -> None:
    bundle_root = tmp_path / "bundle"
    bundle_root.mkdir()
    taskspec = validate_taskspec_payload(
        _template_payload(),
        bundle_root=bundle_root,
        template=True,
    )

    assert taskspec.get_bundle_root() == str(bundle_root.resolve())
    assert TASKSPEC_BUNDLE_ROOT_FIELD not in taskspec.model_dump(mode="json")

    encoded = encode_taskspec_transport_payload(taskspec)
    decoded = decode_taskspec_transport_payload(encoded, template=True)

    assert encoded[TASKSPEC_BUNDLE_ROOT_FIELD] == str(bundle_root.resolve())
    assert decoded.get_bundle_root() == str(bundle_root.resolve())
    assert decoded.model_dump(mode="json") == taskspec.model_dump(mode="json")
    assert encoded[TASKSPEC_BUNDLE_ROOT_FIELD] == str(bundle_root.resolve())


@pytest.mark.parametrize("bundle_root", ["", 1, False, []])
def test_transport_decoder_rejects_invalid_bundle_root(bundle_root: object) -> None:
    payload = _template_payload()
    payload[TASKSPEC_BUNDLE_ROOT_FIELD] = bundle_root

    with pytest.raises((TypeError, ValueError), match="bundle_root"):
        decode_taskspec_transport_payload(payload, template=True)


def test_model_rejects_transport_field_without_decoder(tmp_path: Path) -> None:
    payload = _template_payload()
    payload[TASKSPEC_BUNDLE_ROOT_FIELD] = str(tmp_path)

    with pytest.raises(ValidationError, match=TASKSPEC_BUNDLE_ROOT_FIELD):
        TaskSpec.model_validate(
            payload,
            context={"template": True, "auto_expand": False},
        )


@pytest.mark.parametrize("bundle_root", [False, 1, 1.5, [], {}])
def test_taskspec_bundle_root_setter_rejects_non_path_types(
    bundle_root: object,
) -> None:
    taskspec = validate_taskspec_payload(_template_payload(), template=True)

    with pytest.raises(TypeError, match="bundle_root"):
        taskspec.set_bundle_root(bundle_root)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("bundle_root", "error_type", "message"),
    [
        (False, TypeError, "bundle_root must be a string path, Path, or None"),
        ("  ", ValueError, "bundle_root must not be empty"),
    ],
)
def test_bundle_root_guards_share_exact_errors(
    bundle_root: object,
    error_type: type[Exception],
    message: str,
) -> None:
    taskspec = validate_taskspec_payload(_template_payload(), template=True)
    with pytest.raises(error_type) as setter_error:
        taskspec.set_bundle_root(bundle_root)  # type: ignore[arg-type]

    payload = _template_payload()
    payload[TASKSPEC_BUNDLE_ROOT_FIELD] = bundle_root
    with pytest.raises(error_type) as transport_error:
        decode_taskspec_transport_payload(payload, template=True)

    assert str(setter_error.value) == message
    assert str(transport_error.value) == message
