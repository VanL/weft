"""Contract tests for builtin TaskSpecs.

Spec references:
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.4]
"""

from __future__ import annotations

import json
from importlib import resources
from pathlib import Path

import pytest

from tests.conftest import REPO_ROOT
from weft import builtins as builtins_module
from weft.builtins import (
    builtin_task_catalog,
    builtin_task_names,
    builtin_task_specs,
    builtin_tasks_dir,
)

pytestmark = pytest.mark.shared


def test_builtin_task_assets_are_packaged_and_discoverable() -> None:
    paths = builtin_task_specs()

    assert paths
    assert builtin_task_names() == tuple(
        path.parent.name if path.name == "taskspec.json" else path.stem
        for path in paths
    )

    builtin_root = resources.files("weft.builtins").joinpath("tasks")
    builtin_root_path = builtin_tasks_dir()
    for path in paths:
        packaged = builtin_root.joinpath(path.relative_to(builtin_root_path).as_posix())
        assert packaged.is_file()

        payload = json.loads(packaged.read_text(encoding="utf-8"))
        expected_name = path.parent.name if path.name == "taskspec.json" else path.stem
        assert payload["name"] == expected_name
        assert payload["spec"]["type"]


def test_builtin_taskspec_doc_has_section_for_each_builtin() -> None:
    doc_path = REPO_ROOT / "docs" / "specifications" / "10B-Builtin_TaskSpecs.md"
    doc = doc_path.read_text(encoding="utf-8")

    for name in builtin_task_names():
        assert f"### `{name}`" in doc


@pytest.mark.parametrize(
    ("payload", "message_suffix"),
    [
        ([], "must contain a JSON object"),
        ({"spec": []}, "is missing a spec object"),
        (
            {"spec": {}, "metadata": {"supported_platforms": 1}},
            "supported_platforms must be a list of strings",
        ),
    ],
)
def test_builtin_catalog_rejects_malformed_packaged_values_as_value_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    payload: object,
    message_suffix: str,
) -> None:
    path = tmp_path / "invalid-builtin.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(builtins_module, "builtin_task_specs", lambda: (path,))

    with pytest.raises(ValueError) as exc_info:
        builtin_task_catalog()
    assert type(exc_info.value) is ValueError
    assert str(exc_info.value) == f"Builtin TaskSpec {path} {message_suffix}"
    assert exc_info.value.__cause__ is None
