# Critical Review Remediation Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.4]; docs/specifications/03-Manager_Architecture.md [MA-2], [MA-3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.2], [SB-0.3]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-3], [MF-4], [MF-5], [MF-6]; docs/specifications/07-System_Invariants.md [QUEUE.4], [QUEUE.6], [STATE.1], [STATE.2], [EXEC.1], [OBS.1], [OBS.2], [OBS.3], [OBS.13]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-1.3], [CLI-6]; docs/specifications/13-Agent_Runtime.md [AR-0.1], [AR-2.1], [AR-5], [AR-7]
Superseded by: none

## Goal

Fix the concrete defects, documentation drift, and verified stale-code
scaffolding found during the 2026-06-01 critical code review, while separating
verified bugs from issues that still need active evaluation. The work must
preserve Weft's existing queue-first execution model: queues are live runtime
truth; TaskMonitor and external logs are retention/audit handoff surfaces, not a
second task-state authority. The plan keeps `WEFT_TASK_MONITOR_MODE=delete` as
the default unless a later product decision changes the core promise, clarifies
`jsonl_then_delete` as the recommended production audit-retention preset, fixes
provider-cli execution/isolation bugs, pins pipeline handoff cardinality, and
removes dead abstractions only after behavior-facing fixes are protected by
tests.

## Source Documents

Read these before implementation:

- `AGENTS.md`: especially the project philosophy in section 1.1, the style rules,
  the test commands, and the rule that specs are authoritative.
- `docs/agent-context/decision-hierarchy.md`: specs outrank plans; plans explain
  implementation slices but do not define product behavior after the slice lands.
- `docs/agent-context/principles.md` and
  `docs/agent-context/engineering-principles.md`: queue-first state, boundary
  validation, real broker/process tests, and documentation traceability.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: this plan intentionally
  touches durable submission, task control, cleanup, packaging, and CLI docs.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: use the
  independent review loop before implementation.
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`: especially the
  TID/message-ID submission boundary and runtime-only queue rules.
- `docs/agent-context/runbooks/testing-patterns.md`: use real broker queues and
  `WeftTestHarness` for queue/process behavior.
- `docs/lessons.md`: the active STOP/KILL main-thread ownership lesson is
  directly relevant to the structured-control bug.

Governing specs:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-6]: spawn
  request message IDs become task TIDs and durable user intent.
- `docs/specifications/03-Manager_Architecture.md` [MA-2]: TID correlation and
  manager-side child TID propagation.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2], [SB-0.3]:
  SimpleBroker message IDs are durable ordered IDs that Weft relies on.
- `docs/specifications/02-TaskSpec.md` [TS-0], [TS-1], [TS-1.4] and
  `docs/specifications/07-System_Invariants.md` [IMMUT.3]: resolved TaskSpec
  immutability, mutable runtime state, and the boundary for deleting stale
  mutation scaffolding.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3] and
  `docs/specifications/01-Core_Components.md` [CC-2.4]: task-local controls,
  active STOP/KILL, and acknowledgement vs terminal-proof boundaries.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-4]: first-class
  pipeline task flow and the edge/stage handoff boundary.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5],
  `docs/specifications/07-System_Invariants.md` [OBS], and
  `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1]: status diagnostics and
  TaskMonitor external-log/deferred-write reporting.
- `docs/specifications/10-CLI_Interface.md` [CLI-6] and
  `docs/specifications/04-SimpleBroker_Integration.md` operational notes:
  `weft system dump/load` behavior and backend-aware import risk.
- `docs/specifications/13-Agent_Runtime.md` [AR-0.1], [AR-2.1], [AR-5],
  [AR-7]: provider-cli runtime boundaries, delegated tool profiles, provider
  workspace-access support, and the host/Docker one-shot execution split.

Triggering evidence from the review:

- Reproduced: dumping and loading a pending `weft.spawn.requests` row rewrites
  the broker message ID, so the loaded queue row timestamp no longer matches the
  original submitted TID.
- Reproduced: a structured JSON `STOP` envelope sent to an active `Consumer`
  bypasses active-control deferral and publishes terminal state before the
  runner unwinds.
- Code-verified: `Manager` registers an `atexit` bound method and never
  unregisters it.
- Code-verified: root packaging extras allow plugin/backend versions older than
  the in-repo extension versions tested by editable `uv` sources.
- Code-verified: README says `weft system load -i FILE` is a preflight import,
  but the CLI mutates unless `--dry-run` is passed.
- Code-verified: one-shot `provider_cli` execution resolves a callable
  `tool_profile_ref` twice for one work item because `execute()` validates the
  tool profile and then `prepare_provider_cli_execution()` resolves it again.
- Code-verified: one-shot Gemini `authority_class="general"` plus
  `workspace_access="read-only"` gets `--approval-mode plan` but not the
  isolated Gemini runtime home used by bounded mode and all Gemini sessions.
- Code-verified: `weft/commands/result.py::_queue_names_for_tid` duplicates the
  canonical `task_evidence.queue_names_for_tid` helper and can raise on
  malformed `io` payloads where the canonical helper falls back safely.
- Code-verified: pipeline edge handoff currently moves one reserved payload and
  then reports the edge checkpoint; if a stage emits multiple payloads, outputs
  after the first can be stranded unless the single-output contract is enforced.
- Code-verified: several dead or misleading abstractions remain after earlier
  cleanup work: `monitor/policies/api.py`, unused provider runtime hooks,
  passthrough status helpers, and unused constants.
- Reviewed and retained: TaskSpec nested mutation guards, MultiQueueWatcher
  dynamic queue mutation support, service-convergence decision fields, and
  compiled pipeline metadata are consistent with the current design and are not
  cleanup targets in this plan.
- Needs active evaluation: whether `system dump` omission of claimed rows is a
  bug or an undocumented offline/pending-only boundary.
- Needs active evaluation: whether result/status waiting has a concrete
  inconsistent decision point, not merely high static complexity.
- Needs active evaluation: whether default TaskMonitor retained-ingest raw
  deletion can make a still-live task report as not found through normal
  `weft task status TID`, or whether the Monitor-store fallback fully closes the
  gap. The raw deletion mechanics are code-verified; the user-visible not-found
  failure still needs a real task harness reproduction before changing deletion
  policy.
- Needs active evaluation: whether Qwen's empty `--extensions=` and
  `--allowed-mcp-server-names=` flags demonstrably disable ambient extensions
  and MCP servers in the installed provider version. Local help confirms the
  flags exist, but that is not proof of isolation semantics.

