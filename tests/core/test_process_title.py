"""Process-local title lifecycle and native handoff contracts [CC-2.4]."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import types
from typing import cast

import pytest

from tests.helpers.typing import record_and_return
from weft.core import deferred, process_title
from weft.core.taskspec import StateSection, TaskSpec

pytestmark = pytest.mark.shared


def test_memoizes_success_but_retries_failed_update(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    def apply(title: str) -> None:
        calls.append(title)
        if len(calls) == 1:
            raise OSError("title unavailable")

    monkeypatch.setattr(process_title.sys, "platform", "linux")
    monkeypatch.setattr(
        deferred, "get_setproctitle", lambda: types.SimpleNamespace(setproctitle=apply)
    )
    owner = process_title._ProcessTitle()
    assert owner.set("weft-test") is not None
    assert owner.set("weft-test") is None
    assert owner.set("weft-test") is None
    assert calls == ["weft-test", "weft-test"]


@pytest.mark.parametrize("jitter", [0.0, 1.0, 2.0])
def test_deadline_handoff_uses_latest_and_bypasses_memoization(
    monkeypatch: pytest.MonkeyPatch, jitter: float
) -> None:
    calls: list[tuple[str, object]] = []
    now = [10.0]
    monkeypatch.setattr(process_title.sys, "platform", "darwin")
    monkeypatch.delitem(process_title.sys.modules, "setproctitle", raising=False)
    monkeypatch.setattr(process_title.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(process_title.random, "uniform", lambda a, b: jitter)
    monkeypatch.setattr(
        deferred,
        "get_processtitle",
        lambda: types.SimpleNamespace(
            prepare=lambda **kw: calls.append(("prepare", kw)),
            set_to=lambda title: record_and_return(calls, ("unix", title), True),
        ),
    )

    def stock() -> types.SimpleNamespace:
        calls.append(("import", None))
        return types.SimpleNamespace(
            setproctitle=lambda title: calls.append(("gui", title))
        )

    monkeypatch.setattr(deferred, "get_setproctitle", stock)
    owner = process_title._ProcessTitle()
    assert owner.set("weft:init") is None
    assert owner.seconds_until_due() == 1 + jitter
    assert owner.set("weft:running") is None
    assert owner.set("weft:running") is None
    assert owner.tick() is None
    assert not any(kind == "import" for kind, _ in calls)
    now[0] += 1 + jitter
    assert owner.tick() is None
    assert calls[-3:] == [
        ("unix", "weft:running".ljust(process_title.PROCESS_TITLE_HANDOFF_LENGTH)),
        ("import", None),
        ("gui", "weft:running"),
    ]
    assert owner.seconds_until_due() is None
    count = len(calls)
    assert owner.set("weft:running") is None
    assert owner.tick() is None
    assert len(calls) == count


@pytest.mark.parametrize("platform", ["linux", "win32", "freebsd"])
def test_direct_platform_has_no_deferred_backend(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    calls: list[str] = []
    monkeypatch.setattr(process_title.sys, "platform", platform)
    monkeypatch.setattr(
        deferred,
        "get_setproctitle",
        lambda: types.SimpleNamespace(setproctitle=calls.append),
    )
    monkeypatch.setattr(
        deferred, "get_processtitle", lambda: pytest.fail("unexpected Unix backend")
    )
    owner = process_title._ProcessTitle()
    assert owner.set("weft:running") is None
    assert owner.seconds_until_due() is None
    assert owner.tick() is None
    assert calls == ["weft:running"]


def test_already_imported_stock_is_sole_writer(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_title.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "setproctitle", types.ModuleType("setproctitle"))
    calls: list[str] = []
    monkeypatch.setattr(
        deferred,
        "get_setproctitle",
        lambda: types.SimpleNamespace(setproctitle=calls.append),
    )
    monkeypatch.setattr(
        deferred, "get_processtitle", lambda: pytest.fail("second writer")
    )
    owner = process_title._ProcessTitle()
    assert owner.set("weft:running") is None
    assert owner.seconds_until_due() is None
    assert calls == ["weft:running"]


def test_partial_import_failure_stops_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(process_title.sys, "platform", "darwin")
    monkeypatch.delitem(sys.modules, "setproctitle", raising=False)
    monkeypatch.setattr(
        deferred,
        "get_processtitle",
        lambda: types.SimpleNamespace(
            prepare=lambda **kw: None,
            set_to=lambda title: record_and_return(calls, title, True),
        ),
    )

    def broken_import() -> None:
        raise ImportError("native initialization failed")

    monkeypatch.setattr(deferred, "get_setproctitle", broken_import)
    owner = process_title._ProcessTitle()
    assert owner.set("weft:init") is None
    owner._deadline = 0
    assert "ImportError" in (owner.tick() or "")
    count = len(calls)
    assert owner.tick() is None
    assert owner.set("weft:running") is None
    assert len(calls) == count
    assert owner.seconds_until_due() is None


@pytest.mark.parametrize(
    "failure", [ImportError("missing"), RuntimeError("already prepared")]
)
def test_initialization_failure_is_nonfatal_and_not_repeated(
    monkeypatch: pytest.MonkeyPatch, failure: Exception
) -> None:
    attempts: list[None] = []

    def unavailable() -> None:
        attempts.append(None)
        raise failure

    monkeypatch.setattr(process_title.sys, "platform", "darwin")
    monkeypatch.delitem(sys.modules, "setproctitle", raising=False)
    monkeypatch.setattr(deferred, "get_processtitle", unavailable)
    owner = process_title._ProcessTitle()
    assert owner.set("weft:init") is not None
    assert owner.set("weft:running") is None
    assert len(attempts) == 1
    assert owner.seconds_until_due() is None


def test_false_native_return_is_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(process_title.sys, "platform", "linux")
    monkeypatch.setattr(
        deferred,
        "get_setproctitle",
        lambda: types.SimpleNamespace(setproctitle=lambda title: False),
    )
    assert "reported failure" in (process_title._ProcessTitle().set("weft:test") or "")


# Use the PG suite's whole-test watchdog, not a LaunchServices latency SLA.
@pytest.mark.timeout(900, method="signal")
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native handoff")
def test_real_native_handoff_preserves_full_weft_titles() -> None:
    script = """
