# Local Live-Provider Release Tests Plan

Status: draft
Source specs: `docs/specifications/13-Agent_Runtime.md` [AR-2.1] for provider-adapter fixes; release-tooling changes have no governing product spec
Superseded by: none

## Goal

Add a dedicated local release-precheck suite that invokes every locally
available registered `provider_cli` executable through the existing live
Consumer tests. The suite must make real provider calls, run once per local
release, and fail instead of reporting a false green when no provider can be
tested or an explicitly selected provider is unavailable.

## Source Documents

No product specification governs the repository release helper. Provider-adapter
findings exposed by the live gate are governed by
`docs/specifications/13-Agent_Runtime.md` [AR-2.1]. The current tooling and
user-facing contract are:

- [`README.md`](../../README.md), `## Release`
- [`bin/release.py`](../../bin/release.py)
- [`tests/tasks/test_agent_execution.py`](../../tests/tasks/test_agent_execution.py)
- [`weft/core/agents/provider_cli/registry.py`](../../weft/core/agents/provider_cli/registry.py)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/adversarial-acceptance-probes.md`](../agent-context/runbooks/adversarial-acceptance-probes.md)

Source baseline: `2f95ce8f7a67b4aa413cdd60a36f6646c0b38240` plus the clean worktree at plan authoring time.

## Context and Key Files

Files to add:

- `bin/pytest-live-providers`: local runner that selects provider test node IDs,
  exports the existing live-test environment contract, and invokes pytest in
  the current project interpreter.
- `tests/system/test_pytest_live_providers.py`: contract tests for target
  selection, exact pytest coverage, and fail-closed behavior.

Files to modify:

- `bin/release.py`: add the dedicated runner to default local prechecks exactly
  once.
- `tests/system/test_release_script.py`: prove the default release precheck
  includes the dedicated runner.
- `tests/tasks/test_agent_execution.py`: centralize live-test selection around
  registered provider defaults and make a selected missing executable fail.
- `tests/fixtures/mcp_stdio_fixture.py` and
  `tests/fixtures/runtime_profiles_fixture.py`: record a fixture-server tool
  call so the live Claude MCP test cannot pass by echoing its prompt.
- `README.md`: document the local live-provider gate, target selection, and
  authentication requirement.
- `docs/plans/README.md`: index this plan.

Current structure:

- `bin/release.py::build_precheck_commands()` owns the ordered local release
  precheck set. Its shared environment does not enable live providers.
- `tests/tasks/test_agent_execution.py` already owns the real one-shot,
  persistent-conversation, and Claude MCP live tests. They execute the normal
  `Consumer -> TaskRunner -> provider_cli` path but self-skip unless environment
  variables select them.
- `weft.core.agents.provider_cli.registry` owns the registered provider list and
  each provider's default executable. The new runner and live-test helper must
  reuse those values rather than duplicate provider names or binary mappings.

Comprehension checks before editing:

1. Why would setting `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1` on the full release
   environment run provider calls more than once? Because both SQLite and
   PG-compatible suites collect the same live tests.
2. What proves a real provider ran? The existing live test receives provider
   output containing its unique token; executable discovery alone is not proof.

## Invariants and Constraints

- Keep product runtime behavior, TaskSpec contracts, queues, and state
  transitions unchanged.
- Reuse the existing live Consumer tests. Do not add a second provider test
  implementation or mock provider execution in the release gate.
- Add no dependency and no installed `weft` CLI command.
- Run the live suite once, outside the SQLite and PG full-suite commands. Force
  both live-test opt-ins to `0` in the generic release precheck environment so
  caller-exported values cannot trigger duplicate paid calls in those suites;
  the dedicated runner alone sets them to `1` for its pytest child.
- Run provider tests serially (`-n 0`) to avoid a local burst of concurrent paid
  or rate-limited calls.
- Run the paid live-provider command after every deterministic test and static
  gate so an earlier free failure does not incur provider calls.
- Auto-select every registered provider whose registered default executable is
  on `PATH`. The live tests run from isolated temporary working directories, so
  project-local executable overrides are intentionally not part of this gate.
  If `WEFT_LIVE_PROVIDER_CLI_TARGETS` is set, treat it as an explicit required
  set and fail when any named provider is unknown or its registered default
  executable is unavailable.
- Fail when auto-discovery finds no runnable providers. A release precheck may
  not turn "zero providers tested" into success.
- Select exact pytest node IDs so unselected provider parameters do not create
  expected skips. Cover one-shot and persistent tests for each target, plus the
  existing MCP test when `claude_code` is selected.
- Override the repository's default `-m 'not slow'` with `-m ""`, force `-n 0`,
  and clear inherited `PYTEST_ADDOPTS` for the pytest child. Put `-x` and
  `--maxfail=1` on the dedicated command itself so collection-only or marker
  options from the caller cannot yield a false green.
- Once global opt-in and target selection choose a provider, missing executable
  resolution is a test failure, not a skip. One shared test helper owns the
  repeated opt-in, target, registry-default, and executable checks for one-shot,
  persistent, and MCP cases.
- Authentication and provider response failures are fatal. The test output is
  the diagnostic surface; do not add speculative auth probes.
- Keep GitHub Actions unchanged. This gate is local because it consumes the
  maintainer machine's installed and authenticated provider CLIs.
- No one-way door exists. Rollback is removal of the runner command and script;
  no persisted data or public runtime contract changes.
- External review is required because this changes the release gate and can
  incur provider cost while still looking plausibly green if target selection
  is wrong.

## Deviation Log

No governing product spec exists. The implementation made the following
test/release-tooling departures while preserving product runtime behavior.

| Planned behavior | Actual behavior | Rationale |
| --- | --- | --- |
| Use each provider's normal isolated live-test configuration. | The Codex live test adds `skip_git_repo_check=true`. | Codex 0.144.3 refuses an isolated pytest temporary directory unless this explicit non-git override is present. This changes only the live test TaskSpec. |
| Reuse the prior 30-second live wait. | Live waits allow 120 seconds and fail immediately when the TaskSpec reaches a terminal failure state. | Claude exceeded the old margin locally, and the old polling path hid provider stderr behind a generic timeout. |
| Assert the Claude MCP response token. | The MCP fixture also writes a call marker, and the test asserts its exact contents. | The prompt contains the response token, so response text alone could pass without a tool call. |
| Insert the paid gate with the other test commands. | The paid gate is the final local precheck, after lint, format, and mypy. | Deterministic failures should be found before provider cost is incurred. |
| Keep provider adapter semantics out of scope. | The live gate exposed Codex, Gemini, and Qwen CLI drift, and this change updates the adapters plus `docs/specifications/13-Agent_Runtime.md` [AR-2.1]. | Those were root-cause release blockers, not harness failures. Leaving them unfixed would require weakening the gate. |

## Tasks

1. Add the release-helper contract by red-green TDD.
   - Files: `tests/system/test_release_script.py`, `bin/release.py`.
   - RED: assert that the default precheck command list contains one dedicated
     command with the exact tuple
     `uv run --extra dev python bin/pytest-live-providers`. Invoking the script
     through Python avoids making filesystem executable mode part of the
     release contract.
   - GREEN: add the smallest command constant and ordered insertion, and pin
     generic precheck environment values that disable the live and MCP opt-ins
     for all broad suites.
   - Stop if this requires enabling live-test variables for the SQLite or PG
     suite; that would duplicate paid calls.
   - Done when the targeted release-helper test passes.

2. Add the fail-closed local runner in vertical TDD slices.
   - Files: `tests/system/test_pytest_live_providers.py`,
     `bin/pytest-live-providers`.
   - First slice: exact targets produce exact one-shot and persistent node IDs,
     with Claude MCP included only for `claude_code`; the command uses
     `sys.executable -m pytest`, `-m ""`, `-n 0`, `-x`, and exact node IDs.
   - Second slice: with no explicit targets, iterate
     `list_provider_cli_providers()` and discover each registered
     `provider.default_executable` with `shutil.which()`. The live-test helper
     uses the same registry field and lookup operation; do not call
     `resolve_provider_cli_executable()`, which would add project-setting
     semantics that isolated `tmp_path` live tests do not exercise.
   - Third slice: explicit unknown/unavailable targets and empty auto-discovery
     fail before pytest.
   - Fourth slice: `main()` exports `WEFT_RUN_LIVE_PROVIDER_CLI_TESTS=1`, the
     resolved target list, and the MCP opt-in when applicable, then returns the
     real pytest exit code. It replaces inherited `PYTEST_ADDOPTS` with an empty
     value so caller options cannot change collection or execution.
   - Fifth slice: a black-box invalid-target probe exits cleanly with an
     invocation-error code and no traceback.
   - Mock only subprocess launch and executable availability in these script
     contract tests. Do not mock the actual final live run.
   - Stop if provider names or executable defaults are duplicated outside the
     production registry.

3. Make selected live cases fail closed, then document and exercise the gate.
   - Files: `tests/tasks/test_agent_execution.py`, `README.md`, and this plan.
   - Replace repeated hard-coded executable checks with one helper using
     `get_provider_cli_provider(provider_name).default_executable`. Pass the
     resolved absolute executable into the live TaskSpec so preflight and
     execution use the same binary. Keep global opt-out and non-target cases as
     skips; make a selected missing executable fail.
   - Run `bin/pytest-live-providers` through the repo environment on this
     machine. All five currently resolve (`claude_code`, `codex`, `gemini`,
     `opencode`, `qwen`), so expected coverage is ten provider-parametrized
     cases plus the Claude MCP case.
   - Budget: those 11 cases make 16 real CLI calls (five one-shot, ten
     persistent turns, one Claude MCP), run serially. The 120-second per-turn
     ceiling permits about 32 minutes before setup/teardown overhead in the
     worst case; normal successful runs should be much shorter.
   - Treat provider/auth failures as findings to fix or report. Do not weaken
     the gate into a skip.
   - Update the plan status only after fresh verification and independent
     completed-work review.

## Testing Plan

Per-slice commands:

```bash
source ./.envrc
./.venv/bin/python -m pytest tests/system/test_release_script.py -k precheck -q
./.venv/bin/python -m pytest tests/system/test_pytest_live_providers.py -q
```

Real-path proof:

```bash
source ./.envrc
./.venv/bin/python bin/pytest-live-providers
```

Final gates:

```bash
source ./.envrc
./.venv/bin/python -m pytest tests/system/test_release_script.py tests/system/test_pytest_live_providers.py -q
./.venv/bin/ruff check bin/release.py bin/pytest-live-providers tests/system/test_release_script.py tests/system/test_pytest_live_providers.py tests/tasks/test_agent_execution.py
./.venv/bin/ruff format --check bin/release.py bin/pytest-live-providers tests/system/test_release_script.py tests/system/test_pytest_live_providers.py tests/tasks/test_agent_execution.py
./.venv/bin/mypy weft integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

