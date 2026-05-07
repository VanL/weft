# Task Local Reaper And Retention Policy Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

Parent plan:
[`docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
Release slice: Release 6

## 1. Goal

Add explicit, foreground retention pruning for task-local queues and selected
lifecycle-log rows after archive proof and conservative stale-evidence rules.

This release is the first destructive cleanup slice for task-local broker
state. It must default to dry-run, archive before delete for ordinary apply,
delete exact message IDs only where the backend supports it, and preserve
recovery material unless the shared evidence model proves that material is
obsolete and unrecoverable.

`--force` is different. When a human explicitly runs `weft system prune
--apply --force`, the command must treat that as an intentional override of
ordinary safety protections. We do not silently override the human back to
normal-mode caution. The only non-overridable constraints are mechanical:
explicit apply mode, selected command scope, exact message identity, and
backend deletion capability.

This is not a Manager feature, not a background daemon, and not a new lifecycle
authority.

## 2. Source Documents

Source specs:

- [`docs/specifications/00-Quick_Reference.md`](../specifications/00-Quick_Reference.md)
  "Queue Names", "CLI Surface", "Operational Files"
- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md)
  [CC-2.2], [CC-2.3], [CC-3.4]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-2], [MF-3], [MF-5], [MF-6], "Cleanup Boundary",
  "Queue Management Patterns"
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [STATE.1], [STATE.2], [STATE.4], [OBS.1], [OBS.2], [OBS.3],
  [OBS.13], [OBS.14], [OBS.16], [MANAGER.3]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md)
  [CLI-6]

Guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/README.md`](../agent-context/README.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/testing-patterns.md`](../agent-context/runbooks/testing-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)

Related plans:

- [`2026-05-06-lifecycle-reconciliation-architecture-plan.md`](./2026-05-06-lifecycle-reconciliation-architecture-plan.md)
  defines Release 6 as task-local reaper and broker retention policy.
- [`2026-05-06-task-evidence-reconciliation-model-plan.md`](./2026-05-06-task-evidence-reconciliation-model-plan.md)
  defines the shared read-only evidence classifications that retention pruning
  must reuse.
- [`2026-05-07-lifecycle-monitor-archive-sink-plan.md`](./2026-05-07-lifecycle-monitor-archive-sink-plan.md)
  shipped non-destructive lifecycle summaries and archive sinks. This release
  may reuse archive sink ideas but must not make status depend on monitor
  checkpoints.
- [`2026-05-07-runtime-state-pruning-plan.md`](./2026-05-07-runtime-state-pruning-plan.md)
  shipped `weft system prune` for runtime-only `weft.state.*` queues. Phase 6
  should extend that operator cleanup surface without regressing existing
  runtime-state pruning behavior.
- [`2026-04-30-known-tid-terminal-snapshot-api-plan.md`](./2026-04-30-known-tid-terminal-snapshot-api-plan.md)
  introduced exact task-local acknowledgement targets for known-TID terminal
  observations. Those targets are useful design precedent, but this release
  must not conflate result acknowledgement with broad retention cleanup.

Spec delta required before implementation is done:

- Update [MF-5], "Cleanup Boundary", and "Queue Management Patterns" to define
  explicit task-local and lifecycle-log retention pruning.
- Update [OBS.13] to include retention-prune archive/report outputs as
  operational evidence only.
- Add a new invariant near [OBS.14]/[OBS.16] that task-local destructive
  pruning requires archive proof, exact message IDs, and shared evidence.
- Update [CLI-6] with the extended `weft system prune` options and archive
  contract.
- Update `00-Quick_Reference.md` operational files if a new archive path lands.
- Add backlinks from touched spec sections to this plan.

## 3. Non-Goals

Do not implement these in Release 6:

- A background daemon, scheduler, or Manager-owned reaper.
- Any automatic cleanup during `weft status`, `weft task status`, `weft result`,
  manager startup, task startup, or lifecycle monitor scanning.
- Any new public task state.
- Any second lifecycle database, SQL status table, or hidden retention state
  store.
- Any queue-name changes.
- Any bulk queue deletion by queue name alone.
- Any deletion that depends on fixed-size `peek_many(limit=...)` history reads.
- Any task-local cleanup based only on age without evidence classification.
- Any ordinary cleanup of active, ambiguous, persistent, interactive,
  streaming, or named-endpoint-owned tasks unless a later plan proves a
  narrower safe rule. `--force` is the explicit human override for these
  protections inside the selected prune scope.
- Any attempt to solve higher-level domain failures such as Wazuh data
  conflicts or malformed LLM JSON output.
- Any new dependency.
- Any release of an ops cleanup runbook. That is Release 7.

If implementation starts needing one of these, stop. The plan is drifting away
from Release 6.

## 4. Current Context

Relevant current behavior:

- `weft system prune` currently lives in `weft/commands/runtime_prune.py` and
  `weft/cli/app.py`. It prunes only runtime-only `weft.state.*` queues.
- `weft system lifecycle-monitor` lives in
  `weft/commands/lifecycle_monitor.py`. It scans `weft.log.tasks`, writes
  compact summaries to stdout or disk, and writes operational checkpoints only
  after archive output succeeds. It does not delete broker messages.
- Shared evidence lives in `weft/commands/task_evidence.py`.
  Important helpers:
  - `log_terminal_evidence()`
  - `task_local_terminal_evidence()`
  - `peek_terminal_ctrl_out_evidence()`
  - `peek_final_outbox_evidence()`
  - `claimed_outbox_result_evidence()`
  - `queue_names_for_tid()`
  - `control_queue_names_for_tid()`
  - `queue_message_counts()`
- Task-local acknowledgement precedent exists in `weft/commands/tasks.py`
  `ack_terminal_snapshot()`, which deletes exact queue messages from
  `TaskTerminalSnapshot.ack_targets`.
- `weft/commands/result.py` still consumes final outbox messages for result
  materialization. Retention pruning must not compete with normal result reads
  for active or recent tasks.
- `weft.log.tasks` remains the durable lifecycle source for status
  reconstruction. Phase 6 may prune selected old log rows only after archive
  proof and only when enough retained lifecycle evidence remains for recent
  status/result behavior.
- `weft.state.*` cleanup is already runtime-only and must remain separate from
  task-local/lifecycle retention cleanup in the code and CLI output.

Queue families in scope for this release:

- `weft.log.tasks`
- `T{tid}.outbox`
- `T{tid}.ctrl_out`
- `T{tid}.ctrl_in`
- `T{tid}.inbox`
- `T{tid}.reserved`

Queue families out of scope for this release:

- `weft.spawn.requests`
- `weft.manager.ctrl_in`
- `weft.manager.ctrl_out`
- `weft.manager.outbox`
- `weft.state.*` except through the already-shipped runtime-state pruning path
- `P{tid}.*` pipeline queues unless a separate task in this plan explicitly
  proves pipeline task-local parity. First implementation should treat pipeline
  queues as protected.

## 5. Architecture Decision

Extend `weft system prune` into one explicit operator cleanup front door with
separate prune families.

Keep three internal modules distinct:

1. Existing runtime-state pruning:
   - owner: `weft/commands/runtime_prune.py`
   - responsibility: `weft.state.*` runtime-only cleanup
   - boundary: no task-local or lifecycle-log deletion

2. New retention pruning:
   - owner: `weft/commands/retention_prune.py`
   - responsibility: build archive-backed candidates for task-local queues and
     lifecycle-log rows, then apply exact deletion when requested
   - boundary: no runtime-state pruning logic, no status authority, no Manager
     behavior

3. Thin CLI adapter:
   - owner: `weft/cli/app.py`
   - responsibility: parse options and dispatch to the correct command-layer
     family
   - boundary: no direct queue reads, no deletion logic, no evidence logic

Do not merge all prune logic into a single large file just because the CLI
command is one command. Runtime-state cleanup and task-local retention cleanup
have different safety rules, archive requirements, and rollback risk.

Why extend `weft system prune` instead of adding a second public command:

- `prune` is already the operator cleanup surface.
- Adding task-local retention as a family keeps the user-facing model simple.
- Keeping command-layer modules separate avoids a misleading shared
  abstraction over different risk classes.

Counterargument:

- A separate `weft system reap` command would make the destructive boundary
  visually obvious. The stronger approach is to keep `prune` but require
  explicit family/target flags for task-local and lifecycle-log cleanup,
  default to dry-run, and make the human/JSON output clearly identify
  destructive retention candidates. This preserves the existing CLI direction
  without hiding risk.

## 6. Target CLI Contract

Extend:

```bash
weft system prune
```

Existing runtime-state usage must continue to work:

```bash
weft system prune --queue tid-mappings --dry-run --json
weft system prune --queue all --apply --json
```

Add task-local/lifecycle retention options:

- `--family NAME`
  - Allowed values: `runtime-state`, `task-local`, `task-log`, `retention`,
    `all`.
  - Default: `runtime-state` for backward compatibility with Release 5.
  - `retention` means `task-local` plus `task-log`.
  - `all` means runtime-state plus retention families.
  - Unknown values are errors.
- `--task TID`
  - Repeatable optional filter for task-local retention candidates.
  - May accept full TID only in the first implementation. Short TID resolution
    is allowed only by reusing existing task command resolution helpers.
- `--retention-class NAME`
  - Repeatable optional candidate-class filter.
  - Allowed first-slice classes are listed in "Candidate Classes".
  - Default: all safe classes for the selected family.
- `--archive PATH`
  - Required for ordinary `--apply` when family includes `task-local` or
    `task-log`.
  - Optional when `--force` is set. In force mode, archive writing is
    best-effort unless the user supplies `--archive`; if a supplied archive
    path fails, the command should fail because the user explicitly requested
    that artifact.
  - Optional in dry-run. If omitted in dry-run, write only the report summary.
  - Path points to a JSONL file or a directory. If a directory is supplied,
    create `YYYY-MM-DD-retention-prune.jsonl` inside it.
- `--dry-run / --apply`
  - Default: `--dry-run`.
- Ordinary `--apply` must write archive records before deleting anything.
  Force `--apply` follows the force archive rules below.
- `--force`
  - Default: false.
  - Only meaningful with `--apply` and a retention family.
  - Enables aggressive cleanup for candidates that are report-only or
    protected in normal apply, including active/ambiguous task-local residue,
    inbox/reserved work, claimed outbox residue, malformed rows, unknown-shape
    rows, and messages younger than `--min-age`.
  - Overrides ordinary archive-before-delete, `--min-age`,
    malformed/unknown-shape protection, active/ambiguous task protection,
    claimed-result protection, and report-only candidate protection.
  - Does not override explicit `--dry-run`, selected `--family`/`--task`/
    `--retention-class` scope, exact-message deletion, or backend delete
    failures.
  - Must be rejected with a clear error if used without `--apply`.
- `--min-age SECONDS`
  - Default: 604800 seconds (7 days) for retention families.
  - Existing runtime-state default remains 3600 seconds unless the runtime
    path chooses to keep its own default.
  - Tests may use `--min-age 0`.
- `--keep-recent-per-task N`
  - Default: 1.
  - Keep at least the newest N lifecycle-log rows per TID when pruning
    `weft.log.tasks`.
  - Must be `>= 1`.
- `--limit N`
  - Limits candidate report/application count.
  - Apply mode applies only the first N candidates after rebuilding
    candidates.
- `--json`
  - Emit one summary object.
- `--report PATH`
  - Optional JSONL report path separate from the archive. The report may omit
    raw payload bodies; the archive must contain the selected archive contract.

Exit codes:

- `0`: scan completed successfully, including dry-run with candidates
- `1`: invalid arguments, archive write failure, broker read failure, or delete
  failure

`--force` is the explicit opt-in for aggressive cleanup. It widens the set of
eligible retention candidates and intentionally bypasses ordinary safety
protections. It must not become a hidden default, and it must not delete outside
the selected family/task/class scope. If the command can identify an exact
message ID inside that explicit scope, force may delete it even when normal
evidence is weak or unavailable.

## 7. Candidate Classes

Allowed first-slice task-local/lifecycle classes:

- `terminal_ctrl_out_archived`
  - Exact typed terminal `ctrl_out` envelope for a task already represented by
    retained terminal task-log evidence.
- `terminal_ctrl_out_without_log_reported`
  - Exact typed terminal `ctrl_out` envelope when no terminal task-log proof
    remains visible.
  - Report-only in the first implementation because deleting it would remove
    the only terminal proof visible to status/result readers.
- `terminal_result_outbox_archived`
  - Exact final one-shot outbox message for a task that also has retained
    terminal task-log or retained typed terminal `ctrl_out` proof.
  - Only for non-persistent, non-interactive tasks.
- `result_without_terminal_outbox_reported`
  - Exact final one-shot outbox message when shared evidence classifies the
    task as `result_without_terminal`.
  - Report-only in normal apply because deleting it removes the only terminal
    evidence visible to status/result readers.
  - `--force` may delete it after archive because force explicitly accepts
    that future status may rely on remaining log state plus the operator-held
    archive for historical explanation.
- `terminal_task_log_superseded`
  - Older `weft.log.tasks` row for a TID when at least
    `--keep-recent-per-task` newer rows for that TID remain and an archive
    record has been written.
- `nonterminal_task_log_superseded`
  - Older nonterminal `weft.log.tasks` row for a TID with later terminal proof,
    when `--keep-recent-per-task` newer rows remain and an archive record has
    been written.
- `obsolete_ctrl_in_control`
  - Old task-local `ctrl_in` control message for a terminal task, when the
    control outcome is already visible in terminal log or terminal `ctrl_out`
    evidence.
- `obsolete_inbox_work`
  - Unclaimed inbox work for a terminal task when that work can no longer be
    executed because terminal lifecycle proof exists.
  - Report-only in normal apply.
  - `--force` may delete it after archive when the task has retained terminal
    proof and no live owner proof.
- `obsolete_reserved_work`
  - Reserved work for a terminal task whose owner is not live and whose
    terminal evidence remains visible in retained task-log or typed terminal
    `ctrl_out` proof.
  - Report-only in normal apply.
  - `--force` may delete it after archive when the task has retained terminal
    proof and no live owner proof.
- `unsupported_task_local_shape`
  - Report-only in normal apply. Use this when a queue or payload shape cannot
    be safely interpreted.
  - `--force` may delete it when the row is inside the selected scope and has
    an exact message ID.
- `claimed_outbox_residue_force`
  - Claimed outbox residue that normal mode protects under [OBS.14].
  - Only emitted and applied when `--force` is set and the backend exposes an
    exact claimed message ID. The first implementation must not synthesize a
    fake ID for claimed rows.

The first implementation should actively delete only these classes:

- `terminal_ctrl_out_archived`
- `terminal_result_outbox_archived`
- `terminal_task_log_superseded`
- `nonterminal_task_log_superseded`
- `obsolete_ctrl_in_control`

The first implementation should report but not delete:

- `obsolete_inbox_work`
- `obsolete_reserved_work`
- `result_without_terminal_outbox_reported`
- `terminal_ctrl_out_without_log_reported`
- `unsupported_task_local_shape`

With `--force --apply`, the first implementation may actively delete:

- `obsolete_inbox_work`
- `obsolete_reserved_work`
- `result_without_terminal_outbox_reported`
- `terminal_ctrl_out_without_log_reported`
- `unsupported_task_local_shape`
- `claimed_outbox_residue_force`, only when exact claimed message IDs are
  available

Reason:

Inbox and reserved queues are recovery surfaces. They need a higher bar than
outbox/control/log residue because deleting them may destroy work intent. The
normal apply path keeps them visible. `--force` is the operator override for
cases where broker shrinkage is more important than keeping broker-local
recovery material. A force run should be loud in output, but it should not
refuse the human's explicit cleanup instruction merely because the normal
evidence model would be more cautious.

## 8. Archive Contract

Ordinary retention pruning writes a JSONL archive before deletion. Force mode
uses the archive rules below. The archive is not status truth and is not a
rollback database, but it must carry enough information to explain what was
deleted when it exists.

Archive candidate record:

```json
{
  "schema_version": 1,
  "record_type": "retention_prune_candidate",
  "run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "emitted_at": 1770000000000000000,
  "queue": "T1770000000000000001.ctrl_out",
  "message_id": 1770000000000000002,
  "tid": "1770000000000000001",
  "candidate_class": "terminal_ctrl_out_archived",
  "reason": "terminal_ctrl_out_has_archived_terminal_summary",
  "age_seconds": 900000.0,
  "dry_run": false,
  "applied": false,
  "error": null,
  "payload_sha256": "hex",
  "payload_size_bytes": 123,
  "payload": {"type": "terminal", "tid": "1770000000000000001"}
}
```

Archive summary record:

```json
{
  "schema_version": 1,
  "record_type": "retention_prune_completed",
  "run_id": "2026-05-07T12:00:00.000000Z:pid-12345",
  "dry_run": false,
  "queues_scanned": ["weft.log.tasks", "T1770000000000000001.ctrl_out"],
  "records_scanned": 50,
  "candidates": 4,
  "archived": 4,
  "deleted": 4,
  "failed": 0,
  "candidate_class_counts": {
    "terminal_ctrl_out_archived": 1,
    "terminal_task_log_superseded": 3
  }
}
```

Payload handling:

- Archive records should include the full raw payload when it is JSON and
  smaller than a configured limit.
- For non-JSON or large payloads, include `payload_text` if safe and small, or
  include only `payload_sha256`, `payload_size_bytes`, and
  `payload_excerpt`.
- Do not archive spilled output file contents in the first implementation.
  Archive only the outbox reference payload and file metadata. Spilled-file
  cleanup is not part of this release unless a separate task proves safe
  behavior.
- Do not write archive records after deletion in ordinary apply. Write archive
  records first, flush them, then delete. A crash after archive but before
  delete is safe; the next run may re-archive the same candidate unless
  deduplication is later added.
- In force mode, archive writing is best-effort unless the user supplied
  `--archive`. The command should still include enough stdout/stderr/report
  detail to make the force deletion visible to the operator.

Report contract:

- `--report` may use the same record shape as the archive but may omit full
  payload bodies.
- If both `--archive` and `--report` are supplied, the archive is the required
  pre-delete safety artifact in ordinary apply; the report is operator
  convenience. In force mode, a supplied archive path remains required because
  the human explicitly requested it.

## 9. Safety Rules

Common rules:

- Use generator-based history reads such as `iter_queue_entries()` and
  `iter_queue_json_entries()`.
- Build candidates with exact `message_id` values from broker timestamps.
- In ordinary apply, never delete a row newer than `--min-age`.
- In ordinary apply, never delete malformed or unknown-shape rows.
- Rebuild candidates immediately before apply.
- In ordinary apply, write and flush archive records before deleting.
- Delete exact message IDs only. If exact deletion is unsupported for a
  backend/queue, report the candidate and fail apply for that candidate.
- `--force` must not delete anything outside the selected command scope. It may
  promote report-only/protected rows into force deletion candidates.
- Cleanup failure must not rewrite lifecycle truth.
- Status/result/task commands must not read retention checkpoints or reports.
- Runtime-state pruning must keep working if retention pruning is disabled or
  fails.

Task safety rules:

- In ordinary apply, protect any TID whose shared evidence is active,
  ambiguous, persistent, interactive, streaming, or named-endpoint live.
- In ordinary apply, protect any task whose latest public status is nonterminal
  unless the class is purely superseded old log rows and newer retained rows
  are enough for status reconstruction.
- In ordinary apply, protect all task-local queues for tasks without terminal
  evidence.
- In ordinary apply, protect `T{tid}.reserved` and `T{tid}.inbox`.
- In ordinary apply, protect claimed outbox residue. [OBS.14] says claimed
  outbox residue is recovery evidence, not readable result evidence.
- In force apply, these protections become warnings/report fields, not
  blockers, as long as the message is inside the selected scope and has an
  exact message ID.
- Protect pipeline queues in the first implementation unless explicit pipeline
  tests prove safety. `--force` may still delete pipeline-shaped queues only if
  the selected family explicitly includes them in a later implementation; do
  not accidentally include `P{tid}.*` in generic `T{tid}.*` logic.

`weft.log.tasks` safety:

- Keep at least `--keep-recent-per-task` newest rows per TID.
- In ordinary apply, do not delete the newest terminal row for a TID in the
  first implementation.
- In ordinary apply, do not delete rows for a TID if removing them would make
  status reconstruction ambiguous in local tests.
- In ordinary apply, do not delete manager task rows for the active manager.
- In ordinary apply, do not delete task-log rows for tasks younger than
  `--min-age`. In force apply, `--min-age` is advisory and should appear in
  the report as an overridden protection when violated.

`T{tid}.outbox` safety:

- In ordinary apply, actively delete only exact final one-shot outbox messages
  for tasks that have independent retained terminal task-log or typed terminal
  `ctrl_out` proof.
- In ordinary apply, do not delete streaming chunks, partial stream buffers,
  persistent task outputs, interactive task outputs, pipeline outboxes, or
  claimed outbox residue.
- In ordinary apply, do not delete `result_without_terminal` outbox messages.
  That outbox is the only visible terminal proof, and status must not become
  dependent on retention archives.
- `--force` may delete `result_without_terminal` outbox messages after archive
  when the operator explicitly accepts that aggressive cleanup tradeoff.
- Do not delete outbox messages just because the task log is terminal and the
  task is old; require archive and explicit candidate class.

`T{tid}.ctrl_out` safety:

- In ordinary apply, actively delete only typed terminal envelopes returned by
  `peek_terminal_ctrl_out_evidence()` after archive when retained terminal
  task-log proof also exists.
- In ordinary apply, do not delete typed terminal `ctrl_out` envelopes when
  they are the only visible terminal proof. Report them as
  `terminal_ctrl_out_without_log_reported`.
- `--force` may delete `terminal_ctrl_out_without_log_reported` after archive
  when the operator explicitly accepts that aggressive cleanup tradeoff.
- In ordinary apply, do not delete PONG, STATUS, stream, stderr, malformed, or
  unknown control messages.
- In ordinary apply, do not delete terminal `ctrl_out` for tasks younger than
  `--min-age`.

`T{tid}.ctrl_in` safety:

- In ordinary apply, actively delete old control messages only when the task is
  terminal and the corresponding terminal evidence remains visible in retained
  task-log or retained typed terminal `ctrl_out` proof.
- In ordinary apply, do not delete `PING`/`STATUS` messages for active tasks.
- In ordinary apply, do not delete malformed messages.

`T{tid}.inbox` and `T{tid}.reserved` safety:

- Report-only in normal apply.
- Candidate classification may exist so ops can inspect volume, but apply mode
  must not delete these rows unless `--force` is set.
- With `--force`, delete exact message IDs in these queues even if terminal or
  live-owner proof is missing or ambiguous. The report must call out which
  normal protections were overridden.

## 10. Files To Touch

Implementation files:

- `weft/commands/retention_prune.py`
  - New command-layer module.
  - Own dataclasses, candidate builders, archive writer, dry-run/apply logic,
    and summary rendering for retention families.
  - Must not import CLI.
- `weft/commands/runtime_prune.py`
  - Keep existing runtime-state behavior.
  - Touch only to share command result conventions if necessary. Do not add
    task-local rules here.
- `weft/cli/app.py`
  - Extend `weft system prune` wrapper with retention options.
  - Keep wrapper thin.
- `weft/_constants.py`
  - Add retention-prune schema version, defaults, candidate class strings,
    archive path constants, and allowed family names.
- `weft/commands/task_evidence.py`
  - Touch only for narrow helper extraction if candidate builders need shared
    safe evidence reads. Do not add deletion here.
- `weft/commands/lifecycle_monitor.py`
  - Touch only to reuse archive path/sink helpers if it avoids duplication.
    Do not make lifecycle monitor destructive in this release.
- `weft/commands/__init__.py`
  - Export a command function only if package convention requires it.

Files to avoid unless a failing test proves otherwise:

- `weft/core/manager.py`
  - Manager must not own retention pruning.
- `weft/core/tasks/base.py`
  - Do not change task write paths for retention cleanup.
- `weft/core/tasks/consumer.py`
  - Do not change result publication or reservation semantics for cleanup.
- `weft/commands/status.py`
  - Status must not depend on retention reports/checkpoints.
- `weft/commands/result.py`
  - Do not change normal result consumption unless a regression test proves
    retention cleanup exposed an existing bug.
- `weft/commands/queue.py`
  - Do not implement retention cleanup as a queue passthrough shortcut.

Test files:

- `tests/commands/test_retention_prune.py`
  - Main command-layer behavior tests with real broker queues.
- `tests/cli/test_cli_system.py`
  - Thin CLI coverage for new options and backward compatibility.
- Existing neighboring tests to run:
  - `tests/commands/test_runtime_prune.py`
  - `tests/commands/test_task_commands.py`
  - `tests/commands/test_status.py`
  - `tests/commands/test_result.py`
  - `tests/commands/test_queue.py`
  - `tests/core/test_manager.py`
  - `tests/tasks/test_task_execution.py` if result/outbox cleanup behavior is
    touched

Docs:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/README.md`
- `docs/lessons.md` only if implementation exposes a repeated durable mistake

## 11. Read Before Editing

Required read order:

1. `AGENTS.md`
2. `docs/agent-context/README.md`
3. `docs/agent-context/decision-hierarchy.md`
4. `docs/agent-context/principles.md`
5. `docs/agent-context/engineering-principles.md`
6. `docs/agent-context/runbooks/runtime-and-context-patterns.md`
7. `docs/agent-context/runbooks/testing-patterns.md`
8. `docs/agent-context/runbooks/hardening-plans.md`
9. `docs/plans/2026-05-06-lifecycle-reconciliation-architecture-plan.md`
10. `docs/plans/2026-05-07-lifecycle-monitor-archive-sink-plan.md`
11. `docs/plans/2026-05-07-runtime-state-pruning-plan.md`
12. `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3],
    [MF-5], "Cleanup Boundary", "Queue Management Patterns"
13. `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.14],
    [OBS.16]
