"""Tests for public client and ops exceptions."""

from __future__ import annotations

import pytest

from weft._exceptions import (
    ControlRejected,
    InvalidTID,
    ManagerNotRunning,
    SpecNotFound,
    TaskNotFound,
    WeftError,
)
from weft.commands.submission import normalize_tid

pytestmark = [pytest.mark.shared]


def test_exception_hierarchy_is_stable() -> None:
    assert issubclass(InvalidTID, WeftError)
    assert issubclass(TaskNotFound, WeftError)
    assert issubclass(ControlRejected, WeftError)
    assert issubclass(SpecNotFound, WeftError)
    assert issubclass(ManagerNotRunning, WeftError)


def test_normalize_tid_raises_invalid_tid_for_bad_values() -> None:
    with pytest.raises(InvalidTID):
        normalize_tid("bad-tid")
