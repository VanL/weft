# Liveness Reaper and TID-Mapping Custody Split Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.2]; docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-3.2], [MF-5], Cleanup Boundary; docs/specifications/07-System_Invariants.md [OBS.4], [OBS.5], [OBS.6], [OBS.6a], [OBS.10], [OBS.11], [OBS.11a], [OBS.13.7], [LIVENESS.R1-R10], [MANAGER.18]; docs/specifications/10-CLI_Interface.md [CLI-6]
Superseded by: none

Class: 5. Plan type: implementation with spec revision. Promotion strategy: C,
existing `*A-*.md` planned companions, followed by section-by-section
graduation into the canonical sibling specs before shipped code cites them.
Risky triggers fire (destructive-cleanup custody move, execution-path change,
CLI-contract change, one-way door), so the hardening-plans checklist applies
in full and independent review is required before the spec-promotion slice.

## 1. Goal

Add `LivenessMonitor`, a manager-supervised internal persistent `ServiceTask`
that is the **sole custodian of `weft.state.tid_mappings`**: it probes the
runtime behind each retained TID, retires rows for dead or
sustained-undecidable owners, and is the only component that deletes rows from
that queue. This splits liveness custody out of TaskMonitor, collapses the two
existing tid-mapping deletion paths into one policy with one owner, and makes
"a newest non-terminal mapping row exists" the cross-service contract meaning
"live, or not yet proven dead." There is no query protocol, no reserved
endpoint, no CLI verb, and no durable verdict state: probe deadlines live in
monitor memory, and safety rests on the resurrection property of edge-triggered
mapping publication — a wrongly retired row is recreated by the task's next
append.

## 2. Source Documents

- [`01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.3]
  defines `ServiceTask` (worker groups, due-time math); [CC-3.2] defines
  durable `RunnerHandle` identity, `control.authority`
  (`host-pid` / `runner` / `external-supervisor`), and permits process-local
  runtime liveness probes. Known staleness: [CC-3.2] still says
  `observations.host_pids` where code and
  [`05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  say `observations.host_processes` `(pid, create_time)` pairs; the promotion
  slice fixes [CC-3.2] to `host_processes` rather than silently diverging.
- [`03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.6a] (mapping label; normative prose is the untagged "Managed services"
  item 7) owns internal-service reconciliation; [MA-1.8] owns the admission
  reserve `max(ceil(N * f), 3)`. Line ~528 maps the probe registry to
  `weft/runtime_liveness.py` and must move with the module.
- [`05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3.2] is the periodic internal-service pattern (restart loses runtime
  registrations; late intervals coalesce); [MF-5] separates diagnostics from
  lifecycle truth; the Cleanup Boundary governs destructive queue cleanup.
- [`07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [OBS.4]/[OBS.5] title grammar and short-TID form; [OBS.6]/[OBS.6a]
  edge-triggered append-only mapping publication (no read-before-write);
  [OBS.10] invalid handle shapes must be rejected, not reinterpreted;
  [OBS.11]/[OBS.11a] live evidence must not synthesize or reverse lifecycle;
  [OBS.13.7] undecidable-means-protected destruction gating — this plan
  amends it; [MANAGER.18] duplicates the reserve formula and must be amended
  in the same graduation step as [MA-1.8].
- [`10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-6]
  governs `weft system prune`; this plan removes its `tid-mappings` queue
  group (CLI-contract change with migration note).
- [`14-Python_API_Surfaces.md`](../specifications/14-Python_API_Surfaces.md)
  [PY-1] receives no delta: `weft.liveness` stays private and first-party
  extension registration stays version-coupled.
- Superseded:
  [`2026-08-29-liveness-monitor-plan.md`](2026-08-29-liveness-monitor-plan.md)
  (query-service framing). Inherited from it: the `weft/liveness/` package
  boundary, authority-aware point-in-time analysis, runtime-generation
  fingerprint, cooperative probe budget, extension `liveness_provider`
  routing, manager-supervision shape, admission formula, and the still-valid
  dispositions of its independent-review record (listed in §10). Not
  inherited: the `_weft.liveness` endpoint, request/reply protocol, query
  helper, pending/reply bounds, and the advisory-only containment of cleanup.
  Its planned-companion sections [01A-5], [03A-4], [05A-5], [07A-LIVENESS]
  are replaced by this plan's delta text in the spec-promotion slice.
- Superseded (transitively, already marked):
  [`2026-08-27-per-tid-liveness-registry-and-monitor-split-plan.md`](2026-08-27-per-tid-liveness-registry-and-monitor-split-plan.md).
  Inherited from it: the custody-split intent (liveness leaves TaskMonitor).
  Not inherited: the per-TID queue namespace, the TaskMonitor rename/split
  into LogMonitor, and proposed [OBS.18].
- Required guidance: `AGENTS.md`, `docs/agent-context/decision-hierarchy.md`,
  `docs/agent-context/engineering-principles.md`, and the writing, hardening,
  and review-loop runbooks under `docs/agent-context/runbooks/`.

## 3. Decisions Locked by This Plan

1. **Reaper, not responder.** LivenessMonitor answers no queries. No reserved
   endpoint, no request/reply envelope, no `weft.liveness.query` helper, no
   CLI verb, no change to the 41-command facade bijection. A query surface, if
   ever wanted, is a separate plan.
2. **Single custodian.** LivenessMonitor is the only deleter of
   `weft.state.tid_mappings` rows. TaskMonitor's per-cycle tid-mapping cleanup
   runner is removed; the `tid-mappings` queue group is removed from the
   runtime pruning engine and from `weft system prune` (migration note in the
   CLI spec and README). Tasks remain the only writers.
3. **Single policy path and source location; superseded code deleted.**
   `weft/liveness/policy.py` owns all tid-mapping deletability rules —
   malformed rows, superseded rows, and newest-row retirement (definitively
   stale, or undecidable past the unknown timeout). No second implementation
   or alternate source location for those rules may exist anywhere, and every
   implementation this plan supersedes is deleted in the same slice that
   replaces it (§4 retirement inventory) — no deprecated shims, compatibility
   wrappers, re-exports of the retired module, or orphaned constants/config
   fields. The `weft/liveness` package is the architectural owner, not merely
   a facade over code that remains under `weft/core`.
4. **Row presence is the cross-service contract.** After the split,
   TaskMonitor reads mapping-row presence (newest row exists and is not
   `terminal`) plus live service-registry evidence for **both** of its
   destructive gates — the `stale_open` destruction-protection set and the
   record-less dead-task live set (`_active_runtime_tids`). TaskMonitor
   performs no host or runtime probing of any kind after this plan; the
   `handle_has_live_host_process` calls leave `task_monitor.py` entirely.
   The Manager, bootstrap, and endpoint consumers keep their direct probes —
   those are point-in-time control decisions, not custody.
5. **In-memory deadlines; restart resets.** Unknown deadlines and probe state
   are monitor memory. A monitor restart grants every undecidable owner a
   fresh full timeout. Repeated restarts postponing retirement indefinitely is
   accepted; no durable deadline state may be added to close it.
6. **Not-attempted is not evidence.** Only an attempted, completed probe that
   returns `unknown` may start or age a retirement deadline. Worker-lane
   saturation, plugin load failure before a probe ran, budget expiry with no
   result, and monitor-internal errors freeze the deadline clock for the
   affected TIDs and emit diagnostics. The monitor's own degradation must
   never manufacture retirements.
7. **Runtime ownership stays with extensions.** Core owns the registry,
   generic host analysis, and evidence reduction; extensions register probes
   for their own entry-point key in the process where analysis runs. Alias
   handle producers publish `observations.liveness_provider`; no shared alias
   registration such as `manager-supervisor`.
8. **Self-healing by append is the safety argument.** Mapping publication is
   edge-triggered and append-only ([OBS.6]/[OBS.6a]); a retired row is
   recreated by the owner's next edge (per-work-item activity flips, handle
   changes, and the guaranteed terminal republish). Resurrection re-enters
   normal cleanup: post-disposal activity clears TaskMonitor's
   `task_control_deleted_at_ns` so recreated queues are cleaned again at the
   real terminal.