14. `docs/specifications/10-CLI_Interface.md` [CLI-6]

Code to read:

- `weft/commands/runtime_prune.py`
- `weft/commands/lifecycle_monitor.py`
- `weft/commands/task_evidence.py`
- `weft/commands/tasks.py` `task_terminal_snapshot()` and
  `ack_terminal_snapshot()`
- `weft/commands/result.py` result materialization and claimed-result paths
- `weft/helpers/__init__.py` `iter_queue_entries()` and
  `iter_queue_json_entries()`
- `weft/context.py` `build_context()` and `WeftContext.queue()`
- `weft/core/tasks/base.py` queue wiring and reserved policy
- `weft/core/tasks/consumer.py` work execution and outbox publication
- `tests/commands/test_runtime_prune.py`
- `tests/commands/test_task_commands.py`
- `tests/commands/test_result.py`
- `tests/helpers/weft_harness.py`

Comprehension checks before coding:

- Which queue proves task lifecycle truth today? Answer: `weft.log.tasks`,
  with shared task-local evidence used for reconciliation when log proof is
  missing.
- May ordinary retention pruning delete task-local inbox/reserved work?
  Answer: no, normal apply reports those candidates only.
- May `--force --apply` delete task-local inbox/reserved work? Answer: yes,
  when the messages are inside the selected scope and have exact message IDs.
