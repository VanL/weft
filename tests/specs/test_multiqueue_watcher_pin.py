"""Executable SHA-256 pin check for the downstream Taut copy of MultiQueueWatcher.

Spec references:
- docs/specifications/07-System_Invariants.md [QUEUE.8]
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
WATCHER_PATH = REPO_ROOT / "weft" / "core" / "tasks" / "multiqueue_watcher.py"
INVARIANTS_SPEC_PATH = REPO_ROOT / "docs" / "specifications" / "07-System_Invariants.md"
_DOWNSTREAM_COPY_SHA256_RE = re.compile(
    r"_Downstream exact copy_:.*?\bSHA-256(?:\s+is)?\s+`([0-9a-f]{64})`",
    re.DOTALL,
)

pytestmark = [pytest.mark.shared]


def test_multiqueue_watcher_sha256_matches_queue_8_downstream_pin() -> None:
    """[QUEUE.8] must record the exact whole-file SHA-256 of multiqueue_watcher.py."""

    spec_text = INVARIANTS_SPEC_PATH.read_text(encoding="utf-8")
    match = _DOWNSTREAM_COPY_SHA256_RE.search(spec_text)
    assert match is not None, (
        "07-System_Invariants.md [QUEUE.8] is missing the _Downstream exact copy_ "
        "SHA-256 digest"
    )
    pinned_digest = match.group(1)
    actual_digest = hashlib.sha256(WATCHER_PATH.read_bytes()).hexdigest()

    assert actual_digest == pinned_digest, (
        "weft/core/tasks/multiqueue_watcher.py SHA-256 drifted from "
        "07-System_Invariants.md [QUEUE.8] _Downstream exact copy_ pin; update the "
        "recorded digest and CHANGELOG together so downstream Taut can re-pin"
    )