9. **Preserve uncertain work; salvage before audited destruction; never
   auto-requeue.** Without terminal lifecycle proof, plain `delete` mode may
   automatically remove stale control queues but preserves pending inbox,
   reserved, and unread outbox rows. Existing explicit retention pruning
   (`weft system prune --family task-local --task TID --apply --force
   --archive PATH`) is the later harvest point for that ambiguous data. In
   `jsonl_then_delete` mode, TaskMonitor may dispose the whole family only
   after its existing pre-delete lifetime report has been extended to carry
   those data-bearing rows (bounded). Salvage is evidence for manual recovery;
   no component replays or requeues salvaged rows.
10. **No process control.** Probes are read-only. No liveness result stops,
    kills, restarts, or signals any process, and no liveness result mutates
    TaskSpec state, task-log lifecycle, Manager ownership, or admission.

## 4. Context and Key Files

Files to add:

- `weft/liveness/__init__.py`: narrow private package facade.
- `weft/liveness/models.py`: immutable observation values.
- `weft/liveness/registry.py`: moved process-local probe registry.
- `weft/liveness/analysis.py`: one point-in-time evidence reduction.
- `weft/liveness/host.py`: `psutil` host identity and title corroboration.
- `weft/liveness/policy.py`: the single tid-mapping deletability policy
  (malformed / superseded / newest-row retirement), consolidating
  `weft/core/monitor/policies/tid_mapping.py` and the pruning engine's
  `_tid_mapping_candidates`.
- `weft/core/tasks/liveness_monitor.py`: scheduling, due heap, bounded probe
  workers, deadline state, mapping cursor, reaping.
- tests under `tests/liveness/` and `tests/tasks/test_liveness_monitor.py`.

Files to modify (verified inventory — do not rediscover):

- `weft/_constants.py`: service identity constants near the existing
  internal-service frozensets (`INTERNAL_SERVICE_KEYS` ~:1660-1679, runtime
  task classes ~:1624-1628), new liveness constants (§5 table), and the
  admission floor (`ADMISSION_SERVICE_RESERVE_SLOTS` ~:765 becomes a computed
  floor per §5).
- `weft/core/manager.py`: task-class resolution (~:1586-1608),
  `_child_is_supervision_only` (~:1613-1620), spawn-payload builder alongside
  `_build_heartbeat_spawn_payload` / `_build_task_monitor_spawn_payload`
  (~:4778-4847), `ManagedServiceSpec` factories (~:4849-4862),
  `_managed_service_spawn_queue_name` (~:4864-4873), `_service_key_for_child`
  (~:4919-4931), `_trusted_internal_service_key` (~:5461-5535),
  desired-service list in `_reconcile_managed_services` (~:5904-5913 — the
  new service needs its own enable rule, independent of
  `_task_monitor_enabled`), `_managed_service_convergence_active_reasons`
  (~:6498-6512), admission reserve (~:213-237), and the registry-import
  migration (also ~:138, ~:2451).
- `weft/core/manager_runtime.py`, `weft/core/endpoints.py`: import the moved
  registry; keep their current direct `live/stale/unknown` semantics.
- `weft/commands/system.py`: internal-service classification and display
  (~:530-564), `_service_enabled` (~:1167-1173), diagnostics (~:1263-1279).
- `weft/commands/_task_snapshot_reducer.py` (~:675-692): service-key
  reduction.
- `weft/core/monitor/task_monitor.py`: remove the per-cycle tid-mapping
  cleanup wiring; rewire `_destruction_protected_runtime_tids` (~:3369) to
  row presence; service-key reduction (~:3482-3489); salvage in the
  pre-delete report path (`_handoff_inferred_runtime_report` ~:2180,
  `_handoff_collation_runtime_report`).
- `weft/core/monitor/cleanup.py`: remove `WEFT_TID_MAPPINGS_QUEUE` from
  `TaskMonitorCleanupConfig.queues` and the tid-mapping candidate wiring
  (~:69-95, ~:261-299).
- `weft/core/monitor/store.py`: record merge (~:3446) clears
  `task_control_deleted_at_ns` when post-disposal activity arrives (join the
  existing `raw_deleted_at_ns` / `reserved_cleanup_checked_at_ns` resets).
- `weft/core/monitor/policies/runtime_control.py`: the decision-9 preserve
  rule lands here — `_has_retention_deferred_only_work` (~:447-449) currently
  classifies inbox as a stale suffix deletable pre-retention alongside
  ctrl queues; inbox moves to the preserved data-bearing set, and
  `dead_task_queue_cleanup_plan` / `select_runtime_dead_task_cleanup_candidates`
  must stop selecting inbox, reserved, and outbox rows for automatic deletion
  when terminal lifecycle proof is absent.
- `weft/core/monitor/policies/tid_mapping.py`: dissolved into
  `weft/liveness/policy.py`; `mapping_row_is_live` semantics survive inside
  the policy and the row-presence contract.
- `weft/core/pruning/runtime.py`: remove the `tid-mappings` builder (~:253,
  ~:342-380); `weft/commands/prune.py` and `weft/cli/app.py` (~:1673) drop
  the queue group with a migration note.
- `extensions/weft_docker/weft_docker/plugin.py` (~:719-730): register only
  `docker`; remove the `manager-supervisor` alias registration; alias handle
  producers (containerized manager handles, `weft/core/manager.py`
  ~:2356-2369) publish `observations.liveness_provider`.
- `extensions/weft_macos_sandbox/…/plugin.py`,
  `extensions/weft_microsandbox/…/plugin.py`: extension-owned probes for
  their entry-point keys where runtime identity can prove live or stale;
  otherwise no registration (common miss yields `unknown`).
- `weft/core/tasks/base.py`: **no reserved-endpoint change** — the service
  claims no endpoint. Only the registry-import migration if it imports the
  moved module.
- delete `weft/runtime_liveness.py` after every caller migrates; no
  compatibility wrapper.
- `README.md`, `docs/specifications/00-Quick_Reference.md`, and canonical
  spec `_Implementation mapping_` notes in the final traceability slice.

Retirement inventory (deleted, not deprecated — each item goes in the same
slice that lands its replacement, and the final gate includes a dead-symbol
sweep proving nothing references them):

- `weft/runtime_liveness.py` (task 2 — replaced by `weft/liveness/registry.py`).
- `weft/core/monitor/policies/tid_mapping.py` in full — `decode_tid_state_row`,
  `valid_tid_state_payload`, `mapping_row_is_live`,
  `_newest_message_id_per_key`, `tid_mapping_candidates`,
  `tid_mapping_streaming_candidates` (task 4 — absorbed by
  `weft/liveness/policy.py`).
- `weft/core/monitor/cleanup.py` `_newest_tid_mapping_ids` and the
  tid-mapping wiring; `TaskMonitorCleanupConfig.tid_mapping_min_age_seconds`
  and the `WEFT_TID_MAPPINGS_QUEUE` entry in its `queues` tuple (task 4).
