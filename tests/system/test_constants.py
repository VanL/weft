"""Tests for the _constants module."""

import ast
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from weft._constants import (
    AGENT_SESSION_READY_TIMEOUT_SECONDS,
    COMMAND_SESSION_POST_TERMINATION_WAIT,
    COMMAND_SESSION_TERMINATION_TIMEOUT,
    CONTROL_PAUSE,
    CONTROL_RESUME,
    CONTROL_STOP,
    CONTROL_SURFACE_WAIT_INTERVAL,
    DEFAULT_CLEANUP_ON_EXIT,
    DEFAULT_CPU_PERCENT,  # RENAMED from DEFAULT_CPU_LIMIT
    DEFAULT_FUNCTION_TARGET,
    DEFAULT_MAX_CONNECTIONS,
    DEFAULT_MAX_FDS,
    DEFAULT_MEMORY_MB,  # RENAMED from DEFAULT_MEMORY_LIMIT
    DEFAULT_POLLING_INTERVAL,
    DEFAULT_REPORTING_INTERVAL,
    DEFAULT_STATUS,
    DEFAULT_STREAM_OUTPUT,
    DEFAULT_TIMEOUT,
    EXIT_SUCCESS,
    FAILURE_LIKE_TASK_STATUSES,
    HEARTBEAT_ENDPOINT_PROBE_TIMEOUT,
    HEARTBEAT_MIN_INTERVAL_SECONDS,
    INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL,
    INTERACTIVE_OUTPUT_DRAIN_TIMEOUT,
    INTERACTIVE_STOP_COMPLETION_TIMEOUT,
    INTERACTIVE_STOP_GRACE_SECONDS,
    INTERACTIVE_STOP_POLL_INTERVAL,
    MANAGER_CHILD_EXIT_POLL_INTERVAL,
    MANAGER_COMPETING_STARTUP_GRACE_SECONDS,
    MANAGER_PID_LIVENESS_RECHECK_INTERVAL,
    MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES,
    MANAGER_REGISTRY_POLL_INTERVAL,
    MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY,
    MANAGER_STARTUP_TIMEOUT_SECONDS,
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
    RUNNER_DIAGNOSTICS_FIELD,
    RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS,
    RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS,
    SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT,
    STATUS_CANCELLED,
    STATUS_COMPLETED,
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_RUNNING,
    STATUS_WATCH_MIN_INTERVAL,
    STREAM_CHUNK_SIZE_BYTES,
    SUBPROCESS_POLL_INTERVAL_FLOOR,
    SUBPROCESS_STREAM_DRAIN_TIMEOUT,
    SUBPROCESS_STREAM_READ_SIZE,
    SUBPROCESS_TERMINATION_WAIT_TIMEOUT,
    TASK_PROCESS_POLL_INTERVAL,
    TASK_REACTOR_WAKEUP_MAX_SECONDS,
    TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN,
    TASK_WORKER_RESULT_QUEUE_MAXSIZE,
    TASKSPEC_TID_LENGTH,
    TASKSPEC_VERSION,
    TERMINAL_TASK_STATUSES,
    WEFT_AUTOSTART_TASKS_DEFAULT,
    WEFT_COMPLETED_RESULT_GRACE_SECONDS,
    WEFT_DIRECTORY_NAME_DEFAULT,
    WEFT_MANAGER_LIFETIME_TIMEOUT,
    WEFT_MANAGER_REUSE_ENABLED,
    WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT,
    WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT,
    WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_ENABLED_DEFAULT,
    WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_LOG_SINK_DEFAULT,
    WEFT_TASK_MONITOR_PROCESSOR_DEFAULT,
    WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT,
    WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT,
    WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT,
    __version__,
    compile_config,
    load_config,
)

