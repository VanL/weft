"""The lossless TID display encoding [OBS.5], [OBS.4], [CLI-1.2.3].

Spec references:
- docs/specifications/07-System_Invariants.md [OBS.4], [OBS.5]
- docs/specifications/10-CLI_Interface.md [CLI-1.2.3]
"""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from simplebroker import format_message_id
from tests.helpers.hypothesis_strategies import taskspec_tid_strings
from tests.helpers.typing import BrokerEnv
from tests.helpers.weft_harness import DEFAULT_TASK_COMPLETION_TIMEOUT, WeftTestHarness
from weft._constants import PROCESS_TITLE_MAX_LENGTH, PROCESS_TITLE_STATUSES
from weft.core.task_state import task_state_queue_name
from weft.core.tasks.base import BaseTask
from weft.core.taskspec import IOSection, SpecSection, TaskSpec
from weft.helpers import decode_tid, encode_tid, iter_queue_json_entries

pytestmark = [pytest.mark.shared]

_FULL_TIDS = st.integers(min_value=0, max_value=2**63 - 1).map(format_message_id)


@pytest.mark.property
@given(_FULL_TIDS)
def test_encoding_round_trips_every_tid_in_either_case(tid: str) -> None:
    encoded = encode_tid(tid)
    assert len(encoded) == 13
    assert encoded[0] in "01234567"
    assert decode_tid(encoded) == tid
    assert decode_tid(encoded.lower()) == tid


@pytest.mark.property
@given(_FULL_TIDS, _FULL_TIDS)
def test_fixed_width_encoding_keeps_tid_order(first: str, second: str) -> None:
    assert (encode_tid(first) < encode_tid(second)) == (int(first) < int(second))


def test_consecutive_low_order_integers_encode_distinctly() -> None:
    base = 1_760_000_000_000_000_000 & ~0xFFF
    tids = [format_message_id(base + offset) for offset in range(4096)]
    encoded = [encode_tid(tid) for tid in tids]
    assert len(set(encoded)) == 4096
    assert [decode_tid(value) for value in encoded] == tids


@pytest.mark.parametrize(
    "value,expected",
    [
        (0, "0000000000000"),
        (1, "0000000000001"),
        (1837025672140161024, "1JZKD3PKK0000"),
        (1779000000000000001, "1HC272C7PQ001"),
        (2**63 - 1, "7ZZZZZZZZZZZZ"),
    ],
)
def test_fixed_vectors(value: int, expected: str) -> None:
    tid = format_message_id(value)
    assert encode_tid(tid) == expected
    assert decode_tid(expected) == tid


@pytest.mark.parametrize("spelling", ["1jzkd3pkk0000", "1jZkD3pKk0000"])
def test_decoder_accepts_lower_and_mixed_case(spelling: str) -> None:
    assert spelling != "1JZKD3PKK0000"
    assert decode_tid(spelling) == "1837025672140161024"


def test_decoder_returns_a_full_tid_unchanged() -> None:
    assert decode_tid("1837025672140161024") == "1837025672140161024"


@pytest.mark.parametrize(
    "value", ["", "123", "x" * 19, "²" * 19, "9223372036854775808", 1]
)
def test_encoder_rejects_non_tids(value: object) -> None:
    with pytest.raises(ValueError):
        encode_tid(value)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "value",
    [
        "",
        "0123456789",
        "1JZKD3PKK000",
        "1JZKD3PKK00000",
        "1JZKD3PKK000I",
        "1JZKD3PKK000L",
        "1JZKD3PKK000O",
        "1JZKD3PKK000U",
        "1jzkd3pkk000i",
        "1jzkd3pkk000l",
        "1jzkd3pkk000o",
        "1jzkd3pkk000u",
        "1JZKD-PKK0000",
        "8000000000000",
        "１" * 13,
        "9223372036854775808",
    ],
)
def test_decoder_rejects_other_spellings(value: str) -> None:
    with pytest.raises(ValueError):
        decode_tid(value)


class _FormatTask(BaseTask):
    def _build_queue_configs(self) -> dict[str, dict[str, Any]]:  # pragma: no cover
        return {}

    def _handle_work_message(self, *args: object) -> None:  # pragma: no cover
        pass