- `weft/core/pruning/runtime.py` `_tid_mapping_candidates`, the
  `"tid-mappings"` builder registration, and the `tid-mappings` entry in
  `RUNTIME_PRUNE_SUPPORTED_QUEUE_GROUPS`; the corresponding `--queues`
  choice in `weft/commands/prune.py` / `weft/cli/app.py` (task 4).
- `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS` (renamed to
  `LIVENESS_MAPPING_MIN_AGE_SECONDS`, same default; task 4). Sweep
  `_constants.py` and CLI/env surfaces for any other tid-mapping cleanup
  knob and retire or rename it with a migration note in the same slice.
- The `manager-supervisor` alias registration in
  `extensions/weft_docker/weft_docker/plugin.py` and its test expectations
  (task 3).
- Every `handle_has_live_host_process` import and call in
  `weft/core/monitor/task_monitor.py` (task 7 — TaskMonitor stops probing).

CLI retirement decision: **no command is retired.** The 41-verb facade
bijection ([PY-2]) is unchanged; the only CLI-contract change is
`weft system prune` losing the `tid-mappings` queue group, recorded in
[CLI-6] and README with a migration note pointing at LivenessMonitor and the
`weft queue` escape hatch. Task 4 verifies the bijection test still passes.

Read first, with comprehension checks:

- `weft/core/tasks/base.py` `_register_tid_state` (~:2258) and its call
  sites: why is publication edge-triggered, which edges exist, and why does a
  retired row self-heal without any read-before-write?
- `weft/core/monitor/policies/tid_mapping.py` and
  `weft/core/pruning/runtime.py` `_tid_mapping_candidates`: which superseded
  rule does each implement today, and why must exactly one survive?
- `weft/core/monitor/task_monitor.py` `_destruction_protected_runtime_tids`
  (~:3369) and `_emit_monitor_store_summaries` (~:2900): what does the
  `stale_open` gate protect today, and what changes when protection becomes
  row presence?
- `weft/core/tasks/service.py` worker group (~:302-427) and
  `weft/core/tasks/heartbeat.py` due heap / `next_wait_timeout` (~:495-529):
  Heartbeat is the due-time model; **TaskMonitor and Manager are the
  worker-group models** (`task_monitor.py` ~:1244-1264, `manager.py`
  ~:374-379). No existing caller sets `worker_count > 1`; add a focused
  multi-worker `ServiceTask` test before depending on it.
- `weft/_runner_plugins.py`: why can a spawned monitor process not rely on
  registry mutations made in its parent (spawn context, module-global
  registry)?
- `weft/helpers/__init__.py` `iter_queue_json_entries` (~:311-329): the
  generator-based replay with a message-ID cursor the monitor must reuse.

Shared paths, do not duplicate:

- `ServiceTask` worker-group machinery; the Heartbeat due-heap pattern;
- `iter_queue_json_entries` for replay and cursor reads (note: the
  latest-row-per-TID reduction is currently hand-rolled in ~5 places;
  the monitor uses the policy module's reduction — do not add a sixth copy);
- `RunnerHandle.from_dict` at the durable boundary;
- the manager's existing internal-service spawn/reconciliation path;
- `psutil.Process(pid).create_time()` and zombie rejection; never
  `pid_exists(pid)` alone;
- entry-point plugin loading in `_runner_plugins.py`;
- TaskMonitor's existing `jsonl_then_delete` report-then-delete path for
  salvage — extend the report, do not add a second evidence channel.

## 5. Contracts and Invariants

### Point-in-time analysis (inherited, unchanged from the superseded plan)

`analyze_liveness(tid, snapshot) -> LivenessObservation` with
`LivenessEvidence = Literal["live", "stale", "unknown"]`. Evidence follows
`control.authority`: for `host-pid`, any exact live `(pid, create_time)` pair
is `live`; `stale` requires every valid scoped identity definitively absent /
reused / zombie with none unresolved; empty, malformed, permission-denied, or
ambiguous identity is `unknown`. For `runner` and `external-supervisor`, the
selected extension probe is authoritative; host wrappers and titles
corroborate only. A positive authoritative result beats an older
`terminal: true` hint; with no positive result, a definitive stale result or
the terminal hint yields `stale`. Titles must match the Weft grammar and
short TID but can never prove a full TID ([OBS.4]/[OBS.5]).

Runtime generation is the SHA-256 of canonical JSON over exactly `runner`,
`kind`, `id`, `control.authority`, normalized `observations.host_processes`
`(pid, create_time)` pairs, optional `observations.liveness_provider`, and
the mapping `terminal` hint. Diagnostic churn does not reset a deadline; a
replacement process under the same TID does.

The moved registry keeps stripped non-empty keys, lock-protected lookup,
last-registration-wins, missing-key `unknown`, and exception/invalid-result
guards. The probe callable becomes
`Callable[[RunnerHandle, float], Literal["live", "stale", "unknown"]]` (the
float is the cooperative budget). Provider selection: a valid stripped
`observations.liveness_provider`, else `handle.runner`; the monitor
lazy-loads that key through the runner loader before lookup.

### Reaper policy (the single deletability path)

`weft/liveness/policy.py` classifies `weft.state.tid_mappings` rows:

1. **Malformed** rows past min-age: delete.
2. **Superseded** (non-newest per full TID) rows past min-age: delete. This
   is the one rule; the pruning engine's positional keep-recent variant is
   removed with it.
3. **Newest-row retirement**, only by the monitor, only past min-age:
   - the row's own evidence is definitively `stale` (all scoped identities
     dead, or a valid `terminal: true` hint with no live proof): delete;
   - the row has been **consecutively undecidable** for one runtime
     generation for at least `LIVENESS_UNKNOWN_TIMEOUT_SECONDS` of attempted
     probes: delete, reason `unknown_timeout`;
   - otherwise: retain.

Deadline semantics are owned by one pure `reduce_unknown_deadline(...)`
transition function. Per TID it stores exactly `generation`,
`deadline_monotonic`, and `paused_at_monotonic` (the whole record is absent
until the first attempted-`unknown`). The transitions are:

- first attempted-`unknown`: create a record with
  `deadline_monotonic = now + LIVENESS_UNKNOWN_TIMEOUT_SECONDS` and
  `paused_at_monotonic = None`;
- repeated attempted-`unknown` while running: retain the original deadline;
  retire only when this completed attempted observation arrives at or after
  that deadline;
- first not-attempted cycle while running: set `paused_at_monotonic = now` and
  never retire on that transition; repeated not-attempted cycles leave the
  record unchanged;
- attempted-`unknown` after a pause: add
  `now - paused_at_monotonic` to the deadline, clear the pause, and then apply
  the normal attempted-unknown expiry check. Thus monitor degradation cannot
  consume timeout budget;
- `live` or a changed generation: delete the record; `stale` deletes the
  record and authorizes immediate min-age-gated retirement independently of
  unknown timing;
- a late worker result whose token or generation is no longer current never
  enters the reducer.

All times are finite monotonic floats and transitions that move time backwards
are fatal internal defects. Rows whose `hostname` differs from the local host
are undecidable by construction and follow the same reducer; record the
hostname in the retirement diagnostic.

Deletion is by exact broker message ID. The monitor deletes nothing else: no
task-family queues, no task-log rows, no other `weft.state.*` queues.

### Row-presence contract (TaskMonitor side)