_RUNTIME_OBJECT_ALLOWLIST = {
    "extensions/weft_docker/weft_docker/agent_runner.py": {"_WORK_ITEM_MISSING"},
    "extensions/weft_docker/weft_docker/plugin.py": {"_PLUGIN"},
    "extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py": {"_PLUGIN"},
    "weft/core/agents/provider_cli/registry.py": {"_PROVIDERS"},
    "weft/core/agents/provider_cli/windows_shims.py": {"_TOKEN_RE"},
    "weft/core/agents/runtime.py": {"_RUNTIME_REGISTRY"},
    "weft/core/agents/templates.py": {"_TEMPLATE_PATTERN"},
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

        assert DEFAULT_CPU_PERCENT is None
        assert DEFAULT_MAX_FDS is None
        assert DEFAULT_MAX_CONNECTIONS is None

        assert DEFAULT_STREAM_OUTPUT is False
        assert isinstance(DEFAULT_STREAM_OUTPUT, bool)

        assert DEFAULT_CLEANUP_ON_EXIT is True
        assert isinstance(DEFAULT_CLEANUP_ON_EXIT, bool)

        assert DEFAULT_POLLING_INTERVAL == 1.0
        assert isinstance(DEFAULT_POLLING_INTERVAL, float)

        assert DEFAULT_REPORTING_INTERVAL == "transition"
        assert isinstance(DEFAULT_REPORTING_INTERVAL, str)

        assert TASK_PROCESS_POLL_INTERVAL == 0.05
        assert isinstance(TASK_PROCESS_POLL_INTERVAL, float)
        assert TASK_REACTOR_WAKEUP_MAX_SECONDS == 0.05
        assert isinstance(TASK_REACTOR_WAKEUP_MAX_SECONDS, float)
        assert TASK_WORKER_RESULT_QUEUE_MAXSIZE == 256
        assert isinstance(TASK_WORKER_RESULT_QUEUE_MAXSIZE, int)
        assert TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN == 64
        assert isinstance(TASK_WORKER_RESULT_DRAIN_MAX_PER_TURN, int)
        assert CONTROL_SURFACE_WAIT_INTERVAL == 0.05
        assert SPAWN_SUBMISSION_RECONCILIATION_TIMEOUT == 1.0
        assert STATUS_WATCH_MIN_INTERVAL == 0.1

        assert STREAM_CHUNK_SIZE_BYTES == 512 * 1024
        assert SUBPROCESS_STREAM_READ_SIZE == 64 * 1024
        assert SUBPROCESS_TERMINATION_WAIT_TIMEOUT == 0.2
        assert SUBPROCESS_STREAM_DRAIN_TIMEOUT == 0.25
        assert SUBPROCESS_POLL_INTERVAL_FLOOR == 0.01

    def test_lifecycle_polling_and_timeout_constants(self) -> None:
        assert AGENT_SESSION_READY_TIMEOUT_SECONDS == 30.0
        assert RUNNER_DIAGNOSTICS_FIELD == "runner_diagnostics"
        assert RUNNER_DIAGNOSTICS_MESSAGE_MAX_CHARS == 500
        assert RUNNER_DIAGNOSTICS_TRACEBACK_MAX_CHARS == 4000
        assert MANAGER_STARTUP_TIMEOUT_SECONDS == 10.0
        assert MANAGER_REGISTRY_POLL_INTERVAL == 0.1
        assert MANAGER_PID_LIVENESS_RECHECK_INTERVAL == 0.5
        assert MANAGER_CHILD_EXIT_POLL_INTERVAL == 0.05
        assert MANAGER_PUBLIC_SPAWN_DRAIN_MAX_MESSAGES == 128
        assert MANAGER_COMPETING_STARTUP_GRACE_SECONDS == 0.5
        assert HEARTBEAT_ENDPOINT_PROBE_TIMEOUT == 0.25
        assert WEFT_COMPLETED_RESULT_GRACE_SECONDS == 0.5
        assert INTERACTIVE_OUTPUT_DRAIN_TIMEOUT == 0.25
        assert INTERACTIVE_OUTPUT_DRAIN_POLL_INTERVAL == 0.01
        assert INTERACTIVE_STOP_GRACE_SECONDS == 2.0
        assert INTERACTIVE_STOP_POLL_INTERVAL == 0.05
        assert COMMAND_SESSION_TERMINATION_TIMEOUT == 2.0
        assert COMMAND_SESSION_POST_TERMINATION_WAIT == 0.2
        assert INTERACTIVE_STOP_COMPLETION_TIMEOUT == (
            INTERACTIVE_STOP_GRACE_SECONDS
            + (COMMAND_SESSION_TERMINATION_TIMEOUT * 3)
            + COMMAND_SESSION_POST_TERMINATION_WAIT
            + 0.5
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
        assert STATUS_RUNNING == "running"
        assert STATUS_COMPLETED == "completed"
        assert STATUS_FAILED == "failed"
        assert STATUS_CANCELLED == "cancelled"

        assert DEFAULT_STATUS == STATUS_CREATED
        assert DEFAULT_STATUS == "created"

        # All should be strings
        for status in [
            STATUS_CREATED,
            STATUS_RUNNING,
            STATUS_COMPLETED,
            STATUS_FAILED,
            STATUS_CANCELLED,
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
            assert config["WEFT_DEBUG"] is False

            # Logging
            assert config["WEFT_LOGGING_ENABLED"] is False
            assert (
                config["WEFT_MANAGER_SERVE_LOG_LEVEL"]
                == WEFT_MANAGER_SERVE_LOG_LEVEL_DEFAULT
            )
            assert (
                config["WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS"]
                == WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS_DEFAULT
            )

            # Weft project directory
            assert config["WEFT_DIRECTORY_NAME"] == WEFT_DIRECTORY_NAME_DEFAULT
            assert config["WEFT_LOGS_DIR"] is None
            assert config["WEFT_TASK_MONITOR_ENABLED"] is (
                WEFT_TASK_MONITOR_ENABLED_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_INTERVAL_SECONDS"]
                == WEFT_TASK_MONITOR_INTERVAL_SECONDS_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_BATCH_SIZE"]
                == WEFT_TASK_MONITOR_BATCH_SIZE_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT"]
                == WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE"]
                == WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_PROCESSOR"]
                == WEFT_TASK_MONITOR_PROCESSOR_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_LOG_SINK"]
                == WEFT_TASK_MONITOR_LOG_SINK_DEFAULT
            )
            assert (
                config["WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS"]
                == WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS_DEFAULT
            )

            # Broker config should be complete and typed.
            assert config["BROKER_PROJECT_SCOPE"] is True
            assert config["BROKER_DEFAULT_DB_NAME"] == ".weft/broker.db"
            assert config["BROKER_PROJECT_CONFIG_PATH"] == ".weft"
            assert config["BROKER_PROJECT_CONFIG_NAME"] == "broker.toml"
            assert config["BROKER_AUTO_VACUUM"] == 1
            assert config["BROKER_AUTO_VACUUM_INTERVAL"] == 100
            assert isinstance(config["BROKER_AUTO_VACUUM_INTERVAL"], int)
            assert config["BROKER_MAX_MESSAGE_SIZE"] > 0
            assert isinstance(config["BROKER_MAX_MESSAGE_SIZE"], int)

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
                "WEFT_TASK_MONITOR_PROCESSOR": "tests.core.test_task_monitoring:noop",
                "WEFT_TASK_MONITOR_LOG_SINK": "disk",
                "WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS": "2.5",
            },
            clear=True,
        ):
            config = load_config()

        assert config["WEFT_TASK_MONITOR_ENABLED"] is False
        assert config["WEFT_TASK_MONITOR_INTERVAL_SECONDS"] == (
            HEARTBEAT_MIN_INTERVAL_SECONDS
        )
        assert config["WEFT_TASK_MONITOR_BATCH_SIZE"] == 42
        assert config["WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT"] == 420
        assert config["WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE"] == 7
        assert config["WEFT_LOG_TASKS_EXTERNAL_PATH"] == "task-log.jsonl"
        assert config["WEFT_LOG_TASKS_EXTERNAL_ENABLED"] is True
        assert config["WEFT_LOG_TASKS_EXTERNAL_MODE"] == "raw"
        assert config["WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS"] == 172800.0
        assert (
            config["WEFT_TASK_MONITOR_PROCESSOR"]
            == "tests.core.test_task_monitoring:noop"
        )
        assert config["WEFT_TASK_MONITOR_LOG_SINK"] == "disk"
        assert config["WEFT_TASK_MONITOR_RESTART_BACKOFF_SECONDS"] == 2.5

    def test_task_monitor_interval_rejects_below_heartbeat_minimum(self) -> None:
        with patch.dict(
            os.environ,
            {
                "WEFT_TASK_MONITOR_INTERVAL_SECONDS": str(
                    HEARTBEAT_MIN_INTERVAL_SECONDS - 1
                )
            },
            clear=True,
        ):
            with pytest.raises(ValueError, match="WEFT_TASK_MONITOR_INTERVAL_SECONDS"):
                load_config()

    def test_task_monitor_batch_size_rejects_zero(self) -> None:
        with patch.dict(
            os.environ,
            {"WEFT_TASK_MONITOR_BATCH_SIZE": "0"},
            clear=True,
        ):
            with pytest.raises(ValueError, match="WEFT_TASK_MONITOR_BATCH_SIZE"):
                load_config()

    def test_task_monitor_task_log_scan_limit_rejects_zero(self) -> None:
        with patch.dict(
            os.environ,
            {"WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT": "0"},
            clear=True,
        ):
            with pytest.raises(
                ValueError, match="WEFT_TASK_MONITOR_TASK_LOG_SCAN_LIMIT"
            ):
                load_config()

    def test_task_monitor_store_write_batch_size_rejects_zero(self) -> None:
        with patch.dict(
            os.environ,
            {"WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE": "0"},
            clear=True,
        ):
            with pytest.raises(
                ValueError, match="WEFT_TASK_MONITOR_STORE_WRITE_BATCH_SIZE"
            ):
                load_config()

    def test_log_tasks_retention_period_rejects_zero(self) -> None:
        with patch.dict(
            os.environ,
            {"WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS": "0"},
            clear=True,
        ):
            with pytest.raises(
                ValueError, match="WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS"
            ):
                load_config()

    def test_removed_task_monitor_task_log_cutoff_rejects(self) -> None:
        with patch.dict(
            os.environ,
            {"WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS": "172800"},
            clear=True,
        ):
            with pytest.raises(
                ValueError, match="WEFT_TASK_MONITOR_TASK_LOG_CUTOFF_SECONDS"
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

        assert config["WEFT_MANAGER_SERVE_LOG_LEVEL"] == level

    def test_manager_serve_log_level_rejects_unknown(self) -> None:
        with (
            patch.dict(
                os.environ,
                {"WEFT_MANAGER_SERVE_LOG_LEVEL": "verbose"},
                clear=True,
            ),
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
            pytest.raises(ValueError, match="WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS"),
        ):
            load_config()

    def test_manager_serve_log_overrides_normalize(self) -> None:
        config = compile_config(
            {
                MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY: "true",
                "WEFT_MANAGER_SERVE_LOG_LEVEL": "debug",
                "WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS": 0.25,
            }
        )

        assert config[MANAGER_SERVE_LOG_ACTIVE_CONFIG_KEY] is True
        assert config["WEFT_MANAGER_SERVE_LOG_LEVEL"] == "debug"
        assert config["WEFT_MANAGER_SERVE_LOG_INTERVAL_SECONDS"] == 0.25

    def test_debug_setting(self) -> None:
        """Test debug environment variable."""
        # Values that should enable debug
        for value in ["1", "true", "yes", "debug", "TRUE", "Y"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["WEFT_DEBUG"] is True, (
                    f"Expected True for WEFT_DEBUG={value}"
                )

        # Values that should disable debug
        for value in ["", "0", "f", "F", "false", "False", "FALSE"]:
            with patch.dict(os.environ, {"WEFT_DEBUG": value}):
                config = load_config()
                assert config["WEFT_DEBUG"] is False, (
                    f"Expected False for WEFT_DEBUG={value}"
                )

        # Missing should be False
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["WEFT_DEBUG"] is False

    def test_logging_setting(self) -> None:
        """Test logging environment variable."""
        # Only "1" should enable logging
        with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": "1"}):
            config = load_config()
            assert config["WEFT_LOGGING_ENABLED"] is True

        # Any other value should be False
        for value in ["0", "true", "yes", "enabled", ""]:
            with patch.dict(os.environ, {"WEFT_LOGGING_ENABLED": value}):
                config = load_config()
                assert config["WEFT_LOGGING_ENABLED"] is False

        # Missing should be False
        with patch.dict(os.environ, {}, clear=True):
            config = load_config()
            assert config["WEFT_LOGGING_ENABLED"] is False

    def test_manager_reuse_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_MANAGER_REUSE_ENABLED": "0"}):
            config = load_config()
            assert config["WEFT_MANAGER_REUSE_ENABLED"] is False

        with patch.dict(os.environ, {"WEFT_MANAGER_REUSE_ENABLED": "true"}):
            config = load_config()
            assert config["WEFT_MANAGER_REUSE_ENABLED"] is True

    def test_weft_directory_name_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_DIRECTORY_NAME": ".engram"}, clear=True):
            config = load_config()

        assert config["WEFT_DIRECTORY_NAME"] == ".engram"
        assert config["BROKER_DEFAULT_DB_NAME"] == ".engram/broker.db"
        assert config["BROKER_PROJECT_CONFIG_PATH"] == ".engram"
        assert config["BROKER_PROJECT_CONFIG_NAME"] == "broker.toml"

    @pytest.mark.parametrize("value", ["", ".", "..", "foo/bar", "foo\\bar"])
    def test_weft_directory_name_env_rejects_invalid_values(self, value: str) -> None:
        with (
            patch.dict(os.environ, {"WEFT_DIRECTORY_NAME": value}, clear=True),
            pytest.raises(ValueError, match="WEFT_DIRECTORY_NAME"),
        ):
            load_config()

    def test_weft_logs_dir_env(self) -> None:
        with patch.dict(os.environ, {"WEFT_LOGS_DIR": "var/weft-logs"}, clear=True):
            config = load_config()

        assert config["WEFT_LOGS_DIR"] == "var/weft-logs"

    def test_weft_logs_dir_env_blanks_to_default(self) -> None:
        with patch.dict(os.environ, {"WEFT_LOGS_DIR": "   "}, clear=True):
            config = load_config()

        assert config["WEFT_LOGS_DIR"] is None

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

        assert config["WEFT_DIRECTORY_NAME"] == ".engram"
        assert config["BROKER_DEFAULT_DB_NAME"] == ".custom/weft.db"
        assert config["BROKER_PROJECT_CONFIG_PATH"] == ".engram"

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

        assert config["WEFT_DIRECTORY_NAME"] == ".engram"
        assert config["BROKER_PROJECT_CONFIG_PATH"] == ".custom"
        assert config["BROKER_PROJECT_CONFIG_NAME"] == "queues.toml"

    def test_compile_config_recomputes_broker_defaults_for_weft_overrides(self) -> None:
        with patch.dict(os.environ, {}, clear=True):
            config = compile_config({"WEFT_DIRECTORY_NAME": ".engram"})

        assert config["WEFT_DIRECTORY_NAME"] == ".engram"
        assert config["BROKER_DEFAULT_DB_NAME"] == ".engram/broker.db"
        assert config["BROKER_PROJECT_CONFIG_PATH"] == ".engram"

    def test_compile_config_rejects_ambiguous_postgres_override_shapes(self) -> None:
        with (
            patch.dict(os.environ, {}, clear=True),
            pytest.raises(ValueError, match="ambiguous"),
        ):
            compile_config(
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
            assert config["WEFT_MANAGER_LIFETIME_TIMEOUT"] == 42.5

    @pytest.mark.parametrize(
        "value",
        ["-1", "true", "junk"],
    )
    def test_manager_timeout_env_rejects_invalid_values(self, value: str) -> None:
        with (
            patch.dict(os.environ, {"WEFT_MANAGER_LIFETIME_TIMEOUT": value}),
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

        assert config["BROKER_BACKEND"] == "postgres"
        assert config["BROKER_BACKEND_HOST"] == "db.example.com"
        assert config["BROKER_BACKEND_PORT"] == 5433
        assert config["BROKER_BACKEND_USER"] == "broker"
        assert config["BROKER_BACKEND_PASSWORD"] == "secret"
        assert config["BROKER_BACKEND_DATABASE"] == "simplebroker_app"
        assert config["BROKER_BACKEND_SCHEMA"] == "broker_schema"
        assert config["BROKER_BACKEND_TARGET"] == ""
        assert config["BROKER_AUTO_VACUUM_INTERVAL"] == 100
        assert isinstance(config["BROKER_AUTO_VACUUM_INTERVAL"], int)

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

        assert config["BROKER_BACKEND"] == "postgres"
        assert (
            config["BROKER_BACKEND_TARGET"]
            == "postgresql://broker@db.example.com/simplebroker"
        )
        assert config["BROKER_BACKEND_SCHEMA"] == "broker_schema"

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

    def test_all_config_keys_present(self) -> None:
        """Test that Weft returns its own keys plus a full broker config."""
        config = load_config()

        weft_keys = {
            "WEFT_DEBUG",
            "WEFT_LOGGING_ENABLED",
            "WEFT_REDACT_TASKSPEC_FIELDS",
            "WEFT_DIRECTORY_NAME",
            "WEFT_LOGS_DIR",
            "WEFT_MANAGER_REUSE_ENABLED",
            "WEFT_MANAGER_LIFETIME_TIMEOUT",
            "WEFT_AUTOSTART_TASKS",
        }

        broker_keys = {
            "BROKER_PROJECT_SCOPE",
            "BROKER_DEFAULT_DB_NAME",
            "BROKER_PROJECT_CONFIG_PATH",
            "BROKER_PROJECT_CONFIG_NAME",
            "BROKER_DEBUG",
            "BROKER_LOGGING_ENABLED",
            "BROKER_AUTO_VACUUM",
            "BROKER_AUTO_VACUUM_INTERVAL",
            "BROKER_MAX_MESSAGE_SIZE",
            "BROKER_BACKEND",
            "BROKER_BACKEND_PORT",
        }

        assert weft_keys.issubset(config.keys())
        assert broker_keys.issubset(config.keys())
        assert config["BROKER_PROJECT_SCOPE"] is True
        assert config["BROKER_DEFAULT_DB_NAME"] == ".weft/broker.db"
        assert config["BROKER_PROJECT_CONFIG_PATH"] == ".weft"
        assert config["BROKER_PROJECT_CONFIG_NAME"] == "broker.toml"
        assert config["BROKER_DEBUG"] == config["WEFT_DEBUG"]
        assert config["BROKER_LOGGING_ENABLED"] == config["WEFT_LOGGING_ENABLED"]
        assert isinstance(config["BROKER_AUTO_VACUUM"], int)
        assert isinstance(config["BROKER_AUTO_VACUUM_INTERVAL"], int)
        assert isinstance(config["BROKER_MAX_MESSAGE_SIZE"], int)
        assert isinstance(config["BROKER_BACKEND_PORT"], int)
        assert config["WEFT_REDACT_TASKSPEC_FIELDS"] == ""
        assert config["WEFT_DIRECTORY_NAME"] == WEFT_DIRECTORY_NAME_DEFAULT
        assert config["WEFT_LOGS_DIR"] is None
        assert config["WEFT_MANAGER_LIFETIME_TIMEOUT"] == WEFT_MANAGER_LIFETIME_TIMEOUT
        assert config["WEFT_MANAGER_REUSE_ENABLED"] == WEFT_MANAGER_REUSE_ENABLED
        assert config["WEFT_AUTOSTART_TASKS"] == WEFT_AUTOSTART_TASKS_DEFAULT

    def test_config_immutability(self) -> None:
        """Test that modifying returned config doesn't affect subsequent calls."""
        config1 = load_config()
        original_debug = config1["WEFT_DEBUG"]

        # Modify the returned config
        config1["WEFT_DEBUG"] = not original_debug

        # Get a new config
        config2 = load_config()

        # Should have original value, not modified one
        assert config2["WEFT_DEBUG"] == original_debug
        assert config2["WEFT_DEBUG"] != config1["WEFT_DEBUG"]
