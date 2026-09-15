"""Tests for the _constants module."""

from __future__ import annotations

import ast
import os
import re
from collections.abc import MutableMapping
from contextlib import nullcontext
from pathlib import Path
from typing import cast
from unittest.mock import patch

import pytest

import weft._constants as constants
from simplebroker import Config, resolve_config
from simplebroker.ext import InvalidConfigError
from weft._constants import (
    ADMISSION_SERVICE_RESERVE_SLOTS,
    COMMAND_SESSION_POST_TERMINATION_WAIT,
    COMMAND_SESSION_TERMINATION_TIMEOUT,
    CONTROL_PAUSE,
    CONTROL_RESUME,
    CONTROL_STOP,
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_FUNCTION_TARGET,
    DEFAULT_MEMORY_MB,  # RENAMED from DEFAULT_MEMORY_LIMIT
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_REPORTING_INTERVAL,
    DEFAULT_STATUS,
    DEFAULT_STREAM_OUTPUT,
    DEFAULT_TIMEOUT,
    EXIT_SUCCESS,
    FAILURE_LIKE_TASK_STATUSES,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERACTIVE_STOP_COMPLETION_TIMEOUT,
    INTERACTIVE_STOP_GRACE_SECONDS,
    LIVENESS_MONITOR_ENABLED_DEFAULT,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    MAX_CPU_LIMIT,
    MIN_CONNECTIONS_LIMIT,
    MIN_CPU_LIMIT,
    MIN_FDS_LIMIT,
    MIN_MEMORY_LIMIT,
    PROG_NAME,
    QUEUE_CTRL_IN_SUFFIX,
    QUEUE_CTRL_OUT_SUFFIX,
    QUEUE_INBOX_SUFFIX,
    QUEUE_OUTBOX_SUFFIX,
    STATUS_COMPLETED,
    STATUS_CREATED,
    TASKSPEC_TID_LENGTH,
    TASKSPEC_VERSION,
    TERMINAL_TASK_STATUSES,
    WEFT_ADMISSION_MAX_CONNECTIONS,
    WEFT_ADMISSION_RESERVE_FRACTION,
    WEFT_DIRECTORY_NAME_DEFAULT,
    WEFT_LOG_TASKS_EXTERNAL_PATH_DEFAULT,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
    WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_ENABLED_DEFAULT,
    WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_LOG_SINK_DEFAULT,
    WEFT_TASK_MONITOR_MODE_DEFAULT,
    WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT,
    __version__,
    load_config,
)


@pytest.mark.parametrize(
    ("name", "value", "expected"),
    [
        ("WEFT_DEBUG", "yes", True),
        ("WEFT_DEBUG", 0, False),
        ("WEFT_LOGGING_ENABLED", "false", False),
        ("WEFT_LOGGING_ENABLED", "off", True),
        ("WEFT_LOGGING_ENABLED", True, True),
        ("WEFT_LOGS_DIR", "  /tmp/weft-logs  ", "/tmp/weft-logs"),
        ("WEFT_LOGS_DIR", None, None),
        ("WEFT_REDACT_TASKSPEC_FIELDS", "metadata.secret", "metadata.secret"),
        ("WEFT_TASK_MONITOR_INTERVAL_SECONDS", 60, 60),
        ("WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS", 0.5, 0.5),
        ("WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS", 0.25, 0.25),
        ("WEFT_CUSTOM_VALUE", object(), None),
    ],
)
def test_explicit_override_normalization_preserves_input_contract(
    name: str,
    value: object,
    expected: object,
) -> None:
    """Representative override categories retain their exact coercion contract."""

    with patch.dict(os.environ, {}, clear=True):
        result = load_config({name: value})[name.removeprefix("WEFT_")]

    if name == "WEFT_CUSTOM_VALUE":
        assert result is value
    else:
        assert result == expected


@pytest.mark.parametrize(
    ("name", "value", "expected_error"),
    [
        ("WEFT_LOGGING_ENABLED", 1, InvalidConfigError),
        ("WEFT_LOGS_DIR", Path("logs"), InvalidConfigError),
        ("WEFT_TASK_MONITOR_INTERVAL_SECONDS", 1.5, InvalidConfigError),
        (
            "WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS",
            "1",
            ValueError,
        ),
        (
            WEFT_ADMISSION_MAX_CONNECTIONS,
            1.5,
            InvalidConfigError,
        ),
        (
            WEFT_ADMISSION_RESERVE_FRACTION,
            object(),
            InvalidConfigError,
        ),
    ],
)
def test_explicit_override_normalization_preserves_error_contract(
    name: str,
    value: object,
    expected_error: type[Exception],
) -> None:
    """Override category and removed-setting failures keep their public shape."""

    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.warns(UserWarning, match=rf"\b{name}=")
        if expected_error is InvalidConfigError
        else nullcontext(),
        pytest.raises(expected_error, match=name),
    ):
        load_config({name: value})


