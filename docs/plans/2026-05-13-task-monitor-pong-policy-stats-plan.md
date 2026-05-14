# Task Monitor PONG Policy Stats Plan

Status: completed
Source specs: docs/specifications/00-Quick_Reference.md; docs/specifications/05-Message_Flow_and_State.md [MF-5]; docs/specifications/07-System_Invariants.md [OBS.13], [OBS.16], [OBS.17]
Superseded by: none

## 1. Goal

Make TaskMonitor PONG diagnostics prove which cleanup policies ran during the
last cleanup cycle, without doing any live queue scanning or computation while
answering PING. The monitor should compute small policy-level stats during the
cleanup cycle, cache them on the live `TaskMonitorTask`, and return that cached
summary in both the existing top-level PONG fields and the extended
`task_monitor.last_cycle` block.

The current PONG proves liveness and queue-level movement. It does not prove
policy coverage. An operator can see `weft.log.tasks deleted=14`, but cannot
tell whether malformed cleanup ran, whether completed lifecycle collation ran,
whether terminal-without-start cleanup ran, or whether broad older-than cleanup
ran and selected nothing. This plan fixes that observability gap. It does not
change which rows are eligible for deletion.

## 2. Source Documents

- `docs/specifications/00-Quick_Reference.md`: queue names and
  `WEFT_TASK_MONITOR_*` operational knobs. Update only if the PONG field list
  or operator-facing diagnostic examples are documented there.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5]: TaskMonitor
  cleanup policy order, exact-message delete boundary, and runtime evidence
  semantics. This is the primary behavior source.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.16],
  [OBS.17]: operational output, exact-message deletion, and runtime
  observability invariants. Update the monitoring section so it states that
  PONG policy stats are cached from the most recent cycle, not recomputed on
  PING.
- `docs/plans/2026-05-12-bounded-task-monitor-cleanup-policy-plan.md`:
  completed prior plan that introduced bounded FIFO cleanup. Its cleanup
  behavior remains intended.
- `docs/plans/2026-05-12-task-monitor-cleanup-composition-refactor-plan.md`:
  completed prior plan that separated TaskMonitor orchestration, reusable
  pruning policy functions, queue-window primitives, and task-log collation.
  This plan should preserve those ownership boundaries.
- `docs/plans/2026-05-07-extended-ping-pong-state-probe-plan.md`:
  historical plan for adding extended PING/PONG state. This plan extends that
  additive diagnostics surface, but should not change the base control
  protocol.
- `docs/agent-context/runbooks/writing-plans.md`: zero-context plan standard.
- `docs/agent-context/runbooks/hardening-plans.md`: required because this
  touches runtime cleanup diagnostics and the PONG payload contract.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: use before
  merging implementation. This slice is additive, but it changes
  operator-facing diagnostics for destructive cleanup.

## 3. Current Context And Key Files

Current flow:

```text
PING arrives on T{monitor_tid}.ctrl_in
  -> BaseTask._handle_control_command()
  -> BaseTask._control_response_extras()
  -> TaskMonitorTask._control_snapshot_fields()
  -> TaskMonitorTask._task_monitor_pong_extension()
  -> write PONG to T{monitor_tid}.ctrl_out
```

Current cleanup cycle:

```text
TaskMonitorTask._run_monitor_cycle()
  -> TaskMonitorTask._run_task_monitor_cleanup_cycle()
  -> run_task_monitor_cleanup()
  -> _task_log_candidates() / _tid_mapping_candidates()
  -> apply_exact_prune_candidates()
  -> cache only queue_stats_summary() on TaskMonitorTask
```

Important files to modify:

- `weft/core/pruning/models.py`
  - Add a small cleanup policy stats model and, if needed, first-class policy
    identity on `CleanupCandidate`.
  - Keep this module free of queue scanning, deletion, and TaskMonitor
    scheduling.
- `weft/core/pruning/policies/malformed.py`
  - Thread policy identity through malformed candidate construction.
- `weft/core/pruning/policies/older_than.py`
  - Thread policy identity through older-than candidate construction.