After the split, `_destruction_protected_runtime_tids` protects: TIDs with
live service-registry evidence, the monitor's own TID, and every TID whose
newest mapping row exists and is not `terminal`. `_active_runtime_tids`
(the record-less dead-task live set) is rebuilt on the same evidence: live
service-registry rows plus mapping-row presence, with its
`handle_has_live_host_process` probing removed. TaskMonitor performs no
probing of any kind. Failure direction: if LivenessMonitor is down, rows
stay present and protection persists — degraded liveness never widens
destruction.

Resurrection contract: the record merge clears `task_control_deleted_at_ns`
(alongside the existing `raw_deleted_at_ns` and
`reserved_cleanup_checked_at_ns` resets) whenever post-disposal events arrive
for a family, so a resurrected task's recreated queues re-enter normal
terminal cleanup instead of leaking.

Destruction contract: absent terminal lifecycle proof, plain `delete` mode
automatically removes only stale control queues. It preserves pending inbox,
reserved, and unread outbox rows; their explicit later harvest uses the
existing task-local retention-prune path with `--apply --force` and the
required archive. This rule is based on lifecycle evidence TaskMonitor already
owns, not on a durable copy of LivenessMonitor's ephemeral retirement reason.
Definitively terminal families keep the existing automatic cleanup policy.

Salvage contract: in `jsonl_then_delete` mode, the pre-delete lifetime report
for an ambiguous family disposal adds the exact object below at
`observations.task_local_salvage`:

```json
{
  "schema": "weft.task_local_salvage.v1",
  "rows": [
    {
      "queue": "T<tid>.inbox",
      "role": "inbox",
      "message_id": 1735689600000000000,
      "body_encoding": "utf-8+base64",
      "body_b64": "Li4u",
      "original_bytes": 3,
      "retained_bytes": 3,
      "truncated": false
    }
  ],
  "total_data_rows": 1,
  "overflow_count": 0,
  "overflow_by_role": {"inbox": 0, "reserved": 0, "outbox": 0},
  "control_row_counts": {"ctrl_in": 0, "ctrl_out": 0}
}
```

`rows` contains only visible pending inbox, reserved, and unread outbox rows,
ordered by `(message_id, role, queue)`, keeping the first
`TASK_MONITOR_SALVAGE_MAX_ROWS` in that order. `total_data_rows` counts all
eligible rows before the cap. `overflow_count` and `overflow_by_role` count
eligible rows omitted by the family cap. Control rows are never copied; their
visible row counts appear only in `control_row_counts`. Each body is encoded to
UTF-8 bytes, truncated to the first `TASK_MONITOR_SALVAGE_MAX_ROW_BYTES`
bytes, then base64 encoded; the byte counts describe the pre/post truncation
lengths. This deliberately permits a truncated byte prefix that is not itself
valid UTF-8 after decoding: recovery gets the exact retained bytes.

Internal report objects carry integer `message_id`; external JSON projection
uses the canonical [SB-0.2] decimal string and restore converts it back to an
integer, including for these nested rows. The complete salvage object
participates in the existing `report_id` input through `observations`, so an
identical retry deduplicates and a changed row set produces a new report ID.
The surrounding lifetime-report schema version remains unchanged because
`observations` is already the extension field; `task_local_salvage.schema`
versions this nested contract. Plain `delete` mode does not need salvage for
these rows because it does not automatically delete them. No auto-requeue,
ever.

### Persistent service behavior

- On startup, one generator-based mapping replay builds the latest-row index;
  normal cycles consume only rows after the message-ID cursor; a
  `LIVENESS_FULL_RECONCILE_INTERVAL_SECONDS` full reconciliation repairs
  cursor/index drift (including rows the monitor itself deleted). One
  due-heap entry per TID/generation; stale entries discarded by token.
- All host/runtime inspection runs in the bounded `ServiceTask` worker group
  (`LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES` lanes). The reactor alone reads
  and writes queues, commits deadline state, and deletes rows. Workers own no
  broker handle.
- At most one in-flight probe per TID; work carries a token plus generation;
  the reactor discards a late result unless both match. Missed intervals
  coalesce ([MF-3.2] pattern).
- The 2-second probe budget is cooperative. A hung callback occupies one lane;
  exhaustion degrades the affected cycles to not-attempted (deadline frozen,
  diagnostic emitted) until restart. Process isolation is out of scope.
- An unknown runner or failed optional plugin load is an attempted probe with
  a conclusive miss only when the registry lookup itself ran; a load failure
  before lookup is not-attempted. When in doubt, classify as not-attempted —
  the conservative direction retains rows.
- Memory is `O(current retained TIDs)`. Capacity eviction of deadline state
  is forbidden (it would silently reset deadlines).
- Process title support follows the existing internal services
  (`enable_process_title=False` in the internal payload, matching Heartbeat
  and TaskMonitor).

First-slice defaults, owned by `_constants.py`:

| Name | Value | Meaning |
|---|---:|---|
| `WEFT_LIVENESS_MONITOR_ENABLED` | env key | Boolean service flag. |
| `LIVENESS_MONITOR_ENABLED_DEFAULT` | `True` | Default for the flag. |
| `LIVENESS_PROBE_INTERVAL_SECONDS` | `5.0` | Re-probe cadence. |
| `LIVENESS_UNKNOWN_TIMEOUT_SECONDS` | `300.0` | Consecutive-attempted-unknown retirement deadline. |
| `LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS` | `2.0` | Cooperative probe budget. |
| `LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES` | `8` | Worker-lane bound. |
| `LIVENESS_FULL_RECONCILE_INTERVAL_SECONDS` | `600.0` | Index repair cadence. |
| `LIVENESS_MAPPING_MIN_AGE_SECONDS` | current `TASK_MONITOR_TID_MAPPING_CLEANUP_MIN_AGE_SECONDS` value | Renamed with custody; same default. |
| `TASK_MONITOR_SALVAGE_MAX_ROW_BYTES` | `65536` | Per-row salvage truncation bound. |
| `TASK_MONITOR_SALVAGE_MAX_ROWS` | `256` | Per-family salvage row cap. |

The retirement timeout is deliberately `300.0`, not the superseded plan's
advisory `30.0`: the verdict now deletes a row, and five minutes of
consecutively attempted-undecidable probes (~60 cycles) tolerates runtime-API
outages while remaining far below the stale-open family window. Only the
enabled flag is configurable in the first slice.

Admission: for maximum `N` and reserve fraction `f`, the common reserve
becomes `max(ceil(N * f), 3 + int(liveness_monitor_enabled))` — the three
modeled slots (Manager, TaskMonitor, Heartbeat) plus one while enabled.
Modeled room, not dedicated permits; backend-specific usage observation is
unchanged. [MA-1.8] and [MANAGER.18] must be amended together at graduation.

Fatal versus best effort:

- corrupt monitor-owned state or an impossible deadline transition is fatal;
- individual probe failures, plugin-load failures, permission errors, and
  title reads are best-effort diagnostics and must not terminate the monitor;
- a failed row deletion is best-effort (retried next cycle by reclassification);
- salvage failure in `jsonl_then_delete` mode blocks that family's deletion
  (report-then-delete order is the discipline), and is reported.

### Existing invariants that must not change

- TID format/immutability, forward-only lifecycle, TaskSpec immutability,
  reserved-queue policy.
- Queue-backed lifecycle state remains canonical; liveness never synthesizes
  or reverses lifecycle, ownership, or admission decisions
  ([OBS.11]/[OBS.11a]).
- Mapping publication stays edge-triggered append-only; no read-before-write,
  no periodic republish is added ([OBS.6]/[OBS.6a]).
- Manager/bootstrap/endpoint consumers of the registry keep their current
  direct `live/stale/unknown` semantics after the module move.
