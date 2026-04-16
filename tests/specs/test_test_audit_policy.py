"""Tests for explicit test-audit classification policy."""

from __future__ import annotations

from pathlib import Path

import pytest

import tests.conftest as shared_conftest

pytestmark = [pytest.mark.shared]

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_taskspec_and_spec_contract_modules_are_marked_shared() -> None:
    expected = {
        "tests/taskspec/test_taskspec.py",
        "tests/specs/message_flow/test_agent_spawning_transition.py",
        "tests/specs/message_flow/test_spawning_transition.py",
        "tests/specs/resource_management/test_monitor_compat.py",
        "tests/specs/resource_management/test_resource_limit_killed.py",
        "tests/specs/resource_management/test_resource_metrics.py",
        "tests/specs/resource_management/test_timeout_return_code.py",
        "tests/specs/taskspec/test_agent_taskspec.py",
        "tests/specs/taskspec/test_peak_metrics.py",
        "tests/specs/taskspec/test_process_target.py",
        "tests/specs/taskspec/test_state_transitions.py",
    }

    assert expected.issubset(shared_conftest._SHARED_MODULES)


def _modules_in(directory: str) -> set[str]:
    return {
        path.relative_to(REPO_ROOT).as_posix()
        for path in (REPO_ROOT / directory).glob("test_*.py")
    }


def test_core_modules_are_explicitly_classified_shared() -> None:
    assert _modules_in("tests/core").issubset(shared_conftest._SHARED_MODULES)


def test_task_modules_are_explicitly_classified_without_prefix_exemptions() -> None:
    explicit_file_marks = {
        "tests/tasks/test_command_runner_parity.py",
        "tests/tasks/test_task_stop_sqlite_only.py",
        "tests/tasks/test_tasks_simple.py",
    }
    classified = (
        set(shared_conftest._SHARED_MODULES)
        | set(shared_conftest._SQLITE_ONLY_MODULES)
        | explicit_file_marks
    )

    assert _modules_in("tests/tasks").issubset(classified)


def test_broad_unaudited_prefix_exemptions_are_gone() -> None:
    assert shared_conftest._UNAUDITED_PATH_PREFIX_REASONS == {}


def test_unaudited_module_allowlist_entries_have_reasons() -> None:
    assert shared_conftest._UNAUDITED_MODULE_ALLOWLIST_REASONS
    assert all(
        reason.strip()
        for reason in shared_conftest._UNAUDITED_MODULE_ALLOWLIST_REASONS.values()
    )


def test_classification_tables_do_not_overlap() -> None:
    shared = set(shared_conftest._SHARED_MODULES)
    sqlite_only = set(shared_conftest._SQLITE_ONLY_MODULES)
    allowlist = set(shared_conftest._UNAUDITED_MODULE_ALLOWLIST_REASONS)

    assert shared.isdisjoint(sqlite_only)
    assert shared.isdisjoint(allowlist)
    assert sqlite_only.isdisjoint(allowlist)
