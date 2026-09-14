# Persistent Result Output IDs Plan

Status: draft
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-2], [MF-5]; docs/specifications/13-Agent_Runtime.md [AR-4.1]; docs/specifications/10-CLI_Interface.md [CLI-1.2.2]; docs/specifications/07-System_Invariants.md [QUEUE.6], [OBS.1], [OBS.13.3], [OBS.13.9], [OBS.14], [OBS.17], [IMPL.2]; docs/specifications/00-Quick_Reference.md (per-task queue table); docs/specifications/04-SimpleBroker_Integration.md [SB-0.2]; docs/specifications/02-TaskSpec.md (`spec.persistent`, `spec.stream_output`, `spec.interactive`, `io.outputs.outbox`)
Superseded by: none

Class: 5 — changes normative text in [MF-2] (completion-event payload
contract and the new membership queue), [MF-5] (result delivery rules),
[AR-4.1], [CLI-1.2.2], 07 (one new observability invariant [OBS.18], an
[OBS.13.9] qualifier, and a stale retention sentence), and the
00-Quick_Reference per-task queue table (a sixth standard task-local queue,
`T{tid}.results`); the change runs through the durable spine (Consumer →
`T{tid}.outbox` / `T{tid}.results` / `weft.log.tasks` → result waiter) and
alters a persisted event format, a queue-name contract, and the `weft result`
public contract (risky triggers: "a public contract … result payload, or
persisted format is changing" and "execution touches the durable spine"), so
the hardening-plans checklist applies. Plan type: implementation with spec
revision. Promotion strategy: **B (atomic)** for every touched spec file —
requirement text, mapping claims, code, and reciprocal `Spec:` backlinks land
as one change (§"Spec-changing slice order"). Origin: review record
`ai-slop-finding-09-persistent-result-boundaries.md` (2026-09-08; owner
decision recorded below). Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).
Revision 4 (2026-09-08) after the round-3 cross-family review; see
`## Review Record` and `## Revision Log`. This revision re-enters review. The
Monitor sidecar schema is **not** changed by this revision (the round-3
retention machinery was removed), so the
[2026-08-25-monitor-schema-semantic-validation-plan.md](./2026-08-25-monitor-schema-semantic-validation-plan.md)
schema-version rule is not triggered.

## 1. Goal

`weft result TID` on a persistent task must return the output of exactly one
completed work item. Today the producer (`Consumer`) knows which
`T{tid}.outbox` rows belong to the item it just finished, discards the
message IDs `Queue.write` returns, and emits a `work_item_completed` event
that names nothing; the reader (`weft/commands/result.py::_await_single_result`)
then reconstructs membership by comparing outbox row timestamps with event
timestamps, falling back to "the latest visible row" and to a 0.5 s quiet
period (`WEFT_COMPLETED_RESULT_GRACE_SECONDS`). Reproduced through the
public `await_task_result` path at `178e3a34`: three inputs `b1`, `b2`, `b3`
with three `work_item_completed` events yield `('completed', 'b1')`, then
`('completed', ['b2', 'b3'])`, then `CommandTimeoutError` — the second call
merges two items. Owner decision (Van, 2026-09-08): the consumer records the
`Queue.write` return IDs of the outbox rows it produced for each item and
publishes them; `weft result` drains exactly those rows;
`_resolve_persistent_result_boundary`, the timestamp comparison, and the
quiet-period heuristic go away; **no legacy branch** for old producers; fold
the one-shot `work_completed` late-output drain (same constant) into the
same scheme; the merge defect is fixed by this design, not separately.

Revision 4 moves the membership representation **off the completion event
and onto a task-owned membership queue, `T{tid}.results`**, the sixth
standard task-local queue. Rounds 1–3 established that every event-borne
representation fails one of the two standing rules: a contiguous range is
not exact under interleaving (round 2: shared/custom outboxes, Heartbeat,
`weft queue write`), and an explicit list on the event is bounded by the
broker message limit — a cap that bites unbounded producers (interactive
sessions at ~100 lines/s reach 400,000 rows in ~67 min; live streaming and
agent outputs are unbounded; the event also carries the full redacted
TaskSpec dump, `base.py:1859-1868`, and `WEFT_MAX_MESSAGE_SIZE` can lower the
ceiling, `_constants.py:2222`, `:2252`) and is therefore a class-D
diminution (round 3: Codex R3-1, Claude 4/7). Membership rows on
`T{tid}.results` carry **explicit ID lists chunked across rows**, so they are
exact under interleaving and unbounded (no cap cliff, no dependence on the
event's size budget). Because the queue is task-owned like the outbox, the
completion row in `weft.log.tasks` is no longer result authority: the whole
round-3 TaskMonitor retention mechanism (`result_pending_at_ns`, schema
v6 → v7, [OBS.19], the `raw_external` probe, the fair-recheck cursor, the
`report_only → delete` restart hazard, the probe `BrokerError` contracts) is
unnecessary and is removed from this plan. Kept: publication is part of
delivery (now: a membership-row write failure is an outbox-write failure —
the item fails through one reactor-side path), the one-consumer invariant
with `PartialResultError`, the quiesce-first cutover with `ResultCutoverError`
as reader-side detection, and the pipeline-family ID-less proof.

Verified 2026-09-08 against the installed dependency (`simplebroker 8.0.0`,
`pyproject.toml:36` floor `simplebroker>=8.0.0`): `Queue.write(self, message:
str) -> int` — docstring: "Returns: The committed message's unique 64-bit
timestamp/message ID — the same value read/peek report for this message and
the ID accepted by exact-ID APIs such as `peek_one(exact_timestamp=...)` and
`delete(message_id=...)`." Primitives (signatures read from the installed
`simplebroker/sbqueue.py`): `read_one(*, exact_timestamp=…)`,
`peek_one(*, exact_timestamp=…, include_claimed=False)`,
`peek_many(limit=1000, *, with_timestamps=False, after_timestamp=None,
before_timestamp=None)`, `delete(*, message_id=…)`. Round-3 probe
`scratchpad/plans/09-rev3/probe_membership_queue.py` (real SQLite broker in
a temp dir), with rows `a1`, `foreign`, `a2` written in that order to one
queue: (1) the producer's own IDs are not consecutive integers
(`a2 - a1 == 2` is `False`; hybrid timestamps) and a **run** `[a1, a2]`
read by range returns `['a1', 'foreign', 'a2']` — the producer cannot
observe a foreign row between its own writes, so run encoding is **not
exact** and is rejected (§4); (2) a membership row on `T{tid}.results`
holding `[a1, a2]` lets the reader `read_one(exact_timestamp=…)` exactly
`['a1', 'a2']`, leave `foreign` in place, and `delete(message_id=row)` the
membership row (`True`, queue then empty); a consumed row is invisible to
`peek_one(exact_timestamp=…)` by default; (3) a 4,096-ID membership row is
86,084 bytes (1/121 of the default 10 MiB limit) and 300 such rows
(1,228,800 IDs) were written and listed without issue; (4) a 60 KB TaskSpec
dump on an event leaves capacity for 496,462 IDs at 10 MiB and **47,072** at
a 1 MiB `WEFT_MAX_MESSAGE_SIZE` — the event is not a safe carrier. Exact-ID
primitives already used by weft: `read_one(exact_timestamp=…)`
(`weft/commands/result.py:374`, `:833`; `weft/commands/queue.py:239`),
`peek_one(exact_timestamp=…, include_claimed=True)`
(`weft/core/monitor/task_monitor.py:4505-4510`), `delete(message_id=…)`
for the reserved acknowledgement (`weft/core/tasks/consumer.py:830`).
Earlier probes (`09-rev/probe_range.py`, `09-rev2/probe_interleave.py`)
remain valid for what they measured and are no longer load-bearing.

## 2. Source Documents

- 05 [MF-2] `:134-135`: "persistent tasks emit `work_item_completed` for each
  completed message and `work_completed` only when the task itself reaches a
  terminal finish". Mapping `:149-150` names `consumer.py`, `base.py`,
  `tests/tasks/test_task_execution.py`.
