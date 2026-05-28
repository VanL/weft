# Lifecycle Reconciliation Architecture Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Establish the release sequence and architecture for making Weft lifecycle
state coherent, reconcilable, and cleanable without turning broker queues into
long-term audit storage. Each implementation slice should ship as an
independent Weft release, be deployed on ops, and be validated against the live
broker before the next slice begins.

This is a meta-plan. It records the target architecture and implementation
order. It does not authorize broad code changes or destructive cleanup by
itself. Each release below must get a focused implementation plan before work
starts.

## 2. Source Documents

Source specs:

- `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.3], [CC-3.4]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3],
  [MF-5], [MF-6], "Cleanup Boundary", "Queue Management Patterns"
- `docs/specifications/07-System_Invariants.md` [STATE.1], [STATE.2],
  [STATE.4], [OBS.1], [OBS.2], [OBS.3], [CTX.3]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2.1], [CLI-6]

Guidance:

- `AGENTS.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/runbooks/testing-patterns.md`

Related plans:

- `docs/plans/2026-04-24-manager-status-container-pid-liveness-plan.md`
  partially addressed manager-specific stale PID status reconstruction. The
  live ops evidence shows the same stale PID issue applies more broadly to old
  host task runtime handles.
- `docs/plans/2026-04-30-known-tid-terminal-snapshot-api-plan.md`
  introduced direct known-TID terminal evidence surfaces, including outbox and
  typed terminal `ctrl_out` reads.
- `docs/plans/2026-04-30-task-log-cursor-high-water-mark-plan.md`
  tightened append-only task-log cursor behavior and should remain separate
  from retention and cleanup work.

Spec delta needed:

- Current specs intentionally say there is no separate queue-lifecycle service
  and no age-based output sweeper. The monitor/reaper releases below require
  spec updates before implementation is considered done.
- Current specs treat `weft.log.tasks` as the durable lifecycle log. The
  intended delta is narrower: broker queues remain durable coordination and
  recovery surfaces, while long-term audit retention moves to disk/stdout
  sinks or future log collectors.

## 3. Current Evidence And Problem Split

Live ops observations on 2026-05-06:

- Active manager: `1778090743675379712`, supervised by Docker container
  `fe4e08ab4e4d`.
- `weft.spawn.requests` was empty. The manager spawn queue was not backed up.
- `weft status --json` reported roughly 206k broker messages and a polluted
  task table: many rows showed `status="running"` even though `completed_at`
  was set and the last event was terminal-looking (`work_failed`,
  `control_stop`, or `task_signal_stop`).
- Many "running" rows were caused by old host runtime handles with small PIDs
  such as `74`, `100`, and `307`. On the host these PIDs were unrelated kernel
  or system processes, so they were not valid proof that the original task was
  still running.
- A created/waiting task had a manager-authored terminal `ctrl_out` envelope:
  `status="failed"`, `error="Task wrapper exited before publishing terminal state"`.
  Project-wide status did not fold that terminal proof into the task snapshot.
- A `wazuh-case-rollup` task had an outbox result but no terminal task-log
  event, so status still showed it as running/working despite no corresponding
  worker process.
- Broker counts were dominated by live retained rows, not dead tuples:
  `weft.log.tasks`, `weft.state.tid_mappings`, per-task `outbox`, per-task
  `reserved`, and control queues. Postgres autovacuum existed, so vacuum alone
  cannot solve the retention problem.
- Some task failures are real higher-level failures and must remain visible in
  exported summaries, including Wazuh idempotency/data conflicts and malformed
  LLM JSON schema output.

Problem ownership:

- Weft owns lifecycle coherence, terminal evidence interpretation, runtime
  liveness proof, monitor/reaper behavior, and broker retention policy.
- Higher-level systems own domain failures such as Wazuh data invariants,
  target file naming, and LLM schema retry behavior.

## 4. Architecture Decision

Do not overload the Manager.

Keep four roles separate:

1. Lifecycle write path:
   - Owner: `Manager`, `Consumer`, `BaseTask`, runner/session code.
   - Responsibility: publish task lifecycle, outbox, control, reserved-policy,
     and manager-supervisor terminal evidence.
   - Boundary: this remains the canonical
     `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` spine.

2. Read model:
   - Owner: `weft status`, `weft task status`, `weft result`, and client
     command helpers.
   - Responsibility: reconstruct coherent public state from queue evidence.
   - Boundary: no impossible public lifecycle states. Runtime conflicts must
     be diagnostics, not backward lifecycle transitions.

3. Evidence/reconciliation model:
   - Owner: a shared command/core helper introduced by a focused sub-plan.
   - Responsibility: classify evidence from `weft.log.tasks`, typed terminal
     `ctrl_out`, final outbox, runtime mapping, manager registry, and task-local
     inbox/reserved queues.
   - Boundary: classification informs status and cleanup safety. It does not
     add new public task states.

4. Monitor/reaper:
   - Owner: a normal Weft task, preferably a new lifecycle-specific task built
     closer to `Observer` than the existing reserve-and-forward `Monitor`.
   - Responsibility: peek, remember high-water marks, export summaries, and
     delete exact proven-stale messages when policy allows.
   - Boundary: not lifecycle authority, not manager internals, not a hidden
     database.

Existing primitives:

- `weft/core/tasks/observer.py` defines `Observer`, which peeks without
  consuming. This is the closer foundation for lifecycle monitoring.
- `weft/core/tasks/monitor.py` defines `Monitor`, which reserves inbox
  messages and forwards them downstream. Do not reuse that behavior for
  lifecycle cleanup unless a sub-plan explicitly needs reserve-and-forward
  semantics.
- `weft/core/tasks/multiqueue_watcher.py` already supports peek/read/reserve
  queue modes. Reuse it instead of inventing a second watcher loop.

## 5. Evidence And Reconciliation Model

Public task lifecycle states stay unchanged:

- `created`
- `spawning`
- `running`
- `completed`
- `failed`
- `timeout`
- `cancelled`
- `killed`

Add internal reconciliation classifications instead of new public states:

- `live`: lifecycle and runtime proof agree that work is active.
- `terminal_log`: `weft.log.tasks` contains terminal lifecycle proof.
- `terminal_ctrl_out`: typed terminal envelope exists on task `ctrl_out`.
- `result_without_terminal`: final outbox exists, but no terminal lifecycle
  proof is visible.
- `wrapper_lost`: manager terminal envelope says the task wrapper exited before
  publishing terminal state.
- `stale_created`: task stayed created/waiting past a grace window with no
  live owner proof.
- `orphaned_reserved`: reserved work remains with no live owner proof.
- `orphaned_inbox`: inbox work remains for a task that cannot be launched or
  recovered automatically.
- `runtime_conflict`: lifecycle evidence and runtime-liveness evidence
  disagree.
- `domain_failure`: terminal task failure that belongs to the target workload,
  not Weft runtime mechanics.
- `unknown`: evidence is insufficient for cleanup or terminal reconstruction.

Evidence priority:

1. Terminal task-log events are lifecycle proof.
2. Typed terminal `ctrl_out` envelopes are terminal proof when task-log terminal
   proof is missing.
3. For one-shot, non-persistent tasks, final outbox plus dead/missing runtime
   may prove completion after the grace window. For persistent, interactive, or
   streaming tasks, outbox alone is not terminal proof.
4. Runtime liveness can support `running`, but weak or stale PID evidence must
   never reanimate terminal lifecycle state.
5. Unclaimed inbox/reserved messages are recovery material until a proof rule
   marks them obsolete.
6. Claimed historical residue can be cleanup material only after summary export
   and live-owner checks pass.

Public status rule:

- `status`, `event`, `started_at`, `completed_at`, and `return_code` must be
  mutually coherent.
- If evidence conflicts, public output should keep lifecycle state valid and
  expose conflict through diagnostics such as `reconciliation`,
  `runtime_conflict`, or similar fields defined by a sub-plan.
- Do not emit `status="running"` with `completed_at` set.

## 6. Retention Philosophy

Broker queues are durable coordination and recovery surfaces. They are not the
long-term audit archive.

Long-term retention should be written to:

- disk under a Weft-owned archive path, for example
  `.weft/archive/tasks/YYYY-MM-DD.jsonl`
- stdout when the monitor is configured for supervisor/log-collector capture
- a future log collector sink, outside this meta-plan

Retention rules:

- Export or summarize before deleting any lifecycle, result, control, inbox, or
  reserved message that could explain task behavior.
- Keep broker rows long enough for active recovery and operator inspection.
- Prune runtime-only `weft.state.*` queues more aggressively because specs
  already classify them as runtime-only.
- Do not make `weft.log.tasks` an append-forever audit database.
- Do not create a parallel SQL status table or hidden lifecycle database.

## 7. Release Train And Sub-Plans

Each release below must have its own implementation plan in `docs/plans/`,
receive independent review, ship as a Weft release, deploy to ops, and pass
the ops validation gate before the next release begins.

### Release 1: Status Coherence And Stale PID Reanimation

Plan to create:

- `YYYY-MM-DD-status-coherence-and-stale-pid-liveness-plan.md`

Outcome:

- `weft status` and `weft task status` stop reanimating terminal tasks as
  `running` from weak or stale host PID evidence.
- Public snapshots never violate TaskSpec state invariants.
- Runtime/lifecycle disagreement becomes explicit diagnostic metadata instead
  of a backward lifecycle status rewrite.

Required implementation boundaries:

- Touch read-model code only unless a failing test proves the write path also
  needs a narrow fix.
- Do not introduce monitor/reaper logic in this release.
- Preserve manager registry authority for active managers.
- Preserve generator-based task-log replay.

Ops validation after release:

- Deploy to ops.
- Source `~/governance/.envrc` before using Weft.
- Run `/opt/venv/bin/weft status --json`.
- Confirm there are no rows with `status="running"` and `completed_at` set.
- Confirm old terminal-looking rows are terminal or explicitly marked as
  conflicted/stale.
- Confirm the active manager remains visible in `managers`.

### Release 2: Shared Task Evidence And Reconciliation Classification

Plan to create:

- `YYYY-MM-DD-task-evidence-reconciliation-model-plan.md`

Outcome:

- Status, known-TID task status, result helpers, and the future monitor share
  one evidence-reading model.
- Typed terminal `ctrl_out` is folded into status when task-log terminal proof
  is missing.
- One-shot final outbox evidence can be classified without treating all outbox
  messages as terminal.
- Reconciliation classifications are internal diagnostics, not new public task
  states.

Required implementation boundaries:

- Reuse existing known-TID terminal snapshot work where it fits.
- Do not consume outbox or `ctrl_out` during status/evidence reads.
- Do not treat arbitrary `ctrl_out` payloads as terminal; only typed terminal
  envelopes count.
- Do not add cleanup or deletion in this release.

Ops validation after release:

- Confirm the sampled created/waiting task with manager terminal `ctrl_out`
  appears terminal or as `wrapper_lost`.
- Confirm the `wazuh-case-rollup` outbox-without-terminal case is no longer
  shown as plain working; it should be classified as result evidence without
  terminal publication or equivalent.
- Confirm real domain failures remain visible and are not swept into stale
  cleanup categories.

### Release 3: Terminal Publication And Wrapper-Loss Hardening

Plan to create:

- `YYYY-MM-DD-terminal-publication-hardening-plan.md`

Outcome:

- Close write-path gaps that allow result publication without terminal
  lifecycle publication for ordinary one-shot tasks.
- Ensure manager-authored wrapper-loss terminal envelopes are produced and
  shaped consistently when child wrappers exit before task-owned terminal
  publication.
- Preserve the result/completion grace window.

Required implementation boundaries:

- Stay on the canonical durable spine.
- Do not make manager terminal envelopes normal success results.
- Do not write supervisor-generated errors to outbox unless a spec update
  explicitly defines that contract.
- Keep persistent, streaming, and interactive task semantics separate from
  one-shot task semantics.

Ops validation after release:

- Run representative one-shot governance tasks.
- Confirm successful tasks produce coherent terminal log state and result
  visibility.
- Confirm wrapper-loss failures are terminal in status without manual queue
  inspection.

### Release 4: Peek-Based Lifecycle Monitor With Archive Sink

Plan to create:

- `YYYY-MM-DD-lifecycle-monitor-archive-sink-plan.md`

Outcome:

- Introduce a lifecycle monitor task that peeks at selected queues, tracks
  high-water marks, exports compact task summaries to disk or stdout, and runs
  safely across restarts.
- Default mode is report/dry-run. No destructive cleanup in this first monitor
  release.

Required implementation boundaries:

- Build closer to `Observer` than the current reserve-and-forward `Monitor`.
- Reuse `MultiQueueWatcher` and existing queue helpers.
- Checkpoints are operational hints, not lifecycle truth.
- Archive output must be append-only and restart-safe.
- The monitor must not become a required dependency for correct status.

Ops validation after release:

- Run monitor in dry-run/report mode on ops.
- Confirm it can scan the current broker without consuming messages.
- Confirm archive/stdout summaries distinguish Weft runtime issues from
  higher-level domain failures.
- Confirm restart does not duplicate destructive actions because there are none
  yet.

### Release 5: Runtime-State Pruning

Plan to create:

- `YYYY-MM-DD-runtime-state-pruning-plan.md`

Outcome:

- Prune runtime-only queues such as `weft.state.tid_mappings`,
  `weft.state.managers`, `weft.state.streaming`, and obsolete runtime state
  queues after live/recent safety checks.

Required implementation boundaries:

- Do not prune task-local queues in this release unless the sub-plan proves the
  task-local cleanup rule is already implemented and validated.
- Preserve enough recent TID mapping state for short-TID resolution and active
  task control.
- Keep `weft.state.*` excluded from dump/load.

Ops validation after release:

- Run dry-run pruning first and record before/after candidate counts.
- Run active pruning only after dry-run output matches expectations.
- Confirm active manager and recent task control still work.

### Release 6: Task-Local Reaper And Broker Retention Policy

Plan to create:

- `YYYY-MM-DD-task-local-reaper-retention-policy-plan.md`

Outcome:

- Delete exact task-local messages that are proven stale by the shared evidence
  model and already archived/summarized.
- Apply retention policy to `weft.log.tasks`, task outboxes, `ctrl_out`,
  `ctrl_in`, inbox, and reserved queues.

Required implementation boundaries:

- Destructive cleanup must be exact-message based where practical.
- Dry-run is mandatory.
- Archive/summarize before deletion.
- Never bulk-delete unclaimed inbox/reserved work unless the proof rule says it
  is obsolete and unrecoverable.
- Cleanup failure must not rewrite lifecycle truth.

Ops validation after release:

- Run dry-run on ops and inspect candidate groups.
- Execute a narrow prune on one safe class first, such as old claimed
  runtime-only residue after archive.
- Confirm broker message count drops and `weft status --json` remains coherent.
- Keep a copy of the archive output produced before deletion.

### Release 7: Ops Cleanup Runbook

Plan to create:

- `YYYY-MM-DD-ops-lifecycle-cleanup-runbook-plan.md`

Outcome:

- Provide an operator runbook for the live ops cleanup after Releases 1-6 land.

Required runbook contents:

- exact commands with `.envrc` loading
- dry-run expectations
- archive destination
- eligible queue classes
- ineligible queue classes
- rollback limits
- post-cleanup checks
- domain-failure handoff notes

Ops validation:

- Run the runbook on ops.
- Capture before/after message counts by queue family.
- Confirm the manager spawn queue remains drained unless new work is submitted.
- Confirm status table is not polluted by old contradictory terminal state.

## 8. Invariants And Constraints

Global invariants for all sub-plans:

- TID format and immutability do not change.
- TaskSpec `spec` and `io` remain immutable after resolved TaskSpec creation.
- State transitions remain forward-only.
- Public task states remain the existing state set.
- Queue names remain stable.
- `weft.state.*` queues remain runtime-only.
- Append-only queue history reads must use generator-based helpers or cursor
  APIs, not fixed `peek_many(limit=...)` reads.
- The Manager remains a dispatcher/supervisor, not a reaper or audit writer.
- The monitor/reaper must not be required for ordinary task execution,
  terminal publication, or status correctness.
- Long-term audit output goes to disk/stdout/log collector, not broker queues.
- Destructive cleanup must have a dry-run, proof rule, and post-cleanup
  validation.

Stop and re-plan if:

- implementation creates a second lifecycle database
- status starts depending on monitor memory or monitor checkpoints for
  correctness
- cleanup code needs to guess whether work is obsolete
- the Manager starts owning broad retention or reaping behavior
- a sub-plan tries to combine more than one release above
- rollback cannot be described before implementation starts

## 9. Testing Strategy

Every implementation sub-plan must include focused tests and a release-level
ops validation gate.

Local testing guidance:

- Use real broker-backed queues for status, evidence, and cleanup behavior.
- Use `WeftTestHarness` where manager/task lifecycle behavior matters.
- Use synthetic broker records only when the bug is read-model reconstruction
  and starting real processes would obscure the assertion.
- Do not mock queue reserve/peek/read semantics when a real broker-backed test
  is practical.
- Test public status/result/client behavior first; use private helper tests
  only for complex classification edges.

Suggested final gates per release:

```bash
uv run pytest tests/commands/test_status.py -q
uv run pytest tests/commands/test_task_commands.py -q
uv run pytest tests/commands/test_result.py -q
uv run pytest tests/core/test_manager.py -q
uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox
uv run ruff check weft
```

Sub-plans may narrow these commands when the slice is clearly smaller, but any
release touching cleanup, status, manager, or task lifecycle should explain why
it is safe not to run the broader gates.

Ops validation guidance:

- Always load `~/governance/.envrc` before running Weft on ops.
- Prefer JSON status output for machine checks.
- Capture queue counts before and after monitor/reaper releases.
- Verify both correctness and non-regression:
  - active manager remains visible
  - spawn queue is not falsely reported as backed up
  - no impossible status rows
  - known real domain failures remain visible in archive or status summaries

## 10. Rollout And Rollback

Rollout:

1. Implement one release slice.
2. Run local tests and independent review.
3. Update specs and backlinks for that slice.
4. Cut a Weft release.
5. Deploy to ops.
6. Run that release's ops validation gate.
7. Do not start the next release until the live validation is understood.

Rollback:

- Releases 1-3 should be backward-compatible read/write correctness fixes. If
  a regression appears, roll back the package and preserve broker state.
- Releases 4-5 must default to non-destructive or tightly scoped behavior so
  package rollback is sufficient.
- Release 6 is destructive. It must write archive output before deletion and
  must document what cannot be undone from broker state alone.
- The ops cleanup runbook is not run until destructive cleanup behavior has
  already shipped and passed dry-run validation.

One-way doors:

- deleting broker messages
- changing public JSON output in a breaking way
- changing archive format without versioning
- changing queue names or TaskSpec schema

These require independent review and explicit rollback notes in the sub-plan.

## 11. Independent Review Loop

Before implementing each release, run an independent review of that release's
implementation plan.

Recommended review prompt:

> Read the plan at `[path]`. Carefully examine the plan, source specs, and the
> named code paths. Look for errors, unsafe cleanup assumptions, hidden
> coupling, rollback gaps, and places where a zero-context implementer would
> make the wrong choice. Do not implement anything. Answer whether you could
> implement this confidently and correctly if asked.

The author must address each review point by updating the plan, explaining why
the current path still holds, or recording the point as out of scope.

## 12. Out Of Scope For This Meta-Plan

- Implementing any release slice.
- Running cleanup on ops.
- Adding new public task states.
- Turning the monitor into lifecycle authority.
- Making broker queues the permanent audit archive.
- Fixing governance-domain task failures such as Wazuh idempotency conflicts or
  LLM schema retry behavior.
- Adding a new external log collector.
- Adding new dependencies.

## 13. Fresh-Eyes Checklist For Sub-Plans

Each sub-plan should answer these before implementation:

- Which layer owns this behavior: lifecycle write path, read model, evidence
  model, or monitor/reaper?
- Which exact queue evidence proves the outcome?
- Which messages, if any, may be deleted?
- What archive or stdout output is produced before deletion?
- What can go wrong if the process restarts midway?
- What proves success locally?
- What proves success on ops after deployment?
- What is the rollback path?
