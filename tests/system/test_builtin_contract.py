"""Contract tests for builtin TaskSpecs.

Spec references:
- docs/specifications/10B-Builtin_TaskSpecs.md
- docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.4]
"""

from __future__ import annotations

import json
from importlib import resources

import pytest

from tests.conftest import REPO_ROOT
from weft.builtins import builtin_task_names, builtin_task_specs, builtin_tasks_dir

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
