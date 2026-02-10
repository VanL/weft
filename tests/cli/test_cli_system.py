"""CLI tests for system maintenance commands."""

from __future__ import annotations

from simplebroker import Queue

from tests.conftest import run_cli
from weft.context import build_context


def _write_message(context, queue_name: str, body: str) -> None:
    queue = Queue(
        queue_name,
        db_path=str(context.database_path),
        persistent=True,
        config=context.broker_config,
    )
    queue.write(body)
    queue.close()


def test_system_dump_exports_messages(workdir) -> None:
    context = build_context(spec_context=workdir)
    _write_message(context, "dump.test", "payload")

    output_path = workdir / "weft_export.jsonl"

    rc, out, err = run_cli(
        "system",
        "dump",
        "--output",
        output_path,
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert "Exported" in out
    assert output_path.exists()

    lines = output_path.read_text(encoding="utf-8").splitlines()
    assert any("\"queue\": \"dump.test\"" in line for line in lines)


def test_system_dump_excludes_runtime_queues(workdir) -> None:
    context = build_context(spec_context=workdir)
    _write_message(context, "persist.test", "keep")
    _write_message(context, "weft.state.test_runtime", "skip")

    output_path = workdir / "weft_export_runtime.jsonl"

    rc, out, err = run_cli(
        "system",
        "dump",
        "--output",
        output_path,
        "--context",
        workdir,
        cwd=workdir,
    )

    assert rc == 0
    assert err == ""
    assert "Exported" in out

    content = output_path.read_text(encoding="utf-8")
    assert "persist.test" in content
    assert "weft.state.test_runtime" not in content


def test_system_load_imports_dump(workdir) -> None:
    source_dir = workdir / "source"
    source_dir.mkdir()
    source_context = build_context(spec_context=source_dir)
    _write_message(source_context, "import.test", "hello")

    export_path = workdir / "export.jsonl"
    rc, out, err = run_cli(
        "system",
        "dump",
        "--output",
        export_path,
        "--context",
        source_dir,
        cwd=source_dir,
    )

    assert rc == 0
    assert err == ""
    assert export_path.exists()

    target_dir = workdir / "target"
    target_dir.mkdir()

    rc, out, err = run_cli(
        "system",
        "load",
        "--input",
        export_path,
        "--dry-run",
        "--context",
        target_dir,
        cwd=target_dir,
    )

    assert rc == 0
    assert err == ""
    assert "Import Preview" in out

    rc, out, err = run_cli(
        "system",
        "load",
        "--input",
        export_path,
        "--context",
        target_dir,
        cwd=target_dir,
    )

    assert rc == 0
    assert err == ""
    assert "Import completed" in out

    target_context = build_context(spec_context=target_dir)
    queue = Queue(
        "import.test",
        db_path=str(target_context.database_path),
        persistent=True,
        config=target_context.broker_config,
    )
    try:
        assert queue.peek_one() == "hello"
    finally:
        queue.close()