@pytest.mark.parametrize(
    "name",
    [
        "WEFT_MANAGER_LIFETIME_TIMEOUT",
        "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS",
        "WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS",
        "WEFT_ADMISSION_RESERVE_FRACTION",
        "WEFT_TASK_MONITOR_CATCHUP_INTERVAL_SECONDS",
        "WEFT_TASK_MONITOR_STALE_OPEN_FAMILY_SECONDS",
        "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS",
        "WEFT_TASK_MONITOR_MAINTENANCE_INTERVAL_SECONDS",
        "WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS",
    ],
)
@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("source", ["environment", "override"])
def test_weft_float_config_rejects_nonfinite_values(
    name: str,
    value: float,
    source: str,
) -> None:
    """Weft float fields reject non-JSON numbers at loading [SB-0.4], [CLI-5]."""
    environment = {name: str(value)} if source == "environment" else {}
    overrides = {name: value} if source == "override" else None
    with (
        patch.dict(os.environ, environment, clear=True),
        pytest.warns(UserWarning, match=name),
        pytest.raises(InvalidConfigError, match=name) as exc_info,
    ):
        load_config(overrides)

    assert exc_info.value.key == name
    assert exc_info.value.source == source
    assert "finite" in exc_info.value.expected


_RUNTIME_OBJECT_ALLOWLIST = {
    "extensions/weft_docker/weft_docker/agent_runner.py": {"_WORK_ITEM_MISSING"},
    "extensions/weft_docker/weft_docker/plugin.py": {"_PLUGIN"},
    "extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py": {"_PLUGIN"},
    "extensions/weft_microsandbox/weft_microsandbox/plugin.py": {"_PLUGIN"},
    "weft/core/agents/provider_cli/registry.py": {"_PROVIDERS"},
    "weft/core/agents/provider_cli/windows_shims.py": {"_TOKEN_RE"},
    "weft/core/agents/runtime.py": {"_RUNTIME_REGISTRY"},
    "weft/core/agents/templates.py": {"_TEMPLATE_PATTERN"},
    "weft/core/monitor/external_log.py": {
        "_PATH_WRITER_ALIAS_REGISTRY",
        "_PATH_WRITER_REGISTRY",
        "_PATH_WRITER_REGISTRY_LOCK",
    },
    "weft/core/runners/host.py": {"_HOST_PLUGIN"},
    "weft/core/serve_log.py": {"_LOG_QUEUE", "_LOG_QUEUE_LOCK", "_LOG_WRITER_FD"},
    "weft/manager_detached_launcher.py": {"_NO_SIGNAL"},
}


def _looks_like_constant_name(name: str) -> bool:
    return re.fullmatch(r"_?[A-Z][A-Z0-9_]*", name) is not None