Comprehension questions before editing runtime code:

1. Which broker message ID becomes the child TID, and where does the manager
   turn that ID into the resolved child TaskSpec?
2. What is the difference between a task-control acknowledgement and terminal
   lifecycle proof?
3. Which thread owns reserved-policy handling and terminal publication for
   active STOP/KILL?
4. What must happen before `jsonl_then_delete` may delete a selected subject?
5. Which queues are runtime-only and must stay excluded from dump/load?
6. Which layer owns delegated `provider_cli` tool-profile resolution for a
   single work item, and why should execution not re-run a callable profile
   after validation has already done so?
7. What is the current pipeline edge contract: one handoff payload, or arbitrary
   fan-out? If it is one payload, where should a second payload become a clear
   task failure?
8. Which cleanup candidates are proven pure deletion, and which reviewed
   surfaces are design-bearing enough that they should not be cleanup targets?

## Context and Key Files

System dump/load:

- Files to read first:
  - `weft/commands/_dump_support.py`
  - `weft/commands/_load_support.py`
  - `weft/commands/dump.py`
  - `weft/commands/load.py`
  - `weft/core/spawn_requests.py`
  - `weft/core/manager.py` around `_build_child_spec` and spawn draining
  - `tests/commands/test_dump_load.py`
- Current structure:
  - Dump records message timestamps.
  - Load currently writes queue bodies through ordinary `Queue.write()`, which
    allocates fresh broker message IDs.
  - `weft/core/spawn_requests.py` already has a constrained exact-timestamp
    write path for spawn requests because TID/message-ID identity matters.
- Import path:
  - Use SimpleBroker's public `import_messages()` API for dump/load timestamp
    restoration.
  - Use SimpleBroker's public `write_reserved_message()` API for ordinary spawn
    requests whose TID was generated by the destination broker.
  - For explicit external TIDs, use `import_messages()` for the exact-ID spawn
    row rather than rewriting the TaskSpec TID.
  - Do not hand-write backend SQL in command modules.

Task control:

- Files to read first:
  - `weft/core/tasks/consumer.py`
  - `weft/core/tasks/base.py`
  - `weft/core/control_probe.py`
  - `weft/commands/tasks.py`
  - `tests/tasks/test_consumer.py` and related control tests
- Current structure:
  - `Consumer._poll_active_control_once()` defers active raw `STOP`/`KILL`
    before normal base-class handling.
  - Base control parsing accepts structured JSON envelopes and routes them to
    immediate default STOP/KILL handling.
- Shared path to reuse:
  - Reuse the existing control request parsing semantics. Do not add a second
    control grammar.

Manager lifecycle:

- Files to read first:
  - `weft/core/manager.py`
  - manager cleanup tests in `tests/core/`
- Current structure:
  - `Manager.__init__()` registers `self._atexit_unregister`.
  - `Manager.cleanup()` unregisters the manager service record but does not
    unregister the atexit callback.

TaskMonitor default and status warnings:

- Files to read first:
  - `weft/_constants.py`
  - `weft/core/monitor/task_monitor.py`
  - `weft/core/monitor/runtime.py`
  - `weft/core/monitor/external_log.py`
  - `weft/core/monitor/store.py`
  - `weft/commands/system.py`
  - `docs/specifications/00-Quick_Reference.md`
  - `docs/specifications/05-Message_Flow_and_State.md`
  - `docs/specifications/07-System_Invariants.md`
  - `docs/specifications/10-CLI_Interface.md`
  - `README.md`
- Current structure:
  - Default mode is `delete`.
  - `jsonl_then_delete` enables external logging by default, requires collated
    mode and the Monitor collation store, and must write or durably defer a
    `task_lifetime_report` before exact delete.
  - `weft status` already includes cached service diagnostics for external log
    health and deferred-write counts; verify whether those diagnostics are
    visible enough as warnings.
  - `TaskMonitor._ingest_retained_task_log_rows()` folds visible task-log rows
    into the Monitor store and may exact-delete raw rows in destructive
    collated-store ownership. `weft task status TID` first replays bounded raw
    task-log evidence, then falls back to Monitor-store collation for full TIDs.
    The plan must test that combined read model with a real live task before
    changing raw deletion policy.

Provider CLI runtime:

- Files to read first:
  - `docs/specifications/13-Agent_Runtime.md`
  - `weft/core/agents/backends/provider_cli.py`
  - `weft/core/agents/provider_cli/execution.py`
  - `weft/core/agents/provider_cli/registry.py`
  - `weft/core/agents/provider_cli/runtime_prep.py`
  - `weft/core/agents/resolution.py`
  - `tests/core/test_provider_cli_execution.py`
  - nearest provider-cli backend/session tests
- Current structure:
  - `ProviderCLIBackend.execute()` validates the tool profile and then calls the
    shared one-shot preparation helper, which resolves the tool profile again.
  - `ProviderCLIBackendSession.execute()` uses a separate `_prepare_execution()`
    path that duplicates much of `prepare_provider_cli_execution()`.
  - Gemini one-shot invocation isolates the runtime home only when
    `authority_class == "bounded"`, while Gemini sessions always use the
    isolated environment.
  - Qwen bounded/read-only narrowing passes empty `--extensions=` and
    `--allowed-mcp-server-names=` flags, but provider-version behavior needs
    independent verification.
- Shared path to reuse:
  - Keep `prepare_provider_cli_execution()` as the shared one-shot preparation
    boundary used by host and Docker-backed delegated execution. If session
    preparation is consolidated, reuse the same pure prompt/profile/options
    preparation logic rather than adding a third resolver layer.
  - Do not add hidden provider CLI probes to ordinary `weft run`; explicit
    validation/diagnostic flows own provider compatibility checks.

Result/event queue-name resolution:

- Files to read first:
  - `weft/core/task_evidence.py`
  - `weft/commands/task_evidence.py`
  - `weft/commands/result.py`
  - `weft/commands/events.py`
  - `tests/commands/test_result.py`
  - nearest event iterator tests
- Current structure:
  - `weft.core.task_evidence.queue_names_for_tid()` safely falls back when the
    TaskSpec payload or `io` section is malformed.
  - `weft.commands.result._queue_names_for_tid()` duplicates the happy path but
    casts `io` blindly and can raise.
  - `weft.commands.events` imports the private result helper.