- `weft.state.*` stays runtime-only, excluded from dumps.
- No new dependency; `psutil` and `setproctitle` already exist.
- No process is ever signaled, stopped, or killed by any path in this plan.

## Spec Baseline

- `74a2d3bc184ff6bc649291299569cb301749b465` — `01-Core_Components.md`,
  `03-Manager_Architecture.md`, `05-Message_Flow_and_State.md`,
  `07-System_Invariants.md`, `10-CLI_Interface.md`,
  `14-Python_API_Surfaces.md`, and the planned companions at plan authoring
  time.
- Plan type: implementation with spec revision.
- Promotion baseline: `74a2d3bc184ff6bc649291299569cb301749b465`
  (spec-promotion working tree applied on 2026-08-29; commit identifier pending).

## Proposed Spec Delta

Promotion strategy C via the existing `*A-*.md` planned companions. The
planned companions carry [01A-5], [03A-4], [05A-5], and [07A-LIVENESS] text
authored for the superseded query-service plan; the spec-promotion slice
**replaces** those sections with the text below (the superseded plan file
preserves the old proposal for the record). Graduation is section-by-section
into the canonical siblings; shipped code must not cite the planned sections
before promotion.

| Planned spec | Strategy | Exact section |
|---|---|---|
| `01A-Core_Components_Planned.md` | C | [01A-5] replace: liveness package, evidence authority, reaper policy ownership |
| `03A-Manager_Architecture_Planned.md` | C | [03A-4] replace: third service, independent enable rule, admission floor |
| `05A-Message_Flow_and_State_Planned.md` | C | [05A-5] replace: reaper flow, custody, salvage, prune-group removal |
| `07A-System_Invariants_Planned.md` | C | [07A-LIVENESS] replace: LIVENESS.R1–R10 |

### [01A-5] — replacement text

> A future slice adds `LivenessMonitor`, a manager-supervised internal
> persistent `ServiceTask` that is the sole custodian of
> `weft.state.tid_mappings`. It periodically probes the runtime behind each
> retained TID and retires mapping rows for dead or sustained-undecidable
> owners. It answers no queries and claims no endpoint.
>
> Component boundary: `weft/liveness/` owns point-in-time liveness evidence —
> the process-local probe registry (moved from `weft/runtime_liveness.py`),
> generic `psutil` host inspection, authority-aware evidence reduction, and
> the single tid-mapping deletability policy. First-party runtime extensions
> own their runtime-specific probes and register them under their entry-point
> key in the process where analysis runs; alias handle producers publish
> `observations.liveness_provider`. Core never imports an extension. This
> adds no public [PY-1] surface.
>
> Evidence authority follows `control.authority`: for `host-pid`, any exact
> live `(pid, create_time)` pair with zombie rejection is `live`, `stale`
> requires every valid scoped identity definitively dead with none
> unresolved, and empty, malformed, permission-denied, or ambiguous identity
> is `unknown`; for `runner` and `external-supervisor`, the selected
> extension probe is authoritative and host wrappers corroborate only;
> titles corroborate and never prove a full TID.
> Runtime generation fingerprints exactly `runner`, `kind`, `id`,
> `control.authority`, normalized `observations.host_processes` pairs,
> optional `observations.liveness_provider`, and the mapping terminal hint.
>
> Retirement policy is one pure reducer over per-TID
> `(generation, deadline_monotonic, paused_at_monotonic)` state. The first
> attempted-`unknown` sets `deadline = now + timeout`; later attempted unknown
> keeps it. A not-attempted cycle sets `paused_at` once and never retires; the
> next attempted unknown shifts the deadline forward by the full paused
> duration before checking expiry. `live` or generation change clears the
> record; `stale` clears it and authorizes immediate min-age-gated retirement.
> Late token/generation mismatches never enter the reducer. At an
> attempted-unknown expiry, and for definitively stale rows past min-age, the
> monitor deletes the row by exact message ID. Probes are read-only; the
> monitor deletes only tid-mapping rows and controls no process.
>
> All deadlines are process-local memory. Restart grants a fresh timeout;
> indefinite postponement under repeated restart is accepted, and no durable
> deadline state may be added to close it. Safety rests on edge-triggered
> append-only publication: a retired row is recreated by the owner's next
> mapping edge, and the guaranteed terminal republish restores terminal
> accounting.

### [03A-4] — replacement text

> The canonical Manager supervises `LivenessMonitor` as a third built-in
> persistent service alongside Heartbeat and TaskMonitor, through the
> existing internal spawn queue, runtime envelope, service candidate reducer,
> canonical-owner fence, restart/backoff, and duplicate convergence. No new
> launcher or reconciler. The closed service inventory gains runtime task
> class `liveness_monitor`, service key `_weft.service.liveness_monitor`,
> and role `liveness_monitor`. It claims no endpoint. Its internal payload
> matches the existing internal services (`enable_process_title=False`).
>
> Desired-service rules are independent: `WEFT_LIVENESS_MONITOR_ENABLED`
> implies LivenessMonitor whether or not TaskMonitor is enabled. The flag is
> launch policy, not active-stop authority. For maximum `N` and reserve
> fraction `f`, the common admission reserve becomes
> `max(ceil(N * f), 3 + int(liveness_monitor_enabled))`; modeled room, not
> dedicated permits; backend-specific used-capacity observation unchanged.
>
> Liveness results are not Manager ownership evidence. Manager selection,
> leadership, child liveness, service duplicate convergence, and force-reap
> decisions continue to use their current direct runtime-handle, heartbeat,
> and keyed-PONG paths after the registry import moves.

### [05A-5] — replacement text

> The planned liveness flow is a reaper, not a request/reply service:
>
> ```text
> periodic due time -> latest tid-mapping row / runtime generation
>                   -> bounded host or extension probe worker
>                   -> reactor commits in-memory observation and deadline
>                   -> policy: retain, or exact-delete the mapping row
> ```
>
> Custody: `LivenessMonitor` is the sole deleter of
> `weft.state.tid_mappings`. The TaskMonitor per-cycle tid-mapping cleanup
> and the `tid-mappings` group of the runtime pruning engine (and of
> `weft system prune`) are removed; one policy module owns malformed,
> superseded, and newest-row deletability. Tasks remain the only writers,
> and publication stays edge-triggered append-only.
>
> Cross-service contract: a newest non-terminal mapping row means "live, or
> not yet proven dead." TaskMonitor's destruction-protection gate reads row
> presence plus live service-registry evidence and performs no probing. If
> the monitor is down, rows persist and protection persists.
>
> Resurrection: a task whose row was retired republishes on its next edge;
> post-disposal events clear the family's `task_control_deleted_at_ns` so
> recreated queues re-enter normal terminal cleanup. Without terminal
> lifecycle proof, plain `delete` mode automatically removes only stale
> control queues and preserves pending inbox, reserved, and unread outbox
> rows. Operators may later harvest those rows through the existing explicit
> task-local retention prune with `--apply --force` and its required archive.
> In `jsonl_then_delete` mode the pre-delete family report adds
> `observations.task_local_salvage` with nested schema
> `weft.task_local_salvage.v1`. Its `rows` are the first capped visible inbox,
> reserved, and unread outbox rows ordered by `(message_id, role, queue)`;
> each row carries queue, role, message ID, `utf-8+base64` body bytes,
> original/retained byte counts, and a truncation boolean. The object also
> carries total data-row count, total and per-role overflow counts, and
> ctrl-in/ctrl-out visible row counts. Nested message IDs use integers in the
> internal report and [SB-0.2] strings in external JSON. The complete object
> participates in the existing report ID; the nested schema versions this
> addition without changing the surrounding lifetime-report schema. Nothing
> is ever auto-requeued.
>
> Discovery uses one generator-based startup replay, then message-ID-cursor
> incremental reads, with a periodic full reconciliation; one latest row and
> one due-heap entry per TID/generation; missed intervals coalesce; at most
> one in-flight probe per TID with token-plus-generation commit guards.
> Blocking inspection runs in bounded `ServiceTask` worker lanes; the
> reactor alone touches queues. First-slice constants: enabled by default;
> 5-second interval; 300-second retirement timeout; 2-second cooperative
> budget; 600-second reconciliation; eight lanes. Only the enabled flag is
> configurable.