- May ordinary retention pruning delete claimed outbox residue? Answer: no,
  [OBS.14] protects it as recovery evidence.
- May `--force --apply` delete claimed outbox residue? Answer: yes, if the
  implementation has an exact claimed message ID, emits an explicit force
  candidate class, and the row is inside the selected scope. If exact claimed
  IDs are unavailable, do not synthesize a deletion target.
- May dry-run and apply use different candidate logic? Answer: no.
- May archive records be written after deletion? Answer: no in ordinary apply.
  In force apply, archive is best-effort unless the human supplied
  `--archive`.
- May status depend on retention archive files or checkpoints? Answer: no.
- Should tests mock `Queue.delete()` and call the feature done? Answer: no.
  Use real broker queues and assert exact message presence/absence.

## 12. Bite-Sized Implementation Tasks

### Task 0: Baseline And Scope Guard

Actions:

1. Source the repo environment:

   ```bash
   . ./.envrc
   ```

2. Inspect the worktree:

   ```bash
   git status --short
   ```

3. Run baseline neighboring tests:

   ```bash
   ./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/commands/test_task_commands.py tests/commands/test_result.py tests/commands/test_status.py -q
   ```

Expected result:

- Baseline is known.
- Existing `weft system prune` runtime-state tests pass before edits.

