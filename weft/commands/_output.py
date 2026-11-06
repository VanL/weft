"""Text result delivery for caller-owned spills ([MF-2.1], [CLI-1.2.2])."""

from __future__ import annotations

import hashlib
import json
import os
import socket
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from weft._constants import SPILL_OUTPUT_READ_SIZE
from weft.core.outbox import is_large_output_reference


def _write_stdout_bytes(data: bytes) -> None:
    """Write a complete block even when stdout is an unbuffered file [CLI-1.2.2]."""
    remaining = memoryview(data)
    while remaining:
        written = sys.stdout.buffer.write(remaining)
        if written is None or written <= 0:
            raise OSError("stdout did not accept output")
        remaining = remaining[written:]


def write_command_stdout(text: str) -> None:
    """Deliver UTF-8 command text without platform newline conversion [MF-2]."""
    sys.stdout.flush()
    _write_stdout_bytes(text.encode("utf-8", errors="replace"))
    sys.stdout.flush()


def _deliver_spill(
    value: dict[str, Any], outputs_root: str | None, *, nl: bool
) -> bool:
    path = value["path"]
    hostname = value.get("hostname")
    if hostname is not None and hostname != socket.gethostname():
        raise OSError(f"output is only available on host {hostname} at {path}")
    try:
        resolved = Path(path).resolve()
        roots = [Path(tempfile.gettempdir()) / "weft" / "outputs"]
        if outputs_root is not None:
            roots.append(Path(outputs_root))
        if not any(
            resolved != root.resolve() and resolved.is_relative_to(root.resolve())
            for root in roots
        ):
            return False
    except (OSError, ValueError, RuntimeError) as exc:
        raise OSError(f"incomplete output at {path}: {exc}") from exc
    _stream_spill(value, resolved, nl=nl)
    return True


def _stream_spill(value: dict[str, Any], resolved: Path, *, nl: bool) -> None:
    """Transfer and verify one recognized file, including stdout closure."""
    path = value["path"]
    try:
        digest = hashlib.sha256()
        size = 0
        sys.stdout.flush()
        with resolved.open("rb") as source:
            while chunk := source.read(SPILL_OUTPUT_READ_SIZE):
                digest.update(chunk)
                size += len(chunk)
                _write_stdout_bytes(chunk)
        if size != value["size"]:
            raise OSError(f"size mismatch: expected {value['size']}, read {size}")
        if digest.hexdigest() != value["sha256"].lower():
            raise OSError("SHA-256 mismatch")
        if nl:
            sys.stdout.write("\n")
        sys.stdout.flush()
    except BrokenPipeError as exc:
        # Prevent Python's final stdout flush from replacing exit 1 with 120.
        with open(os.devnull, "wb") as sink:
            os.dup2(sink.fileno(), sys.stdout.fileno())
        raise OSError(f"incomplete output at {path}: {exc}") from exc
    except (OSError, ValueError) as exc:
        raise OSError(f"incomplete output at {path}: {exc}") from exc


def _render_output_value(
    value: Any,
    *,
    outputs_root: str | None,
    nl: bool,
    emit_text: Callable[[str, bool], None],
    command_stdout: bool = False,
) -> None:
    if is_large_output_reference(value) and _deliver_spill(value, outputs_root, nl=nl):
        return
    if value is not None:
        text = (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else str(value)
        )
        if command_stdout:
            write_command_stdout(text)
        else:
            emit_text(text, nl)


def render_text_output(
    value: Any,
    *,
    outputs_root: str | None,
    nl: bool = True,
    emit_text: Callable[[str, bool], None],
    command_stdout: bool = False,
) -> None:
    """Deliver only top-level references, preserving nested values [MF-2.1]."""
    if isinstance(value, list) and (
        command_stdout or any(is_large_output_reference(item) for item in value)
    ):
        for item in value:
            _render_output_value(
                item,
                outputs_root=outputs_root,
                nl=not command_stdout,
                emit_text=emit_text,
                command_stdout=command_stdout,
            )
        return
    _render_output_value(
        value,
        outputs_root=outputs_root,
        nl=nl and not command_stdout,
        emit_text=emit_text,
        command_stdout=command_stdout,
    )