import json, sys
print("stage: importing runtime", file=sys.stderr, flush=True)
from types import SimpleNamespace
from weft.core import deferred, process_title
from weft.core.taskspec import StateSection, TaskSpec
from weft.core.tasks.base import BaseTask
from weft._constants import PROCESS_TITLE_MAX_LENGTH
assert "setproctitle" not in sys.modules
assert "processtitle" not in sys.modules
class FormatTask(BaseTask):
    def _build_queue_configs(self): pass
    def _handle_work_message(self, *args): pass
obj = object.__new__(FormatTask)
obj.taskspec = SimpleNamespace(name="abcdefghijklmnopqrst", spec=SimpleNamespace(weft_context="/tmp/projectx"))
obj.tid_short = "1234567890"
fmt = obj._format_process_title
owner = process_title._ProcessTitle()
initial = fmt("init")
print("stage: initial Unix title", file=sys.stderr, flush=True)
assert owner.set(initial) is None
assert "setproctitle" not in sys.modules
assert deferred.get_processtitle() is deferred.get_processtitle()
print(json.dumps(initial), flush=True)
sys.stdin.readline()
owner._deadline = 0
print("stage: GUI handoff", file=sys.stderr, flush=True)
assert owner.tick() is None
assert deferred.get_setproctitle() is deferred.get_setproctitle()
for status, detail in [("running", None), ("completed", "123456789012345"), ("init", None), ("cancelled", "123456789012345")]:
    title = fmt(status, detail)
    if detail:
        assert len(title) == PROCESS_TITLE_MAX_LENGTH
    print(f"stage: native update {status}", file=sys.stderr, flush=True)
    assert owner.set(title) is None
    assert deferred.get_setproctitle().getproctitle() == title
    print(json.dumps(title), flush=True)
    sys.stdin.readline()
"""
    env = dict(os.environ)
    env.pop("SPT_NOENV", None)
    child = subprocess.Popen(
        [sys.executable, "-c", script],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )
    try:
        assert child.stdout is not None and child.stdin is not None
        for _ in range(5):
            line = child.stdout.readline()
            assert line, child.stderr.read() if child.stderr else "child exited"
            title = json.loads(line)
            observed = subprocess.check_output(
                ["/bin/ps", "-ww", "-p", str(child.pid), "-o", "command="], text=True
            ).strip()
            assert observed == title
            child.stdin.write("continue\n")
            child.stdin.flush()
        assert child.wait(timeout=10) == 0
    finally:
        if child.poll() is None:
            child.kill()
        _, stderr = child.communicate()
        if child.returncode:
            print(f"Native child stages before failure:\n{stderr}")


@pytest.mark.parametrize("fail_padding", [True, False])
def test_handoff_setter_failure_keeps_correct_writer(
    monkeypatch: pytest.MonkeyPatch, fail_padding: bool
) -> None:
    calls: list[tuple[str, str]] = []
    failed = [False]
    monkeypatch.setattr(process_title.sys, "platform", "darwin")
    monkeypatch.delitem(sys.modules, "setproctitle", raising=False)

    def unix(title: str) -> bool:
        calls.append(("unix", title))
        if fail_padding and title.endswith(" "):
            raise OSError("padding failed")
        return True

    def stock(title: str) -> None:
        calls.append(("stock", title))
        if not failed[0]:
            failed[0] = True
            raise OSError("title failed")

    monkeypatch.setattr(
        deferred,
        "get_processtitle",
        lambda: types.SimpleNamespace(prepare=lambda **kw: None, set_to=unix),
    )
    monkeypatch.setattr(
        deferred, "get_setproctitle", lambda: types.SimpleNamespace(setproctitle=stock)
    )
    owner = process_title._ProcessTitle()
    assert owner.set("weft:init") is None
    owner._deadline = 0
    assert owner.tick() is not None
    assert owner.seconds_until_due() is None
    if fail_padding:
        assert owner.set("weft:running") is None
        assert calls[-1] == ("unix", "weft:running")
        assert not any(kind == "stock" for kind, _ in calls)
    else:
        assert owner.set("weft:init") is None
        assert calls[-1] == ("stock", "weft:init")
        count = len(calls)
        assert owner.set("weft:init") is None
        assert len(calls) == count


def test_diagnostic_roundtrip_and_summary_preserve_execution_error() -> None:
    # Schema ownership: native observability failures are not execution errors.
    state = StateSection(error="execution error", process_title_error="native error")
    assert StateSection().process_title_error is None
    assert StateSection.model_validate_json(state.model_dump_json()) == state
    summary = TaskSpec.to_log_dict(
        cast(
            TaskSpec,
            types.SimpleNamespace(
                tid="1234567890123456789",
                name="test",
                state=state,
                get_runtime_seconds=lambda: None,
                metadata={},
            ),
        )
    )
    assert summary["error"] == "execution error"
    assert summary["process_title_error"] == "native error"