Stop and re-plan if:

- The worktree contains unrelated dirty files that overlap the planned files
  and cannot be separated safely.
- The existing runtime prune behavior is already failing.

### Task 1: Red Tests For CLI Shape And Backward Compatibility

Files to touch:

- `tests/cli/test_cli_system.py`

Tests:

- Existing `weft system prune --queue tid-mappings --dry-run --json` still
  behaves as runtime-state pruning.
- `weft system prune --family task-local --dry-run --json --min-age 0` exits
  `0` and emits a retention summary.
- `weft system prune --family task-local --apply --min-age 0` without
  `--archive` exits `1`.
- `weft system prune --family task-local --apply --force --min-age 0` without
  `--archive` is allowed and emits a clear force-mode warning/summary.
- `weft system prune --family task-local --force --min-age 0` without
  `--apply` exits `1`.
- `weft system prune --family nonsense` exits `1` with a clear error.
- `weft system prune --family task-local --keep-recent-per-task 0` exits `1`.

Expected red failure:

- CLI does not yet accept retention family options.

Do not:

- Test only Typer parsing without exercising the command-layer function.

### Task 2: Core Dataclasses And Validation

Files to touch:

- `weft/commands/retention_prune.py`
- `weft/_constants.py`

Implementation:

- Add dataclasses:
  - `RetentionPruneConfig`
  - `RetentionPruneCandidate`
  - `RetentionPruneResult`
  - `RetentionQueueScanStats`