### [07A-LIVENESS] — replacement text

> - **LIVENESS.R1**: Liveness observations and retirement deadlines are
>   ephemeral process-local state. No verdict queue, table, replay log, or
>   persisted deadline exists. Monitor restart resets every timeout;
>   indefinite postponement under repeated restart is accepted.
> - **LIVENESS.R2**: Liveness evidence never synthesizes, reverses, or
>   authorizes a TaskSpec transition, task-log verdict, Manager ownership
>   decision, admission change, process signal, or any deletion other than
>   `weft.state.tid_mappings` rows.
> - **LIVENESS.R3**: `LivenessMonitor` is the sole deleter of
>   `weft.state.tid_mappings`, and exactly one policy module defines row
>   deletability. No second implementation of malformed, superseded, or
>   newest-row rules may exist.
> - **LIVENESS.R4**: Newest-row retirement requires min-age plus either
>   definitive staleness or a consecutively attempted-`unknown` generation
>   whose in-memory deadline has expired. The one deadline reducer stores
>   generation, deadline, and optional pause time. Not-attempted degradation
>   starts one pause and never retires; the next attempted unknown shifts the
>   deadline by the paused duration before testing it. Only an attempted,
>   completed probe can produce timeout retirement.
> - **LIVENESS.R5**: Evidence authority follows
>   `RunnerHandle.control.authority`; host identity requires
>   `(pid, create_time)` with zombie rejection; extension probes are
>   authoritative for `runner`/`external-supervisor`; titles corroborate and
>   never prove a full TID. Probes are read-only and registration grants no
>   control authority.
> - **LIVENESS.R6**: Mapping publication stays edge-triggered append-only.
>   No component adds read-before-write, periodic republish, or automatic
>   requeue of salvaged rows. Self-healing rests on the owner's next append
>   and the guaranteed terminal republish.
> - **LIVENESS.R7**: TaskMonitor destruction protection reads newest-row
>   presence (non-terminal) plus live service-registry evidence, with no
>   probing. Post-disposal family activity clears
>   `task_control_deleted_at_ns` so recreated queues re-enter terminal
>   cleanup. Without terminal lifecycle proof, plain `delete` mode may remove
>   stale control queues but preserves pending inbox, reserved, and unread
>   outbox rows; the existing forced task-local retention-prune path is their
>   explicit later harvest point. In `jsonl_then_delete` mode, ambiguous
>   family disposal salvages bounded copies of those rows into the pre-delete
>   report before whole-family deletion.
> - **LIVENESS.R8**: Probe concurrency, cadence, retirement timeout,
>   reconciliation interval, min-age, and salvage bounds are named constants
>   in `weft/_constants.py`. At most one in-flight probe per TID; late
>   results are discarded by token-plus-generation match; intervals
>   coalesce.
> - **LIVENESS.R9**: For maximum `N` and reserve fraction `f`, the common
>   modeled internal-lane reserve is
>   `max(ceil(N * f), 3 + int(liveness_monitor_enabled))`; modeled room, not
>   permits.
> - **LIVENESS.R10**: Monitor memory is `O(current retained TIDs)`; capacity
>   eviction of deadline state is forbidden. Startup and reconciliation use
>   generator reads; ordinary cycles consume only rows after the in-memory
>   cursor.

Graduation (section-by-section, before code cites them): [01A-5] into
[CC-2.3]/[CC-3.2] (including the `host_pids` → `host_processes` correction
and moving the registry mapping out of `03-Manager_Architecture.md` ~:528);
[03A-4] into the Managed services item and [MA-1.8]; [05A-5] into [MF-5] and
the Cleanup Boundary; [07A-LIVENESS] into the observability and manager
invariant groups, **amending [OBS.13.7] and [MANAGER.18] in place** so the
canonical text does not contradict the new rules; [CLI-6] records the
`tid-mappings` prune-group removal with a migration note. Add plan backlinks;
remove the planned copies. Implementation mappings and reciprocal `Spec:`
references land together in the final traceability slice. `14-Python_API_Surfaces.md`
receives no delta.

## 6. Rollout, Rollback, and One-Way Doors

Roll out in one release: callers and extensions import the moved module, the
prune CLI group disappears, and custody moves — these must not straddle
releases. There is no persisted payload migration and no queue cutover. The
service can be disabled via `WEFT_LIVENESS_MONITOR_ENABLED`; while disabled,
tid-mapping rows are simply not cleaned (safe direction: retention grows,
nothing is destroyed).

Rollback: remove the service from the manager inventory, restore the
TaskMonitor per-cycle cleanup wiring and the pruning-engine group, restore
`weft/runtime_liveness.py` imports. All new state is memory; no data cleanup.
The row-presence rewire of destruction protection rolls back with the same
change (the old `mapping_row_is_live` gate returns with the policy module).

One-way doors, held to the higher bar:

1. **Timeout-derived deletion of undecidable newest rows** — the explicit
   inversion of "undecidable means never delete." Contained by: min-age plus
   300-second attempted-only deadline, the not-attempted freeze, the
   self-healing append property, and the stale-open window remaining far
   longer than the retirement timeout.
2. **Removal of the `tid-mappings` prune group** — a CLI-contract change.
   Users who pruned mappings manually rely on the monitor afterward;
   `weft queue` operations remain the manual escape hatch. Migration note in
   [CLI-6] and README.
3. **Salvage-before-delete ordering** — once specified, report-then-delete
   for family disposal is a discipline later work must not weaken.

## 7. Implementation Tasks

1. **Spec-promotion slice.** Replace the four planned sections with the
   §Proposed Spec Delta text, add plan backlinks, run `tests/specs`,
   `check-doc-paths`, and the traceability scanner; record the promotion
   baseline. Stop if the canonical owners cannot express the delta without
   contradiction or independent-review findings are undispositioned.

2. **Liveness package and analyzer.** Move the registry to
   `weft/liveness/registry.py`; add models, host inspection, analysis, and
   the generation fingerprint; migrate all imports (`endpoints.py`,
   `manager_runtime.py`, `manager.py` ~:138/~:2451, extensions); delete
   `weft/runtime_liveness.py`. Red-green tests: registry miss/hit/error,
   PID/create-time identity, zombie rejection, title corroboration,
   authority-aware conflicts, multi-PID truth table, malformed handles,
   fingerprint canonicalization. `models.py`, `registry.py`, `host.py`,
   `analysis.py`, and the narrow package facade must not import `weft.core`,
   an extension, or broker state. `policy.py` is the deliberate exception: it
   may import the existing core queue-window, cleanup-candidate, progress, and
   exact-prune types so consolidation does not duplicate those contracts, but
   it must not import TaskMonitor or any task/reactor owner.

