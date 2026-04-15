# Claude Host Credentials Helper and Claude Descriptor Hardening Plan

## Goal

Make the shipped `example-dockerized-agent-claude_code` builtin actually
runnable for users whose host `claude` login is macOS-Keychain-backed, without
introducing hidden credential harvesting on ordinary `weft run` paths.

Concretely, this slice ships:

1. A new explicit, optional builtin task helper
   `prepare-claude-host-credentials` that reads the Claude OAuth credential
   from the macOS Keychain one time and materializes it as a Linux-shaped
   `~/.claude/.credentials.json` on the host. Subsequent Docker-backed Claude
   agent runs use the existing runtime descriptor mounts to see it.
2. Hardening of the Claude container runtime descriptor so host mounts are
   read-only and an accidental container-side auth refresh cannot corrupt the
   host `~/.claude` or `~/.claude.json`.
3. Documentation updates so the Claude Code example stops apologizing for
   Keychain auth and instead names three concrete paths that work:
   `prepare-claude-host-credentials`, `claude setup-token`, and
   `ANTHROPIC_API_KEY`.

This slice deliberately does not:

- add hidden Keychain reads to the ordinary Docker-agent execution path
- mutate host state outside the single Claude credential target path
- widen descriptor capabilities to run arbitrary host commands

The opinionated choice: credential harvesting is an **explicit one-shot
helper**, not a descriptor capability. The descriptor already supports the
Linux-shaped `~/.claude/.credentials.json` layout for free; we only need to
put the file there with an explicit user action.

## Source Documents

Source specs:

- [`docs/specifications/00-Overview_and_Architecture.md`](../specifications/00-Overview_and_Architecture.md)
  §Product Identity, §Product Boundary
- [`docs/specifications/10B-Builtin_TaskSpecs.md`](../specifications/10B-Builtin_TaskSpecs.md)
  §Current Contract, §Adding A Builtin, §`example-dockerized-agent-claude_code`
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-7] `provider_cli` backend
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  §Context Invariants [CTX.4]

Current related plans (status noted):

- [`docs/plans/2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md`](./2026-04-15-multi-provider-docker-provider-cli-expansion-plan.md)
  — in flight; informs descriptor shape but does not block this slice.
- [`docs/plans/2026-04-14-provider-cli-container-runtime-descriptor-plan.md`](./2026-04-14-provider-cli-container-runtime-descriptor-plan.md)
  — landed; defines the descriptor schema this plan tightens.
- [`docs/plans/2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md`](./2026-04-14-builtin-taskspecs-and-spec-resolution-plan.md)
  — landed; defines the builtin contract the new helper must satisfy.

Repo guidance the implementer must read before touching code:

- [`AGENTS.md`](../../AGENTS.md) §4 House Style, §5 Common Tasks
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)

External technical references (informational only):

- Anthropic Claude Code docs on `setup-token` and `CLAUDE_CODE_OAUTH_TOKEN`
- macOS `security` command documentation for `find-generic-password -w`

Source-spec gap:

- 10B does not yet describe `prepare-claude-host-credentials`.
- 10B's `example-dockerized-agent-claude_code` section currently documents the
  Keychain limitation but not the resolution path. This slice is not done
  until that section names the three working auth paths explicitly.
- The Claude container runtime descriptor today declares host mounts as
  writable (`"read_only": false`). This is the mutable-state footgun this
  slice fixes.

## Context and Key Files

Files to modify:

- new builtin helper:
  - `weft/builtins/claude_host_credentials.py` (new module)
  - `weft/builtins/tasks/prepare-claude-host-credentials.json` (new builtin
    TaskSpec asset)
- descriptor tightening:
  - `weft/core/agents/provider_cli/runtime_descriptors/claude_code.json`