- Add constants for:
  - schema version
  - default min age
  - default keep count
  - candidate classes
  - report-only candidate classes
  - default archive subdir, for example `archive/retention-prune`
- Implement config validation:
  - `min_age_seconds >= 0`
  - `keep_recent_per_task >= 1`
  - `limit is None or limit >= 1`
  - ordinary apply with retention family requires archive path
  - force apply with retention family does not require archive path
  - force without apply is rejected
  - unknown family/class filters are rejected

Tests:

- Add command-layer validation tests in `tests/commands/test_retention_prune.py`.

Run:

```bash
./.venv/bin/python -m pytest tests/commands/test_retention_prune.py tests/cli/test_cli_system.py -q
```

### Task 3: Archive Writer Before Delete

Files to touch:

- `weft/commands/retention_prune.py`

Implementation:

- Implement JSONL archive path resolution.
- Implement candidate archive serialization with payload hash and size.
- Implement atomic-enough write behavior:
  - create parent directories
  - append records
  - flush before delete
- Implement a test hook or internal ordering that proves archive write happens
  before delete without mocking broker queues.

Tests:

- Dry-run with archive writes candidate records and summary, deletes nothing.
- Ordinary apply writes archive before deletion.
- Simulated archive write failure in ordinary apply causes exit `1` and
  deletes nothing. Use an invalid archive path or permission-style fixture
  rather than mocking `Queue.delete()`.