The real live command is the acceptance proof. Unit tests may verify command
construction but cannot substitute for unique-token responses from real CLIs.
The black-box invalid-target probe must also assert a nonzero invocation error
and no `Traceback` on stderr.

## Independent Review Loop

Ask an independent agent to review this plan, the current release-helper
precheck construction, the live tests, and provider registry. The reviewer
should look specifically for false-green paths, duplicated provider calls,
accidental CI credential requirements, excessive parallelism, and hidden skips.
After implementation, request a second review of the actual diff and rerun any
finding before acting on it.

## Rollout and Rollback

The new command enters only the local `bin/release.py` precheck order. The
existing `--skip-checks` escape hatch remains unchanged. A failed provider call
blocks local tagging, which is the intended rollout signal. Rollback removes
the command from `build_precheck_commands()` and leaves all product behavior and
remote release workflows untouched.

## Out of Scope

- Installing provider CLIs or managing their credentials.
- Adding live-provider secrets to GitHub Actions.
- Changing provider prompts or response parsing.
- Reworking the broader release command representation.
- Adding a public `weft` CLI surface for test orchestration.

## Fresh-Eyes Review

Completed after the initial draft.

- P1: the repository default deselects `slow` tests, and inherited
  `PYTEST_ADDOPTS` could request collection only. Updated the runner contract to
  clear inherited options and pass explicit marker, serial, and fail-fast args.
