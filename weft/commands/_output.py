"""Text result delivery for caller-owned spills ([MF-2.1], [CLI-1.2.2])."""

from __future__ import annotations

import hashlib
import json
import os
import re
import socket
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from weft._constants import SPILL_OUTPUT_READ_SIZE


def _spill_shape(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and value.get("type") == "large_output"
        and isinstance(value.get("path"), str)
        and type(value.get("size")) is int
        and value["size"] >= 0
        and isinstance(value.get("sha256"), str)
        and re.fullmatch(r"[0-9a-fA-F]{64}", value["sha256"]) is not None
        and (
            "hostname" not in value
            or isinstance(value["hostname"], str)
            and bool(value["hostname"])
        )
    )


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
                sys.stdout.buffer.write(chunk)
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
) -> None:
    if _spill_shape(value) and _deliver_spill(value, outputs_root, nl=nl):
        return
    if value is not None:
        text = (
            json.dumps(value, ensure_ascii=False)
            if isinstance(value, (dict, list))
            else str(value)
        )
        emit_text(text, nl)


def render_text_output(
    value: Any,
    *,
    outputs_root: str | None,
    nl: bool = True,
    emit_text: Callable[[str, bool], None],
) -> None:
    """Deliver only top-level references, preserving nested values [MF-2.1]."""
    if isinstance(value, list) and any(_spill_shape(item) for item in value):
        for item in value:
            _render_output_value(
                item, outputs_root=outputs_root, nl=True, emit_text=emit_text
            )
        return
    _render_output_value(value, outputs_root=outputs_root, nl=nl, emit_text=emit_text)