def _module_level_uppercase_assignments(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: set[str] = set()
    for node in tree.body:
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets = list(node.targets)
        elif isinstance(node, ast.AnnAssign) and node.simple:
            targets = [node.target]
        for target in targets:
            if isinstance(target, ast.Name) and _looks_like_constant_name(target.id):
                names.add(target.id)
    return names


class TestConstants:
    """Test that all constants are defined with expected values."""

    def test_version(self) -> None:
        """Test version constant is consistent with pyproject.toml."""
        assert isinstance(__version__, str)

        # Check consistency with pyproject.toml
        pyproject_path = Path(__file__).parent.parent.parent / "pyproject.toml"
        with open(pyproject_path, encoding="utf-8") as f:
            content = f.read()

        # Find version in pyproject.toml using regex
        # Look for version = "x.y.z" pattern
        match = re.search(r'^version\s*=\s*"([^"]+)"', content, re.MULTILINE)
        if not match:
            raise ValueError("Could not find version in pyproject.toml")

        pyproject_version = match.group(1)
        assert __version__ == pyproject_version, (
            f"Version mismatch: __version__={__version__} but "
            f"pyproject.toml has version={pyproject_version}"
        )

    def test_program_constants(self) -> None:
        """Test program identification constants."""
        assert PROG_NAME == "weft"
        assert isinstance(PROG_NAME, str)

    def test_exit_codes(self) -> None:
        """Test exit code constants."""
        assert EXIT_SUCCESS == 0
        assert isinstance(EXIT_SUCCESS, int)

    def test_taskspec_version_and_identification(self) -> None:
        """Test TaskSpec version and identification constants."""
        assert TASKSPEC_VERSION == "1.0"
        assert isinstance(TASKSPEC_VERSION, str)

        assert TASKSPEC_TID_LENGTH == 19
        assert isinstance(TASKSPEC_TID_LENGTH, int)

    def test_spec_section_defaults(self) -> None:
        """Test SpecSection default value constants."""
        assert DEFAULT_FUNCTION_TARGET == "weft.tasks:noop"
        assert isinstance(DEFAULT_FUNCTION_TARGET, str)

        assert DEFAULT_TIMEOUT is None

        assert DEFAULT_MEMORY_MB == 1024
        assert isinstance(DEFAULT_MEMORY_MB, int)

        assert DEFAULT_STREAM_OUTPUT is False
        assert isinstance(DEFAULT_STREAM_OUTPUT, bool)

        assert DEFAULT_CLEANUP_ON_EXIT is True
        assert isinstance(DEFAULT_CLEANUP_ON_EXIT, bool)

        assert DEFAULT_POLLING_INTERVAL == 1.0
        assert isinstance(DEFAULT_POLLING_INTERVAL, float)

        assert DEFAULT_REPORTING_INTERVAL == "transition"
        assert isinstance(DEFAULT_REPORTING_INTERVAL, str)

    def test_interactive_completion_budget_covers_shutdown_phases(self) -> None:
        """The outer CLI wait must outlast the shutdown phases [CLI-1.1.1]."""
        assert INTERACTIVE_STOP_COMPLETION_TIMEOUT > (
            INTERACTIVE_STOP_GRACE_SECONDS
            + (COMMAND_SESSION_TERMINATION_TIMEOUT * 3)
            + COMMAND_SESSION_POST_TERMINATION_WAIT
        )

    def test_queue_naming_conventions(self) -> None:
        """Test queue naming suffix constants."""
        assert QUEUE_INBOX_SUFFIX == "inbox"
        assert QUEUE_OUTBOX_SUFFIX == "outbox"
        assert QUEUE_CTRL_IN_SUFFIX == "ctrl_in"
        assert QUEUE_CTRL_OUT_SUFFIX == "ctrl_out"

        # All should be strings
        for suffix in [
            QUEUE_INBOX_SUFFIX,
            QUEUE_OUTBOX_SUFFIX,
            QUEUE_CTRL_IN_SUFFIX,
            QUEUE_CTRL_OUT_SUFFIX,
        ]:
            assert isinstance(suffix, str)

    def test_state_section_defaults(self) -> None:
        """Test StateSection status constants."""
        assert STATUS_CREATED == "created"
        assert STATUS_COMPLETED == "completed"

        assert DEFAULT_STATUS == STATUS_CREATED
        assert DEFAULT_STATUS == "created"

        # All should be strings
        for status in [
            STATUS_CREATED,
            STATUS_COMPLETED,
        ]:
            assert isinstance(status, str)

        assert TERMINAL_TASK_STATUSES == frozenset(
            {"completed", "failed", "timeout", "cancelled", "killed"}
        )
        assert FAILURE_LIKE_TASK_STATUSES == frozenset(
            {"failed", "timeout", "cancelled", "killed"}
        )

    def test_control_commands(self) -> None:
        """Test control command constants."""
        assert CONTROL_STOP == "STOP"
        assert CONTROL_PAUSE == "PAUSE"
        assert CONTROL_RESUME == "RESUME"

        # All should be strings
        for cmd in [CONTROL_STOP, CONTROL_PAUSE, CONTROL_RESUME]:
            assert isinstance(cmd, str)

    def test_resource_limits(self) -> None:
        """Test resource limit constants."""
        assert MIN_MEMORY_LIMIT == 1
        assert isinstance(MIN_MEMORY_LIMIT, int)

        assert MAX_CPU_LIMIT == 100
        assert MIN_CPU_LIMIT == 1
        assert isinstance(MAX_CPU_LIMIT, int)
        assert isinstance(MIN_CPU_LIMIT, int)

        # CPU limits should be sensible
        assert MIN_CPU_LIMIT < MAX_CPU_LIMIT
        assert MIN_CPU_LIMIT > 0
        assert MAX_CPU_LIMIT <= 100

        assert MIN_FDS_LIMIT == 1
        assert isinstance(MIN_FDS_LIMIT, int)

        assert MIN_CONNECTIONS_LIMIT == 0
        assert isinstance(MIN_CONNECTIONS_LIMIT, int)

    def test_production_constants_live_in_constants_module(self) -> None:
        """Ensure immutable policy constants stay centralized in `_constants.py`."""

        repo_root = Path(__file__).resolve().parents[2]
        candidates = sorted(repo_root.glob("weft/**/*.py")) + sorted(
            repo_root.glob("extensions/**/*.py")
        )

        violations: list[str] = []
        stale_allowlist_entries: list[str] = []
        for path in candidates:
            if path.name == "_constants.py":
                continue
            relative_path = path.relative_to(repo_root).as_posix()
            assigned_names = _module_level_uppercase_assignments(path)
            allowed_names = _RUNTIME_OBJECT_ALLOWLIST.get(relative_path, set())
            unexpected = sorted(assigned_names - allowed_names)
            if unexpected:
                violations.append(f"{relative_path}: {', '.join(unexpected)}")
            stale = sorted(allowed_names - assigned_names)
            if stale:
                stale_allowlist_entries.append(f"{relative_path}: {', '.join(stale)}")

        assert not stale_allowlist_entries, (
            "Stale runtime-object allowlist entries found:\n"
            + "\n".join(stale_allowlist_entries)
        )
        assert not violations, (
            "Production constants must live in weft/_constants.py. "
            "Only runtime singletons, registries, sentinels, and compiled "
            "patterns are exempt.\n" + "\n".join(violations)
        )


class TestLoadConfig:
    """Test the load_config function with various environment configurations."""

    def test_default_config(self) -> None:
        """Test load_config returns expected defaults when no env vars are set."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

            # Debug
            assert config["DEBUG"] is False

            # Logging
            assert config["LOGGING_ENABLED"] is False
            assert (
                config["MANAGER_SERVE_LOG_LEVEL"]
                == WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT
            )
            assert (
                config["MANAGER_SERVE_LOG_INTERVAL_SECONDS"]
                == WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT
            )

            # Weft project directory
            assert config["DIRECTORY_NAME"] == WEFT_DIRECTORY_NAME_DEFAULT
            assert config["LOGS_DIR"] is None
            assert config["TASK_MONITOR_ENABLED"] is (WEFT_TASK_MONITOR_ENABLED_DEFAULT)
            assert config["LIVENESS_MONITOR_ENABLED"] is (
                LIVENESS_MONITOR_ENABLED_DEFAULT
            )
            assert (
                config["TASK_MONITOR_INTERVAL_SECONDS"]
                == WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT
            )
            assert (
                config["TASK_MONITOR_BATCH_SIZE"]
                == WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
            )
            assert (
                config["TASK_MONITOR_TASK_LOG_SCAN_LIMIT"]
                == WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT
            )
            assert (
                config["TASK_MONITOR_STORE_WRITE_BATCH_SIZE"]
                == WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT
            )
            assert config["TASK_MONITOR_MODE"] == WEFT_TASK_MONITOR_MODE_DEFAULT
            assert (
                config["TASK_MONITOR_PROCESSOR"] == WEFT_TASK_MONITOR_PROCESSOR_DEFAULT
            )
            assert (
                config["LOG_TASKS_EXTERNAL_PATH"]
                == WEFT_LOG_TASKS_EXTERNAL_PATH_DEFAULT
            )
            assert config["LOG_TASKS_EXTERNAL_ENABLED"] is False
            assert config["TASK_MONITOR_LOG_SINK"] == WEFT_TASK_MONITOR_LOG_SINK_DEFAULT
            assert (
                config["TASK_MONITOR_RESTART_BACKOFF_SECONDS"]
                == WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT
            )

            # Broker config should be complete and typed.
            assert config["PROJECT_SCOPE"] is True
            assert config["DEFAULT_DB_NAME"] == ".weft/broker.db"
            assert config["PROJECT_CONFIG_PATH"] == ".weft"
            assert config["PROJECT_CONFIG_NAME"] == "broker.toml"
            assert config["AUTO_VACUUM"] == 1
            assert config["AUTO_VACUUM_INTERVAL"] == 100
            assert isinstance(config["AUTO_VACUUM_INTERVAL"], int)
            assert config["MAX_MESSAGE_SIZE"] > 0
            assert isinstance(config["MAX_MESSAGE_SIZE"], int)

    def test_task_monitor_config_normalization(self) -> None:
        """Task-monitor env values normalize to runtime types."""

        with patch.dict(
            os.environ,
            {
                "WEFT_TASK_MONITOR_ENABLED": "0",
                "WEFT_TASK_MONITOR_INTERVAL_SECONDS": str(
                    HEARTBEAT_MIN_INTERVAL_SECONDS
                ),
                "WEFT_TASK_MONITOR_BATCH_SIZE": "42",
                "WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": "420",
                "WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE": "7",
                "WEFT_LOG_TASKS_EXTERNAL_PATH": "task-log.jsonl",
                "WEFT_LOG_TASKS_EXTERNAL_MODE": "raw",
                "WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "172800",
                "WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS": "3600",
                "WEFT_TASK_MONITOR_MODE": "custom",
                "WEFT_TASK_MONITOR_PROCESSOR": "tests.core.test_task_monitoring:noop",
                "WEFT_TASK_MONITOR_LOG_SINK": "disk",
                "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": "2.5",
            },
            clear=True,
        ):
            config = load_config()

        assert config["TASK_MONITOR_ENABLED"] is False
        assert config["TASK_MONITOR_INTERVAL_SECONDS"] == (
            HEARTBEAT_MIN_INTERVAL_SECONDS
        )
        assert config["TASK_MONITOR_BATCH_SIZE"] == 42
        assert config["TASK_MONITOR_TASK_LOG_SCAN_LIMIT"] == 420
        assert config["TASK_MONITOR_STORE_WRITE_BATCH_SIZE"] == 7
        assert config["LOG_TASKS_EXTERNAL_PATH"] == "task-log.jsonl"
        assert config["LOG_TASKS_EXTERNAL_ENABLED"] is False
        assert config["LOG_TASKS_EXTERNAL_MODE"] == "raw"
        assert config["LOG_TASKS_RETENTION_PERIOD_SECONDS"] == 172800.0
        assert config["TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS"] == 3600.0
        assert config["TASK_MONITOR_MODE"] == "custom"
        assert (
            config["TASK_MONITOR_PROCESSOR"] == "tests.core.test_task_monitoring:noop"
        )
        assert config["TASK_MONITOR_LOG_SINK"] == "disk"
        assert config["TASK_MONITOR_RESTART_BACKOFF_SECONDS"] == 2.5

    @pytest.mark.parametrize("explicit_unset", [False, True])
    def test_reserved_cleanup_min_age_unset_is_none_for_derivation(
        self, explicit_unset: bool
    ) -> None:
        """An absent or explicitly unset reserved gate remains None for derivation.

        None is the contract that lets ``TaskMonitorRuntimeConfig`` derive
        the effective gate from the configured task-log retention so the
        gates agree ([OBS.13.5]); a pre-resolved float here would freeze
        the compile-time default and break retention-only overrides.
        """

        with patch.dict(os.environ, {}, clear=True):
            config = load_config(
                {"WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS": None}
                if explicit_unset
                else None
            )

        assert config["TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS"] is None

    def test_reserved_cleanup_min_age_rejects_negative(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS": "-1"},
                clear=True,
            ),
            pytest.warns(
                UserWarning,
                match=r"\bWEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS=",
            ),
            pytest.raises(
                ValueError,
                match="WEFT_TASK_MONITOR_RESERVED_CLEANUP_MIN_AGE_SECONDS",
            ),
        ):
            load_config()

    def test_task_monitor_interval_rejects_below_heartbeat_minimum(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "WEFT_TASK_MONITOR_INTERVAL_SECONDS": str(
                        HEARTBEAT_MIN_INTERVAL_SECONDS - 1
                    )
                },
                clear=True,
            ),
            pytest.warns(UserWarning, match=r"\bWEFT_TASK_MONITOR_INTERVAL_SECONDS="),
            pytest.raises(ValueError, match="WEFT_TASK_MONITOR_INTERVAL_SECONDS"),
        ):
            load_config()

    def test_task_monitor_batch_size_rejects_zero(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_TASK_MONITOR_BATCH_SIZE": "0"},
                clear=True,
            ),
            pytest.warns(UserWarning, match=r"\bWEFT_TASK_MONITOR_BATCH_SIZE="),
            pytest.raises(ValueError, match="WEFT_TASK_MONITOR_BATCH_SIZE"),
        ):
            load_config()

    def test_task_monitor_task_log_scan_limit_rejects_zero(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": "0"},
                clear=True,
            ),
            pytest.warns(
                UserWarning, match=r"\bWEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT="
            ),
            pytest.raises(ValueError, match="WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT"),
        ):
            load_config()

    def test_task_monitor_store_write_batch_size_rejects_zero(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE": "0"},
                clear=True,
            ),
            pytest.warns(
                UserWarning, match=r"\bWEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE="
            ),
            pytest.raises(ValueError, match="WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE"),
        ):
            load_config()

    def test_log_tasks_retention_period_rejects_zero(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0"},
                clear=True,
            ),
            pytest.warns(
                UserWarning, match=r"\bWEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS="
            ),
            pytest.raises(ValueError, match="WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS"),
        ):
            load_config()

    def test_removed_task_monitor_task_log_cutoff_rejects(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS": "172800"},
                clear=True,
            ),
            pytest.raises(
                ValueError, match="WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS"
            ),
        ):
            load_config()

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            ("WEFT_TASK_MONITOR_TABLE_DELETE_ENABLED", "1"),
            ("WEFT_TASK_MONITOR_CLEANUP_WORKERS", "1"),
        ],
    )
    def test_removed_task_monitor_env_rejects(self, name: str, value: str) -> None:
        with (
            patch.dict(os.environ, {name: value}, clear=True),
            pytest.raises(ValueError, match=name),
        ):
            load_config()

    @pytest.mark.parametrize("level", ["off", "info", "debug", "trace"])
    def test_manager_serve_log_level_env(self, level: str) -> None:
        with patch.dict(
            os.environ,
            {"WEFT_MANAGER_SERVE_LOG_LEVEL": level},
            clear=True,
        ):
            config = load_config()

        assert config["MANAGER_SERVE_LOG_LEVEL"] == level

    def test_manager_serve_log_level_rejects_unknown(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_MANAGER_SERVE_LOG_LEVEL": "verbose"},
                clear=True,
            ),
            pytest.warns(UserWarning, match=r"\bWEFT_MANAGER_SERVE_LOG_LEVEL="),
            pytest.raises(ValueError, match="WEFT_MANAGER_SERVE_LOG_LEVEL"),
        ):
            load_config()

    @pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
    def test_manager_serve_log_interval_rejects_invalid(self, value: str) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS": value},
                clear=True,
            ),
            pytest.warns(
                UserWarning, match=r"\bWEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS="
            ),
            pytest.raises(ValueError, match="WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS"),
        ):
            load_config()

    def test_manager_serve_log_overrides_normalize(self) -> None:
        config = load_config(
            {
                f"WEFT_{MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY}": "true",
                "WEFT_MANAGER_SERVE_LOG_LEVEL": "debug",
                "WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS": 0.25,
            }
        )

        assert config[MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY] is True
        assert config["MANAGER_SERVE_LOG_LEVEL"] == "debug"
        assert config["MANAGER_SERVE_LOG_INTERVAL_SECONDS"] == 0.25

    def test_debug_setting(self) -> None:
        """Test debug environment variable."""
        # Values that should enable debug
        for value in ["1", "true", "yes", "debug", "TRUE", "Y"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["DEBUG"] is True, f"Expected True for WEFT_DEBUG={value}"

        # Values that should disable debug
        for value in ["", "0", "f", "F", "false", "False", "FALSE"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["DEBUG"] is False, (
                    f"Expected False for WEFT_DEBUG={value}"
                )

        # Missing should be False
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["DEBUG"] is False

    def test_logging_setting(self) -> None:
        """Test logging environment variable."""
        for value in ["1", "true", "yes", "enabled", "off"]:
            with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": value}):
                config = load_config()
                assert config["LOGGING_ENABLED"] is True

        for value in ["", "0", "f", "false", "none", "null"]:
            with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": value}):
                config = load_config()
                assert config["LOGGING_ENABLED"] is False

        # Missing should be False
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["LOGGING_ENABLED"] is False

    def test_manager_reuse_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_MANAGER_REUSE_ENABLED": "0"}):
            config = load_config()
            assert config["MANAGER_REUSE_ENABLED"] is False

        with patch.dict(os.environ, {"WEFT_MANAGER_REUSE_ENABLED": "true"}):
            config = load_config()
            assert config["MANAGER_REUSE_ENABLED"] is True

    def test_liveness_monitor_enabled_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_LIVENESS_MONITOR_ENABLED": "0"}):
            config = load_config()
            assert config["LIVENESS_MONITOR_ENABLED"] is False

        with patch.dict(os.environ, {"WEFT_LIVENESS_MONITOR_ENABLED": "true"}):
            config = load_config()
            assert config["LIVENESS_MONITOR_ENABLED"] is True

    def test_admission_config_defaults_are_disabled(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()

        assert config["ADMISSION_MAX_CONNECTIONS"] == 0
        assert config["ADMISSION_RESERVE_FRACTION"] == 0.1
        assert ADMISSION_SERVICE_RESERVE_SLOTS == 3

    @pytest.mark.parametrize(
        ("name", "value", "expected"),
        [
            (WEFT_ADMISSION_MAX_CONNECTIONS, "0", 0),
            (WEFT_ADMISSION_MAX_CONNECTIONS, "1", 1),
            (WEFT_ADMISSION_RESERVE_FRACTION, "0", 0.0),
            (WEFT_ADMISSION_RESERVE_FRACTION, "0.25", 0.25),
        ],
    )
    def test_admission_env_values_normalize(
        self,
        name: str,
        value: str,
        expected: object,
    ) -> None:
        with patch.dict(os.environ, {name: value}, clear=True):
            config = load_config()

        assert config[name.removeprefix("WEFT_")] == expected

    @pytest.mark.parametrize(
        ("name", "value"),
        [
            (WEFT_ADMISSION_MAX_CONNECTIONS, "-1"),
            (WEFT_ADMISSION_MAX_CONNECTIONS, "1.0"),
            (WEFT_ADMISSION_RESERVE_FRACTION, "-0.1"),
            (WEFT_ADMISSION_RESERVE_FRACTION, "1"),
            (WEFT_ADMISSION_RESERVE_FRACTION, "nan"),
            (WEFT_ADMISSION_RESERVE_FRACTION, "inf"),
        ],
    )
    def test_admission_env_values_reject_invalid_ranges(
        self,
        name: str,
        value: str,
    ) -> None:
        with (
            patch.dict(os.environ, {name: value}, clear=True),
            pytest.warns(UserWarning, match=rf"\b{name}="),
            pytest.raises(ValueError, match=name),
        ):
            load_config()

    def test_admission_explicit_overrides_use_the_same_parsers(self) -> None:
        config = load_config(
            {
                WEFT_ADMISSION_MAX_CONNECTIONS: 3,
                WEFT_ADMISSION_RESERVE_FRACTION: 0.2,
            }
        )

        assert config["ADMISSION_MAX_CONNECTIONS"] == 3
        assert config["ADMISSION_RESERVE_FRACTION"] == 0.2

    def test_weft_directory_name_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_DIRECTORY_NAME": ".engram"}, clear=True):
            config = load_config()

        assert config["DIRECTORY_NAME"] == ".engram"
        assert config["DEFAULT_DB_NAME"] == ".engram/broker.db"
        assert config["PROJECT_CONFIG_PATH"] == ".engram"
        assert config["PROJECT_CONFIG_NAME"] == "broker.toml"

    @pytest.mark.parametrize("value", ["", ".", "..", "foo/bar", "foo\\bar"])
    def test_weft_directory_name_env_rejects_invalid_values(self, value: str) -> None:
        with (
            patch.dict(os.environ, {"WEFT_DIRECTORY_NAME": value}, clear=True),
            pytest.warns(UserWarning, match=r"\bWEFT_DIRECTORY_NAME="),
            pytest.raises(ValueError, match="WEFT_DIRECTORY_NAME"),
        ):
            load_config()

    def test_weft_logs_dir_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_LOGS_DIR": "var/weft-logs"}, clear=True):
            config = load_config()

        assert config["LOGS_DIR"] == "var/weft-logs"

    def test_weft_logs_dir_env_blanks_to_default(self) -> None:
        with patch.dict(os.environ, {"WEFT_LOGS_DIR": "   "}, clear=True):
            config = load_config()

        assert config["LOGS_DIR"] is None

    def test_explicit_default_db_name_beats_directory_name_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WEFT_DIRECTORY_NAME": ".engram",
                "WEFT_DEFAULT_DB_NAME": ".custom/weft.db",
            },
            clear=True,
        ):
            config = load_config()

        assert config["DIRECTORY_NAME"] == ".engram"
        assert config["DEFAULT_DB_NAME"] == ".custom/weft.db"
        assert config["PROJECT_CONFIG_PATH"] == ".engram"

    def test_explicit_project_config_path_beats_directory_name_default(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WEFT_DIRECTORY_NAME": ".engram",
                "WEFT_PROJECT_CONFIG_PATH": ".custom",
                "WEFT_PROJECT_CONFIG_NAME": "queues.toml",
            },
            clear=True,
        ):
            config = load_config()

        assert config["DIRECTORY_NAME"] == ".engram"
        assert config["PROJECT_CONFIG_PATH"] == ".custom"
        assert config["PROJECT_CONFIG_NAME"] == "queues.toml"

    def test_load_config_recomputes_broker_defaults_for_weft_overrides(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config({"WEFT_DIRECTORY_NAME": ".engram"})

        assert config["DIRECTORY_NAME"] == ".engram"
        assert config["DEFAULT_DB_NAME"] == ".engram/broker.db"
        assert config["PROJECT_CONFIG_PATH"] == ".engram"

    def test_fractional_string_vacuum_threshold_reaches_broker_resolver(self) -> None:
        """A WEFT percentage string keeps SimpleBroker's string semantics."""
        with patch.dict(os.environ, {}, clear=True):
            config = load_config({"WEFT_VACUUM_THRESHOLD": "0.5"})

        assert config["VACUUM_THRESHOLD"] == 0.5

    def test_weft_broker_values_do_not_change_standalone_simplebroker_config(
        self,
    ) -> None:
        """Compiling Weft config does not mutate env or SimpleBroker defaults."""

        with patch.dict(os.environ, {"WEFT_CACHE_MB": "17"}, clear=True):
            before_environment = dict(os.environ)
            weft_config = load_config()
            standalone_config = resolve_config()

            assert dict(os.environ) == before_environment

        assert weft_config["CACHE_MB"] == 17
        assert standalone_config["CACHE_MB"] == 10

    def test_removed_simplebroker_vacuum_lock_timeout_env_is_ignored(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WEFT_VACUUM_LOCK_TIMEOUT": "10",
                "BROKER_VACUUM_LOCK_TIMEOUT": "10",
            },
            clear=True,
        ):
            config = load_config()

        assert "WEFT_VACUUM_LOCK_TIMEOUT" not in config
        assert "BROKER_VACUUM_LOCK_TIMEOUT" not in config

    def test_removed_simplebroker_vacuum_lock_timeout_overrides_are_ignored(
        self,
    ) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = load_config(
                {
                    "WEFT_VACUUM_LOCK_TIMEOUT": "10",
                    "BROKER_VACUUM_LOCK_TIMEOUT": "10",
                }
            )

        assert "WEFT_VACUUM_LOCK_TIMEOUT" not in config
        assert "BROKER_VACUUM_LOCK_TIMEOUT" not in config

    def test_load_config_rejects_ambiguous_postgres_override_shapes(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValueError, match="ambiguous"),
        ):
            load_config(
                {
                    "WEFT_BACKEND": "postgres",
                    "WEFT_BACKEND_TARGET": "postgresql://broker@db.example.com/simplebroker",
                    "WEFT_BACKEND_HOST": "db.example.com",
                }
            )

    def test_manager_timeout_env(self) -> None:
        """Manager timeout honours the environment variable."""
        with patch.dict(os.environ, {"WEFT_MANAGER_LIFETIME_TIMEOUT": "42.5"}):
            config = load_config()
            assert config["MANAGER_LIFETIME_TIMEOUT"] == 42.5

    @pytest.mark.parametrize(
        "value",
        ["-1", "true", "junk"],
    )
    def test_manager_timeout_env_rejects_invalid_values(self, value: str) -> None:
        with (
            patch.dict(os.environ, {"WEFT_MANAGER_LIFETIME_TIMEOUT": value}),
            pytest.warns(UserWarning, match=r"\bWEFT_MANAGER_LIFETIME_TIMEOUT="),
            pytest.raises(ValueError, match="WEFT_MANAGER_LIFETIME_TIMEOUT"),
        ):
            load_config()

    def test_backend_env_translation_from_parts(self) -> None:
        """Backend-selection env vars are translated to typed SimpleBroker keys."""
        with patch.dict(
            os.environ,
            {
                "WEFT_BACKEND": "postgres",
                "WEFT_BACKEND_HOST": "db.example.com",
                "WEFT_BACKEND_PORT": "5433",
                "WEFT_BACKEND_USER": "broker",
                "WEFT_BACKEND_PASSWORD": "secret",
                "WEFT_BACKEND_DATABASE": "simplebroker_app",
                "WEFT_BACKEND_SCHEMA": "broker_schema",
            },
            clear=True,
        ):
            config = load_config()

        assert config["BACKEND"] == "postgres"
        assert config["BACKEND_HOST"] == "db.example.com"
        assert config["BACKEND_PORT"] == 5433
        assert config["BACKEND_USER"] == "broker"
        assert config["BACKEND_PASSWORD"] == "secret"
        assert config["BACKEND_DATABASE"] == "simplebroker_app"
        assert config["BACKEND_SCHEMA"] == "broker_schema"
        assert config["BACKEND_TARGET"] == ""
        assert config["AUTO_VACUUM_INTERVAL"] == 100
        assert isinstance(config["AUTO_VACUUM_INTERVAL"], int)

    def test_backend_env_translation_from_target(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WEFT_BACKEND": "postgres",
                "WEFT_BACKEND_TARGET": "postgresql://broker@db.example.com/simplebroker",
                "WEFT_BACKEND_SCHEMA": "broker_schema",
            },
            clear=True,
        ):
            config = load_config()

        assert config["BACKEND"] == "postgres"
        assert (
            config["BACKEND_TARGET"]
            == "postgresql://broker@db.example.com/simplebroker"
        )
        assert config["BACKEND_SCHEMA"] == "broker_schema"

    def test_backend_env_rejects_target_plus_parts(self) -> None:
        with (
            patch.dict(
                os.environ,
                {
                    "WEFT_BACKEND": "postgres",
                    "WEFT_BACKEND_TARGET": (
                        "postgresql://broker@db.example.com/simplebroker"
                    ),
                    "WEFT_BACKEND_HOST": "db.example.com",
                },
                clear=True,
            ),
            pytest.raises(ValueError, match="ambiguous"),
        ):
            load_config()

    def test_config_immutability(self) -> None:
        """Test that modifying returned config doesn't affect subsequent calls."""
        config1 = load_config()
        original_debug = config1["DEBUG"]

        # The canonical snapshot itself cannot be edited.
        with pytest.raises(TypeError):
            cast(MutableMapping[str, object], config1)["DEBUG"] = not original_debug
        edited = dict(config1)
        edited["DEBUG"] = not original_debug

        # Get a new config
        config2 = load_config()

        # Should have original value, not modified one
        assert config2["DEBUG"] == original_debug
        assert config2["DEBUG"] != edited["DEBUG"]