- P1: project-config executable resolution did not match isolated live-test
  working directories. Updated the boundary to registered default executables
  on `PATH`, shared by runner and tests.
- P1: selected missing executables could still skip successfully. Made the
  live-test helper change mandatory and fail-closed after selection.
- P1: caller-exported opt-ins could duplicate calls in SQLite and PG suites.
  Added forced-off generic release environment requirements.
- P2: recorded the 16-call, serial timeout/cost budget and exact interpreter
  invocation seam.
- P2: required a black-box clean-error probe for the new script.

The completed-work review found four additional issues. All were accepted:

- P1: the outer 120-second polling wait was still bounded by a 30-second
  TaskSpec timeout. Live TaskSpecs now use the same 120-second limit.
- P1: the Claude MCP response check could pass by echoing the prompt token. The
  fixture now records the tool call, and both fixture-backed and live tests
  assert the marker.
- P2: paid provider calls preceded free static gates. The live command is now
  the final precheck.
- P2: zero-provider failure lacked a firing test. `main()` now has direct
  coverage proving it returns nonzero without launching pytest.

Independent review status: completed by a separate agent. All findings were
accepted and incorporated. Residual risk is provider-side auth, latency, and
response drift; the real local suite is the intended evidence for those risks.

## Verification Results

Contract tests pass for release ordering, target discovery, exact node
selection, environment isolation, and fail-closed errors. The real provider
probes produced these results on 2026-07-13:

| Provider | Result | Evidence |
| --- | --- | --- |
| Claude Code | Pass | One-shot, two-turn persistent session, and MCP tests passed. |
| Codex | Pass | One-shot and two-turn persistent tests passed after updating resumed turns to the supported `codex exec resume` flag set. |
| Gemini | Pass | One-shot and two-turn persistent tests passed after adding `--skip-trust` and removing the conflicting yolo flag. |
| OpenCode | Pass | One-shot and two-turn persistent tests passed. |
| Qwen | Blocked by local credentials | The adapter no longer sends yolo and the live smoke pins `z-ai/glm-4.5-air`, but the local account now returns `401 Incorrect API key provided`. |

The full auto-discovered live gate remains red only because the local Qwen API
key is invalid. The selected live gate
`WEFT_LIVE_PROVIDER_CLI_TARGETS=claude_code,codex,gemini,opencode uv run --extra dev python bin/pytest-live-providers`
passed 9 cases in 198.37 seconds on 2026-07-13.
