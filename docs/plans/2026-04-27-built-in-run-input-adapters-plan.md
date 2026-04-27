# Built-In Run Input Adapters Plan

Status: implemented
Source specs: see Source Documents below
Superseded by: none

## Goal

Add Weft-owned `spec.run_input` adapters for the common case where a TaskSpec's
declared CLI options should become the initial work payload. This lets
downstream systems add `weft run --spec TASK --case-id ...` style contracts
without first shipping custom adapter modules. The change must not alter
`weft run --spec` parsing, manager submission, task execution, queue contracts,
or the existing rejection of `--arg` and `--kw` with `--spec`.

## Source Documents

Source specs:

- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1], [TS-1.4A]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-1.1.1]

Repo guidance:

- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)

Superseded draft:

- An initial downstream-first draft was discarded before implementation. This
  plan is the canonical record for the Weft-side slice.

## Context and Key Files

Files to modify:

- `weft/builtins/run_input.py`
- `tests/core/test_spec_run_input.py`
- `docs/specifications/02-TaskSpec.md`
- `docs/specifications/10-CLI_Interface.md`
- `README.md`
- `docs/tutorials/first-task.md`

Read first:

- `weft/core/taskspec/run_input.py`
  - owns `SpecRunInputRequest`, adapter import, invocation, and
    JSON-serializable payload validation
- `weft/core/targets.py`
  - owns the runtime distinction between flat payload dicts and `{"kwargs":
    ...}` envelopes for function targets
- `tests/core/test_spec_run_input.py`
  - current focused tests for adapter invocation

## Invariants and Constraints

- Do not make `--kw` compatible with `--spec`; that rejection remains the
  public contract.
- Do not add a second execution path. Built-in adapters run through the
  existing `spec.run_input.adapter_ref` import and invocation path.
- Do not add adapter configuration schema in this slice.
- Do not interpret TaskSpec metadata as work payload.
- Built-in adapter outputs must be JSON-serializable plain Python data.
- Flat payload and kwargs-envelope semantics must stay explicit:
  - `arguments_payload` returns `dict(request.arguments)`
  - `keyword_arguments_payload` returns `{"kwargs": dict(request.arguments)}`
- Blank-value rejection must be opt-in through `nonempty_*` adapters so specs
  that intentionally accept empty strings are not broken.
- `nonempty_*` adapters reject blank strings only. They do not validate UUIDs,
  dates, paths beyond declared `type="path"` normalization, or domain-specific
  identifier formats.

## Tasks

1. Add Weft-owned built-in adapters.
   - File: `weft/builtins/run_input.py`
   - Add:
     - `arguments_payload`
     - `nonempty_arguments_payload`
     - `keyword_arguments_payload`
     - `nonempty_keyword_arguments_payload`
   - Reuse `SpecRunInputRequest`; do not duplicate parser logic.

2. Add focused adapter tests.
   - File: `tests/core/test_spec_run_input.py`
   - Test flat payload output, kwargs envelope output, and opt-in blank-value
     rejection.

3. Add CLI-level end-to-end tests.
   - File: `tests/cli/test_cli_run.py`
   - Create temporary TaskSpecs that reference:
     - `weft.builtins.run_input:arguments_payload`
     - `weft.builtins.run_input:keyword_arguments_payload`
   - Run `weft run --spec PATH --case-id abc --json` and assert the target
     receives the expected payload shape.

4. Update public docs and specs.
   - Files:
     - `docs/specifications/02-TaskSpec.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `README.md`
     - `docs/tutorials/first-task.md`
   - Document when to choose flat payload vs kwargs envelope.

5. Verify.
   - Run:
     - `./.venv/bin/python -m pytest tests/core/test_spec_run_input.py -q`
     - `./.venv/bin/python -m pytest tests/cli/test_cli_run.py::test_cli_run_spec_builtin_arguments_payload_shapes_work_item tests/cli/test_cli_run.py::test_cli_run_spec_builtin_keyword_arguments_payload_shapes_work_item tests/cli/test_cli_validate.py::test_validate_taskspec_run_input_builtin_adapter -q`
     - `./.venv/bin/ruff check weft/builtins/run_input.py tests/core/test_spec_run_input.py tests/cli/test_cli_run.py tests/cli/test_cli_validate.py tests/tasks/sample_targets.py`

## Rollback

Rollback is straightforward: remove `weft/builtins/run_input.py`, remove the
new tests, and remove the documentation references. No persisted queue shape,
TaskSpec schema field, manager behavior, or runtime state changes are involved.
