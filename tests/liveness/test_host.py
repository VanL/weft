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
    def title_read() -> list[str]:
        raise AssertionError("title must not be read")

    process = SimpleNamespace(
        create_time=lambda: 12.5,
        status=lambda: psutil.STATUS_SLEEPING,
        cmdline=title_read,
    )
    monkeypatch.setattr(host.psutil, "Process", lambda _pid: process)

    observation = host.inspect_host_process(123, 12.5)
    assert observation.evidence == "live"
    assert observation.reason == "identity_match"


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
