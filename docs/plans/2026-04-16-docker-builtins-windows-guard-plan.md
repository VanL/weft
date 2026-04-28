# Docker Builtins Windows Guard Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Make Docker-dependent builtins explicit about their platform boundary and fail
cleanly on Windows before they reach Docker plugin startup. Keep builtin
inspection intact where practical, but stop Docker-backed builtin execution
from looking like a generic runner failure. Update the builtin tests so
Windows CI does not exercise positive-path Docker builtin behavior.

## 2. Source Documents

Source specs:
- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/specifications/10-CLI_Interface.md [CLI-1.4], [CLI-6]`
- `docs/specifications/02-TaskSpec.md [TS-1.3]`

Local guidance:
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`

## 3. Context and Key Files

Files to modify:
- `weft/builtins/__init__.py`
- `weft/commands/builtins.py`
- `weft/builtins/agent_images.py`
- `weft/builtins/tasks/prepare-agent-images.json`
- `weft/builtins/tasks/dockerized-agent/taskspec.json`
- `weft/builtins/tasks/dockerized-agent/dockerized_agent.py`
- `tests/core/test_builtin_agent_images.py`
- `tests/core/test_builtin_dockerized_agent.py`
- `tests/core/test_builtin_platform_support.py`
- `tests/cli/test_cli_spec.py`
- `tests/cli/test_cli_system.py`
- `docs/specifications/10B-Builtin_TaskSpecs.md`
- `docs/lessons.md`

Read first:
- `weft/builtins/__init__.py`
- `weft/builtins/agent_images.py`
- `weft/builtins/tasks/dockerized-agent/dockerized_agent.py`
- `tests/core/test_builtin_agent_images.py`
- `tests/core/test_builtin_dockerized_agent.py`
- `tests/cli/test_cli_spec.py`
- `tests/cli/test_cli_system.py`

Current structure:
- builtin spec assets are plain JSON under `weft/builtins/tasks/`
- `prepare-agent-images` is a function builtin that imports Docker image helpers
- `dockerized-agent` is a bundle-local parameterized TaskSpec that ultimately
  selects `spec.runner.name="docker"`
- Windows rejection currently lives in the Docker runner plugin, not at the
  builtin contract boundary
- builtin inventory output comes from `builtin_task_catalog()` through
  `weft/commands/builtins.py`

Comprehension questions:
1. Which code path currently rejects Docker on Windows today?
2. Which Docker builtins are function-backed versus parameterized TaskSpec
   bundles?

## 4. Invariants and Constraints

- Do not add a second spec-resolution or execution path.
- Do not make builtin inspection depend on Docker availability.
- Do not widen Windows support for Docker. This slice only makes the boundary
  explicit and earlier.
- Keep the Docker runner plugin as the runtime authority for actual Docker
  execution behavior.
- Keep builtin resolution order unchanged: local shadows still beat builtins.
- No new dependency and no drive-by refactor.

## 5. Tasks

1. Add explicit builtin platform metadata and shared parsing helpers.
   - Touch `weft/builtins/__init__.py`,
     `weft/builtins/tasks/prepare-agent-images.json`,
     `weft/builtins/tasks/dockerized-agent/taskspec.json`.
   - Add a small shared helper for supported-platform metadata plus a
     user-facing unsupported-platform message.
   - Extend builtin inventory data so JSON and text output can report the
     supported-platform contract.
   - Verification: builtin catalog and system builtins tests can read the new
     metadata without changing builtin shadow behavior.

2. Enforce the Windows guard at the builtin entry points.
   - Touch `weft/builtins/agent_images.py` and
     `weft/builtins/tasks/dockerized-agent/dockerized_agent.py`.
   - `prepare-agent-images` should reject Windows before Docker plugin loading.
   - `dockerized-agent` should reject Windows during parameterization, before
     the run path continues into normal submission.
   - Stop if this starts pushing the guard into shared CLI resolution or
     manager code. The guard belongs at the builtin-owned boundary.
   - Verification: direct unit tests prove the clear Windows rejection message.

3. Update tests and docs to match the explicit boundary.
   - Touch `tests/core/test_builtin_agent_images.py`,
     `tests/core/test_builtin_dockerized_agent.py`,
     `tests/cli/test_cli_spec.py`,
     `tests/cli/test_cli_system.py`,
     `docs/specifications/10B-Builtin_TaskSpecs.md`.
   - Positive-path Docker builtin tests should be skipped on real Windows.
   - Add a small Windows-rejection test that runs cross-platform by simulating
     the Windows platform value.
   - Update the builtin spec doc sections to say these builtins are Linux and
     macOS only today.
   - Verification: targeted builtin/core/CLI tests pass and Windows-specific
     expectations are explicit in the test suite.

## 6. Verification

- `./.venv/bin/python -m pytest tests/core/test_builtin_agent_images.py tests/core/test_builtin_dockerized_agent.py tests/cli/test_cli_spec.py tests/cli/test_cli_system.py -q`
- `./.venv/bin/python -m pytest tests/tasks/test_command_runner_parity.py::test_docker_runner_is_skipped_on_windows tests/system/test_release_script.py::test_docker_extension_tests_are_disabled_on_windows -q`

## 7. Rollback

If the new guard causes unexpected breakage, remove the builtin-entry-point
checks and the supported-platform metadata changes together. The underlying
Docker runner Windows rejection remains in place, so rollback is low-risk and
does not affect task persistence or queue contracts.
