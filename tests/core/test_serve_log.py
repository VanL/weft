"""Tests for best-effort foreground manager operational logging."""

from __future__ import annotations

import io

import pytest

from weft.core import serve_log

pytestmark = [pytest.mark.shared]


def test_build_serve_log_record_formats_only_owned_broker_message_ids() -> None:
    record = serve_log.build_serve_log_record(
        config={"_serve_log_active": True, "level": "info"},
        event="spawn_reserved",
        component="manager",
        manager_tid="1779300000000000001",
        manager_tid_short="00000001",
        required_level="info",
        pid=42,
        fields={
            "message_timestamp": 1779300000000000002,
            "observed_timestamp": 1779300000000000003,
            "superseded_message_id": 1779300000000000004,
            "count": 3,
            "opaque": {"message_timestamp": 1779300000000000005},
        },
    )

    assert record["message_timestamp"] == "1779300000000000002"
    assert record["observed_timestamp"] == "1779300000000000003"
    assert record["superseded_message_id"] == "1779300000000000004"
    assert record["timestamp_ns"] > 0
    assert isinstance(record["timestamp_ns"], int)
    assert record["pid"] == 42
    assert record["count"] == 3
    assert record["opaque"]["message_timestamp"] == 1779300000000000005


def test_emit_serve_log_record_suppresses_unserializable_diagnostic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A record failure emits nothing and never escapes to runtime work."""

    stderr = io.StringIO()
    monkeypatch.setattr(serve_log.sys, "stderr", stderr)

    class ExplodingRecord(dict[str, str]):
        def items(self):
            raise RuntimeError("secret record contents")

    serve_log.emit_serve_log_record(ExplodingRecord(secret="must-not-leak"))

    assert stderr.getvalue() == ""
    assert "secret" not in stderr.getvalue()


def test_emit_serve_log_record_warns_without_record_data_when_stderr_write_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A replaceable stderr sink cannot fail manager runtime work or leak fields."""

    warnings: list[tuple[int, bytes]] = []

    class FailingStderr:
        def fileno(self) -> int:
            raise AttributeError("no descriptor")

        def write(self, value: str) -> int:
            raise RuntimeError(f"sink rejected {value}")

        def flush(self) -> None:
            raise AssertionError("write should fail first")

    monkeypatch.setattr(serve_log.sys, "stderr", FailingStderr())
    monkeypatch.setattr(
        serve_log.os,
        "write",
        lambda fd, payload: warnings.append((fd, payload)) or len(payload),
    )

    serve_log.emit_serve_log_record({"secret": "must-not-leak"})

    assert warnings == [(2, b"weft manager serve log output failed\n")]
    assert b"secret" not in warnings[0][1]


def test_emit_serve_log_record_ignores_low_level_warning_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Failure of the fixed fallback warning also cannot replace runtime work."""

    class FailingStderr:
        def fileno(self) -> int:
            raise AttributeError("no descriptor")

        def write(self, value: str) -> int:
            raise RuntimeError(f"sink rejected {value}")

    def fail_warning(fd: int, payload: bytes) -> int:
        raise OSError(fd, payload)

    monkeypatch.setattr(serve_log.sys, "stderr", FailingStderr())
    monkeypatch.setattr(serve_log.os, "write", fail_warning)

    serve_log.emit_serve_log_record({"event": "runtime-continues"})
