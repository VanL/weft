"""Property-based tests for configuration parser helpers."""

from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from weft._constants import (
    _parse_bool,
    _parse_positive_float,
    _parse_positive_int,
    _parse_weft_directory_name,
)

pytestmark = [pytest.mark.shared, pytest.mark.property]


_TEXT = st.text(
    alphabet=st.characters(
        blacklist_categories=("Cs",),
        blacklist_characters=("\x00",),
    ),
    max_size=32,
)


@given(value=st.one_of(st.none(), _TEXT))
def test_parse_bool_matches_false_token_contract(value: str | None) -> None:
    false_values = {"0", "F", "NONE", "NULL", "FALSE"}

    expected = bool(value) and value.upper() not in false_values

    assert _parse_bool(value) is expected


@given(value=st.integers(min_value=1, max_value=1_000_000))
def test_parse_positive_int_accepts_positive_decimal_strings(value: int) -> None:
    assert _parse_positive_int(str(value), name="TEST_INT") == value


@given(value=st.integers(min_value=-1_000_000, max_value=0))
def test_parse_positive_int_rejects_non_positive_decimal_strings(value: int) -> None:
    with pytest.raises(ValueError, match="TEST_INT"):
        _parse_positive_int(str(value), name="TEST_INT")


@given(
    value=st.floats(
        min_value=1e-9,
        max_value=1_000_000.0,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_parse_positive_float_accepts_positive_finite_strings(value: float) -> None:
    assert _parse_positive_float(str(value), name="TEST_FLOAT") == float(str(value))


@given(
    value=st.floats(
        max_value=0.0,
        allow_nan=False,
        allow_infinity=False,
    )
)
def test_parse_positive_float_rejects_non_positive_finite_strings(
    value: float,
) -> None:
    with pytest.raises(ValueError, match="TEST_FLOAT"):
        _parse_positive_float(str(value), name="TEST_FLOAT")


@given(
    name=st.text(
        alphabet=st.characters(
            categories=("Ll", "Lu", "Nd"),
            include_characters="-_.",
        ),
        min_size=1,
        max_size=32,
    ).filter(lambda value: value not in {".", ".."})
)
def test_parse_weft_directory_name_accepts_single_directory_names(name: str) -> None:
    assert _parse_weft_directory_name(f" {name} ") == name


@given(
    name=st.one_of(
        st.just(""),
        st.just("   "),
        st.just("."),
        st.just(".."),
        st.text(min_size=0, max_size=12).map(lambda value: f"{value}/child"),
        st.text(min_size=0, max_size=12).map(lambda value: f"{value}\\child"),
    )
)
def test_parse_weft_directory_name_rejects_empty_dot_and_paths(name: str) -> None:
    with pytest.raises(ValueError, match="WEFT_DIRECTORY_NAME"):
        _parse_weft_directory_name(name)