@pytest.mark.property
@given(
    tid=taskspec_tid_strings(),
    name=st.text(max_size=40),
    details=st.none() | st.text(max_size=40),
    context=st.text(alphabet=st.characters(blacklist_characters="/\x00"), max_size=40),
    status=st.sampled_from(sorted(PROCESS_TITLE_STATUSES)),
)
def test_title_carries_the_complete_encoded_tid_within_budget(
    tid: str, name: str, details: str | None, context: str, status: str
) -> None:
    """The formatter never truncates or sanitizes the TID segment [OBS.4]."""

    task = object.__new__(_FormatTask)
    task.taskspec = SimpleNamespace(  # type: ignore[assignment]
        name=name, spec=SimpleNamespace(weft_context="/tmp/" + context)
    )
    task.tid_short = encode_tid(tid)
    title = task._format_process_title(status, details)
    assert PROCESS_TITLE_MAX_LENGTH == 71
    assert len(title) <= PROCESS_TITLE_MAX_LENGTH
    assert decode_tid(title.split(":", 1)[0].rsplit("-", 1)[1]) == tid


def test_consumer_publishes_and_matches_encoded_title(
    broker_env: BrokerEnv, weft_harness: WeftTestHarness
) -> None:
    """A real Consumer publishes the full encoding in its title and snapshot."""

    if importlib.util.find_spec("setproctitle") is None:
        pytest.skip("setproctitle is not installed")
    tid = str(time.time_ns())
    spec = TaskSpec(
        tid=tid,
        name="encoded-title",
        spec=SpecSection(
            type="function",
            function_target="tests.tasks.sample_targets:echo_payload",
            enable_process_title=False,
            weft_context=str(weft_harness.root),
        ),
        io=IOSection(
            inputs={"inbox": f"T{tid}.inbox"},
            outputs={"outbox": f"T{tid}.outbox"},
            control={"ctrl_in": f"T{tid}.ctrl_in", "ctrl_out": f"T{tid}.ctrl_out"},
        ),
    )
    # Native title libraries own process-global argv memory. Importing stock
    # setproctitle after another test started macOS's deferred writer bypasses
    # its padded handoff and can permanently truncate subsequent titles.
    script = """
import sys
import setproctitle
from weft.context import build_context
from weft.core.tasks import Consumer
from weft.core.taskspec import TaskSpec
from weft.helpers import encode_tid

spec = TaskSpec.model_validate_json(sys.stdin.read())
context = build_context(spec_context=spec.spec.weft_context)
task = Consumer(context.broker_target, spec)
try:
    task.enable_process_title = True
    task._update_process_title("running")
    assert (
        f"-{encode_tid(task.tid)}:encoded-title:running"
        in setproctitle.getproctitle()
    ), setproctitle.getproctitle()
finally:
    task.stop()
"""
    result = subprocess.run(
        [sys.executable, "-c", script],
        input=spec.model_dump_json(),
        capture_output=True,
        text=True,
        check=False,
        timeout=DEFAULT_TASK_COMPLETION_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    _target, make_queue = broker_env
    queue = make_queue(task_state_queue_name(tid))
    rows = [
        row for row, _stamp in iter_queue_json_entries(queue) if row.get("full") == tid
    ]
    assert rows[-1]["short"] == encode_tid(tid)


_REPO_ROOT = Path(__file__).resolve().parents[2]
_WEFT_ROOT = _REPO_ROOT / "weft"
_CASE_FOLDS = frozenset({"upper", "lower", "casefold"})

# (module path, qualified name, TID parameter). A new flexible TID lookup
# boundary adds itself here in the same change [OBS.5].
_NAMED_BOUNDARIES = (
    ("weft/commands/tasks.py", "resolve_full_tid", "raw"),
    ("weft/commands/tasks.py", "_command_tid", "raw"),
    ("weft/commands/result.py", "_normalize_tid", "raw_tid"),
    ("weft/commands/manager.py", "cmd_manager_stop", "tid"),
    ("weft/commands/manager.py", "cmd_manager_status", "tid"),
    ("weft/client/_client.py", "WeftClient.task", "tid"),
)


def _called_name(call: ast.Call) -> str | None:
    if isinstance(call.func, ast.Name):
        return call.func.id
    if isinstance(call.func, ast.Attribute):
        return call.func.attr
    return None


def _mentions(node: ast.AST, name: str) -> bool:
    return any(
        isinstance(child, ast.Name) and child.id == name for child in ast.walk(node)
    )


def _parse(relative: str) -> ast.Module:
    return ast.parse((_REPO_ROOT / relative).read_text(encoding="utf-8"))


def _function(module: ast.Module, qualname: str) -> ast.FunctionDef:
    scope: list[ast.stmt] = module.body
    found: ast.AST | None = None
    for part in qualname.split("."):
        found = next(
            (
                node
                for node in scope
                if isinstance(node, ast.FunctionDef | ast.ClassDef)
                and node.name == part
            ),
            None,
        )
        assert found is not None, f"{qualname}: {part} not found"
        scope = found.body
    assert isinstance(found, ast.FunctionDef), qualname
    return found


def _prune_selector_boundary() -> tuple[ast.FunctionDef, str | None]:
    """Locate the shared prune capability by its ``task_filters=`` binding."""

    module = _parse("weft/commands/prune.py")
    binders = [
        node
        for node in ast.walk(module)
        if isinstance(node, ast.FunctionDef)
        and any(
            isinstance(child, ast.keyword) and child.arg == "task_filters"
            for child in ast.walk(node)
        )
    ]
    assert len(binders) == 1, [node.name for node in binders]
    function = binders[0]
    targets = [
        loop.target.id
        for loop in ast.walk(function)
        if isinstance(loop, ast.For | ast.comprehension)
        and isinstance(loop.target, ast.Name)
        and _mentions(loop.iter, "task")
    ]
    return function, targets[0] if len(targets) == 1 else None


def _boundary_failures(function: ast.FunctionDef, label: str, param: str) -> list[str]:
    failures = []
    calls = [node for node in ast.walk(function) if isinstance(node, ast.Call)]
    if not any(
        _called_name(call) == "normalize_tid"
        and any(_mentions(arg, param) for arg in call.args)
        for call in calls
    ):
        failures.append(f"{label}: no normalize_tid call on {param!r}")
    for call in calls:
        name = _called_name(call)
        if (
            name in _CASE_FOLDS
            and isinstance(call.func, ast.Attribute)
            and _mentions(call.func.value, param)
        ):
            failures.append(f"{label}: local .{name}() on {param!r}")
        if name == "int" and any(_mentions(arg, param) for arg in call.args):
            failures.append(f"{label}: local int(...) on {param!r}")
    return failures


def test_tid_arguments_share_one_canonicalizer_and_one_decoder() -> None:
    """Every flexible TID lookup boundary canonicalizes through one function.

    Bounded static checks over source under ``weft/`` (``ast`` only; no
    imports, no mocks), owner ruling 1 of 2026-11-03 [OBS.5], [CLI-1.2.3]:

    (a) Per boundary. Each designated boundary function, located by module
        path and qualified name with its TID parameter named, calls
        ``normalize_tid`` with an argument expression that contains that
        parameter, and calls no ``.upper()``, ``.lower()`` or ``.casefold()``
        on it and no ``int(...)`` over it. The shared prune capability is
        located as the one function in ``weft/commands/prune.py`` that binds
        the ``task_filters=`` keyword, and its parameter is the target of the
        loop over ``task``. Other case folds in the same function (on a
        status string, say) are not inspected.
    (b) One decoder call site. Exactly one call to ``decode_tid`` exists in
        ``weft/``, inside ``weft/commands/submission.py::normalize_tid``.
    (c) Stated limit. A second decoder under another name with its own
        alphabet would pass (a) and (b); so would a boundary that calls
        ``normalize_tid`` and discards its result. The behavior tests (the
        CLI equivalence test, the direct ``_command_tid`` assertions, the
        Manager and prune selection tests, the client case table, and the
        codec's fixed vectors) catch a decoder that decodes differently or a
        normalized value that never reaches selection.
    """

    failures: list[str] = []
    for relative, qualname, param in _NAMED_BOUNDARIES:
        function = _function(_parse(relative), qualname)
        failures.extend(_boundary_failures(function, f"{relative}::{qualname}", param))
    prune_function, prune_param = _prune_selector_boundary()
    prune_label = f"weft/commands/prune.py::{prune_function.name}"
    if prune_param is None:
        failures.append(f"{prune_label}: no single loop over the --task selectors")
    else:
        failures.extend(_boundary_failures(prune_function, prune_label, prune_param))

    decoder_sites: list[tuple[str, int, bool]] = []
    for path in sorted(_WEFT_ROOT.rglob("*.py")):
        module = ast.parse(path.read_text(encoding="utf-8"))
        relative = path.relative_to(_REPO_ROOT).as_posix()
        owned: set[int] = set()
        if relative == "weft/commands/submission.py":
            owned = {id(node) for node in ast.walk(_function(module, "normalize_tid"))}
        decoder_sites.extend(
            (relative, node.lineno, id(node) in owned)
            for node in ast.walk(module)
            if isinstance(node, ast.Call) and _called_name(node) == "decode_tid"
        )
    if [owned for _relative, _line, owned in decoder_sites] != [True]:
        failures.append(f"decode_tid call sites: {decoder_sites}")
    assert not failures, "\n".join(failures)
