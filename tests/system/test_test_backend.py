"""Tests for backend-aware Weft test provisioning helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

import tests.helpers.test_backend as test_backend_module
from tests.helpers.test_backend import (
    cleanup_postgres_schema_for_root,
    cleanup_prepared_roots,
    prepare_project_root,
)
from weft.context import build_context

pytestmark = [pytest.mark.shared]


def _write_postgres_config(root: Path, schema: str) -> Path:
    config_path = root / "broker.toml"
    config_path.write_text(
        "\n".join(
            (
                "version = 1",
                'backend = "postgres"',
                "",
                "[backend_options]",
                f'schema = "{schema}"',
                "",
            )
        ),
        encoding="utf-8",
    )
    return config_path


def test_cleanup_prepared_roots_logs_failure_and_continues(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    failed_root = tmp_path / "failed"
    successful_root = tmp_path / "successful"
    failed_root.mkdir()
    successful_root.mkdir()
    failed_config = _write_postgres_config(failed_root, "failed_schema")
    successful_config = _write_postgres_config(successful_root, "successful_schema")

    plugin = Mock()
    private_error = "cleanup failed for private target"

    def cleanup_target(
        _dsn: str,
        *,
        backend_options: dict[str, str],
    ) -> None:
        if backend_options["schema"] == "failed_schema":
            raise RuntimeError(private_error)

    plugin.cleanup_target.side_effect = cleanup_target
    env = {
        "BROKER_TEST_BACKEND": "postgres",
        "SIMPLEBROKER_PG_TEST_DSN": "postgresql://test.invalid/weft",
    }

    with (
        patch.object(
            Path,
            "rglob",
            return_value=[failed_config, successful_config],
        ),
        patch(
            "tests.helpers.test_backend.get_backend_plugin",
            return_value=plugin,
        ),
        caplog.at_level(logging.WARNING, logger="tests.helpers.test_backend"),
    ):
        cleanup_prepared_roots(tmp_path, env=env)

    cleaned_schemas = [
        call.kwargs["backend_options"]["schema"]
        for call in plugin.cleanup_target.call_args_list
    ]
    assert cleaned_schemas == ["failed_schema", "successful_schema"]
    assert len(caplog.records) == 1
    record = caplog.records[0]
    assert record.message == "Failed to clean Postgres test schema"
    assert record.schema == "failed_schema"
    assert record.config_path == str(failed_config)
    assert record.exc_info is None
    assert private_error not in caplog.text
    assert "postgresql://test.invalid/weft" not in caplog.text


def test_cleanup_postgres_schema_for_root_logs_private_failure(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project_root = tmp_path / "project"
    plugin = Mock()
    private_error = "cleanup failed for private target"
    plugin.cleanup_target.side_effect = RuntimeError(private_error)
    env = {
        "BROKER_TEST_BACKEND": "postgres",
        "SIMPLEBROKER_PG_TEST_DSN": "postgresql://private.invalid/weft",
    }
    resolved_root = project_root.resolve()
    schema = test_backend_module.postgres_schema_for_root(resolved_root)
    cache_key = (str(resolved_root), env["SIMPLEBROKER_PG_TEST_DSN"], schema)
    prepared_roots = {cache_key}
    monkeypatch.setattr(
        test_backend_module,
        "_PREPARED_POSTGRES_ROOTS",
        prepared_roots,
    )

    with (
        patch(
            "tests.helpers.test_backend.get_backend_plugin",
            return_value=plugin,
        ),
        caplog.at_level(logging.WARNING, logger="tests.helpers.test_backend"),
    ):
        cleanup_postgres_schema_for_root(project_root, env=env)

    plugin.cleanup_target.assert_called_once()
    assert [record.message for record in caplog.records] == [
        "Failed to clean Postgres test schema"
    ]
    expected_schema = plugin.cleanup_target.call_args.kwargs["backend_options"][
        "schema"
    ]
    assert caplog.records[0].schema == expected_schema
    assert cache_key in prepared_roots
    assert caplog.records[0].exc_info is None
    assert str(project_root) not in caplog.text
    assert private_error not in caplog.text
    assert "postgresql://private.invalid/weft" not in caplog.text


def test_prepare_project_root_supports_context_queue_roundtrip(tmp_path: Path) -> None:
    root = prepare_project_root(tmp_path / "project")
    try:
        context = build_context(spec_context=root)
        queue = context.queue("backend.helper.roundtrip", persistent=True)
        queue.write("hello")
        assert queue.read() == "hello"
    finally:
        cleanup_prepared_roots(root.parent)


def test_prepare_project_root_isolates_distinct_roots(tmp_path: Path) -> None:
    source_root = prepare_project_root(tmp_path / "source")
    target_root = prepare_project_root(tmp_path / "target")

    try:
        source = build_context(spec_context=source_root)
        target = build_context(spec_context=target_root)

        source_queue = source.queue("backend.helper.isolation", persistent=True)
        source_queue.write("payload")

        target_queue = target.queue("backend.helper.isolation", persistent=True)
        assert target_queue.peek_one() is None
    finally:
        cleanup_prepared_roots(tmp_path)