- `weft/core/tasks/task_monitor_cleanup.py`
  - Compute policy stats during the cleanup cycle.
  - Return policy stats on `TaskMonitorCleanupResult`.
  - Keep policy execution deterministic and first-claim-wins.
- `weft/core/tasks/task_monitor.py`
  - Cache policy stats after cleanup.
  - Expose cached policy stats in PONG without queue reads.
- `tests/core/test_task_monitor_cleanup.py`
  - Add direct cleanup-runner tests for policy stats.
- `tests/tasks/test_task_monitor.py`
  - Add task-level PONG tests proving cached policy stats appear and PING does
    not trigger a cleanup scan.
- `docs/specifications/05-Message_Flow_and_State.md`
  - Add this plan backlink and, during implementation, update the TaskMonitor
    cleanup paragraph if field names or policy stat semantics differ.
- `docs/specifications/07-System_Invariants.md`
  - Add this plan backlink and, during implementation, update the monitoring
    section to name cached policy stats.
- `docs/plans/README.md`
  - Add this plan and keep the count/status metadata in sync.

Files to read before editing:

- `weft/core/tasks/base.py` `_control_response_extras()`,
  `register_pong_extension_provider()`, and `_pong_extension_fields()`.
  These own the additive PONG extension boundary.
- `weft/core/tasks/task_monitor.py` `_control_snapshot_fields()`,
  `_task_monitor_pong_extension()`, `_run_monitor_cycle()`, and
  `_run_task_monitor_cleanup_cycle()`.
- `weft/core/tasks/task_monitor_cleanup.py` `_tid_mapping_candidates()`,
  `_task_log_candidates()`, `_terminal_reserved_candidates()`, and
  `_with_apply_counts()`.
- `weft/core/task_log_collation.py` `collate_next_task_log_group()` and
  `CollatedMessageGroup`.
- `weft/core/pruning/apply.py` `apply_exact_prune_candidates()`.
- `tests/tasks/test_task_monitor.py` existing PONG extension assertions.
- `tests/core/test_task_monitor_cleanup.py` existing policy-order tests.

Comprehension checks before editing:

- Can you explain why PONG must not call `run_task_monitor_cleanup()` or scan a
  queue?
- Can you list the task-log cleanup policy order from [MF-5] without looking at
  this plan?
- Can you point to the one helper that actually deletes exact broker message
  IDs?
- Can you explain why `last_cleanup_queue_stats` is not enough to prove policy
  coverage?

## 4. Invariants And Constraints

- PONG must remain lightweight. It may serialize cached in-memory state and
  compute monotonic time deltas, but it must not open queues, scan queues,
  decode queue rows, call cleanup policy functions, or delete/report
  candidates.
- This is an additive diagnostics change. Keep existing top-level PONG fields:
  `last_prune_records_scanned`, `last_cleanup_queue_stats`, `last_processed`,
  `last_deleted`, `last_reported`, `last_error`, `last_warnings`, and
  `last_errors`.
- Keep `last_cleanup_queue_stats` for compatibility. Add policy stats beside
  it; do not replace it.
- Do not change the base control protocol. `command`, `status`, `tid`,
  `timestamp`, `request_id`, `message`, and `extended` semantics remain as
  they are.
- Do not change cleanup eligibility or deletion semantics in this slice. The
  same rows that would be selected before should still be selected after.
- Exact deletion must still go through
  `weft/core/pruning/apply.py::apply_exact_prune_candidates()`.
- Queue-window scanning stays bounded by `WEFT_TASK_MONITOR_BATCH_SIZE`.
- Generator-based or queue-window reads stay inside the cleanup cycle. PONG
  never performs history reads.
- Runtime-only queues remain runtime-only. This plan adds diagnostics, not a
  durable archive or audit record.
- `report_only` must produce the same policy stats shape with `reported`
  counts instead of `deleted` counts.
- `jsonl_then_delete` remains fail-closed. Do not use this plan to implement
  logging-before-delete.
- No new dependency, plugin system, or policy registry. Use typed dataclasses
  and small functions.
- Do not mock SimpleBroker queue behavior for policy tests. Use real
  broker-backed queues through existing fixtures/helpers.
