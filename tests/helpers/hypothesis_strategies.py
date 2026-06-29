"""Small Hypothesis strategies shared by property tests."""

from __future__ import annotations

from hypothesis import strategies as st
from hypothesis.strategies import SearchStrategy

from weft._constants import STANDARD_TASK_QUEUE_SUFFIXES


def taskspec_tid_strings() -> SearchStrategy[str]:
    """Return valid 19-digit TaskSpec TID strings."""

    return st.integers(
        min_value=1_700_000_000_000_000_000,
        max_value=1_790_000_000_000_000_000,
    ).map(str)


def digit_tid_strings() -> SearchStrategy[str]:
    """Return non-empty digit strings for queue identity helpers."""

    return st.text(
        alphabet=st.characters(min_codepoint=ord("0"), max_codepoint=ord("9")),
        min_size=1,
        max_size=24,
    )


def invalid_taskspec_tid_strings() -> SearchStrategy[str]:
    """Return strings near the TaskSpec TID boundary but still invalid."""

    wrong_length_digits = st.one_of(
        st.integers(min_value=0, max_value=999_999_999_999_999_999).map(str),
        st.integers(
            min_value=10_000_000_000_000_000_000,
            max_value=99_999_999_999_999_999_999,
        ).map(str),
    )
    non_digits = st.text(
        alphabet=st.characters(
            blacklist_categories=("Cs",),
            blacklist_characters=("\x00",),
        ),
        min_size=1,
        max_size=24,
    ).filter(lambda value: not value.isdigit())
    return st.one_of(st.just(""), wrong_length_digits, non_digits)


def json_scalars() -> SearchStrategy[object]:
    """Return small JSON scalar values."""

    return st.one_of(
        st.none(),
        st.booleans(),
        st.integers(min_value=-1_000, max_value=1_000),
        st.floats(
            min_value=-1_000.0,
            max_value=1_000.0,
            allow_nan=False,
            allow_infinity=False,
        ),
        st.text(
            alphabet=st.characters(
                blacklist_categories=("Cs",),
                blacklist_characters=("\x00",),
            ),
            max_size=32,
        ),
    )


def json_values(max_leaves: int = 8) -> SearchStrategy[object]:
    """Return small JSON-like values that shrink to readable examples."""

    return st.recursive(
        json_scalars(),
        lambda children: st.one_of(
            st.lists(children, max_size=4),
            st.dictionaries(
                st.text(
                    alphabet=st.characters(
                        blacklist_categories=("Cs",),
                        blacklist_characters=("\x00",),
                    ),
                    min_size=1,
                    max_size=16,
                ),
                children,
                max_size=4,
            ),
        ),
        max_leaves=max_leaves,
    )


def queue_suffixes() -> SearchStrategy[str]:
    """Return canonical task-local queue suffixes."""

    return st.sampled_from(tuple(sorted(STANDARD_TASK_QUEUE_SUFFIXES)))
