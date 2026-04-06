"""Tests for backend-aware Weft test provisioning helpers."""

from __future__ import annotations

from pathlib import Path

import pytest

from tests.helpers.test_backend import cleanup_prepared_roots, prepare_project_root
from weft.context import build_context

pytestmark = [pytest.mark.shared]


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
