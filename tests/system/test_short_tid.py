"""The shared short-form derivation boundary [OBS.5]."""

from __future__ import annotations

import os

import psutil
import pytest

from weft.helpers import tid_short_form
from weft.liveness.host import inspect_host_process

pytestmark = pytest.mark.shared


@pytest.mark.parametrize("tid", ["", "123", "undecidable", "x" * 19, "²" * 19])
def test_short_form_rejects_non_derivable_ids(tid: str) -> None:
    with pytest.raises(ValueError, match="19-digit"):
        tid_short_form(tid)


def test_shared_short_form_preserves_current_derivation() -> None:
    assert tid_short_form("1760000000123456789") == "0123456789"


def test_invalid_expected_tid_preserves_exact_process_evidence() -> None:
    process = psutil.Process(os.getpid())
    observation = inspect_host_process(
        process.pid, process.create_time(), expected_tid="invalid"
    )
    assert observation.evidence == "live"
    assert observation.reason == "identity_match_title_unconfirmed"