- Simulated archive write failure in force apply without a user-supplied
  `--archive` records a warning and still applies exact selected candidates.
- Simulated archive write failure in force apply with a user-supplied
  `--archive` exits `1`, because the human explicitly requested that artifact.

Stop and re-plan if:

- Implementation wants to delete first and archive later.
- Implementation wants a hidden checkpoint database for archive state.

### Task 4: Task-Log Candidate Builder

Files to touch:

- `weft/commands/retention_prune.py`

Implementation:

- Read `weft.log.tasks` with `iter_queue_json_entries()`.
- Group by `tid`.
- Keep newest `keep_recent_per_task` rows per TID.
- Candidate older rows only when:
  - row is older than `min_age`
  - row is not protected by keep count
  - later terminal proof for that TID exists, or the row itself is an older
    terminal row and a newer retained terminal/log row remains
- Do not candidate the newest terminal row in the first implementation.
- Do not candidate active manager rows.

Tests:

- Older nonterminal task-log rows for a terminal task are candidates.
- Newest row per TID is protected in ordinary apply.
- Newest terminal row is protected in ordinary apply.
- Rows younger than min age are protected in ordinary apply.
- Rows for active/nonterminal tasks are protected in ordinary apply.
- Force apply may delete otherwise protected rows inside the selected task-log
  scope and records overridden protections in the report.
- Apply deletes exact older log rows only and leaves protected rows.

Do not:

- Use `peek_many(limit=...)`.
- Infer terminal state from status strings without using shared evidence
  helpers where possible.

### Task 5: Terminal `ctrl_out` Candidate Builder

Files to touch:

- `weft/commands/retention_prune.py`
- `weft/commands/task_evidence.py` only if a narrow helper is needed

Implementation:

- Discover candidate TIDs from task-log reduction and optional `--task`
  filters.
- Resolve `ctrl_out` with `queue_names_for_tid()` /
  `control_queue_names_for_tid()`.
- Use `peek_terminal_ctrl_out_evidence()` to identify exact typed terminal
  envelope ack targets.
- Candidate terminal envelopes older than `min_age` as
  `terminal_ctrl_out_archived` only when retained terminal task-log proof exists
  for the task.
- Candidate terminal envelopes without retained terminal task-log proof as
  `terminal_ctrl_out_without_log_reported` with `report_only=True`.
- Do not candidate ordinary PONG/STATUS replies, malformed messages, or stream
  chunks.
- In force mode, ordinary PONG/STATUS/malformed/unknown messages may become
  force candidates if they are inside the selected `ctrl_out` queue scope and
  have exact message IDs. The candidate reason must say normal interpretation
  failed or was overridden.

Tests:

- Terminal envelope with retained terminal task-log proof becomes
  `terminal_ctrl_out_archived`.
- Terminal envelope without retained terminal task-log proof becomes
  `terminal_ctrl_out_without_log_reported` and is not deleted by apply.
- PONG and STATUS replies in the same queue are not candidates.
- Force apply may delete terminal-without-log, PONG/STATUS, malformed, and
  unknown `ctrl_out` messages in selected scope and records overridden
  protections.
- Apply deletes only the terminal envelope message ID.

### Task 6: Final One-Shot Outbox Candidate Builder

Files to touch:

- `weft/commands/retention_prune.py`

Implementation:

- Use `peek_final_outbox_evidence()` for non-persistent, non-interactive tasks.
- Candidate exact ack targets as `terminal_result_outbox_archived` only when
  the task also has retained terminal task-log or retained typed terminal
  `ctrl_out` proof.
- Candidate `result_without_terminal` evidence as
  `result_without_terminal_outbox_reported` with `report_only=True`.
- Candidate claimed outbox residue as `claimed_outbox_residue_force` only in
  force mode and only when an exact claimed message ID is available.
- Archive the decoded public value or safe payload excerpt for both active and
  report-only candidates when an archive is supplied.
- In ordinary mode, do not candidate claimed result residue.
- In ordinary mode, do not candidate persistent, interactive, streaming, or
  pipeline outputs.
- In force mode, persistent, interactive, streaming, and claimed outbox rows may
  become force candidates if selected explicitly by family/task/class scope.
  Pipeline outputs remain out of scope unless a later implementation adds an
  explicit pipeline family.

Tests:

