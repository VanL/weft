"""Xdist titles must not change the native API exercised by tests [TS-0]."""

from __future__ import annotations

import ast
import subprocess
import sys
from importlib.util import find_spec
from pathlib import Path

import pytest

from tests.helpers import xdist_titles
from weft.helpers import terminate_process_tree

pytestmark = pytest.mark.shared


@pytest.mark.parametrize("adapt", [False, True])
def test_installed_bootstrap_native_import_boundary(adapt: bool) -> None:
    """The stock source imports native code; our bootstrap never attempts it."""
    script = """
import builtins
import sys
from pathlib import Path
import xdist
from tests.helpers.xdist_titles import without_worker_titles

source = Path(xdist.__file__).with_name('remote.py').read_text()
adapt = sys.argv[1] == 'True'
if adapt:
    source = without_worker_titles(source)
original_import = builtins.__import__
class NativeImportAttempt(Exception):
    pass
def guarded_import(name, *args, **kwargs):
    if name == 'setproctitle':
        raise NativeImportAttempt
    return original_import(name, *args, **kwargs)
builtins.__import__ = guarded_import
namespace = {'__name__': 'bootstrap_probe'}
try:
    exec(compile(source, '<xdist-bootstrap>', 'exec'), namespace)
except NativeImportAttempt:
    assert not adapt
else:
    assert adapt
    namespace['worker_title']('must not reach native code')
finally:
    builtins.__import__ = original_import
assert 'setproctitle' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-c", script, str(adapt)],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, result.stderr


def test_adaptation_preserves_remaining_bootstrap() -> None:
    module = find_spec("xdist.remote")
    assert module is not None and module.origin is not None
    source = Path(module.origin).read_text(encoding="utf-8")
    before = ast.parse(source)
    after = ast.parse(xdist_titles.without_worker_titles(source))
    changes = [
        (old, new)
        for old, new in zip(before.body, after.body, strict=True)
        if ast.dump(old) != ast.dump(new)
    ]
    assert len(changes) == 1
    old, new = changes[0]
    assert isinstance(old, ast.Try)
    assert ast.dump(new) == ast.dump(old.handlers[0].body[0])


@pytest.mark.parametrize("platform", ["linux", "win32", "darwin"])
def test_bootstrap_override_is_macos_only(
    monkeypatch: pytest.MonkeyPatch, platform: str
) -> None:
    monkeypatch.setattr(xdist_titles.sys, "platform", platform)
    result = xdist_titles.pytest_xdist_getremotemodule()
    assert (result is not None) == (platform == "darwin")


@pytest.mark.parametrize(
    "source",
    [
        "pass",
        "try:\n from setproctitle import setproctitle\nexcept Exception:\n pass",
        "try:\n from setproctitle import setproctitle\nexcept ImportError:\n def setproctitle(title):\n  print(title)",
        "try:\n from setproctitle import getproctitle\nexcept ImportError:\n def setproctitle(title: str) -> None:\n  pass",
        "try:\n from setproctitle import setproctitle\nexcept ImportError:\n @native_decorator\n def setproctitle(title: str) -> None:\n  pass",
        "try:\n from setproctitle import setproctitle\nexcept ImportError:\n def setproctitle(title=__import__('setproctitle')):\n  pass",
    ],
)
def test_incompatible_bootstrap_fails_explicitly(source: str) -> None:
    with pytest.raises(RuntimeError, match="Unsupported xdist bootstrap"):
        xdist_titles.without_worker_titles(source)


@pytest.mark.timeout(900, method="signal")
@pytest.mark.skipif(sys.platform != "darwin", reason="macOS native worker handoff")
def test_real_worker_preserves_production_title_handoff(tmp_path: Path) -> None:
    """Exercise production native writers inside a freshly bootstrapped worker."""
    config = tmp_path / "pytest.ini"
    config.write_text("[pytest]\n", encoding="utf-8")
    probe = tmp_path / "test_native_worker.py"
    probe.write_text(
        """
import os
import subprocess
import sys

from weft.core import deferred, process_title

TITLE = 'weft-native-worker:production:running'

def observed_title():
    return subprocess.check_output(
        ['/bin/ps', '-ww', '-p', str(os.getpid()), '-o', 'command='], text=True
    ).strip()

def test_production_handoff(request):
    assert hasattr(request.config, 'workerinput')
    assert 'setproctitle' not in sys.modules
    assert 'processtitle' not in sys.modules
    assert process_title.set_process_title(TITLE) is None
    assert observed_title() == TITLE
    assert 'setproctitle' not in sys.modules
    process_title._title._deadline = 0
    assert process_title.tick() is None
    assert deferred.get_setproctitle().getproctitle() == TITLE
    assert observed_title() == TITLE

def test_xdist_did_not_overwrite_production_title():
    # Both the idle and next-test title callbacks ran between these tests.
    assert deferred.get_setproctitle().getproctitle() == TITLE
    assert observed_title() == TITLE
""",
        encoding="utf-8",
    )
    child = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "pytest",
            "-c",
            str(config),
            "-p",
            "tests.helpers.xdist_titles",
            "--import-mode=importlib",
            "-n",
            "1",
            str(probe),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        stdout, stderr = child.communicate()
        assert child.returncode == 0, stdout + stderr
    finally:
        if child.poll() is None:
            terminate_process_tree(child.pid)
        child.communicate()
