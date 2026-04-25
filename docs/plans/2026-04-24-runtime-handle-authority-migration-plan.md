# Runtime Handle Authority Migration Plan

Status: proposed
Source specs: see Source Documents below
Superseded by: none

## Goal

Move Weft from ambiguous PID-based runtime interpretation to a cleaner contract:
`runtime_handle` is the authoritative identity for liveness, control, and public
status. PIDs become scoped observations owned by the runner, not generic durable
identity. This plan intentionally does **not** preserve runtime backward
compatibility for existing broker records; operators should reset runtime
queues or run a one-time migration helper if they need to preserve historical
inspection during the cutover.

## Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.4], [CC-3.1], [CC-3.2], [CC-3.4]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md)
  [TS-1.3]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-0], [MA-1.4], [MA-3], [MA-4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-5], [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [OBS.3], [OBS.4], [IMPL.1]
- [`docs/specifications/08-Testing_Strategy.md`](../specifications/08-Testing_Strategy.md)
- [`docs/specifications/13-Agent_Runtime.md`](../specifications/13-Agent_Runtime.md)
  [AR-5]

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Related plans:

- [`docs/plans/2026-04-24-manager-status-container-pid-liveness-plan.md`](./2026-04-24-manager-status-container-pid-liveness-plan.md)
  is the narrow incident fix. This plan supersets the underlying model change,
  but does not supersede the narrow plan unless this migration is implemented
  first.
- [`docs/plans/2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md`](./2026-04-13-runner-monitor-result-waiter-and-liveness-fixes-plan.md)
  is proposed prior art for runner liveness problems. Do not treat it as
  landed behavior.
- [`docs/lessons.md`](../lessons.md) entries for zombie PID liveness and
  manager bootstrap unification.

## Context and Key Files

Files to modify:

- `weft/ext.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `weft/core/manager_runtime.py`
- `weft/_constants.py`
- `weft/core/runners/host.py`
- `weft/core/runners/subprocess_runner.py`
- `weft/core/tasks/consumer.py`
- `weft/core/tasks/interactive.py`
- `weft/core/endpoints.py`
- `weft/commands/system.py`
- `weft/commands/tasks.py`
- `weft/commands/types.py`
- `weft/cli/app.py`
- `extensions/weft_docker/weft_docker/plugin.py`
- `extensions/weft_docker/weft_docker/agent_runner.py`
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
- tests under `tests/commands/`, `tests/core/`, `tests/tasks/`,
  `tests/cli/`, `extensions/weft_docker/tests/`, and
  `extensions/weft_macos_sandbox/tests/`
- specs listed above
- `docs/lessons.md`

Optional helper script:

- `scripts/migrate_runtime_handles.py` or
  `tools/migrate_runtime_handles.py`, only if the implementation needs a
  one-time operator path to rewrite existing spec/example fixtures or a live
  broker. Do not add this helper unless the cutover evidence shows it is useful.

Read first:

- `weft/ext.py`
  - `RunnerHandle` currently stores `runner_name`, `runtime_id`, `host_pids`,
    and metadata.
  - `RunnerRuntimeDescription` is the runner-owned liveness/status description.
- `weft/core/tasks/base.py`
  - `_report_state_change()` writes `task_pid`, `managed_pids`, `runner`, and
    `runtime_handle` into `weft.log.tasks`.
  - `_build_tid_mapping_payload()` writes the same ambiguity into
    `weft.state.tid_mappings`.
  - `_stop_registered_runtime_handle()` already delegates stop/kill to runner
    plugins when a handle exists.
- `weft/commands/system.py`
  - `_collect_task_snapshots()` merges task log and TID mappings.
  - `_effective_public_status()` still uses bare `pid` / `task_pid` as a
    liveness authority.
- `weft/commands/tasks.py`
  - STOP/KILL fallbacks still prefer PID signaling before runner plugin control.
- `weft/core/endpoints.py`
  - endpoint ownership liveness currently checks mapping PID directly.
- `weft/core/manager.py` and `weft/core/manager_runtime.py`
  - manager registry records use `pid` as if it is host-local.
- `extensions/weft_docker/weft_docker/plugin.py`
  - Docker already creates a `RunnerHandle` where the container identity is in
    `runtime_id`/metadata, but it does not expose a typed runtime identity shape.
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
  - macOS sandbox currently behaves like a scoped host-process runner and uses
    `RunnerHandle.host_pids` for stop/kill/describe.

Comprehension checks before editing:

- Which code paths still treat `pid` or `task_pid` as durable liveness proof?
- Which parts of the system need host process liveness for cleanup but should
  not expose it as the public runtime identity?
- Which runner plugin owns the answer to "is this runtime alive?"
- Which queues contain runtime-only state that can be reset instead of migrated?

## Target Contract

Replace the current `RunnerHandle` shape with a typed runtime identity:

```json
{
  "runner": "docker",
  "kind": "container",
  "id": "596abb83061c",
  "control": {
    "authority": "runner"
  },
  "observations": {
    "container_name": "weft-...",
    "container_pid": 1,
    "host_pids": []
  },
  "metadata": {
    "image": "python:3.13-alpine"
  }
}
```

Host example:

```json
{
  "runner": "host",
  "kind": "process",
  "id": "12345",
  "control": {
    "authority": "host-pid"
  },
  "observations": {
    "host_pids": [12345]
  },
  "metadata": {}
}
```

Supervised/containerized manager example:

```json
{
  "runner": "manager-supervisor",
  "kind": "supervised-process",
  "id": "container:mm-api_weft_manager-1",
  "control": {
    "authority": "external-supervisor"
  },
  "observations": {
    "container_pid": 1,
    "container_name": "mm-api_weft_manager-1"
  },
  "metadata": {}
}
```

Exact Python dataclass contract:

```python
@dataclass(frozen=True, slots=True)
class RunnerHandle:
    runner: str
    kind: Literal["process", "container", "sandboxed-process", "supervised-process"]
    id: str
    control: Mapping[str, Any] = field(default_factory=dict)
    observations: Mapping[str, Any] = field(default_factory=dict)
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

Exact JSON keys emitted by `RunnerHandle.to_dict()`:

- `runner`: plugin or authority name, for example `host`, `docker`,
  `macos-sandbox`, or `manager-supervisor`
- `kind`: runtime kind, one of `process`, `container`,
  `sandboxed-process`, or `supervised-process`
- `id`: runtime ID meaningful to `runner` and `control.authority`
- `control`: object with required string key `authority`
- `observations`: object for scoped observed facts such as `host_pids`,
  `container_pid`, `container_name`, or `sandbox_profile`
- `metadata`: runner-owned metadata for display and diagnostics

Exact compatibility decision:

- Remove Python attributes `runner_name`, `runtime_id`, `host_pids`, and
  `primary_pid`.
- Remove JSON keys `runner_name`, `runtime_id`, and top-level `host_pids`.
- `RunnerHandle.from_dict()` must reject old-shape payloads. It must not
  translate them.

Manager runtime authority contract:

- Detached host-launched managers publish a `RunnerHandle` with
  `runner="host"`, `kind="process"`, `id=str(host_pid)`,
  `control.authority="host-pid"`, and
  `observations.host_pids=[host_pid]`.
- Foreground `weft manager serve` uses the same host-process handle unless an
  explicit supervisor handle is supplied.
- Supervised/containerized managers do not use generic Docker inspection from
  Weft core. The supervisor must provide a handle at process start through a
  single env/config contract:
  - environment variable: `WEFT_MANAGER_RUNTIME_HANDLE_JSON`
  - config key: `WEFT_MANAGER_RUNTIME_HANDLE_JSON`
  - value: JSON object that validates as the exact `RunnerHandle` shape
  using:
  - `runner="manager-supervisor"`
  - `kind="supervised-process"`
  - `control.authority="external-supervisor"`
  - `id` set to the supervisor-owned runtime identifier
  - observations carrying scoped details such as `container_pid=1`
- If no explicit supervisor handle is supplied, the manager must publish a
  host-process handle for its current process only. This is valid only when that
  PID is meaningful to the status reader's host namespace.
- `manager-supervisor` is not a `RunnerPlugin`. Generic status/control code
  must not call `require_runner_plugin("manager-supervisor")`. Manager
  liveness for this authority comes from the manager registry heartbeat and the
  supervisor-provided identity, not runner plugin probing.

## Invariants and Constraints

- No runtime backward compatibility in code. Readers should reject or ignore
  old handle shapes instead of silently accepting both old and new formats.
- No queue-name changes.
- No TID format changes.
- No TaskSpec `spec`/`io` mutation after resolution.
- State transitions remain forward-only.
- `weft.state.*` queues remain runtime-only. This migration may require
  clearing them on upgrade rather than supporting mixed old/new records.
- Keep the durable execution spine intact:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Do not add Docker-specific logic to generic status/control code. Generic code
  should call the runner plugin or manager registry authority.
- Do not infer liveness from unscoped PIDs.
- Do not leave permanent "legacy" readers in core code. Temporary migration
  scripts are acceptable; compatibility branches in runtime code are not.
- Do not hide manager rows from the task log. Fix their runtime identity.
- Do not make status depend on starting or probing runtimes that are not already
  represented by a runtime handle.
- Generic Weft core must never inspect Docker, podman, Kubernetes, or other
  supervisor APIs to interpret manager liveness. The producer of a supervised
  manager handle owns that authority.

## Cutover Policy

This is a breaking runtime-state migration.

Required operator path:

- Stop all managers and tasks for the affected context.
- Clear runtime queues that contain old runtime identity records:
  `weft.state.managers`, `weft.state.tid_mappings`, `weft.state.streaming`, and
  any endpoint/runtime-only queues named in the specs.
- Either clear `weft.log.tasks` or accept that old log records are historical
  audit rows with basic `tid`, `name`, `event`, timestamp, and lifecycle
  status only. Old `runtime_handle` payloads inside `weft.log.tasks` must be
  ignored, not parsed.
- Restart the manager with the new code.

Optional helper:

- A one-time helper may rewrite broker records to the new shape if preserving
  old `weft.log.tasks` inspection is worth it.
- The helper should live outside runtime code and should be explicit:
  `python scripts/migrate_runtime_handles.py --context PATH --dry-run`.
- The helper must not become a permanent fallback reader.
- The helper must not be required for correctness when operators accept losing
  runtime details on old task-log rows. Runtime code should simply ignore old
  handle shapes.

## Implementation Slices

Each slice should land independently with its own focused tests and review.
Do not combine structural schema work with status/control behavior in one
commit unless a later reviewer explicitly approves it.

1. Slice A: specify the new runtime handle contract.
   - Outcome: specs define runtime identity before code changes.
   - Files to touch:
     - `docs/specifications/01-Core_Components.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/13-Agent_Runtime.md`
   - Add plan backlinks.
   - Define exactly the Python dataclass and JSON keys listed in Target
     Contract.
   - Explicitly remove unscoped `pid`, `task_pid`, and top-level `host_pids`
     from durable runtime payloads.
   - State that runner plugins own liveness through `describe(handle)`.
   - State the manager supervisor producer contract.
   - Stop and re-plan if the spec wants mixed old/new runtime records.
   - Verification:
     - `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`

2. Slice B: reshape `RunnerHandle` with contract tests.
   - Outcome: the core handle type emits and accepts only the new shape.
   - Files to touch:
     - `weft/ext.py`
     - focused tests for `RunnerHandle`
   - Remove `runner_name`, `runtime_id`, `host_pids`, and `primary_pid`.
   - Add strict validation for `runner`, `kind`, `id`, `control`,
     `observations`, and `metadata`.
   - `from_dict()` must reject old-shape payloads containing `runner_name`,
     `runtime_id`, or top-level `host_pids`.
   - Verification:
     - run the new `RunnerHandle` tests first
     - `./.venv/bin/mypy weft/ext.py`

3. Slice C: update first-party runner producers and plugins.
   - Outcome: first-party runners produce only typed runtime handles.
   - Files to touch:
     - `weft/core/runners/host.py`
     - `extensions/weft_docker/weft_docker/plugin.py`
     - `extensions/weft_docker/weft_docker/agent_runner.py`
     - `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`
     - relevant runner tests
   - Host runner must use `runner="host"`, `kind="process"`,
     `control.authority="host-pid"`, and `observations.host_pids`.
   - Docker runner must use `runner="docker"`, `kind="container"`,
     `control.authority="runner"`, and container ID/name observations.
   - macOS sandbox must use `runner="macos-sandbox"`,
     `kind="sandboxed-process"`, `control.authority="host-pid"`, and scoped
     host PID observations.
   - Update host plugin `stop`, `kill`, and `describe` to read scoped host PIDs
     from the handle.
   - Update Docker plugin `describe`, `stop`, and `kill` to use container ID or
     runner-owned runtime ID, not generic PID fields.
   - Update macOS sandbox `describe`, `stop`, and `kill` the same way as the
     host-process class, while preserving sandbox metadata.
   - Verification:
     - `./.venv/bin/python -m pytest tests/tasks/test_command_runner_parity.py -q`
     - `./.venv/bin/python -m pytest extensions/weft_docker/tests/test_docker_plugin.py -q`
     - macOS sandbox tests if present on the current platform

4. Slice D: stop emitting unscoped PID fields from task mapping and log payloads.
   - Outcome: new task lifecycle records carry runtime identity and scoped
     observations only.
   - Files to touch:
     - `weft/core/tasks/base.py`
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/interactive.py`
     - `weft/core/runners/subprocess_runner.py`
     - `tests/tasks/test_task_observability.py`
   - Remove public `pid`, `task_pid`, and unscoped `managed_pids` from:
     - `_report_state_change()`
     - `_build_tid_mapping_payload()`
   - If cleanup still needs host process IDs internally, keep them as private
     task attributes and scoped `runtime_handle.observations.host_pids`.
   - Preserve process title behavior. Process titles are human observability,
     not runtime identity.
   - Stop and re-plan if this starts changing TaskSpec state instead of emitted
     runtime payloads.
   - Verification:
     - `./.venv/bin/python -m pytest tests/tasks/test_task_observability.py -q`
     - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q`

5. Slice E: move manager registry to typed runtime identity.
   - Outcome: manager registry records no longer imply that `pid` is host-local.
   - Files to touch:
     - `weft/core/manager.py`
     - `weft/core/manager_runtime.py`
     - `weft/_constants.py`
     - `weft/commands/manager.py`
     - `weft/commands/system.py`
     - `tests/core/test_manager.py`
     - `tests/cli/test_cli_manager.py`
     - `tests/cli/test_cli_serve.py`
   - Replace manager registry `pid` with a typed `runtime_handle` object using
     the exact Target Contract shape.
   - For ordinary host-launched managers, use `kind="process"` and scoped
     `observations.host_pids`.
   - For foreground managers, use the same host-process handle unless an
     explicit supervisor handle is supplied.
   - For supervised/container managers, read an explicit supervisor handle from
     `WEFT_MANAGER_RUNTIME_HANDLE_JSON` at process start. Do not inspect Docker
     from generic manager code.
   - Update readiness checks:
     - host-process handles require matching scoped host PID and live PID
     - external-supervisor handles require matching registry TID plus the
       supervisor-provided handle; generic Weft does not prove supervisor
       liveness beyond the registry heartbeat
     - if startup needs stronger supervised-manager proof, stop and write a
       follow-up supervisor contract plan instead of guessing
   - Stop and re-plan if implementation needs to inspect Docker from generic
     manager code.
   - Verification:
     - `./.venv/bin/python -m pytest tests/core/test_manager.py -q`
     - `./.venv/bin/python -m pytest tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py -q`

6. Slice F: rewrite status reconstruction around runtime handles.
   - Outcome: public task status never upgrades terminal state to `running`
     from unscoped PID data.
   - Files to touch:
     - `weft/commands/system.py`
     - `weft/commands/types.py`
     - `tests/commands/test_status.py`
     - `tests/cli/test_status.py`
   - Make `_runtime_handle_from_mapping()` accept only the new handle shape.
   - Make `_effective_public_status()` use:
     - terminal log status as the base truth
     - `runner.describe(runtime_handle)` for live runtime override
     - manager registry authority for manager tasks
     - external-supervisor manager handles without loading a runner plugin
   - Remove `_task_process_alive()` and `_task_process_id()` PID fallbacks, or
     restrict them to scoped host process handles only.
   - Old-shape `runtime_handle` payloads in `weft.log.tasks` and mappings must
     be ignored for runtime description. Basic log replay still reports the
     task's durable status/name/event when the taskspec payload is otherwise
     usable.
   - `describe(handle)` failure is non-fatal: preserve queue-derived lifecycle
     status, attach `runtime.state="unknown"` and diagnostic metadata, and do
     not upgrade terminal state to `running`.
   - Add regression tests for:
     - host process task with live scoped host PID remains public `running`
       during terminal wrapper teardown
     - Docker task liveness comes from Docker plugin description
     - stale manager with container-local PID `1` is not public `running`
     - old handle shape is rejected or ignored, not accepted silently
     - `describe(handle)` failure does not hide queue-derived lifecycle state
   - Verification:
     - `./.venv/bin/python -m pytest tests/commands/test_status.py -q`
     - `./.venv/bin/python -m pytest tests/cli/test_status.py -q`

7. Slice G: rewrite task control and endpoint fallbacks around runtime handles.
   - Outcome: STOP/KILL uses runner-owned control, not generic PID fallback,
     except for scoped host process handles.
   - Files to touch:
     - `weft/commands/tasks.py`
     - `weft/core/endpoints.py`
     - `weft/cli/app.py`
     - related command and endpoint tests
   - Replace `_task_pid_from_mapping()` with a helper that returns scoped host
     PIDs only for host process handles.
   - STOP/KILL order should be:
     - normal control queue request
     - runner plugin `stop(handle)` / `kill(handle)` when a handle exists
     - scoped host PID fallback only when the handle authority is `host-pid`
   - Endpoint liveness should use task status plus runner description, not
     mapping PID.
   - Stop and re-plan if a caller needs a generic `pid` fallback to pass tests;
     that indicates a test or contract still uses the old model.
   - Verification:
     - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q`
     - endpoint-related tests identified by `rg "endpoints|Endpoint" tests -n`

8. Slice H: update JSON/text public surfaces.
   - Outcome: CLI and client output reflects the new model clearly.
   - Files to touch:
     - `weft/commands/types.py`
     - `weft/commands/system.py`
     - `weft/commands/manager.py`
     - `weft/client/_types.py`
     - `weft/client/_namespaces.py`
     - CLI tests
   - Public task snapshots should expose `runtime_handle` and `runtime`.
   - Manager snapshots should expose runtime identity and may expose scoped
     observations for display.
   - Remove or rename public `pid` fields unless they are explicitly scoped as
     `host_pid` or `container_pid`.
   - Text output may continue showing a PID-like field only if the label is
     scoped, for example `HOST_PID` or `CONTAINER_PID`.
   - Verification:
     - `./.venv/bin/python -m pytest tests/core/test_client.py tests/cli/test_cli_list_task.py -q`

9. Slice I: decide whether to add the one-time helper.
   - Outcome: operator cutover is explicit.
   - Files to touch if needed:
     - `scripts/migrate_runtime_handles.py`
     - tests for the helper
     - docs/specifications or README migration notes
   - Helper scope:
     - dry-run by default
     - report old records by queue
     - optionally rewrite obvious host records to `kind="process"`
     - optionally clear runtime-only queues
   - Do not support ambiguous container-local PID migration automatically. For
     those, report the record and require operator reset or explicit mapping.
   - If no helper is added, document the exact runtime-queue reset procedure in
     the specs or README touched by this migration.

10. Slice J: update tests and remove old-shape fixtures.
   - Outcome: the suite enforces the new contract, not compatibility.
   - Files to touch:
     - `tests/commands/test_status.py`
     - `tests/commands/test_task_commands.py`
     - `tests/tasks/test_task_observability.py`
     - `tests/cli/test_cli_list_task.py`
     - `tests/cli/test_status.py`
     - `tests/core/test_client.py`
     - `extensions/weft_docker/tests/test_docker_plugin.py`
     - macOS sandbox tests if present
   - Replace fixture payloads containing `pid`, `task_pid`, or top-level
     `host_pids`.
   - Add negative tests that old shapes are not parsed.
   - Keep broker-backed tests real. Do not mock queue replay.

11. Final verification.
    - Focused tests:
      - `./.venv/bin/python -m pytest tests/commands/test_status.py -q`
      - `./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q`
      - `./.venv/bin/python -m pytest tests/tasks/test_task_observability.py -q`
      - `./.venv/bin/python -m pytest tests/cli/test_status.py tests/cli/test_cli_manager.py tests/cli/test_cli_serve.py -q`
      - `./.venv/bin/python -m pytest extensions/weft_docker/tests/test_docker_plugin.py -q`
      - macOS sandbox tests if present and supported on the current platform
    - Broader checks:
      - `./.venv/bin/python -m pytest`
      - `./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
      - `./.venv/bin/ruff check weft extensions tests`
    - Runtime exercise:
      - start a host manager and run a host task
      - start a supervised/containerized manager if available
      - confirm `weft status --json` reports one manager and uses scoped
        runtime identity
      - confirm STOP/KILL still work for host and Docker tasks

## Rollback

Because this plan intentionally rejects runtime backward compatibility, rollback
means:

- stop managers/tasks
- revert code and docs
- clear runtime-only queues created by the new code
- restart the old code with a clean runtime state

Rollback should not require migrating TaskSpec files because this plan changes
runtime observation payloads, not user-authored TaskSpec schema.

## Review Gate

This plan requires independent review before implementation.

Suggested prompt:

> Read `docs/plans/2026-04-24-runtime-handle-authority-migration-plan.md`,
> `weft/ext.py`, `weft/core/tasks/base.py`, `weft/commands/system.py`,
> `weft/commands/tasks.py`, `weft/core/manager_runtime.py`, and
> `extensions/weft_docker/weft_docker/plugin.py`. Look for places where the
> plan leaves old unscoped PID authority alive, under-specifies manager
> liveness, or breaks host STOP/KILL semantics. Could you implement this
> confidently without adding runtime backward compatibility?

Reviewer must explicitly answer whether the no-backward-compat policy is
confined to runtime state and does not accidentally break user-authored
TaskSpecs.

## Out Of Scope

- Supporting old runtime handle shapes in core code.
- Docker namespace inspection in generic Weft code.
- Remote runner design beyond the generic typed handle contract.
- Changing TaskSpec `spec.runner`.
- Hiding managers from task history.
- Queue-name redesign.
- Public API stability for runtime-state JSON fields during this migration.