- Do not edit unrelated manager or service-convergence code in this slice.
  There are currently unrelated dirty changes in `weft/core/manager.py` and
  `tests/core/test_manager.py`; do not revert or rely on them.

## 5. Target PONG Shape

Add cached policy stats in two places:

```json
{
  "last_cleanup_policy_stats": [
    {
      "policy": "task_log.collate_complete_lifecycle",
      "queue": "weft.log.tasks",
      "scanned": 1000,
      "selected": 12,
      "deleted": 12,
      "reported": 0,
      "stop_reason": "task_log_anchor_too_young_for_collate",
      "reason_counts": {
        "anchor_tid_terminal_event_found_in_window": 12
      }
    }
  ],
  "extended": {
    "task_monitor": {
      "last_cycle": {
        "cleanup_policy_stats": [
          {
            "policy": "task_log.collate_complete_lifecycle",
            "queue": "weft.log.tasks",
            "scanned": 1000,
            "selected": 12,
            "deleted": 12,
            "reported": 0,
            "stop_reason": "task_log_anchor_too_young_for_collate",
            "reason_counts": {
              "anchor_tid_terminal_event_found_in_window": 12
            }
          }
        ]
      }
    }
  }
}
```

Policy records should be small and bounded. Do not include raw messages,
payload hashes, full candidate lists, or unbounded collated task samples.
Detailed collated task samples may remain in `last_cleanup_queue_stats`, where
they are already capped in extended PONG output by
`TASK_MONITOR_PONG_DETAIL_LIMIT`.

Required policy names:

- `tid_mapping.delete_malformed`
- `tid_mapping.delete_older_than`
- `task_log.delete_malformed`
- `task_log.collate_complete_lifecycle`
- `task_log.collate_terminal_without_start`
- `task_log.delete_old_without_start`
- `reserved.delete_terminal_proven`

Emit a zero-count policy record for every configured policy that runs in a
cycle. Zero-count records are the core debugging feature: they prove the policy
ran and found nothing, which is different from the policy never running.

For `reserved.delete_terminal_proven`, emit records only for reserved queues
that had rows in the bounded inspection window. Do not emit one zero-count
record for every collated TID with an empty reserved queue; that would make
healthy PONG responses noisy without proving one of the primary task-log
policies. The task-log and TID-mapping policies remain configured policies and
must emit zero-count records when they run.

## 6. Policy Semantics To Preserve

Task-log policy order remains:

1. `task_log.delete_malformed`
2. `task_log.collate_complete_lifecycle`
3. `task_log.collate_terminal_without_start`
4. `reserved.delete_terminal_proven`
5. `task_log.delete_old_without_start`

`task_log.collate_complete_lifecycle` means rows for a TID with visible start
evidence and terminal evidence in the bounded window.

`task_log.collate_terminal_without_start` means terminal rows or terminal
groups for a TID where the bounded window did not include a start event.
Today this is represented by candidate class `truncated_terminal_task_log` and
reason `terminal_event_without_visible_start_in_window`. Keep the existing
candidate class/reason unless a test proves they are misleading; add policy
identity rather than renaming existing candidate classes.

`task_log.delete_old_without_start` means old unclaimed task-log rows whose TID
does not have visible start evidence in the bounded window after collated
groups are claimed. It must preserve TIDs with visible start evidence and no
terminal evidence in the window. This is the broad older-than policy; it runs
last because it is the most inclusive.

`tid_mapping.delete_malformed` and `tid_mapping.delete_older_than` apply only
to `weft.state.tid_mappings` in this slice.

`task_log.delete_malformed` applies only to `weft.log.tasks` in this slice.
Do not generalize malformed cleanup to arbitrary user queues.

## 7. Tasks

