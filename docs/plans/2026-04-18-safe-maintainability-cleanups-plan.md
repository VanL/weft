# Safe Maintainability Cleanups Plan

Status: completed
Source specs: none (maintainability cleanup only)
Superseded by: none

## 1. Goal

Apply a narrow set of behavior-preserving cleanup changes surfaced by the audit:
remove verified tautologies and redundant guards, align shipped `PAUSE` /
`RESUME` runtime controls with the canonical constants, trim an internal no-op
defaults path, and reuse an existing helper instead of carrying a duplicate.

## 2. Source Documents

Source spec: None — maintainability cleanup / refactor only.

Relevant guardrails and owning specs:
- `AGENTS.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/engineering-principles.md`
- `docs/specifications/01-Core_Components.md` [CC-2.4], [CC-3.2], [CC-3.3]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]
- `docs/specifications/11-CLI_Architecture_Crosswalk.md`
- `docs/specifications/12-Future_Ideas.md`

## 3. Context and Key Files

Files to modify:
- `weft/_constants.py`
- `weft/commands/_task_history.py`
- `weft/commands/result.py`
- `weft/commands/run.py`
- `weft/commands/specs.py`
- `weft/commands/tasks.py`
- `weft/core/endpoints.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/debugger.py`
- `weft/core/taskspec.py`
- `weft/helpers.py`
- `tests/commands/test_run.py`

Read first:
- `weft/core/tasks/base.py`
- `weft/commands/result.py`
- `weft/commands/tasks.py`
- `weft/commands/status.py`

Current structure:
- `weft/commands/result.py` owns result materialization and persistent-result
  wait logic.
- `weft/core/tasks/base.py` owns task control handling, including shipped
  runtime `PAUSE` / `RESUME`.
- `weft/commands/status.py` already owns the canonical
  `_runtime_handle_from_mapping()` helper used by `weft/commands/tasks.py`.
- `weft/core/taskspec.py` injects default limits into authored payloads.

## 4. Invariants and Constraints

- No public CLI shape change.
- No plugin protocol change.
- No file deletions in this slice.
- No spec-owned behavior change. `PAUSE` / `RESUME` runtime support stays
  shipped; only stale constant text and raw-string usage are corrected.
- No drive-by refactor beyond verified redundancies.
- Verification stays targeted: prove touched helpers and wait paths still work,
  then run lint for the edited Python files.

## 5. Tasks

1. Remove verified redundant branches and guards.
   - Files to touch:
     - `weft/commands/result.py`
     - `weft/commands/_task_history.py`
     - `weft/commands/specs.py`
     - `weft/helpers.py`
     - `weft/core/endpoints.py`
     - `weft/core/tasks/debugger.py`
   - Required action:
     - delete only branches that are unreachable from the current local
       control flow
     - keep behavior identical
   - Verification:
     - targeted `tests/commands/test_run.py`

2. Align shipped pause/resume runtime control constants and trim no-op internal
   duplication.
   - Files to touch:
     - `weft/_constants.py`
     - `weft/core/tasks/base.py`
     - `weft/core/taskspec.py`
     - `weft/commands/tasks.py`
   - Required action:
     - update stale `CONTROL_PAUSE` / `CONTROL_RESUME` docstrings
     - use the constants in `BaseTask`
     - simplify the limit-default injection path to the only effective default
     - reuse `weft/commands/status.py::_runtime_handle_from_mapping`
   - Verification:
     - targeted `tests/tasks/test_control_channel.py`

3. Remove the unused `run` wait-helper parameters and update callers/tests.
   - Files to touch:
     - `weft/commands/run.py`
     - `tests/commands/test_run.py`
   - Required action:
     - drop the dead parameters from the helper signature
     - update all local callers and direct tests
   - Verification:
     - targeted `tests/commands/test_run.py`

4. Run focused verification and lint.
   - Commands:
     - `./.venv/bin/python -m pytest tests/commands/test_run.py tests/tasks/test_control_channel.py -q`
     - `./.venv/bin/ruff check weft tests/commands/test_run.py tests/tasks/test_control_channel.py`
