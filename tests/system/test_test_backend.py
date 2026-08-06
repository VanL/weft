"""Tests for backend-aware Weft test provisioning helpers."""

from __future__ import annotations

import logging
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from tests.helpers.test_backend import cleanup_prepared_roots, prepare_project_root
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

    def cleanup_target(
        _dsn: str,
        *,
        backend_options: dict[str, str],
    ) -> None:
        if backend_options["schema"] == "failed_schema":
            raise RuntimeError("cleanup failed")

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