- docs:
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/13-Agent_Runtime.md`
  - `README.md`
- tests:
  - `tests/core/test_builtin_claude_host_credentials.py` (new)
  - `tests/core/test_provider_cli_container_runtime.py` (extend)
  - `tests/system/test_builtin_contract.py` (extend)
  - `tests/core/test_builtin_example_dockerized_agent.py` (verify no
    regression from the read-only mount change)
- CHANGELOG (additive entry only)

Read first (in order):

1. `docs/specifications/10B-Builtin_TaskSpecs.md` — full file; pay attention
   to §Current Contract and §Adding A Builtin. Every rule there applies to
   the new helper.
2. `docs/specifications/13-Agent_Runtime.md` [AR-7] — the
   `example-dockerized-agent-claude_code` flow and descriptor assumptions.
3. `weft/builtins/agent_probe.py` — the canonical template for a
   settings-adjacent read-or-write builtin. The new helper should follow the
   same skeleton: a top-level task function, a pure work function, a
   per-target report dict, and careful handling of "preserved" vs "created"
   vs "refreshed".
4. `weft/builtins/tasks/probe-agents.json` — canonical example of a
   function-type builtin TaskSpec.
5. `weft/core/agents/provider_cli/container_runtime.py` —
   `ProviderContainerMountRequirement` and the mount resolution path. The
   descriptor schema already supports `read_only: true`; no code change
   needed here.
6. `weft/core/agents/provider_cli/runtime_descriptors/claude_code.json` —
   current shape this plan tightens.
7. `extensions/weft_docker/weft_docker/agent_runner.py` — how the
   `ResolvedProviderContainerMount.read_only` flag flows into the actual
   bind-mount option. Confirm read-only mounts are honored at the Docker
   layer before assuming the descriptor change is sufficient.
8. `weft/commands/specs.py` — spec resolution and `ResolvedSpecReference`;
   the new builtin must resolve through the existing order without special
   casing.
9. `weft/commands/status.py` (`system builtins` output) — the new builtin
   must appear in `weft system builtins` without additional registration.

Shared paths and helpers to reuse:

- `weft.context.build_context` for resolving the project context — matches
  `probe_agents_task`.
- `weft.core.agents.provider_cli.settings` module conventions for safe file
  writes with `0600` permissions — mirror its file-write idiom rather than
  inventing a new writer.
- The existing `weft.builtins.tasks/*.json` single-file builtin shape — do
  not use a bundle directory for this helper. It does not need bundle-local
  Python; the shared helper module lives in `weft.builtins`.
- Existing report conventions in `probe_agents_task`:
  - flat top-level `summary` + per-item detail
  - explicit `action` enum per item (`created` / `preserved` / `unchanged`)
  - explicit `status` enum per item

Current structure:

- `weft/builtins/tasks/*.json` is the single-file builtin layout. Bundle-form
  builtins live in subdirectories with a `taskspec.json`. The new helper is
  single-file.
- The current Claude container descriptor mounts host `~/.claude` →
  `/root/.claude` writable and host `~/.claude.json` → `/root/.claude.json`
  writable. Both are marked `required: false`.
- On macOS, `~/.claude/.credentials.json` does not exist; credentials live
  in the system Keychain under service `Claude Code-credentials`, account
  `Claude`. On Linux, `~/.claude/.credentials.json` is the canonical
  on-disk credential file shape.
- The existing `CLAUDE_CODE_OAUTH_TOKEN` env forward in the descriptor will
  continue to work unchanged and is independent of the credentials-file
  path. This plan only affects the on-disk mount story.

Comprehension questions the implementer must be able to answer before
editing:

1. Which file resolves `ResolvedProviderContainerMount.read_only` into the
   actual Docker bind-mount option? *(Answer:
   `extensions/weft_docker/weft_docker/agent_runner.py`.)*
2. Which spec section forbids "hidden setup or probing on ordinary
   execution paths"? *(Answer: `00-Overview_and_Architecture.md` §Product
   Boundary.)*
3. What happens today if a user sets
   `WEFT_BACKEND=postgres` with no Postgres plugin installed, conceptually
   compared to a Claude run with no credential? *(Expected answer: Weft
   fails loudly with an install hint — the same shape applies here: the
   helper must fail loudly with a clear next-step hint, never silently.)*
4. When a builtin TaskSpec is resolved by name, what order does Weft walk?
   *(Answer: file path → stored `.weft/tasks/<name>.json` → bundle
   `.weft/tasks/<name>/taskspec.json` → builtin. Local stored shadows
   builtin.)*

If the implementer cannot answer these, stop and re-read Context before
editing.

## Invariants and Constraints

These are hard constraints for this slice.

1. **No hidden Keychain access on ordinary run paths.** The Keychain read
   happens only when the user explicitly runs
   `weft run --spec prepare-claude-host-credentials`. It does not fire from
   `example-dockerized-agent-claude_code`, from `probe-agents`, from
   `prepare-agent-images`, or from any descriptor resolution path.
2. **No descriptor capability expansion.** The descriptor schema does not
   gain a `"host_command"` source kind or any other ability to execute
   arbitrary host commands. If that is ever wanted, it belongs in a separate
   plan.
3. **One target file only.** The helper writes exactly
   `${HOME}/.claude/.credentials.json`. It does not touch `~/.claude.json`,
   does not create `~/.claude/` subdirectories beyond the bare minimum, and
   does not modify any other Claude state.
4. **No second execution path.** `prepare-claude-host-credentials` runs as
   an ordinary function-type TaskSpec through the durable spine:
   `TaskSpec → Manager → Consumer → TaskRunner → queues/state log`. Do not
   add a bypass that runs the helper synchronously from the CLI outside the
   task runtime.
5. **File permissions.** The written credentials file is `0600` (owner
   read/write only). Parent `~/.claude/` is created with `0700` if missing.
6. **Platform gating is loud, not silent.** On non-Darwin the helper fails
   with exit status 1 and a report pointing at `claude setup-token` and
   `ANTHROPIC_API_KEY` as alternatives. It does not fall back to a partial
   behavior.
7. **Descriptor read-only change is a one-way compatibility door.** Users
   running prior examples may depend on the container writing session state
   back into host `~/.claude.json`. We accept breaking that because it was
   never a declared contract, but the README/10B change must name this
   explicitly and the CHANGELOG must mark it.
8. **Builtin contract invariants (from 10B) hold.** The helper is read-only
   at spec level (ships as package data), is never run implicitly, does not
   change bare command lookup, does not become a required startup step.
9. **No downstream dependency on the helper from other builtins.**
   `example-dockerized-agent-claude_code` does not check for, trigger, or
   recommend the helper at spawn time. The recommendation lives in docs
   only.
10. **Idempotent.** Running the helper twice in a row with a fresh
    credential produces `action: "preserved"` on the second run, not
    `"refreshed"`. A stale/missing credential produces
    `action: "created"` or `"refreshed"`. Never silently overwrite a fresher
    credential with a staler one.
11. **Respect existing explicit state.** If the target file exists and is
    fresher than the Keychain copy (e.g., the user ran
    `claude setup-token` and manually populated it), the helper reports
    `action: "preserved"` and does not overwrite.

Stop-and-re-evaluate gates:

- you are about to add a Keychain read from anywhere other than the
  helper's task function
- you are about to add a descriptor source kind that runs host commands
- the helper is about to write anywhere other than
  `~/.claude/.credentials.json`
- the helper is about to import from `extensions/weft_docker` (it must not
  — the helper is host-side and has no Docker dependency)
- a test is about to mock `security` via module-level monkeypatch that
  leaks across tests (use an injected command-runner seam instead)
- the read-only descriptor change breaks an existing test you cannot
  explain — stop and prove the prior test was asserting accidental
  writable-mount behavior, not a declared contract

## Current Structure and Hidden Couplings

- `ProviderContainerMountRequirement.read_only` defaults to `True` in the
  Python dataclass, but the JSON descriptor currently sets `false`
  explicitly for both Claude mounts. So the change is JSON-only; no code
  default shifts.
- `ResolvedProviderContainerMount.read_only` flows through
  `extensions/weft_docker/weft_docker/agent_runner.py` into the Docker bind
  spec. The comprehension question above is the coupling check.
- The `CLAUDE_CODE_OAUTH_TOKEN` env forward is already declared in the
  descriptor and remains the env-shaped alternative. The helper adds a
  file-shaped alternative. Both coexist: the container's Claude CLI will
  pick whichever it finds first by its own precedence rules. This is not a
  contract Weft owns; it is Claude CLI behavior. Document, do not replicate.
- `~/.claude.json` contains a large amount of non-credential host state
  (growth-book features, UI dismissal counts, cached statsig gates, etc.).
  Mounting it read-only into the container is safe for reads; mounting it
  writable risks either corruption or platform-drift between macOS and
  Linux representations. The read-only change is the right default
  regardless of this slice; this slice takes the opportunity.

## Tasks

Tasks are dependency-ordered.

### 1. Verify the Docker runner honors `read_only` mount flag

- **Outcome**: a test proves that when a provider descriptor declares
  `read_only: true`, the resolved Docker bind mount is created with the
  read-only flag at the Docker SDK layer, not just in the dataclass.
- **Files to touch**:
  - `extensions/weft_docker/tests/test_agent_runner.py`
- **Read first**:
  - `extensions/weft_docker/weft_docker/agent_runner.py` — specifically the
    function that assembles `host_config` / bind specs
  - existing mount-related tests in the same file
- **Test to add**: one focused test that stages a resolved descriptor with
  one `read_only: true` mount and one `read_only: false` mount, invokes the
  runner's mount-assembly function (or its nearest public seam), and
  asserts the Docker mount specs carry the correct read/write modes.
- **Constraints**:
  - do not modify runner code in this task unless the test reveals the
    flag is not honored
  - if the flag is not honored today, log that discovery and stop this
    slice before changing the descriptor — the entire plan assumes this
    flag works
- **Stop if**: the runner ignores `read_only`. Re-plan before proceeding.
- **Done when**: the new test passes green and proves the flag flows
  through to the Docker layer.
- **Verification**:
  `./.venv/bin/python -m pytest extensions/weft_docker/tests/test_agent_runner.py -q`

### 2. Harden the Claude runtime descriptor to read-only mounts

- **Outcome**: host `~/.claude` and `~/.claude.json` are mounted read-only
  into the Docker-backed Claude container. The container's Claude CLI can
  read credentials but cannot write back and corrupt host state.
- **Files to touch**:
  - `weft/core/agents/provider_cli/runtime_descriptors/claude_code.json`
  - `tests/core/test_provider_cli_container_runtime.py`
- **Read first**:
  - the current `claude_code.json` contents
  - `ProviderContainerMountRequirement` in `container_runtime.py`
  - the existing descriptor-parse tests in
    `tests/core/test_provider_cli_container_runtime.py`
- **Change**: flip both mount entries to `"read_only": true`.
- **Tests to add**:
  - a descriptor-parse test asserting both Claude mounts resolve with
    `read_only=True`
  - a regression guard: a brittle test is fine here because the invariant
    is precisely that this flag does not drift back to writable. Name the
    test clearly, e.g.,
    `test_claude_descriptor_host_mounts_are_read_only_by_contract`.
- **Constraint — one-way door**: this is a public-contract change for
  anyone running the shipped example today. Document in §Rollout.
- **Stop if**: the existing
  `tests/core/test_builtin_example_dockerized_agent.py` starts failing in a
  way that indicates the example depended on writable mounts for
  correctness. If that happens, pause and re-plan — either the test was
  asserting accidental behavior (fix the test) or the example needs the
  `copied_files` / isolated runtime home shape instead (defer to a separate
  plan).
- **Done when**: descriptor tests and the example-dockerized-agent test
  suite stay green, with the new mount-mode assertions added.
- **Verification**:
  `./.venv/bin/python -m pytest tests/core/test_provider_cli_container_runtime.py tests/core/test_builtin_example_dockerized_agent.py -q`

### 3. Add the `prepare-claude-host-credentials` builtin TaskSpec asset

- **Outcome**: a read-only builtin TaskSpec ships at
  `weft/builtins/tasks/prepare-claude-host-credentials.json` and resolves
  via `weft run --spec prepare-claude-host-credentials`.
- **Files to touch**:
  - `weft/builtins/tasks/prepare-claude-host-credentials.json`
  - `tests/system/test_builtin_contract.py` — extend the inventory
    assertions to include the new builtin
- **Read first**:
  - `weft/builtins/tasks/probe-agents.json` — canonical shape
  - 10B §Adding A Builtin
- **Shape**:
  - `spec.type = "function"`
  - `spec.function_target = "weft.builtins.claude_host_credentials:prepare_claude_host_credentials_task"`
  - `spec.timeout` omitted (Keychain read is fast, but Keychain may prompt;
    do not pin a short timeout that would trip on a user prompt — see
    Task 4 for prompt handling)
  - `metadata.builtin = true`
  - `metadata.category = "agent-runtime"`
  - No `run_input` (the helper takes no CLI arguments)
  - `description` names the Keychain source and the target file path
    verbatim
- **Tests to add**:
  - inventory test: `weft system builtins` lists this builtin
  - resolution test: `resolve_spec_reference("prepare-claude-host-credentials", spec_type="task")`
    returns `source == "builtin"`
  - validation test: the TaskSpec parses through `TaskSpec.model_validate`
    in template mode without error
- **Done when**: both the inventory and resolution tests pass green.
- **Verification**:
  `./.venv/bin/python -m pytest tests/system/test_builtin_contract.py -q`

### 4. Implement the helper module

- **Outcome**: `weft/builtins/claude_host_credentials.py` implements the
  runtime behavior: Keychain read, parse, freshness check, file write with
  correct permissions, structured report.
- **Files to touch**:
  - `weft/builtins/claude_host_credentials.py` (new)
  - `tests/core/test_builtin_claude_host_credentials.py` (new)
- **Read first**:
  - `weft/builtins/agent_probe.py` — function-level skeleton
  - `weft/core/agents/provider_cli/settings.py` — safe-file-write idiom
  - `docs/agent-context/engineering-principles.md` on failure-mode clarity
- **Public surface** (exact names and shapes):
  - Module docstring cites `10B` and `AR-7`.
  - Top-level task function:
    ```python
    def prepare_claude_host_credentials_task() -> dict[str, Any]: ...
    ```
    Reads no inputs. Returns a report dict. Matches `probe_agents_task`
    conventions.
  - Pure work function (testable without patching):
    ```python
    def prepare_claude_host_credentials(
        *,
        home_dir: Path,
        now_ms: int,
        platform: str,
        keychain_reader: Callable[[], str] | None,
        existing_reader: Callable[[Path], str | None] | None,
        target_writer: Callable[[Path, str], None] | None,
    ) -> dict[str, Any]: ...
    ```
    All host-system couplings are injected. Defaults use real
    implementations; tests inject fakes. Do not patch `subprocess.run` at
    module level from tests.
- **Behavior**:
  - If `platform != "darwin"`: return a report with
    `status: "unsupported_platform"`, `action: "none"`, and a `next_action`
    string naming `claude setup-token` and `ANTHROPIC_API_KEY`. Raise
    nothing. The ordinary task runner maps a structured failure report to
    the appropriate exit code via the caller reading the report; do NOT
    manually `sys.exit` from the helper.
  - On darwin, run the default `keychain_reader` which invokes
    `security find-generic-password -s "Claude Code-credentials" -a "Claude" -w`.
    - On non-zero exit or empty stdout: return `status: "keychain_missing"`,
      `action: "none"`, `next_action: "Run `claude /login` on the host first."`.
    - Do not wrap or mask the `security` stderr; surface the first line in
      a `details` field.
  - Parse the stdout as JSON. Extract `claudeAiOauth.accessToken`,
    `claudeAiOauth.refreshToken`, `claudeAiOauth.expiresAt`,
    `claudeAiOauth.scopes`. If the shape does not match, return
    `status: "keychain_shape_unexpected"` with the list of missing keys in
    `details`.
  - Compute `expires_in_minutes` from `expiresAt - now_ms`. If `< 0`, set
    `expired: true`.
  - Check existing file at `home_dir / ".claude" / ".credentials.json"`.
    If present and `expiresAt` is later than the Keychain copy's
    `expiresAt`, return `action: "preserved"`, `status: "existing_is_fresher"`.
  - Otherwise write the exact JSON returned by the Keychain to the target
    file via the injected `target_writer`. The default writer:
    - creates `~/.claude/` with mode `0700` if missing
    - writes to a sibling tempfile with mode `0600`, then renames over
      the target
    - never calls `chmod` on a file owned by another user; if the target
      exists and is not owned by the current uid, fail with a clear error
- **Report shape** (returned from the task function):
  ```python
  {
    "platform": "darwin" | "linux" | ...,
    "status": "created" | "refreshed" | "preserved" |
              "unsupported_platform" | "keychain_missing" |
              "keychain_shape_unexpected" | "write_failed",
    "action": "created" | "refreshed" | "preserved" | "none",
    "target_path": "/Users/van/.claude/.credentials.json",
    "oauth_account": {
      "email_address": "...",   # subset of oauthAccount from ~/.claude.json
      "organization_name": "...",
      # any other non-sensitive metadata fields; no tokens
    } | None,
    "expires_at_ms": 1745...,    # or None
    "expires_in_minutes": 47,    # or None
    "expired": False,            # or None
    "next_action": "...",        # always present on non-success statuses
    "details": "...",            # optional; populated for error statuses
  }
  ```
  The tokens themselves are never included in the report.
- **Tests to add** (behavior-level, injected seams):
  1. unsupported platform returns `status: "unsupported_platform"` and does
     not touch the filesystem
  2. keychain returns empty → `keychain_missing` with next-action string
  3. keychain JSON missing `refreshToken` → `keychain_shape_unexpected`
     with missing-key list
  4. fresh keychain, missing target file → `action: "created"`, file
     written, mode `0600`, directory `0700`
  5. fresh keychain, existing stale target → `action: "refreshed"`, file
     replaced atomically
  6. fresh keychain, existing fresher target → `action: "preserved"`, file
     bytes unchanged
  7. write failure (simulated via injected `target_writer` raising) →
     `status: "write_failed"`, no partial target file remains
  8. report never contains the access token or refresh token string
- **Constraints**:
  - no mocks of `subprocess.run`, `Path.home`, or `time.time` at the
    `patch` level; use the injected seams
  - no logging of token values; if a developer later adds debug logs,
    tokens must be explicitly redacted
- **Stop if**:
  - you are about to call the Keychain from anywhere other than the
    default `keychain_reader`
  - you are about to add flags/arguments to the task function
  - the report shape is about to include raw tokens
- **Done when**: all 8 tests pass green.
- **Verification**:
  `./.venv/bin/python -m pytest tests/core/test_builtin_claude_host_credentials.py -q`

### 5. Document the helper in 10B and rewrite the Claude example caveat

- **Outcome**: `docs/specifications/10B-Builtin_TaskSpecs.md` gains a
  dedicated `### prepare-claude-host-credentials` section and the existing
  `### example-dockerized-agent-claude_code` section names the three
  working auth paths.
- **Files to touch**:
  - `docs/specifications/10B-Builtin_TaskSpecs.md`
  - `docs/specifications/13-Agent_Runtime.md` — update [AR-7] Claude notes
    to match the descriptor read-only change
- **Required additions to 10B** (new `prepare-claude-host-credentials`
  section, following the pattern used by `probe-agents`):
  - Purpose
  - What it does
  - What it does not do (in particular: does not auto-fire from
    `example-dockerized-agent-claude_code`)
  - Why it fits (explicit, one-shot, user-triggered; no hidden Keychain
    reads on ordinary paths)
  - Current output shape
  - Platform support: macOS only
  - Implementation mapping
- **Required rewrite to
  `### example-dockerized-agent-claude_code`**: replace the
  apology-shaped line ("it does not make a host macOS `claude.ai` login
  magically portable into the Linux container") with an explicit **three
  working paths** list:
  1. Run `prepare-claude-host-credentials` once per credential rotation
  2. Run `claude setup-token` on the host and export as
     `CLAUDE_CODE_OAUTH_TOKEN`
  3. Export `ANTHROPIC_API_KEY` from the Anthropic console
- **Constraint**: do not remove the existing cautionary sentence entirely;
  keep a short form explaining *why* Keychain login is not directly
  portable. The goal is "name the problem and the remedy in the same
  breath," not "hide the caveat."
- **Done when**: doc review confirms the new builtin section matches the
  shape of neighboring sections and the Claude example section names all
  three paths with correct references.

### 6. Update README

- **Outcome**: README's Docker-Backed One-Shot `provider_cli` section and
  Builtin Task Helpers section mention the new helper and the three auth
  paths.
- **Files to touch**:
  - `README.md`
- **Required updates**:
  - Builtin Task Helpers list: add one sentence for
    `prepare-claude-host-credentials` in the same register as the existing
    `probe-agents` / `prepare-agent-images` sentences
  - Docker-Backed section: one short bullet under the Claude example
    pointing users at the three auth paths and linking to the 10B Claude
    example section
- **Constraint**: do not expand the Installation or Configuration sections.
  Scope creep on the README is a common smell in this kind of change.

### 7. CHANGELOG entry

- **Outcome**: additive CHANGELOG entry describing both changes (new
  builtin, descriptor mount hardening).
- **Files to touch**:
  - `CHANGELOG.md`
- **Required shape**: one Added line for the helper, one Changed line for
  the descriptor. Mark the descriptor change as a potential
  backward-compat break for users who relied on writable mounts.

## Testing Plan

Harness and fixtures:

- Use `pytest` directly for unit-level tests on the helper (Tasks 1, 4).
- Use the existing `tests/core/test_provider_cli_container_runtime.py`
  fixtures for descriptor tests (Task 2).
- Use the existing `tests/system/test_builtin_contract.py` fixtures for
  the builtin inventory test (Task 3).
- Do **not** use `WeftTestHarness` for the helper module unit tests —
  the helper's contract is a pure function with injected seams. Add one
  integration test that actually invokes `weft run --spec
  prepare-claude-host-credentials` through the harness to prove the
  end-to-end resolution and execution path works; name the test so it is
  discoverable and keep it in `tests/tasks/`.

What not to mock:

- descriptor parsing and resolution (Task 2)
- file system operations in the real-temp-dir tests (Task 4: use
  `tmp_path`, not a mocked `Path`)
- builtin resolution order (Task 3: use real builtin inventory, not a
  stub)
- the `WeftTestHarness`-backed end-to-end run of the new task

What to mock:

- the macOS `security` subprocess in Task 4 via the injected
  `keychain_reader` — never via `patch("subprocess.run")` at module level
- platform detection via the injected `platform` parameter, not via
  `patch("sys.platform")`

Contracts each test protects:

- Task 1 test: `read_only` declaration in the descriptor JSON is actually
  honored at the Docker bind-mount layer. Regression protection for any
  future runner refactor.
- Task 2 tests: the Claude descriptor never silently drifts back to
  writable mounts. This is the writable-mount footgun guard.
- Task 4 tests: the helper's external contract (report shape, action enum,
  no secret leakage, file permissions, atomic write) is stable across
  refactors.
- Task 3 inventory test: builtin resolution order honors this helper
  without custom resolver code.

Completion and timing considerations:

- The helper is a function-type task with no queue history dependency, so
  the completion-event / outbox grace window is not relevant.
- Do not add `time.sleep`-based polling in any new test.

Commands to run during development (per-task verification in the Task
blocks above). Final-gate commands listed in §Verification and Gates.

## Verification and Gates

Per-task verification is named inside each task.

Final gates before considering the slice done:

```bash
. ./.envrc
uv sync --all-extras

# Full local suite
./.venv/bin/python -m pytest -q

# Narrower focus on the paths this slice touches
./.venv/bin/python -m pytest \
  tests/core/test_builtin_claude_host_credentials.py \
  tests/core/test_provider_cli_container_runtime.py \
  tests/core/test_builtin_example_dockerized_agent.py \
  tests/system/test_builtin_contract.py \
  extensions/weft_docker/tests/test_agent_runner.py -q

# Type and lint
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests
./.venv/bin/ruff format --check weft tests
```

Live manual smoke (macOS only, required before marking done):

1. Confirm `~/.claude/.credentials.json` does not currently exist (or move
   it aside).
2. Run `./.venv/bin/python -m weft.cli run --spec prepare-claude-host-credentials`.
   Expect a macOS Keychain prompt on first run; approve with "Always
   Allow" to avoid repeat prompts. Expect the report to show
   `action: "created"`, a non-null `expires_in_minutes`, and no token
   strings in output.
3. Verify the target file exists at `~/.claude/.credentials.json` with
   mode `0600`.
4. Run `./.venv/bin/python -m weft.cli run --spec example-dockerized-agent-claude_code --prompt "Explain this document in one sentence." --document docs/specifications/00-Overview_and_Architecture.md`.
5. Verify the run completes, returns one sentence, and the host
   `~/.claude.json` file's mtime is unchanged (read-only mount contract).
6. Re-run `prepare-claude-host-credentials`; expect `action: "preserved"`
   on the second run.

Observable success signals:

- Claude Docker example exits cleanly with model output.
- Host `~/.claude.json` is not modified during the container run.
- `weft system builtins` lists `prepare-claude-host-credentials` with
  `source: builtin`.
- `weft spec list` shows the new helper with `(builtin)` label.

## Rollout and Rollback

Rollout order:

1. Task 1 (verify `read_only` flows through) — purely additive test, safe
   to land alone.
2. Tasks 3 and 4 (ship the helper) — purely additive, no behavior change
   for existing users.
3. Task 2 (descriptor tightening) — this is the behavior change. Land
   after the helper is available so users have a resolution path ready.
4. Tasks 5, 6, 7 (docs, README, CHANGELOG) — land with or after Task 2.

Rollback rule:

- If the descriptor tightening (Task 2) causes an unforeseen regression in
  the example-dockerized-agent-claude_code end-to-end smoke, revert only
  that commit. Helper module and builtin asset stay landed since they are
  independent.
- Do not revert by loosening the descriptor to `read_only: false`. If the
  example truly needs the container to persist state, the correct fix is
  the `copied_files` / isolated runtime home pattern already present in
  the descriptor schema — that work belongs in a separate plan.

One-way doors in this slice:

- The descriptor read-only change is public-visible in that existing users
  may see changed container behavior. Call this out in CHANGELOG. It is
  not an irreversible one-way door in the spec sense (the descriptor can
  be flipped back), but the user-trust one-way is that once we have named
  "read-only is the default" in docs we should not silently flip back.
- The helper's file write is not a one-way door. It creates or replaces
  a file users can delete or regenerate.

## Out of Scope

Explicitly not changing in this slice:

- The descriptor schema itself (no new source kinds, no new capabilities).
- The `CLAUDE_CODE_OAUTH_TOKEN` env forward behavior.
- Linux-host support for the helper (the helper refuses on non-darwin and
  points at `claude setup-token` / `ANTHROPIC_API_KEY`).
- Any change to `probe-agents`, `prepare-agent-images`, or the
  `example-dockerized-agent-*` sibling builtins.
- Moving Claude to an isolated runtime home via `copied_files` (covered in
  the active multi-provider Docker expansion plan; deferred here).
- Cross-provider credential-harvesting helpers. If Gemini, Qwen, or others
  need analogous helpers later, they belong in separate per-provider plans
  so the Keychain/platform specifics stay scoped.
- Auto-refresh when the harvested credential is near expiry. The current
  design is: user re-runs the helper when it expires. A background
  refresher is not in scope.
- A GUI or TUI for the helper. Plain task output only.

## Independent Review Loop

Required: this slice touches a public builtin contract, changes a shipped
descriptor, and reads macOS Keychain. Treat as risky.

Reviewer stance:

> Read the plan at
> `docs/plans/2026-04-14-claude-host-credentials-and-descriptor-hardening-plan.md`.
> Carefully examine the plan and the referenced code
> (`weft/core/agents/provider_cli/runtime_descriptors/claude_code.json`,
> `weft/core/agents/provider_cli/container_runtime.py`,
> `weft/builtins/agent_probe.py`,
> `extensions/weft_docker/weft_docker/agent_runner.py`,
> `weft/commands/specs.py`). Do no implementation. Answer:
>
> 1. Could you implement this confidently and correctly if asked?
> 2. Does the plan actually preserve the Weft product boundary ("no
>    hidden setup on ordinary execution paths")?
> 3. Is the descriptor read-only change safe? If you believe a current
>    test or shipped example depends on writable mounts, name it.
> 4. Is there any path by which the Keychain read could fire from
>    anywhere other than `prepare-claude-host-credentials`? If yes, the
>    plan must be tightened.
> 5. Does the helper's file-write path handle concurrent invocations
>    safely (two terminals both run the helper at once)? If not, is the
>    failure mode acceptable (first-wins, no corruption)?

Reviewer should be a different agent family from the author where
available. Feedback returns to the author via inline comments on this
plan file; the author must explicitly accept, amend, or justify
each point.

## Fresh-Eyes Re-Read Checklist

Before treating this plan as implementation-ready, re-read as a new
engineer and confirm:

- every filename in §Tasks is reachable via Glob from the repo root
- every referenced test file exists or is marked `(new)`
- every shell command is runnable from a fresh `. ./.envrc` +
  `uv sync --all-extras` state
- the helper's failure modes cover: missing Keychain item, wrong shape,
  wrong platform, write failure, existing-fresher-file, existing-stale-file
- the descriptor change has a matching test that would fail if the JSON
  flipped back to writable
- no task silently pulls in a refactor of unrelated code (YAGNI)
- §Out of Scope names the biggest things an implementer might be tempted
  to do opportunistically

## Why This Is The Right Scope

The steelman alternative is to add a `"host_command"` source kind to the
descriptor and let the shipped `claude_code.json` declare the Keychain
read inline. That is seductive because it is fewer moving parts for the
user — one `weft run --spec example-dockerized-agent-claude_code` would
"just work."

That is the wrong first move:

- it bakes host-command execution into descriptor spawn-time resolution,
  which crosses the Product Boundary rule against hidden setup on
  ordinary execution paths
- it means every Docker-backed Claude run opens your Keychain, which is
  both surprising and a measurable identity-boundary widening
- it pressures the descriptor schema to grow a general scripting surface
  that every future provider could demand

The weaker alternative is to do nothing and keep the apology in the doc.

That is also wrong:

- the shipped example currently does not work for the common macOS
  audience
- users will either stop using the example or bypass it by hand, and
  either way the builtin fails its stated purpose ("teach the Weft shape
  through a concrete example")

This plan is the narrow middle:

- one explicit optional builtin helper that users run consciously
- one descriptor tightening that hardens an accidental host-corruption
  footgun
- doc updates that state the three working paths without hiding the
  underlying constraint

## Related Specs to Backlink

After landing:

- add a Plans entry in `docs/specifications/10B-Builtin_TaskSpecs.md`
  `## Related Plans` pointing at this plan
- add a Plans entry in `docs/specifications/13-Agent_Runtime.md`
  `## Related Plans` pointing at this plan
- confirm `_Implementation mapping_` notes in 10B's new
  `prepare-claude-host-credentials` section and in the updated Claude
  example section match the shipped code paths
