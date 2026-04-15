"""CLI tests for pipeline execution."""

from __future__ import annotations

import json

from tests.conftest import run_cli
from weft.context import build_context


def _write_json(path, payload) -> None:
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_pipeline_run_sequential(workdir, weft_harness) -> None:
    ctx = build_context(spec_context=workdir)
    tasks_dir = ctx.weft_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        tasks_dir / "stage1.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        tasks_dir / "stage2.json",
        {
            "name": "stage2",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    pipeline_path = workdir / "pipeline.json"
    _write_json(
        pipeline_path,
        {
            "name": "pipe",
            "stages": [
                {"name": "one", "task": "stage1"},
                {
                    "name": "two",
                    "task": "stage2",
                    "defaults": {"keyword_args": {"suffix": "-done"}},
                },
            ],
        },
    )

    rc, out, err = run_cli(
        "run",
        "--pipeline",
        pipeline_path,
        "--input",
        "hello",
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0
    assert "hello-done" in out
    assert err == ""


def test_pipeline_run_reads_piped_stdin_when_input_omitted(
    workdir, weft_harness
) -> None:
    ctx = build_context(spec_context=workdir)
    tasks_dir = ctx.weft_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        tasks_dir / "stage1.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        tasks_dir / "stage2.json",
        {
            "name": "stage2",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    pipeline_path = workdir / "pipeline_stdin.json"
    _write_json(
        pipeline_path,
        {
            "name": "pipe-stdin",
            "stages": [
                {"name": "one", "task": "stage1"},
                {
                    "name": "two",
                    "task": "stage2",
                    "defaults": {"keyword_args": {"suffix": "-done"}},
                },
            ],
        },
    )

    rc, out, err = run_cli(
        "run",
        "--pipeline",
        pipeline_path,
        cwd=workdir,
        harness=weft_harness,
        stdin="hello",
    )

    assert rc == 0
    assert out == "hello-done"
    assert err == ""


def test_pipeline_run_stage_can_be_bundle_backed_task_spec(
    workdir, weft_harness
) -> None:
    ctx = build_context(spec_context=workdir)
    tasks_dir = ctx.weft_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    bundle_dir = tasks_dir / "stage1"
    bundle_dir.mkdir(parents=True, exist_ok=True)
    (bundle_dir / "helper_module.py").write_text(
        "\n".join(
            [
                "def bundle_stage(payload: str) -> str:",
                "    return f'{payload}-bundle'",
                "",
            ]
        ),
        encoding="utf-8",
    )
    _write_json(
        bundle_dir / "taskspec.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "helper_module:bundle_stage",
            },
            "metadata": {},
        },
    )

    pipeline_path = workdir / "pipeline_bundle.json"
    _write_json(
        pipeline_path,
        {
            "name": "pipe-bundle",
            "stages": [{"name": "one", "task": "stage1"}],
        },
    )

    rc, out, err = run_cli(
        "run",
        "--pipeline",
        pipeline_path,
        "--input",
        "hello",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    assert "hello-bundle" in out
    assert err == ""


def test_pipeline_run_no_wait_returns_pipeline_tid(workdir, weft_harness) -> None:
    ctx = build_context(spec_context=workdir)
    tasks_dir = ctx.weft_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        tasks_dir / "stage1.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        tasks_dir / "stage2.json",
        {
            "name": "stage2",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    pipeline_path = workdir / "pipeline_no_wait.json"
    _write_json(
        pipeline_path,
        {
            "name": "pipe-no-wait",
            "stages": [
                {"name": "one", "task": "stage1"},
                {
                    "name": "two",
                    "task": "stage2",
                    "defaults": {"keyword_args": {"suffix": "-done"}},
                },
            ],
        },
    )

    rc, out, err = run_cli(
        "run",
        "--pipeline",
        pipeline_path,
        "--no-wait",
        "--input",
        "hello",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 0
    tid = out.strip()
    assert tid.isdigit()
    weft_harness.register_tid(tid)
    weft_harness.wait_for_completion(tid)

    rc, out, err = run_cli(
        "result",
        tid,
        cwd=workdir,
        harness=weft_harness,
    )
    assert rc == 0
    assert out == "hello-done"
    assert err == ""


def test_pipeline_stage_failure_returns_nonzero_and_names_failing_stage(
    workdir, weft_harness
) -> None:
    ctx = build_context(spec_context=workdir)
    tasks_dir = ctx.weft_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        tasks_dir / "stage1.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )
    _write_json(
        tasks_dir / "stage2.json",
        {
            "name": "stage2",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:fail_payload",
            },
            "metadata": {},
        },
    )

    pipeline_path = workdir / "pipeline_fail.json"
    _write_json(
        pipeline_path,
        {
            "name": "pipe-fail",
            "stages": [
                {"name": "one", "task": "stage1"},
                {"name": "two", "task": "stage2"},
            ],
        },
    )

    rc, out, err = run_cli(
        "run",
        "--pipeline",
        pipeline_path,
        "--input",
        "hello",
        cwd=workdir,
        harness=weft_harness,
    )

    assert rc == 1
    combined = f"{out}\n{err}"
    assert "two" in combined


def test_pipeline_rejects_input_flag_with_piped_stdin(workdir, weft_harness) -> None:
    ctx = build_context(spec_context=workdir)
    tasks_dir = ctx.weft_dir / "tasks"
    tasks_dir.mkdir(parents=True, exist_ok=True)

    _write_json(
        tasks_dir / "stage1.json",
        {
            "name": "stage1",
            "spec": {
                "type": "function",
                "function_target": "tests.tasks.sample_targets:echo_payload",
            },
            "metadata": {},
        },
    )

    pipeline_path = workdir / "pipeline_conflict.json"
    _write_json(
        pipeline_path,
        {
            "name": "pipe-conflict",
            "stages": [{"name": "one", "task": "stage1"}],
        },
    )

    rc, out, err = run_cli(
        "run",
        "--pipeline",
        pipeline_path,
        "--input",
        "hello",
        cwd=workdir,
        harness=weft_harness,
        stdin="ignored",
    )

    assert rc != 0
    combined = f"{out}\n{err}"
    assert "--input cannot be used together with piped stdin" in combined