1. Add failing tests for cleanup policy stats.
   - Outcome: the test suite describes the desired policy-level diagnostics
     before implementation.
   - Files to touch:
     - `tests/core/test_task_monitor_cleanup.py`
   - Read first:
     - existing tests around malformed cleanup, repeated collation, truncated
       terminal cleanup, old orphan cleanup, and reserved cleanup.
   - Add or update tests:
     - A mixed `weft.log.tasks` window containing:
       - one malformed row;
       - one complete lifecycle group with a start and terminal;
       - one terminal row without visible start;
       - one old non-start, nonterminal row;
       - one open started TID that must remain protected.
     - Assert `result.policy_stats_summary()` has separate records for:
       `task_log.delete_malformed`,
       `task_log.collate_complete_lifecycle`,
       `task_log.collate_terminal_without_start`, and
       `task_log.delete_old_without_start`.
     - Assert each policy record has the expected `selected`, `deleted` or
       `reported`, `reason_counts`, and `stop_reason`.
     - Add a TID mapping scenario that proves separate
       `tid_mapping.delete_malformed` and `tid_mapping.delete_older_than`
       records.
     - Add a zero-count policy scenario where a policy runs and selects zero.
   - Test style:
     - Use real broker-backed queues and existing helpers in the test file.
     - Do not mock policy functions.
   - Red-green gate:
     - Run `uv run pytest tests/core/test_task_monitor_cleanup.py -q`.
       The new tests should fail before implementation.

2. Add policy identity to cleanup candidates.
   - Outcome: selected rows can be attributed to the policy that claimed them,
     so apply results can be summarized by policy without recomputing.
   - Files to touch:
     - `weft/core/pruning/models.py`
     - `weft/core/pruning/policies/malformed.py`
     - `weft/core/pruning/policies/older_than.py`
     - `weft/core/tasks/task_monitor_cleanup.py`
   - Approach:
     - Add `policy: str` to `CleanupCandidate`.
     - Add a `policy` argument to `cleanup_candidate_from_row()`.
     - Add a `policy` argument to `malformed_row_candidates()` and
       `older_than_candidates()`.
     - Thread explicit policy names through all call sites.
   - Constraints:
     - Keep `candidate_class` and `reason` unchanged unless a test proves they
       are wrong.
     - Do not put policy names in ad hoc metadata when a first-class field is
       now available.
   - Verification:
     - Existing cleanup tests should still pass once expected constructors are
       updated.

3. Add `CleanupPolicyStats` and summary helpers.
   - Outcome: cleanup results expose JSON-safe policy stats alongside existing
     queue stats.
   - Files to touch:
     - `weft/core/pruning/models.py`
     - `weft/core/tasks/task_monitor_cleanup.py`
   - Approach:
     - Define a frozen, slots dataclass:
       `CleanupPolicyStats(policy, queue, scanned, selected, deleted,
       reported, stop_reason, reason_counts)`.
     - Add `to_summary()` mirroring `CleanupQueueStats.to_summary()`.
     - Add `policy_stats` to `TaskMonitorCleanupResult`.
     - Add `policy_stats_summary()` to `TaskMonitorCleanupResult`.
   - Apply-count rule:
     - When `apply=True`, `deleted` is the number of applied candidates for
       that policy whose exact delete succeeded.
     - When `apply=True`, `reported` is the number of selected candidates for
       that policy whose exact delete did not happen.
     - When `apply=False`, `deleted=0` and `reported=selected`.
   - Stop gate:
     - If computing policy stats requires scanning queues a second time, stop
       and redesign. Policy stats must be derived from the same bounded rows
       and selected candidates as the cleanup cycle.

4. Emit policy stats from the TID mapping path.
   - Outcome: `weft.state.tid_mappings` cycles report both malformed and
     older-than policy execution.
   - Files to touch:
     - `weft/core/tasks/task_monitor_cleanup.py`
   - Approach:
     - In `_tid_mapping_candidates()`, keep the current policy order:
       malformed first, older-than second.
     - Build one `CleanupPolicyStats` input record per policy before apply
       counts are filled in.
     - Emit zero-count records when either policy selects nothing.
   - Verification:
     - `tests/core/test_task_monitor_cleanup.py` TID mapping stats test passes.

