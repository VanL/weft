"""Tests for the GitHub Actions release workflow."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.shared]


def test_github_release_uploads_only_python_distributions() -> None:
    """GitHub Release assets should exclude Sigstore sidecar files."""

    workflow_path = (
        Path(__file__).resolve().parents[2] / ".github" / "workflows" / "release.yml"
    )
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))

    github_release_job = workflow["jobs"]["github-release"]
    github_release_steps = github_release_job["steps"]
    upload_step = next(
        step
        for step in github_release_steps
        if step["name"] == "Create GitHub Release and upload artifacts"
    )

    files = upload_step["with"]["files"].splitlines()

    assert files == ["dist/*.tar.gz", "dist/*.whl"]
    assert "dist/*" not in files
    assert "id-token" not in github_release_job["permissions"]
    assert all("sigstore" not in step.get("uses", "") for step in github_release_steps)