- One final one-shot outbox message with independent terminal log proof becomes
  `terminal_result_outbox_archived`.
- One final one-shot outbox message without terminal log/control proof becomes
  `result_without_terminal_outbox_reported` and is not deleted by apply.
- Persistent task outbox is protected in ordinary apply.
- Interactive task outbox is protected in ordinary apply.
- Claimed outbox residue is protected and reported as ineligible in ordinary
  apply if practical.
- Force apply deletes selected result-without-terminal candidates and any
  exact-ID claimed outbox candidates by exact message ID, then records
  overridden protections.
- Apply deletes only the exact final outbox message.

Stop and re-plan if:

- The implementation needs to consume result output to classify it. This must
  remain peek-based until apply deletes the exact selected message.

### Task 7: `ctrl_in` Candidate Builder

Files to touch:

- `weft/commands/retention_prune.py`

Implementation:

- Resolve `ctrl_in` for terminal tasks.
- In ordinary mode, candidate old control messages only when task terminal
  evidence exists and retained/archive-backed proof will remain visible after
  deletion.
- In ordinary apply, allow only recognizable raw control commands or structured
  command envelopes. Malformed messages are protected.
- In force apply, malformed or unknown `ctrl_in` messages may become force
  candidates if they are inside the selected scope and have exact message IDs.
- Classify as `obsolete_ctrl_in_control`.

Tests:

- Old STOP/KILL control message for terminal task is candidate.
- PING for active task is protected in ordinary apply.
- Malformed control payload is protected in ordinary apply.
- Force apply may delete active-task PING/STATUS and malformed control payloads
  in selected scope and records overridden protections.
- Apply deletes exact eligible control message only.

### Task 8: Inbox And Reserved Report-Only Candidates

Files to touch:

- `weft/commands/retention_prune.py`

Implementation:

- For terminal tasks, inspect `T{tid}.inbox` and `T{tid}.reserved` with
  generator reads.
- Candidate old messages as report-only classes:
  - `obsolete_inbox_work`
  - `obsolete_reserved_work`
- Set `report_only=True` in ordinary apply; ordinary apply must not delete
  them.
- In force apply, promote selected inbox/reserved candidates to active delete
  candidates.
- Include queue, message ID, TID, age, and payload hash/excerpt in report and
  archive if an archive is supplied.

Tests:

- Dry-run reports old inbox/reserved work for terminal tasks.
- Ordinary apply does not delete inbox/reserved report-only candidates.
- Active task inbox/reserved work is protected in ordinary apply.
- Force apply deletes selected inbox/reserved candidates by exact message ID,
  including active or ambiguous work, and records overridden protections.

### Task 9: CLI Integration

Files to touch:

- `weft/cli/app.py`
- `weft/commands/runtime_prune.py`
- `weft/commands/retention_prune.py`

Implementation:

- Keep existing runtime-state options working.
- Add `--family`, `--task`, `--retention-class`,
  `--keep-recent-per-task`, and `--archive`.
- Route `--family runtime-state` to existing runtime prune.
- Route retention families to `retention_prune.py`.
- For `--family all`, run runtime-state prune and retention prune as separate
  command-layer calls and merge summaries only at the CLI output/report layer.
- If runtime-state succeeds and retention fails, exit `1` and report both
  outcomes. Do not hide partial success.

Tests:

- CLI dry-run JSON includes family-level summaries.
- Existing runtime-state tests still pass unchanged.
- Apply without archive for retention family fails before deletion.
- Apply with force and no archive succeeds when candidate deletion succeeds and
  emits force-mode warnings in JSON/human output.
- `--family all` dry-run reports both runtime and retention sections.

Stop and re-plan if:

- The CLI wrapper starts reading queues directly.
- Runtime-state and retention candidate builders start sharing unsafe
  abstractions just to reduce line count.

### Task 10: Specs And Documentation

Files to touch:

- `docs/specifications/00-Quick_Reference.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md`
- `docs/specifications/10-CLI_Interface.md`
- `docs/plans/2026-05-07-task-local-reaper-retention-policy-plan.md`
- `docs/plans/README.md`

Implementation:

- Update specs with the final CLI contract, archive path, deletion safety
  rules, and implementation mappings.
- Add backlinks to this plan.
- Mark this plan `completed` only after implementation and verification pass.
- Keep README plan status synchronized.

Run:

```bash
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
```

### Task 11: Verification Gates

Run focused gates:

```bash
./.venv/bin/python -m pytest tests/commands/test_retention_prune.py -q
./.venv/bin/python -m pytest tests/commands/test_runtime_prune.py tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest tests/commands/test_result.py tests/commands/test_task_commands.py tests/commands/test_status.py -q
./.venv/bin/python -m pytest tests/commands/test_queue.py tests/core/test_manager.py -q
./.venv/bin/ruff check weft tests/commands/test_retention_prune.py tests/cli/test_cli_system.py
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
```

Run broader gates before release:

```bash
./.venv/bin/python -m pytest -q
./.venv/bin/ruff format --check weft tests extensions integrations
```

If Postgres-backed cleanup behavior is touched or candidate deletion uses
backend-specific paths, run the Postgres suite:

```bash
./.venv/bin/python bin/pytest-pg --all
```

## 13. Test Design Rules

Use real broker-backed queues.

Preferred fixtures:

- `broker_env` for direct queue behavior
- `WeftTestHarness` for end-to-end task lifecycle and CLI behavior
- `build_context()` with `prepare_project_root()` for command-layer tests

Do not mock:

- `simplebroker.Queue`
- `Queue.delete()`
- queue generator iteration
- reservation semantics
- task-log replay
- outbox parsing

Mock only:

- filesystem archive failure when no real path fixture can express it
- time source if deterministic age tests cannot use `--min-age 0`

Important test invariants:

- Dry-run deletes nothing.
- Apply rebuilds candidates before deletion.
- In ordinary apply, archive records exist before deletion.
- In force apply without a supplied archive, archive failure does not block
  deletion, but the force-mode warning is visible in output.