5. Emit policy stats from the task-log path.
   - Outcome: `weft.log.tasks` reports each conceptual policy separately.
   - Files to touch:
     - `weft/core/tasks/task_monitor_cleanup.py`
     - `weft/core/task_log_collation.py` only if the current return type cannot
       support attribution cleanly.
   - Approach:
     - Keep one bounded decoded window.
     - Keep malformed first and mark those IDs claimed.
     - Keep the repeated collation loop.
     - Attribute collated groups with visible start to
       `task_log.collate_complete_lifecycle`.
     - Attribute collated terminal-without-start groups to
       `task_log.collate_terminal_without_start`.
     - Keep reserved cleanup after collation and attribute any selected
       reserved rows to `reserved.delete_terminal_proven`.
     - Attribute broad older-than rows to `task_log.delete_old_without_start`.
     - Preserve open-start TID protection for older-than deletion.
   - Constraints:
     - Do not run broad older-than before collation.
     - Do not delete or report duplicate candidates; first-claim-wins remains
       the rule.
     - Do not add a policy registry. Local helper functions are enough.
   - Verification:
     - Mixed task-log test passes.
     - Existing collation and reserved cleanup tests still pass.

6. Cache policy stats on `TaskMonitorTask`.
   - Outcome: the live monitor stores the most recent cycle's policy stats in
     memory and PONG can read them without active work.
   - Files to touch:
     - `weft/core/tasks/task_monitor.py`
   - Approach:
     - Add `self._last_cleanup_policy_stats: tuple[dict[str, Any], ...] = ()`
       in `TaskMonitorTask.__init__()`.
     - Clear `_last_cleanup_policy_stats` at the start of a built-in cleanup
       cycle along with `_last_cleanup_queue_stats`.
     - After `run_task_monitor_cleanup()`, set it from
       `cleanup.policy_stats_summary()`.
     - Include `last_cleanup_policy_stats` in `_control_snapshot_fields()`.
     - Include `cleanup_policy_stats` under
       `_task_monitor_pong_extension()["task_monitor"]["last_cycle"]`.
   - Constraints:
     - `_task_monitor_pong_extension()` must not call cleanup code.
     - Do not open or close broker queues in PONG generation.
   - Verification:
     - Existing PONG extension test is updated and passes.

7. Add PONG cache tests.
   - Outcome: tests prove PING uses cached policy stats and does not calculate
     them live.
   - Files to touch:
     - `tests/tasks/test_task_monitor.py`
   - Approach:
     - Extend `test_task_monitor_ping_includes_health_and_preserves_task_log`
       or add a sibling test.
     - Let `TaskMonitorTask.process_once()` run a cleanup cycle using real
       broker queues.
     - Send PING and call `process_once()` again to handle the control message.
     - Assert:
       - top-level `last_cleanup_policy_stats` exists;
       - extended
         `task_monitor.last_cycle.cleanup_policy_stats` equals the top-level
         cached value;
       - existing queue stats remain present;
       - the task-log row remains in report-only mode.
     - For the no-active-calculation proof, after the initial cycle monkeypatch
       `task_monitor_mod.run_task_monitor_cleanup` to raise and send PING
       before the next cycle is due. PONG should still succeed from cached
       state.
   - Test style:
     - Use real control queues (`ctrl_in`, `ctrl_out`) and a real
       `TaskMonitorTask`.
     - Mock only `upsert_heartbeat`, as the existing test already does.
       Do not mock SimpleBroker queues.

8. Update docs and implementation mappings.
   - Outcome: specs describe the new cached policy stats contract.
   - Files to touch:
     - `docs/specifications/00-Quick_Reference.md` if operational PONG fields
       are listed there.
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/07-System_Invariants.md`
   - Required doc points:
     - PONG policy stats are cached from the last cleanup cycle.
     - PONG must not recompute or scan queues.
     - Queue stats and policy stats have different purposes.
     - `weft.log.tasks` remains runtime evidence, not audit evidence.
   - Verification:
     - Run `uv run pytest tests/specs/test_plan_metadata.py -q`.

9. Run focused verification.
   - Commands:
     - `uv run pytest tests/core/test_task_monitor_cleanup.py -q`
     - `uv run pytest tests/tasks/test_task_monitor.py -q`
     - `uv run pytest tests/core/test_task_monitoring.py tests/commands/test_task_monitor.py -q`
     - `uv run pytest tests/specs/test_plan_metadata.py tests/system/test_constants.py -q`
     - `uv run ruff check weft tests`
     - `uv run mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
   - Expand to broader suites if any shared contract changes unexpectedly.
   - If Postgres behavior is suspect, run the relevant `bin/pytest-pg` slice
     for task monitor or cleanup tests. Do not substitute SQLite-only proof for
     an ops-observed Postgres issue if the change affects queue IO.