3. **Extension registration and provider routing.** Docker registers only
   `docker` (alias registration removed); alias handle producers publish
   `observations.liveness_provider`; macOS sandbox and Microsandbox register
   extension-owned probes for their entry-point keys where their runtime
   identity can prove live or stale, otherwise nothing. Probe callables gain
   the cooperative-budget parameter. Regression tests for Manager and
   endpoint alias handles after the shared alias is removed; fake external
   runtime clients, real registry selection. Stop if core needs an extension
   name table.

4. **Policy consolidation (custody move, behavior unchanged).** Create
   `weft/liveness/policy.py` from `policies/tid_mapping.py` plus the pruning
   engine's superseded rule; remove the pruning-engine `tid-mappings` group,
   the `weft system prune` group (CLI migration note), and TaskMonitor's
   per-cycle tid-mapping wiring. Tasks 4 and 5 are **one landing**: develop
   the policy module first, but the removals in this task cut over only when
   task 5's service runs the policy, so custody never has zero owners at any
   commit that could ship.
   All existing tid-mapping cleanup tests move to the new owner and pass
   with unchanged semantics: this slice deletes no behavior, only relocates
   it. Newest-row retirement still requires definitive staleness here — the
   timeout rule arrives in task 6. Stop if any second implementation of a
   deletability rule survives, or if `weft system prune` grows a replacement
   feature.

5. **LivenessMonitor service and manager supervision.** Implement the
   `ServiceTask` (due heap, bounded workers with `worker_count=8` — add the
   focused multi-worker `ServiceTask` test first, since no existing caller
   exceeds 1 — cursor replay, full reconciliation, token/generation guards)
   running the task-4 policy. Extend every closed-inventory site listed in
   §4 (constants, task-class resolution, payloads, trusted metadata, key
   reduction, status summaries, snapshot reducer, convergence, child
   cleanup, admission reserve with the `3 + enabled` floor, independent
   enable rule at ~:5904 and ~:6506, `_service_enabled`). Harness tests:
   bootstrap, restart after death, duplicate convergence, disable behavior,
   floors of four/three, no public submission of the reserved class/key.
   Stop if a second reconciler, a direct process launcher, or an endpoint
   claim appears.

6. **Unknown-timeout retirement.** Add deadline state with the
   attempted-vs-not-attempted distinction and the retirement rule to the
   policy path. Injected monotonic clock in unit tests; real broker in
   service tests. Cover: countdown without extension; reset on `live`;
   generation reset; restart reset; not-attempted pause and exact deadline
   shift under lane saturation, plugin load failure, and budget expiry;
   boundary at just before and exactly at expiry; backward-time rejection;
   hostname-foreign rows; deletion by exact message ID; no deletion of any
   other queue. Stop if the implementation
   wants durable deadlines, capacity eviction, or a verdict queue.

7. **TaskMonitor split completion.** Rewire
   `_destruction_protected_runtime_tids` **and** `_active_runtime_tids` to
   row presence plus service-registry evidence (removing all
   `handle_has_live_host_process` probing from `task_monitor.py`; note the
   deliberate nuance that a `terminal: true` newest row no longer grants
   record-less protection — the retention gate owns that timing); clear
   `task_control_deleted_at_ns` on post-disposal activity in the store
   merge; make terminal lifecycle proof the gate for automatic deletion of
   data-bearing queues in plain `delete` mode (the classification change
   lands in `policies/runtime_control.py` — see §4: inbox leaves the stale
   suffix set); add bounded salvage to the
   `jsonl_then_delete` pre-delete family report. Reuse the existing task-local
   retention-prune force/archive path for later explicit harvest; add no new
   prune engine. Tests: quiet undecidable family protected while its row
   exists; unprotected after retirement; plain `delete` removes stale control
   queues but preserves pending inbox, reserved, and unread outbox rows when
   terminal lifecycle proof is absent; `system prune --family task-local
   --task TID --apply --force --archive PATH` later archives and deletes those
   preserved rows; terminal-proof families retain their existing automatic
   cleanup; a resurrected family's queues clean at real terminal (no
   early-return leak); exact nested salvage schema, row ordering, UTF-8 byte
   truncation/base64 projection, external message-ID projection/restore,
   report-ID stability, cap and per-role overflow counts;
   salvage failure blocks that family's deletion. End-to-end resurrection
   tests cover both modes: in plain `delete`, a new inbox write reaches the
   quiet owner through the preserved/recreated queue and causes mapping
   republication; in `jsonl_then_delete`, retire the row, salvage and dispose
   the family, let the owner publish its next edge/result, and assert the
   terminal mapping row reappears and cleanup converges. Stop if any
   auto-requeue, durable retirement-reason handoff, or second evidence channel
   appears.

8. **Traceability reconciliation.** Graduate the planned sections per the
   delta table (amending [OBS.13.7], [MANAGER.18], [CLI-6] in place), update
   `00-Quick_Reference.md`, README internal-service/admission/prune
   passages, `_Implementation mapping_` notes, module docstrings, and
   reciprocal `Spec:` backlinks together; rerun the traceability scanner
   against the promotion baseline with no new findings; remove every
   pointer to `weft/runtime_liveness.py`.

## 8. Testing and Acceptance

Keep real: `WeftTestHarness`, SimpleBroker queues, manager supervision,
spawned-process isolation, psutil identity against a real short-lived child,
mapping publication edges, and the report-then-delete path. Mock only Docker
daemon, macOS sandbox inspection, Microsandbox API, permission denial, and a
hung extension probe. Inject the monotonic clock instead of sleeping through
deadlines.

Required focused coverage beyond the per-task lists:

- custody exclusivity: after the split, a repository-wide check that no code
  path outside `weft/liveness/policy.py` + `liveness_monitor.py` deletes
  from `weft.state.tid_mappings` (grep-based architecture test);
- all prior tid-mapping cleanup, manager, and endpoint liveness tests pass
  unchanged after the custody move and import migration;
- `weft system prune` rejects or omits the removed group with the documented
  migration message;
- retirement never fires from not-attempted cycles (regression named for the
  lane-saturation false-death hazard);
- row-presence protection fails toward protection when the monitor is down;
- plain `delete` never automatically deletes inbox, reserved, or unread
  outbox rows for a family lacking terminal lifecycle proof; the existing
  forced task-local prune archives and deletes them only when explicitly
  selected;
- dead-symbol sweep: no reference anywhere to any retirement-inventory
  symbol (§4) survives the slice that deletes it — verified by grep in the
  final gate, not asserted.

Final gates:

```bash
./.venv/bin/python -m pytest tests/liveness tests/tasks/test_liveness_monitor.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs -q
```

Runtime acceptance: `weft status` shows one canonical LivenessMonitor;
live/stale/undecidable fixtures behave per §5; a fabricated undecidable
mapping is retired only after the attempted-unknown deadline and reappears on
the task's next edge; in plain `delete`, its ambiguous data-bearing queues
survive automatic cleanup and remain eligible for explicit archived forced
prune; killing the monitor and restarting grants a fresh timeout; queue
listing shows only standard task-local queues and existing global registries;
no process receives any signal.

