"""Tests for generic host-process liveness inspection."""

from __future__ import annotations

from types import SimpleNamespace

import psutil
import pytest

from weft.liveness import host

pytestmark = [pytest.mark.shared]


def test_host_inspection_accepts_matching_non_zombie_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process = SimpleNamespace(
        create_time=lambda: 12.5, status=lambda: psutil.STATUS_SLEEPING
    )
    monkeypatch.setattr(host.psutil, "Process", lambda _pid: process)

    assert host.inspect_host_process(123, 12.5).evidence == "live"


@pytest.mark.parametrize(
    ("title", "reason"),
    [
        (
            "weft-project-0000000001:consumer:running",
            "identity_match_title_match",
        ),
        ("python worker.py", "identity_match_title_unconfirmed"),
    ],
)
def test_host_inspection_uses_process_title_only_as_corroboration(
    monkeypatch: pytest.MonkeyPatch,
    title: str,
    reason: str,
) -> None:
    process = SimpleNamespace(
        create_time=lambda: 12.5,
        status=lambda: psutil.STATUS_SLEEPING,
        cmdline=lambda: [title],
    )
    monkeypatch.setattr(host.psutil, "Process", lambda _pid: process)

    observation = host.inspect_host_process(
        123,
        12.5,
        expected_tid="1779000000000000001",
    )

    assert observation.evidence == "live"
    assert observation.reason == reason


@pytest.mark.parametrize(
    ("process", "reason"),
    [
        (
            SimpleNamespace(
                create_time=lambda: 13.0,
                status=lambda: psutil.STATUS_SLEEPING,
            ),
            "identity_mismatch",
        ),
        (
            SimpleNamespace(
                create_time=lambda: 12.5,
                status=lambda: psutil.STATUS_ZOMBIE,
            ),
            "process_zombie",
        ),
    ],
)
def test_host_inspection_rejects_reused_or_zombie_process(
    monkeypatch: pytest.MonkeyPatch,
    process: object,
    reason: str,
) -> None:
    monkeypatch.setattr(host.psutil, "Process", lambda _pid: process)
    observation = host.inspect_host_process(123, 12.5)
    assert observation.evidence == "stale"
    assert observation.reason == reason


def test_host_inspection_distinguishes_absent_from_unresolved(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def missing(_pid: int) -> object:
        raise psutil.NoSuchProcess(_pid)

    monkeypatch.setattr(host.psutil, "Process", missing)
    assert host.inspect_host_process(123, 12.5).evidence == "stale"

    def denied(_pid: int) -> object:
        raise psutil.AccessDenied(_pid)

    monkeypatch.setattr(host.psutil, "Process", denied)
    assert host.inspect_host_process(123, 12.5).evidence == "unknown"
    assert host.inspect_host_process(123, None).evidence == "unknown"