- 05 [MF-5] `:827-834` (result-wait rules; exact text in Spec Baseline),
  `:803-810` (one-shot terminal proof and `result_without_terminal`),
  `:842-865` implementation mapping; collation rule `:598-601` ("valid rows
  are folded into the Monitor table and then exact-deleted in the same
  bounded pass") — **unchanged by this revision**; the completion row is no
  longer result authority, so the fold pass deletes it exactly as today.
- 07 [OBS.13.2] `:365-372`, [OBS.13.3] `:373-384` (fold-then-exact-delete,
  no age gate — unchanged), [OBS.13.9] `:464-476` ("Standard `T{tid}.outbox`
  is retained until task-log retention age, and standard `T{tid}.reserved`
  remains owned by the reserved cleanup policy" — the membership queue joins
  the outbox in that sentence), [OBS.14] `:508-511`, [OBS.17] `:519-526`
  (last OBS bullet; insertion anchor), [IMPL.2] `:599-600` ("queue payload
  size is bounded by the broker's configured message limit"), monitoring
  prose `:937-948` (the stale "valid rows older than
  `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS`" sentence — see §4 "Retention
  contradiction").
- 00-Quick_Reference `:8-17` (per-task queue table: five rows), mapping `:87`,
  `## Related Plans` `:238`.
- 13 [AR-4.1] `:416-421` (quoted in Spec Baseline); mapping `:427-430`.
- 10 [CLI-1.2.2] `:476-479` mapping (`result.py::_await_single_result` is
  already claimed), `:484-485` (`--stream` "same task-log completion and
  grace rules"); `:930-931` (`system dump` excludes only `weft.state.*`).
- 04 [SB-0.2] `:77-113`: message IDs stay integers inside broker bodies and
  internal JSON; input boundaries normalize through
  `weft/helpers/message_ids.py::normalize_exact_message_id` (`:14`).
- 02-TaskSpec `:83` (`persistent`), `:151-152` (`interactive`,
  `stream_output`), `:163` (`io.outputs.outbox` — "Follows naming
  convention if not provided on task initialization": the outbox name is
  configurable, hence shareable), `:290`, `:302-303` (which reader applies).
- Outbox writers other than the owning task: `weft/core/tasks/base.py:379-382`
  (`_resolve_queue_names` honours `io.outputs["outbox"]`),
  `weft/core/tasks/heartbeat.py:264-290` (`_validate_destination_queue`:
  "Ordinary application and task-local queues remain valid" as heartbeat
  destinations), `weft queue write`/`weft queue move` (public).
- Queue-name contract the membership queue joins: `weft/_constants.py:893-917`
  (`QUEUE_*_SUFFIX`, `STANDARD_TASK_QUEUE_SUFFIXES`); `base.py:372-400`
  (`_resolve_queue_names`: `reserved` at `:392` is TID-derived and not an
  `io` key — the precedent the membership queue follows);
  `weft/core/task_evidence.py:170-195` (`queue_names_for_tid`);
  `weft/core/monitor/policies/dead_task.py:70-135`
  (`standard_task_queue_identity` `:70-79` rejects unknown suffixes;
  `standard_dead_task_retention_queue_names` `:92-98` = outbox + reserved,
  age-gated); `weft/core/monitor/policies/runtime_control.py:214-259`
  (`terminal_task_runtime_queue_cleanup_plan`: outbox retained until
  retention age measured from terminal evidence, `:250-252`), `:443-471`
  (retention/data-bearing suffix sets); `weft/core/monitor/task_monitor.py:2213-2219`
  (role-by-suffix map), `:3835-3841` and `:4202-4210` (runtime queue
  discovery patterns); `weft/commands/dump.py:57` (`exclude=["weft.state.*"]`);
  `weft/commands/queue.py:495-524` (`list_queues` — unfiltered);
  `weft/commands/result.py:862` (`--all` scans `T*.outbox` only);
  `weft/core/pipelines.py:372-373`, `:515-516`, `:580-581` (stages chain
  only the `inbox`/`outbox` io keys); `tests/helpers/hypothesis_strategies.py:96-99`
  (`queue_suffixes()` samples `STANDARD_TASK_QUEUE_SUFFIXES`);
  `tests/helpers/weft_harness.py:359`, `:393-394`, `:500` (harness inspects
  outbox and manager reserved queues; cleanup is temp-dir removal).
- Effective message limit: `weft/helpers/__init__.py:145-156`
  (`resolve_broker_max_message_size(config)`), `weft/_constants.py:2222`
  (`WEFT_MAX_MESSAGE_SIZE` → `BROKER_MAX_MESSAGE_SIZE`), `:2252` (10 MiB
  default); `simplebroker/_constants.py:89`.
- Manager quiescence: `weft/core/manager.py:1068-1078` (child launch skipped
  while `_draining or should_stop`), `:3829-3835` (drain entry);
  `weft/commands/manager.py:225` (`cmd_manager_stop`); children are
  detached and survive Manager exit by design (`docs/agent-context/` lessons;
  `weft task stop TID` is the per-task stop).
- `simplebroker.ext.BrokerError` (imported in `weft/core/queue_wait.py:16`
  and siblings) derives from `Exception` only.
- History (non-normative): [2026-07-11-simplebroker-committed-write-id-adoption-plan.md](./2026-07-11-simplebroker-committed-write-id-adoption-plan.md);
  [2026-08-10-simplebroker-7-json-message-id-boundary-plan.md](./2026-08-10-simplebroker-7-json-message-id-boundary-plan.md)
  (int-vs-string rule; the membership row's IDs follow it); commit
  `85214c2f3` (2026-05-07, "Handle skewed persistent result boundaries")
  and its tests, retired here.
- Guidance: `CLAUDE.md` §1.1 (per-task queue sets are used freely; queues are
  cheap), §4; `docs/agent-context/engineering-principles.md` §3 and §9;
  `docs/agent-context/runbooks/adversarial-acceptance-probes.md` floors 2
  and 5; `docs/agent-context/runbooks/writing-plans.md` "Promotion
  strategies" (`:425-452`, strategy B) and "closes the graph" (`:475-487`,
  backstitch gate); `docs/agent-context/decision-hierarchy.md:212-220`.

## 3. Context and Key Files

### Current structure — producer (`weft/core/tasks/consumer.py`, `weft/core/tasks/base.py`)

Per work item, on the task reactor thread, in this order:

1. `_start_reactor_work_message` `:227-249` (or `run_work_item` `:319-345`
   for the direct path) → `_begin_work_item` `:797-814` → the runner executes
   on the `CONSUMER_ACTIVE_WORKER_LANE` worker thread
   (`_run_reactor_work_item` `:349-372`, `_run_task_for_reactor` `:374-464`).
2. **Live command streaming** (`_uses_live_command_streaming` `:585-590`;
   runner check `weft/core/tasks/runner.py:178-181`): the worker thread's
   chunk callbacks `:417-441` publish `_ConsumerWorkerEvent(kind="stream_chunk")`
   through `BaseTask._publish_worker_result` `:720-740` into the FIFO
   `_worker_result_queue` (`base.py:281-283`). The reactor dequeues them in
   `_drain_worker_results` `:789-815` → `_handle_worker_result` `:476-487`
   → `_handle_consumer_worker_event` `:549-559` → `_emit_live_stream_chunk`
   `:561-583`, one `{"type":"stream", …}` envelope per chunk written at
   `:583`. The worker's `_ConsumerWorkResult` is published to the same FIFO
   *after* `run_with_hooks` returns (`:449-459`), i.e. after every chunk
   callback; `_handle_active_work_result` `:489-548` additionally drains at
   `:509-510`. **Every outbox write happens on the reactor thread.**
3. `_commit_work_outcome` `:276-297`: `_ensure_outcome_ok` (`:1002`; non-ok
   → `_finalize_terminal_outcome` `:964-1000`, a failure event, task
   terminal, no rows named); then `_serialized_result_bytes` `:592-598`
   (live streaming) or `_emit_result` `:875-881` → `_emit_single_output`
   `:883-909` per output (`AgentExecutionResult.outputs`): `stream_output` →
   `BaseTask._write_streaming_result` `base.py:2792-2846` (N chunk
   envelopes, write at `:2842`); oversize → `_spill_large_output`
   `base.py:627-655` and one `{"type":"large_output", …}` row at
   `consumer.py:905`; otherwise one plain row at `:908`. All `.write(...)`
   return values are discarded today.
4. `_finalize_message` `:819-873`: reserved acknowledgement
   `delete(message_id=timestamp)` `:830` inside the `:828-840` guard
   (failure → WARNING, continue: the acknowledgement is best-effort and the
   item still completes); persistent → `_report_state_change(event=
   "work_item_completed", …)` `:845-852`, `_end_streaming_session`; one-shot
   → `mark_completed`, `work_completed` `:859-866`, `_send_terminal_envelope`
   (`base.py:1924`), pipeline terminal event, spill cleanup.
   `BaseTask._report_state_change` `base.py:1845-1895` builds the payload —
   including `"taskspec": redact_taskspec_dump(taskspec.model_dump(...))`
   at `:1859-1868`, so the event's size is not a small constant — and
   writes it through `_write_state_queue_message` `base.py:1786-1843`:
   non-terminal events are one attempt, DEBUG on failure (`:1808-1814`);
   terminal events get `TERMINAL_EVENT_WRITE_RETRIES` = 3 attempts at
   `TERMINAL_EVENT_WRITE_RETRY_INTERVAL` = 0.2 s (`_constants.py:666`,
   `:678`) and a WARNING, still non-fatal (`:1816-1843`). Under this plan
   the completion event is observability only ([OBS.1]); its write policy
   is **unchanged**.
5. Interactive sessions (`weft/core/tasks/interactive.py`): stdout chunks are
   written at `:323` (`_interactive_emit_chunks`, one row per
   `CommandSession._READ_SIZE` = 64 KiB read, `sessions.py:65`, typically
   one line per poll — an unbounded producer); `_interactive_flush_outputs`
   `:270-308` is called from `Consumer._process_reactor_turn` `:138-139`
   **outside** its `try/finally`, so an exception from a write there escapes
   the reactor turn with no `work_failed`; `_interactive_finalize_session`
   `:354-450` reports `work_completed` (`:405-410`) **before** writing the
   final empty stdout envelope at `:423`, and returns early on a second call
   (`:357-358`), so nothing after a raise in the envelope block can report a
   failure (Codex R3-2).
6. `work_completed` producers at HEAD (grep `event="work_completed"`):
   `consumer.py:860`, `interactive.py:407`, `pipeline.py:206`, `:875`.
   Built-in service tasks (`Monitor`, `HeartbeatTask`, `TaskMonitor`, the
   Manager) never emit it. Pipelines: stages cannot be persistent
   (`weft/core/pipelines.py:306-307`); a stage outbox is read as the next
   stage's inbox (`:485`) and by the exit edge (`:580`); `PipelineEdgeTask`/
   `PipelineTask` emit `work_completed` without row identity; the pipeline
   family is identified by `metadata.role in {"pipeline", "pipeline_edge"}`
   (`pipelines.py:513`, `:578`, `:628`; `pipeline.py:756`); stages carry
   `role == "pipeline_stage"` (`:323`, `:457`) and are ordinary Consumers.
7. **Outbox writers**: exactly six sites write a Consumer-family task's *own*
   outbox rows — `base.py:2842`, `consumer.py:583`, `:905`, `:908`,
   `interactive.py:323`, `:423` — all in the task's own process on its
   reactor thread. The **queue** is not exclusive (`base.py:379-382`;
   `02-TaskSpec.md:163`; `heartbeat.py:264-290`; `weft queue write`/`move`).
   Pipeline edges move stage outbox rows into the pipeline TID's outbox
   (`pipeline.py:223`, `:249`); `weft/core/tasks/monitor.py:70-78` moves
   rows into its own outbox without a completion event. "One writer per
   queue" is not an invariant weft can state; and the producer **cannot
   detect** a foreign row between its own writes (§1 probe (1)), which is
   why membership is an explicit ID list and never a range or run.
8. **Deferred control ordering** (Codex R3-3 / Claude 1, reproduced):
   `_finalize_deferred_active_control` `:698-751` calls
   `_handle_stop_request` (`base.py:2071-2094`: `mark_cancelled`, writes
   `control_stop`, terminal envelope — **skipped entirely when the status is
   already terminal**) or `_handle_kill_request`, then sends the ACK
   (`_send_control_response("STOP", "ack")` `:741`, `:751`);
   `TaskSpec.transition_to` raises on any transition out of a terminal
   state (`model.py:1500-1503`). Therefore a failure path must call
   `mark_failed` **first**, then `_finalize_deferred_active_control(
   apply_reserved_policy=False)` (which then sends the ACK only), then the
   terminal finalization — the order round 3 got backwards.

### Current structure — reader (`weft/commands/result.py`, `weft/commands/_result_wait.py`)

- Production callers: `weft result TID` → `cmd_result` `:965` →
  `await_task_result` `:719-796`; `weft run` wait → `weft/commands/run.py:420`;
  Python `Task.result()` → `weft/client/_task.py:51`.
- `_await_result_materialization` `:175-338` collects
  `batch_boundary_timestamps` `:259-265` into `ResultMaterialization`
  `:63-76`; resolves the configured outbox through
  `task_evidence.queue_names_for_tid` (`:201`, `:280`).
- `_await_single_result` `:419-716`: non-persistent → `await_one_shot_result`
  (`_result_wait.py:114-330`); persistent branch from `:464` watches
  `[outbox, ctrl_out, log]` (`:464-467`), keeps `first_pending_timestamp`,
  `latest_pending_timestamp`, `pending_quiet_since`, `boundary_timestamp`,
  `boundary_seen_at`, `pending_completion_timestamps`; boundary inference
  `_resolve_persistent_result_boundary` `:403-416` (the merge defect),
  quiet-period boundary `:529-544`, per-event resolution `:552-587`,
  `if status != "running": break` `:588-589`, drain `:591-617` via
  `_drain_outbox_until_timestamp` `:356-384`, late drain `:619-627`,
  deadline drain `:629-648`, three grace clamps `:650-708`.
- `await_one_shot_result`: **drains the outbox destructively
  (`drain_available_outbox_values` `:190-194`) before polling the log
  (`:223-227`) on every turn**; `materialized_completed` returns as soon as
  anything was drained (`:218-221`); on a terminal non-completed status the
  loop breaks with the rows drained so far (`:242-243`) — a failed one-shot's
  partial output rides with `failed` today; grace drain `:250-262`; the
  terminal-less "single visible result quiet" rule `:270-284`; the deadline
  visible-result rule `:286-295`; grace clamps `:302-322`.
- Shared decoding: `weft/core/outbox.py::process_outbox_message` `:37-91`
  and `aggregate_public_outputs` `:94-100`.
- `WEFT_COMPLETED_RESULT_GRACE_SECONDS` `weft/_constants.py:1898-1899` is
  also used by `weft/commands/events.py:239`, `:436-450`, `:472-490` as a
  post-terminal result grace on the realtime events surface (**out of
  scope**, §9) and asserted at `tests/system/test_constants.py:329`.

### Downstream consumers of the completion-event payload (verified)

- `weft/core/monitor/collation.py::update_from_task_log_row` `:70-85` →
  `update_from_task_log_payload`; `_lifecycle_summary` `:210-226` copies only
  `event`, `status`, `terminal_status`, `activity`, `waiting_on`,
  `message_id`, `checkpoint`; `weft/core/monitor/task_log_scanner.py::decode_task_log_row`
  `:98-125` rejects only non-JSON, non-object, and missing/non-string `tid`.
  Extra event keys are ignored; no Monitor table or JSON-at-rest change, so
  no schema-version bump.
- `weft/core/task_evidence.py`, `weft/commands/_task_snapshot_reducer.py`,
  `weft/commands/events.py::_state_payload` `:67-86` (fixed keys),
  `integrations/weft_django`, `../engram`: no strict-key rejection.
- Test pins on event shape: `tests/cli/test_cli_run.py:2424`
  (`result_bytes > 0`), `tests/tasks/test_task_execution.py:4583`
  (`result_bytes == output_size`) — both kept.

### Files to modify

- `weft/_constants.py` (`QUEUE_RESULTS_SUFFIX`, `STANDARD_TASK_QUEUE_SUFFIXES`,
  `RESULT_MEMBERSHIP_CHUNK_IDS`; grace-constant docstring)
- `weft/core/tasks/base.py` (`results` queue name; pending ID list;
  `_write_outbox_row`; `_flush_membership_row`; `_write_streaming_result`
  write site)
- `weft/core/tasks/consumer.py` (`_emit_live_stream_chunk`,
  `_emit_single_output`, `_commit_work_outcome` output-failure
  finalization, `_finalize_message`)
- `weft/core/tasks/interactive.py` (session-owned publication-failure path;
  final membership row and envelope ordering)
- `weft/core/task_evidence.py` (`results_queue_name_for_tid`)
- `weft/core/outbox.py` (`decode_membership_row`, `MembershipRow`)
- `weft/core/monitor/policies/dead_task.py`, `weft/core/monitor/policies/runtime_control.py`,
  `weft/core/monitor/task_monitor.py` (the results queue joins the outbox's
  retention and discovery sets)
- `weft/commands/_result_wait.py` (`next_membership_item`,
  `deliver_membership_item`, `completion_event_marker`; one-shot waiter)
- `weft/commands/result.py` (materialization; persistent waiter)
- `weft/_exceptions.py` (`ResultCutoverError`, `PartialResultError`)
- `docs/specifications/00-Quick_Reference.md`, `05-Message_Flow_and_State.md`,
  `07-System_Invariants.md`, `13-Agent_Runtime.md`, `10-CLI_Interface.md`
  (delta below)
- `CHANGELOG.md`; `docs/ruff-suppression-registry.md` (if suppressions
  RUFF-SUP-106/108/109 become unused); `docs/agent-context/runbooks/runtime-and-context-patterns.md`
  §8 `:152-160`
- Tests: `tests/tasks/test_task_execution.py`, `tests/tasks/test_task_interactive.py`,
  `tests/tasks/test_agent_execution.py`, `tests/tasks/test_task_monitor.py`
  (queue discovery/cleanup only), `tests/core/test_outbox.py`,
  `tests/commands/test_result.py`, `tests/commands/test_queue.py`,
  `tests/commands/test_dump.py` (or the existing dump test file),
  `tests/core/test_client.py`, `tests/system/test_constants.py`,
  `tests/cli/test_cli_long_session.py:339-341`,
  `tests/long_session_surface_benchmark.py:998-1000`

### Read first (with comprehension questions)

- [MF-2] `:100-152`, [MF-5] `:795-865`, [AR-4.1], [SB-0.2], 07 [OBS.13.9],
  [OBS.17], [IMPL.2]; 00-Quick_Reference `:8-17`.
- `consumer.py:120-160`, `:227-300`, `:489-600`, `:690-751`, `:797-909`,
  `:964-1000`, `:1257-1261`; `base.py:270-290`, `:372-400`, `:720-815`,
  `:1786-1895`, `:2071-2110`, `:2792-2846`; `interactive.py:270-450`;
  `result.py:63-76`, `:175-338`, `:356-716`; `_result_wait.py:114-330`;
  `weft/core/outbox.py:37-100`; `weft/helpers/message_ids.py`;
  `weft/helpers/__init__.py:145-156`; `dead_task.py:60-135`;
  `runtime_control.py:214-259`, `:443-471`; `task_monitor.py:2213-2219`,
  `:3835-3841`, `:4202-4210`; `weft/commands/_task_history.py:48`.
- Questions the implementer must answer before editing: (1) Why is an
  explicit list of `Queue.write` return IDs exactly the item's rows, and why
  is neither a range nor a "run" exact? (answer: each ID identifies the
  task's own committed row; the queue may carry a foreign row between two of
  the task's rows, and the producer cannot see it — §1 probe (1), §3 item
  7.) (2) Which write commits first? (answer: outbox rows, then membership
  rows with the final one last, then the reserved ack, then the event, all
  on one thread against one broker; commit order is visibility order.)
  (3) Why does the Monitor no longer matter to delivery? (answer: the
  reader consumes `T{tid}.results`, a task-owned queue the Monitor treats
  exactly like `T{tid}.outbox` — retained until task-log retention age after
  terminal — and never consults `weft.log.tasks` for membership.) (4) What
  does `_apply_reserved_policy_on_error(None)` do? (answer: returns
  immediately, `:1258-1259`.) (5) Where does an exception thrown by an
  outbox or membership write during `_commit_work_outcome` go today?
  (answer: out of `_handle_active_work_result` `:489-541` — status still
  `running`, so the `except` re-raises — through `_drain_worker_results`,
  `_process_reactor_turn`, `process_once` `base.py:1297`, into
  `run_until_stopped`'s `finally` `:1188-1192`; no `work_failed`. Task 3
  adds the reactor-side finalization.) (6) Why must `mark_failed` precede
  the deferred STOP/KILL reply? (answer: §3 item 8.)

## 4. Invariants and Constraints

- **Outbox row shape is unchanged.** No marker rows, no per-row envelope,
  no `work_item` key on payloads ([AR-4.1]; pipelines chain outboxes as
  inboxes). Identity rides on the task-owned membership queue.
- **Representation — task-owned membership queue (recommended default;
  Open Owner Question 1).** Every Consumer-family task (command, function,
  and agent targets, including interactive sessions and pipeline stages)
  owns a sixth standard task-local queue, `T{tid}.results`
  (`QUEUE_RESULTS_SUFFIX = "results"`, added to `STANDARD_TASK_QUEUE_SUFFIXES`).
  Its name is derived from the TID only, like `T{tid}.reserved`
  (`base.py:392`); it is **not** an `io` key, so a custom or shared outbox
  still has a private membership queue. For each work item the producer
  writes one or more **membership rows**, each a JSON object:
  `{"type": "membership", "message_id": <input message id | null>, "seq":
  n, "final": bool, "outbox_message_ids": [int, ...]}` — the `Queue.write`
  return values of the outbox rows the task wrote for that item, in write
  order, integers ([SB-0.2] internal JSON). Rows are **chunked**: the
  pending list is flushed as a `final: false` row whenever it reaches
  `RESULT_MEMBERSHIP_CHUNK_IDS` (4,096 IDs ≈ 86 KB, 1/121 of the default
  limit; at task start the chunk is clamped to
  `max(1, (resolve_broker_max_message_size(config) - 512) // 21)` so a
  lowered `WEFT_MAX_MESSAGE_SIZE` shrinks the chunk instead of breaking the
  row), and the remainder is flushed as the item's single `final: true` row
  (an item with no rows writes one final row with `[]`). Chunking bounds
  producer memory and row size at the same time; the number of rows is
  unbounded, so **no producer has a cap cliff** — interactive sessions,
  live streaming, and agent outputs of any length publish exactly. The
  list is **exact under interleaving**: a foreign row on a shared or custom
  outbox is never named (§1 probe (2)). *Rejected encoding*: `outbox_runs:
  [[first, last], ...]` — the producer cannot observe a foreign row
  between its own writes, so a run read by range returns it (§1 probe
  (1)); runs would be a compact encoding that silently reintroduces the
  round-2 defect. *Alternatives the owner may pick instead* (OOQ 1): the
  round-3 event-borne list with `RESULT_OUTBOX_ID_LIMIT` (exact, but a
  class-D cliff for unbounded producers and a dependence on the event's
  size budget), or range + `outbox_row_count` on the event (three integers,
  no cliff, not exact on shared outboxes).
- **Completion events keep today's shape plus `membership_rows: n`.** Every
  `work_item_completed` and every Consumer-family `work_completed` carries
  the integer count of membership rows written for that item (≥ 1). The
  reader **never** uses it for membership; it is (a) the cutover detector —
  an event without the key was written by a pre-upgrade producer — and (b)
  a diagnostic on `weft task events`. Decided with evidence over "nothing":
  without a shape marker the only cutover signals are terminal status plus
  unnamed visible rows, which misclassify a new producer's failed-item
  rows and foreign post-completion rows and miss stranded batches of
  cancelled persistent tasks; the count costs one small integer and one
  firing test. The event's write policy is unchanged ([OBS.1]: one attempt
  non-terminal, retried terminal, non-fatal): losing it loses Monitor
  evidence, never delivery.
- **Strict validation, one wire shape.** A membership row is accepted only
  when it is a JSON object with `type == "membership"`, `final` a bool,
  `seq` an int ≥ 0, `message_id` an int > 0 or `null`, and
  `outbox_message_ids` a list whose every element satisfies `type(v) is int
  and v > 0`, strictly increasing (bools and canonical strings rejected —
  there is no second shape for a brand-new internal row). Any other row on
  `T{tid}.results` is a foreign write to a task-owned queue: the reader
  raises `CommandExecutionError` naming the queue and the row ID and the
  remedy (`weft queue read T{tid}.results` / `weft queue delete`); it never
  decodes or deletes it silently.
- **Ordering and membership invariant (producer) — new [OBS.18].** Every
  row named by a membership row is committed before that membership row;
  every membership row of an item is committed before the item's final row;
  the final row is committed before the reserved acknowledgement and the
  completion event; no row for that item is written after its final row.
  All on the reactor thread, against the same broker. Membership is the
  explicit list, never a position, timestamp, range, or run on the outbox.
  Interactive sessions are reordered to honour it (task 3).
- **One consumer — new [OBS.18].** Rows on a task outbox that no membership
  row names (foreign writers, in-progress items, a failed item's rows) are
  outside `weft result TID` delivery and readable through bulk surfaces
  only. `weft result TID` assumes one consumer of a task's membership rows
  at a time; `read_one(exact_timestamp=…)` is atomic per row, so two racing
  readers cannot both receive the same row, and a reader that receives some
  but not all of a membership row's named rows reports `PartialResultError`
  naming the missing IDs and the membership row, without deleting that row
  (Open Owner Question 3). A membership row whose named rows are all
  already consumed (a crash after the reads and before the row's deletion;
  a pruned result) is skipped and deleted — the resumable case.
- **Publication is part of delivery — one producer path.** A membership-row
  write failure is an outbox-write failure: both happen inside
  `_commit_work_outcome` before the reserved acknowledgement, and both are
  finalized by one catch (below). The completion event is not part of
  delivery any more, so the round-3 `result_bearing` retry and
  `ResultPublicationError` are gone.
- **Output-publication failures are finalized on the reactor, in the right
  order (Codex R2-6, R3-3; Claude 1).** Today an exception from an outbox
  write inside `_commit_work_outcome` escapes with the TaskSpec still
  `running` (§3 question 5). This plan adds one catch in
  `_commit_work_outcome` around the emit-and-finalize block: on any
  exception with the status not terminal — (1) clear the pending ID list;
  (2) `self.taskspec.mark_failed(error=str(exc))` **first**; (3)
  `self._finalize_deferred_active_control(apply_reserved_policy=False)` —
  now that the status is terminal, `_handle_stop_request`/`_handle_kill_request`
  skip their transition and events (`base.py:2081-2094`) and the helper
  sends the **ACK only** (`consumer.py:741`, `:751`); (4)
  `self._finalize_terminal_outcome(title_state="failed", title_detail=None,
  event="work_failed", pipeline_status="failed", timestamp=timestamp,
  metrics_payload=None, runner_diagnostics=…, exc=exc)`, which writes the
  one terminal event, sends the terminal envelope, applies
  `reserved_policy_on_error` to the still-unacknowledged row — the one
  [QUEUE.6] disposition — and re-raises (`:1000`). `_finalize_work_exception`
  `:250-274` is refactored to that order (mark, reply, finalize) so the
  runner-failure path and the publication-failure path are one function.
  The existing `except` at `:531-541` then sees a terminal status and logs
  at DEBUG. A status already terminal at the catch → re-raise (no second
  finalization).
- **Interactive sessions — one session-owned publication-failure path
  (Codex R3-2).** `_interactive_flush_outputs` wraps
  `_interactive_emit_chunks` (chunk writes and the membership flushes they
  trigger) in `except (BrokerError, OSError, RuntimeError) as exc:` →
  `session.terminate(); session.stop_monitor();
  self._interactive_finalize_session(failure_reason=f"output publication
  failed: {exc}")` — the same terminal path the limit-violation branch
  uses (`:281-292`): `mark_failed`, `work_failed`, `reserved_policy_on_error`,
  terminal envelope, session closed. In `_interactive_finalize_session` the
  final stdout envelope and the item's `final: true` membership row are
  written **before** the status block: a failed final-envelope write is
  logged at WARNING and the session continues (the envelope is cosmetic
  and simply is not named); a failed final membership write sets
  `failure_reason` so the status block reports `work_failed` (the session's
  output is undeliverable). Interactive sessions never call
  `_finalize_message`; the session is one item and its membership rows
  span the session.
- **Cutover, not compatibility — Manager-level quiescence (Codex R3-9).**
  A `work_item_completed` or non-pipeline-family `work_completed` event
  without `membership_rows` was written by a pre-upgrade Consumer. The
  reader raises `ResultCutoverError` (`CommandError`) **only when** such an
  event is observed **and** `T{tid}.results` has no visible row **and** the
  task's resolved outbox has at least one visible row — rows nothing will
  ever name. After a correctly executed drain the outbox is empty, so old
  keyless events still sitting in `weft.log.tasks` (or already collated by
  the Monitor) cannot raise it — this is how old keyless events are
  retired: they are not consulted for membership at all and are only a
  shape signal in the presence of stranded rows (removes Codex R3-9's third
  point). Detection is best-effort where the old event is still visible;
  the procedure is the contract. Operator cutover, in this order: (1)
  **stop submissions**: `weft manager stop` — the draining Manager refuses
  new child launches (`manager.py:1068-1078`) and exits; running children
  are detached and survive by design; (2) `weft task list --status running`
  → `weft task stop TID` for every running persistent/interactive task;
  (3) wait until each reports a terminal status (`weft task status TID`);
  (4) with the **old** release still installed, drain every unread result
  **per TID** with `weft result TID` repeated until it times out with
  nothing — it resolves the task's configured outbox
  (`task_evidence.queue_names_for_tid`, `result.py:201`, `:439`), whereas
  `weft result --all` scans only standard `T*.outbox` names
  (`result.py:862`) and misses custom/shared outboxes; include
  already-completed tasks whose results were never read; (5) upgrade;
  (6) `weft manager start`. Because an old Consumer that receives STOP
  while an item is active publishes that item's keyless completion
  **after** finishing it (`consumer.py:511-527`, deferred control at
  `:690-696`), draining before stopping strands a batch (Codex R2-5).
  Rows left behind stay readable with `weft queue read T{tid}.outbox` and
  `weft result --all` after the upgrade. Recorded in CHANGELOG (task 9).
  *Alternative (Open Owner Question 2):* a producer-first staged release —
  rejected by the standing no-shim rule; listed so the owner can overrule.
- **Membership queue retention — no Monitor change to result delivery.**
  `T{tid}.results` is a standard task-local queue and follows
  `T{tid}.outbox` everywhere the outbox is treated as data-bearing: it is
  retained until task-log retention age measured from terminal evidence
  (`terminal_task_runtime_queue_cleanup_plan`, `runtime_control.py:250-252`;
  [OBS.13.9]), it is a retention-deferred dead-TID queue
  (`standard_dead_task_retention_queue_names`, `dead_task.py:92-98`), it
  counts as data-bearing/retention-deferred work (`runtime_control.py:454-471`),
  it is discovered by the runtime cleanup snapshots (`task_monitor.py:3838`,
  `:4205`) and labelled in the role map (`:2215`), and `standard_task_queue_identity`
  accepts its suffix (`dead_task.py:77`). Consequences: the TaskMonitor's
  task-log collation deletes completion rows exactly as today ([OBS.13.3]
  unchanged); `result_pending_at_ns`, schema v7, [OBS.19], the `raw_external`
  probe, the fair-recheck cursor, the `report_only → delete` restart hazard,
  and the probe `BrokerError` contracts do not exist. A dangling membership
  row (its outbox rows pruned by `weft system prune`, or moved out of a
  pipeline stage's outbox by the edge) is harmless: it is skipped and
  deleted on the next read, and the queue is removed with the outbox at
  retention age. Results queues are counted with outbox queues in the
  existing runtime-cleanup counters (`dead_tid_outbox_queues_deleted`,
  `outbox_queue_names`) — no new PONG field (Open Owner Question 5).
- **Queue-name contract (b), enumerated.** Quick Reference per-task table
  gains the row; `weft queue list` shows `T{tid}.results` (unfiltered,
  `queue.py:495-524`, no code change, firing test); `weft system dump`
  includes it (only `weft.state.*` is excluded, `dump.py:57`, no code
  change, firing test) and `system load` restores it; `WeftTestHarness`
  needs no change (temp-dir cleanup); `tests/helpers/hypothesis_strategies.py::queue_suffixes`
  picks the new suffix up from the constant; pipelines never chain a
  `.results` queue (stage wiring uses only the `inbox`/`outbox` io keys,
  `pipelines.py:372-373`, `:515-516`, `:580-581`; firing test); `weft system
  prune` is untouched (see the dangling-row rule). `weft result --all`/
  `--peek` (`_read_outbox_task_result` `:799-850`) stay ID-agnostic and do
  not touch membership rows.
- **Retention contradiction resolved.** 07 `:941-943` says "valid rows
  older than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` are folded into
  Monitor-owned tables before exact deletion"; [OBS.13.3] `:373-384`, [MF-5]
  `:599-601`, and the code (`task_monitor.py:2586-2665`, no age check) say
  fold-and-delete in the same pass regardless of age. The constant actually
  gates the pre-checkpoint recovery pass (`:2750-2757`), open-family
  retirement (`:2486-2491`), reserved cleanup (`_constants.py:1054-1059`),
  and dead-TID/outbox cleanup (`:4214-4237`). The delta rewrites the
  `:941-943` sentence to match [OBS.13.3] (round-1 C6, still accepted).
- **Reader invariants**: consume only the rows a membership row names;
  never drain a Consumer-family outbox before membership proof; never
  infer a boundary from quiet time or timestamps; a terminal non-completed
  status is returned without delivering unnamed rows for persistent tasks
  ([OBS.14] stays) and with one final non-blocking drain for one-shot
  tasks (today's `:242-243` behaviour, kept); `_claimed_result_blockage` and
  `result_without_terminal` classification stay unchanged. The
  pipeline-family predicate is `metadata.role in {"pipeline", "pipeline_edge"}`
  (inline; `is_pipeline_taskspec_payload` `_task_history.py:48-53` keeps
  matching only `"pipeline"` for its three other callers — Claude F2).
- **One-shot waits (exact for weft producers).** For a non-pipeline task the
  one-shot loop **never** drains the outbox before proof: each turn it
  checks `T{tid}.results` for a complete item (a `final: true` row at the
  head) and, if present, reads exactly the named rows and returns
  `completed` at once — before or without the terminal event (the final
  membership row precedes it, so it is proof). ID-less proof (typed
  terminal `ctrl_out` envelope; pipeline-family `work_completed`) ends the
  wait with one final non-blocking drain — the current contract for
  current producers ([MF-5] `:829-830`), not a legacy decoder (Open Owner
  Question 4). The terminal-less "single visible result quiet" rule
  (`:270-284`) survives only on the ID-less path; for a Consumer-family
  task a visible unnamed row is an in-progress item or a crashed
  producer, and the wait ends at the caller's timeout (register row 17).
  No timed grace after any proof.
- TID format/immutability, forward-only transitions, reserved-policy
  handling ([QUEUE.6] — the acknowledgement at `:830` still precedes the
  event and now follows the final membership row), `spec`/`io`
  immutability, `weft.state.streaming` markers, spawn-context behaviour:
  unchanged.
- Hidden couplings, named: reactor-thread-only outbox writes;
  `_drain_worker_results` FIFO; the deferred-control terminal transition
  (§3 item 8); `ResultMaterialization.batch_boundary_timestamps` consumed by
  `tests/commands/test_result.py:2171-2224`; C901 suppressions
  RUFF-SUP-106/108/109 (re-verify with RUF100); `tests/system/test_constants.py:329`;
  `tests/core/test_ops_shared.py:279` (events surface, untouched);
  `standard_task_queue_identity` rejecting unknown suffixes (a results
  queue would otherwise be invisible to dead-TID cleanup and leak);
  PostgreSQL — the exact-ID reader path runs through `bin/pytest-pg`
  (task 8); `WeftTestHarness` disables the supervised monitor by default
  (`tests/helpers/weft_harness.py:671`), so the shipping-default proof
  enables it explicitly.
- Error-path priorities: a failed outbox or membership write during output
  emission is finalized on the reactor as `work_failed` (above; the failure
  event carries no `membership_rows`; already-written rows and membership
  chunks are unnamed/dangling and self-heal); a failed reserved
  acknowledgement still leads to the completion event; a failed
  completion-event write is observability loss only; a broker error during
  the reader's exact-ID reads propagates.
- Rollback: revert the single atomic change. The old reader ignores
  `membership_rows` and the `T{tid}.results` queues (they are ordinary
  queues to it) and infers boundaries as before; the old dead-TID cleanup
  does not recognise the `results` suffix, so leftover results queues are
  cleaned by `weft queue delete` or `weft system prune` — noted in the
  CHANGELOG. **Never delete the broker database**: Monitor tables live in
  it (`store.py:2381-2398`) and so do queues and results (Codex R3-8); no
  sidecar schema change exists in this revision, so no sidecar step is
  needed. The CHANGELOG break is that events written before the upgrade
  are not boundaries (cutover error).
- Rollout: one atomic landing (strategy B); the quiesce-first operator
  cutover above; PostgreSQL run before landing.
- Review gates: no new execution path; new code is two exception classes,
  one pure membership-row decoder with its frozen dataclass, one producer
  write helper and one flush helper, one reader item collector and one
  deliverer, one event-marker check, one queue-name constant with its
  policy-set additions, one chunk constant; no new dependency; no change
  to `weft result` flags; one new exit-code producer (`ResultCutoverError`/
  `PartialResultError` → existing `CommandError` handling, exit 1) with
  firing tests; external review before landing.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `178e3a34` — docs/specifications/00, 05, 07, 13, 10, 04, 02 at plan
  authoring time (2026-09-08). Promotion baseline identifier (R2-8, Claude
  round-3 #6): **base SHA `178e3a34`** plus the pre-landing identifier task
  10 records here — the SHA of the **parent of the commit that records it**
  and the output of `git diff --stat 178e3a34..<that parent> -- . ':!docs/plans/2026-09-08-persistent-result-output-ids-plan.md' | tail -1`,
  taken from a clean worktree (`git status --porcelain` empty). Excluding
  the plan file and naming the parent makes the recorded values stable
  under the recording commit itself. The landing SHA is recorded afterwards
  in the Revision Log by a separate docs-only commit.
- Exact text at baseline that this plan replaces or extends:
  - 00 `:12-16`: the five-row per-task queue table (`T{tid}.inbox`,
    `T{tid}.reserved`, `T{tid}.outbox`, `T{tid}.ctrl_in`, `T{tid}.ctrl_out`).
  - 05 `:134-135`: "- persistent tasks emit `work_item_completed` for each
    completed message and\n  `work_completed` only when the task itself
    reaches a terminal finish"
  - 05 `:827-830`: "- `weft result` and `weft run` share the same wait helper
    path: they watch the\n  outbox, ctrl-out, and log queues, tolerate the
    short gap between a terminal\n  log event and final outbox visibility,
    and do one last non-blocking outbox\n  drain before returning
    `completed`"
  - 05 `:831-832`: "- persistent result waits treat both
    `work_item_completed` and\n  `work_completed` as completion boundaries
    for the same task"
  - 05 `:833-834`: "- `weft result --stream` follows unread outbox stream
    chunks without changing\n  the task-log boundary events that define
    completion"
  - 05 `:835-840` (kept verbatim): emitted-result-fact and materialization
    rules. 05 `:599-601` kept verbatim (collation rule unchanged).
  - 07 `:469-471` (inside [OBS.13.9]): "Standard\n    `T{tid}.outbox` is
    retained until task-log retention age, and standard\n    `T{tid}.reserved`
    remains owned by the reserved cleanup policy."
  - 07 `:519-526`: the [OBS.17] bullet (last bullet before
    `### Liveness Invariants` at `:528`; insertion anchor, unchanged).
  - 07 `:940-943`: "Retained task-log cleanup is\nMonitor-table driven:
    malformed `weft.log.tasks` rows are exact-deleted; valid\nrows older
    than `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` are folded into\nMonitor-owned
    tables before exact deletion;"
  - 13 `:416-421`: "- For persistent tasks, `work_item_completed` is the
    boundary event for one\n  inbox message while the task itself remains
    `running`.\n- `weft result TID` uses those existing task log events to
    determine when a\n  batch of outbox messages for one work item is
    complete.\n- `weft result TID --stream` may render unread outbox stream
    chunks live, but\n  it still stops at those same existing boundary
    events rather than inventing a\n  new public protocol."
  - 10 `:484-485`: "- `weft result TID --stream` follows unread outbox
    stream chunks for that one\n  task while still using the same task-log
    completion and grace rules"

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/00-Quick_Reference.md | B | per-task queue table `:12-16` gains one row; mapping `:87` gains `weft/core/task_evidence.py::results_queue_name_for_tid` |
| docs/specifications/05-Message_Flow_and_State.md | B | [MF-2] `:134-135` replace; [MF-5] `:827-834` replace (three bullets); `:842` mapping gains `weft/commands/_result_wait.py::next_membership_item`, `::deliver_membership_item`, `::completion_event_marker` and `weft/core/outbox.py::decode_membership_row` |
| docs/specifications/07-System_Invariants.md | B | [OBS.13.9] `:469-471` replace; insert [OBS.18] after `:526`; `:940-943` replace; `:270-272` mapping gains `weft/commands/_result_wait.py` and `weft/core/monitor/policies/dead_task.py` |
| docs/specifications/13-Agent_Runtime.md | B | [AR-4.1] `:416-421` replace (three bullets); mapping `:427-430` unchanged |
| docs/specifications/10-CLI_Interface.md | B | [CLI-1.2.2] `:484-485` replace; mapping `:476-479` unchanged |

Strategy A was wrong in round 1: [MF-2] `:149`, [MF-5] `:842`, [AR-4.1]
`:427`, and [CLI-1.2.2] `:476` already carry implementation mappings to
`consumer.py`, `base.py`, and `result.py`, so promoting the text alone would
make active specs claim that unchanged code implements named-row delivery.
Under B nothing is promoted before the code that satisfies it.

### 00 — add a row to the per-task queue table after `:14` (`T{tid}.outbox`)

> | `T{tid}.results` | Result membership: which outbox rows belong to each completed work item ([MF-2]) |

### 05 [MF-2] — replace the bullet at `:134-135`

> - persistent tasks emit `work_item_completed` for each completed message and
>   `work_completed` only when the task itself reaches a terminal finish.
>   Every Consumer-family task (command, function, and agent targets,
>   including interactive command sessions and pipeline stages) owns a
>   standard task-local membership queue, `T{tid}.results`, whose name is
>   derived from the TID only and is not an `io` key. For each work item
>   the task writes one or more membership rows to it: JSON objects
>   `{"type": "membership", "message_id": <input message id or null>, "seq":
>   n, "final": bool, "outbox_message_ids": [...]}` carrying the SimpleBroker
>   message IDs returned by `Queue.write` for the outbox rows written for
>   that item, in write order, as integers ([SB-0.2] internal JSON), chunked
>   across rows so that no row approaches the broker message limit
>   ([IMPL.2]) and no item is bounded in row count; the item's last row has
>   `final: true` (an item that wrote no row has one final row with an empty
>   list). Membership is the list and nothing else: rows on the same outbox
>   written by another task sharing the outbox name, by a Heartbeat, or by
>   `weft queue write`/`move` are not the item's output ([OBS.18]). Every
>   named row is committed before the membership row that names it; the
>   final row is committed before the reserved acknowledgement and before
>   the completion event, on the task reactor thread against the same
>   broker. The completion event (`work_item_completed`, or the
>   Consumer-family `work_completed`) carries `membership_rows`, the count
>   of membership rows written for the item; readers never derive
>   membership from the event. Outbox row payloads are unchanged. Failure
>   events (`work_failed`, `work_timeout`, `work_limit_violation`,
>   `work_cancelled`, `work_killed`) and pipeline-family `work_completed`
>   events (roles `pipeline` and `pipeline_edge`) carry no membership. A
>   membership-row write is part of result delivery: an outbox or
>   membership write that fails while an item's output is being published
>   fails the item (`work_failed`, reserved policy applied); rows already
>   written for it are unnamed.

### 05 [MF-5] — replace the three bullets at `:827-834`

> - `weft result` and `weft run` share the same wait helper path: they watch
>   the membership, ctrl-out, and log queues and deliver results by the
>   outbox message IDs the task's membership rows name ([MF-2]). For a
>   Consumer-family one-shot task the wait never drains the outbox before
>   proof: when the head of `T{tid}.results` is a complete item (its
>   `final: true` row is visible) the waiter reads exactly the named rows by
>   message ID, deletes the membership rows, and returns `completed` at
>   once, with or without the terminal event. Completion proof that names no
>   rows — a typed terminal `ctrl_out` envelope, or a pipeline-family
>   `work_completed` — ends a one-shot wait with one final non-blocking
>   drain of the visible outbox. A terminal non-completed status ends a
>   one-shot wait with one final drain. There is no timed grace window after
>   completion proof, no comparison of outbox row timestamps with event
>   timestamps, and no quiet-period inference of a boundary
> - persistent result waits deliver one work-item batch per call: the
>   oldest complete item on `T{tid}.results`. The waiter reads that item's
>   membership rows in order, reads exactly the rows they name by message
>   ID, deletes each membership row once its rows are read, and never reads
>   rows it was not given. A deliverable item is returned before a later
>   terminal non-completed status; that status is reported by a subsequent
>   call once no deliverable item remains, without delivering unnamed rows.
>   A membership row whose named rows are all already consumed is skipped
>   and deleted. A membership row that yields some but not all of its named
>   rows is reported as `PartialResultError` and is not deleted ([OBS.18]:
>   one consumer per task). A row on `T{tid}.results` that is not a
>   well-formed membership row is reported as an error naming the row; it
>   is never decoded or deleted silently. A `work_item_completed` or
>   non-pipeline-family `work_completed` event without `membership_rows`
>   was written before this contract; when such an event is observed while
>   `T{tid}.results` is empty and the task's outbox still holds a visible
>   row, the waiter raises `ResultCutoverError` naming the task and the
>   cutover procedure instead of waiting; otherwise the event is ignored.
>   Visible outbox rows with no membership are an in-progress item, a
>   failed item's output, or a foreign writer's rows: a wait on them ends
>   at the caller's timeout with the rows untouched, and the rows stay
>   readable through `weft queue peek`, `weft queue read`, and `weft result
>   --all`. Task-log collation never affects delivery: `T{tid}.results` is
>   a task-owned data queue retained with `T{tid}.outbox` ([OBS.13.9])
> - `weft result --stream` renders an item's unread stream chunks as that
>   item is consumed, without changing the membership rows that define it

### 07 [OBS.13.9] — replace `:469-471`

> Standard
>     `T{tid}.outbox` and `T{tid}.results` are retained until task-log
>     retention age, and standard `T{tid}.reserved` remains owned by the
>     reserved cleanup policy.

### 07 — insert after `:526` (after the [OBS.17] bullet, before `### Liveness Invariants`)

> - **OBS.18**: a Consumer-family task writes its own outbox rows only from
>   its task process on its reactor thread, but the outbox queue is not
>   exclusive: its name is TaskSpec-configurable and may be shared, and
>   Heartbeat tasks and `weft queue write`/`move` may add rows to it, and
>   the task cannot observe a foreign row between its own writes. Membership
>   therefore lives on the task-owned queue `T{tid}.results` as explicit
>   lists of message IDs (`outbox_message_ids`), never as a position,
>   timestamp, range, or run on the outbox. Every named row is committed
>   before the membership row that names it; an item's `final: true` row is
>   committed before its reserved acknowledgement and completion event; no
>   row for that item is written after its final row. Rows on the outbox
>   that no membership row names are outside `weft result TID` delivery and
>   readable through bulk surfaces only. `weft result TID` delivery assumes
>   one consumer of a task's membership rows at a time; a read that returns
>   some but not all of a membership row's named rows is reported as a
>   partial result, never delivered as a value.

### 07 — replace `:940-943`

> Retained task-log cleanup is
> Monitor-table driven: malformed `weft.log.tasks` rows are exact-deleted; valid
> rows are folded into Monitor-owned tables and exact-deleted in the same
> bounded pass regardless of age ([OBS.13.3]); result membership never lives
> in `weft.log.tasks` ([MF-2]), so collation timing does not affect delivery;
> `WEFT_LOG_TASKS_RETENTION_PERIOD_SECONDS` gates the pre-checkpoint recovery
> pass, raw external export, open-family disposition, and reserved/dead-TID
> cleanup, not first-pass folding;

### 13 [AR-4.1] — replace the three bullets at `:416-421`

> - For persistent tasks, `work_item_completed` is the boundary event for one
>   inbox message while the task itself remains `running`; the item's outbox
>   rows are named by message ID on the task's membership queue ([MF-2]).
> - `weft result TID` reads exactly the rows named by the next undelivered
>   membership item ([MF-5]).
> - `weft result TID --stream` may render that item's unread stream chunks
>   live, but it still delivers by those same membership rows rather than
>   inventing a new public protocol.

### 10 [CLI-1.2.2] — replace the bullet at `:484-485`

> - `weft result TID --stream` follows unread outbox stream chunks for that one
>   task while still using the same membership-row delivery rules

## Spec-changing slice order

Strategy B: promotion and implementation are one landing. Sequence on a
branch: 1. plan (this document) → 2. independent review of plan and delta
(§8) → 3. branch commit A: apply the exact delta text **and** the mapping
additions listed in the table → 4. branch commits for tasks 3–9 (each with
its reciprocal `Spec:` backlinks, so the branch never carries a code
citation of an absent section) → 5. task 10 gates (backstitch, specs,
doc-paths) from the branch head, then record the pre-landing identifier
(parent SHA + clean-worktree diff stat excluding the plan file) in Spec
Baseline → 6. land as **one** change to `main` (squash or merge commit; no
intermediate state reaches `main`) → 7. a separate docs-only commit records
the landing SHA in the Revision Log. Never implement against this plan's
delta text on `main` while `docs/specifications/` still reflects the
baseline.

## 5. Tasks

1. **Independent review** of this revision (§8). Rounds 1–3 are recorded in
   `## Review Record`; round 4 must re-verify the membership-queue
   representation and chunking (task 3), the failure-path ordering (task
   3), the queue-name contract additions (task 7), the cutover detector
   and the in-process cutover test (tasks 4, 8), and that no Monitor
   retention change remains anywhere in the plan. Open Owner Questions
   carry recommended defaults; the plan is implementable on those defaults
   and is blocked only if the owner overrules one.

2. **Spec text and mappings (branch commit A).**
   - Files: 00, 05, 07, 13, 10; add this plan under each `## Related
     Plans` (00 `:238`, 05 `:1525`, 07 `:1068`, 13 `:886`, 10 `:1032`).
   - Apply the exact delta text and the mapping additions in the table.
   - Verify: `./.venv/bin/python -m pytest tests/specs -q` and
     `bin/check-doc-paths`. Expect backstitch reciprocal-missing warnings
     until the code commits land on the branch; they must be gone by task
     10.

3. **Producer: record outbox IDs, publish membership rows on
   `T{tid}.results`, mark the event, finalize publication failures on the
   reactor in the right order.**
   - Outcome: every Consumer-family item's outbox rows are named by
     membership rows on `T{tid}.results` (chunked, final row last, `[]`
     for none); rows precede their membership row; the final row precedes
     the reserved ack and the event; the event carries `membership_rows`;
     an outbox or membership write that fails during publication fails the
     item with exactly one `work_failed`, one reserved disposition, and an
     ACK to any deferred STOP/KILL; interactive sessions fail through one
     session-owned path.
   - Files: `weft/_constants.py`, `weft/core/tasks/base.py`,
     `weft/core/tasks/consumer.py`, `weft/core/tasks/interactive.py`,
     `weft/core/task_evidence.py`; `tests/tasks/test_task_execution.py`,
     `tests/tasks/test_task_interactive.py`, `tests/tasks/test_agent_execution.py`,
     `tests/system/test_constants.py`.
   - Read first: §3 producer trace, items 5 and 8, question (5); [MF-2]
     delta; `base.py:270-290`, `:372-400`, `:1786-1895`, `:2071-2110`,
     `:2792-2846`; `consumer.py:250-300`, `:489-541`, `:561-583`, `:690-751`,
     `:819-909`, `:964-1000`, `:1257-1261`; `interactive.py:270-450`;
     `weft/helpers/__init__.py:145-156`.
   - Approach (reactor thread only):
     - `_constants.py`: `QUEUE_RESULTS_SUFFIX: Final[str] = "results"` after
       `:902` with the docstring "Suffix for result membership queue names
       (T{tid}.results): which outbox rows belong to each completed work
       item. Derived from the TID only; not an io key."; append it to
       `STANDARD_TASK_QUEUE_SUFFIXES` `:911-917`;
       `RESULT_MEMBERSHIP_CHUNK_IDS: Final[int] = 4096` with the derivation
       (21 bytes per 19-digit ID in a JSON array ≈ 86 KB per row at the
       default limit; clamped to the effective limit at task start) and
       the [IMPL.2] reference; pin both in `tests/system/test_constants.py`.
     - `task_evidence.py`: `results_queue_name_for_tid(tid: str) -> str`
       returning `f"T{tid}.{QUEUE_RESULTS_SUFFIX}"` (one f-string helper so
       reader, monitor policies, and tests agree; no io lookup).
     - `base.py::_resolve_queue_names` `:372-400`: add `"results":
       f"{tid_prefix}.{QUEUE_RESULTS_SUFFIX}"` next to `reserved` `:392`.
       `BaseTask.__init__` (next to `_spilled_output_dirs` `:275`):
       `self._pending_outbox_ids: list[int] = []`,
       `self._membership_seq: int = 0`, `self._membership_rows_written: int
       = 0`, `self._membership_chunk_ids: int = min(RESULT_MEMBERSHIP_CHUNK_IDS,
       max(1, (resolve_broker_max_message_size(self._weft_config) - 512)
       // 21))`.
     - `BaseTask._write_outbox_row(self, outbox_queue: Queue, body: str) ->
       int`: `message_id = outbox_queue.write(body)`; append; if
       `len(self._pending_outbox_ids) >= self._membership_chunk_ids`:
       `self._flush_membership_row(final=False, message_id=None)`; return
       the ID. `BaseTask._flush_membership_row(self, *, final: bool,
       message_id: int | None) -> None`: writes
       `json.dumps({"type": "membership", "message_id": message_id, "seq":
       self._membership_seq, "final": final, "outbox_message_ids":
       self._pending_outbox_ids})` to `self._queue(self._queue_names["results"])`,
       then clears the list, increments `seq` and `_membership_rows_written`.
       Non-final flushes carry `message_id=None` because the input ID is
       only known to `_finalize_message`; the final row carries it. One
       helper each, no class, no wrapper queue. Exceptions propagate (the
       caller's catch owns them).
     - Replace the six raw outbox writes with `self._write_outbox_row(...)`:
       `base.py:2842`, `consumer.py:583`, `:905`, `:908`,
       `interactive.py:323`, `:423`. Interactive stderr envelopes go to
       `ctrl_out` and are not recorded; `weft.state.streaming` writes are
       not outbox rows.
     - `_finalize_message` `:819-873`: **first** `self._flush_membership_row(
       final=True, message_id=timestamp)` and `rows = self._membership_rows_written;
       self._membership_rows_written = 0; self._membership_seq = 0`; then
       the reserved ack `:828-840` as today; then pass
       `membership_rows=rows` into both `_report_state_change` calls
       (`:845-852`, `:859-866`). The event write policy is unchanged.
     - `_finalize_work_exception` `:250-274` reordered: `mark_failed`, then
       `self._finalize_deferred_active_control(apply_reserved_policy=False)`
       (ACK-only now that the status is terminal — §3 item 8), then
       `_finalize_terminal_outcome(...)`. Every existing caller keeps its
       behaviour (a deferred command previously got its reply from
       `_handle_active_work_result` only on the ok path).
     - `_commit_work_outcome` `:276-300`: wrap the block from the
       `_emit_result`/`_serialized_result_bytes` call through
       `_finalize_message` in `try/except Exception as exc:`; in the
       handler, if `self.taskspec.state.status in TERMINAL_TASK_STATUSES`
       → `raise`; otherwise `self._pending_outbox_ids = []` (and reset the
       seq/count) and `self._finalize_work_exception(exc, timestamp)` (the
       reserved row is still unacknowledged at this point, so the reserved
       policy applies as for any runner failure; it re-raises through
       `_finalize_terminal_outcome`). Add a `# noqa: BLE001 approved
       [TS-3.1] [RUFF-SUP-…] exception` pragma with a registry row (task
       9) — a process-boundary catch that converts an output-publication
       failure into the task's terminal state.
     - `_finalize_terminal_outcome` `:964-1000`: reset the pending list and
       counters at entry (failure events carry no membership; a failed
       item's rows and chunks are unnamed/dangling).
     - `interactive.py`: (a) `_interactive_flush_outputs` `:270-308`: wrap
       the `_interactive_emit_chunks` call in `try: … except (BrokerError,
       OSError, RuntimeError) as exc:` → `session.terminate();
       session.stop_monitor(); self._interactive_finalize_session(
       failure_reason=f"output publication failed: {exc}")` and return —
       the same shape as the limit-violation branch `:281-292`; (b)
       `_interactive_finalize_session` `:354-450`: move the two "final
       envelope" blocks (`:413-425` stdout → outbox, `:427-438` stderr →
       ctrl_out) **above** the status block (`:365-410`); guard the stdout
       envelope write with `except (BrokerError, OSError, RuntimeError):
       logger.warning("Interactive final stdout envelope write failed",
       exc_info=True)`; then `try: self._flush_membership_row(final=True,
       message_id=None) except (BrokerError, OSError, RuntimeError) as exc:
       failure_reason = failure_reason or f"result membership publication
       failed: {exc}"`; the status block then reports `work_failed` with
       that reason (its `terminal_override_allowed` logic is unchanged) or
       `work_completed` with `membership_rows=self._membership_rows_written`.
       Comment that the session is one item whose membership rows span the
       session.
   - Reuse: existing writers; `_report_state_change` `**extra`;
     `_finalize_work_exception`; `_finalize_deferred_active_control`;
     `resolve_broker_max_message_size`; the limit-violation finalize shape.
   - Constraints/forbidden: no envelope on outbox rows; no second event; no
     per-row metadata; no range, run, or count keys for membership; no
     locks (assert reactor-thread ownership in a comment); no change to
     `result_bytes`; no change to reserved acknowledgement order relative
     to the event; no event retry change; no `io` key for the results
     queue; no second catch in `_handle_active_work_result`; no cap
     constant.
   - Tests (red first; `broker_env`, real `Consumer`; helpers
     `tests/tasks/test_task_execution.py:2022` `drain_queue`, `:2069`
     `make_command_taskspec`, `:3387` `_drive_consumer_until`; membership
     rows read with `Queue(results_queue_name_for_tid(tid)).peek_many(
     with_timestamps=True)`):
     - `test_work_item_membership_names_exact_outbox_ids`: persistent
       function task, two inbox items; `peek_many(with_timestamps=True)` on
       the outbox; item 1's final membership row lists item 1's row IDs in
       order with `message_id` = its input ID, item 2's lists item 2's,
       disjoint; each `work_item_completed` carries `membership_rows == 1`.
       Fails at HEAD: no results queue.
     - **interleaving writer (R2-1)**: persistent function task on a
       **custom shared outbox** (`io.outputs.outbox = "shared.outbox"`);
       between item 1's rows a second real `Queue("shared.outbox")` writer
       inserts a row (wrapping `write` on the task's outbox queue to
       perform one foreign write after the first chunk — observation-only
       wrapper plus one real foreign write, §6) → the membership list omits
       the foreign ID; exact reads of the list return only the item's rows;
       the foreign row stays; the results queue is `T{tid}.results`, not
       under the custom name.
     - chunking: patch `RESULT_MEMBERSHIP_CHUNK_IDS` to 2 on the base
       module (constant patch) and stream a 5-chunk payload → three
       membership rows (`seq` 0, 1, 2; `final` false, false, true; lists of
       2, 2, 1 IDs in order; every ID smaller than the next membership
       row's own ID, which is smaller than the event row's ID — ordering
       via `with_timestamps`); `membership_rows == 3` on the event.
     - lowered limit: `WEFT_MAX_MESSAGE_SIZE` in the task config small
       enough to clamp the chunk below 4096 → the chunk equals the clamp
       and every row is under the limit.
     - extend `test_stream_output_writes_chunks` `:4536` and
       `test_stream_output_small_payload_single_chunk` `:4586`: the list
       equals the chunk row IDs in order.
     - live command streaming, persistent (`make_command_taskspec(...,
       persistent=True, stream_output=True)`, as `:4679`): the membership
       covers every envelope written by `_emit_live_stream_chunk`.
     - spilled output (`test_run_work_item_spills_large_output` `:2617` /
       `test_large_output_spills_to_disk` `:4429`): the list is exactly the
       one `large_output` row's ID.
     - agent multi-output: extend
       `test_consumer_persistent_agent_per_message_processes_multiple_messages`
       (`tests/tasks/test_agent_execution.py:571`): the list is exactly that
       message's output row IDs.
     - zero-row item: fake runner returning `AgentExecutionResult` with no
       outputs → one final row with `[]`; `membership_rows == 1`.
     - **output-write failure with deferred STOP (R3-3 / Claude 1)**:
       inject a `BrokerError` on the outbox queue's second `write` during
       `_write_streaming_result` (`base.py:2831-2846`; the one permitted
       outbox write-failure injection, §6) on a task with
       `reserved_policy_on_error=clear` and a STOP written to `ctrl_in`
       while the item is active → status `failed`; **exactly one** terminal
       event in `weft.log.tasks` for the TID and it is `work_failed` (no
       `control_stop`); the reserved queue is empty (**one** [QUEUE.6]
       disposition, cleared once); one `STOP` ACK on `ctrl_out`; the
       already-written chunk row remains in the outbox unnamed; no
       membership row. Twin with KILL (`work_failed`, one KILL ACK, no
       `control_kill`). At HEAD both fail: no `work_failed`, status stays
       `running`.
     - **membership-write failure**: inject `BrokerError` on the results
       queue's `write` (the second permitted injection, §6) during the
       final flush → same assertions as above (reserved row disposed by
       the error policy; the item's outbox rows present and unnamed;
       exactly one `work_failed`).
     - interactive (`tests/tasks/test_task_interactive.py`, next to
       `test_interactive_command_streams_output` `:140`): the session's
       membership rows cover every stdout row including the final envelope,
       and the final membership row's ID is smaller than the
       `work_completed` row's ID; `membership_rows` equals the count.
       Mid-session (R3-2): with the chunk patched to 2 and a `BrokerError`
       injected on the results queue's write → session terminated,
       `work_failed` with "output publication failed", reserved policy on
       error applied, terminal envelope on `ctrl_out`, exactly one terminal
       event. Final-envelope injection (F4): a `BrokerError` on the final
       stdout envelope write → `work_completed` still written, the
       membership excludes the failed envelope, WARNING logged. Final
       membership injection: `BrokerError` on the final membership write →
       `work_failed` with "result membership publication failed".
   - Stop if: any outbox write for a work item is found off the reactor
     thread; or a runner path writes result rows after `_finalize_message`
     — report, do not add a grace.
   - Done when: the tests above pass and the three task test files stay
     green.

4. **Shared reader helpers (`weft/core/outbox.py`, `weft/commands/_result_wait.py`)
   and the cutover detector.**
   - `weft/core/outbox.py`: `@dataclass(frozen=True, slots=True) class
     MembershipRow: message_id: int | None; seq: int; final: bool;
     outbox_message_ids: tuple[int, ...]` and
     `decode_membership_row(body: str) -> MembershipRow | None` (pure; lives
     in `core` next to `process_outbox_message` so `core` never imports
     `commands`): the strict rules in §4; anything else → `None`.
   - `_result_wait.py::next_membership_item(results_queue, *, tid) ->
     tuple[tuple[tuple[int, MembershipRow], ...], bool]`: `peek_many(
     with_timestamps=True, limit=RESULT_MEMBERSHIP_PEEK_LIMIT)` — reuse an
     existing peek limit constant if one fits (`weft/_constants.py`
     `QUEUE_PEEK_*`), else the SimpleBroker default 1000 without a new
     constant; walk rows from the head; a row that fails to decode → raise
     `CommandExecutionError(f"results queue {name} for task {tid} holds a
     non-membership row {row_id}; remove it with `weft queue read {name}`
     or `weft queue delete`")`; collect `(row_id, row)` until the first
     `final` row → return `(rows, True)`; no final row visible → `(rows,
     False)` (item in progress or nothing pending). An item that spans more
     than the peek limit is collected across successive peeks
     (`after_timestamp=last_row_id`) until its final row is found.
   - `deliver_membership_item(outbox_queue, results_queue, rows, stream_buffer,
     *, emit_stream, tid) -> list[DecodedOutboxValue]`: for each
     `(row_id, row)` in order: read each named ID with
     `read_one(exact_timestamp=id)`; `None` → skip; else
     `process_outbox_message(body, stream_buffer, emit_stream=emit_stream)`;
     after the row: if some but not all IDs were read → raise
     `PartialResultError(tid, row_id, missing_ids)` (`CommandError`)
     **without** deleting the row; otherwise `results_queue.delete(
     message_id=row_id)`. Returns the decoded values (possibly empty when
     every row was already consumed — the resumable/pruned case, skipped
     by the caller). Broker errors propagate.
   - `completion_event_marker(payload) -> bool | None`: for `event` in
     `{work_item_completed, work_completed}` with `metadata.role` not in
     `{"pipeline", "pipeline_edge"}`: `membership_rows` present and
     `type(v) is int and v >= 0` → `True`; absent or malformed → `False`;
     pipeline-family or any other event → `None`.
     `raise_if_stranded_cutover(context, tid, *, outbox_queue,
     results_queue)`: when the marker is `False`, raise
     `ResultCutoverError(tid)` (`CommandError`) iff `results_queue.peek_one()
     is None and outbox_queue.peek_one() is not None`; the message says
     "stop the manager and every running task, drain results per TID with
     the previous release, then upgrade" and names both the running and the
     completed-but-unread case. Called from materialization and from both
     waiters wherever events are polled.
   - Delete `result.py::_drain_outbox_until_timestamp` `:356-384`,
     `_latest_visible_outbox_timestamp` `:387-400`,
     `_resolve_persistent_result_boundary` `:403-416` (no other callers).
   - `weft/_exceptions.py`: `ResultCutoverError(CommandError)`,
     `PartialResultError(CommandError)`.
   - Forbidden: a batch class beyond the frozen row dataclass; a generic
     schema validator; timestamp comparison; accepting string IDs; a bulk
     range read; deleting a malformed or partial membership row.
   - Tests (`tests/core/test_outbox.py` for the decoder;
     `tests/commands/test_result.py` with real `Queue` via `build_context`):
     decoder — a valid row round-trips; `["12"]`-style strings, `True`,
     `0`, negative, duplicates, decreasing, `final` non-bool, wrong `type`,
     a non-object → `None`; `[]` → `()`. `next_membership_item` — two
     complete items visible → returns the first only; a `final: false` row
     alone → `(rows, False)`; a foreign string row at the head →
     `CommandExecutionError` naming the queue and row. `deliver` — reads
     only the named rows and leaves an interleaved foreign row and rows on
     both sides untouched; deletes the membership rows in order; a row
     whose IDs are all consumed → skipped and deleted; one of three named
     rows consumed elsewhere → `PartialResultError` naming the missing ID
     and the row, row not deleted; stream item reassembles through a fresh
     `stream_buffer`; a 2-row item (chunked) is delivered as one value list.
     Marker — absent key on `work_item_completed` with rows in the outbox
     and an empty results queue → `ResultCutoverError` whose message names
     the TID and the procedure; absent key with an **empty** outbox → no
     error (the drained-then-upgraded case; pins R3-9's third point);
     absent key on `role == "pipeline"` and on `role == "pipeline_edge"`
     `work_completed` → `None` (F2); `membership_rows: "1"` → treated as
     absent.
   - Done when: those tests pass.

5. **Persistent waiter and materialization.**
   - Outcome: `_await_single_result` persistent branch delivers exactly one
     membership item per call per the [MF-5] delta; no timestamp or quiet
     state remains.
   - Files: `weft/commands/result.py`; `tests/commands/test_result.py`.
   - Read first: [MF-5] delta; `result.py:175-338`, `:419-716`.
   - Approach:
     - `ResultMaterialization`: drop `batch_boundary_timestamps`; add
       `results_name: str` (from `results_queue_name_for_tid`);
       `_await_result_materialization` `:259-265` no longer collects
       boundaries but runs `raise_if_stranded_cutover` on each polled
       completion event. Rename the `_await_single_result` keyword away
       (remove `initial_batch_boundary_timestamps`).
     - Persistent loop (replace `:464-716` state and body): watch
       `[results_queue, ctrl_queue, log_queue]`; per turn: (a) ctrl_out
       terminal envelopes as today; (b) `poll_log_events`; apply
       `terminal_status_from_event` as today and `raise_if_stranded_cutover`
       per completion event; (c) `next_membership_item`; if complete →
       `deliver_membership_item` with an item-local `stream_buffer`; no
       values (all consumed) → continue the turn; otherwise
       `append_public_value` each value (honouring `show_stderr`) and
       return `("completed", aggregate_public_outputs(values), None)`; (d)
       if `status != "running"` return `(status, None, error_message)`
       (unnamed rows untouched); (e) if the terminal status is `completed`
       and no complete item remains return `("completed", None, None)`;
       (f) deadline → `CommandTimeoutError` (no drain); (g)
       `monitor.wait(min(poll_interval, remaining))` — no grace clamps.
     - `emit_stream` passes through to the row read; `--stream` does not
       change delivery.
     - Delete the `WEFT_COMPLETED_RESULT_GRACE_SECONDS` import from `result.py`.
   - Reuse: `poll_log_events`, `drain_ctrl_out_stream_messages`,
     `select_terminal_envelope`, `append_public_value`,
     `aggregate_public_outputs`, `QueueChangeMonitor`.
   - Forbidden: peeking or draining the outbox to decide anything;
     `latest visible` scans; any fallback for old events other than the
     cutover error; delivering unnamed rows at the deadline or on a
     terminal status.
   - Tests (real broker through `build_context`; outbox and results queues
     written directly as the existing tests do, membership rows built from
     `write()` return values; events written to the log with
     `membership_rows`):
     - **red first — the reproduced defect**: `b1`, `b2`, `b3` each with
       its final membership row and event → three calls through the public
       `await_task_result` return `b1`, `b2`, `b3`; a fourth raises
       `CommandTimeoutError` with the outbox and results queues empty.
     - rewrite `test_await_single_result_persistent_returns_one_work_item_batch`
       `:1453` (multi-row item then `"third"`) and
       `test_await_single_result_persistent_stream_mode_keeps_next_batch`
       `:1632` to membership rows.
     - **interleaved foreign row**: item 1 = `[b1a, b1b]` with a foreign
       row written between them on the same queue → the call returns
       `b1a + b1b` only and the foreign row stays.
     - **chunked item**: two membership rows (`final` false/true) → one
       value list; both rows deleted.
     - **item without its event** (event write lost): delivered on the
       final membership row alone.
     - cutover: keyless `work_item_completed` + visible outbox rows + empty
       results → `ResultCutoverError` from `cmd_result` (exit code 1
       through the CLI adapter test), rows untouched; keyless event + empty
       outbox → no error, the wait continues (red at HEAD because today's
       quiet period returns rows).
     - in-progress item: outbox rows and a `final: false` row only →
       `CommandTimeoutError`, rows untouched.
     - partial: one of item 1's rows consumed elsewhere →
       `PartialResultError`; the membership row remains.
     - consumed item: all of item 1's rows read before the call → item 1's
       row deleted, item 2 returned.
     - item then `work_cancelled` visible in one poll → first call returns
       the item; second returns `("cancelled", None, error)`.
     - KILL mid-item: unnamed rows plus `work_killed` → `killed`, rows
       untouched.
     - deadline with unnamed visible rows → `CommandTimeoutError`, rows
       untouched (replaces `:629-648`).
     - persistent terminal `work_completed` after a final item → item
       delivered; next call `("completed", None, None)` immediately.
     - malformed results row → `CommandExecutionError` naming the row.
     - materialization: update `test_cmd_result_passes_materialized_state_to_result_wait`
       `:2171` and `test_await_single_result_reuses_materialized_batch_boundary_state`
       `:1799` to the new field.
     - retire `test_await_single_result_persistent_returns_quiet_visible_output_without_boundary`
       `:1517` (A, register row 3) and the three skew tests `:1844`,
       `:1891`, `:1937` (their scenarios cannot arise under [OBS.18];
       register row 5). Record the retirement in the CHANGELOG entry.
   - Stop if: a real-broker test observes a named row unreadable right
     after its membership row is visible — a broker visibility defect to
     report upstream, not a reason to reintroduce a grace.
   - Done when: `tests/commands/test_result.py -q` is green and
     `./.venv/bin/ruff check --extend-select RUF100 weft/commands/result.py`
     passes (task 9 handles the registry).

6. **One-shot waiter: membership proof first, no pre-proof drain, no
   completion grace.**
   - Outcome: `await_one_shot_result` delivers a Consumer-family result on
     its final membership row without draining the outbox beforehand;
     ID-less proof keeps the single drain; a terminal non-completed status
     keeps its final drain; no timed grace after any proof.
   - Files: `weft/commands/_result_wait.py`, `weft/commands/result.py`
     (pass `results_name` and the pipeline-family flag);
     `tests/commands/test_result.py`.
   - Approach: add keyword `membership_expected: bool` (True unless
     pipeline-family). Per turn: (1) when `membership_expected`:
     `next_membership_item`; complete → `deliver_membership_item`, merge
     with nothing (no prior drain), return `("completed", aggregate, None)`;
     all-consumed → continue; (2) ctrl_out terminal envelopes and
     `poll_log_events` as today (with `raise_if_stranded_cutover`); on
     `completed` proof: `membership_expected` → keep waiting for the head
     item (the final row precedes the event, so it is already visible —
     assert this in the test — and the next turn delivers it); ID-less →
     one `drain_available_outbox_values` pass, return; (3) terminal
     non-completed → one final drain (today's `:242-243`), return the
     status with what was drained; (4) `pending_wrapper_lost_error`,
     `emitted_result_seen`, `materialized_completed` early return (now
     membership-aware: deliver the head item, or single-drain for ID-less)
     kept; the terminal-less quiet rule `:270-284` and the deadline
     visible-result rule `:286-295` kept **only when not
     `membership_expected`**; delete `completed_at`, the grace drain
     `:250-262`, the grace clamps `:302-322`. The
     `drain_available_outbox_values` call at `:190-194` runs only when not
     `membership_expected`.
   - Forbidden: any wait after completion proof; any drain of a
     Consumer-family outbox before proof; a decoder for keyless events
     beyond the existing single non-blocking drain for ID-less producers;
     non-destructive peek tracking.
   - Tests:
     - red first (Codex round-1 C3 scenario): the item's final membership
       row naming `[a]` **and** an unnamed row `b` both visible before
       waiting begins → result is `a` only, `b` remains, elapsed
       `< WEFT_COMPLETED_RESULT_GRACE_SECONDS` (today: both are drained and
       the call waits the grace).
     - row and final membership row visible, event never arrives → value
       delivered at once (event loss is not delivery loss).
     - row visible, no membership, no event → `CommandTimeoutError`, row
       untouched (register row 17; today: returned after the quiet rule).
     - `work_failed` with an unnamed partial row visible → `("failed",
       partial, error)` as today.
     - ctrl_out terminal envelope only for a pipeline TID → visible rows
       drained once, `completed`; pipeline result
       (`test_result_reads_pipeline_outbox_by_pipeline_tid` `:2226`) stays
       green.
     - `test_await_single_result_reuses_materialized_completion_state`
       `:2140`, `test_await_single_result_zero_timeout_does_not_wait_on_partial_stream`
       `:932` stay green; `test_await_single_result_returns_visible_one_shot_result_at_deadline`
       `:903` is rewritten for the ID-less path and gets a
       membership-expected twin asserting the timeout.
   - Done when: those pass; `tests/commands/test_run_public.py`,
     `tests/commands/test_run.py -q` stay green;
     `ruff check --extend-select RUF100 weft/commands/_result_wait.py` passes.

7. **Queue-name contract: the membership queue joins the standard task-local
   set everywhere the outbox is data-bearing.**
   - Outcome: `T{tid}.results` is discovered, classified, retained, and
     cleaned exactly like `T{tid}.outbox`; it appears in `weft queue list`
     and `weft system dump`; pipelines never chain it.
   - Files: `weft/core/monitor/policies/dead_task.py`,
     `weft/core/monitor/policies/runtime_control.py`,
     `weft/core/monitor/task_monitor.py`; `tests/tasks/test_task_monitor.py`,
     `tests/core/` policy tests for the two modules, `tests/commands/test_queue.py`,
     the dump/load test file, `tests/core/test_pipelines.py` (or the
     existing pipeline test file).
   - Read first: [OBS.13.9] delta; `dead_task.py:60-135`;
     `runtime_control.py:214-259`, `:443-471`; `task_monitor.py:2213-2219`,
     `:3835-3841`, `:4202-4210`; `dump.py:57`; `pipelines.py:365-380`,
     `:505-520`, `:575-585`.
   - Approach: `standard_dead_task_retention_queue_names` returns
     `(outbox, results, reserved)`; `dead_task_queue_cleanup_plan`'s
     `outbox_queue_names` filter includes the results suffix (results are
     counted with outbox queues — no new counter);
     `terminal_task_runtime_queue_cleanup_plan` `:250-252` adds
     `f"T{record.tid}.{QUEUE_RESULTS_SUFFIX}"` to `outbox_queue_names`
     under the same `retention_eligible` gate; the retention and
     data-bearing suffix sets `:454-471` gain the suffix; the role map
     `:2215` gains `QUEUE_RESULTS_SUFFIX: "results"`; both discovery
     pattern tuples gain `f"T*.{QUEUE_RESULTS_SUFFIX}"`;
     `standard_task_queue_identity` needs no change (it reads the constant).
   - Forbidden: a new PONG/`weft status` field; a new cleanup policy
     identity ([OBS.13.12] keeps four); any Monitor store change; a
     results-queue rule that differs from the outbox's.
   - Tests: `standard_task_queue_identity("T1.results") == ("1", "results")`;
     dead-TID plan includes the results queue only when retention-eligible
     and counts it in `outbox_queue_names`; terminal runtime plan likewise;
     `_has_retention_deferred_only_work({"results"})` is `True`; a supervised
     monitor cycle (`_run_builtin_cycle_worker`, as `test_task_monitor.py:442`)
     deletes a dead TID's `T{tid}.results` after retention and leaves it
     before; `weft queue list` (`cmd_queue_list`) lists `T{tid}.results`
     with its row count; `weft system dump` output contains the queue and
     `system load` restores it; a two-stage pipeline's stage `.results`
     queues are never read by the edge or the next stage (their rows
     remain after the pipeline completes) and the pipeline result is
     unaffected; `queue_suffixes()` strategy yields the new suffix.
   - Done when: those pass and `tests/tasks/test_task_monitor.py -q` stays
     green.

8. **End-to-end proof through real Manager/Consumer/TaskMonitor paths
   under shipping defaults, and the cutover sequence in-process.**
   - Files: `tests/core/test_client.py` (or `tests/commands/test_run_public.py`),
     `tests/cli/test_cli_long_session.py`, `tests/long_session_surface_benchmark.py`,
     `tests/tasks/test_task_execution.py` (test D).
   - `WeftTestHarness` test A (shipping-default collation): start the manager
     with `WEFT_TASK_MONITOR_ENABLED=1` (the harness default is `0`,
     `weft_harness.py:671`; `tests/cli/test_cli_serve.py:535-543` shows the
     env override), mode `delete` (default), interval default; persistent
     function task (`tests/helpers/long_session_utils.py:135`
     `write_persistent_spec`); write two inputs; wait until both
     `work_item_completed` events are visible; force a monitor cycle by
     writing any message to the supervised monitor's inbox
     (`task_monitor.py:1603-1610`; TID from `weft.state.services`) and wait
     for its store checkpoint or PONG cycle count to advance; assert both
     completion rows are **gone** from `weft.log.tasks` (collated as today)
     and both membership rows are still on `T{tid}.results`; two
     `Task.result()` calls → two distinct values in order; the results
     queue is then empty; a third call with a short timeout raises
     `CommandTimeoutError`; `weft task stop` → a final call reports
     `cancelled`. Do **not** raise the monitor interval — collation racing
     delivery is the thing under test, and it must not matter.
   - Harness test B (live command streaming, persistent `stream_output:
     true`, `sys.executable` script printing two lines): `weft result TID
     --stream` renders the item's chunks once and returns the stripped
     stdout; a second input yields a second result.
   - Harness test C (cutover detection): write a keyless
     `work_item_completed` for a running persistent task directly into
     `weft.log.tasks` with a row in its outbox and nothing on its results
     queue → `weft result TID` exits 1 with the cutover message naming both
     the running and the completed-but-unread case; rows untouched. Variant:
     a keyless one-shot `work_completed` for a completed task with an unread
     outbox row (F3) → same error. Variant: same event with an empty outbox
     → no error (times out).
   - Test D (cutover **sequence**, R2-5, R3-10 / Claude 3 — **in-process
     real Consumer with real control queues**; Manager launch is **not**
     exercised, because a spawned child does not inherit a test-process
     patch, `launcher.py:167`): construct a real persistent `Consumer` on
     `broker_env` with `_flush_membership_row` patched to a no-op and
     `_report_state_change` wrapped to drop `membership_rows` (constant-shape
     patches on the real producer simulating the previous release, §6); a
     controlled runner that blocks on a threading event; drive with
     `_drive_consumer_until`; write an inbox item; while it is active write
     `STOP` to `T{tid}.ctrl_in`; release the runner; assert the order the
     old release produces — a keyless `work_item_completed` is written
     **after** the STOP request row and before the `control_stop` terminal
     event (`consumer.py:511-527`; the deferred STOP event is
     `control_stop`, not `work_cancelled`); then run the documented
     procedure against that broker: the task is terminal; drain with the
     ID-agnostic bulk reader (`weft result --all` through `cmd_result`,
     standard outbox name), assert the outbox is empty, and assert that a
     new-reader `weft result TID` afterwards reports `cancelled`, not
     `ResultCutoverError`. A negative twin drains **before** the STOP and
     asserts the stranded batch surfaces as `ResultCutoverError` after the
     stop, documenting why the order matters.
   - Remove the two `time.sleep(WEFT_COMPLETED_RESULT_GRACE_SECONDS + 0.15)`
     lines and their comments (`tests/cli/test_cli_long_session.py:339-341`,
     `tests/long_session_surface_benchmark.py:998-1000`): terminal status
     visible now implies membership visible ([OBS.18]).
   - Done when: `./.venv/bin/python -m pytest tests/cli/test_cli_long_session.py -m "" -q`
     passes on SQLite **and** the exact-ID reader tests of tasks 4–6 pass
     under `bin/pytest-pg` (`read_one(exact_timestamp=…)`, `peek_many(
     with_timestamps=True)`, and `delete(message_id=…)` under the
     PostgreSQL backend).

9. **Constant, docs, CHANGELOG, suppressions.**
   - `weft/_constants.py:1898-1899`: keep `WEFT_COMPLETED_RESULT_GRACE_SECONDS`
     (surviving users: the ID-less one-shot quiet rule and the realtime
     events surface); docstring: "Quiet window a one-shot result waiter
     allows before returning a visible final result from an ID-less
     producer (pipelines) that has no terminal evidence, and the
     post-terminal result budget the realtime events surface
     (`weft/commands/events.py`) still grants. Result waits for
     Consumer-family tasks do not use it: membership rows on
     `T{tid}.results` name each item's outbox rows ([MF-2])."
     `tests/system/test_constants.py:329` unchanged.
   - `CHANGELOG.md` under `## Unreleased` → `### Changed` (breaking):
     "Consumer tasks now own a sixth standard task-local queue,
     `T{tid}.results`, holding membership rows that name the outbox rows of
     each completed work item by message ID; `weft result TID` reads
     exactly those rows and no longer infers batches from row timestamps or
     a 0.5 s quiet period, which also fixes adjacent items being merged into
     one result and excludes rows other writers put on a shared outbox.
     Results return as soon as the item's membership is visible. A task
     whose outbox or membership write fails while publishing output now
     fails (`work_failed`) instead of continuing with undeliverable output.
     `work_item_completed` and Consumer `work_completed` events carry
     `membership_rows`. `T{tid}.results` is listed by `weft queue list`,
     included in `weft system dump`, and cleaned with `T{tid}.outbox`.
     **Cutover, in this order:** `weft manager stop`; stop every running
     persistent and interactive task (`weft task list --status running`,
     `weft task stop TID`); wait for terminal status; with the previous
     release still installed drain every unread result per task with
     `weft result TID` (repeat until it times out; `weft result --all` does
     not cover custom outbox names), including already-completed tasks
     whose results were never read; then upgrade and `weft manager start`.
     Completion events written before the upgrade carry no membership, and
     `weft result TID` reports `ResultCutoverError` when such a task still
     has unread rows; rows left behind remain readable with `weft queue
     read T{tid}.outbox` or `weft result --all`. Rolling back leaves
     `T{tid}.results` queues that the previous release does not clean up
     automatically; remove them with `weft queue delete`. Never delete the
     broker database. Retired tests: the quiet-visible-output and the
     three boundary-skew result tests."
   - `docs/agent-context/runbooks/runtime-and-context-patterns.md` §8
     (`:152-160`): rewrite to "membership rows on `T{tid}.results` name
     each item's outbox rows; result readers consume by exact message ID;
     do not add timing sleeps between a completion event and a result
     read. The realtime events surface still keeps a short post-terminal
     budget."
   - Range-language purge (Codex R3-12): after editing, `grep -n -i
     "outbox range\|by range\|outbox_first_id\|outbox_last_id\|outbox_runs"
     docs/agent-context docs/specifications weft CHANGELOG.md` must return
     nothing outside `docs/plans/`.
   - `docs/ruff-suppression-registry.md`: after tasks 5–6 run
     `./.venv/bin/ruff check --extend-select RUF100 weft/commands/result.py weft/commands/_result_wait.py`;
     for each `# noqa: C901` reported unused (RUFF-SUP-106/108/109), remove
     the pragma, retire the registry row; add the row for the BLE001
     boundary catch in task 3 (`_commit_work_outcome`); regenerate with
     `./.venv/bin/python bin/ruff_suppression_index.py --write`.
   - `docs/lessons.md`: one entry only if implementation exposes a repeated
     pattern (the 2026-04-09 "Completion Grace" entry is history).

10. **Traceability reconciliation and landing gates.** Add `Spec: [MF-2]
    [OBS.18]` to `_write_outbox_row`, `_flush_membership_row`,
    `_finalize_message`, `_commit_work_outcome`, and the interactive
    finalize docstring; `Spec: [MF-5]` to `next_membership_item`,
    `deliver_membership_item`, `completion_event_marker`,
    `decode_membership_row`, `_await_single_result`, `await_one_shot_result`;
    `Spec: [OBS.13.9]` to the two policy functions that gain the suffix;
    confirm the mapping additions from the delta table are present; close
    the Deviation Log; rerun `tests/specs`, `bin/check-doc-paths`, the
    final gates (§7), and the backstitch gate; from a clean worktree record
    the pre-landing identifier (parent SHA of the recording commit and the
    plan-excluded diff stat, per Spec Baseline); land as one change; then
    record the landing SHA in the Revision Log in a separate docs-only
    commit.

## 6. Testing Plan

- Harnesses: `broker_env` + real `Consumer` for the producer and for the
  in-process cutover sequence; `build_context` real queues for the waiters;
  `broker_env` + real `TaskMonitor._run_builtin_cycle_worker` for queue
  discovery/cleanup; `WeftTestHarness` (monitor enabled) for the end-to-end
  proofs. Fake runners are allowed only to shape outcomes (multi-output,
  zero-output, blocking-until-released).
- Do not mock: `Queue.write`/`read_one`/`peek_one`/`peek_many`/`delete`,
  `_report_state_change`, `_finalize_message`, `_commit_work_outcome`,
  `_finalize_deferred_active_control`, `poll_log_events`,
  `drain_ctrl_out_stream_messages`, `QueueChangeMonitor`, the
  Manager/Consumer/TaskMonitor lifecycle in task 8. **Named exceptions**,
  all failure injection or constant patches with everything else real:
  (1) the task-3 output-write-failure test wraps the outbox queue's `write`
  to raise `BrokerError` once; (2) the task-3 membership-write-failure
  tests wrap the results queue's `write` to raise `BrokerError` once (mid
  and final); (3) the task-3 interactive final-envelope test wraps the
  outbox `write` to raise once on the final envelope; (4) constant patches
  of `RESULT_MEMBERSHIP_CHUNK_IDS` and a small `WEFT_MAX_MESSAGE_SIZE`
  (bounds, not behaviour); (5) the task-8 test D patches that make the real
  producer skip membership rows and drop `membership_rows` (simulating the
  previous release); (6) the task-3 interleaving test's wrapper that
  performs one real foreign `write` between the task's own writes.
- Red-green order: task 3 membership test (no results queue at HEAD) →
  task 3 interleaving test → task 3 output-write-failure-with-STOP test
  (no `work_failed` at HEAD) → task 3 interactive mid-session test → task
  5 three-item test (merges at HEAD) → task 5 cutover test (returns rows
  at HEAD) → task 6 unnamed-row test (includes the row and waits the
  grace at HEAD) → task 7 dead-TID results cleanup test (queue leaks at
  HEAD because the suffix is unknown) → task 8 test D negative twin.
- Enumerable contract elements with firing tests: the queue name
  `T{tid}.results` and `QUEUE_RESULTS_SUFFIX` (constant pin, identity
  parse, list, dump/load, cleanup plans, discovery, pipeline
  non-chaining); membership row fields (`type`, `message_id`, `seq`,
  `final`, `outbox_message_ids`) and chunking (`RESULT_MEMBERSHIP_CHUNK_IDS`
  pin, chunk test, clamp test); `membership_rows` on `work_item_completed`
  and `work_completed` (plain, chunked, live-streamed, spilled, agent
  multi-output, empty, interactive); marker absent on failure events;
  reader rules (named-row-only delivery, FIFO, skip consumed, partial
  error with row retained, malformed-row error, item-before-terminal,
  cutover error with exit code 1 for both the running and the
  completed-but-unread case and no error after a drain, pipeline and
  pipeline_edge exemption, in-progress timeout, KILL, deadline); one-shot
  membership-first delivery, event-less delivery, ID-less single drain,
  failed-status final drain, unnamed row untouched; publication failure →
  `work_failed` with one terminal event, one reserved disposition, one
  ACK for STOP and for KILL; interactive single failure path (mid-session,
  final membership, final envelope); materialization field; CHANGELOG-
  declared break (cutover tests C and D).
- Edge cases deliberately out: a second reader racing on the same item
  (excluded by [OBS.18] one consumer — Open Owner Question 3; any partial
  outcome is detectable and reported); PostgreSQL general row visibility
  (the exact-ID and delete paths are exercised under `bin/pytest-pg`).
- Post-deploy observation: `weft queue peek T{tid}.results` shows one
  final membership row per unread item; `weft queue peek weft.log.tasks`
  shows `membership_rows` on completion events; a persistent service read
  with `weft result TID` in a loop never returns two inputs' outputs in
  one call; `weft result TID` latency after an item completes drops below
  the poll interval regardless of monitor timing.

## 7. Verification and Gates

Per task (load the repo toolchain first: `. ./.envrc`):

```bash
./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/tasks/test_task_interactive.py tests/tasks/test_agent_execution.py -q
./.venv/bin/python -m pytest tests/core/test_outbox.py tests/system/test_constants.py tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/commands/test_result.py tests/commands/test_queue.py tests/commands/test_run.py tests/commands/test_run_public.py tests/core/test_client.py -q
./.venv/bin/python -m pytest tests/cli/test_cli_long_session.py -m "" -q
bin/pytest-pg tests/commands/test_result.py tests/tasks/test_task_monitor.py -q
./.venv/bin/python -m pytest tests/specs -q && bin/check-doc-paths
./.venv/bin/ruff check --extend-select RUF100 weft/commands/result.py weft/commands/_result_wait.py
grep -rn -i "outbox range\|by range\|outbox_first_id\|outbox_last_id\|outbox_runs" docs/agent-context docs/specifications weft CHANGELOG.md ; test $? -eq 1
```

Final gates (full blast radius — the reader is shared by `weft run`,
`weft result`, and the client; the queue-name set touches every cleanup
policy):

```bash
./.venv/bin/python -m pytest -m "" -q
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python bin/ruff_suppression_index.py --write && git diff --exit-code docs/ruff-suppression-registry.md
# backstitch traceability gate (sibling checkout ../backstitch; weft is its external corpus)
(cd ../backstitch && .venv/bin/python -m backstitch check --repo-root /Users/van/Developer/weft --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --format json --output /tmp/weft-spec-trace.json)
(cd ../backstitch && .venv/bin/python -m pytest tests/test_weft_corpus_traceability.py -q)
```

The backstitch corpus test asserts the weft error set equals a known
baseline (`../backstitch/tests/test_weft_corpus_traceability.py:27-44`,
`:84-121`; it includes an existing `SPEC_ANCHOR_MISSING` for
`weft/core/tasks/consumer.py`). This plan must add **no** new error or
warning signature; if the baseline changes because existing debt was
resolved by these edits, report it — do not edit the baseline from weft.

Rollout: one atomic landing after the operator cutover note is in
CHANGELOG. Rollback: revert the landing commit; the old reader ignores the
marker and the results queues; never delete the broker database (§4).

## 8. Independent Review Loop

External review will be run by the top-level agent using a different agent
family (Codex CLI is available on this machine) plus a Claude reviewer,
with the Planning Review Prompt from
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` (§"Planning
Review Prompt", verdict `PASS`/`BLOCKED`). Reviewers must read: this plan
including `## Proposed Spec Delta`, `## Review Record`, and `## Open Owner
Questions`; 00 `:8-17`, `:87`; 05 [MF-2] `:100-152`, [MF-5] `:594-604` and
`:795-865`; 07 [OBS.13.3] `:373-384`, [OBS.13.9] `:464-476`, [OBS.14]
`:508-511`, [OBS.17] `:519-526`, [IMPL.2] `:599-600`, `:937-948`; 13
[AR-4.1]; 04 [SB-0.2]; 02 `:163`; 10 `:930-931`;
`weft/_constants.py:885-935, :2222, :2252`; `weft/helpers/__init__.py:145-156`;
`weft/core/tasks/consumer.py:120-160, :227-300, :489-600, :690-751, :797-1000, :1257-1261`;
`weft/core/tasks/base.py:270-290, :372-400, :720-815, :1106-1200, :1297-1330, :1786-1895, :2071-2110, :2792-2846`;
`weft/core/tasks/interactive.py:270-450`; `weft/core/taskspec/model.py:1490-1520`;
`weft/core/tasks/heartbeat.py:264-290`;
`weft/commands/result.py:63-76, :175-338, :356-716, :799-870`;
`weft/commands/_result_wait.py:114-330`; `weft/commands/_task_history.py:48-53`;
`weft/commands/dump.py:57`; `weft/commands/queue.py:495-524`;
`weft/core/monitor/policies/dead_task.py:60-135`;
`weft/core/monitor/policies/runtime_control.py:214-259, :443-471`;
`weft/core/monitor/task_monitor.py:2213-2219, :3835-3841, :4202-4210`;
`weft/core/pipelines.py:365-380, :505-520, :575-585`;
`weft/core/launcher.py:150-175`; `tests/commands/test_result.py:1453-1990, :2140-2224`;
`tests/tasks/test_task_execution.py:3387-3410`; the probe
`scratchpad/plans/09-rev3/probe_membership_queue.py`. Stance: (1) find any
outbox row a Consumer-family task writes that no membership row would
name, or any named row written after its final membership row (breaks
[OBS.18]); (2) find any current producer of
`work_item_completed`/`work_completed` besides `Consumer`, the interactive
mixin, and the pipeline tasks; (3) find a cleanup path that deletes a
`T{tid}.results` queue or row before its outbox, or one that never deletes
it (a leak); (4) break the failure paths: a state where the task neither
publishes nor fails, a double finalization, or a deferred STOP/KILL that
transitions before `mark_failed`; (5) check the one-shot membership-first
argument against `materialized_completed` and `pending_wrapper_lost_error`;
(6) check the in-process cutover test against what the previous release's
Consumer really does on STOP; (7) check the queue-name contract sweep for
a missed surface (harness, dump/load, prune, pipelines, PONG counters);
(8) look for performative ceremony to remove. Feedback is appended to
`## Review Record` with a disposition per finding before landing.

## 9. Out of Scope

- `weft/commands/events.py` realtime result grace (`:239`, `:436-450`,
  `:472-490`) and `tests/core/test_ops_shared.py:279` — a follow-up may use
  membership rows for non-consuming exact peeks (Open Owner Question 6).
- Pipeline/edge membership (Open Owner Question 4).
- A separate PONG counter for results-queue cleanup (Open Owner Question 5).
- `weft result --all`/`--peek` semantics; `_claimed_result_blockage`;
  `result_without_terminal`; `weft system prune` families.
- Interactive attach/`weft run --interactive` rendering paths.
- Live rendering of persistent `--stream` chunks before the item is
  complete (renders at delivery, unchanged).
- Automatic pre-upgrade detection of running old Consumers (the cutover is
  an operator procedure plus the reader-side error).

## 10. Fresh-Eyes Review

Author pass 1 (2026-09-08, first draft), findings by severity — kept for
the record; several were superseded by later rounds (Open Owner Question
numbers in passes 1–3 refer to the numbering of those revisions):

1. **High — hidden coupling not in the evidence file.** TaskMonitor `delete`
   mode exact-deletes `work_item_completed` rows of running persistent tasks
   in the fold pass. First draft proposed accepting time-sensitive delivery;
   the review rejected that as a diminution. Now moot: membership does not
   live in `weft.log.tasks`.
2. **High — one-shot ID-less completion proof.** Kept the single
   non-blocking drain for `ctrl_out`/pipeline proof.
3. **Medium — interactive ordering.** Final stdout envelope after
   `work_completed`; reorder in task 3.
4. **Medium — batch skipping.** Empty/consumed items must not block later
   items; specified and pinned.
5. **Medium — terminal-vs-batch race.** Item-before-terminal delivery
   (register row 8).
6. **Low — event-shape pins.** `result_bytes` assertions kept.
7. **Low — ruff suppressions.** Registry step in task 9.

Author pass 2 (2026-09-08, after applying the round-1 review):

1. **High — checkpoint pin.** Superseded (round 2, then removed in
   revision 4).
2. **Medium — empty-vs-consumed batch.** Both skipped; one return shape.
3. **Medium — publication failure re-entrancy.** `_finalize_terminal_outcome`
   raises; `_handle_active_work_result` swallows after a terminal
   transition (`:531-541`).
4. **Low — PostgreSQL.** `bin/pytest-pg` gate for the result tests.
5. **Low — backstitch baseline.** `consumer.py` carries an existing
   `SPEC_ANCHOR_MISSING` debt; this plan must not change the baseline.

Author pass 3 (2026-09-08, after applying the round-2 review):

1. **High — the cap is a real cliff for one producer.** Recorded as a
   class-D row; round 3 showed it is a cliff for every unbounded producer;
   resolved in revision 4 by moving membership off the event.
2. **High — the fold-time mark must not be lost by a re-fold.** Moot
   (mechanism removed).
3. **Medium — re-check ordering within a cycle.** Moot.
4. **Medium — `core` must not import `commands`.** The decoder lives in
   `weft/core/outbox.py`; kept.
5. **Low — schema rollback.** Moot (no schema change).
6. **Low — task 8 test D honesty.** Now an in-process real-Consumer test
   with an explicit "Manager launch not exercised" statement.

Author pass 4 (2026-09-08, after applying the round-3 review), separate
read of the revised text:

1. **High — the brief's run encoding fails exactness.** The producer cannot
   observe a foreign row between its own writes, so `[[first, last]]` runs
   silently reintroduce the round-2 defect (§1 probe (1)). Replaced by
   explicit ID lists chunked across membership rows, which are exact and
   unbounded; said so plainly in §4 and OOQ 1.
2. **High — `mark_failed` must precede the deferred reply.** Verified
   `_handle_stop_request` skips its transition when terminal and
   `_finalize_deferred_active_control` then sends only the ACK, so the fix
   is an ordering change inside `_finalize_work_exception`, not a new
   helper. Wrote the STOP/KILL + `clear` tests with one-event/one-
   disposition assertions.
3. **High — a results queue with an unknown suffix would leak.**
   `standard_task_queue_identity` rejects unknown suffixes, so without task
   7 dead-TID cleanup would never see `T{tid}.results`. Task 7 enumerates
   every suffix-driven site and the red test is the leak.
4. **Medium — one-shot family predicate.** Built-in service tasks share
   `spec.type` values with Consumers and never emit `work_completed`; a
   `weft result` on a service TID previously returned rows only through the
   quiet-period heuristic the owner removed (register row 3), so
   `membership_expected = not pipeline-family` is sufficient and honest;
   recorded in the register (row 17).
5. **Medium — `weft result --all` and the cutover.** It scans `T*.outbox`
   only; the procedure now drains per TID and the CHANGELOG says why.
6. **Low — the completion event keeps `membership_rows`.** Decided with
   the false-positive analysis in §4 rather than by taste; the reader never
   uses it for membership.
7. **Low — dangling membership rows.** Pruned or moved outbox rows leave
   rows on `T{tid}.results`; they are skipped and deleted on read and
   removed with the outbox; no prune change needed.

## Observable-Difference Register

Verified against the shipping default configuration (SQLite broker,
manager-supervised TaskMonitor in `delete` mode with immediate first cycle,
LivenessMonitor on).

| # | Observable change | Class | Notes |
|---|-------------------|-------|-------|
| 1 | Persistent item values delivered when the item's membership is visible | N | Same values, same consuming behaviour, same `--stream` rendering point |
| 2 | Adjacent items merged into one result (reproduced 3-batch defect) | C | Fixed by named-row delivery |
| 3 | Rows returned after a 0.5 s quiet period with no boundary (includes `weft result` on service TIDs, which never emit completion proof) | A | Owner 2026-09-08: the quiet-period heuristic goes away; rows readable via `weft queue read`/`weft result --all` |
| 4 | Deadline drain of unnamed visible rows reported as `completed` (`result.py:629-648`) | C | Removes a misreport; timeout with rows untouched |
| 5 | Pre-upgrade completion events act as boundaries; skewed/late-polled events tolerated; completed-but-unread pre-upgrade results | A | Owner: no legacy branch; explicit `ResultCutoverError` only while unread rows remain + quiesce-first CHANGELOG procedure; `weft result --all` still reads them after upgrade |
| 6 | `weft result TID` for an item whose completion row was collated by the TaskMonitor (0–300 s after the event under defaults) | **C** (was D in round 1) | Today: delivered merged with other pending batches. After: membership lives on `T{tid}.results`; delivered item-precise regardless of monitor timing; no Monitor change |
| 7 | A new standard task-local queue `T{tid}.results` per Consumer-family task (visible in `weft queue list`, `weft system dump`, harness snapshots; retained and cleaned with the outbox; counted with outbox queues in runtime-cleanup counters) | C | One membership row per unread item plus chunk rows while an item is in flight; ≤ 86 KB per row; bounded by unread outbox rows; removed at retention age |
| 8 | Completed item visible in the same poll as a terminal non-completed event | C | Delivered first; terminal status on the next call (today hidden by `:589`) |
| 9 | Item whose outbox or membership write fails during publication | C | Today: exception escapes the reactor turn with the task still `running` and no `work_failed`. After: `work_failed` written once, reserved error policy applied once, deferred STOP/KILL answered with an ACK only |
| 10 | One-shot result latency after completion | C | ≤ poll interval after the final membership row instead of ≥ 0.5 s after the event; delivered even if the event write was lost |
| 11 | One-shot wait with the membership and an unnamed foreign row both visible at start | C | Row left in the outbox instead of merged into the result |
| 12 | Rows written by a foreign writer after a Consumer `work_completed`, or **between** an item's rows on a shared/custom outbox | C | Not delivered as that item's result (today they are merged in); readable via bulk surfaces; shared/custom outbox routing itself is unchanged |
| 13 | Interactive final stdout envelope precedes the session `work_completed`; a failed envelope write is logged and does not suppress the terminal event; a failed chunk or membership write terminates the session as `work_failed` | N / C | Externally identical output on success; closes a race; today a mid-session write failure escapes the reactor with the task left `running` |
| 14 | `weft task events --json`, Django realtime, Monitor tables, `weft status` | N | Fixed-key projections |
| 15 | `weft queue peek weft.log.tasks` shows `membership_rows` on completion events | C | Additive; one small integer |
| 16 | `weft result --all`, `--peek`, pipeline results, `result_without_terminal`, claimed-residue handling | N | Untouched; membership rows are not consulted or removed by bulk reads (a later `weft result TID` skips and deletes them) |
| 17 | One-shot Consumer task whose rows are visible with no membership and no terminal proof (crashed producer, or in-progress) | C | Today: returned as `completed` after the quiet rule. After: timeout; rows readable via bulk surfaces; the `result_without_terminal` prune class still harvests them |
| 18 | Named-row read returning some but not all rows | C | `PartialResultError` naming the missing IDs and the membership row instead of a silently truncated value |
| 19 | Monitor cost | N | No retention probe, no re-check, no schema change; the results queue is one more pattern in two discovery snapshots |
| 20 | Unbounded producers (interactive sessions, long live streams, large agent outputs) | N | No row-count cap; chunked membership rows scale with output |
| 21 | `WEFT_MAX_MESSAGE_SIZE` lowered below the default | N | The membership chunk clamps to the effective limit at task start |
| 22 | Rollback to the previous release | C | Old reader ignores the marker and the results queues; leftover `T{tid}.results` queues are not cleaned automatically by the old release (CHANGELOG: `weft queue delete`); never delete the broker database |
| 23 | Pipeline stage `T{tid}.results` queues | N | Written by stage Consumers, never chained or read by edges; cleaned with the stage outbox |
| 24 | A non-membership row on `T{tid}.results` | C | `weft result TID` reports an error naming the row instead of hanging; only a foreign write can produce it |

## Open Owner Questions

| # | Question | Recommended default (written into the delta and tasks) | Alternatives the owner may pick |
|---|----------|---------------------------------------------------------|---------------------------------|
| 1 | **Membership representation.** Where and how does the producer name an item's outbox rows? Comparison (exactness under interleaving / no-diminution for unbounded producers / complexity): | **Task-owned membership queue `T{tid}.results`** with explicit ID lists chunked across rows — exact: yes (§1 probe (2)); unbounded: yes (rows, not a cap); complexity: one new standard queue suffix and its policy-set additions (task 7), no Monitor change, no schema change. | (a) **Event-borne list + `RESULT_OUTBOX_ID_LIMIT`** (round 3) — exact: yes; unbounded: **no** (class-D cliff; ~67 min of a 100-line/s interactive session; ceiling shrinks with the TaskSpec dump and `WEFT_MAX_MESSAGE_SIZE`); complexity: no new queue but the whole Monitor retention mechanism (schema v7, [OBS.19], four deletion paths, re-check cursor). (b) **Event-borne range + `outbox_row_count`** — exact: **no** on shared/custom outboxes (`PartialResultError` on mismatch, best-effort otherwise); unbounded: yes; complexity: as (a) minus the cap, plus the Monitor mechanism. Run encoding (`outbox_runs`) on the membership queue was evaluated and rejected: not exact (§1 probe (1)). |
| 2 | **Cutover mode.** | Quiesce-first at Manager level: `weft manager stop` → stop tasks → wait for terminal → drain per TID with the **previous** release's `weft result TID` → upgrade → `weft manager start`; `ResultCutoverError` fires only while unread rows remain for a keyless event; test D proves the STOP ordering in-process. | Producer-first staged release (old reader tolerates both shapes for one release) — rejected by the no-shim rule; listed only so the owner can overrule. |
| 3 | **One result consumer per task.** | Standing invariant in [OBS.18]: one consumer reads a task's membership rows at a time; `read_one(exact_timestamp=…)` is atomic per row; a partial read raises `PartialResultError` naming the row and missing IDs and leaves the membership row in place for the operator. | Accept concurrent readers by making `PartialResultError` a soft outcome (deliver what was read, delete the row) — not recommended; hides loss. |
| 4 | **ID-less completion proof on one-shot waits / pipelines.** | Keep the spec's existing single non-blocking drain for typed terminal `ctrl_out` envelopes and pipeline-family `work_completed` (roles `pipeline` and `pipeline_edge`); pipelines write no membership at the pipeline TID; a follow-up may have the exit edge write membership for moved rows. | Extend pipelines now — out of scope for this plan. |
| 5 | **Results-queue cleanup visibility.** | Results queues are counted with outbox queues in the existing runtime-cleanup counters (`dead_tid_outbox_queues_deleted`, `outbox_queue_names`); no new PONG/`weft status` field. | A separate `results` counter and plan field — one more enumerable contract element with a firing test; not in this plan. |
| 6 | **`weft/commands/events.py` grace waits.** | Follow-up that peeks the named IDs on `T{tid}.results` instead of polling the outbox for up to 0.5 s after a terminal event; the constant stays with the retargeted docstring. | Fold into this plan — widens blast radius; not recommended. |

## Review Record (append-only)

**2026-09-08 — round 1, cross-family review of revision 1 (Codex:
BLOCKED, 10 findings; Claude: PASS, 4 findings + two verifications). Every
finding was reproduced against `178e3a34` before disposition.**

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| C1 — TaskMonitor deletes the new result authority under defaults; first cycle is immediate, not "after 300 s"; task 7 masked the race | Accepted. Reproduced: no age gate `:2586-2615`, `rows_to_delete = tuple(selected_rows)` `:2629`, first cycle `:666/:746/:1507`, catch-up 2 s. Design changed: [OBS.19] retention gate with checkpoint pin (task 4); shipping-default harness test with a forced cycle (task 8); register row 6 reclassified C | §4, delta (05/07), tasks 4, 8, register, OOQ1 |
| C2a — per-row ID list is unbounded against [IMPL.2] | Accepted. Representation changed to a contiguous range; one-writer evidence: six write sites, all the task's reactor thread; `MAX_MESSAGE_SIZE` 10 MiB | §1, §3 item 7, §4, [MF-2] delta, [OBS.18] |
| C2b — completion-event write is best-effort (`base.py:1808-1814`), so membership can be silently lost | Accepted. `result_bearing` retry + `ResultPublicationError` → task `failed` via `_finalize_terminal_outcome(timestamp=None)`; contrasted with the best-effort reserved ack `:828-840`; tests for both injections | §4, [MF-2] delta, task 3, §6 |
| C3 — one-shot drains before it polls (`_result_wait.py:190` vs `:223`), contradicting the proposed exact contract and its test | Accepted. Loop reordered to poll-before-drain; contract narrowed and stated ("rows consumed before the proof are delivered with it; same rows under [OBS.18]"); the both-visible-at-start test added | §4, [MF-5] delta, task 7 |
| C4 — strategy A/D would make active specs false; backstitch gate missing | Accepted. Strategy B atomic for all four files; mapping additions in the delta table; backstitch `check` + corpus test added to §7 | Class line, delta table, slice order, §7, task 10 |
| C5 — a package upgrade does not replace running old Consumers | Accepted. Operator cutover procedure + `ResultCutoverError` from the reader on key-less non-pipeline completion events (no decoder); harness test C | §4, [MF-5] delta, tasks 5, 6, 8, 9 (CHANGELOG) |
| C6 — 07 `:941-943` (age-gated fold) contradicts [OBS.13.3]/code | Accepted. Reproduced: retention constant gates only recovery `:2756`, family retirement `:2490`, reserved/dead-TID cleanup. Delta rewrites `:940-943` and qualifies [OBS.13.3] | §4, delta (07) |
| C7 — validator accepting canonical strings is a second wire shape | Accepted. `type(v) is int and v > 0`, bools and strings rejected | §4, task 5 |
| C8 — monitor test suppressed the race; "item 1 fails, item 2 succeeds" impossible (`:964` terminal); Ruff gate lacked RUF100 | Accepted. Test A runs at default interval with a forced cycle; failure test replaced by the partial-write path; `--extend-select RUF100` in tasks 6, 7, 9 and §7 | tasks 3, 8, 9, §7 |
| C9 — partial batch reads lack an atomicity contract | Accepted as owner question with a recommended default: one consumer per task ([OBS.18]); `read_many` commits per call; `PartialResultError` when a bound is missing | §4, [OBS.18], task 5, OOQ3 |
| C10 — anchors and doc text (`:828`→`:830`, `:2839`→`:2842`, `:347-368`→`:349-372`, `:370-466`→`:374-464`, `:212-227`→`:210-226`, `update_from_task_log_row` in `collation.py:70-85`, six not five write sites, `events.py` still uses the grace, runtime runbook wording) | Accepted; every anchor re-verified and corrected; constant docstring and runbook text no longer claim the events surface stopped using the grace | §1, §3, task 9 |
| K1 — retention exposure is 0–300 s, not ≥ 300 s; task 7's interval bump was a real race | Accepted (same fix as C1); wording corrected everywhere | §3 Q3, §4, register row 6 |
| K2 — `base.py:2839`→`:2842`; `consumer.py:227-250`→`:227-249` | Accepted | §3 |
| K3 — §8 promised a Review Record the plan lacked | Accepted; this section | — |
| K4 — producer ordering is a candidate 07 invariant | Accepted; folded into [OBS.18] next to [OBS.14]/[OBS.17] | delta (07) |
| K — verifications (a) monitor fold-and-delete confirmed, (b) live streaming write order confirmed, `Queue.write -> int` confirmed | Recorded; used as evidence in §3 | — |

Declined: none. Out of scope: none. Both reviewers' baseline-quote checks
passed; no quoted spec text changed between rounds.

**2026-09-08 — round 2, cross-family review of revision 2 (Codex: FAIL,
8 findings R2-1..R2-8; Claude: PASS, 6 findings F1..F6 + probe results +
3 questions). Every finding was reproduced against `178e3a34` before
disposition; the round-2 probe is `scratchpad/plans/09-rev2/probe_interleave.py`.**

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| R2-1 (P1) — one writer per physical outbox is false (shared/custom outbox `02:163`, `base.py:379-382`; Heartbeat `heartbeat.py:264-290`; `weft queue write/move`); a foreign row inside `[first, last]` is consumed as the item's output; register covered only rows after completion | Accepted; C2a/K4 reopened. Reproduced: `_resolve_queue_names` honours `io.outputs["outbox"]`; heartbeat accepts any ordinary queue; the probe shows a range read returning the interleaved row and an exact-ID list not. Representation changed to the explicit bounded list `outbox_message_ids` with `RESULT_OUTBOX_ID_LIMIT` (recommended default); range + `outbox_row_count` kept as the owner's alternative (OOQ1); interleaving-writer tests added in tasks 3 and 6; register row 12 extended, row 21 added | §1, §3 item 7, §4, [MF-2]/[MF-5]/[OBS.18] delta, tasks 3, 5, 6, register, OOQ1/5 |
| R2-2 (P1) — a skipped completion row has no child ref; deleting a sibling marks the family `raw_deleted_at_ns` (`NOT EXISTS` refs); once terminal, orphan recovery deletes every raw row of the TID | Accepted. Reproduced: `reconcile_raw_deleted_tasks_for_tids` `sql.py:1045-1065`; `select_raw_deleted_task_log_recovery_tids` `sql.py:880-905`; `_recover_orphan_task_log_rows` `:4696-4699`. Retention redesigned: fold as today, mark the ref `result_pending_at_ns` (schema v7), every deletion path honours it; cross-family test added | §2, §4 [OBS.19], [MF-5] `:599-601` delta, [OBS.19] delta, task 4 |
| R2-3 (P1) — the checkpoint pin blocks summary readiness for every later terminal family (terminal cutoff **is** the checkpoint, `:2994`, `store.py:1800-1803`) and stalls at the scan limit into a 2 s loop; ODR omitted both; one-shot `work_completed` also retains | Accepted. Reproduced: `terminal_retention_seconds=0.0` at `:2994`; `progress_requires_catchup` = any `waypoint_reached`. Pin withdrawn; checkpoint advances as today; re-check step bounded by `batch_size` with `waypoint_reached=False` and `blocked_reason="result_pending_batch_limit"`; summary-readiness and bound tests added; register rows 7/19 rewritten to cover one-shot rows and the absence of global degradation | §4 [OBS.19], task 4, register 7/19, OOQ2 |
| R2-4 (P2) — `raw_external` ownership bypasses [OBS.19] (`:5641-5740` exports and deletes after the age gate without consulting the store) | Accepted. Reproduced. The `raw_external` loop applies the same probe and skips unread completion rows; test added; register row 24 | §4 [OBS.19], 07 `:940-943` delta, task 4, register 24 |
| R2-5 (P1) — the cutover drained before stopping; an old Consumer that gets STOP mid-item publishes an ID-less completion **after** the drain (`consumer.py:511-527`) | Accepted. Reproduced: deferred control at `:690-696`, ok path publishes then `_finalize_deferred_active_control` cancels. Procedure reordered to quiesce-first (stop → terminal → drain with the old release → upgrade); CHANGELOG text rewritten; harness test D proves the STOP ordering and runs the documented sequence with the smallest honest simulation of the old producer; negative twin | §4 cutover, task 8 (test D), task 9, register 5, OOQ3 |
| R2-6 (P1) — the partial-write test is not implementable: an outbox write exception in `_commit_work_outcome` is re-raised with the TaskSpec still `running`; no `work_failed` | Accepted. Reproduced the escape path: `:489-541` re-raises when not terminal → `_drain_worker_results` → `process_once` → `run_until_stopped` `finally`. Added one catch in `_commit_work_outcome` routing to `_finalize_work_exception(exc, timestamp)` (reserved policy applies), guarded by the terminal-status check so `ResultPublicationError` is not finalized twice; test rewritten with explicit assertions; register row 20 | §3 Q5, §4, [MF-2] delta, task 3, register 20 |
| R2-7 (P2) — no `delete_errors` catch exists for a gate `BrokerError`; the `:2531` outer catch excludes it | Accepted. Reproduced: `BrokerError` MRO is `Exception` only; `:2531` catches `(OSError, RuntimeError, ValueError)`. Explicit `except BrokerError` in the fold pass, the re-check, and the `raw_external` loop: record error, no deletion, no checkpoint write, `stop_reason="result_gate_error"`, retryable failed pass; tests for each | §4 error-path priorities, task 4 |
| R2-8 (P2) — a landing commit cannot record its own SHA | Accepted. Promotion baseline identifier = base SHA `178e3a34` + pre-landing branch head SHA and clean-worktree diff stat; landing SHA recorded afterwards by a docs-only commit | Spec Baseline, slice order, task 10 |
| F1 (P2) — the gate must apply only where a delete would happen; `report_only`/`jsonl_then_delete` re-fold and churn under the pin | Accepted. Reproduced `apply` at `:2441` and jsonl's malformed-only delete `:2630-2659`. Mark set only when `apply` is true; `report_only` unchanged; jsonl marks and the ref path honours the mark after summary; tests for both modes; pin withdrawn so no churn exists | §4 [OBS.19], task 4, register 23 |
| F2 (P3) — `is_pipeline_taskspec_payload` matches `role == "pipeline"` only; `PipelineEdgeTask` emits key-less `work_completed` (`pipeline.py:206`) → false cutover error | Accepted. Reproduced. `completion_outbox_ids` checks `metadata.role in {"pipeline", "pipeline_edge"}` inline; the shared helper keeps its meaning for its three other callers; test for both roles | §4 reader invariants, [MF-2]/[MF-5] delta, task 5 |
| F3 (P3) — a one-shot task completed before the upgrade whose row is still in the log raises the cutover error with a "stop persistent tasks" message | Accepted. Procedure and message cover completed-but-unread tasks; harness test C variant; register row 5 | §4 cutover, tasks 8, 9, register 5 |
| F4 (P3) — moving the final stdout envelope above the status block lets a raising write suppress the terminal event | Accepted. The moved write is guarded (log, continue; the cap error re-raises by design); test with an injected failure | task 3, register 13 |
| F5 (nit) — on `ResultPublicationError` the deferred STOP/KILL gets no reply | Accepted. Both failure paths call `_finalize_deferred_active_control(apply_reserved_policy=False)` before finalizing; asserted in the output-write-failure test | §4, task 3 |
| F6 (nit) — "reading outbox bodies" is unenforceable | Accepted; reworded to "does not decode outbox bodies" | task 4 |
| Claude Q1 (destructive modes only) | Answered by F1's disposition: mark only when `apply` is true | — |
| Claude Q2 (edge TIDs as result targets) | Answered by F2: edge events are exempt by role; whether an edge TID is a public target is unchanged | — |
| Claude Q3 (50,000 unread stall) | Answered: no global stall exists without the pin; the re-check bound is `batch_size` per cycle with a named `blocked_reason` and no catch-up churn | §4 [OBS.19], task 4 |
| Claude probe results (1)-(5) | Recorded; (1) one-writer verification is superseded by R2-1 — the six write sites are the task's own rows, the queue is not exclusive; (2)-(5) stand | §3 item 7 |

Declined: none. Out of scope: none. Codex's owner questions 1-3 are Open
Owner Questions 1-3 with recommended defaults.

**2026-09-08 — round 3, cross-family review of revision 3 (Codex: FAIL,
12 findings R3-1..R3-12; Claude: FAIL, 8 findings). Every finding was
reproduced against `178e3a34` before disposition; the round-3 probe is
`scratchpad/plans/09-rev3/probe_membership_queue.py`. The central finding
(R3-1 / Claude 4, 7; OOQ1/5) is that no event-borne representation is both
exact and free of diminution for unbounded producers; revision 4 moves
membership onto a task-owned queue, which makes the Monitor retention
findings moot (mechanism removed).**

| Finding | Disposition | Section changed |
|---------|-------------|-----------------|
| R3-1 (P1) — the cap arithmetic ignores the TaskSpec dump on the event and the `WEFT_MAX_MESSAGE_SIZE` override; live streaming, `stream_output`, and agent outputs are not bounded to ~40 rows; row 21 understates a class-D diminution | Accepted. Reproduced: `_report_state_change` embeds `redact_taskspec_dump(...)` (`base.py:1859-1868`); `WEFT_MAX_MESSAGE_SIZE` → `BROKER_MAX_MESSAGE_SIZE` (`_constants.py:2222`, `:2252`); probe (4): a 60 KB dump leaves 496,462 IDs at 10 MiB and 47,072 at 1 MiB. Representation moved off the event onto `T{tid}.results` with chunked explicit lists (no cap, no dependence on the event budget); the brief's run encoding was evaluated and rejected as not exact (probe (1)) | §1, §4, [MF-2]/[MF-5]/[OBS.18] delta, tasks 3–5, register 7/20/21, OOQ1 |
| R3-2 (P1) — the interactive cap failure is not implementable: a chunk-write exception escapes the reactor; the status block is unreachable after a raise in the envelope block | Accepted. Reproduced: `_interactive_flush_outputs` is called outside the reactor `try/finally` (`consumer.py:138-139`); the early return at `interactive.py:357-358`. One session-owned path: `_interactive_flush_outputs` catches broker/OS/runtime errors → `session.terminate()` + `_interactive_finalize_session(failure_reason=…)` (the limit-violation shape `:281-292`); final envelope and final membership row precede the status block; mid-session, final-membership, and final-envelope injection tests | §3 item 5, §4, task 3 |
| R3-3 (P1) / Claude 1 (P1) — `_finalize_deferred_active_control` transitions to cancelled/killed before `mark_failed`, which then raises; no `work_failed`, no error disposition | Accepted. Reproduced: `_handle_stop_request` `base.py:2071-2094`; `transition_to` guard `model.py:1500-1503`; ACK sent after the transition `consumer.py:741`/`:751`. Fix: `_finalize_work_exception` reordered to `mark_failed` → `_finalize_deferred_active_control(apply_reserved_policy=False)` (skips its transition when terminal, sends the ACK only) → `_finalize_terminal_outcome`; STOP and KILL tests with `reserved_policy_on_error=clear`, exactly one terminal event and one [QUEUE.6] disposition | §3 item 8, §4, task 3 |
| R3-4 (P1) — `report_only → delete` restart deletes an unread completion row whose ref is unmarked | Moot: the Monitor retention mechanism is removed; completion rows are not result authority | Revision Log |
| R3-5 (P2) — the re-check is bounded but not fair (starvation behind a permanently unread prefix) | Moot: no re-check exists | Revision Log |
| R3-6 (P2) — `raw_external` approach/test/register disagree; re-export duplicates JSONL | Moot: no `raw_external` probe exists; `raw_external` behaviour is unchanged from HEAD | Revision Log |
| R3-7 (P1) — v6 → v7 lacks [SB-0.4a]/[OBS.13.4] deltas and a rollback-injection test | Moot: no schema change; the 08-25 schema-version rule is not triggered (Class line) | Class line, Revision Log |
| R3-8 (P1) — the rollback note tells the operator to delete "the sidecar database", which is the broker database | Accepted. Reproduced: `_sidecar_session` uses `broker.sidecar()` (`store.py:2381-2398`) — Monitor tables live in the broker database. Rollback note rewritten: never delete the broker database; no sidecar step exists in this revision | §4 rollback, §7, task 9 (CHANGELOG) |
| R3-9 (P1) — the procedure leaves the Manager accepting work; `result --all` misses custom outboxes; old keyless events can still trigger the cutover error after a correct drain | Accepted. Reproduced: `manager.py:1068-1078` (launch skipped only while draining); `result.py:862` (`T*.outbox` pattern); `queue_names_for_tid` used by `weft result TID` (`result.py:201`, `:439`). Procedure now starts with `weft manager stop`, drains per TID with `weft result TID`, and `ResultCutoverError` fires only while the outbox holds unread rows and the results queue is empty — after a drain the old event is inert (third point removed); events are never consulted for membership; tests for the drained case | §4 cutover, [MF-5] delta, tasks 4, 5, 8, 9, register 5, OOQ2 |
| R3-10 (P2) / Claude 3 (P2) — test D's monkeypatch cannot reach a spawned Consumer; a sleeping command sees STOP via cancellation; the terminal event is `control_stop` | Accepted. Reproduced: spawn context `launcher.py:167`; deferred STOP event name `consumer.py:734`. Test D is now an in-process real Consumer on `broker_env` with real control queues and a blocking controlled runner; asserts `control_stop`; states explicitly that Manager launch is not exercised | task 8, §6 |
| R3-11 (P2) — the re-check cannot guarantee "no checkpoint written" | Moot: no re-check exists | Revision Log |
| R3-12 (P2) — task 9 still says "outbox range" | Accepted. All non-historical range language replaced; grep check added to task 9 and §7 | task 9, §7 |
| Claude 2 (P2) — retained rows behind the checkpoint make the pre-checkpoint recovery pass O(retained) and can hit the scan limit into a 2 s loop | Moot: no rows are retained behind the checkpoint | Revision Log |
| Claude 4 (P2) — interactive rows are one per pipe read; ~100 lines/s reaches 400,000 rows in ~67 min | Accepted (evidence for R3-1). Reproduced: `sessions.py:65`, `host.py:1077`; arithmetic confirmed. Recorded in §1; resolved by the membership queue | §1, register 20 |
| Claude 5 (P3) — pre-checkpoint recovery folds valid rows without the probe | Moot: mechanism removed | Revision Log |
| Claude 6 (P3) — recording the branch-head SHA and diff stat in the plan changes both | Accepted. The identifier is the parent of the recording commit and a diff stat that excludes the plan file | Spec Baseline, slice order, task 10 |
| Claude 7 (nit) — "2 KiB other fields" is not the event's size | Accepted (evidence for R3-1); the event's size includes the TaskSpec dump; membership no longer rides on it | §1, §3 item 4 |
| Claude 8 (nit) — `verify_v6()` counterpart unstated | Moot: no schema change | Revision Log |
| Codex owner questions 1–5 | 1 → OOQ1 (membership queue default with the comparison table); 2 (column vs table) → moot; 3 → OOQ2 (Manager-level quiescence); 4 (`raw_external`) → moot; 5 (visibility) → OOQ5 now concerns results-queue cleanup counters | Open Owner Questions |

Declined: none. Out of scope: none. Both reviewers' round-3 verifications
(exact-ID membership under interleaving; `core → commands` boundary of the
decoder; the baseline identifier scheme) are carried into revision 4.

## Revision Log

| Date | Change | Reason |
|------|--------|--------|
| 2026-09-08 | Revision 2. Material changes: (1) membership representation is a contiguous range `outbox_first_id`/`outbox_last_id` (two ints, both `null` for zero rows) instead of a per-row list; (2) new [OBS.19] TaskMonitor retention rule with checkpoint pin, plus [OBS.13.3] qualifier and the 07 `:940-943` retention-sentence rewrite; (3) completion-event publication is part of delivery — retried, then `ResultPublicationError` fails the task; (4) promotion strategy B atomic for all files, backstitch gate in §7; (5) one-shot waiter polls before it drains; (6) explicit cutover (`ResultCutoverError` + operator procedure) instead of a timeout for pre-upgrade events; (7) int-only validator; (8) `PartialResultError` and the one-consumer invariant in [OBS.18]; (9) all anchors re-verified. Register row 6 reclassified D→C. The revision re-enters review. | Round-1 cross-family review (see Review Record) |
| 2026-09-08 | Revision 3. Material changes: (1) representation reopened — the one-writer premise behind the range was falsified; recommended default became the explicit bounded event list `outbox_message_ids` with `RESULT_OUTBOX_ID_LIMIT` (400,000); range + `outbox_row_count` recorded as the owner's alternative; (2) retention mechanism replaced — the checkpoint pin was withdrawn; completion rows folded as today with child refs marked `result_pending_at_ns` (Monitor schema v6 → v7), honoured by all four raw-deletion paths, cleared by a bounded per-cycle re-check; (3) cutover sequence quiesce-first with a harness test of the STOP ordering; (4) reactor-side finalization of output-publication failures; (5) explicit `BrokerError` handling in every retention probe; (6) pipeline-edge exemption; guarded interactive final envelope; (7) promotion baseline identifier = base SHA + pre-landing identifier. Register rows 20-24 added, row 21 class D escalated. The revision re-entered review. | Round-2 cross-family review (see Review Record) |
| 2026-09-08 | Revision 4. Material changes: (1) **representation moved off the completion event onto a task-owned membership queue `T{tid}.results`** (sixth standard task-local queue, TID-derived, not an `io` key): explicit `outbox_message_ids` lists chunked across membership rows (`RESULT_MEMBERSHIP_CHUNK_IDS` = 4,096, clamped to the effective message limit), `final` flag, `seq`, input `message_id`; exact under interleaving and unbounded — no `RESULT_OUTBOX_ID_LIMIT`, no cap cliff; the brief's run encoding was evaluated and rejected as not exact (probe (1)); the completion event keeps today's shape plus `membership_rows` (a cutover shape marker and diagnostic, never consulted for membership). (2) **Monitor retention machinery removed** as unnecessary: `result_pending_at_ns`, schema v6 → v7 and the 08-25 rule trigger, [OBS.19], the [OBS.13.3] qualifier, the [MF-5] `:599-601` delta, the `raw_external` probe, the fair-recheck cursor, the `report_only → delete` restart hazard, the probe `BrokerError` contracts, `_completion_result_unread`, `_recheck_result_pending_task_log_refs`, the three store methods, and every task-4 test and register row (former rows 7, 19, 22, 23, 24) that existed only for it; the collation rule is unchanged from HEAD. (3) **Failure paths reordered**: `mark_failed` → ACK-only deferred reply → `_finalize_terminal_outcome`; a membership-row write failure is an outbox-write failure through the same catch; `result_bearing` and `ResultPublicationError` removed; interactive sessions get one session-owned publication-failure path. (4) **Cutover at Manager level** (`weft manager stop` first; per-TID drain with `weft result TID`; `ResultCutoverError` only while unread rows remain, so retired keyless events are inert). (5) **Queue-name contract** task 7 added ([OBS.13.9] delta, Quick Reference row, cleanup policy sets, discovery patterns, dump/list/pipeline tests). (6) Test D is an in-process real-Consumer test; Manager launch not exercised. (7) Rollback never deletes the broker database. (8) Baseline identifier = parent of the recording commit, plan file excluded. (9) Range-language purge with a grep gate. Open Owner Questions rewritten around the representation decision with an exactness/no-diminution/complexity comparison. The revision re-enters review. | Round-3 cross-family review (see Review Record) |

## Supersession Note

2026-09-11: this plan's design (membership IDs on a sixth task-local
queue) was rejected by the owner on 2026-09-10 in favor of the
result-reads-outbox-only contract (one outbox row per call; block until
row or terminal; exit 2 for nothing; `--nowait`; spill references returned
unchanged; stream-frame reassembly only). That decision was recorded in
`2026-09-10-weft-result-outbox-contract-plan.md` (added in `467baa34`),
which the owner removed unimplemented in `9b858304` before any spec
promotion; recover it from git history if the successor work resumes.
`Superseded by:` reads `none` only because the successor file no longer
exists — this plan remains rejected-in-substance history, inheriting
nothing decided-but-unbuilt (its deviation log is empty and
`T{tid}.results` was never created).
