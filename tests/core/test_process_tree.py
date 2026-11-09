"""Descendant enumeration against psutil on the running platform (Spec: [RM-5.1])."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import psutil
import pytest

from tests.helpers.process_tree import spawn_tree
from weft.core.process_tree import descendant_processes

pytestmark = [pytest.mark.shared]


def test_descendant_processes_matches_psutil_for_a_two_level_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The walk finds every descendant psutil finds, grandchildren included.

    Half of each level is forked from a live non-main thread, which Linux
    records under that thread rather than the process's main thread. On
    macOS and Linux the walk must not fall back to psutil's host-wide scan,
    so that scan is made unavailable once psutil's answer is recorded.
    """

    tree = spawn_tree(tmp_path, depth=2, fanout=2)
    try:
        root = psutil.Process(tree.root.pid)
        expected = {child.pid for child in root.children(recursive=True)}
        assert expected == tree.descendant_pids
        if sys.platform in ("darwin", "linux"):

            def host_wide_scan(*_args: object, **_kwargs: object) -> None:
                raise AssertionError("descendant walk used psutil's host-wide scan")

            monkeypatch.setattr(psutil.Process, "children", host_wide_scan)

        walked = descendant_processes(root)

        assert {child.pid for child in walked} == expected
        assert len(walked) == len(expected)
    finally:
        tree.kill()


def test_descendant_processes_survives_a_stale_root_creation_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A root time cached before a backward clock correction hides no child.

    The root object reports a creation time an hour later than the kernel's,
    as one read before the wall clock was stepped back would. Its children
    then look older than it; psutil reads the root's time afresh and keeps
    them, and the walk must too.
    """

    tree = spawn_tree(tmp_path, depth=2, fanout=1)
    try:
        root = psutil.Process(tree.root.pid)
        stale_create_time = root.create_time() + 3600.0
        monkeypatch.setattr(root, "create_time", lambda: stale_create_time)
        expected = {child.pid for child in root.children(recursive=True)}
        assert expected == tree.descendant_pids

        walked = descendant_processes(root)

        assert {child.pid for child in walked} == expected
    finally:
        tree.kill()


def test_descendant_processes_rejects_a_root_that_has_exited() -> None:
    """An exited root raises NoSuchProcess instead of naming a PID's new owner."""

    child = subprocess.Popen([sys.executable, "-c", "pass"])
    root = psutil.Process(child.pid)
    child.wait(timeout=10.0)

    with pytest.raises(psutil.NoSuchProcess):
        descendant_processes(root)
