"""Tests for the GitHub Actions release workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.shared]


def test_github_release_uploads_only_python_distributions() -> None:
    """GitHub Release assets should be Python distributions, without sidecars."""

    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    # The release-asset job is found by its upload action, never by job id.
    asset_jobs = [
        job
        for job in workflow["jobs"].values()
        if any(
            str(step.get("uses", "")).startswith("softprops/action-gh-release@")
            for step in job.get("steps", [])
        )
    ]
    assert asset_jobs
    for job in asset_jobs:
        assert "id-token" not in job.get("permissions", {})
        for step in job["steps"]:
            if not str(step.get("uses", "")).startswith("softprops/action-gh-release@"):
                continue
            files = [
                line.strip()
                for line in str(step["with"]["files"]).splitlines()
                if line.strip()
            ]
            assert all(entry.endswith((".whl", ".tar.gz")) for entry in files)
            assert any(entry.endswith(".whl") for entry in files)
            assert any(entry.endswith(".tar.gz") for entry in files)