@pytest.mark.parametrize("source", ["environment", "override"])
@pytest.mark.parametrize("value", ["0", "1"])
def test_removed_collation_store_toggle_fails_with_migration_message(
    source: str,
    value: str,
) -> None:
    name = "WEFT_TASK_MONITOR_COLLATION_STORE_ENABLED"
    expected = (
        name + " was removed; the collation store is always enabled; "
        "use WEFT_TASK_MONITOR_MODE=report_only to disable destructive cleanup"
    )
    with (
        patch.dict(
            os.environ, {name: value} if source == "environment" else {}, clear=True
        ),
        pytest.raises(ValueError) as caught,
    ):
        load_config({name: value} if source == "override" else None)
    assert str(caught.value) == expected


def test_config_uses_unprefixed_percentage_values() -> None:
    """The new broker contract returns percentage units and canonical names."""
    with patch.dict(os.environ, {}, clear=True):
        result = load_config({"WEFT_VACUUM_THRESHOLD": "0.5"})
    assert result["VACUUM_THRESHOLD"] == 0.5


def test_weft_config_isolated_namespace_and_runtime_snapshot() -> None:
    with patch.dict(
        os.environ, {"BROKER_CACHE_MB": "invalid", "WEFT_CACHE_MB": "17"}, clear=True
    ):
        config = load_config()
    assert isinstance(config, Config)
    assert config.prefix == "WEFT"
    assert config["CACHE_MB"] == 17
    snapshot = dict(config)
    with patch.dict(os.environ, {"WEFT_CACHE_MB": "99"}, clear=True):
        restored = constants.resolve_runtime_config(snapshot)
    assert restored["CACHE_MB"] == 17
    assert constants.resolve_runtime_config(restored) is restored