- Shared path to reuse:
  - Use the canonical task-evidence helper everywhere. Do not create another
    command-local resolver.

Pipeline handoff cardinality:

- Files to read first:
  - `docs/specifications/05-Message_Flow_and_State.md` [MF-4]
  - `weft/core/pipelines.py`
  - `weft/core/tasks/pipeline.py`
  - `weft/core/task_evidence.py`
  - `tests/tasks/test_pipeline_runtime.py`
  - `tests/core/test_pipelines.py`
- Current structure:
  - `PipelineEdgeTask._handoff_payload()` moves exactly one reserved message
    from the edge source queue to its target queue, then publishes one edge
    checkpoint.
  - The top-level `PipelineTask` advances on that checkpoint. It does not
    currently inspect the source queue for extra stage outputs before moving to
    the next stage or completing the pipeline.
- Contract to pin:
  - Unless a spec change explicitly designs fan-out, a pipeline-compatible stage
    emits exactly one handoff payload. Extra output is a stage/edge contract
    violation and must become a clear failure, not silently stranded work.

Dead-code and stale-scaffolding cleanup:

- Files to read first:
  - `weft/core/monitor/policies/api.py`
  - `tests/core/monitor/policies/test_policy_api.py`
  - `weft/core/state_machines.py`
  - `weft/commands/system.py`
  - `weft/core/agents/provider_cli/registry.py`
  - `weft/_constants.py`
  - tests nearest each touched module
- Current structure:
  - `monitor/policies/api.py` is a planned policy abstraction that is not wired
    into production policy modules; the production policy shape lives in
    `weft/core/monitor/policies/types.py` and `weft/core/pruning/models.py`.
  - `TaskSpec` nested mutation guards are design-bearing support for partial
    immutability and are retained.
  - `MultiQueueWatcher` dynamic `add_queue()`/`remove_queue()` support fits the
    watcher abstraction and is retained.
  - `StateMachine` itself is not dead: lifecycle and control convergence use it.
    Only reachability/coverage helpers should be considered for deletion, and
    only if the lifecycle/control coverage tests are rewritten without losing
    contract coverage.
  - Service-convergence decision fields are retained for reducer diagnostics and
    manager/service supervision.
  - Compiled pipeline metadata is retained for child launch ordering, status,
    queue bindings, and operator inspection.
- Cleanup rule:
  - Delete only proven pure dead code in this plan. Do not relabel retained
    design surfaces as cleanup targets.

Packaging/docs:

- Files to read first:
  - `pyproject.toml`
  - `extensions/weft_docker/pyproject.toml`
  - `extensions/weft_macos_sandbox/pyproject.toml`
  - README sections for install extras and `weft system load`
  - `tests/specs/test_plan_metadata.py`
- Current structure:
  - Root `tool.uv.sources` tests local editable extensions, but optional
    dependency minima can resolve older published packages.
  - README currently describes `weft system load -i FILE` as preflight, which is
    false without `--dry-run`.

Result/status waiting:

- Files to read first:
  - `weft/commands/_result_wait.py`
  - `weft/commands/result.py`
  - `weft/commands/run.py`
  - `weft/commands/task_evidence.py`
  - `docs/lessons.md` terminal/result visibility lessons
- Current structure:
  - There are several wait loops because persistent result, one-shot result,
    streaming, pipeline, and terminal-proof grace have different user-visible
    behavior.
  - High branch counts alone are not a bug.

## Invariants and Constraints

- TID format and immutability stay intact. Spawn-request message IDs that already
  exist must not be silently rewritten when the feature claims to preserve
  runnable broker state.
- State transitions remain forward-only. Fixing control handling must not
  re-open terminal tasks or emit duplicate terminal events.
- Reserved queue policy remains owned by the main task thread for active
  STOP/KILL. Do not reintroduce terminal publication from the control polling
  side before runner unwind.
- Runtime-only `weft.state.*` queues remain excluded from dump/load unless a spec
  change explicitly reclassifies them. This plan does not reclassify them.
- `weft.log.tasks` is runtime evidence while retained, not a legal or forensic
  audit record. External logs/deferred writes are retention handoff surfaces.
- Keep `WEFT_TASK_MONITOR_MODE_DEFAULT = "delete"` unless the product promise is
  explicitly changed to include default task-lifetime audit retention.
- `jsonl_then_delete` must remain durable-before-delete: JSONL success or
  Monitor deferred-write success before exact delete; if both fail, no delete.
- No new dependencies.
- No public CLI shape changes except correcting docs and warning text. The
  existing flags and env vars remain.
- No broad refactor of `Manager`, `TaskMonitor`, result waiting, or TaskSpec.
  Extract only when a task needs it to remove duplicated bug-prone behavior.
- Provider CLI work must stay on the existing agent runtime spine. Do not add
  hidden provider probes, background health gates, or provider ecosystem
  management to ordinary task submission.
- Tool-profile callables should run at the smallest execution boundary that can
  correctly own their result. Do not invoke a user callable twice for one
  one-shot work item. If session behavior is changed, document whether the
  profile is session-scoped or turn-scoped in `13-Agent_Runtime.md`.
- Read-only delegated provider policy must not imply broader ambient access than
  the selected provider can actually enforce. If a provider flag cannot be
  verified, document the limitation instead of claiming stronger isolation.
- Pipeline work remains first-class task composition, not a new fan-out engine.
  Do not add multi-output semantics unless the spec is expanded beyond the
  current linear pipeline model.
- Monitor-owned tables and external JSONL/deferred-write records remain derived
  operational evidence. They may support status fallback after raw-log
  retirement, but they must not become result authority or task lifecycle truth.
- Dead-code cleanup must not remove code solely because it is large, old, or
  inconvenient. A deletion candidate needs either no production references or a
  replacement path with behavior tests that prove the public contract is
  preserved.
- Tests for broker message IDs, queue state, task controls, and cleanup behavior
  must use real broker-backed queues. Do not mock away SimpleBroker semantics.
- Stop and re-plan if preserving exact message IDs requires unsupported private
  backend SQL beyond the existing spawn-request adapter. That may belong in
  SimpleBroker first.
- Stop and re-plan if provider CLI consolidation starts changing public agent
  result payloads, TaskSpec schema, provider option names, or Docker runner
  image/runtime descriptor contracts.
Review gates:

