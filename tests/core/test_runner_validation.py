"""Tests for runner TaskSpec payload validation boundaries."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

import pytest

from weft.core.environment_profiles import (
    materialize_runner_environment_from_taskspec,
)
from weft.core.runner_validation import runner_name_from_taskspec

pytestmark = [pytest.mark.shared]


@pytest.mark.parametrize(
    ("payload", "message"),
    [
        ({"spec": []}, "spec must be an object"),
        (
            {"spec": {"type": "command", "runner": {"name": 1}}},
            "spec.runner.name must be a string",
        ),
    ],
)
@pytest.mark.parametrize(
    "validator",
    [
        runner_name_from_taskspec,
        materialize_runner_environment_from_taskspec,
    ],
)
def test_runner_payload_shape_errors_are_value_errors(
    validator: Callable[[Mapping[str, Any]], object],
    payload: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError) as exc_info:
        validator(payload)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == message


def test_runner_environment_mapping_type_error_is_normalized_to_value_error() -> None:
    payload = {
        "spec": {
            "type": "command",
            "runner": {"name": "host"},
            "env": {"INVALID": 1},
        }
    }

    with pytest.raises(ValueError) as exc_info:
        materialize_runner_environment_from_taskspec(payload)
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == "spec.env must be a mapping of strings to strings"
