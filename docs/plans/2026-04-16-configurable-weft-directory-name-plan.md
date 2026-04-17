## Goal

Make the Weft project metadata directory name configurable through a Weft-owned
environment variable while preserving the current default `.weft`. The change
must stay on the existing context-resolution path and keep broker-target
selection, task/runtime behavior, and stored-project layout consistent when the
directory name is overridden.

## Source Documents

Source specs:
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0]
- `docs/specifications/05-Message_Flow_and_State.md` (spill path locality rules)
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1]
- `docs/specifications/00-Quick_Reference.md` (environment variables table)

Source guidance:
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`

## Context and Key Files

Files to modify:
- `weft/_constants.py`
- `weft/context.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `tests/system/test_constants.py`
- `tests/context/test_context.py`
- `tests/cli/test_cli_init.py`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/specifications/00-Quick_Reference.md`
- `README.md`

Read first:
- `weft/_constants.py`
- `weft/context.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `tests/context/test_context.py`
- `tests/system/test_constants.py`

Shared paths and helpers to reuse:
- `load_config()` remains the only Weft-owned environment normalization path
- `build_context()` remains the only context-resolution entry point
- `resolve_broker_target()` / `target_for_directory()` remain the broker target
  owners; do not add a second discovery algorithm

Current structure:
- `load_config()` currently hard-codes the default sqlite project path as
  `.weft/broker.db`
- `build_context()` currently hard-codes `root / ".weft"` for Weft-owned
  artifacts
- `find_existing_weft_dir()` currently searches upward only for `.weft`
- task spill paths and manager autostart-root fallback still reconstruct
  `spec_context / ".weft"` locally instead of going through a shared config

Comprehension questions before editing:
- Which layer owns broker-target discovery today? Answer:
  `weft/context.py` via SimpleBroker helpers.
- Which layer should own the configurable directory-name default? Answer:
  `weft/_constants.py` through `load_config()`.

## Invariants and Constraints

- Keep the canonical context-resolution path:
  `load_config() -> build_context() -> SimpleBroker target resolution`.
- Do not introduce a second project-discovery path outside `build_context()`.
- Preserve current default behavior when the new env var is unset. `.weft`
  remains the default directory name.
- Preserve broker precedence. An explicit `WEFT_DEFAULT_DB_NAME` override must
  still win over the derived default sqlite path.
- Keep the Weft artifact directory independent from broker-target ownership.
  The new env var changes Weft-owned directory naming; it does not make
  `.weft/config.json` or sibling metadata participate in broker-target
  resolution.
- Treat the override as a directory name, not an arbitrary path. Reject empty
  values and path-like values with separators or `.` / `..`.
- Keep task state, TID rules, reserved-queue policy, and runtime-only
  `weft.state.*` queue behavior unchanged.
- No new dependency. No drive-by refactor. No mock-heavy substitute for real
  context and CLI proofs where focused tests already exist.

Rollback note:
- Reverting must be possible by removing the new config usage while preserving
  `.weft` as the default. No persisted data migration is required because the
  override is environment-driven.

Out of scope:
- General path templating for every `.weft/...` string in docs/tests
- Per-project persisted override of the Weft directory name
- Changing SimpleBroker project-config semantics

## Tasks

1. Add the Weft-directory-name config boundary in `_constants.py`.
   - Outcome: one validated Weft-owned config value describes the project
     metadata directory name and the derived default sqlite db path.
   - Files to touch:
     - `weft/_constants.py`
     - `tests/system/test_constants.py`
   - Read first:
     - `weft/_constants.py`
     - `tests/system/test_constants.py`
   - Add a default constant for the directory name and a validated
     `WEFT_DIRECTORY_NAME` env-backed config key.
   - Derive the default `BROKER_DEFAULT_DB_NAME` from that config value unless
     the caller already supplied `WEFT_DEFAULT_DB_NAME` / `BROKER_DEFAULT_DB_NAME`.
   - Add focused config tests for:
     - default `.weft`
     - custom override such as `.engram`
     - rejection of invalid names
   - Stop and re-evaluate if this step starts reading env vars outside
     `load_config()` or makes the default db path stop respecting explicit
     `WEFT_DEFAULT_DB_NAME`.

2. Extend context discovery and artifact-path construction to use the new config.
   - Outcome: project creation, discovery, and upward marker search all honor
     the configured Weft directory name.
   - Files to touch:
     - `weft/context.py`
     - `tests/context/test_context.py`
     - `tests/cli/test_cli_init.py`
   - Read first:
     - `docs/specifications/04-SimpleBroker_Integration.md` [SB-0]
     - `weft/context.py`
     - `tests/context/test_context.py`
     - `tests/cli/test_cli_init.py`
   - Replace the hard-coded `.weft` path construction and upward search with
     the config-backed directory name.
   - Keep `--context` semantics unchanged: it still points at the project root.
   - Add focused tests proving that a custom directory name:
     - becomes `ctx.weft_dir`
     - is used by `weft init`
     - remains discoverable through `build_context()`
   - Stop and re-evaluate if this step starts treating `--context` as the
     metadata-directory path instead of the project root.

3. Remove remaining runtime hard-codes that reconstruct `.weft` locally.
   - Outcome: task spill paths and manager autostart-root fallback remain
     consistent with `build_context()`.
   - Files to touch:
     - `weft/core/tasks/base.py`
     - `weft/core/manager.py`
   - Read first:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `weft/core/tasks/base.py`
     - `weft/core/manager.py`
   - Reuse the already-loaded task/manager config rather than adding a second
     env read path.
   - Verify that `spec.weft_context` still means project root, with the
     metadata directory name appended from config.
   - Stop and re-evaluate if more code wants a shared helper that does not yet
     exist. Keep the change local unless duplication becomes real.

4. Update the touched specs and README to document the new contract.
   - Outcome: the normative docs stop claiming that `.weft` is always fixed.
   - Files to touch:
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/00-Quick_Reference.md`
     - `README.md`
   - Read first:
     - the current edited versions in the worktree
   - Keep edits narrow. Say that `.weft` is the default and that
     `WEFT_DIRECTORY_NAME` can override the Weft-owned project directory name.
   - Add plan backlinks where appropriate in the touched specs.
   - Stop and re-evaluate if doc edits start expanding into unrelated
     terminology cleanup.

5. Run targeted verification.
   - Outcome: config loading, context discovery, and init behavior are covered.
   - Commands:
     - `./.venv/bin/python -m pytest tests/system/test_constants.py -q`
     - `./.venv/bin/python -m pytest tests/context/test_context.py -q`
     - `./.venv/bin/python -m pytest tests/cli/test_cli_init.py -q`
   - If a focused regression appears in spill-path or manager logic, add the
     smallest additional targeted test instead of broadening the entire suite
     immediately.
