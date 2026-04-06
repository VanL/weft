"""CLI tests for the tidy maintenance command."""

from __future__ import annotations

from tests.conftest import run_cli
from weft.context import build_context


def test_tidy_runs_backend_native_compaction_twice(workdir):
    ctx = build_context(spec_context=workdir)

    # Do a trivial queue write so the database exists and has WAL state.
    queue = ctx.queue("tidy.test", persistent=True)
    queue.write("payload")
    queue.close()

    rc, out, err = run_cli("system", "tidy", cwd=workdir)
    assert rc == 0
    assert err == ""
    assert "Tidied" in out

    second_rc, second_out, second_err = run_cli("system", "tidy", cwd=workdir)
    assert second_rc == 0
    assert second_err == ""
    assert "Tidied" in second_out