## 8. Post-Deploy Evidence

After deployment, `weft task ping <task-monitor-tid>` should show:

- `timed_out: false`
- `pong.processor: "delete"`
- `pong.last_processor_success: true`
- `pong.last_cleanup_queue_stats` still present
- `pong.last_cleanup_policy_stats` present
- `pong.extended.task_monitor.last_cycle.cleanup_policy_stats` present
- one record per configured policy that ran in the last cycle
- zero-count records for policies that ran and selected nothing
- positive `deleted` or `reported` counts attributed to the exact policy that
  selected them

Expected cadence remains one cleanup cycle every
`WEFT_TASK_MONITOR_INTERVAL_SECONDS` seconds, default `300`. PING can be called
any time between cycles; it should return the cached stats from the last cycle,
not start a new cycle.

Healthy examples:

```text
policy=task_log.delete_malformed selected=0 deleted=0
policy=task_log.collate_complete_lifecycle selected=12 deleted=12
policy=task_log.collate_terminal_without_start selected=2 deleted=2
policy=task_log.delete_old_without_start selected=0 deleted=0
```

Unhealthy examples:

```text
PONG times out
last_cleanup_policy_stats is missing after a successful cleanup cycle
policy stats exist but all policies are absent except queue-level collation
PONG latency increases because it is scanning queues
queue stats and policy stats disagree on total selected/deleted counts
```

## 9. Rollback

Rollback is straightforward because the change is additive:

- Keep old readers compatible by preserving existing PONG fields.
- If the new policy stats are wrong, revert only:
  - the `CleanupPolicyStats` additions;
  - the `policy` attribution field if it is not used elsewhere;
  - the TaskMonitor cached PONG fields;
  - related tests and docs.
- Do not revert cleanup policy behavior unless a test proves the implementation
  accidentally changed row eligibility.

Operational rollback signal:

- If deployment shows PONG still responsive and queue stats still correct but
  policy stats wrong or missing, this is an observability rollback, not a
  cleanup disablement.
- If deployment shows cleanup deletion behavior changed, stop and diagnose the
  policy runner before deploying further.

## 10. Out Of Scope

- No cleanup rate tuning.
- No change to retention cutoffs.
- No `WEFT_TASK_MONITOR_INTERVAL_SECONDS` or batch-size changes.
- No new queues.
- No service registry cleanup.
- No `jsonl_then_delete` implementation.
- No durable archival/logging of collated lifecycle summaries.
- No public CLI formatting change beyond existing JSON PONG payload content.

## 11. Fresh-Eyes Self-Review

Review performed while drafting this plan.

Findings:

1. The first draft risked treating policy stats as a formatting-only change.
   That would create observability that lies because PONG would have to infer
   policy coverage from queue-level stats. Fixed by requiring first-class
   policy identity on selected candidates and policy stats returned from the
   cleanup runner.
2. The first draft did not explicitly forbid active PONG scans. Fixed by
   making cached-only PONG behavior an invariant, a test requirement, and a
   post-deploy signal.
3. The first draft blurred terminal-without-start with ordinary collation.
   Fixed by naming `task_log.collate_terminal_without_start` as a separate
   policy record while preserving existing candidate class/reason strings.
4. The first draft could have implied malformed cleanup applies globally.
   Fixed by limiting malformed cleanup to explicitly owned cleanup queues in
   this slice.
5. External review is recommended before merge because this is a PONG
   diagnostics contract and cleanup-runtime observability change. The plan is
   usable for implementation, but final landing should include an independent
   review of the diff or the plan.

Residual risk:

- The exact `scanned` meaning for policy records must be kept simple and
  documented in implementation. This plan expects `scanned` to mean the size
  of the bounded window inspected by that policy, or the reserved queue window
  for reserved cleanup. If implementers want more granular "rows actually
  visited by the inner loop," they should stop and update the plan before
  changing semantics.
