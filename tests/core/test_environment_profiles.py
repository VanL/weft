"""Tests for runner environment-profile loading and materialization."""

from __future__ import annotations

import pytest

from weft.core.environment_profiles import (
    load_runner_environment_profile,
    materialize_runner_environment,
)


def test_load_runner_environment_profile_returns_callable() -> None:
    profile = load_runner_environment_profile(
        "tests.fixtures.runtime_profiles_fixture:host_environment_profile"
    )

    assert profile is not None


def test_materialize_runner_environment_merges_profile_defaults() -> None:
    materialized = materialize_runner_environment(
        target_type="command",
        runner_name="host",
        runner_options={},
        env={},
        working_dir=None,
        environment_profile_ref=(
            "tests.fixtures.runtime_profiles_fixture:host_environment_profile"
        ),
        tid="123",
    )

    assert materialized.env["WEFT_ENV_PROFILE"] == "host-default"
    assert materialized.working_dir is None
    assert materialized.profile_metadata["profile"] == "host"


def test_materialize_runner_environment_explicit_env_wins() -> None:
    materialized = materialize_runner_environment(
        target_type="command",
        runner_name="host",
        runner_options={},
        env={"WEFT_ENV_PROFILE": "explicit"},
        working_dir=None,
        environment_profile_ref=(
            "tests.fixtures.runtime_profiles_fixture:host_environment_profile"
        ),
        tid="123",
    )

    assert materialized.env["WEFT_ENV_PROFILE"] == "explicit"


def test_materialize_runner_environment_explicit_working_dir_wins() -> None:
    materialized = materialize_runner_environment(
        target_type="command",
        runner_name="docker",
        runner_options={},
        env={},
        working_dir="/tmp/explicit",
        environment_profile_ref=(
            "tests.fixtures.runtime_profiles_fixture:docker_image_environment_profile"
        ),
        tid="123",
    )

    assert materialized.working_dir == "/tmp/explicit"


def test_materialize_runner_environment_can_supply_docker_build_defaults(
    tmp_path,
) -> None:
    dockerfile = tmp_path / "Dockerfile"
    dockerfile.write_text("FROM busybox\n", encoding="utf-8")

    materialized = materialize_runner_environment(
        target_type="command",
        runner_name="docker",
        runner_options={},
        env={},
        working_dir=str(tmp_path),
        environment_profile_ref=(
            "tests.fixtures.runtime_profiles_fixture:docker_build_environment_profile"
        ),
        tid="123",
    )

    assert materialized.runner_options["build"]["context"] == str(tmp_path)
    assert materialized.runner_options["build"]["dockerfile"] == str(dockerfile)


def test_materialize_runner_environment_rejects_wrong_runner_profile() -> None:
    with pytest.raises(ValueError, match="requires runner_name='docker'"):
        materialize_runner_environment(
            target_type="command",
            runner_name="host",
            runner_options={},
            env={},
            working_dir=None,
            environment_profile_ref=(
                "tests.fixtures.runtime_profiles_fixture:docker_image_environment_profile"
            ),
            tid="123",
        )


def test_materialize_runner_environment_rejects_invalid_result() -> None:
    with pytest.raises(
        TypeError,
        match="Runner environment profile must return RunnerEnvironmentProfileResult",
    ):
        materialize_runner_environment(
            target_type="command",
            runner_name="host",
            runner_options={},
            env={},
            working_dir=None,
            environment_profile_ref=(
                "tests.fixtures.runtime_profiles_fixture:invalid_environment_profile"
            ),
            tid="123",
        )


def test_materialize_runner_environment_loads_bundle_local_profile(
    tmp_path,
) -> None:
    helper = tmp_path / "helper_module.py"
    helper.write_text(
        "\n".join(
            [
                "from weft.ext import RunnerEnvironmentProfileResult",
                "",
                "def bundle_environment_profile(**kwargs):",
                "    del kwargs",
                "    return RunnerEnvironmentProfileResult(",
                "        env={'WEFT_ENV_PROFILE': 'bundle-local'},",
                "        metadata={'profile': 'bundle-local'},",
                "    )",
                "",
            ]
        ),
        encoding="utf-8",
    )

    materialized = materialize_runner_environment(
        target_type="command",
        runner_name="host",
        runner_options={},
        env={},
        working_dir=None,
        environment_profile_ref="helper_module:bundle_environment_profile",
        bundle_root=str(tmp_path),
        tid="123",
    )

    assert materialized.env["WEFT_ENV_PROFILE"] == "bundle-local"
    assert materialized.profile_metadata["profile"] == "bundle-local"