## 9. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [OBS.13.12], [CLI-6] | Preserve the existing `runtime_state.retention` cleanup-policy identity while moving TID-mapping custody. | The obsolete policy identity was retired; the shipped cleanup inventory now contains four policies, and TID mappings are absent from generic prune. | Once selection and deletion moved to `weft/liveness`, retaining a TaskMonitor or prune policy identity would imply a second owner that no longer exists. | Specs and CLI migration text were updated in this slice. |
| [LIVENESS.R7], [OBS.13.7] | Rebuild TaskMonitor protection from mapping-row presence plus the plan's broader live-host evidence skip set. | The terminal-proof cleanup skip set uses only live service-registry evidence; mapping-row presence protects nonterminal families at selection, while terminal lifecycle proof outranks a surviving mapping row. | TaskMonitor must perform no liveness probing, and terminal lifecycle proof cannot be blocked forever by an undecidable mapping. | The narrower ownership rule is normative in [LIVENESS.R7] and [OBS.13.7]. |
| [MF-5], [OBS.13.7] | Retire completed Monitor collation families during terminal-control cleanup. | `retire_completed_collation_families` runs in the reserved cleanup slice after terminal-control cleanup. | Keeping the collation row through reserved cleanup preserves the terminal proof needed to classify and harvest a late-discovered reserved queue. | The sequencing note and implementation mapping now identify the reserved slice as retirement owner. |

## 10. Independent Review Loop

Before the spec-promotion slice, a fresh-context reviewer — a different model
family when available — must read this plan, §Proposed Spec Delta,
`weft/runtime_liveness.py`, `weft/core/monitor/policies/tid_mapping.py`,
`weft/core/pruning/runtime.py` (`_tid_mapping_candidates`),
`_destruction_protected_runtime_tids` and `_emit_monitor_store_summaries` in
`task_monitor.py`, the store record merge, `ServiceTask`, and the first-party
runtime plugins. Review stance: attack the one-way doors — could
timeout-derived retirement fire from monitor self-degradation despite R4;
does the row-presence contract widen destruction anywhere; is any second
deletability path left alive; can the salvage bound lose the one row that
mattered; does plain `delete` preserve every data-bearing row when terminal
lifecycle proof is absent; does anything in the plan quietly grant
process-control authority.
Each finding is adopted, rebutted with evidence, or explicitly deferred.

Inherited dispositions from the superseded plan's completed review that still
bind this design: cooperative budget with late-result discard (thread
preemption impossible); one provider-selection rule with no shared aliases;
authority-aware reduction so host wrappers cannot mask a dead runtime;
generation-fingerprint field enumeration; `O(current retained TIDs)` memory
with no capacity eviction; macOS sandbox keeps `control.authority="runner"`
with an extension-owned probe over its published `(pid, create_time)`.

### Independent Review Record

Initial implementation-gate review returned BLOCKED on 2026-08-29 with three
findings, all adopted in this revision: (1) the unknown clock now has an exact
pause/resume reducer and state tuple so not-attempted time cannot consume the
deadline; (2) salvage now has an exact nested JSON schema, ordering,
truncation/encoding, overflow, projection/restore, schema-version, and
report-ID contract; (3) the package dependency gate now keeps the evidence
modules and facade core-free while explicitly allowing `policy.py` to reuse
existing core pruning value types without importing TaskMonitor. Re-review is
complete and passed.

Owner decision 2026-08-29: for families lacking terminal
lifecycle proof, plain `delete` preserves inbox, reserved, and unread outbox
rows; stale control queues may be removed automatically; the existing
explicit archived forced task-local prune is the later harvest path. This
avoids persisting LivenessMonitor's ephemeral retirement reason. Owner also
confirmed that tid-mapping classification and deletion must be centralized:
`weft/liveness/policy.py` is the one selector and LivenessMonitor is the sole
code path that applies exact deletions. Source ownership is centralized too:
the complete policy implementation lives in `weft/liveness`, with no retained
implementation, shim, or re-export under `weft/core/monitor/policies/`. The
prior review objection to moving the existing function-based policy out of
`weft/core/monitor/policies/` is withdrawn; directory continuity is not an
architectural requirement.
The final implementation review found and verified corrections for stale or
duplicate due-heap entries, missing in-flight result-token checks, deadline and
in-flight loss during full reconciliation, invalid durable handles being
misclassified as not-attempted, an unread internal-service inbox, dead policy
artifacts, missing architecture enforcement, and stale implementation mapping
text. Its last finding, misordered canonical constant docstrings, was also
corrected. No substantive finding remains open.

The required independent suppression/refactor review considered locality,
maintainability, and understandability. It judged the Manager internal-service
identity-table refactor net positive, so that refactor was applied. It judged
refactoring the Microsandbox dynamic-SDK boundary and LivenessMonitor per-row
worker boundary net negative, so their distinct local containment policies
remain registered as RUFF-SUP-371 and RUFF-SUP-372. A shared tag would obscure
that the two catches have different owners and failure semantics.

Final working-tree verification on 2026-08-29: the focused liveness,
architecture, Manager, TaskMonitor, pruning, status, and CLI-system suite
passes; Ruff passes; configured mypy passes for 192 source files; the
suppression index, documentation paths, and `git diff --check` pass. An
all-markers repository run reached 4,339 passes and 16 skips with one
index-sensitive failure caused solely by the four intentionally deleted but
uncommitted Python files and one CLI process timeout that passed in isolation.
The prospective-index form of the Ruff discovery test passes. A second
all-markers run against that prospective index reached 4,338 passes and 16
skips with three CLI process/timing failures under 12-way load; all three pass
together in isolation, and their harness disables LivenessMonitor. These
full-suite runs therefore expose existing parallel process-test instability,
not a reproduced liveness regression, but they are not recorded as clean
full-suite passes.

Spec promotion applied 2026-08-29 on baseline
`74a2d3bc184ff6bc649291299569cb301749b465`: [01A-5], [03A-4], [05A-5], and
[07A-LIVENESS] graduated into canonical specs 01, 03, 05, and 07; [CLI-6]
records the `tid-mappings` prune-group removal. The graduated planned sections
were removed. The implementation and independent re-review are complete in the
working tree. The implementation landed in commit `a27e7dc7`.

## 11. Out of Scope

- any query surface: reserved endpoint, request/reply envelope, query
  helper, CLI verb, or `weft.client` API;
- PING/PONG as an active probe;
- changing mapping publication triggers (no periodic republish, no
  read-before-write self-checks);
- auto-requeue or replay of salvaged rows;
- killing, signaling, or restarting any task process;
- per-TID queue namespaces; renaming or splitting TaskMonitor's remaining
  responsibilities (task-log collation, family custody stay put);
- changing `weft.log.tasks` retention or the stale-open window;
- multi-host mapping semantics beyond the hostname diagnostic;
- capacity eviction or durable deadline state.

## 12. Fresh-Eyes Review

Completed 2026-08-29 as a separate pass after drafting. Findings, all fixed
in place: the [01A-5] delta text referenced "the governing plan" (spec text
must be self-contained — replaced with the explicit authority rules); tasks
4 and 5 were ambiguous about whether they are one landing or two (clarified:
one landing, custody never has zero owners); the `_active_runtime_tids`
rewire and its terminal-row nuance were implicit (made explicit in §5 and
task 7); dead-code removal was implicit in the consolidation tasks (now an
explicit retirement inventory in §4 with a grep-verified dead-symbol sweep
in the final gate); the CLI-retirement question is answered explicitly (no
verb retired; one prune queue group removed under [CLI-6] with a migration
note). Open items deliberately left for independent review rather than
self-resolved: whether the 300-second retirement default is right; whether
the record-less terminal-row protection nuance in task 7 needs its own
regression beyond the listed tests. The §10 independent review and owner
approval are complete.

The final implementation review on 2026-08-31 found and fixed two additional
boundary defects: superseded physical probe work could be removed from the
in-flight count before completion, violating [LIVENESS.R8], and post-disposal
activity compared a broker message ID with a wall-clock cleanup timestamp.
Regression tests now cover both cases.
