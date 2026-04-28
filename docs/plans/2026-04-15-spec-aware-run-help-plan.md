# Spec-Aware `weft run --spec ... --help`

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Make `weft run --spec NAME|PATH --help` load the selected TaskSpec and show its
declared submission-time options in addition to the normal `weft run` help.
This should cover builtins and stored specs, stay on the existing CLI path, and
avoid inventing a second help command.

## 2. Source Documents

Source specs:
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]
- `docs/specifications/02-TaskSpec.md` [TS-1], [TS-1.4], [TS-1.4A]
- `docs/specifications/10B-Builtin_TaskSpecs.md`

Local guidance:
- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`

Related in-flight plans:
- `docs/plans/2026-04-15-spec-run-input-adapter-and-declared-args-plan.md`
- `docs/plans/2026-04-15-parameterized-taskspec-materialization-plan.md`

## 3. Context and Key Files

Files to modify:
- `weft/cli.py`
- `weft/commands/run.py`
- `tests/cli/test_cli_run.py`
- `docs/specifications/10-CLI_Interface.md`

Read first:
- `weft/cli.py`
- `weft/commands/run.py`
- `weft/core/spec_parameterization.py`
- `weft/core/spec_run_input.py`
- `tests/cli/test_cli_run.py`

Shared paths to reuse:
- `weft/commands/run.py::_load_taskspec_reference`
- `weft/core/spec_run_input.py::normalize_declared_option_name`

Current structure:
- `weft/cli.py::run_command()` is registered with `allow_extra_args` and
  `ignore_unknown_options` so spec-declared args can flow through `command`
  tokens.
- Typer still owns the built-in `--help` path, so `weft run --spec ... --help`
  exits before any spec-aware code runs.
- `weft/commands/run.py` already owns spec loading, local parameterization, and
  local run-input shaping.

## 4. Invariants and Constraints

- No new execution path. This is CLI help rendering only.
- Do not change ordinary `weft run --help` semantics except to route them
  through the explicit custom help flag.
- Do not change task submission, manager bootstrap, or runtime behavior.
- Builtin and stored specs must both work through the same explicit
  `--spec NAME|PATH` path.
- Spec-aware help must be additive. Generic `weft run` help still needs to be
  visible.
- Do not add a new top-level command such as `weft spec help`.
- No new dependency.

## 5. Tasks

1. Add a custom `run` help flag in `weft/cli.py`.
   - Disable Typer's auto-help for the `run` command only.
   - Add an explicit eager `--help` option.
   - When `--help` is requested and `--spec` is absent, print the normal
     generic `run` help from the current context and exit 0.
   - When `--help` is requested and `--spec` is present, delegate to
     `weft/commands/run.py` for spec-aware rendering and exit 0.

2. Implement spec-aware help rendering in `weft/commands/run.py`.
   - Reuse `_load_taskspec_reference`.
   - Append a spec-aware section to the generic `run` help.
   - Show:
     - selected TaskSpec name
     - description when present
     - declared `spec.parameterization` options
     - declared `spec.run_input` options
     - declared stdin contract when present
   - Reuse declared option names from `normalize_declared_option_name`.
   - Keep formatting plain text and deterministic.

3. Add CLI coverage in `tests/cli/test_cli_run.py`.
   - Verify generic `weft run --help` still works.
   - Verify `weft run --spec example-dockerized-agent --help` includes
     spec-aware sections and declared options such as `--provider`, `--model`,
     `--prompt`, and `--document`.
   - Verify the builtin help path exits 0 and does not try to run the task.

4. Update the CLI spec.
   - In `docs/specifications/10-CLI_Interface.md`, document that
     `weft run --spec NAME|PATH --help` loads the selected TaskSpec and shows
     declared submission-time options from `spec.parameterization` and
     `spec.run_input`.
   - Add this plan to the related-plans list.

## 6. Verification

Run:
- `./.venv/bin/pytest -n0 tests/cli/test_cli_run.py`
- `./.venv/bin/ruff check weft/cli.py weft/commands/run.py tests/cli/test_cli_run.py`
- `./.venv/bin/ruff format --check weft/cli.py weft/commands/run.py tests/cli/test_cli_run.py`
- `./.venv/bin/mypy weft/cli.py weft/commands/run.py`

Manual proof:
- `./.venv/bin/python -m weft.cli run --help`
- `./.venv/bin/python -m weft.cli run --spec example-dockerized-agent --help`

## 7. Rollback

If the dynamic help path causes parsing regressions, remove the custom `run`
help flag, restore Typer's default `--help`, and keep the rest of the
spec-declared option parsing unchanged.
