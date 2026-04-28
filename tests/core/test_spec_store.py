"""Tests for stored spec JSON loading."""

from __future__ import annotations

import json

import pytest

from weft.core.spec_store import read_spec_json

pytestmark = [pytest.mark.shared]


def test_read_spec_json_chains_missing_file_error(tmp_path) -> None:
    missing = tmp_path / "missing.json"

    with pytest.raises(ValueError) as exc_info:
        read_spec_json(missing)

    assert isinstance(exc_info.value.__cause__, FileNotFoundError)


def test_read_spec_json_chains_malformed_json_error(tmp_path) -> None:
    path = tmp_path / "broken.json"
    path.write_text("{", encoding="utf-8")

    with pytest.raises(ValueError) as exc_info:
        read_spec_json(path)

    assert isinstance(exc_info.value.__cause__, json.JSONDecodeError)