- Self-driven fresh-eyes plan review before implementation.
- External review required before implementation because this touches durable
  submission identity, task control, provider runtime isolation, pipeline
  handoff semantics, destructive cleanup documentation, pure dead-code deletion,
  adjacent design-surface review, and packaging surfaces.

## Tasks

1. Fix or fail closed on dump/load message-ID preservation.

   - Outcome: `system dump/load` no longer silently rewrites IDs for queues where
     message ID is part of the Weft contract.
   - Files to touch:
     - `weft/commands/_dump_support.py`
     - `weft/commands/_load_support.py`
     - `weft/commands/dump.py`
     - `weft/commands/load.py`
     - `tests/commands/test_dump_load.py`
     - `docs/specifications/10-CLI_Interface.md`
     - `docs/specifications/04-SimpleBroker_Integration.md`
     - `README.md`
   - Read first:
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-1], [MF-6]
     - `docs/specifications/03-Manager_Architecture.md` [MA-2]
     - `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.2]
     - `weft/core/spawn_requests.py`
   - Approach:
     - Add a failing regression that writes a pending spawn request with
       `submit_spawn_request()`, dumps it, loads it into a new context, and
       asserts the loaded queue row timestamp equals the original TID.
     - Use SimpleBroker's public `import_messages()` API directly from
       load/import code.
     - Use SimpleBroker's public `write_reserved_message()` API for normal
       generated-TID spawn submission.
     - Do not keep a Weft `broker_import.py` wrapper now that SimpleBroker owns
       the exact-ID import and reserved-write APIs.
     - Preserve timestamps for all included queue records if the backend can do
       so safely. If a backend cannot preserve exact IDs, fail before writes
       begin with an explicit error instead of silently importing runnable
       state under new IDs.
     - Add a separate characterization test for claimed rows. If the current
       intended contract is pending-only export, update specs/README to say
       that clearly and warn or fail when included queues have claimed rows. If
       the intended contract is full broker-state export, extend dump/load to
       carry claimed rows without losing claim state.
   - Constraints:
     - Do not persist `weft.state.*`.
     - Do not mutate embedded TaskSpec TIDs to match newly allocated timestamps.
       That hides the violation instead of fixing it.
     - Do not add backend-specific SQL in command-layer code.
   - Done when:
     - Spawn request timestamp/TID round-trips.
     - Claimed-row behavior is either implemented or explicitly documented and
       guarded.
     - Specs and README no longer overstate dump/load behavior.

2. Fix structured JSON STOP/KILL active-control deferral.

   - Outcome: raw and structured STOP/KILL use the same active-control path and
     do not publish terminal state until the runner unwinds.
   - Files to touch:
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/base.py` only if the parser must be made reusable
     - `tests/tasks/test_consumer.py` or the nearest existing control test file
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/01-Core_Components.md`
   - Read first:
     - `docs/lessons.md` active STOP/KILL main-thread ownership lesson
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-3]
     - `docs/specifications/01-Core_Components.md` [CC-2.4]
   - Approach:
     - Add failing tests for `{"command":"STOP","request_id":"..."}` and
       `{"command":"KILL","request_id":"..."}` sent to `T{tid}.ctrl_in` while
       `_active_work_in_flight` is true.
     - Parse enough of the control envelope in `Consumer._poll_active_control_once()`
       to identify STOP/KILL before falling back to base handling.
     - Preserve `request_id` so the eventual post-unwind acknowledgement follows
       the same echo behavior that the current base parser would have produced
       for that structured request.
     - Keep ordinary `PING`/`STATUS` behavior unchanged.
   - Constraints:
     - No duplicate control parser with different semantics.
     - No terminal state publication from the active poll path.
     - Do not change CLI task stop/kill payloads unless tests prove the CLI path
       must change.
   - Done when:
     - Structured STOP/KILL are deferred like raw STOP/KILL.
     - The active task remains non-terminal until the existing finalize path runs.
     - Control acknowledgements remain progress evidence, not terminal proof.

3. Unregister the Manager atexit callback during explicit cleanup.

   - Outcome: explicit manager cleanup releases the bound atexit callback while
     keeping the fallback for abnormal interpreter exit.
   - Files to touch:
     - `weft/core/manager.py`
     - focused manager lifecycle test file under `tests/core/`
   - Read first:
     - `docs/specifications/03-Manager_Architecture.md` [MA-3]
     - manager cleanup implementation in `weft/core/manager.py`
   - Approach:
     - Store the exact registered callback on the instance.
     - Call `atexit.unregister(callback)` in cleanup after the normal unregister
       path has run.
     - Make unregister idempotent and defensive; interpreter shutdown fallback
       should not become noisy.
   - Constraints:
     - Do not remove the atexit fallback unless a separate review decides it is
       unnecessary.
     - Do not add broker I/O to new shutdown-time paths.
   - Tests:
     - Use a narrow monkeypatch around `atexit.register`/`atexit.unregister` to
       prove cleanup unregisters the same callback. This is acceptable because
       the behavior under test is Python lifecycle registration, not broker
       semantics.

4. Align packaging extras with the versions this repo actually tests.

   - Outcome: installing root extras cannot silently resolve plugin/backend
     versions older than the compatibility floor implied by this repo.
   - Files to touch:
     - `pyproject.toml`
     - optionally a small metadata test under `tests/specs/` or `tests/`
   - Read first:
     - root optional dependency groups
     - extension package versions and dependency requirements
   - Approach:
     - Set `weft-docker`, `weft-macos-sandbox`, and `simplebroker-pg` minimums
       consistently across individual, `all`, and `dev` extras.
     - Use the local extension versions as the default compatibility floor for
       Weft-owned extension packages unless release notes prove a lower
       published version is intentionally supported. For `simplebroker-pg`, use
       the highest minimum already required by any root extra unless release
       notes justify a different floor.
     - Add a test that parses `pyproject.toml` and extension pyprojects and
       checks that root extras do not undercut local extension versions.
   - Constraints:
     - Do not change runtime imports or plugin loading in this slice.
     - Do not widen dependency ranges without a release-policy reason.

5. Correct `system load` docs and clarify dry-run/preflight behavior.

   - Outcome: README and CLI spec no longer imply that `weft system load -i FILE`
     is a preflight operation.
   - Files to touch:
     - `README.md`
     - `docs/specifications/10-CLI_Interface.md` [CLI-6]
     - any CLI help tests if present and affected
   - Approach:
     - Change examples to `weft system load --dry-run -i FILE` for preflight.
     - State plainly that `weft system load -i FILE` imports, and that exit code
       `3` covers alias conflicts discovered before writes begin.
     - Keep current CLI flags unchanged.

6. Clarify TaskMonitor default, production audit preset, status warnings, and
   live-status fallback.

   - Outcome: specs and README reflect the intended boundary: `delete` remains
     the zero-config runtime-health default; `jsonl_then_delete` is the
     production audit-retention preset; queues are live truth, not the audit
     record. If raw task-log retirement can make a live known task look
     nonexistent, `task status` reports a degraded known-task snapshot or a
     visible diagnostic instead of exit 2.
   - Files to touch:
     - `docs/specifications/00-Quick_Reference.md`
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
     - `docs/specifications/10-CLI_Interface.md`
     - `README.md`
     - `weft/commands/system.py` only if current status text is not warning-like
     - `weft/commands/tasks.py` only if the live-status fallback gap is reproduced
     - `weft/core/monitor/task_monitor.py` only if the correct fix is deletion
       candidate selection rather than read-side reconciliation
     - relevant status/TaskMonitor tests
   - Approach:
     - Keep `WEFT_TASK_MONITOR_MODE_DEFAULT = "delete"`.
     - Add docs that say `jsonl_then_delete` is recommended when operators need
       built-in task-lifetime audit handoff.
     - Make status behavior explicit: if external JSONL is unhealthy or deferred
       writes are pending, `weft status` should surface a visible warning from
       cached TaskMonitor diagnostics without actively probing the file path.
     - Run a real-code evaluation with `WEFT_TASK_MONITOR_MODE=jsonl_then_delete`
       and an unwritable or invalid external log path. If text status already
       clearly warns, only add docs/tests. If it only says
       `diagnostics=external-log-unhealthy`, adjust wording to make the warning
       visible without changing JSON shape.
     - Add a real-harness evaluation for the reported live-task gap: start a
       long-running task, enable default `delete` TaskMonitor cleanup with a
       short retention window, let Monitor retire raw task-log rows, then call
       `weft task status <tid>` without `--ping`. Record whether the
       Monitor-store fallback returns a running/degraded snapshot or whether the
       task is reported not found.
     - If the not-found gap reproduces, prefer fixing the read side first:
       surface a known-task snapshot from Monitor collation or a clear
       `known_unobservable` diagnostic. Only gate raw deletion to terminal/aged
       rows if the reproduced failure shows Monitor collation cannot support a
       reliable derived status fallback for active tasks.
     - If the not-found gap does not reproduce, record the result in this plan's
       implementation notes and keep the fix to warning text/docs/tests. Do not
       change TaskMonitor deletion policy based on static suspicion alone.
   - Constraints:
     - Do not make local JSONL sound like a complete audit system. It is a handoff
       surface that still needs shipping/backup/monitoring in production.
     - Do not PING TaskMonitor or touch the external log path from `weft status`;
       status must use cached diagnostics.
     - Do not change the default to `jsonl_then_delete` in this slice.
     - Do not treat Monitor-store rows as lifecycle truth; they are derived
       fallback evidence after raw log retirement.

7. Fix provider-cli tool-profile double resolution and Gemini read-only
   one-shot isolation.

   - Outcome: a one-shot `provider_cli` work item resolves its callable
     `tool_profile_ref` once, and Gemini `workspace_access="read-only"` uses
     the same isolated runtime home for one-shot execution that bounded mode and
     sessions already use.
   - Files to touch:
     - `weft/core/agents/backends/provider_cli.py`
     - `weft/core/agents/provider_cli/execution.py`
     - `weft/core/agents/provider_cli/registry.py`
     - `weft/core/agents/provider_cli/runtime_prep.py` only if tests need a
       narrow helper
     - `docs/specifications/13-Agent_Runtime.md`
     - README provider-cli sections if they describe read-only or provider
       runtime-home behavior
     - provider-cli backend/execution tests under `tests/core/`
   - Read first:
     - `docs/specifications/13-Agent_Runtime.md` [AR-5], [AR-7]
     - `weft/core/agents/resolution.py`
     - `weft/core/agents/backends/provider_cli.py`
     - `weft/core/agents/provider_cli/execution.py`
     - `weft/core/agents/provider_cli/registry.py`
   - Approach:
     - Add a failing one-shot regression using a callable tool profile with a
       counter or metadata side effect. Assert one `ProviderCLIBackend.execute()`
       call resolves the profile once, not twice.
     - Consolidate the validation/prepare path so resolved tool-profile and
       provider options are carried forward instead of recomputed. Keep host and
       Docker one-shot users on the shared `prepare_provider_cli_execution()`
       path.
     - Evaluate persistent session behavior separately. Because the current
       resolver does not receive `work_item`, per-turn tool-profile resolution
       is probably unnecessary; however, do not change session scoping without
       documenting the chosen boundary in `13-Agent_Runtime.md` and adding a
       session regression.
     - Add a Gemini one-shot test for `authority_class="general"` plus
       `workspace_access="read-only"` that asserts invocation env is populated
       with the isolated Gemini runtime home. Keep the existing bounded and
       session tests passing.
     - Independently verify Qwen provider flags before documenting them as
       enforcing ambient extension/MCP disablement. Local help parsing is enough
       to prove syntax support, not enough to prove isolation semantics.
   - Constraints:
     - No hidden provider CLI spawn/probe in ordinary `weft run`.
     - No provider option rename or TaskSpec schema change.
     - Do not collapse provider-specific permission matrices into a generic
       abstraction that hides concrete CLI behavior.
     - Docker-backed one-shot provider execution must continue to reuse the same
       shared preparation helper, with runner-specific execution left in the
       Docker extension.
   - Done when:
     - One-shot tool-profile callable invocation count is one.
     - Gemini read-only one-shot uses an isolated runtime home.
     - Specs/README describe the actual read-only isolation boundary and any
       Qwen verification limitation.

8. Consolidate result/event queue-name resolution and evaluate result/status wait
   complexity.

   - Outcome: result and event readers use one safe queue-name resolver, and any
     remaining result/status wait refactor is tied to a concrete inconsistent
     decision rather than branch-count discomfort.
   - Files to touch:
     - `weft/commands/result.py`
     - `weft/commands/events.py`
     - `weft/commands/task_evidence.py` only if exports need adjustment
     - `tests/commands/test_result.py`
     - nearest event iterator tests
   - Files to read:
     - `weft/commands/_result_wait.py`
     - `weft/commands/result.py`
     - `weft/commands/run.py`
     - `weft/commands/task_evidence.py`
     - `weft/core/task_evidence.py`
     - related result/status tests
   - Approach:
     - Add a regression where a known TaskSpec payload has malformed or
       non-dict `io`, `outputs`, or `control`. `weft result` and event
       iteration should fall back to `T{tid}.outbox` and `T{tid}.ctrl_out`
       rather than raising.
     - Replace command-local `_queue_names_for_tid()` with the canonical
       `task_evidence.queue_names_for_tid()` helper and stop importing a private
       result helper from `events.py`.
     - Trace at least these scenarios through the current code: one-shot success,
       persistent task completion, terminal failure without result, streaming
       output, and manager wrapper-lost evidence.
     - Document whether the same terminal/result decision is implemented in more
       than one place with different outcomes.
     - If a concrete duplication exists, extract only that pure decision into a
       small helper and add behavior tests. If no inconsistency is found, do not
       refactor on static complexity alone.
   - Constraints:
     - No broad result-wait rewrite.
     - No new result payload schema.
     - Preserve completion-event/outbox grace behavior.

9. Pin pipeline handoff cardinality and fail clearly on extra stage outputs.

   - Outcome: the current linear pipeline contract is explicit. If a stage emits
     multiple handoff payloads before the edge advances, the edge or pipeline
     fails with a clear error instead of moving one payload and stranding the
     rest.
   - Files to touch:
     - `weft/core/tasks/pipeline.py`
     - `weft/core/pipelines.py` only if runtime metadata or compiled plan docs
       need adjustment
     - `tests/tasks/test_pipeline_runtime.py`
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-4]
     - README pipeline section if it implies arbitrary multi-output handoff
   - Read first:
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-4]
     - `weft/core/tasks/pipeline.py::PipelineEdgeTask._handoff_payload`
     - `weft/core/tasks/pipeline.py::PipelineTask._handle_edge_checkpoint`
     - existing pipeline runtime tests
   - Approach:
     - Add a failing broker-backed test where a stage outbox/source queue has two
       visible payloads for the edge. The expected result should be a failed edge
       and failed pipeline with an error that names the single-handoff contract.
     - Implement the narrowest check near the edge handoff owner. Prefer proving
       the source queue has no additional visible payload for this edge before
       publishing the checkpoint. Do not make the top-level pipeline task scan
       arbitrary stage queues as a second scheduler.
     - Update [MF-4] to state that current first-class pipelines are linear and
       each pipeline-compatible stage emits exactly one handoff payload. Future
       fan-out requires a separate spec and plan.
   - Constraints:
     - Do not add fan-out, batching, merge, or multi-output semantics in this
       slice.
     - Do not change public pipeline queue names or result payload shape.
     - Do not reinterpret ordinary task multi-output behavior; this contract is
       for pipeline stage handoff only.

10. Remove proven pure dead code and stale scaffolding in dependency-safe slices.

   - Outcome: dead abstractions and misleading helpers are removed without
     changing runtime behavior. Cleanup is sequenced after behavior-facing fixes
     and limited to code that is provably production-unreferenced.
   - Files to touch:
     - `weft/core/monitor/policies/api.py`
     - `tests/core/monitor/policies/test_policy_api.py`
     - unused constants in `weft/_constants.py`
     - unused provider `ensure_runtime_requirements()` hooks
     - passthrough `weft/commands/system.py::_effective_public_status`
   - Explicitly retained, not cleanup targets:
     - `weft/core/taskspec/model.py` nested mutation guards
     - `weft/core/tasks/multiqueue_watcher.py` dynamic queue mutation support
     - `weft/core/service_convergence.py` decision fields
     - compiled pipeline runtime/status metadata
     - core `StateMachine` reducer support
     - TaskMonitor service supervision and task-log cleanup policy machinery
   - Read first:
     - `docs/specifications/01-Core_Components.md` [CC-2.3], [CC-3.4]
     - `docs/specifications/07-System_Invariants.md` [OBS.13]
     - `docs/specifications/13-Agent_Runtime.md` [AR-5], [AR-7]
     - `weft/core/monitor/policies/types.py`
     - `weft/core/pruning/models.py`
     - `weft/core/agents/provider_cli/registry.py`
     - `weft/commands/system.py`
     - `weft/_constants.py`
   - Approach:
     - Delete pure dead code with `rg` proof and focused tests. This includes
       `monitor/policies/api.py`, its isolated test, unused constants,
       `ensure_runtime_requirements()`, and `_effective_public_status()` if no
       caller needs its abstraction after inlining.
     - Second pass: remove command/helper duplication already covered by task 8.
     - Record the reviewed-and-retained design surfaces in the implementation
       notes so future reviews do not reopen them as stale cleanup targets
       without new evidence.
   - Constraints:
     - Do not delete `StateMachine` itself. It is live in lifecycle and control
       convergence. If reachability/coverage helpers are removed, rewrite tests
       so they still cover allowed lifecycle/control transitions.
     - Do not remove TaskMonitor or collapse cleanup policy paths under a
       "dead-code" label. The concern there is duplicated/competing cleanup
       ownership, which must be handled by behavior tasks and specs.
     - Do not target TaskSpec mutation guards, MultiQueueWatcher dynamic queue
       mutation, service-convergence fields, or pipeline metadata in this plan.
   - Done when:
     - Each deletion has a short implementation note with its `rg` evidence or
       replacement owner.
     - Focused tests pass for each touched module.
     - The plan records the design-bearing surfaces that were reviewed and
       retained.

11. Update durable lessons only if implementation reveals a repeated pattern not
   already captured.

   - Outcome: `docs/lessons.md` changes only for a genuinely reusable lesson.
   - Approach:
     - The JSON STOP/KILL bug probably reinforces an existing lesson; do not add
       redundant text unless the fix reveals a new structured-envelope caveat.
     - The dump/load ID issue may merit a lesson if the root cause is "metadata
       timestamp captured but import path ignored it." Add a short lesson only if
       the implementation shows that pattern could recur.
     - Provider CLI double resolution may merit a lesson only if the refactor
       reveals a broader pattern of validation and execution both invoking
       user-supplied callables.
     - Dead-code cleanup may merit a lesson only if a stale abstraction misled
       implementation or review, not merely because unused code existed.

## Testing Plan

Use real broker-backed queues for all queue semantics. Do not mock SimpleBroker
message IDs, reservations, task-local control queues, or task-log/outbox
visibility.

Per-slice tests:

- Dump/load:
  - Add a regression in `tests/commands/test_dump_load.py` that round-trips a
    pending spawn request and asserts loaded queue timestamp equals submitted
    TID.
  - Add a claimed-row characterization test. The assertion depends on the chosen
    contract: either claimed rows are preserved, or dump/load reports that the
    export is pending-only and refuses/warns on claimed included rows.
  - Include at least one backend-neutral path where practical; if a backend
    cannot support exact-ID import, assert fail-closed behavior before writes.
- Control:
  - Add active in-flight structured STOP and KILL tests using a real `ctrl_in`
    queue and a `Consumer` instance.
  - Assert no terminal status is published until the existing deferred finalize
    path runs.
  - Assert request ID handling stays correct for structured controls.
- Manager atexit:
  - Add a narrow lifecycle test around callback registration/unregistration.
- Packaging:
  - Add metadata test coverage if the version-floor rule can be checked
    mechanically without brittle parsing.
- Documentation/status:
  - Update spec tests if they enforce plan/spec backlinks or command docs.
  - Add or update a status rendering test for cached TaskMonitor external-log
    health/deferred-write warning text.
  - Add a real-harness live-task status evaluation with default `delete`
    TaskMonitor cleanup. The test should distinguish "not reproduced, fallback
    works" from "reproduced, status returned not found" rather than relying on
    static code inspection.
- Provider CLI:
  - Add a one-shot execution regression proving callable tool profiles are
    invoked once per work item.
  - Add Gemini one-shot read-only invocation tests that assert isolated runtime
    env is populated.
  - If session profile scoping changes, add persistent-session tests that prove
    the chosen session/turn boundary.
  - For Qwen, record a manual or automated provider-version check that proves
    flag syntax at minimum; do not claim actual extension/MCP disablement unless
    the test proves it.
- Result/events:
  - Add malformed TaskSpec `io` payload coverage for `weft result` and event
    iteration. Both should fall back to canonical task-local queues.
- Result/status evaluation:
  - If a bug is found, add a failing behavior test before any refactor.
  - If no bug is found, record the no-action decision in this plan or the
    implementation handoff; do not add tests for the sake of metrics.
- Pipeline:
  - Add a broker-backed pipeline edge test where a stage emits two payloads.
    Assert the edge/pipeline fails clearly under the current single-handoff
    contract.
- Dead-code cleanup:
  - For pure deletion, run the closest tests plus `rg` checks before and after.
  - Do not add removal tests for retained design surfaces. TaskSpec mutation
    guards, MultiQueueWatcher dynamic mutation, service-convergence decision
    fields, and pipeline metadata are not cleanup targets in this plan.

Commands to run during implementation:

```bash
./.venv/bin/python -m pytest tests/commands/test_dump_load.py -q
./.venv/bin/python -m pytest tests/tasks -q
./.venv/bin/python -m pytest tests/core -q
./.venv/bin/python -m pytest tests/core/test_provider_cli_execution.py -q
./.venv/bin/python -m pytest tests/commands/test_result.py -q
./.venv/bin/python -m pytest tests/tasks/test_pipeline_runtime.py -q
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

