"""Exact message-ID boundary tests."""

from __future__ import annotations

import pytest

from weft.helpers.message_ids import normalize_exact_message_id

pytestmark = pytest.mark.shared


def test_normalize_exact_message_id_accepts_storage_maximum() -> None:
    assert normalize_exact_message_id("9223372036854775807") == 2**63 - 1


@pytest.mark.parametrize(
    "value",
    [
        "9223372036854775808",
        "11111111111111111111",
        "١١١١١١١١١١١١١١١١١١١",
        " 1000000000000000000",
        "100000000000000000",
        "100000000000000000x",
    ],
)
def test_normalize_exact_message_id_rejects_out_of_range_or_noncanonical_strings(
    value: str,
) -> None:
    with pytest.raises(ValueError):
        normalize_exact_message_id(value)
