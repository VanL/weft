# CLI Surface Doc And Help Alignment Plan

## Goal

Align the user-facing CLI contract across live help, README examples, and CLI
spec docs without changing the intentional `weft init` workflow. This slice
should document `init` as a git-like project initializer, make `--spec` docs
match the current file-only implementation, and stop advertising unsupported
`--monitor` behavior in `weft run --help`.

## Source Documents

Source specs:

- `docs/specifications/10-CLI_Interface.md` [CLI-0.1], [CLI-1.1]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0]

Read for current behavior:

- `README.md`
- `problem-report-cli-surface-inconsistencies.md`
- `weft/cli.py`
- `weft/commands/run.py`
- `weft/commands/init.py`
- `tests/cli/test_cli_init.py`
- `tests/cli/test_cli_run.py`

## Context and Key Files

Files to modify:

- `README.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/plans/2026-04-13-cli-surface-doc-and-help-alignment-plan.md`
- `weft/cli.py`
- `tests/cli/test_cli_init.py`
- `tests/cli/test_cli_run.py`

Read first:

- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `README.md`
- `problem-report-cli-surface-inconsistencies.md`
- `weft/cli.py`
- `weft/commands/run.py`
- `weft/commands/init.py`

Shared paths and owners:

- `weft/cli.py` owns public help text, option types, and option visibility.
- `weft/commands/run.py` owns runtime validation for `weft run`.
- `weft/commands/init.py` owns `weft init` target resolution and success text.
- `README.md` and `docs/specifications/10-CLI_Interface.md` are the main
  operator-facing contract docs.
- `docs/specifications/04-SimpleBroker_Integration.md` documents project
  initialization and context discovery semantics.

Comprehension questions before editing:

- Which layer decides whether an option appears in `weft run --help`?
  `weft/cli.py`, not `weft/commands/run.py`.
- Which layer enforces whether `--monitor` is accepted at runtime?
  `weft/commands/run.py`.
- Which command intentionally selects a project root positionally instead of
  with `--context`?
  `weft init`.

## Invariants and Constraints

- Keep `weft init` behavior unchanged in this slice:
  positional `[DIRECTORY]`, current-directory default, no new `--context`.
- Keep `weft run --spec` behavior unchanged in this slice:
  it accepts a JSON file path, not stored task names.
- Keep pipeline name support unchanged:
  `weft run --pipeline NAME|PATH` still accepts stored pipeline names.
- Do not add a second CLI path or new task execution behavior.
- Do not implement `--monitor`; only stop advertising it until support exists.
- No drive-by CLI polish outside the specific report items.
- Keep docs and help synchronized in the same change. Do not fix one surface
  while leaving the others ambiguous.

Stop and re-evaluate if:

- the work starts adding a new `init` targeting mode,
- the best fix for `--spec` drift appears to require a behavior change rather
  than documentation alignment,
- or hiding `--monitor` would break an existing supported workflow.

Rollback:

- Revert the doc/help alignment patch as one slice.
- No data migration or persisted contract rollback is involved because runtime
  execution behavior stays unchanged.

## Tasks

1. Document the intentional `init` model clearly.
   - Outcome: README and CLI spec explain that `weft init` behaves like
     project-root initializers such as `git init`: it targets the current
     directory by default, accepts an optional positional directory, and does
     not use `--context` because it is creating or selecting the project root.
   - Files to touch:
     - `README.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
   - Read first:
     - `weft/commands/init.py`
     - current `init` sections in README and both spec docs
   - Reuse:
     - the existing `weft init /path/to/project` examples
   - Constraints:
     - no CLI behavior change
     - remove stale aspirational wording that implies prompt-based init or
       `--force` support if it contradicts current behavior
   - Tests / checks:
     - live `weft init --help`
     - doc text review against `cmd_init()`
   - Done when:
     - a new user can tell from docs when to use `weft init` versus
       `--context`

2. Make `--spec` documentation match the live contract.
   - Outcome: README and CLI spec say `weft run --spec FILE`, remove stored
     task-name examples, and keep stored-name language limited to pipelines or
     `weft spec *` management commands.
   - Files to touch:
     - `README.md`
     - `docs/specifications/10-CLI_Interface.md`
   - Read first:
     - `weft/cli.py`
     - `weft/commands/run.py`
     - `weft/commands/specs.py`
   - Reuse:
     - existing explicit JSON-path examples such as `task.json`
   - Constraints:
     - no runtime behavior change to `--spec`
     - do not invent a new stored-task execution command
   - Tests / checks:
     - live `weft run --help`
     - grep for remaining `--spec NAME|PATH` or bare-name examples
   - Done when:
     - help, README, and CLI spec all describe the same `--spec` contract

3. Stop exposing unsupported `--monitor` in help and lock it with tests.
   - Outcome: `weft run --help` no longer advertises `--monitor`, while
     runtime rejection remains unchanged for any direct invocation path that
     still reaches `cmd_run()`.
   - Files to touch:
     - `weft/cli.py`
     - `tests/cli/test_cli_run.py`
   - Read first:
     - `weft/cli.py`
     - `weft/commands/run.py`
     - existing CLI run tests
   - Reuse:
     - existing `run_cli(...)` helper in `tests/conftest.py`
   - Constraints:
     - do not implement monitor mode here
     - keep `--continuous/--once` behavior unchanged
   - Tests / checks:
     - add a CLI help regression asserting `--monitor` is absent
     - keep runtime validation untouched unless a test proves help hiding
       changes parsing behavior unexpectedly
   - Done when:
     - help no longer lies about monitor support

4. Add narrow regression coverage for the intended surface.
   - Outcome: tests prove the documented `init` default target, the documented
     positional explicit target, and the updated help text for `--spec` and
     `--monitor`.
   - Files to touch:
     - `tests/cli/test_cli_init.py`
     - `tests/cli/test_cli_run.py`
   - Read first:
     - existing init and run CLI tests
   - Constraints:
     - keep tests black-box and CLI-facing
     - use real subprocess help output, not internal Typer object assertions
   - Tests / checks:
     - `pytest` on the touched test modules
   - Done when:
     - the CLI surface described in docs is enforced by tests

5. Refresh traceability backlinks and run a fresh-eyes review.
   - Outcome: touched specs link back to this plan, and an independent review
     checks for lingering contract drift or ambiguous wording.
   - Files to touch:
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/plans/2026-04-13-cli-surface-doc-and-help-alignment-plan.md`
   - Constraints:
     - if review argues for changing `weft init` behavior, record why that is
       out of scope for this slice instead of silently changing direction
   - Done when:
     - the spec-plan-doc chain is bidirectional and review findings, if any,
       are resolved or explicitly rejected with reasons

## Engineering Approach

- DRY: reuse current file-path examples and the existing CLI surface instead of
  inventing new modes.
- YAGNI: do not implement stored task-name execution or monitor mode here.
- Real proof over inference: use live CLI help and subprocess tests, not only
  code inspection.

## Testing Plan

- Run:
  - `./.venv/bin/python -m pytest tests/cli/test_cli_init.py tests/cli/test_cli_run.py -q`
- Spot check:
  - `./.venv/bin/python -m weft.cli init --help`
  - `./.venv/bin/python -m weft.cli run --help`
- Review grep:
  - confirm no remaining `--spec NAME|PATH` or bare-name `weft run --spec foo`
    examples in touched docs

## Review Plan

- Independent review requested after implementation.
- Preferred reviewer: a different model family if available in the environment.
- Review focus:
  - contract drift between docs and live help
  - unintended CLI behavior changes
  - missing traceability or missing regression coverage