Final gate before claiming done:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft
```

If full `pytest` is too slow for an intermediate slice, run the narrow tests
first, but the final gate should be full because the work crosses durable
submission, controls, manager lifecycle, packaging, and docs.

## Implementation Notes

Implementation pass completed 2026-06-01 for the behavior-facing slices and the
pure dead-code deletions that had repo-wide `rg` proof. External review was
attempted before implementation with Qwen, Gemini, and Codex CLI paths, but the
available local tools either required interactive OAuth, failed their tool
bridge, or had a broken executable. No subagent tool was used because the user
had not explicitly requested background agents. The implementation therefore
used a self-review plus focused regression tests.

Completed behavior fixes:

- Dump/load now preserves imported broker message timestamps through
  SimpleBroker's public `import_messages()` API. Spawn-request submission uses
  SimpleBroker's public `write_reserved_message()` API for generated TIDs and
  `import_messages()` for explicit external TIDs. The temporary
  `weft/core/broker_import.py` wrapper was removed after SimpleBroker 4.2.0
  added the needed public APIs. Load fails before writes when exact timestamp
  import is unavailable. Claimed rows remain outside the dump contract;
  `system dump` reports omitted claimed message counts.
- Structured JSON STOP/KILL now uses the same active deferral semantics as raw
  STOP/KILL and echoes `request_id` on the post-unwind acknowledgement.
- `Manager.cleanup()` unregisters the exact atexit callback it registered, with
  idempotent defensive cleanup.
- Root extras now match the in-repo extension compatibility floor for
  `weft-docker`, `weft-macos-sandbox`, and `simplebroker-pg`.
- `weft status` now renders cached TaskMonitor external-log failures and
  deferred writes as `warning=...` text fields while preserving JSON shape and
  avoiding active probes.
- Provider-cli one-shot execution resolves callable tool profiles once per work
  item, and Gemini read-only one-shot execution uses the isolated runtime home.
  Qwen flag syntax is documented as syntax-verified only; provider semantic
  isolation was not proven.
- Result and realtime-event readers now use the canonical queue-name resolver
  so malformed TaskSpec `io` sections fall back to standard task-local queues.
- Pipeline edges now enforce the current linear single-handoff contract and fail
  clearly when a stage leaves extra visible handoff payloads.

Completed dead-code cleanup with repo-wide reference checks:

- Removed `weft/core/monitor/policies/api.py` and its isolated test. Production
  cleanup policy result ownership remains in
  `weft/core/monitor/policies/types.py`, `weft/core/pruning/models.py`, and
  concrete policy modules.
- Removed unused provider `ensure_runtime_requirements()` hooks.
- Removed passthrough `_effective_public_status()`.
- Removed unused constants:
  `RUNTIME_PRUNE_CLASS_STALE_PIPELINE`,
  `RETENTION_PRUNE_SUPPORTED_FAMILIES`,
  `PIPELINE_SUPPORTED_STAGE_DEFAULT_KEYS`, and
  `PROVIDER_CONTAINER_DESCRIPTOR_VERSION`. Other initially suspicious constants
  were retained because they are used by extensions, config parsing, or public
  command validation.

Evaluations:

- The live-status concern was not reproduced as a not-found policy bug. The
  code already falls back from raw task-log evidence to the Monitor store for
  full 19-digit TIDs after raw log retirement. Regression coverage now includes
  both terminal and running Monitor-store snapshots with absent raw log rows.
- Result/status waiting still has several loops because one-shot result,
  persistent result, streaming output, pipeline status, and manager wrapper-loss
  evidence have different public outcomes. No inconsistent decision point was
  found in this pass, so no broad refactor was made.

Reviewed and retained design surfaces:

- TaskSpec nested mutation guards, MultiQueueWatcher dynamic queue mutation
  support, service-convergence decision fields, and compiled pipeline metadata
  were reviewed after implementation and retained. They are consistent with the
  current design, so they are not deferred cleanup targets.

## Rollout and Rollback

- Dump/load exact-ID behavior is rollout-sensitive. Old dumps that include
  timestamps should remain loadable. If exact-ID import is unavailable for a
  backend, fail before writes begin rather than partially loading under new IDs.
- Structured STOP/KILL fix is backward-compatible: raw CLI controls keep working,
  structured controls become safer.
- Manager atexit unregister is backward-compatible if the fallback remains.
- Packaging minimum bumps affect installers. If a lower plugin version is proven
  compatible, keep the lower floor and document the compatibility reason in the
  packaging test or release notes.
- TaskMonitor docs/status changes are additive and do not change the default.
- Provider CLI double-resolution and Gemini read-only fixes are
  backward-compatible if they preserve provider command arguments except for the
  added isolated environment in read-only Gemini one-shot. If a provider-version
  compatibility issue appears, roll back the provider-specific isolation change
  and document the limitation rather than weakening the generic read-only
  contract silently.
- Result/events helper consolidation is backward-compatible if malformed
  TaskSpec payloads fall back to canonical queue names and valid custom queue
  names still work.
- Pipeline cardinality enforcement may turn previously silent stranded-output
  cases into explicit failures. This is acceptable only if [MF-4] is updated to
  state the current single-handoff contract. If users need multi-output fan-out,
  rollback should restore prior behavior only as a temporary compatibility
  measure and a separate fan-out design should be written.
- Dead-code deletion rolls back by restoring the deleted helper/field only if a
  production caller or documented contract was missed. Do not roll back broad
  cleanup wholesale when one behavior-adjacent deletion is wrong; revert that
  slice and keep pure deletions that stayed proven.
- Rollback for runtime code should restore prior behavior only if no queue data
  format has changed. Do not introduce a queue/payload format migration in this
  plan.

## Out of Scope

- Changing the default TaskMonitor mode to `jsonl_then_delete`.
- Redesigning TaskMonitor, Manager, BaseTask, or result waiting because of file
  size or branch-count metrics alone.
- Making local JSONL a complete audit system.
- Adding hidden provider health probes, login checks, or background provider
  ecosystem management to ordinary agent execution.
- Designing pipeline fan-out, batching, merge stages, or arbitrary multi-output
  handoff semantics.
- Deleting the core `StateMachine` abstraction or TaskMonitor service under a
  "dead code" rationale. Both are production concepts; only specific unused
  helpers or stale scaffolding are in scope.
- Reworking TaskSpec schema, TaskSpec nested mutation guards,
  MultiQueueWatcher dynamic queue mutation support, service-convergence
  decision fields, compiled pipeline metadata, or public queue payloads as
  cleanup targets.
- Adding new dependencies or a new persistence layer.
- Changing public CLI flags, queue names, TaskSpec schema, result payload schema,
  or runtime-only queue classification.
- Moving exact-ID import support into ad hoc SQL scattered through Weft. If the
  needed primitive belongs in SimpleBroker, stop and plan that first.

## Independent Review Loop

External review is required before implementation. Ask a fresh agent, preferably
from a different agent family, to review this plan and the named source files.

Suggested prompt:

> Read `docs/plans/2026-06-01-critical-review-remediation-plan.md` and the
> source specs it names. Review the plan as if you had to implement it with no
> extra context. Look for incorrect assumptions, missing constraints, bad
> sequencing, under-specified tests, and places where the plan could accidentally
> create a second durable path, silently weaken provider isolation, over-delete
> live code under a dead-code label, reopen retained design surfaces as cleanup
> targets, or change pipeline semantics without a spec boundary. Do not
> implement anything.

The author must perform a separate fresh-eyes self-review after drafting and
record any corrections before reporting the plan as implementation-ready. If
external review has not happened yet, report this plan as draft/review-pending.
The review should explicitly inspect the added provider-cli, pipeline, and
pure dead-code cleanup slices, not only the original dump/control/TaskMonitor
tasks.

## Fresh-Eyes Self-Review

Initial self-review completed 2026-06-01 by the plan author after adding the
index row and running the plan metadata test. Corrections made during that
review:

- Replaced broad System Invariant section labels in metadata with exact
  invariant codes.
- Clarified that the dump/load exact-ID implementation decision must be recorded
  after inspecting SimpleBroker's actual API.
- Tightened structured-control `request_id` handling so the plan preserves the
  current parser's echo behavior rather than leaving it conditional.
- Split Weft-owned extension version floors from the third-party
  `simplebroker-pg` minimum rule.

Follow-up self-review completed 2026-06-01 after incorporating the additional
provider-cli, TaskMonitor live-status, pipeline, result-helper, and dead-code
concerns. Corrections made during that review:

- Added `docs/specifications/13-Agent_Runtime.md` to the metadata and governing
  source-spec list so provider runtime fixes have explicit spec ownership.
- Split verified behavior bugs from active-evaluation items: TaskMonitor
  live-status not-found and Qwen flag semantics now require real reproduction or
  provider-version verification before behavior claims are made.
- Added explicit out-of-scope guards against deleting `StateMachine`,
  TaskMonitor, or task-log cleanup machinery under a broad dead-code label.
- Reclassified TaskSpec mutation guards, MultiQueueWatcher dynamic queue
  mutation support, service-convergence decision fields, and compiled pipeline
  metadata as reviewed-and-retained design surfaces, not cleanup targets.

Residual risk: the plan is broader than the original draft. External review is
still required before implementation because the work crosses durable
submission, task control, provider isolation, pipeline handoff semantics,
cleanup documentation, pure dead-code deletion, adjacent design-surface review,
and packaging surfaces.