def test_later_valid_override_repairs_invalid_environment() -> None:
    with (
        patch.dict(os.environ, {"WEFT_CACHE_MB": "invalid"}, clear=True),
        pytest.warns(UserWarning, match="WEFT_CACHE_MB"),
    ):
        config = load_config({"WEFT_CACHE_MB": 17})
    assert config["CACHE_MB"] == 17


def test_runtime_partial_config_uses_declared_defaults_without_environment() -> None:
    with patch.dict(os.environ, {"WEFT_CACHE_MB": "99"}, clear=True):
        config = constants.resolve_runtime_config(
            {"CACHE_MB": 17, "DIRECTORY_NAME": ".custom"}
        )
    assert config["CACHE_MB"] == 17
    assert config["DEFAULT_DB_NAME"] == ".custom/broker.db"
    assert config["PROJECT_CONFIG_PATH"] == ".custom"


def test_private_serve_marker_is_not_an_environment_setting() -> None:
    with patch.dict(os.environ, {"WEFT_MANAGER_SERVE_LOG_ACTIVE": "true"}, clear=True):
        assert load_config()["MANAGER_SERVE_LOG_ACTIVE"] is False
        assert (
            load_config({"WEFT_MANAGER_SERVE_LOG_ACTIVE": True})[
                "MANAGER_SERVE_LOG_ACTIVE"
            ]
            is True
        )


@pytest.mark.parametrize(
    "value", [None, "", "relative/project", "~/project", " project with spaces "]
)
def test_context_config_loads_without_resolving_paths(value: str | None) -> None:
    """The declared root is data until the context owner resolves it [SB-0.4]."""
    with patch.dict(os.environ, {}, clear=True):
        assert load_config()["CONTEXT"] is None
        assert load_config({"WEFT_CONTEXT": value})["CONTEXT"] == (value or None)
    if value is not None:
        with patch.dict(os.environ, {"WEFT_CONTEXT": value}, clear=True):
            assert load_config()["CONTEXT"] == (value or None)


@pytest.mark.parametrize("value", [1, False, Path("project"), ["project"]])
def test_context_config_rejects_non_string_values(value: object) -> None:
    """A context override cannot smuggle a live object into Config JSON."""
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.warns(UserWarning, match="WEFT_CONTEXT"),
        pytest.raises(
            InvalidConfigError,
            match="a project-root path string or None; an empty string means discovery",
        ),
    ):
        load_config({"WEFT_CONTEXT": value})
