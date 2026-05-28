"""Tests for Docker container profile materialization."""

from __future__ import annotations

from pathlib import Path

import pytest
from weft_docker.profiles import materialize_container_profile

pytestmark = [pytest.mark.shared]


def _write_profile(path: Path, content: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content.strip() + "\n", encoding="utf-8")
    return path


def test_container_profile_materializes_defaults_paths_and_env_precedence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    profile_file = project_root / ".weft" / "docker-profiles.toml"
    env_file = project_root / ".env"
    env_file.parent.mkdir(parents=True)
    env_file.write_text("SERVICE_URL=ignored\n", encoding="utf-8")
    config_dir = project_root / "config"
    config_dir.mkdir()
    profile = _write_profile(
        profile_file,
        """
        version = 1
        root = ".."

        [profiles.ops]
        image = "profile-image:latest"
        network = "project_ops"
        mount_workdir = false
        container_workdir = "/app/project"
        env_from_host = ["OPTIONAL_TOKEN"]
        required_env_from_host = ["REQUIRED_TOKEN"]

        [profiles.ops.env]
        SERVICE_URL = "https://internal-service:8443"
        REQUIRED_TOKEN = "profile-default"

        [[profiles.ops.mounts]]
        source = ".env"
        target = "/app/project/.env"
        read_only = true

        [[profiles.ops.mounts]]
        source = "config"
        target = "/app/project/config"
        read_only = false
        """,
    )
    monkeypatch.setenv("OPTIONAL_TOKEN", "from-host")

    materialized = materialize_container_profile(
        runner_options={
            "container_profile": "ops",
            "container_profile_file": str(profile),
            "image": "explicit-image:latest",
        },
        env={
            "SERVICE_URL": "https://explicit.example.test",
            "REQUIRED_TOKEN": "from-task",
        },
        bundle_root=None,
    )

    assert materialized.runner_options["image"] == "explicit-image:latest"
    assert materialized.runner_options["network"] == "project_ops"
    assert materialized.runner_options["mount_workdir"] is False
    assert materialized.runner_options["container_workdir"] == "/app/project"
    assert "container_profile" not in materialized.runner_options
    assert "container_profile_file" not in materialized.runner_options
    assert materialized.env == {
        "SERVICE_URL": "https://explicit.example.test",
        "OPTIONAL_TOKEN": "from-host",
        "REQUIRED_TOKEN": "from-task",
    }
    assert materialized.runner_options["mounts"] == [
        {
            "source": str(env_file.resolve()),
            "target": "/app/project/.env",
            "read_only": True,
        },
        {
            "source": str(config_dir.resolve()),
            "target": "/app/project/config",
            "read_only": False,
        },
    ]


def test_container_profile_absent_preserves_options_and_env() -> None:
    materialized = materialize_container_profile(
        runner_options={
            "image": "busybox:latest",
            "container_profile_file": ".weft/ignored.toml",
        },
        env={"SERVICE_URL": "https://explicit.example.test"},
        bundle_root=None,
        host_env={},
    )

    assert materialized.runner_options == {"image": "busybox:latest"}
    assert materialized.env == {"SERVICE_URL": "https://explicit.example.test"}
    assert materialized.profile_name is None


def test_container_profile_missing_file_reports_clear_error(tmp_path: Path) -> None:
    missing = tmp_path / ".weft" / "missing.toml"

    with pytest.raises(ValueError, match="Docker profile file does not exist"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(missing),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_malformed_toml_reports_clear_error(tmp_path: Path) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops
        image = "busybox:latest"
        """,
    )

    with pytest.raises(ValueError, match="not valid TOML"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_missing_profile_reports_clear_error(tmp_path: Path) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.other]
        image = "busybox:latest"
        """,
    )

    with pytest.raises(ValueError, match="profile 'ops'.*not found"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_rejects_invalid_version(tmp_path: Path) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        version = 2

        [profiles.ops]
        image = "busybox:latest"
        """,
    )

    with pytest.raises(ValueError, match="version must be 1"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_required_env_may_be_supplied_by_taskspec_env(
    tmp_path: Path,
) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        required_env_from_host = ["REQUIRED_TOKEN"]
        """,
    )

    materialized = materialize_container_profile(
        runner_options={
            "container_profile": "ops",
            "container_profile_file": str(profile),
        },
        env={"REQUIRED_TOKEN": "from-task"},
        bundle_root=None,
        host_env={},
    )

    assert materialized.env["REQUIRED_TOKEN"] == "from-task"


def test_container_profile_required_env_fails_when_missing_from_host_and_task(
    tmp_path: Path,
) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        required_env_from_host = ["REQUIRED_TOKEN"]
        """,
    )

    with pytest.raises(ValueError, match="REQUIRED_TOKEN"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_file_relative_path_uses_bundle_root(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    profile = _write_profile(
        bundle_root / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        """,
    )

    materialized = materialize_container_profile(
        runner_options={"container_profile": "ops"},
        env={},
        bundle_root=str(bundle_root),
        host_env={},
    )

    assert materialized.profile_file == profile.resolve()
    assert materialized.profile_root == profile.parent.resolve()


def test_container_profile_explicit_root_resolves_profile_sourced_paths(
    tmp_path: Path,
) -> None:
    bundle_root = tmp_path / "bundle"
    project_root = tmp_path / "project"
    config_dir = project_root / "config"
    dockerfile = project_root / "Dockerfile"
    config_dir.mkdir(parents=True)
    dockerfile.write_text("FROM busybox\n", encoding="utf-8")
    _write_profile(
        bundle_root / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        build = { context = ".", dockerfile = "Dockerfile" }

        [[profiles.ops.mounts]]
        source = "config"
        target = "/app/config"
        """,
    )

    materialized = materialize_container_profile(
        runner_options={
            "container_profile": "ops",
            "container_profile_root": "../project",
        },
        env={},
        bundle_root=str(bundle_root),
        host_env={},
    )

    assert materialized.runner_options["build"] == {
        "context": str(project_root.resolve()),
        "dockerfile": str(dockerfile.resolve()),
    }
    assert materialized.runner_options["mounts"][0]["source"] == str(
        config_dir.resolve()
    )


def test_container_profile_rejects_env_forwarding_inside_env_table(
    tmp_path: Path,
) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"

        [profiles.ops.env]
        env_from_host = ["OPTIONAL_TOKEN"]
        """,
    )

    with pytest.raises(ValueError, match="profiles.ops.env"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_rejects_unsupported_profile_keys(
    tmp_path: Path,
) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"
        ports = ["8080:8080"]
        """,
    )

    with pytest.raises(ValueError, match="unsupported Docker profile keys"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
        )


def test_container_profile_preflight_checks_profile_sourced_mount_paths(
    tmp_path: Path,
) -> None:
    profile = _write_profile(
        tmp_path / ".weft" / "docker-profiles.toml",
        """
        [profiles.ops]
        image = "busybox:latest"

        [[profiles.ops.mounts]]
        source = "missing"
        target = "/app/missing"
        """,
    )

    with pytest.raises(ValueError, match="Docker profile mount source does not exist"):
        materialize_container_profile(
            runner_options={
                "container_profile": "ops",
                "container_profile_file": str(profile),
            },
            env={},
            bundle_root=None,
            host_env={},
            preflight=True,
        )
