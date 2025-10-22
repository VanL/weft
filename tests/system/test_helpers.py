"""Tests for helper utilities in weft.helpers."""

from __future__ import annotations

import json
import logging
import os
import threading
from io import StringIO
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from weft.helpers import (  # noqa: D401 - module already documented
    CommandNotFoundError,
    debug_print,
    format_tid,
    is_debug_enabled,
    is_logging_enabled,
    log_critical,
    log_debug,
    log_error,
    log_exception,
    log_info,
    log_warning,
    parse_tid,
    reload_config,
    resolve_cli_command,
    send_log,
    write_file_atomically,
    write_json_atomically,
)


@pytest.fixture(autouse=True)
def _reset_config() -> Any:
    """Ensure helpers observe fresh configuration for every test."""
    reload_config()
    yield
    reload_config()


class TestSendLog:
    """Tests for send_log and related logging helpers."""

    def test_send_log_disabled(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "0"}):
            reload_config()
            with caplog.at_level(logging.DEBUG):
                send_log("should-be-silent")

        assert caplog.records == []

    def test_send_log_enabled(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            with caplog.at_level(logging.DEBUG):
                send_log("debug message", level=logging.DEBUG)
                send_log("info message")
                send_log("error message", level=logging.ERROR)

        assert [record.levelno for record in caplog.records] == [
            logging.DEBUG,
            logging.INFO,
            logging.ERROR,
        ]
        assert [record.getMessage() for record in caplog.records] == [
            "debug message",
            "info message",
            "error message",
        ]

    def test_send_log_custom_logger(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            with caplog.at_level(logging.INFO):
                send_log("custom", logger_name="weft.custom")

        assert len(caplog.records) == 1
        assert caplog.records[0].name == "weft.custom"
        assert caplog.records[0].getMessage() == "custom"

    def test_send_log_with_kwargs(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            with caplog.at_level(logging.ERROR):
                try:
                    raise RuntimeError("boom")
                except RuntimeError:
                    send_log("captured", level=logging.ERROR, exc_info=True)

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.ERROR
        assert record.exc_info is not None


class TestDebugPrint:
    """Tests for debug_print respecting the WEFT_DEBUG flag."""

    def test_debug_print_disabled(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": ""}):
            reload_config()
            debug_print("ignored", "message")

        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == ""

    def test_debug_print_enabled_to_stderr(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": "1"}):
            reload_config()
            debug_print("visible output")

        captured = capsys.readouterr()
        assert captured.err == "visible output\n"
        assert captured.out == ""

    def test_debug_print_multiple_args(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": "1"}):
            reload_config()
            debug_print("value:", 42, "status:", True)

        captured = capsys.readouterr()
        assert captured.err == "value: 42 status: True\n"

    def test_debug_print_custom_separator(
        self, capsys: pytest.CaptureFixture[str]
    ) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": "1"}):
            reload_config()
            debug_print("A", "B", "C", sep="-")

        captured = capsys.readouterr()
        assert captured.err == "A-B-C\n"

    def test_debug_print_custom_end(self, capsys: pytest.CaptureFixture[str]) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": "1"}):
            reload_config()
            debug_print("line1", end=" ")
            debug_print("line2")

        captured = capsys.readouterr()
        assert captured.err == "line1 line2\n"

    def test_debug_print_to_file_object(self) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": "1"}):
            reload_config()
            buffer = StringIO()
            debug_print("file output", file=buffer)

        assert buffer.getvalue() == "file output\n"


class TestLogConvenienceFunctions:
    """Tests exercising the convenience logging helpers."""

    def test_log_level_helpers(self, caplog: pytest.LogCaptureFixture) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            with caplog.at_level(logging.DEBUG):
                log_debug("debug")
                log_info("info")
                log_warning("warn")
                log_error("error")
                log_critical("critical")

        assert [record.levelno for record in caplog.records] == [
            logging.DEBUG,
            logging.INFO,
            logging.WARNING,
            logging.ERROR,
            logging.CRITICAL,
        ]

    def test_log_exception_records_exc_info(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            with caplog.at_level(logging.ERROR):
                try:
                    raise ValueError("failure")
                except ValueError:
                    log_exception("processing failed")

        assert len(caplog.records) == 1
        record = caplog.records[0]
        assert record.levelno == logging.ERROR
        assert record.getMessage() == "processing failed"
        assert record.exc_info is not None


class TestTidUtilities:
    """Tests for the TID formatting helpers."""

    def test_format_tid_handles_str_and_int(self) -> None:
        assert format_tid("123") == "T123"
        assert format_tid(456) == "T456"

    def test_parse_tid_valid(self) -> None:
        assert parse_tid("T123456") == "123456"

    @pytest.mark.parametrize("value", ["123456", "X123", ""])
    def test_parse_tid_invalid(self, value: str) -> None:
        with pytest.raises(ValueError):
            parse_tid(value)


class TestConfigurationUtilities:
    """Tests for configuration queries and reload behaviour."""

    def test_is_logging_enabled(self) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()
            assert is_logging_enabled() is True

        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "0"}):
            reload_config()
            assert is_logging_enabled() is False

    def test_is_debug_enabled(self) -> None:
        with patch.dict("os.environ", {"WEFT_DEBUG": "1"}):
            reload_config()
            assert is_debug_enabled() is True

        with patch.dict("os.environ", {"WEFT_DEBUG": ""}):
            reload_config()
            assert is_debug_enabled() is False

    def test_reload_config_reflects_environment_changes(self) -> None:
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "0", "WEFT_DEBUG": ""}):
            reload_config()
            assert is_logging_enabled() is False
            assert is_debug_enabled() is False

        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1", "WEFT_DEBUG": "1"}):
            reload_config()
            assert is_logging_enabled() is True
            assert is_debug_enabled() is True


class TestAtomicFileWriting:
    """Tests for write_file_atomically covering success and fallback paths."""

    def test_write_file_atomically_text_success(self, tmp_path: Path) -> None:
        target = tmp_path / "example.txt"
        write_file_atomically(target, content="hello world")
        assert target.read_text(encoding="utf-8") == "hello world"

    def test_write_file_atomically_binary_success(self, tmp_path: Path) -> None:
        target = tmp_path / "example.bin"
        payload = b"\x00\x01\x02"
        write_file_atomically(target, data=payload)
        assert target.read_bytes() == payload

    def test_write_file_atomically_creates_parent_directories(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "nested" / "dir" / "file.txt"
        write_file_atomically(target, content="nested")
        assert target.read_text() == "nested"

    def test_write_file_atomically_custom_encoding(self, tmp_path: Path) -> None:
        target = tmp_path / "encoded.txt"
        write_file_atomically(target, content="encoding test", encoding="utf-16")
        assert target.read_text(encoding="utf-16") == "encoding test"

    def test_write_file_atomically_fallback_on_replace_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        target = tmp_path / "fallback.txt"
        with patch.dict("os.environ", {"WEFT_LOGGING_ENABLED": "1"}):
            reload_config()

            def replace_fail(
                self: Path, target_path: Path
            ) -> Path:  # pragma: no cover - simple stub
                raise OSError("replace failed")

            monkeypatch.setattr(Path, "replace", replace_fail)
            with caplog.at_level(logging.WARNING):
                write_file_atomically(target, content="fallback content")

        assert target.read_text() == "fallback content"
        assert not list(tmp_path.glob("*.tmp"))
        assert any(
            "Atomic write failed" in record.getMessage() for record in caplog.records
        )

    def test_write_file_atomically_invalid_args(self, tmp_path: Path) -> None:
        target = tmp_path / "invalid.txt"
        with pytest.raises(ValueError):
            write_file_atomically(target, content="text", data=b"bytes")
        with pytest.raises(ValueError):
            write_file_atomically(target)


class TestWriteJsonAtomically:
    """Tests for write_json_atomically ensuring JSON integrity."""

    def test_write_json_atomically_success(self, tmp_path: Path) -> None:
        target = tmp_path / "config.json"
        data = {"value": 42, "nested": {"flag": True}}
        write_json_atomically(target, data)
        assert json.loads(target.read_text()) == data

    def test_write_json_atomically_creates_parent_dirs(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "config" / "settings.json"
        data = {"name": "weft"}
        write_json_atomically(target, data)
        assert json.loads(target.read_text()) == data

    def test_write_json_atomically_fallback_on_replace_error(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        target = tmp_path / "config.json"

        def replace_fail(
            self: Path, target_path: Path
        ) -> Path:  # pragma: no cover - simple stub
            raise OSError("replace failed")

        monkeypatch.setattr(Path, "replace", replace_fail)
        write_json_atomically(target, {"status": "ok"})
        assert json.loads(target.read_text()) == {"status": "ok"}

    def test_write_json_atomically_handles_concurrent_writes(
        self, tmp_path: Path
    ) -> None:
        target = tmp_path / "concurrent.json"
        written = []

        def writer(idx: int) -> None:
            payload = {"thread": idx}
            write_json_atomically(target, payload)
            written.append(idx)

        threads = [threading.Thread(target=writer, args=(i,)) for i in range(5)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert len(written) == 5
        final_data = json.loads(target.read_text())
        assert final_data["thread"] in range(5)


class TestResolveCliCommand:
    """Tests for resolving CLI commands to executable paths."""

    @staticmethod
    def _make_dummy_executable(tmp_path: Path) -> tuple[Path, str]:
        base_name = "weft-test-command"
        if os.name == "nt":
            file_name = f"{base_name}.bat"
            command_name = base_name
            script = "@echo off\nexit /B 0\n"
        else:
            file_name = base_name
            command_name = base_name
            script = "#!/bin/sh\nexit 0\n"

        path = tmp_path / file_name
        path.write_text(script, encoding="utf-8")
        try:
            path.chmod(path.stat().st_mode | 0o111)
        except PermissionError:  # pragma: no cover - Windows may not permit chmod
            pass

        return path, command_name

    def test_resolves_using_environment_path(self, tmp_path: Path) -> None:
        executable, command = self._make_dummy_executable(tmp_path)
        extra_path = str(tmp_path)
        with patch.dict("os.environ", {"PATH": extra_path}):
            resolved = resolve_cli_command(command)
        assert Path(resolved).resolve() == executable.resolve()

    def test_resolves_with_custom_search_path(self, tmp_path: Path) -> None:
        executable, command = self._make_dummy_executable(tmp_path)
        resolved = resolve_cli_command(command, search_path=str(tmp_path))
        assert Path(resolved).resolve() == executable.resolve()

    def test_raises_for_missing_command(self) -> None:
        missing = "weft-command-that-does-not-exist"
        with pytest.raises(CommandNotFoundError) as excinfo:
            resolve_cli_command(missing)
        assert missing in str(excinfo.value)

    def test_rejects_blank_commands(self) -> None:
        with pytest.raises(ValueError):
            resolve_cli_command("   ")