- Report-only candidates are never deleted in ordinary apply.
- Force-enabled candidates are deleted in force apply.
- Protected queue messages remain readable after ordinary apply.
- Force apply may delete protected queue messages inside selected scope.
- Existing `weft result` and `weft task status` behavior remains coherent
  after cleanup.
- Existing runtime-state prune behavior is unchanged.

Anti-patterns:

- A test that asserts only candidate count without checking exact message IDs.
- A mock-only test that never writes a real queue message.
- A cleanup test that deletes inbox/reserved work without `--force`.
- A test that uses `time.sleep()` as the proof of age when `--min-age 0` would
  be deterministic.

## 14. Ops Validation

After release and deploy:

1. Load ops environment:

   ```bash
   cd ~/governance
   . ./.envrc
   ```

2. Capture baseline status and queue counts:

   ```bash
   /opt/venv/bin/weft status --json > /tmp/weft-status-before-retention-prune.json
   /opt/venv/bin/weft queue list --json > /tmp/weft-queues-before-retention-prune.json
   ```

3. Dry-run retention pruning only:

   ```bash
   /opt/venv/bin/weft system prune --family retention --dry-run --json --report /tmp/weft-retention-prune-dry-run.jsonl > /tmp/weft-retention-prune-dry-run.json
   ```

4. Inspect candidate classes:

   - Confirm no active manager queues are candidates.
   - Confirm no active or ambiguous tasks are delete-eligible in ordinary
     dry-run.
   - Confirm inbox/reserved candidates are report-only in ordinary dry-run.
   - Confirm force-enabled candidates are clearly marked if `--force` is used
     in a separate dry-run.
   - Confirm candidate counts match expectations before applying.

5. Apply a narrow safe class:

   ```bash
   /opt/venv/bin/weft system prune --family task-local --retention-class terminal_ctrl_out_archived --apply --archive /tmp/weft-retention-archive.jsonl --json > /tmp/weft-retention-apply-ctrl-out.json
   ```

6. Capture post-run status and queue counts:

   ```bash
   /opt/venv/bin/weft status --json > /tmp/weft-status-after-retention-prune.json
   /opt/venv/bin/weft queue list --json > /tmp/weft-queues-after-retention-prune.json
   ```

7. Confirm:

   - `weft status --json` remains coherent.
   - Active manager remains visible.
   - Spawn queue is not falsely reported as backed up.
   - Broker message count drops only in expected queue families.
   - Archive file exists and includes candidate and summary records.

Do not apply broad retention cleanup on ops until a human reviews dry-run
candidate classes. Release 7 will provide the full ops runbook.

## 15. Rollout And Rollback

Rollout:

1. Ship implementation with dry-run default.
2. Run local and release tests.
3. Deploy to ops.
4. Run dry-run retention pruning.
5. Apply one narrow safe class.
6. Review before broadening.

Rollback:

- Package rollback stops future pruning.
- Deleted broker messages cannot be restored automatically from Weft. Archive
  records explain what was deleted but are not a broker replay system.
- A crash after archive before ordinary delete is safe; rerun may re-archive
  the same candidate.
- A crash after partial delete is acceptable only because each message had an
  archive record first and deletion was exact-message based. In force mode
  without archive, the human accepted that weaker rollback posture.

One-way doors:

- Deleting `weft.log.tasks`.
- Deleting task outbox messages.
- Deleting control messages.
- Force deletion from inbox/reserved.

Ordinary apply requires dry-run proof, archive proof, and independent review.
Force apply requires explicit human opt-in and must make overridden protections
visible in output.

## 16. Independent Review Checklist

Before implementation starts, ask an independent reviewer:

- Does the plan clearly separate runtime-state pruning from retention pruning?
- Are inbox and reserved queues protected in ordinary apply and force-enabled
  only by explicit human override?
- Are claimed outbox residues protected under [OBS.14] in ordinary apply and
  force-enabled only through an explicit force class?
- Is archive-before-delete enforceable in ordinary apply, and is force-mode
  archive weakening explicit?
- Are dry-run/apply paths guaranteed to use the same candidate builder?
- Are exact message IDs required everywhere deletion happens?
- Are generator reads required for append-only histories?
- Does any task make status depend on retention archives or checkpoints?
- Does the CLI extension preserve Release 5 behavior?
- Would a zero-context engineer know which files to edit and which files to
  avoid?

## 17. Fresh-Eyes Review Of This Plan

Review pass 1 found a scope trap: Release 6 could be misread as permission to
delete all task-local queues for terminal tasks. The plan now separates normal
apply from force apply: ordinary cleanup keeps `inbox` and `reserved`
report-only, while `--force --apply` is a human override that may delete exact
selected messages and must report the overridden protections.

Review pass 2 found a CLI trap: `weft system prune` already exists for
runtime-only queues. The plan now requires `--family runtime-state` to preserve
existing behavior and routes retention cleanup through a separate
`retention_prune.py` module instead of mixing safety models.

Review pass 3 found an archive ambiguity: "summarize before deletion" could
lead to a summary-only archive that cannot explain exact deleted messages. The
plan now requires one archive candidate record per exact message ID before
ordinary deletion, with payload hash/size and safe payload or excerpt. Force
mode may proceed without archive only when the human did not request an archive
path, and output must make that weaker rollback posture explicit.

Review pass 4 found a result-safety issue: deleting an outbox that is the only
terminal proof would make status depend on retention archives. The plan now
allows active outbox deletion only when independent terminal task-log or typed
terminal `ctrl_out` proof remains. `result_without_terminal` outbox evidence
is report-only in ordinary apply; `--force --apply` may delete it as an
explicit human override.

Review pass 5 found a spec drift risk: current specs say no built-in
age-based sweeper exists. The plan now states the required spec deltas and
keeps the plan in `draft` until implementation lands.

This plan still matches the meta-plan direction. It does not move toward a
different architecture, because it keeps cleanup explicit, foreground,
exact-message based, and independent from Manager/status correctness. The force
path intentionally permits more aggressive deletion because the human asked for
it; it does not create an automatic reaper or a second lifecycle authority.
