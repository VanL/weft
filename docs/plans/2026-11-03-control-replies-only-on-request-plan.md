# Control Replies Only on Request: Outcomes Are Broadcast

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-2], [MF-5]; docs/specifications/01-Core_Components.md [CC-2.4]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1], [CLI-1.3]; docs/specifications/07-System_Invariants.md [QUEUE.2a], [QUEUE.6], OBS.12a; docs/specifications/00-Quick_Reference.md (Queue Names, Control Messages); docs/specifications/12-Pipeline_Composition_and_UX.md [PL-1.5], [PL-3.2], [PL-4.4], [PL-5.2]
Superseded by: none

Class: 5 (spec-changing). The reply contract for control commands in [MF-3],
[CC-2.4], [QUEUE.2a], OBS.12a, [CLI-1.1.1], [CLI-1.3], and the Quick
Reference changes, so materiality requires a spec change, and one theory
clause ([WT-2a.12], with consistency edits to [WT-2] item 1 and the [WT-4]
4.1 row "Everything is a task") changes with the owner's acceptance. Risky triggers also fire, so the
hardening-plans checklist applies in full: a public queue contract changes
(what a control command writes back, and the STATUS request envelope), and
two execution paths change (the task control handlers in `weft/core/tasks/`
and `weft/core/manager.py`, and the CLI control wait in `weft/commands/`).
Review happens before implementation. Not +P: no runbook, skill, or gate
changes. Plan type: implementation with spec revision. Promotion strategy B
(atomic with the code); see `## Proposed Spec Delta`. One-way doors: none.

This is plan 3 of the 2026-11 cleanup and reconciliation series opened by
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (completed). Its
justifying audit rows are I-12, 4.1-a, and 4.1-b in that plan's audit table
(lines 5440, 5443, 5444 at `b7eed9f9`). It replaces the draft
`docs/plans/2026-11-02-control-reply-routing-plan.md` (never committed; see
`## Review Log`), which routed STOP/KILL acknowledgements to a requester
queue. The owner ruled against that design on 2026-11-03.

## 1. Goal

The owner's ruling, verbatim. On the previous draft, the owner said on
2026-11-03: "My preliminary thinking is that it isn't *necessary* to have a
message response for every control message. It may be *useful*." Then, after
the session lead showed that every task writes its STOP/KILL acknowledgement
after its terminal transition (so the acknowledgement repeats what the
terminal notification already broadcast and lands after the requester has
left): "So maybe I was wrong on the ordering. It seems like just relying on
the broadcast is operationally much simpler." Then, asked whether this would
be unsurprising to a user, the owner heard that it makes `weft task stop`
behave like every stop command a user knows (`docker stop`, `systemctl
stop`: return when stopped, no receipt), and ruled: "Redraft plan 3".

The rule. A control command whose outcome is a state change (STOP, KILL,
PAUSE, RESUME) gets no reply. The task makes the transition and publishes it
where it already does: the state or terminal event in `weft.log.tasks`, the
task-owned `weft.state.tasks.<tid>` snapshot, and the terminal envelope on
`ctrl_out`. The requester is one of the observers those facts are addressed
to ([WT-4] "Address a fact to its observers") and watches queues it already
watches. A command whose outcome is a private fact (PING, and STATUS if the
owner moves it) is answered to the return address the requester supplied in
`reply_to`, the PING shape that exists today ([WT-2a.12]; [ALT-WT-003]).

One sentence for a user: if the answer is something everyone can see, nobody
replies; if the answer is just for you, give a return address. A message
nobody asked for is a message nobody retrieves or cleans up.

Concretely, after this plan: no task writes a `{command, status: "ack"}` row
anywhere; `weft task stop`, `weft task kill`, and interactive teardown wait
only for terminal evidence and never delete rows on a target's `ctrl_out`;
and (owner decision 1, option (a), which the tasks implement) STATUS takes
the PING envelope and is answered on the requester's `reply_to`, so
`ctrl_out` carries only observer-addressed facts.

## 2. Source Documents

Governing specs (the current text sanctions acknowledgement replies; this
plan changes it). Every span is re-derived from the live text at `b7eed9f9`
(Execution log E9):

- `docs/specifications/05-Message_Flow_and_State.md` [MF-3] lines 203-207
  (request envelopes), 213-215 (`ctrl_out` contents), 226-230 (PING reply;
  the STATUS bullet is inserted after it), 297-299 ("Queued control is
  acknowledged after this unwind"), 315-316 (no-scan sentence), 317-320
  (keyed-reply ownership), 326-329 (CLI control waiter), 333-346 (active
  handoff, deferral echo, reply echo, KILL acknowledgement), implementation
  mapping 376-377 and 379-380; [MF-2] lines 157-159 ("acknowledges
  control"); [MF-5] line 1150 ("STOP/ACK").
- `docs/specifications/01-Core_Components.md` [CC-2.4] lines 712-735 and
  787-789; the docstring contract in
  `weft/core/tasks/base.py::BaseTask._handle_control_command` (line 1710,
  "send a ctrl-out response for handled STOP/KILL commands") is its code
  twin.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1] lines 304-313 and
  mapping 320-321; [CLI-1.3] lines 643-647.
- `docs/specifications/07-System_Invariants.md` [QUEUE.2a] lines 109-118
  (changes only under owner decision 1 (a): the STATUS envelope), [QUEUE.6]
  lines 147-148, OBS.12a lines 476-483.
- `docs/specifications/00-Quick_Reference.md` Queue Names rows lines 15-16
  and 25; Control Messages paragraph lines 155-160 and the STATUS row line
  166.
- `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-1.5] lines
  247-248, 259, 281-282; [PL-3.2] lines 500-501; [PL-4.4] lines 673-674;
  [PL-5.2] line 720 (all but 673-674 change only under owner decision 1 (a),
  because they call the pipeline `ctrl_out` the "control reply" surface).

Audited and left unchanged (reasons in `## Proposed Spec Delta`, "Spans
audited and unchanged").

Theory and audit:

- `docs/program-theory.md` [WT-2] item 1 "Everything is a task" (lines
  160-165), [WT-2a.12] (lines 335-341), [WT-2a.13], [WT-4]
  4.1 rows "Everything is a task" (line 413), "It is just a message" (line
  414), "Address a fact to its observers" (line 418); [ALT-WT-003] (lines
  546-564), [ALT-WT-012] (lines 710-724); [WT-7] "Possession probe, keyed"
  (line 943); [WT-8] row "Everything is a task" (line 1041).
- `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` audit rows
  I-12, 4.1-a, 4.1-b (completed plan; the rows are the drift record).
- `docs/plans/2026-09-18-event-routed-manager-pong-plan.md` (completed): the
  PING/PONG `reply_to` precedent that STATUS copies. Historical, not
  normative.
- `docs/plans/2026-11-02-reactor-edge-timer-floors-plan.md` (series plan 2,
  draft): its Task 2 (deferred to the owner) owns the `interval` cap of
  `weft/commands/tasks.py::_control_surface_wait_timeout`. This plan does not
  change that cap. Its Task 2 text names the two grace windows this plan
  removes; if the owner later runs it, it is rewritten against this plan's
  result (coupling recorded, plan 2 not edited here).

Local guidance: `CLAUDE.md` §1.1, §4 (house style), §5.1;
`docs/agent-context/runbooks/writing-plans.md`,
`docs/agent-context/runbooks/hardening-plans.md`,
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`,
`docs/agent-context/runbooks/adversarial-acceptance-probes.md`;
`docs/agent-context/engineering-principles.md`;
`docs/agent-context/decision-hierarchy.md` [DOM-15].

## 3. Context and Key Files

### Current structure (what exists at `b7eed9f9`)

Responders. Every acknowledgement is written to the responder's own
`ctrl_out` by `weft/core/tasks/base.py::BaseTask._send_control_response`
(line 2113), which builds the payload with `_control_response_payload`
(line 2089: `command`, `status`, `tid`, `timestamp`, optional live
`activity`/`waiting_on`, extras) and writes it with
`self._ctrl_out_queue.write(...)`, best-effort. The directed writer for
replies addressed to a requester is `BaseTask._send_control_reply`
(line 2127), which writes to a named queue through the task's connected
broker, best-effort; at `b7eed9f9` only PING uses it (line 1722). The 15
call lines of `_send_control_response` (Execution log E1), classified:

| File | Line | Site | Disposition |
|------|------|------|-------------|
| `weft/core/tasks/base.py` | 1737 | `BaseTask._handle_control_command` STOP ack after `_handle_stop_request` | deleted |
| `weft/core/tasks/base.py` | 1751 | same, KILL ack after `_handle_kill_request` | deleted |
| `weft/core/tasks/base.py` | 1765 | same, PAUSE ack (`paused=True`) | deleted |
| `weft/core/tasks/base.py` | 1780 | same, RESUME ack (`paused=False`) | deleted |
| `weft/core/tasks/base.py` | 1789 | same, STATUS reply (`status="ok"`, snapshot fields) | moved to `_send_control_reply(request.reply_to, ...)` under owner decision 1 (a); kept under (b) |
| `weft/core/tasks/consumer.py` | 777 | `Consumer._finalize_deferred_active_control` STOP ack | deleted |
| `weft/core/tasks/consumer.py` | 792 | same, KILL ack | deleted |
| `weft/core/tasks/interactive.py` | 560 | `InteractiveTaskMixin._interactive_handle_control`, late STOP/KILL to an already-terminal task | deleted (the branch stays and returns `True`) |
| `weft/core/tasks/interactive.py` | 580 | same, STOP ack after `_interactive_shutdown` | deleted |
| `weft/core/tasks/interactive.py` | 600 | same, KILL ack | deleted |
| `weft/core/tasks/pipeline.py` | 900 | `PipelineTask._handle_control_command` STATUS reply (adds `status_queue`) | moved to `_send_control_reply` under (a); kept under (b) |
| `weft/core/tasks/pipeline.py` | 922 | same, STOP ack | deleted |
| `weft/core/tasks/pipeline.py` | 939 | same, KILL ack | deleted |
| `weft/core/tasks/monitor.py` | 111 | `Monitor._handle_control_command` STOP ack | deleted |
| `weft/core/manager.py` | 1317 | `Manager._handle_control_command` STOP ack with `draining: true` | deleted |

No rejection reply exists: a row that does not parse as a request is passed
to `_handle_control_reply` and exact-acknowledged with no write
(`BaseTask._handle_control_message`, lines 1674-1678). So under (a) the
method `_send_control_response` has no caller left and is deleted, with its
abstract declaration in `InteractiveTaskMixin` (interactive.py lines
131-139) and the helper `BaseTask._control_request_id_extra` (line 1799,
whose only callers are the four deleted base sites, E11). Under (b) the
method stays for the two STATUS callers; the abstract declaration and
`_control_request_id_extra` are deleted either way.

Every task writes its STOP/KILL acknowledgement after its terminal
transition has been published. Evidence: a scratch probe of a `Consumer`
handling a keyed STOP and KILL shows the typed terminal envelope first and
the acknowledgement second on `ctrl_out` (E14), and
`tests/tasks/test_task_interactive.py::test_interactive_command_control_unwinds_before_terminal_and_ack`
asserts `terminal_timestamp < acknowledgements[0][1]` for the interactive
path. The Manager is the one exception: its STOP acknowledges entry into
draining before the Manager is terminal, and it also publishes that fact as
the `control_stop` lifecycle event with `draining=True`
(`weft/core/manager.py` lines 4829-4834, E19) and as `should_stop=true` in
its PONG snapshot.

The state change each command publishes, unchanged by this plan:

- STOP, KILL: `_handle_stop_request` / `_handle_kill_request` (or the
  interactive and deferred equivalents) mark the TaskSpec terminal and call
  `BaseTask._report_state_change` (line 2008), which writes one serialized
  record to `weft.log.tasks` and, for a terminal status, the same record as
  a typed terminal envelope to the configured `ctrl_out` ([MF-3] lines
  267-276), then refreshes the `weft.state.tasks.<tid>` snapshot
  (`_publish_projection_edge`, line 2072).
- PAUSE, RESUME: `_report_state_change(event="control_pause")` /
  `"control_resume"` write the lifecycle event to `weft.log.tasks` and
  refresh the snapshot, whose `projection.event` becomes `control_pause` /
  `control_resume` (E13: scratch probe output). A PAUSE to a paused task, or
  a RESUME to a running one, changes nothing and publishes nothing.

`Consumer` defers active STOP/KILL: `_poll_active_control_once` (line 630)
calls `_defer_active_control(command, timestamp, request_id=...)` (line 683),
which stores `_deferred_active_control_command`, `_timestamp`, and
`_request_id` (lines 106-108); `_finalize_deferred_active_control` (line
733) applies the terminal transition after the runner unwinds and then
writes the acknowledgement. The stored `request_id` has no use other than
that acknowledgement (E11), so the field and the keyword go.

Requesters, `weft task stop` / `weft task kill` and the Python client
(`weft/client/_task.py` lines 136 and 139, `weft/client/_namespaces.py` lines
150 and 170 call `weft/commands/tasks.py::stop_task` / `kill_task`):

- `weft/commands/tasks.py::_send_control` (line 1628) writes
  `encode_control_message(command)`: no `request_id`, no `reply_to`.
  Unchanged.
- `_stop_resolved_tasks` (line 2140) and `_kill_resolved_tasks` (line 2257)
  call `_send_control`, then `_await_control_surface` once, then again after
  a runner fallback (`_stop_via_fallback` line 1993, `_kill_via_fallback`
  line 2019, `_force_kill_task_processes`). Unchanged.
- `_await_control_surface` (line 1901) builds `_ControlSurfaceResources`
  (line 1723: one `QueueChangeMonitor` over the task-state queue, the target
  `ctrl_out`, and the optional pipeline status queue), then each turn calls
  `_observe_control_envelopes`, then reads `mapping_for_tid` and
  `_task_status`, then asks `_control_surface_wait_timeout` (line 1873) for
  the next wait.
- `_observe_control_envelopes` (line 1803) peeks the target `ctrl_out` from
  its oldest row up to the horizon captured at the start of the call,
  **exact-deletes each visited row that is not a completion record**
  (`broker.delete_message_ids`, line 1841; it checks neither owner nor
  request before deleting, so it deletes other requesters' rows, STATUS
  replies, and interactive stderr stream frames, contrary to [MF-3] lines
  317-320), and returns the first typed terminal snapshot for the target. It
  also records two clocks in `_ControlSurfaceObservation` (line 1795):
  `public_signal_observed_at` (any row with `command` and `status`, or a
  terminal-looking row) and `kill_ack_observed_at` (any row whose normalized
  `command`/`status` are `KILL`/`ack`, any requester, no `tid` check).
- `_control_surface_wait_timeout` turns those clocks into two grace windows,
  each `CONTROL_SURFACE_WAIT_INTERVAL` (0.05 s) long, beside the overall
  `CONTROL_SURFACE_WAIT_TIMEOUT` (2.0 s) deadline (`weft/_constants.py` lines
  714 and 723): each observed public signal resets the public-signal grace
  deadline to `observed_at + CONTROL_SURFACE_WAIT_INTERVAL`
  (`_await_control_surface`), and past the overall deadline the wait goes on
  until the latest grace deadline passes, so continuing control traffic
  extends the wait without bound; the KILL window ends the wait
  0.05 s after a KILL ack row even when the overall deadline is far away.
  Because the terminal envelope precedes the acknowledgement on the same
  `ctrl_out` and the observer returns at the first terminal snapshot, the
  KILL window changes the outcome only when an acknowledgement is visible
  without a terminal envelope: a task whose bounded completion publication
  exhausted its retries ([MF-3] lines 281-283), or a foreign or pre-upgrade
  row.

Requester, interactive teardown (`weft run --interactive`):

- `weft/commands/run.py::_stop_interactive_run_session` (line 503) opens
  `result_observation(ctx, names)` over the task-state queue, `ctrl_in`,
  `ctrl_out`, and `outbox`; writes `encode_control_message(CONTROL_STOP,
  request_id=uuid4().hex)` (lines 529-532); then loops: returns when
  `_terminal_observation_snapshot(...)` is terminal or when
  `_matching_stop_ack(...)` finds its own acknowledgement, else waits; on
  expiry of `INTERACTIVE_STOP_COMPLETION_TIMEOUT` (8.7 s at `b7eed9f9`, E12)
  it writes a keyed KILL (line 550) and returns without waiting.
- `_matching_stop_ack` (line 486) peeks every row of the target's `ctrl_out`
  and exact-deletes the first row whose `tid`, `command`, `status="ack"`, and
  `request_id` match. Its docstring says "ACK is progress".
- Because the task publishes completion before it acknowledges, the
  terminal check normally fires first; the acknowledgement settles the wait
  alone only when completion publication failed after its bounded retries.

Reply-less senders that stay unchanged (verified, E4): the Manager's child
control `weft/core/manager.py::Manager._send_child_control_command` (line
4954), the pipeline's broadcast to its children
`weft/core/tasks/pipeline.py::PipelineTask._broadcast_control` (line 821,
`encode_control_message(command)` at 841), and
`weft/core/manager_runtime.py::_send_stop` (line 1158). `weft manager stop`
waits in `weft/core/manager_runtime.py::_await_manager_stop_confirmation`
(line 1683) on registry and process evidence through a `QueueChangeMonitor`
over the registry queue; it reads no `ctrl_out` row (E17).

Readers of acknowledgement rows in production, exactly two (E3):
`weft/commands/run.py` line 494 and `weft/commands/tasks.py` line 1864. Both
go. No sender of STATUS, PAUSE, or RESUME exists under `weft/commands/`,
`weft/cli/`, or `weft/client/`, and no file under `integrations/` or
`extensions/` names a control command constant (E5, E6).

Envelope: `weft/core/control_messages.py::_validate_control_request`
(line 33) requires `request_id` and `reply_to` for PING and raises
`"reply_to is only valid for PING"` otherwise (line 48);
`parse_control_request` (line 103) accepts exactly
`{command, request_id, reply_to}` for PING and `{command}` or
`{command, request_id}` for every other command (lines 113-117).

### Files to modify

Spec (Task 5): `docs/specifications/05-Message_Flow_and_State.md`,
`docs/specifications/01-Core_Components.md`,
`docs/specifications/10-CLI_Interface.md`,
`docs/specifications/07-System_Invariants.md`,
`docs/specifications/00-Quick_Reference.md`,
`docs/specifications/12-Pipeline_Composition_and_UX.md`; `CHANGELOG.md`.
Theory (owner-gated, separate commit): `docs/program-theory.md`.

Code (Tasks 3-4a): `weft/_constants.py` (one constant, owner decision 1 (a);
and, Task 4a, the `CONTROL_CONVERGENCE_STATE_VALUES` and
`CONTROL_CONVERGENCE_ACTION_VALUES` sets), `weft/commands/control_convergence.py`
(Task 4a),
`weft/core/control_messages.py` ((a) only), `weft/core/tasks/base.py`,
`weft/core/tasks/consumer.py`, `weft/core/tasks/interactive.py`,
`weft/core/tasks/pipeline.py`, `weft/core/tasks/monitor.py`,
`weft/core/manager.py`, `weft/commands/tasks.py`, `weft/commands/run.py`.

Tests (existing files only; no new test file):
`tests/core/test_control_messages.py`, `tests/tasks/test_control_channel.py`,
`tests/tasks/test_task_execution.py`, `tests/tasks/test_task_interactive.py`,
`tests/tasks/test_pipeline_runtime.py`,
`tests/tasks/test_task_observer_behavior.py`,
`tests/tasks/test_reserved_disposition.py`,
`tests/tasks/test_task_observability.py`, `tests/tasks/test_task_monitor.py`,
`tests/core/test_manager.py`, `tests/core/test_task_runtime_connections.py`,
`tests/commands/test_task_commands.py`,
`tests/commands/test_control_observation_connections.py`,
`tests/commands/test_active_interactive_lifecycle.py`,
`tests/cli/test_cli_system.py`.

### Read first

- `weft/core/tasks/base.py` lines 1667-1823 (control dispatch, PING and
  STATUS replies, `_control_response_extras`) and 2008-2151
  (`_report_state_change`, `_publish_projection_edge`,
  `_control_response_payload`, `_send_control_response`,
  `_send_control_reply`).
- `weft/core/tasks/consumer.py` lines 630-792 (active-control deferral).
- `weft/core/tasks/interactive.py` lines 131-139 and 541-602.
- `weft/core/tasks/pipeline.py` lines 821-845 and 894-941;
  `weft/core/tasks/monitor.py` lines 92-113; `weft/core/manager.py` lines
  1307-1319 and 4800-4840.
- `weft/core/control_messages.py` in full (135 lines; shape only).
- `weft/commands/tasks.py` lines 1628-1645 and 1723-1981;
  `weft/commands/run.py` lines 486-553.
- `weft/commands/control_convergence.py` in full (275 lines; edited only
  by Task 4a) and its tests in `tests/commands/test_task_commands.py`
  lines 1082-1280.
- Tests named in Task 2, plus their helpers:
  `tests/commands/test_task_commands.py` `_FakeQueueChangeMonitor` (line
  699) and `_observation` (line 767);
  `tests/commands/test_active_interactive_lifecycle.py` `_live_session`,
  `_stop_peer`, `_publish_stopped_peer` (lines 47-135);
  `tests/tasks/test_task_monitor.py` `_read_control_reply` (line 2508) and
  `_read_status_reply` (line 11513).

### Shared paths: reuse, do not duplicate

- Directed reply write: `BaseTask._send_control_reply`. STATUS uses it as
  PING does. Do not add a second writer.
- Terminal publication: `BaseTask._report_state_change` and the completion
  publisher. Not touched.
- Wake source for the CLI waits: the monitors each requester already owns
  (`_ControlSurfaceResources` in `weft/commands/tasks.py`,
  `result_observation` in `weft/commands/run.py`). No queue is added to
  either set, and no second monitor appears ([WT-2a.6]).
- Cursor reads: the connection-level
  `broker.peek_generator(name, with_timestamps=True, after_timestamp=...,
  before_timestamp=...)` already used by `_observe_control_envelopes`;
  `after_timestamp` is an exclusive message-id cursor (SimpleBroker's SQL
  filter is `ts > ?`), the same semantics `weft/core/queue_wait.py` line 64
  relies on when it passes `after_timestamp=queue.last_ts` to a
  `QueueWatcher` (E18).

### Comprehension questions (answer in the Execution log before the first edit)

1. After this change, what does `weft task stop TID` read to learn that the
   task reached `cancelled`, and what may it delete?
   - Expected: the typed terminal envelope on the target's `ctrl_out`, read
     peek-only through a cursor, and the task-state evidence from
     `_task_status` / `mapping_for_tid`. It deletes nothing anywhere; there
     is no acknowledgement to read.
2. A PAUSE arrives at a running `Consumer`. What is written, and where?
   - Expected: `_paused` becomes `True`; `_report_state_change(event=
     "control_pause")` writes the event to `weft.log.tasks` and refreshes
     `weft.state.tasks.<tid>` (`projection.event == "control_pause"`);
     nothing is written to `ctrl_out` or to any requester queue. A second
     PAUSE writes nothing at all.
3. A STATUS request arrives without `reply_to` (owner decision 1 (a)).
   What happens?
   - Expected: `parse_control_request` returns `None`; the row goes to the
     reply hook and is exact-acknowledged once; no reply is written. A
     STATUS with `request_id` and `reply_to` gets exactly one reply on
     `reply_to` and none on `ctrl_out`.
4. Why does the KILL path of `weft task kill` not lose speed in the normal
   case when the KILL-acknowledgement grace window goes away?
   - Expected: the task publishes its terminal envelope before it would have
     acknowledged, and the waiter returns at the first terminal snapshot, so
     the window was reached only when an acknowledgement was visible without
     a terminal envelope (completion publication failure, or a foreign or
     pre-upgrade row).

## 4. Invariants and Constraints

Must not change:

- The state transitions and their publication. STOP still cancels, KILL
  still kills, PAUSE and RESUME still toggle `_paused`, through the same
  methods in the same order; `BaseTask._report_state_change`, the completion
  publisher, and `_publish_projection_edge` are not edited. Forward-only
  states ([STATE.1], [STATE.2]); a late STOP/KILL to a terminal task still
  regresses nothing and emits no second terminal event ([CC-2.4] lines
  788-789).
- Reserved policy: STOP applies `reserved_policy_on_stop`, KILL applies
  `reserved_policy_on_error`, on the same code paths and in the same order
  ([QUEUE.4]-[QUEUE.6], [MF-2]); the deferral's `apply_reserved_policy`
  handling is unchanged.
- The request envelope for STOP, KILL, PAUSE, and RESUME: exactly
  `{command}` or `{command, request_id}`; `reply_to` stays invalid for them.
  A `request_id` on these commands stays accepted; it is echoed nowhere
  because nothing replies.
- PING, PONG routing, PONG matching, `send_keyed_ping_probe`, and every
  Manager reactor probe ([QUEUE.2a], [MF-3] lines 219-262).
- Terminal envelope shape and the [MF-3] rule that readers ignore malformed
  or unrelated `ctrl_out` rows (lines 306-307); that rule is what makes
  pre-upgrade acknowledgement rows harmless.
- `weft task stop` / `weft task kill` counts, exit codes (0, 1 for
  `ControlRejected`, 2 for `TaskNotFound`; `weft/cli/app.py`
  `_command_error_code`), the runner/host fallback order, and
  the reducer's production decisions in `weft/commands/control_convergence.py`
  (Task 4a deletes only states, transitions, and one action that no
  production caller reaches; every decision the five call sites receive is
  unchanged). The overall
  `CONTROL_SURFACE_WAIT_TIMEOUT` (2.0 s), the `interval` cap
  `CONTROL_SURFACE_WAIT_INTERVAL` (0.05 s, owned by series plan 2), and
  `INTERACTIVE_STOP_COMPLETION_TIMEOUT` (8.7 s) keep their values.
- Interactive escalation: after `INTERACTIVE_STOP_COMPLETION_TIMEOUT` with
  no terminal evidence, the interactive client writes one KILL and returns
  without waiting, as today.
- Manager stop confirmation ([MA-3]): `_send_stop` and
  `_await_manager_stop_confirmation` unchanged.
- TID format ([WT-2a.1], [IMMUT.3]); spawn-context processes; `core` does
  not import `commands`; `spec`/`io` immutability; `weft.state.*` stays
  runtime-only.

Review gates:

- No compatibility lane, dual reader, or tolerance window ([WT-2a.9],
  [ALT-WT-012]): the CLI does not read acknowledgement rows "for old tasks",
  and no task writes them "for old CLIs".
- No new abstraction, queue name family, CLI flag, TaskSpec field, or
  dependency. One new constant under owner decision 1 (a):
  `CONTROL_REPLY_COMMANDS: Final[frozenset[str]]` in `weft/_constants.py`
  beside `CONTROL_COMMANDS` (line 1774), holding `CONTROL_PING` and
  `CONTROL_STATUS`, the commands whose outcome is a private fact.
- No mocks of queues, task handlers, or process lifecycle. Tests that
  already replace `QueueChangeMonitor.wait` or `time.monotonic` as a drive
  or clock seam may keep doing so; the handler and queues behind them stay
  real.
- Net deletion: the change removes code paths; a diff that adds a helper,
  a wrapper, or a reply route is drifting.

Hidden couplings:

- Two production readers depend on acknowledgement rows (E3). They are
  rewritten in the same commit as the responders (strategy B); between
  Task 3 and Task 4 the requester suites are expected red. Do not commit an
  intermediate state.
- `_observe_control_envelopes`' deletes were the only CLI-side cleanup of
  ordinary `ctrl_out` rows. After this plan no producer writes a control
  reply there, so nothing new needs that cleanup. What remains on a
  `ctrl_out` is observer-addressed (terminal envelopes, `work_item_completed`
  boundaries, interactive stderr frames) or pre-upgrade residue. Existing
  cleanup retires it exactly as it retires the same rows today: the
  supervised TaskMonitor runtime-control cleanup
  (`weft/core/monitor/policies/runtime_control.py`, capped by
  `WEFT_TASK_MONITOR_CONTROL_QUEUE_DELETE_LIMIT`, Quick Reference line 202)
  deletes standard `T{tid}.ctrl_out` queues of terminal and dead TIDs when
  the monitor is enabled, and retention pruning selects nonterminal
  `ctrl_out` rows under `force`
  (`weft/core/pruning/retention.py::_ctrl_out_candidates`, line 686).
  Normal task exit retains `ctrl_out` as completion evidence
  (`BaseTask._cleanup_standard_control_queues_on_exit` deletes only
  `ctrl_in`). This plan widens no cleanup path.
- Interactive stderr frames on a live interactive task's `ctrl_out` have an
  owner that consumes them: the result-collection drain
  `weft/commands/_result_wait.py::drain_ctrl_out_stream_messages` (line 137)
  renders each non-completion row and exact-deletes it, retaining completion
  records. That path is unchanged. It also consumed acknowledgement rows
  silently today (an acknowledgement has no `data` key, so
  `weft/commands/_streaming.py::handle_ctrl_stream` renders nothing); those
  rows no longer arrive, so its output does not change. The only change is
  that a concurrent `weft task stop` visit no longer deletes frames that
  drain has not rendered yet (R6).
- STATUS under (a): a hand-written controller that sends `{"command":
  "STATUS"}` today gets a reply on `ctrl_out`; after this plan its row is
  rejected (no reply), and `encode_control_message("STATUS")` raises
  `ValueError`. No in-tree sender exists (E5, E6, E10); the in-tree tests
  that send STATUS are updated in Task 2. The CHANGELOG states the break.
- A STATUS reply that lands on a long-lived task's own `ctrl_in` (because
  that task named its configured queue as `reply_to`) has `command`,
  `status`, and `tid` keys, so `parse_control_request` returns `None` and the
  row goes to `_handle_control_reply`, as a PONG does; a reply can never be
  dispatched as a request.
- Series plan 2's deferred Task 2 names the two grace windows; see §2.

Fatal versus best-effort: unchanged. A STATUS reply write is best-effort
inside `_send_control_reply` (debug log on `BrokerError`, `OSError`,
`RuntimeError`), as PONG is; it never changes task state. Terminal
publication keeps its bounded retry policy.

### Rollout, rollback, and one-way doors (stated before the tasks)

Rollout: one coordinated release; responders, requesters, and the spec land
in one commit (strategy B). No durable queue row changes shape. The theory
clause lands separately, only with the owner's acceptance.

Mixed versions (detached tasks outlive the CLI and the Manager,
[WT-2a.13]):

- An old task under a new CLI accepts the unchanged STOP/KILL request, stops
  normally, and writes an acknowledgement after its terminal envelope. The
  new waiter returns at the terminal envelope (it comes first) and ignores
  the acknowledgement row as protocol noise ([MF-3] lines 306-307); the row
  is retired by existing cleanup like any other pre-upgrade residue. An old
  task that receives a new-shape STATUS (owner decision 1 (a)) rejects it
  (its `parse_control_request` returns `None` at the envelope-shape check,
  lines 113-117, so the row goes to the reply hook) and writes nothing; no
  in-tree sender exists.
- A new task under an old CLI stops normally. The old CLI's grace logic
  never sees an acknowledgement and returns on terminal evidence or at its
  overall deadline, exactly as it does today whenever it reaches terminal
  first. An old interactive client returns on terminal evidence (the normal
  path today) and sends its KILL only after the full budget when terminal
  evidence never appears.
- No compatibility lane and none needed ([ALT-WT-012]).

Rollback: revert the one landing commit (and the theory commit if it
landed). No data repair: no row shape changed; acknowledgement rows that a
reverted task writes again are read by the reverted CLI as today.

One-way doors: none. No identifier, storage, retention, or queue-name
change.

Post-deploy observation: after `weft task stop TID` on a live task,
`weft queue peek T{tid}.ctrl_out` shows the typed terminal envelope and no
row with `"status": "ack"`; `weft queue peek weft.log.tasks` shows the
`control_stop` event; the command exits 0. After a PAUSE written with
`weft queue write T{tid}.ctrl_in '{"command":"PAUSE"}'`, `weft queue peek
weft.state.tasks.<tid>` shows `projection.event` `control_pause` and
`T{tid}.ctrl_out` gains no row.

### Observable-difference register

Classes: A owner-decided removal, N none, C changed with no loss, D
diminution.

| # | Behavior | Before (`b7eed9f9`) | After | Class |
|---|----------|---------------------|-------|-------|
| R1 | STOP/KILL acknowledgement rows (`Consumer`, interactive, deferred, pipeline, `Monitor`) | one `{command, status: "ack", tid, timestamp, request_id?}` row on the responder's `ctrl_out`, after the terminal envelope | none; the terminal envelope, the `control_stop`/`control_kill` event, and the snapshot carry the outcome | A (owner ruling, 2026-11-03) |
| R2 | PAUSE/RESUME acknowledgement rows | one `{command, status: "ack", paused, ...}` row on `ctrl_out` per request, even when the state did not change | none; `control_pause`/`control_resume` event and snapshot when the state changes; nothing when it does not | A (same ruling) |
| R3 | Late STOP/KILL to an already-terminal task | acknowledgement row on `ctrl_out`, no state change | nothing written, no state change | A (part of R1) |
| R4 | Manager STOP "draining" acknowledgement | `{command: "STOP", status: "ack", draining: true}` on the Manager `ctrl_out` | none; the `control_stop` event with `draining=True` and PONG `should_stop=true` carry it | A; a STOP to a Manager that is already draining publishes nothing (`_begin_shutdown_drain` returns early), where today it still acknowledges; no reader: `_send_stop` names no reply route and `_await_manager_stop_confirmation` reads registry and process evidence only (E17); every other `"draining"` in `weft/` is a registry status value, a process title, or a serve-log field (E19) |
| R5 | Pipeline children's acknowledgements of the pipeline's broadcast STOP/KILL | each child acks on its own `ctrl_out` | none | A (part of R1); no reader |
| R6 | Observer deletes on the target `ctrl_out` | `_observe_control_envelopes` exact-deletes every visited non-completion row, other requesters' rows and stderr frames included | peek-only through a per-call cursor; deletes nothing | C: it no longer deletes rows it does not own ([MF-3] lines 317-320) |
| R7 | Public-signal grace window | the overall 2.0 s deadline (`CONTROL_SURFACE_WAIT_TIMEOUT`), extended by one `CONTROL_SURFACE_WAIT_INTERVAL` (0.05 s) per observed public signal (any `command`+`status` row): each signal resets the grace deadline to `observed_at + 0.05 s` (`_await_control_surface`), so under continuing control traffic the wait is unbounded | the overall 2.0 s deadline only; wait slices still capped at 0.05 s | C: unrelated traffic no longer extends the wait; the return to the same fallback comes at the overall deadline instead of a traffic-dependent, unbounded time after it |
| R8 | KILL-acknowledgement grace window | a KILL ack row (any requester) ends the wait 0.05 s after it was seen | the wait ends on terminal evidence or at 2.0 s | D, owner item 3: added failure-path latency; reachable only when an ack is visible without a terminal envelope (§3); then the runner/host fallback starts up to 1.95 s later than before (`CONTROL_SURFACE_WAIT_TIMEOUT` 2.0 s minus `CONTROL_SURFACE_WAIT_INTERVAL` 0.05 s); the normal path is unchanged because the terminal envelope precedes the ack |
| R9 | Interactive stop return | returns on terminal evidence or on its own acknowledgement; KILL after 8.7 s without either | returns on terminal evidence; KILL after 8.7 s without it | D, owner item 3: added failure-path latency. The acknowledgement follows completion publication (E14, interactive ordering test), so in the normal path the terminal read returns in the same turn or the next observer turn (today's loop at `weft/commands/run.py` lines 535-545 reads terminal evidence and then separately scans acknowledgements, so publication landing between the two reads can let the acknowledgement branch win even though publication succeeded). The full-budget wait requires terminal evidence to stay unavailable through the task-state surface as well as `ctrl_out`: when completion publication failed after its bounded retries, the old client returned at the ack, and the new one waits the full `INTERACTIVE_STOP_COMPLETION_TIMEOUT` (8.7 s) and sends KILL |
| R10 | Interactive STOP and escalation KILL rows on `ctrl_in` | carry `request_id=uuid4().hex` | `{"command": "STOP"}` / `{"command": "KILL"}` | C: no reader of that `request_id` remains (E3, E11) |
| R11 | Requester TIDs, requester queues, late-reply orphans, mixed-version failure to stop | none | none | N (the previous draft added all four; this plan adds none) |
| R12 | `ctrl_out` residue | acks, STATUS replies, stderr frames; deleted by `weft task stop`/`kill` visits and by the result-collection drain | no new control rows; stderr frames consumed by the result-collection drain as today; pre-upgrade residue and observer-addressed rows retired by existing cleanup (§4 hidden couplings) | N for new rows |
| R13 | STATUS reply destination (owner decision 1 (a)) | `ctrl_out`, for `{command}` or `{command, request_id}` | the request's `reply_to`, for exactly `{command, request_id, reply_to}`; other STATUS shapes rejected without reply | C: no in-tree sender (E5, E10); hand-written senders must name a reply queue (CHANGELOG) |
| R14 | Python client `Task.stop`/`Task.kill`, `client.tasks.stop`/`kill` | `stop_task`/`kill_task` | same functions, same results | N |
| R15 | `weft task stop`/`kill` exit codes and counts | 0 / 1 / 2; killed count on proof | same | N |
| R16 | `Consumer` deferral state | stores `request_id` for the ack echo | stores command and timestamp only | N (internal) |
| R17 | Control-convergence reducer state set (`weft/commands/control_convergence.py`, Task 4a) | states include `accepted`; actions include `wait`; evidence has `ack_seen`; four transitions reach or leave `accepted`, one budget-open wait | one state and one action fewer (seven states become six; six actions become five): no `accepted` and no `wait` (owner item 4 covers `wait`); no `ack_seen`; an open budget with no terminal or dead-runtime proof raises `ValueError` instead of returning `wait` | C: no production path changes; the five call sites receive the same decisions (E16) |
| R18 | Reducer test rows (`tests/commands/test_task_commands.py`) | four `ack_seen=True` setters (three table rows, one `ack` branch) plus the `accepted`-source and `wait for first evidence` rows | deleted; two rows rewritten onto `command_sent`; the `ack` test renamed to wrong-terminal only | N (test-only; coverage asserts still pass) |

R8 and R9 are D: each adds latency on a failure path, and under the
owner's no-diminution rule added latency is a diminution. Their acceptance
is the owner's (owner item 3, with the actual bounds). This plan does not
preserve either grace window and adds no machinery to justify one. No other
row is D.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `b7eed9f9` — `docs/specifications/05-Message_Flow_and_State.md`,
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/10-CLI_Interface.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/00-Quick_Reference.md`, and
  `docs/specifications/12-Pipeline_Composition_and_UX.md` at plan authoring
  time (2026-11-03). Plan type: implementation with spec revision.
- Promotion baseline identifier: diff base `0cd077e4` plus worktree state
  (the blocks were applied to the live specs at `0cd077e4`, which carry plans
  10, 9, 5 and 2 on top of `b7eed9f9`; every Current-text quote still matched
  exactly once); landing commit `29cd89bb` (strategy B lands spec and code
  together).

## Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/05-Message_Flow_and_State.md | B — atomic with the code | [MF-3] lines 203-207, 213-215, insertion after 230, 297-299, 315-316, 317-320, 326-329, 333-346, mapping 376-377 and 379-380; [MF-2] lines 157-159; [MF-5] line 1150; `## Related Plans` backlink |
| docs/specifications/01-Core_Components.md | B | [CC-2.4] lines 712-735 and 787-789; first `## Related Plans` backlink (line 36) |
| docs/specifications/10-CLI_Interface.md | B | [CLI-1.1.1] lines 304-313 and mapping 320-321; [CLI-1.3] lines 643-647; `## Related Plans` backlink |
| docs/specifications/07-System_Invariants.md | B | [QUEUE.2a] lines 109-118 (owner decision 1 (a)); [QUEUE.6] lines 147-148; OBS.12a lines 476-483; `## Related Plans` backlink |
| docs/specifications/00-Quick_Reference.md | B | Queue Names rows lines 15-16 and 25; Control Messages paragraph lines 155-160; STATUS row line 166; `## Related Plans` backlink |
| docs/specifications/12-Pipeline_Composition_and_UX.md | B | [PL-1.5] lines 247-248, 259, 281-282, [PL-3.2] lines 500-501, [PL-5.2] line 720 (owner decision 1 (a)); [PL-4.4] lines 673-674; `## Related Plans` backlink |

Why B and not A: every normative sentence in the delta describes the
absence of a reply. Under strategy A the promoted spec would say "STOP,
KILL, PAUSE, and RESUME write no reply" while shipped responders still write
acknowledgements on every call, and it would say "CLI control waiters delete
nothing" while `_observe_control_envelopes` still deletes; for the whole
interval between the two landings the spec would describe acknowledgements
the code no longer documents and the code would write rows the spec says do
not exist. The [MF-3] implementation mapping already names
`_observe_control_envelopes`, `_send_control`,
`_stop_interactive_run_session`, `_finalize_deferred_active_control`, and
`_interactive_handle_control`, whose behavior changes in the same edit. B
lands requirement text, mapping sentences, code, and the reciprocal `Spec:`
docstrings in one commit. No new section code is created. Mechanism: active
spec text edited in place; no planned or exploratory classification is used.

Clause audit: each replaced span below is quoted in full under "Current
text", and the paragraph after each block names every clause carried
forward, narrowed, or retired.

Owner decision 1, option (b) delta (one paragraph; the blocks below are
written for (a)). If the owner keeps STATUS on `ctrl_out`: the [MF-3]
request-envelope block keeps STATUS in the `{command}` / `{command,
request_id}` list and says "STATUS replies go to `ctrl_out`"; the
`ctrl_out` contents block adds "STATUS replies (recorded drift against
[WT-4] 'It is just a message', for a later plan)"; the inserted STATUS bullet
is dropped and the no-reply bullet stays; the [MF-3] no-scan sentence
(315-316) is unchanged; the [CC-2.4] block keeps STATUS with STOP, KILL,
PAUSE, and RESUME in the second envelope list and says its reply goes to
`ctrl_out`; [QUEUE.2a] is unchanged; the Quick Reference `ctrl_in` row drops
"or STATUS replies", the `ctrl_out` rows read "STATUS replies and terminal
notifications", the Control Messages paragraph keeps STATUS with the
reply-less list but names its `ctrl_out` reply, and the STATUS row is
unchanged; every [PL-*] block except [PL-4.4] is unchanged. Code under (b):
`weft/core/control_messages.py` is not edited; `weft/_constants.py` gets
only Task 4a's reducer edits (`CONTROL_CONVERGENCE_STATE_VALUES` and
`CONTROL_CONVERGENCE_ACTION_VALUES`), not Task 3 edit 1's
`CONTROL_REPLY_COMMANDS`; Task 3 edit 2 is dropped, and Task 3 edits 3 and
6 keep the STATUS branches on `_send_control_response` (so the method
stays for the two STATUS callers and the `_handle_control_message`
docstring reads "React to control requests; PING replies go to the
request's `reply_to`, STATUS replies to `ctrl_out`."); the STATUS test
edits in Task 2 (T4, T7b, T8b, T11, T12, T22-T24) are dropped. Text and
gates under (b): the CHANGELOG bullet drops its two STATUS sentences ("STATUS
now takes the PING envelope ..." and "a bare STATUS is rejected without
reply") and "or sent a bare STATUS" and "or name a `reply_to`"; the
proposed theory clause and the [WT-2] and [WT-4] proposals name PING (not
"PING or STATUS") as the requester that receives a reply, and record STATUS
on `ctrl_out` as drift; the Task 3 grep gate and the second final
verification grep print `_send_control_response` in
`weft/core/tasks/base.py`, in `weft/core/tasks/pipeline.py`, and, because
T11's rewrite is dropped under (b), at the retained call in
`tests/tasks/test_task_observability.py:709` (both gates scan `weft tests`);
their notes say so.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the request-envelope bullet that opens "a PING request is exactly" and closes "alternate casing are not supported." (lines 203-207)

Current text:

> - a PING request is exactly `{command, request_id, reply_to}`. `command` is
>   exactly `PING`; `request_id` and `reply_to` are nonblank strings; and
>   `reply_to` is the requester's own `ctrl_in` queue. Other control requests
>   retain the exact `{command}` or `{command, request_id}` envelopes. Raw
>   command strings, extra keys, and alternate casing are not supported.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- a PING or STATUS request is exactly `{command, request_id, reply_to}`.
  `command` is exactly `PING` or `STATUS`; `request_id` and `reply_to` are
  nonblank strings; and `reply_to` is the requester's own `ctrl_in` queue.
  STOP, KILL, PAUSE, and RESUME requests retain the exact `{command}` or
  `{command, request_id}` envelopes; these commands write no reply, so their
  `request_id` is echoed nowhere. Raw command strings, extra keys, and
  alternate casing are not supported.
```

Carried forward: PING exact shape, nonblank strings, own-`ctrl_in` rule,
the exact envelopes of the other commands, unsupported forms. Changed:
STATUS takes the PING shape (owner decision 1 (a)); the reply-less commands
are named.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the `ctrl_out` contents bullet that opens "`ctrl_out` carries non-PONG task-local replies" and closes "make removed reply formats current contracts." (lines 213-215)

Current text:

> - `ctrl_out` carries non-PONG task-local replies and terminal notifications.
>   Readers ignore malformed or unrelated replies as protocol noise; this
>   robustness does not make removed reply formats current contracts.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- `ctrl_out` carries facts addressed to the task's observers: terminal
  notifications, persistent `work_item_completed` boundaries, and interactive
  stderr stream frames. No control reply is written to `ctrl_out`. Readers
  ignore malformed or unrelated rows, including acknowledgement rows written
  by a task from before this contract, as protocol noise; this robustness
  does not make removed reply formats current contracts.
```

Carried forward: terminal notifications; the protocol-noise rule. Made
exact: the completion boundaries (already published there, lines 273-275)
and interactive stderr frames (`weft/core/tasks/interactive.py` lines 360
and 462) are named. Retired: "non-PONG task-local replies".

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — insert two bullets after the PING bullet that closes "it echoes `request_id` and omits `reply_to`" (after line 230)

Nothing is removed.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- `STATUS` writes exactly one JSON STATUS reply to `reply_to`, never to the
  target's `ctrl_out`. The reply contains `command="STATUS"`, `status="ok"`,
  `tid`, `timestamp`, and the task's control snapshot fields (`task_status`,
  `process_title_error`, `paused`, `should_stop`, `runner`, optional
  `activity`, `waiting_on`, and best-effort `runtime`, and any fields its
  task type adds); a pipeline
  adds `status_queue` after refreshing `P{tid}.status`. It echoes
  `request_id` and omits `reply_to`
- STOP, KILL, PAUSE, and RESUME write no reply. Each publishes its outcome
  where the task already publishes state: the lifecycle event in
  `weft.log.tasks` (`control_stop`, `control_kill`, `control_pause`, or
  `control_resume`), the task-owned `weft.state.tasks.<tid>` snapshot, and,
  for STOP and KILL, the typed terminal envelope on `ctrl_out`. A Manager
  STOP publishes `control_stop` with `draining=true` when its drain begins
  and its terminal state when the drain completes. A requester observes
  these facts as any observer does. A STOP or KILL to a task that is already
  terminal, a PAUSE to a paused task, and a RESUME to a running task change
  nothing and write nothing
```

New text. The STATUS field list is taken from
`BaseTask._control_response_payload` (base.py line 2089) and
`BaseTask._control_snapshot_fields` (line 1870); the pipeline clause from
`PipelineTask._handle_control_command` (pipeline.py lines 898-906); the
Manager clause from `Manager._begin_shutdown_drain` (manager.py lines
4829-4834).

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the opening of the interactive-completion paragraph, from "Interactive STOP/KILL, external termination signals" through "It does not publish local completion while the session can" (lines 297-299)

Current text:

> Interactive STOP/KILL, external termination signals, and parent loss record
> the terminal state on the owner thread, unwind the session, and publish completion after
> its final stdout/stderr frames. Queued control is acknowledged after this unwind. It does not publish local completion while the session can

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
Interactive STOP/KILL, external termination signals, and parent loss record
the terminal state on the owner thread, unwind the session, and publish completion after
its final stdout/stderr frames. Queued control writes no reply. It does not publish local completion while the session can
```

Carried forward: owner-thread terminal recording, unwind, completion after
final frames, the sentence that continues on line 300. Retired: "Queued
control is acknowledged after this unwind."

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the closing sentence of the non-request-row bullet, from "dead-task sweep retires it. PING/PONG uses no target-`ctrl_out` scan" through "or separate polling loop" (lines 315-316)

Current text:

>   dead-task sweep retires it. PING/PONG uses no target-`ctrl_out` scan, reply
>   sweep, dynamic watcher membership, or separate polling loop

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
  dead-task sweep retires it. PING/PONG and STATUS use no target-`ctrl_out`
  scan, reply sweep, dynamic watcher membership, or separate polling loop
```

Carried forward: the sweep sentence and the full no-scan list. Changed:
STATUS joins PING/PONG (owner decision 1 (a)). The rest of the bullet
(lines 308-314) is unchanged and already covers any reply, not only PONG.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the keyed-reply ownership bullet that opens "non-PONG keyed control replies remain owned" and closes "terminal envelopes remain untouched" (lines 317-320)

Current text:

> - non-PONG keyed control replies remain owned and retired by the prober that
>   issued the `request_id`: an exact matching reply is deleted on match, and the
>   prober may sweep rows bearing its own request ID at timeout or abandonment.
>   Rows keyed to another request and terminal envelopes remain untouched

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- a control requester consumes only the rows on its own reply queue, and a
  control waiter never deletes rows on a target's `ctrl_out`: terminal
  envelopes, completion boundaries, stream frames, and unrelated rows there
  are left to their own readers and cleanup
```

Carried forward: an owner retires only its own replies; others' rows and
terminal envelopes stay untouched. Retired: the keyed-reply sweep on
`ctrl_out` (there are no replies there to own).

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the CLI control-waiter bullet that opens "CLI control waiters may accept a typed terminal" and closes "remain progress evidence only" (lines 326-329)

Current text:

> - CLI control waiters may accept a typed terminal `ctrl_out` envelope as
>   sufficient stop/kill completion proof for that task without replaying the
>   global task log first; ordinary control acknowledgements remain progress
>   evidence only

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- CLI control waiters may accept a typed terminal `ctrl_out` envelope as
  sufficient stop/kill completion proof for that task without replaying the
  global task log first. They read `ctrl_out` peek-only through a cursor that
  starts at the oldest row and advances past each row read, delete nothing
  there, and return on terminal evidence or at their overall deadline
```

Carried forward: terminal envelope as sufficient proof without log replay.
Retired: acknowledgements as progress evidence (none exist). Added: the
peek-only cursor and the single deadline.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] — replace the four bullets from "active STOP/KILL may delete the structured `ctrl_in` message" through "host-observable PID exists." (lines 333-346)

Current text:

> - active STOP/KILL may delete the structured `ctrl_in` message as an internal
>   handoff detail, but public acknowledgement remains the post-unwind
>   `ctrl_out` reply plus the terminal task-log event on the main task thread
> - active STOP/KILL deferral applies to the structured JSON control envelope.
>   STOP/KILL envelopes that include `request_id` must echo that value in the
>   eventual post-unwind acknowledgement.
> - every other reply or acknowledgement to a valid keyed control request,
>   including PAUSE and RESUME, also echoes the request's `request_id`.
> - a `KILL` acknowledgement means the task accepted the command; it is progress
>   evidence only. Readers and CLI control helpers must not promote ack-only
>   evidence into terminal lifecycle state. Kill success is proven by terminal
>   `killed` evidence, by observed controlled host PIDs becoming dead after a
>   kill action, or by an authoritative runner control surface when no
>   host-observable PID exists.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- active STOP/KILL may delete the structured `ctrl_in` message as an internal
  handoff detail; the public outcome is the terminal task-log event, written
  on the main task thread after the runtime unwinds, and its typed terminal
  `ctrl_out` envelope
- active STOP/KILL deferral applies to the structured JSON control envelope.
  The deferral carries the command and its queue timestamp; finalization
  after the unwind writes no reply.
- every reply to a valid keyed control request (PONG, STATUS) echoes the
  request's `request_id`.
- kill success is proven by terminal `killed` evidence, by observed
  controlled host PIDs becoming dead after a kill action, or by an
  authoritative runner control surface when no host-observable PID exists.
  Readers and CLI control helpers infer no lifecycle state from any other
  row.
```

Carried forward: the internal handoff delete; the terminal task-log event on
the main thread after unwind; deferral scope; the request-id echo for
replies that exist; the three kill-proof sources verbatim; the rule against
promoting non-proof rows. Retired: "public acknowledgement remains the
post-unwind `ctrl_out` reply", the STOP/KILL `request_id` echo, "including
PAUSE and RESUME", and "a `KILL` acknowledgement means the task accepted the
command; it is progress evidence only".

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] `_Implementation mapping_` — replace "`weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_handle_control` acknowledges after" through "shutdown. Ordering and retry coverage is in" (lines 376-377)

Current text:

> `weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_handle_control` acknowledges after
> shutdown. Ordering and retry coverage is in

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
`weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_handle_control` publishes terminal
proof after shutdown and writes no reply. Ordering and retry coverage is in
```

### `docs/specifications/05-Message_Flow_and_State.md` [MF-3] `_Implementation mapping_` — replace the sentence "`weft/commands/tasks.py::_observe_control_envelopes` consumes ordinary replies while retaining valid completion facts for every owner of a shared control route." (lines 379-380)

Current text:

> `weft/commands/tasks.py::_observe_control_envelopes` consumes ordinary replies while
> retaining valid completion facts for every owner of a shared control route.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
`weft/commands/tasks.py::_observe_control_envelopes` reads a control route
peek-only through a per-call cursor and deletes nothing, so valid completion
facts stay readable for every owner of a shared control route.
```

The rest of the mapping (lines 348-375, 378, 381) is unchanged: it names
`BaseTask._handle_control_command`, `Consumer._poll_active_control_once`,
`Consumer._finalize_deferred_active_control`,
`PipelineTask._handle_control_command`, `Manager._handle_control_command`,
`weft/commands/tasks.py::_send_control`, and
`weft/commands/run.py::_stop_interactive_run_session`, which keep owning the
same boundaries.

### `docs/specifications/05-Message_Flow_and_State.md` [MF-2] — replace "a successful work outcome uses only success acknowledgement for its" through "control without applying reserved policy to that completed input," (lines 157-159)

Current text:

> - a successful work outcome uses only success acknowledgement for its
>   input; deferred STOP/KILL completes task termination and acknowledges
>   control without applying reserved policy to that completed input,

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- a successful work outcome uses only success acknowledgement for its
  input; deferred STOP/KILL completes task termination without applying
  reserved policy to that completed input,
```

Carried forward: success-only acknowledgement of the input; no reserved
policy on that completed input; line 160 ("even when its acknowledgement
delete failed", the reserved-row delete) continues unchanged. Retired:
"and acknowledges control" (the control row is acknowledged at deferral,
`consumer.py` line 672; the clause named the reply).

### `docs/specifications/05-Message_Flow_and_State.md` [MF-5] — replace the line "with the existing STOP/ACK, cancellation, deadline, and KILL behavior; it performs" (line 1150)

Current text:

> with the existing STOP/ACK, cancellation, deadline, and KILL behavior; it performs

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
with the existing STOP, cancellation, deadline, and KILL behavior; it performs
```

### `docs/specifications/01-Core_Components.md` [CC-2.4] — replace the first six bullets, from "`ctrl_in` accepts strict structured JSON request envelopes." through "round trips exist for live tasks" (lines 712-735)

Current text:

> - `ctrl_in` accepts strict structured JSON request envelopes. A PING request is
>   exactly `{command, request_id, reply_to}`. `command` is exactly `PING`;
>   `request_id` and `reply_to` are nonblank strings; and `reply_to` is the
>   requester's own `ctrl_in` queue: the configured queue of a long-lived task,
>   or `T{tid}.ctrl_in` for an ephemeral requester that minted a TID for this
>   probe. In both cases the requester is the sole reader of that queue. Other
>   requests contain exactly the required `command` key and optional
>   nonblank-string `request_id`, with `command` exactly one of `STATUS`, `STOP`,
>   `KILL`, `PAUSE`, or `RESUME`. Raw command strings, extra keys, and alternate
>   casing are not supported requests
> - every Weft-owned controller uses the shared control-envelope encoder.
>   `BaseTask` uses the matching parser and rejects malformed, non-object, or
>   unsupported envelopes without treating them as commands. After strict
>   request parsing, a non-request row is passed to the task's protected reply
>   hook and then exact-acknowledged once so it cannot block a later valid
>   request. A PONG never enters PING request dispatch and never produces a
>   response
> - a valid PING writes exactly one PONG to `reply_to`; it never writes that
>   PONG to the responder's `ctrl_out`. The PONG echoes `request_id` and does not
>   echo `reply_to`
> - `ctrl_out` carries other task-local replies and terminal notifications.
>   Readers ignore malformed or unrelated replies as protocol noise; this
>   robustness does not make removed reply formats current contracts
> - `STOP`, `KILL`, `STATUS`, and `PING` round trips exist for live tasks

Replacement (`docs/specifications/01-Core_Components.md`):

```text
- `ctrl_in` accepts strict structured JSON request envelopes. A PING or STATUS
  request is exactly `{command, request_id, reply_to}`. `command` is exactly
  `PING` or `STATUS`; `request_id` and `reply_to` are nonblank strings; and
  `reply_to` is the requester's own `ctrl_in` queue: the configured queue of a
  long-lived task, or `T{tid}.ctrl_in` for an ephemeral requester that minted
  a TID for this request. In both cases the requester is the sole reader of
  that queue. Other requests contain exactly the required `command` key and
  optional nonblank-string `request_id`, with `command` exactly one of `STOP`,
  `KILL`, `PAUSE`, or `RESUME`. Raw command strings, extra keys, and alternate
  casing are not supported requests
- every Weft-owned controller uses the shared control-envelope encoder.
  `BaseTask` uses the matching parser and rejects malformed, non-object, or
  unsupported envelopes without treating them as commands. After strict
  request parsing, a non-request row is passed to the task's protected reply
  hook and then exact-acknowledged once so it cannot block a later valid
  request. A PONG or STATUS reply never enters request dispatch and never
  produces a response
- a valid PING writes exactly one PONG, and a valid STATUS exactly one STATUS
  reply, to `reply_to`; neither is written to the responder's `ctrl_out`.
  Each echoes `request_id` and does not echo `reply_to`
- `STOP`, `KILL`, `PAUSE`, and `RESUME` write no reply. The responder makes
  the transition and publishes it where it already publishes state: the
  lifecycle event in `weft.log.tasks`, its `weft.state.tasks.<tid>` snapshot,
  and, for a terminal transition, the typed terminal envelope on `ctrl_out`
- `ctrl_out` carries terminal notifications and other facts addressed to the
  task's observers, never a control reply. Readers ignore malformed or
  unrelated rows as protocol noise; this robustness does not make removed
  reply formats current contracts
- `STOP` and `KILL` are accepted by every live task, and `STATUS` and `PING`
  round trips exist for live tasks
```

Carried forward: strict envelopes, both requester kinds, sole reader, the
shared encoder and parser, rejected-row handling, PONG routing and echo
rules, the protocol-noise rule, support on every live task. Changed: STATUS
takes the PING shape and route; STOP, KILL, PAUSE, and RESUME are named as
reply-less; "`ctrl_out` carries other task-local replies" is retired.

### `docs/specifications/01-Core_Components.md` [CC-2.4] — replace the bullet "every reply or acknowledgement to a valid keyed request echoes its" through "state events when the task is already terminal" (lines 787-789)

Current text:

> - every reply or acknowledgement to a valid keyed request echoes its
>   `request_id`. Handled STOP/KILL commands must not emit duplicate terminal
>   state events when the task is already terminal

Replacement (`docs/specifications/01-Core_Components.md`):

```text
- every reply to a valid keyed request echoes its `request_id`. Handled
  STOP/KILL commands must not emit duplicate terminal state events when the
  task is already terminal
```

Carried forward: the echo rule for replies; the no-duplicate-terminal rule.
Retired: "or acknowledgement".

### `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1] — replace the interactive queue-routing bullet and the STOP paragraph, from "interactive stderr, status, and terminal control replies go through" through "acknowledgement remains after task-side runtime unwind." (lines 304-313)

Current text:

> - interactive stderr, status, and terminal control replies go through
>   `T{tid}.ctrl_out`; non-interactive command `stream_output` writes stdout and
>   stderr stream frames to `T{tid}.outbox`, with stderr treated as live
>   diagnostics rather than result data
>
> After requesting STOP, the interactive client observes matching
> acknowledgement or existing terminal evidence for the named
> `INTERACTIVE_STOP_COMPLETION_TIMEOUT` budget before escalating to KILL.
> Terminal evidence ends the wait promptly; acknowledgement remains after
> task-side runtime unwind.

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
- interactive stderr stream frames and terminal notifications go through
  `T{tid}.ctrl_out`; non-interactive command `stream_output` writes stdout and
  stderr stream frames to `T{tid}.outbox`, with stderr treated as live
  diagnostics rather than result data

After requesting STOP, the interactive client observes terminal evidence for
the named `INTERACTIVE_STOP_COMPLETION_TIMEOUT` budget before escalating to
KILL. STOP gets no reply ([MF-3]); terminal evidence ends the wait promptly.
```

Carried forward: interactive stderr and terminal notifications on
`ctrl_out`; the non-interactive `stream_output` clause verbatim; the named
budget; prompt return on terminal evidence; KILL escalation. Retired:
"status" and "control replies" on `ctrl_out`, "matching acknowledgement",
"acknowledgement remains after task-side runtime unwind".

### `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1] `_Implementation mapping_` — replace "`tests/commands/test_active_interactive_lifecycle.py` covers the active CLI" through "and keyed STOP settlement." (lines 320-321)

Current text:

> `tests/commands/test_active_interactive_lifecycle.py` covers the active CLI
> owner, cancellation, EOF, and keyed STOP settlement.

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
`tests/commands/test_active_interactive_lifecycle.py` covers the active CLI
owner, cancellation, EOF, and STOP settlement on terminal evidence.
```

### `docs/specifications/10-CLI_Interface.md` [CLI-1.3] — replace the task-control paragraph that opens "`weft task kill` reports a killed count" and closes "command-acceptance progress, not terminal proof." (lines 643-647)

Current text:

> `weft task kill` reports a killed count only when the command helper observes
> terminal `killed` evidence, controlled host-process death after a kill action,
> or an authoritative runner kill result for a handle with no host-observable
> PID. A task-local `KILL` acknowledgement is command-acceptance progress, not
> terminal proof.

Replacement (`docs/specifications/10-CLI_Interface.md`):

```text
`weft task stop` and `weft task kill` send a STOP or KILL that gets no reply
and wait for the target's terminal evidence for one bounded observation
budget before any runner or host fallback ([MF-3]). They read the target's
`ctrl_out` peek-only and delete nothing there. `weft task kill` reports a
killed count only when the command helper observes terminal `killed`
evidence, controlled host-process death after a kill action, or an
authoritative runner kill result for a handle with no host-observable PID.
```

Carried forward: the kill-count proof rule verbatim. Retired: "A
task-local `KILL` acknowledgement is command-acceptance progress, not
terminal proof." Added: what the commands send, read, and wait for.

### `docs/specifications/07-System_Invariants.md` [QUEUE.2a] — replace the paragraph that opens "A PING request is exactly `{command, request_id," and closes "and never produces another PONG." (lines 109-118; owner decision 1 (a) only)

Current text:

> A PING request is exactly `{command, request_id, reply_to}` with `command`
> exactly `PING` and both other values nonblank strings. `reply_to` is the
> requester's own `ctrl_in`: a long-lived task's configured queue or an
> ephemeral requester's `T{tid}.ctrl_in`. The requester is the sole reader. The
> target writes exactly one PONG there and never to its own `ctrl_out`. Other requests are exactly `command` plus optional
> nonblank-string `request_id`, with `command` one of `STATUS`, `STOP`, `KILL`,
> `PAUSE`, or `RESUME`. Rejected rows are passed to reply dispatch, then
> exact-acknowledged once. Every reply to a valid keyed request echoes its
> `request_id`; PONG does not echo `reply_to`, never enters PING request dispatch,
> and never produces another PONG.

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
A PING or STATUS request is exactly `{command, request_id, reply_to}` with
`command` exactly `PING` or `STATUS` and both other values nonblank strings.
`reply_to` is the requester's own `ctrl_in`: a long-lived task's configured
queue or an ephemeral requester's `T{tid}.ctrl_in`. The requester is the sole
reader. The target writes exactly one reply there (a PONG for PING) and never
to its own `ctrl_out`. Other requests are exactly `command` plus optional
nonblank-string `request_id`, with `command` one of `STOP`, `KILL`, `PAUSE`,
or `RESUME`; they get no reply. Rejected rows are passed to reply dispatch,
then exact-acknowledged once. Every reply to a valid keyed request echoes its
`request_id`; a reply does not echo `reply_to`, never enters request dispatch,
and never produces another reply.
```

Carried forward: PING shape, sole-reader rule, no reply on `ctrl_out`,
rejected-row handling, the echo rules, no reply dispatch. Changed: STATUS
takes the PING shape; the other commands are named as reply-less. Under
owner decision 1 (b) this paragraph is unchanged: it says nothing about
acknowledgements, and the STOP/KILL/PAUSE/RESUME envelope does not change.

### `docs/specifications/07-System_Invariants.md` [QUEUE.6] — replace "acknowledgement failed; task termination and control acknowledgement" through "still complete. A later independent control operation with no active row" (lines 147-148)

Current text:

>   acknowledgement failed; task termination and control acknowledgement
>   still complete. A later independent control operation with no active row

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
  acknowledgement failed; task termination still completes. A later
  independent control operation with no active row
```

Carried forward: the success-acknowledgement failure case, task termination
completing, and the sentence that continues on line 149. Retired: "and
control acknowledgement".

### `docs/specifications/07-System_Invariants.md` OBS.12a — replace the bullet that opens "**OBS.12a**: task-local control acknowledgements are not lifecycle" and closes "`weft/commands/tasks.py`." (lines 476-483)

Current text:

> - **OBS.12a**: task-local control acknowledgements are not lifecycle
>   mutations. A `KILL` ack can shorten the path to fallback control, but it
>   must not be reported as terminal `killed` state unless terminal evidence,
>   controlled host-process death, or an authoritative no-host-PID runner kill
>   proof is also present. Command-layer result classification is selected by
>   the pure reducer in `weft/commands/control_convergence.py`; queue waits,
>   runner fallback, and process termination remain owned by
>   `weft/commands/tasks.py`.

Replacement (`docs/specifications/07-System_Invariants.md`):

```text
- **OBS.12a**: the result of a state-changing control command (STOP,
  KILL, PAUSE, RESUME) is the task's own published transition, never a
  reply row. `weft task kill` reports `killed` only on
  terminal `killed` evidence, controlled host-process death, or an
  authoritative no-host-PID runner kill proof, and no other `ctrl_out` row
  shortens or ends a control wait. Command-layer result classification of
  the evidence observed after the wait is selected by the pure reducer in
  `weft/commands/control_convergence.py`, which has no acknowledgement
  input and no wait decision; queue waits, runner fallback, and process
  termination remain owned by `weft/commands/tasks.py`.
```

Carried forward: the three kill-proof sources; reducer ownership; wait,
fallback, and termination ownership. Retired: "A `KILL` ack can shorten the
path to fallback control" (register R8). Added: the reducer classifies
post-wait evidence only (Task 4a; register R17).

### `docs/specifications/00-Quick_Reference.md` Queue Names — replace the `T{tid}.ctrl_in` and `T{tid}.ctrl_out` rows (lines 15-16)

Current text:

> | `T{tid}.ctrl_in` | Control messages (STOP, KILL, STATUS, PING, PAUSE, RESUME) |
> | `T{tid}.ctrl_out` | Non-PONG control responses and terminal notifications |

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
| `T{tid}.ctrl_in` | Control requests (STOP, KILL, STATUS, PING, PAUSE, RESUME), and PONG or STATUS replies addressed to this TID as requester |
| `T{tid}.ctrl_out` | Terminal notifications and other observer-addressed facts; never a control reply |
```

### `docs/specifications/00-Quick_Reference.md` Queue Names — replace the `P{tid}.ctrl_out` row (line 25)

Current text:

> | `P{tid}.ctrl_out` | Non-PONG pipeline control replies |

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
| `P{tid}.ctrl_out` | Pipeline terminal notifications; never a control reply |
```

### `docs/specifications/00-Quick_Reference.md` Control Messages — replace the paragraph that opens "Every non-PING request is a JSON object" and closes "are not current request formats." (lines 155-160)

Current text:

> Every non-PING request is a JSON object containing exactly `command` and, when
> keyed, `request_id`. A PING is exactly `{command, request_id, reply_to}`;
> `request_id` and `reply_to` are nonblank strings, and `reply_to` is the
> requester's own `ctrl_in`. `command` is one of the exact uppercase values
> below. Raw strings, extra keys, and case-normalized variants are not current
> request formats.

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
Every STOP, KILL, PAUSE, or RESUME request is a JSON object containing exactly
`command` and, when keyed, `request_id`; these commands get no reply, and
their outcome is the task's published state change. A PING or STATUS is
exactly `{command, request_id, reply_to}`; `request_id` and `reply_to` are
nonblank strings, and `reply_to` is the requester's own `ctrl_in`. `command`
is one of the exact uppercase values below. Raw strings, extra keys, and
case-normalized variants are not current request formats.
```

### `docs/specifications/00-Quick_Reference.md` Control Messages — replace the STATUS row (line 166)

Current text:

> | `STATUS` | Emit current task-local status on `ctrl_out` |

Replacement (`docs/specifications/00-Quick_Reference.md`):

```text
| `STATUS` | Writes one current task-local status reply to the requester's named `reply_to` and echoes `request_id` |
```

### `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-1.5] — replace "This is why pipeline child tasks do **not** report to `ctrl_out`. `ctrl_out`" through "is public and observer-facing for control replies. Child-to-owner coordination" (lines 247-248; owner decision 1 (a))

Current text:

> This is why pipeline child tasks do **not** report to `ctrl_out`. `ctrl_out`
> is public and observer-facing for control replies. Child-to-owner coordination

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
This is why pipeline child tasks do **not** report to `ctrl_out`. `ctrl_out`
is public and observer-facing for terminal notifications. Child-to-owner coordination
```

### `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-1.5] — replace the bullet "`ctrl_out` carries control replies for the pipeline run as a unit" (line 259; owner decision 1 (a))

Current text:

> - `ctrl_out` carries control replies for the pipeline run as a unit

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
- `ctrl_out` carries terminal notifications for the pipeline run as a unit
```

### `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-1.5] — replace "Weft follows the same shape. `outbox` stays pure data. `ctrl_out` stays the" through "control reply surface. Pipelines get one additional public observability" (lines 281-282; owner decision 1 (a))

Current text:

> Weft follows the same shape. `outbox` stays pure data. `ctrl_out` stays the
> control reply surface. Pipelines get one additional public observability

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
Weft follows the same shape. `outbox` stays pure data. `ctrl_out` stays the
terminal-notification surface. Pipelines get one additional public observability
```

### `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-3.2] — replace "Public `ctrl_in` remains command ingress. Public `ctrl_out` remains the" through "pipeline's public control-reply surface. Public `P{pipeline_tid}.status`" (lines 500-501; owner decision 1 (a))

Current text:

> Public `ctrl_in` remains command ingress. Public `ctrl_out` remains the
> pipeline's public control-reply surface. Public `P{pipeline_tid}.status`

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
Public `ctrl_in` remains command ingress. Public `ctrl_out` remains the
pipeline's public terminal-notification surface. Public `P{pipeline_tid}.status`
```

### `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-4.4] — replace step 5 of "Baseline fail-fast propagation", "Active children apply their normal reserved-queue semantics before" through "acknowledging or terminating." (lines 673-674)

Current text:

> 5. Active children apply their normal reserved-queue semantics before
>    acknowledging or terminating.

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
5. Active children apply their normal reserved-queue semantics before
   terminating.
```

Retired: "acknowledging or" (children write no reply to the pipeline's
reply-less broadcast; true under either option of owner decision 1).

### `docs/specifications/12-Pipeline_Composition_and_UX.md` [PL-5.2] — replace the bullet "the pipeline task's own `ctrl_out` control replies" (line 720; owner decision 1 (a))

Current text:

> - the pipeline task's own `ctrl_out` control replies

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):

```text
- the pipeline task's own `ctrl_out` terminal notifications
```

### `## Related Plans` backlinks (all six files)

Append one bullet directly under the `## Related Plans` heading of 05 (line
2095), 01 (the first one, line 36), 10 (line 1094), 07 (line 1392), 00
(line 248), and 12 (line 940):

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):

```text
- [Control Replies Only on Request: Outcomes Are Broadcast](../plans/2026-11-03-control-replies-only-on-request-plan.md) removes the STOP, KILL, PAUSE, and RESUME replies, whose outcome is the published state change, and answers STATUS on the requester's `reply_to` ([MF-3]).
```

Use the same bullet text in 01 (citing [CC-2.4]), 10 (citing [CLI-1.1.1],
[CLI-1.3]), 07 (citing [QUEUE.2a], OBS.12a), 00 (citing the Queue Names and
Control Messages tables), and 12 (citing [PL-1.5]). Those five bullets are
not Replacement blocks; Task 5 verifies them by grep.

### Spans audited and unchanged

- 05 [MF-3] lines 208-212 (rejected rows "exact-acknowledged without a
  `ctrl_out` reply"): "acknowledged" is the broker acknowledgement of the
  `ctrl_in` row, which stays; the sentence is already true.
- 05 [MF-3] lines 219-225 (PING requester lifecycle): PING-specific and
  unchanged; the STATUS route reuses the same rules through the inserted
  bullet and the request-envelope bullet.
- 05 [MF-3] lines 306-307 ("readers must ignore ordinary control replies"):
  kept; it is the rule that makes pre-upgrade acknowledgement rows harmless
  (§4 mixed versions).
- 05 [MF-3] lines 308-314 (non-request row to the reply hook): already
  generic over replies.
- 05 [MF-3] lines 324-325 (shared task-evidence reader): unchanged
  behavior.
- 05 line 1108 ("One-shot public snapshots attached to ping/stop/kill
  results inherit the point-snapshot contract without changing command
  acknowledgement, success or failure decisions"): generic "command
  acknowledgement" wording that does not promise a state-change reply row;
  still true after this plan; unchanged.
- 05 [MF-3] lines 348-375, 378, 381 (mapping): unchanged owners (see the
  mapping block above).
- 01 [CC-2.4] line 814 ("control replies should stay off the data plane"):
  still true of PONG and STATUS replies.
- `docs/specifications/02-TaskSpec.md` line 174 (a JSON comment
  describing `ctrl_out` as "Status, error messages reported by the Task"):
  an example comment, not a reply contract; left as is.
- `docs/specifications/03-Manager_Architecture.md` [MA-3]: Manager stop
  confirmation reads registry and process evidence and names no
  acknowledgement (E17); unchanged.
- 07 [QUEUE.2a] under owner decision 1 (b): unchanged (see its block).
- 12 [PL-1.5] lines 239-240 ("pipeline `ctrl_in` / `ctrl_out` are the public
  control plane"): still true; the plane carries commands in and facts out.
- `docs/specifications/14-Python_API_Surfaces.md` line 332
  (`TaskPingResult(..., acknowledged: bool, ...)`): PING, unchanged.
- Spans naming `weft/commands/control_convergence.py` (Task 4a): 07
  REDUCER mapping line 72, 05 [MF-3] mapping line 371, 08 [TS-1] line 370
  ("command-control reducer tables"), and 09 line 197 ("Decide when
  STOP/KILL has enough evidence to report success, fallback, rejection, or
  timeout."). None names the `accepted` state, `ack_seen`, or a reducer
  wait (`grep -rn 'control_convergence\|ack_seen' docs/specifications/`
  returns only these four lines and OBS.12a line 481), and each stays true
  after Task 4a; unchanged. Only OBS.12a changes (its block above).

### Theory clause (proposed; lands only with the owner's acceptance, owner decision 2)

Not a Replacement block: `docs/program-theory.md` is not a spec file, the
change is class-5 theory text, and it lands in its own commit after the
owner accepts it. `bin/check-doc-paths` does not check it.

[WT-2a.12] (lines 335-341). Current text:

> - **[WT-2a.12] Everything is a task, and every protocol is a message.** Any
>   Weft code that exchanges facts with a task does so as a task: it holds a
>   TID from SimpleBroker's hybrid-timestamp generator and receives replies on
>   its own `T{tid}.ctrl_in`. A protocol is an ordinary message on an ordinary
>   queue, pushed to a queue its recipient already watches; no caller has a
>   privileged side-channel ([MF-3]). (Owner, 2026-09-17: "In weft, everything
>   is a task" and "There is no code without a task identity.")

Proposed (`docs/program-theory.md`), pending owner acceptance:

```text
- **[WT-2a.12] Everything is a task, and every protocol is a message.** Any
  code that needs to receive a message has a task identity: it holds a TID
  from SimpleBroker's hybrid-timestamp generator and owns the
  `T{tid}.ctrl_in` where messages addressed to it are delivered. To receive
  a message is to have one addressed to you and delivered to a queue you
  own; to observe a broadcast is to read a fact addressed to observers from
  the queue it was published on. A STOP, KILL, PAUSE, or RESUME requester
  observes and does not receive: it reads the published outcome (for STOP
  and KILL, the terminal envelope on the target's `ctrl_out`) as any
  observer does, so it needs no identity. A PING or STATUS requester
  receives a reply addressed to it, so it holds a TID and owns the
  `ctrl_in` the reply lands on. A protocol is an ordinary message on an
  ordinary queue, pushed to a queue its recipient already watches; no
  caller has a privileged side-channel ([MF-3]).
  (Owner, 2026-09-17: "In weft, everything is a task." Owner, 2026-11-03:
  "Any code that needs to receive a message has a task identity." and "It
  seems like just relying on the broadcast is operationally much simpler.")
```

The clause's first sentence is the owner's 2026-11-03 sentence verbatim.
The 2026-09-17 quote "There is no code without a task identity" is dropped
here and from [WT-2] item 1 (below): a `weft task stop` process holds no
task identity after this plan, so the quote would become false. The
explanation rests on the difference between receiving and observing, not
on whether a reply exists: a STOP requester still reads a message (the
broadcast terminal envelope), but that message is addressed to observers,
not to it.

[WT-2] item 1 "Everything is a task" (lines 160-165), consistency edit.
It still carries the universal requester wording and the 2026-09-17 quote
retired from [WT-2a.12]. Current text:

> 1. **Everything is a task (`T{tid}.*`)**:
>    A Manager is a task that spawns and supervises other tasks. An agent is a
>    task. A pipeline is a task. Even a short-lived CLI requester (`weft status`,
>    `PING`, result waiter) mints a TID and receives replies on its own
>    `T{tid}.ctrl_in`. The owner stated this invariant on 2026-09-17: "In weft,
>    everything is a task" and "There is no code without a task identity."

Proposed (`docs/program-theory.md`), pending owner acceptance:

```text
1. **Everything is a task (`T{tid}.*`)**:
   A Manager is a task that spawns and supervises other tasks. An agent is a
   task. A pipeline is a task. Any code that needs to receive a message has
   a task identity: even a short-lived CLI requester that receives a reply
   (`weft status`, `PING`, result waiter) mints a TID and receives it on its
   own `T{tid}.ctrl_in`. A requester that only observes a broadcast, such as
   `weft task stop` reading the terminal envelope, needs no identity. The
   owner stated this invariant on 2026-09-17, "In weft, everything is a
   task", and on 2026-11-03: "Any code that needs to receive a message has a
   task identity."
```

[WT-4] 4.1 row "Everything is a task" (line 413). Current text:

> | Everything is a task | No caller gets a privileged side-channel. Even a short-lived CLI requester mints a TID and receives control replies on `T{tid}.ctrl_in`. | Owner, 2026-09-17 | [MF-3]; `2026-09-18` PONG plan; [WT-2a.12] |

Proposed (`docs/program-theory.md`), pending owner acceptance:

```text
| Everything is a task | No caller gets a privileged side-channel. Any code that needs to receive a message has a task identity: a PING or STATUS requester, even a short-lived CLI requester, mints a TID and owns the `T{tid}.ctrl_in` its reply is addressed to. A STOP, KILL, PAUSE, or RESUME requester observes rather than receives: it reads the outcome the task broadcasts (for STOP and KILL, the terminal envelope on the target's `ctrl_out`), so it needs no identity. | Owner, 2026-09-17; owner, 2026-11-03 | [MF-3]; `2026-09-18` PONG plan; [WT-2a.12] |
```

Optional third edit, for the owner to take or leave: [WT-8] row
"Everything is a task" (line 1041), "Evolved" cell, from "Agents,
pipelines, services, and short-lived requesters are tasks too" to "Agents,
pipelines, services, and short-lived requesters that receive messages are
tasks too".

The [WT-4] rows "It is just a message" (line 414) and "Address a fact to its
observers" (line 418) and [ALT-WT-003] stay as written: the first still
governs every reply that exists, the second is the rule this plan applies,
and the third's "a new control reply follows the same shape" is what the
STATUS move does. The owner's probe-key bank item B1 needs re-keying by
the owner after this plan; its expected answer is not reproduced here.

## 5. Tasks

Dependency order: 0 → 1 → 2 → 3 → 4 → 4a → 5 → 6. Tasks 3, 4, 4a, and 5
land in **one commit** (strategy B); nothing is committed between them. Between
Task 3 and Task 4 the requester suites in `tests/commands/` are expected
red. The theory edit is a separate commit made only after the owner accepts
the wording (owner decision 2). Commits are made only when the owner asks.

### Task 0. Read, answer, record

- Outcome: the implementer can name every edit point and the facts each
  requester waits on.
- Do: read §3 "Read first"; write answers to the four comprehension
  questions in `## Execution log` under a dated "Implementer" heading.
- Done when: every answer matches the expected answer in substance. A wrong
  answer is a stop-and-re-read blocker.

### Task 1. Independent review of plan and delta (before any code)

- Outcome: a cross-family reviewer has read this plan, its
  `## Proposed Spec Delta`, the theory proposal, and the code, and every
  finding has a disposition in `## Review Log`.
- Invocation: Tier-1 via `skills/call-agent/SKILL.md`, using the
  recommended prompt in `docs/agent-context/runbooks/writing-plans.md` §8,
  with the owner's one-scoped-round rule. Native same-family subagents only
  as a logged Tier-2 fallback after two bounded Tier-1 attempts fail.
- Reviewer inputs: this plan; the six spec files at `b7eed9f9`;
  `docs/program-theory.md` [WT-2a.12], [WT-4] 4.1, [ALT-WT-003],
  [ALT-WT-012], [WT-7]; the files in §3 "Read first".
- Stop if: the reviewer flags `RECONSIDERATION REQUESTED` against an
  [ALT-WT-*] entry, or the owner picks option (b) for decision 1 (then apply
  the (b) paragraph of the delta and drop the STATUS items from Tasks 2-4
  before Task 2).
- Done when: findings are dispositioned and owner decisions 1-4 have answers
  (or their recommendations are explicitly accepted).

### Task 2. RED: write and change the tests first (no production edit)

- Outcome: every test below exists in its final form; each one that should
  fail at `b7eed9f9` fails for the stated reason, and its failing assertion
  is quoted in `## Execution log`; for each test that passes at baseline,
  the mutation that makes it fail is named and its mutated run quoted.
- Harness: `broker_env` and real `Consumer`, `PipelineTask`, `Monitor`,
  `Manager`, `TaskMonitor` instances on real queues for responders;
  `WeftTestHarness` and real queues for requesters; the existing
  `_FakeQueueChangeMonitor` / `QueueChangeMonitor.wait` and
  `time.monotonic` replacements are the only drive and clock seams.
- Stop if: a test needs a mocked handler, a mocked queue, or a patched
  `_report_state_change` to pass.

Responder tests (STOP, KILL, PAUSE, RESUME write nothing on `ctrl_out`):

- T1. `tests/tasks/test_control_channel.py::test_stop_kill_command_sends_keyed_ack`
  (line 162) becomes `test_stop_kill_command_publishes_terminal_and_writes_no_reply`
  (same parameters: STOP/`cancelled`, KILL/`killed`). After
  `task.process_once()`: `ctrl_out` holds exactly one row; it is a typed
  terminal envelope (`type == "terminal"`, `source == "task"`, `tid`,
  expected `status`); no `ctrl_out` row has both `command` and `status`
  keys; `weft.log.tasks` holds a `control_stop` / `control_kill` event with
  the expected status; `task.taskspec.state.status` is expected. RED: the
  acknowledgement row is present, so the "no `command`+`status` row"
  assertion fails.
- T2. `tests/tasks/test_control_channel.py::test_late_stop_after_terminal_state_acks_without_state_regression`
  (line 192) becomes `test_late_stop_after_terminal_state_writes_nothing`:
  `_read_all(ctrl_out) == []`, status stays `completed`, no `control_stop`
  event. RED: one acknowledgement row.
- T3. `tests/tasks/test_control_channel.py::test_pause_resume_control_flow`
  (line 87): after PAUSE, `ctrl_out` holds no row; `weft.log.tasks` holds
  `control_pause`; the newest row of `weft.state.tasks.<tid>`
  (`weft/core/task_state.py::task_state_queue_name`) has
  `projection.event == "control_pause"`; the inbox row stays unprocessed.
  After RESUME the same with `control_resume`, and the work completes. RED:
  the PAUSE acknowledgement row (E13 shows it).
- T4 (decision 1 (a)). `tests/tasks/test_control_channel.py::test_status_command_reports_state`
  (line 138) becomes `test_status_command_replies_to_reply_to`: write
  `encode_control_message("STATUS", request_id="status-1",
  reply_to=reply_name)` with `reply_name = f"T{other_tid}.ctrl_in"`; after
  `process_once()` the reply queue holds exactly one row with
  `command == "STATUS"`, `status == "ok"`, `request_id == "status-1"`,
  `tid`, `paused is False`, `task_status`, `runner`, and no `reply_to` key;
  `ctrl_out` holds no row. RED: `encode_control_message` raises
  `ValueError: reply_to is only valid for PING`.
- T5. `tests/tasks/test_control_channel.py::test_manager_ping_includes_manager_selection_fields`
  (line 580), STOP section: after `_handle_control_command(ControlRequest(
  "STOP", "manager-stop"), ...)`, `_read_all(ctrl_out) == []`;
  `weft.log.tasks` holds a `control_stop` event with `draining is True`;
  the existing `_draining` and `should_stop` snapshot assertions stay. RED:
  the `draining` acknowledgement row.
- T6. `tests/tasks/test_task_execution.py`: in
  `test_structured_active_stop_kill_defers_until_finalize` (line 3032),
  `test_deferred_stop_finalizes_before_timeout_outcome` (line 2742),
  `test_deferred_kill_finalizes_before_limit_outcome` (line 2826),
  `test_deferred_control_wins_over_error_and_cancelled_outcomes` (line
  2915), `test_deferred_stop_kill_finalizes_persistent_task_on_ok_outcome`
  (line 3110), and
  `test_consumer_reactor_stop_cancels_active_command_on_main_thread` (line
  3715), replace each acknowledgement assertion with "no `ctrl_out` row has
  both `command` and `status`", keeping the terminal-status, deferral, and
  reserved assertions. In
  `test_active_consumer_acks_invalid_control_then_defers_later_valid_stop`
  (line 3069) delete the `_deferred_active_control_request_id` assertion
  (line 3101; the field is removed in Task 3). The direct call at line 2731
  (`task._defer_active_control(CONTROL_STOP, int(unique_tid) + 1)`) stays
  valid. RED: the acknowledgement rows.
- T7. `tests/tasks/test_task_interactive.py::test_interactive_command_control_unwinds_before_terminal_and_ack`
  (line 730) becomes `test_interactive_command_control_unwinds_before_terminal`:
  `acknowledgements == []`; every other assertion (one terminal envelope,
  lifecycle record equality, retry attempts, final stream ordering) stays.
  RED: one acknowledgement. T7b (decision 1 (a)):
  `test_interactive_control_commands_report_live_status` (line 974) sends a keyed
  STATUS with `reply_to` and reads the reply there; RED: `ValueError` from
  the encoder.
- T8. `tests/tasks/test_pipeline_runtime.py::test_pipeline_task_stop_propagates_to_waiting_children`
  (line 1637) and `::test_pipeline_task_kill_propagates_to_waiting_children`
  (line 1676): replace the acknowledgement `any(...)` with "no row on the
  pipeline `ctrl_out` has both `command` and `status`"; the propagated
  child-row assertion stays. RED: the pipeline acknowledgement. T8b
  (decision 1 (a)), new test in the same file,
  `test_pipeline_status_replies_to_reply_to`: a keyed STATUS with
  `reply_to` to the pipeline `ctrl_in`; the reply on `reply_to` has
  `status_queue == compiled.runtime.queues.status` and the `request_id`;
  the pipeline `ctrl_out` holds no STATUS row. RED: `ValueError` from the
  encoder.
- T9. `tests/tasks/test_task_observer_behavior.py::test_monitor_stop_command`
  (line 186): `control_responses == []`; the terminal assertions stay. RED:
  the acknowledgement.
- T10. `tests/tasks/test_reserved_disposition.py::test_independent_idle_stop_may_clear_prior_failed_ack_residue`
  (line 112): the final `any(... "ack" ...)` becomes `not any(...)`. RED:
  the acknowledgement.
- T11 (decision 1 (a)). `tests/tasks/test_task_observability.py::test_control_response_broker_error_is_best_effort`
  (line 693) becomes `test_control_reply_broker_error_is_best_effort`: make
  the directed write fail (replace `task._get_connected_queue` with an
  object whose `get_connection()` yields a broker whose `write` raises
  `BrokerError`; this is the external-failure boundary, not a handler mock)
  and assert `task._send_control_reply("T1.ctrl_in", "PING", "ok",
  message="PONG")` returns without raising and attempted one write. Passes
  at baseline; mutation: delete the `except` in `_send_control_reply` and
  the test fails with `BrokerError`. Record that run.
- T12 (decision 1 (a)). `tests/core/test_control_messages.py`:
  `test_control_request_round_trip_preserves_each_command` drops `"STATUS"`
  from its parameters; `test_ping_control_request_requires_and_preserves_reply_route`
  becomes `test_reply_command_requires_and_preserves_reply_route`,
  parametrized over `PING` and `STATUS`;
  `test_non_ping_control_request_without_request_id_has_only_command_key`
  becomes `test_reply_less_control_request_without_request_id_has_only_command_key`
  and uses `STOP`; in `test_parse_control_request_rejects_every_noncanonical_shape`
  remove `'{"command":"STATUS","request_id":"x","reply_to":"T1.ctrl_in"}'`
  and add `'{"command":"STATUS"}'`, `'{"command":"STATUS","request_id":"x"}'`,
  `'{"command":"STOP","request_id":"x","reply_to":"T1.ctrl_in"}'`,
  `'{"command":"PAUSE","request_id":"x","reply_to":"T1.ctrl_in"}'`; in
  `test_encode_control_message_rejects_noncanonical_values` remove
  `("STATUS", "request", "T1.ctrl_in")` and add `("STATUS", None, None)`,
  `("STATUS", "request", None)`, and `(cmd, "request", "T1.ctrl_in")` for
  each of `STOP`, `KILL`, `PAUSE`, `RESUME`. RED: the STATUS round trip
  with `reply_to` raises.

Requester tests (wait on terminal evidence, delete nothing):

- T13. `tests/commands/test_task_commands.py::test_control_surface_wait_timeout_preserves_three_clock_precedence`
  (line 681) becomes `test_control_surface_wait_timeout_caps_at_interval_until_overall_deadline`,
  parameters `(overall_deadline, expected)` with `now=10.0`,
  `interval=2.0`: `(15.0, 2.0)`, `(11.0, 1.0)`, `(10.0, None)`,
  `(9.0, None)`. Of the twelve rows at `b7eed9f9` (lines 667-678), two
  survive as `(15.0, 2.0)` and `(9.0, None)` (the rows with no public and no
  KILL deadline, lines 667 and 672); the ten rows at lines 668-671 and
  673-678 are deleted because their public-signal or KILL-acknowledgement
  deadline no longer exists; `(11.0, 1.0)` and `(10.0, None)` are added to
  pin the cap and the expiry boundary. RED: the call without
  `public_signal_deadline` / `kill_ack_deadline` raises `TypeError`.
- T14. Delete `test_await_control_surface_public_grace_outlives_expired_kill_ack`
  (line 1524): its scenario (a public signal outliving a KILL grace) cannot
  exist.
- T15. `test_await_control_surface_does_not_promote_kill_ack_to_terminal`
  (line 1585) stays unedited as a mixed-version anchor (a KILL
  acknowledgement row is noise and never terminal).
- T16. `test_observe_control_envelopes_keeps_same_drain_observation_times`
  (line 1630) becomes `test_observe_control_envelopes_is_peek_only_after_cursor`:
  write a STATUS-shaped row, a KILL acknowledgement row, and a stderr frame
  to `T{tid}.ctrl_out`; call `_observe_control_envelopes(..., after_timestamp=None)`;
  assert no terminal snapshot, `last_message_id` equals the third row's id,
  and all three rows are still present (peek). Then write a typed terminal
  envelope for `tid`; call again with `after_timestamp=` the returned id;
  assert the terminal snapshot and `last_message_id` equal to the terminal
  row's id. RED: `TypeError` on `after_timestamp`, and the rows are
  deleted.
- T17. `tests/commands/test_control_observation_connections.py::test_control_observer_retains_shared_completion_facts_with_bounded_drain`
  (line 650) becomes `test_control_observer_retains_every_row_within_horizon`:
  pass `after_timestamp=None`; drop the `kill_ack_observed_at` assertion;
  assert the route afterwards holds `facts`, the KILL row, the stderr frame,
  and the late row, in order (nothing deleted). RED: the KILL row and the
  stderr frame are deleted at baseline.
- T18. New `tests/commands/test_task_commands.py::test_await_control_surface_leaves_ctrl_out_rows_untouched`
  (the observer test): following
  `test_await_control_surface_accepts_terminal_ctrl_out_without_log_replay`
  (line 1665) for setup, write to `T{tid}.ctrl_out` a STATUS-shaped row, a
  pre-upgrade `{"command": "STOP", "status": "ack", "tid": tid}` row, a
  stderr frame, and the typed terminal `cancelled` envelope; peek
  `(body, message_id)` before; call `_await_control_surface(ctx, tid)`;
  assert the snapshot is `cancelled` and the peek after equals the peek
  before, byte for byte. Mutation caught: any delete restored in
  `_observe_control_envelopes`. RED at baseline: the three non-completion
  rows are deleted.
- T19. New `tests/commands/test_task_commands.py::test_await_control_surface_ignores_pre_upgrade_kill_ack_until_terminal`
  (the mixed-version test): `T{tid}.ctrl_out` holds a pre-upgrade
  `{"command": "KILL", "status": "ack", "tid": tid}` row; `_task_status`
  returns a `running` snapshot until the terminal row exists; the scripted
  monitor's first `wait` advances the clock by `4 *
  CONTROL_SURFACE_WAIT_INTERVAL` (past the old KILL grace, well inside the
  2.0 s deadline) without writing; its second `wait` writes the typed
  terminal `killed` envelope. Assert the returned snapshot is `killed` and
  `wait` was called exactly twice. Mutation caught: a restored
  acknowledgement reader (the KILL grace) returns the `running` snapshot
  after the first wait. RED at baseline: the returned snapshot is
  `running`.
- T20. `tests/commands/test_active_interactive_lifecycle.py::test_active_stop_waits_named_budget_and_accepts_only_own_ack`
  (line 430) becomes `test_active_stop_waits_named_budget_for_terminal_evidence`,
  parametrized `terminal_after` in `[None, 1.5]`. Keep the clock patch, the
  `Queue.write` recorder of rows sent to `T{session.tid}.ctrl_in` (it now
  only records: delete its STOP branch, lines 454-461, with its
  `request_id` assertion, `own_key` capture, and
  `consumer._send_control_response(...)` call, and the now-unused `own_key`
  list), and the `QueueChangeMonitor.wait` hook (delete its
  `_send_control_response` call, line 469). In the first wait call the hook writes an
  acknowledgement-shaped row to `T{session.tid}.ctrl_out`
  (`{"command": "STOP", "status": "ack", "tid": session.tid}`, plus the
  STOP row's `request_id` when the recorded STOP carries one) with a raw
  queue write; then, when `terminal_after` is set, it advances the clock by
  1.5 and calls `_publish_stopped_peer(harness, consumer)` (real
  cancellation), else it advances the clock by the full timeout. Assert:
  commands sent are `["STOP", "KILL"]` when `terminal_after is None`, else
  `["STOP"]`; `waits == [INTERACTIVE_STOP_COMPLETION_TIMEOUT]`; with no
  terminal, the KILL is sent exactly `INTERACTIVE_STOP_COMPLETION_TIMEOUT`
  after the STOP; the acknowledgement-shaped row is still on `ctrl_out`
  afterwards (not consumed); the recorded STOP and KILL rows carry no
  `request_id` (register R10). Mutation caught: a restored
  `_matching_stop_ack` settles on the row and no KILL is sent. RED at
  baseline: in the `None` case the old code matches the row (it carries the
  STOP's own `request_id`) and returns without KILL.
- T20b. `tests/commands/test_active_interactive_lifecycle.py::test_active_cli_quit_sends_keyed_stop_and_keeps_terminal_proof`
  (line 236) becomes `test_active_cli_quit_sends_stop_and_keeps_terminal_proof`:
  replace `assert parsed[0].request_id` (line 270) with
  `assert parsed[0].request_id is None` (register R10); every other
  assertion stays. RED at baseline: the STOP carries `uuid4().hex`. Without
  this edit the unedited test fails after Task 4.
- T21. `test_active_stop_rejects_invalid_terminal_before_real_task_proof`
  and `tests/commands/test_control_observation_connections.py::test_interactive_stop_observes_local_completion_without_telemetry`
  (line 600) stay unedited.

STATUS callers in other suites (decision 1 (a)):

- T22. `tests/tasks/test_task_monitor.py`: `_read_control_reply` (line
  2508) routes `CONTROL_STATUS` exactly as it routes `CONTROL_PING` (requires
  `reply_queue`, sends `reply_to=reply_queue.name`, reads the reply queue);
  `_read_status_reply` (line 11513) gains a required `reply_queue` keyword,
  and its callers at lines 11563, 11680, 11706, 11714, 11759, and 11879
  pass one (a fresh `T{n}.ctrl_in` queue per test, as the PING callers at
  lines 3139 and 3282 do).
  `test_task_monitor_terminal_control_cleanup_worker_does_not_block_control`
  (line 9343; its STATUS write is line 9418) sends its STATUS with `reply_to` and reads the reply there.
  RED: `ValueError` from the encoder.
- T23. `tests/core/test_manager.py::test_manager_control_replies_do_not_reset_idle_clock`
  (line 10394; the STATUS write is line 10432): send
  `encode_control_message("STATUS", request_id="idle-status",
  reply_to="T1.ctrl_in")`; the idle-clock assertions stay. RED:
  `ValueError` from the encoder.
- T24. `tests/core/test_task_runtime_connections.py::test_manager_dynamic_control_queues_reuse_task_connection`
  (line 465; the STATUS uses are lines 480 and 484): use `"STOP"` instead of `"STATUS"` (the test pins
  connection reuse, not the command). Passes at baseline and after; no RED
  needed (it would raise `ValueError` after Task 3 without the edit).

CLI floors (`docs/agent-context/runbooks/adversarial-acceptance-probes.md`):

- T25. No new test: equivalent floors already exist and stay green
  unedited. At `b7eed9f9` an unknown full TID on the CLI exits 1, not 2:
  `tests/cli/test_cli_system.py::test_single_task_control_unavailable_metadata_fails_without_traceback`
  (line 266, parametrized `stop` / `kill`, `1777000000000000999` against an
  empty harness context) asserts exit 1, "observation metadata
  unavailable" on stderr, and no `Traceback` (run during fresh-eyes review:
  2 passed). The exit-2 `TaskNotFound` class is held at the command layer by
  `tests/commands/test_task_commands.py::test_stop_task_rejects_unknown_task_before_sending_control`
  (line 285) and `::test_single_task_control_precheck_preserves_live_context`
  (line 407), which pin the raise in `_require_controllable_task`
  (`weft/commands/tasks.py` line 2218). This plan changes neither path.
- Anchors that stay green unedited:
  `tests/cli/test_cli_system.py::test_task_stop_terminal_task_is_rejected_without_control_queue_residue`
  (exit 1, no traceback), `tests/cli/test_cli_run.py::test_cli_run_wait_reports_cancelled_task`
  (a real `weft task stop` on a live task),
  `tests/commands/test_task_commands.py::test_stop_tasks_terminates_active_process_tree`,
  `::test_kill_tasks_terminates_active_process_tree`,
  `tests/tasks/test_task_stop_sqlite_only.py::test_stop_exits_process_before_reporting_clean_cancel`,
  `tests/core/test_control_probe.py::test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in`,
  `tests/tasks/test_pipeline_runtime.py::test_pipeline_task_routes_pong_to_requester_control_queue`,
  `tests/tasks/test_control_channel.py::test_ping_control_command_returns_pong`.
  (`::test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal`
  is no longer an unedited anchor: Task 4a renames it and drops its `ack`
  branch.)

- Verify (RED run, recorded):

  ```bash
  . ./.envrc
  ./.venv/bin/python -m pytest tests/core/test_control_messages.py tests/tasks/test_control_channel.py tests/tasks/test_task_execution.py tests/tasks/test_task_interactive.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_task_observer_behavior.py tests/tasks/test_reserved_disposition.py tests/tasks/test_task_observability.py tests/commands/test_task_commands.py tests/commands/test_control_observation_connections.py tests/commands/test_active_interactive_lifecycle.py -q
  ./.venv/bin/python -m pytest tests/tasks/test_task_monitor.py tests/core/test_manager.py -q -k "status or idle_clock or control_cleanup_worker or ping"
  ```

- Done when: each RED test fails with the stated assertion or exception
  (quoted in the log), each baseline-green test has its mutated failing run
  quoted, and nothing else in those files changed.

### Task 3. GREEN: responders (core work; same commit as Tasks 4, 4a, and 5)

- Outcome: no task writes a reply for STOP, KILL, PAUSE, or RESUME; STATUS
  replies go to `reply_to` (decision 1 (a)); T1-T12 and T22-T24 pass.
- Files: `weft/_constants.py`, `weft/core/control_messages.py`,
  `weft/core/tasks/base.py`, `weft/core/tasks/consumer.py`,
  `weft/core/tasks/interactive.py`, `weft/core/tasks/pipeline.py`,
  `weft/core/tasks/monitor.py`, `weft/core/manager.py`.
- Edits:
  1. `weft/_constants.py`: add `CONTROL_REPLY_COMMANDS: Final[frozenset[str]]
     = frozenset({CONTROL_PING, CONTROL_STATUS})` after `CONTROL_COMMANDS`,
     with a docstring: "Commands whose outcome is a private fact, answered
     to the request's `reply_to` ([MF-3], [QUEUE.2a])."
  2. `weft/core/control_messages.py`: `_validate_control_request` requires
     `request_id` and `reply_to` when `request.command in
     CONTROL_REPLY_COMMANDS` (message "PING and STATUS require request_id
     and reply_to") and otherwise raises `"reply_to is only valid for PING
     and STATUS"`; `parse_control_request` requires exactly
     `{"command", "request_id", "reply_to"}` for a command in
     `CONTROL_REPLY_COMMANDS`. The module stays shape-only.
  3. `weft/core/tasks/base.py::BaseTask._handle_control_command`: delete
     the four `_send_control_response` calls for STOP, KILL, PAUSE, RESUME
     (lines 1737-1741, 1751-1755, 1765-1770, 1780-1785), keeping the state
     changes and `return True`; STATUS becomes `assert request.reply_to is
     not None` then `self._send_control_reply(request.reply_to,
     CONTROL_STATUS, "ok", **self._control_response_extras(request))`.
     Rewrite the override contract bullet "send a ctrl-out response for
     handled STOP/KILL commands" (line 1710) to "publish the state change;
     write no reply for STOP, KILL, PAUSE, or RESUME". Rewrite the
     `_handle_control_message` docstring (line 1670) to "React to control
     requests; replies go only to a request's `reply_to`." Delete
     `_control_request_id_extra` (lines 1798-1804) and `_send_control_response`
     (lines 2113-2125). Keep `_control_response_payload` (used by
     `_send_control_reply`) and `_ctrl_out_queue` (used by interactive
     stream frames).
  4. `weft/core/tasks/consumer.py`: delete `_deferred_active_control_request_id`
     (line 108 and its uses at 728, 752, 758), the `request_id` keyword of
     `_defer_active_control` and its pass-through at line 670, the one
     `response_extra` dict (line 763) and the two `_send_control_response`
     calls (lines 777, 792) in `_finalize_deferred_active_control`. Update both
     docstrings: point 3 of `_defer_active_control` and the finalize
     docstring no longer mention an acknowledgement response; finalization
     "applies the terminal transition and reserved policy when required, and
     writes no reply".
  5. `weft/core/tasks/interactive.py`: delete the abstract
     `_send_control_response` (lines 131-139), `response_extra` (lines
     553-555), and the three calls (lines 560, 580, 600); the
     already-terminal branch keeps `return True`. Docstring of
     `_interactive_handle_control`: "Publish terminal proof after unwind;
     write no reply."
  6. `weft/core/tasks/pipeline.py::PipelineTask._handle_control_command`:
     STATUS keeps `_publish_pipeline_snapshot()` and then calls
     `self._send_control_reply(request.reply_to, CONTROL_STATUS, "ok",
     **self._control_response_extras(request),
     status_queue=self._runtime.queues.status)` after `assert
     request.reply_to is not None`; STOP and KILL lose their
     `response_extra` and `_send_control_response` (lines 917-922,
     934-939). `_broadcast_control` is unchanged.
  7. `weft/core/tasks/monitor.py::Monitor._handle_control_command`: delete
     lines 106-111.
  8. `weft/core/manager.py::Manager._handle_control_command`: STOP keeps
     `self._begin_graceful_shutdown(message_id=context.timestamp)` and
     `return True`; delete lines 1314-1317. Docstring: "Override STOP to
     drain active children before manager exit; the drain is published as
     `control_stop` with `draining=True`."
- Constraints: no new helper; no change to `_report_state_change`,
  `_handle_stop_request`, `_handle_kill_request`, `_apply_reserved_policy`,
  or the order of their calls; `TaskControlPolicy` declarations unchanged.
- Stop if: any site wants a reply "for compatibility", or a test needs the
  completion publisher changed.
- Verify: `./.venv/bin/python -m pytest tests/core/test_control_messages.py tests/core/test_task_runtime_connections.py tests/tasks -q`
  and `./.venv/bin/python -m pytest tests/core/test_manager.py -q -k idle_clock`
  (all green; the second file and command cover T24 and T23), then the grep gate:
  `grep -rn "_send_control_response\|_control_request_id_extra\|_deferred_active_control_request_id" weft tests --include=*.py`
  prints nothing (decision 1 (a); under (b) only the two STATUS calls and
  the method remain).

### Task 4. GREEN: requesters (same commit as Tasks 3, 4a, and 5)

- Outcome: the CLI waits only on terminal evidence and deletes nothing on a
  target's `ctrl_out`; T13-T21 and T25 pass.
- Files: `weft/commands/tasks.py`, `weft/commands/run.py`.
- Edits in `weft/commands/tasks.py`:
  1. `_ControlSurfaceObservation` (line 1795): fields become
     `terminal_snapshot: system_cmd.TaskSnapshot | None` and
     `last_message_id: int | None`. Docstring: "Facts observed in one
     peek-only pass over the current ctrl-out range."
  2. `_observe_control_envelopes(ctrl_queue, *, tid, taskspec_payload,
     after_timestamp: int | None)`: keep the horizon read and `before`
     computation; return `_ControlSurfaceObservation(None, after_timestamp)`
     when the horizon is `None` or not after `after_timestamp`; call
     `broker.peek_generator(ctrl_queue.name, with_timestamps=True,
     after_timestamp=after_timestamp, before_timestamp=before)` inside the
     existing `closing_queue_iterator(...)` wrapper (the early return at a
     terminal match must still close the generator; this is why E15 keeps
     that import in `tasks.py`); for each
     row set `last = message_id`, decode, and return
     `_ControlSurfaceObservation(terminal_snapshot, message_id)` at the
     first `_snapshot_from_terminal_ctrl_out(...)` match; otherwise return
     `_ControlSurfaceObservation(None, last)`. Delete the
     `delete_message_ids` call, the `retained` logic, and both clocks.
     Docstring: "Peek one bounded control range after a cursor and select
     the first matching typed terminal. Deletes nothing. Spec: [MF-3],
     [MF-5]." Remove the now-unused `_is_completion_record` import (line 90;
     `result_observation` stays).
  3. `_control_surface_wait_timeout(*, overall_deadline: float, now: float,
     interval: float) -> float | None`: return `None` when
     `overall_deadline - now <= 0`, else `min(overall_deadline - now,
     interval)`. It keeps the overall deadline and the `interval` cap
     (series plan 2 owns the cap; do not change it here). Docstring names
     both.
  4. `_await_control_surface`: replace `public_signal_deadline` and
     `kill_ack_deadline` with `ctrl_out_cursor: int | None = None`; each
     turn call `_observe_control_envelopes(..., after_timestamp=
     ctrl_out_cursor)`, return on a terminal snapshot, else set
     `ctrl_out_cursor = observation.last_message_id`; then the existing
     `mapping_for_tid` / `_task_status` read and terminal return; then
     `wait_timeout = _control_surface_wait_timeout(overall_deadline=
     deadline, now=time.monotonic(), interval=CONTROL_SURFACE_WAIT_INTERVAL)`;
     `None` returns `(latest_entry, latest_snapshot)`. Delete the
     KILL-grace early return (lines 1965-1969).
  5. `_send_control`, `_stop_resolved_tasks`, `_kill_resolved_tasks`,
     fallbacks, `_ControlSurfaceResources`: unchanged.
- Edits in `weft/commands/run.py`:
  1. Delete `_matching_stop_ack` (lines 486-499).
  2. `_stop_interactive_run_session`: write
     `encode_control_message(CONTROL_STOP)`; the loop returns when
     `snapshot.terminal`; on expiry write `encode_control_message(
     CONTROL_KILL)` and return, as today. Docstring: "Close stdin, send
     STOP, and wait for terminal evidence through the named budget
     [CLI-1.1.1], [MF-3]. STOP gets no reply. KILL follows only expiry of
     the named STOP completion budget."
  3. Remove imports left unused (expected: `uuid4`, `decode_control_object`,
     `closing_queue_iterator`; let `ruff check` confirm, E15).
- Constraints: no new monitor, queue, or helper; `weft/commands/control_convergence.py`
  not edited in this task (Task 4a owns it); budgets unchanged.
- Stop if: the change wants an acknowledgement read, a requester queue, a
  second monitor, or a changed budget.
- Verify: `./.venv/bin/python -m pytest tests/commands tests/cli/test_cli_system.py -q`,
  then `grep -rnE '== "ack"' weft --include=*.py` and
  `grep -rn "_matching_stop_ack\|public_signal\|kill_ack" weft --include=*.py`
  print nothing.

### Task 4a. Reducer: remove the acknowledgement concept (new task; same commit as Tasks 3, 4, and 5)

A new task, not an extension of Task 4: Task 4 changes what the requester
waits on; this task deletes reducer vocabulary that no production caller
reaches, with its own RED and GREEN.

- Outcome: `weft/commands/control_convergence.py` has no acknowledgement
  input, no `accepted` state, and no `wait` action; its docstring says it
  classifies post-wait evidence; every decision the five production call
  sites receive is unchanged.
- Evidence (re-verified at the live tree, 2026-11-03):
  `ControlConvergenceEvidence.ack_seen: bool = False` (line 61); the
  `accepted` state in `ControlConvergenceState` (line 27) and in
  `weft/_constants.py::CONTROL_CONVERGENCE_STATE_VALUES` (line 561);
  `_should_wait` (line 100) read by transition `accepted-command-waits`
  (lines 220-227); `_should_wait_for_evidence` (line 107) read by
  transition `command-sent-waits-for-evidence` (lines 212-219); `"wait"` in
  `ControlConvergenceAction` (line 35) and in
  `CONTROL_CONVERGENCE_ACTION_VALUES` (line 578). Two further transitions
  have `accepted` as their only source, `terminal-observed-from-accepted`
  (lines 156-163) and `runtime-dead-from-accepted` (lines 188-195), and
  `command-timeout-escalates-runner` (lines 228-235) has source
  `frozenset(("command_sent", "accepted"))`. All five production calls of
  `reduce_control_convergence` (`weft/commands/tasks.py` lines 2163, 2184,
  2279, 2298, 2327) pass a current state of `command_sent`,
  `escalating_runner`, or `escalating_host`, build the evidence without
  `ack_seen`, and pass `observation_budget_expired=True`, so `accepted` is
  unreachable and neither wait transition fires in production (E16). The
  wait itself happens in `_await_control_surface` before the reducer runs.
  The only setters of `ack_seen=True` are the parametrized rows at
  `tests/commands/test_task_commands.py` lines 1189, 1196, and 1205 and the
  `ack` branch at line 1267; they exist to satisfy `assert_states_covered`
  and `assert_actions_covered`. The module is imported only by
  `weft/commands/tasks.py` (line 101) and that test file (line 31).
- Not touched: `weft/core/service_convergence.py` (Manager and service
  ownership convergence; its `accepted` is a parsed-row disposition, a
  different concept) and the `weft/liveness/` package (the liveness
  policy). Neither imports this module or its constants.
- Scope choice (owner item 4): the budget-open wait goes too, because no
  caller can pass an open budget and an action only a test can trigger is
  the test-only seam the test-audit rule names. If the owner keeps it
  instead, apply the alternative paragraph under owner item 4, which lists
  every sentence, gate, and test that changes.
- RED (tests first, no production edit), in
  `tests/commands/test_task_commands.py`:
  1. In `test_control_convergence_machine_covers_all_transitions`, delete
     the rows `"wait for first evidence"`, `"runtime dead after
     accepted"`, `"ack waits"`, and `"accepted ack waits"`.
  2. Rewrite `"stop terminal after ack"` as `"stop terminal after
     command"` with current state `"command_sent"` (same evidence, same
     expected `accept_terminal` / `terminal_observed`), so a STOP terminal
     row remains.
  3. Rewrite `"command wait expires to runner escalation"` with current
     state `"command_sent"` and evidence
     `ControlConvergenceEvidence(command=CONTROL_KILL,
     observation_budget_expired=True)` (no `ack_seen`), same expected
     `escalate_runner` / `escalating_runner`; it is the only row covering
     `command-timeout-escalates-runner`.
  4. Rename `test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal`
     to `test_control_convergence_does_not_accept_wrong_kill_terminal`;
     delete its `ack` call and the two `ack` assertions; keep the
     `wrong_terminal` call and assertions unchanged.
  - Expected RED (simulated against the live reducer with the edited rows,
    2026-11-03): `test_control_convergence_machine_covers_all_transitions`
    fails at `assert_transition_ids_covered` with `AssertionError:
    Uncovered transition IDs: 'accepted-command-waits',
    'command-sent-waits-for-evidence', 'runtime-dead-from-accepted',
    'terminal-observed-from-accepted'` (the state and action checks behind
    it would report `'accepted'` and `'wait'`). The renamed test passes.
    Quote the failure in `## Execution log`.
- GREEN, `weft/commands/control_convergence.py` and `weft/_constants.py`:
  1. Delete the `ack_seen` field from `ControlConvergenceEvidence`.
  2. Delete `"accepted"` from `ControlConvergenceState` and from
     `CONTROL_CONVERGENCE_STATE_VALUES`.
  3. Delete `_should_wait` and the transitions `accepted-command-waits`,
     `terminal-observed-from-accepted`, and `runtime-dead-from-accepted`.
  4. Delete `_should_wait_for_evidence` and the transition
     `command-sent-waits-for-evidence`.
  5. Change `command-timeout-escalates-runner`'s source to
     `"command_sent"`.
  6. Delete `"wait"` from `ControlConvergenceAction` and from
     `CONTROL_CONVERGENCE_ACTION_VALUES`.
  7. Keep `observation_budget_expired`: every production caller passes
     `True`, and it still gates the escalation and unknown predicates. An
     open budget with no terminal or dead-runtime evidence now matches no
     transition and `StateMachine.decide` raises `ValueError` ("No
     transition matched"), which is the fail-loud answer for a caller that
     classifies before waiting.
  8. `reduce_control_convergence` docstring: "Classify post-wait control
     evidence into the deterministic convergence decision. The caller
     waits first (`weft/commands/tasks.py::_await_control_surface`); the
     reducer has no acknowledgement input and no wait decision. Spec:
     OBS.12a." Module docstring unchanged except adding OBS.12a to the
     07 reference line.
  9. `ControlConvergenceEvidence` docstring: "Post-wait evidence snapshot
     for command-layer STOP/KILL convergence."
- Constraints: no call site in `weft/commands/tasks.py` changes (none
  passes `ack_seen`, `accepted`, or an open budget, and none expects
  `wait` from `_require_control_action`); the module-level
  `assert_all_states_reachable(("command_sent",))` still holds.
- Verify:

  ```bash
  . ./.envrc
  ./.venv/bin/python -m pytest tests/commands/test_task_commands.py -q -k "control_convergence"
  ./.venv/bin/python -m pytest tests/commands -q
  grep -rn "ack_seen\|_should_wait\|accepted-command-waits\|command-sent-waits-for-evidence" weft tests --include=*.py
  grep -n '"accepted"\|"wait"' weft/commands/control_convergence.py
  ```

  Both pytest runs pass, and `assert_transition_ids_covered`,
  `assert_states_covered`, and `assert_actions_covered` pass, which proves
  every remaining state, action, and transition is covered by a test row
  (structural coverage, not production reachability; reachability comes
  from the caller trace in E16). Both greps print nothing (exit 1).
- Stop if: any production call site would need an edit, or any of the
  acknowledgement and wait vocabulary survives: `ack_seen`, the `accepted`
  state, the `wait` action, or the transitions `accepted-command-waits` and
  `command-sent-waits-for-evidence` (under owner item 4's alternative,
  `wait` and `command-sent-waits-for-evidence` are kept). Other transitions
  reached only by test rows are retained:
  `runtime-dead-from-command-sent`, `runtime-dead-from-runner-escalation`,
  and `terminal-observed-from-host-escalation` (production callers start
  from the states they do, with the budget expired). Deleting them would be
  a scope expansion for the owner (owner item 5), not this plan's work.

### Task 5. Spec, CHANGELOG, backlinks, theory proposal (same commit as Tasks 3, 4, and 4a, except the theory edit)

- Outcome: the spec describes reply-less state-change commands and the
  STATUS route; the CHANGELOG states the break.
- Files: the six spec files; `CHANGELOG.md`; `docs/program-theory.md` only
  after the owner accepts (separate commit).
- Apply every Replacement block in `## Proposed Spec Delta` verbatim, plus
  the five non-block backlink bullets. Record the promotion baseline
  identifier (diff base `b7eed9f9` plus worktree state, then the landing
  commit SHA once the owner commits) in `## Spec Baseline`, and set
  `Status: promoted` here and in the index row in the same change, so
  `bin/check-doc-paths` checks the blocks.
- Docstrings (traceability): `encode_control_message` and
  `parse_control_request` keep `Spec: [QUEUE.2a]`; `_observe_control_envelopes`,
  `_control_surface_wait_timeout`, and `_stop_interactive_run_session` cite
  `[MF-3]` (and `[CLI-1.1.1]` for the last); `BaseTask._handle_control_command`
  and `_send_control_reply` keep `Spec: [CC-2.4], [MF-3]`; the new constant's
  docstring cites `[MF-3]` and `[QUEUE.2a]`.
- `CHANGELOG.md` under `## [Unreleased]`, `### Changed`, one bullet: STOP,
  KILL, PAUSE, and RESUME no longer write an acknowledgement row to
  `ctrl_out` (or anywhere); their outcome is the published state change
  (`weft.log.tasks`, `weft.state.tasks.<tid>`, and the terminal `ctrl_out`
  envelope for STOP and KILL). `weft task stop`, `weft task kill`, and
  interactive teardown wait for terminal evidence only and no longer delete
  rows on a task's `ctrl_out`. STATUS now takes the PING envelope
  `{command, request_id, reply_to}` and replies only to `reply_to`; a bare
  STATUS is rejected without reply. No compatibility shim: a hand-written
  controller that waited for a `{"status": "ack"}` row or sent a bare
  STATUS must watch the published state or name a `reply_to`. The
  command-control convergence reducer (`weft/commands/control_convergence.py`)
  drops its `accepted` state, its `ack_seen` input, and its `wait` action;
  no production decision changes.
- Theory: after the owner accepts (decision 2), apply the proposed
  [WT-2a.12], [WT-2] item 1, and [WT-4] row text (and the optional [WT-8]
  cell if taken)
  verbatim in a separate commit; record the acceptance in `## Review Log`.
- Verify:

  ```bash
  . ./.envrc
  ./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
  bin/check-doc-paths
  grep -n "2026-11-03-control-replies-only-on-request-plan" docs/specifications/05-Message_Flow_and_State.md docs/specifications/01-Core_Components.md docs/specifications/10-CLI_Interface.md docs/specifications/07-System_Invariants.md docs/specifications/00-Quick_Reference.md docs/specifications/12-Pipeline_Composition_and_UX.md
  grep -n "non-PONG\|Non-PONG\|public acknowledgement\|STOP/ACK\|ACK is progress\|KILL\` ack\|KILL\` acknowledgement\|control acknowledgement\|acknowledges control\|is acknowledged after this unwind\|control reply surface\|control-reply surface\|control replies for the pipeline" docs/specifications/*.md
  ```

  The backlink grep returns one line per file (six). The retired-phrase
  grep returns nothing: at `b7eed9f9` every one of its hits is a line a
  Replacement block replaces (E9).
- Stop if: `bin/check-doc-paths` reports a Replacement block that does not
  match its target; fix the landed text or the block, never both
  differently.

### Task 6. Full gates, traceability, [WT-7] closeout, completed-work review (after the landing commit)

- Run `## 7. Verification` in full and paste the summary lines.
- Traceability: the [MF-3] mapping sentences in the Replacement blocks name
  the functions as landed; each touched function's docstring cites its
  governing section.
- [WT-7] class-5 completion requirements (`docs/program-theory.md`
  `## Tensions and falsifiers [WT-7]`, "Possession probe, keyed"):
  1. Keyed possession probe: the owner writes the key first (one
     hypothetical feature and where it belongs, one category error and the
     entry that refuses it, one defect class an audit of a named subsystem
     should find). An agent that did not author this plan, from a different
     family, answers from `docs/program-theory.md` alone and is graded
     against the key. Key and result go in `## Review Log`; on FAIL,
     classify after disposition (document deficiency, key fault, or agent
     reasoning). A self-graded probe is not a probe. The owner's probe-key
     bank item B1 is graded against the owner's re-keyed answer (§ Theory
     clause).
  2. Theory-versus-code audit table refresh, by reference: in
     `## Execution log`, re-audit rows I-12, 4.1-a, and 4.1-b of the audit
     table in `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`
     against the landed tree (and against the theory text as accepted or
     not), citing each row it refreshes; unchanged rows carry forward by
     reference. Do not edit the reconciliation plan.
- Independent completed-work review: one scoped cross-family round via
  `skills/call-agent/SKILL.md` over the landed diff and the execution log;
  findings dispositioned in `## Review Log`.
- Close: `Status: completed` here and in the index row in the same change.

## 6. Testing Plan

- Harness: `broker_env` with real task classes for responders;
  `WeftTestHarness`, `prepare_project_root` / `build_context`, and real
  queues for requesters; real CLI invocations for the T25 floors and the
  CLI anchors.
- What proves the change: T1-T3, T5-T10 prove no reply row exists for
  STOP, KILL, PAUSE, RESUME on every responder type and that the state
  change is still published (log event, snapshot, terminal envelope); T4,
  T7b, T8b, T12, T22, T23 prove the STATUS route; T13, T16-T20 prove the
  waits read terminal evidence only, delete nothing, and ignore
  pre-upgrade acknowledgement rows; T25 and the anchors hold the CLI exit
  classes and the no-traceback floor.
- Enumerable contract elements, each with a firing test: STOP (T1, T6, T7,
  T8, T9, T10, T18), KILL (T1, T6, T7, T8, T19), PAUSE and RESUME (T3),
  PING (anchors `test_ping_control_command_returns_pong`,
  `test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in`), STATUS
  (T4, T7b, T8b, T12, T22); `ctrl_out` contents (T1, T16, T17, T18); the CLI
  wait budget rows (T13: cap, expiry boundary; T19: no early return); the
  interactive escalation (T20, both parameters) and the unkeyed STOP and
  KILL rows (T20, T20b); exit codes 0/1/2 (anchors
  and T25).
- Do not mock: queues, task handlers, `_report_state_change`, the broker
  write path except T11's injected `BrokerError` at the external boundary,
  process lifecycle in the anchors.
- Tempting to skip: the mixed-version rows (T15, T19, T20's ack-shaped row).
  They are in scope because detached tasks from before the upgrade keep
  writing acknowledgements ([WT-2a.13]).
- Completion grace: none needed; every assertion reads rows the code under
  test wrote synchronously, or waits through the real drive loop.

## 7. Verification

Per task: the commands inside each task. Final gates, from the landed
state (expected exit codes below the block):

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_control_messages.py tests/tasks/test_control_channel.py tests/tasks/test_task_execution.py tests/tasks/test_task_interactive.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_task_observer_behavior.py tests/tasks/test_reserved_disposition.py tests/tasks/test_task_observability.py tests/tasks/test_task_monitor.py tests/core/test_manager.py tests/core/test_task_runtime_connections.py tests/commands/test_task_commands.py tests/commands/test_control_observation_connections.py tests/commands/test_active_interactive_lifecycle.py tests/cli/test_cli_system.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m "" tests/tasks tests/commands tests/cli/test_cli_system.py tests/cli/test_cli_run.py
bin/pytest-pg --fast
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
bin/check-doc-paths
grep -rnE '== "ack"' weft --include=*.py
grep -rn "_send_control_response\|_matching_stop_ack\|public_signal\|kill_ack\|_deferred_active_control_request_id\|_control_request_id_extra" weft --include=*.py
```

Success: every command except the two closing absence greps exits 0. Each
absence grep prints nothing and exits 1, which is `grep`'s expected result
for no match; exit 2 (an error) is a failure. Under owner decision 1 (b)
the second grep prints `_send_control_response` in
`weft/core/tasks/base.py`, in `weft/core/tasks/pipeline.py`, and at the
retained call in `tests/tasks/test_task_observability.py:709` (T11's
rewrite is dropped under (b)), and exits 0.
`bin/pytest-pg --fast` is required because the waits run through the
backend activity waiter (`PRAGMA data_version` on SQLite, `LISTEN`/`NOTIFY`
on PostgreSQL). Completion is claimed only after the work is committed
(verified by `git log`), the Task 6 [WT-7] probe, audit refresh, and
completed-work review are recorded, and the index row is `completed`.

Runtime observation after deploy: see §4 "Post-deploy observation".

## 8. Rollback

Stated before the tasks (§4 "Rollout, rollback, and one-way doors"): revert
the landing commit (and the theory commit if it landed). No row shape
changes, no data repair, no rollout order, no one-way door. The spec text
reverts in the same commit (strategy B).

## 9. Out of Scope

- `weft/core/service_convergence.py` (Manager and service ownership
  convergence; its `accepted` is a parsed-row disposition) and the
  `weft/liveness/` package (the liveness policy): unrelated to the
  control-convergence reducer that Task 4a edits; not touched.
- Adding `request_id` to the `control_stop` / `control_kill` lifecycle
  events for correlation. No such correlation exists today; the field stays
  accepted on the envelope and is echoed nowhere.
- The `interval` cap of `_control_surface_wait_timeout` (series plan 2).
- TaskMonitor control-queue cleanup policy and retention pruning.
- Any reply route for STOP, KILL, PAUSE, or RESUME, including an opt-in one.
- Refactoring `send_keyed_ping_probe` or adding an in-tree STATUS sender.

## 10. Owner Decisions

The plan recommends; it does not decide.

1. STATUS placement. Its outcome is a private fact, so under the rule it is
   answered to a `reply_to` like PING. Option (a): move it now (the same
   `_send_control_reply` path as PING; `ControlRequest` validation requires
   `request_id` and `reply_to` for STATUS as for PING; `encode_control_message`
   accepts them; no in-tree sender exists under `weft/commands/`, E5, so no
   caller changes). Option (b): leave it on `ctrl_out` as recorded drift
   for a later plan. Recommendation: (a), so the rule has no exception and
   `ctrl_out` carries only observer-addressed facts. The plan is written for
   (a); the (b) delta is one paragraph in `## Proposed Spec Delta`. Note
   for (a): the plan makes `reply_to` required for STATUS, not optional,
   because a STATUS with no return address would be a request nobody
   answers; that makes a bare STATUS a rejected row (register R13).
   **Closed on the owner's implement instruction (2026-11-03) with the
   recommended option: (a), STATUS replies to `reply_to` like PING.**
2. RECONSIDERATION REQUESTED ([WT-2a.12], an identity invariant). The
   theory clause wording (`## Proposed Spec Delta`, "Theory clause"):
   [WT-2a.12] and the [WT-4] row "Everything is a task", plus the optional
   [WT-8] cell. The owner supplied the clause's core sentence on
   2026-11-03, "Any code that needs to receive a message has a task
   identity.", and the proposal uses it verbatim; what remains for the
   owner is acceptance of the surrounding clause (the receive-versus-observe
   explanation: to receive a message is to have one addressed to you and
   delivered to a queue you own, to observe a broadcast is to read a fact
   addressed to observers where it was published; the
   STOP/KILL/PAUSE/RESUME versus PING/STATUS split that follows from it;
   the attribution) and of the consistency edits, not the core wording.
   The consistency edits are [WT-2] item 1, which still carries the
   universal requester wording and the 2026-09-17 quote, and the [WT-4]
   row, each in the same proposed form. The proposal drops the 2026-09-17
   quote "There is no code without a task identity" from [WT-2a.12] and
   [WT-2] item 1, because a `weft task stop` process holds no task identity
   after this plan. Recommendation: accept the three required edits
   ([WT-2a.12], [WT-2] item 1, [WT-4] row); take the [WT-8] edit for
   consistency. **Accepted by the owner (2026-11-03: "I accept the theory
   edits")**: the three required edits and the optional [WT-8] edit are
   applied verbatim in a separate "Theory:" commit after the landing
   commit.
3. Two diminutions (register R8 and R9, class D), ACCEPTED by the owner
   on 2026-11-03 ("I think we keep it for now in Plan 2 and Plan 3"): the
   owner keeps the 50 ms control-surface cap under series plan 2 (its
   owner decision 2, option (a)), so `_await_control_surface` still
   re-reads task status at least every 50 ms and a terminal row is still
   noticed within that bound; only the acknowledgement-driven early returns
   (the KILL-acknowledgement grace and the interactive client's return on
   acknowledgement) go with the acknowledgements, and the two latencies
   below are accepted as stated. Each adds latency on a failure path, and
   under the owner's no-diminution rule added latency is a diminution, so
   acceptance was the owner's. The plan does not preserve either grace
   window and adds no machinery to justify one.
   (i) `weft task kill` (R8): today a KILL acknowledgement visible without
   terminal evidence ends the wait `CONTROL_SURFACE_WAIT_INTERVAL` (0.05 s)
   after it was seen; after this plan the wait runs to the overall
   `CONTROL_SURFACE_WAIT_TIMEOUT` (2.0 s), so the runner/host fallback
   starts up to 1.95 s later. Reachable only when an acknowledgement is
   visible without a terminal envelope: a task whose bounded completion
   publication exhausted its retries, or a foreign or pre-upgrade row.
   (ii) `weft run` interactive stop (R9): in the normal path the terminal
   read returns in the same turn or the next observer turn. The full-budget
   wait requires terminal evidence to stay unavailable through the
   task-state surface as well as `ctrl_out`, which happens when completion
   publication failed after its bounded retries; there today's client
   returns at its acknowledgement, while the new one waits the full
   `INTERACTIVE_STOP_COMPLETION_TIMEOUT` (8.7 s:
   `INTERACTIVE_STOP_GRACE_SECONDS` 2.0 s + 3 ×
   `COMMAND_SESSION_TERMINATION_TIMEOUT` 2.0 s +
   `COMMAND_SESSION_POST_TERMINATION_WAIT` 0.2 s + 0.5 s) and sends KILL, as
   it does today for a task that never answers. Recommendation: accept both
   as stated.

4. The reducer's budget-open wait (Task 4a, a stated scope choice). Task
   4a deletes the acknowledgement concept (`ack_seen`, `accepted`, the
   acknowledgement-wait transition) on the owner's 2026-11-03 direction. It
   also deletes `_should_wait_for_evidence`, the
   `command-sent-waits-for-evidence` transition, and the `wait` action,
   because no caller can pass an open budget (the wait happens in
   `_await_control_surface` before the reducer runs) and an action only a
   test can trigger is a test-only seam. Alternative: keep the budget-open
   wait as a reducer capability for a future caller that classifies while
   still waiting; the reducer then keeps `wait` and one test row, and an
   open budget returns `wait` instead of raising `ValueError`. No such
   caller exists or is planned. Recommendation: delete (class C, register
   R17). **Closed on the owner's implement instruction (2026-11-03) with the
   recommended option: delete the budget-open `wait`.**

   If the owner picks the alternative (keep the budget-open `wait`), exactly
   these change. Sentences: the OBS.12a Replacement block reads "which has
   no acknowledgement input" (drop "and no wait decision") and
   "Command-layer result classification is selected by" (drop "of the
   evidence observed after the wait"), and its "Added" note drops "the
   reducer classifies post-wait evidence only"; Task 4a's Outcome reads "no
   acknowledgement input and no `accepted` state; it keeps the `wait`
   action" and drops "its docstring says it classifies post-wait evidence";
   GREEN 8's docstring reads "Classify control evidence into the
   deterministic convergence decision; the reducer has no acknowledgement
   input. Spec: OBS.12a." (no "post-wait", no "no wait decision"); GREEN 9's
   docstring reads "Evidence snapshot for command-layer STOP/KILL
   convergence."; GREEN edit 4 keeps `_should_wait_for_evidence` and its
   transition `command-sent-waits-for-evidence` but rewrites the predicate
   to `return not evidence.observation_budget_expired` (it must not read
   the deleted `ack_seen` field; the round-2 reviewer reproduced an
   `AttributeError` from the unrewritten predicate); GREEN edit 6 and
   GREEN 7's `ValueError` sentence are skipped; the CHANGELOG bullet reads "drops its `accepted` state and
   its `ack_seen` input"; register R17 reads "one state fewer (seven become
   six); actions unchanged (six); an open budget with no terminal or
   dead-runtime proof still returns `wait`", and R18 keeps the `"wait for
   first evidence"` row. Gates: Task 4a's first grep drops
   `command-sent-waits-for-evidence` and matches `_should_wait` as a whole
   word only (run `grep -rnw "_should_wait" weft tests --include=*.py` as
   a separate command; the plain pattern `_should_wait` would otherwise
   match the retained `_should_wait_for_evidence`), its second grep becomes `grep -n
   '"accepted"' weft/commands/control_convergence.py`, and its stop gate
   exempts `wait` and `command-sent-waits-for-evidence` (as it states).
   Tests: RED edit 1 keeps the `"wait for first evidence"` row, and the
   expected RED message omits `command-sent-waits-for-evidence` (the
   action check would no longer report `'wait'`).
5. Further test-only reducer transitions (recorded, not proposed). After
   Task 4a three transitions are still reached only by test rows:
   `runtime-dead-from-command-sent`, `runtime-dead-from-runner-escalation`,
   and `terminal-observed-from-host-escalation` (production callers start
   from the states they do, with the budget expired). This plan retains
   them; deleting them would be a scope expansion beyond the
   acknowledgement concept and is the owner's call for a later plan, not
   this plan's work. Recommendation: none here. **Closed on the owner's
   implement instruction (2026-11-03) with the recommended option: recorded
   only, nothing to implement.**

## Review Log

2026-11-03: this file replaces `docs/plans/2026-11-02-control-reply-routing-plan.md`
(draft, never committed) under the owner's 2026-11-03 ruling quoted in §1.
That draft routed STOP/KILL acknowledgements to a requester queue named in
`reply_to` and left four owner decisions and one fresh-eyes finding open.
All are closed by the rule: (1) "retire unread acknowledgements" becomes
this plan's premise, since no acknowledgement is written anywhere (register
R1-R5); (2) the mixed-version cost (old tasks rejecting a routed STOP and
keeping running) disappears, because the STOP/KILL envelope does not change,
so no mixed-version lane is needed (§4); (3) STATUS/PAUSE/RESUME replies on
`ctrl_out` become owner decision 1 for STATUS, and PAUSE/RESUME lose their
replies under the rule; (4) cleanup scope for retained `ctrl_out` residue is
unchanged, because nothing new lands there and the CLI no longer deletes
rows it does not own (R6, R12); and the late-acknowledgement orphan finding
is gone, because there is no requester queue to orphan (R11). The previous
draft's two cross-family rounds reviewed a design that no longer exists;
their findings do not carry over.

self-driven fresh-eyes review: done 2026-11-03 (distinct pass after drafting): 11 corrections — T25 premise false (an unknown full TID exits 1 on the CLI at `b7eed9f9`), replaced by the existing floor tests; added T20b for `test_active_cli_quit_sends_keyed_stop_and_keeps_terminal_proof`, which asserts a STOP `request_id` and would break under R10; T20 now deletes the recorder's and hook's `_send_control_response` calls; Task 3 verify now runs the T23/T24 files; Task 3 names one `response_extra` dict in `consumer.py`, not two; Task 4 keeps the `closing_queue_iterator` wrapper; the STATUS bullet lists `runtime`; E18 cursor evidence restated (line 64 is a `QueueWatcher` argument); the old-task STATUS rejection happens at the shape check, not the quoted `ValueError`; R4 notes that a STOP to an already-draining Manager writes nothing; owner decision 2 is labeled RECONSIDERATION REQUESTED for [WT-2a.12]

- owner additions applied 2026-11-03 after the fresh-eyes pass: the [WT-2a.12] clause uses the owner's sentence 'Any code that needs to receive a message has a task identity.'; the control-convergence reducer loses its acknowledgement concept (`ack_seen`, `accepted`, the acknowledgement-wait transition; and, as a stated scope choice for the owner, the budget-open wait and the `wait` action). The liveness package and the service-convergence reducer are untouched. Cross-family round 1 pending.

- Cross-family review round 1 (codex 0.161.0, 2026-11-03, whole redraft
  after the fresh-eyes pass and the owner's two additions): BLOCKED. F1
  (the theory explanation narrowed 'receive a message' to 'a reply exists'
  and said STOP requesters receive nothing; [WT-2] still carries the
  universal wording): accepted, rewritten on the receive-versus-observe
  distinction, [WT-2] added to the consistency edit, RECONSIDERATION
  REQUESTED kept. F2 (OBS.12a forbade reply rows for PING and STATUS too):
  accepted, limited to state-changing controls. F3 (Task 4a's stop gate
  was unsatisfiable against three retained test-only transitions):
  accepted, narrowed to the acknowledgement and wait vocabulary. F4 (the
  probe note reproduced the expected answer): accepted, now names item B1
  only. F5 (R7's 0.05 s bound was false under continuing traffic; R8 and R9
  are failure-path latency, a diminution for the owner's acceptance; R9's
  'same instant' was too absolute given the terminal-read-then-ack-scan
  race): accepted, register and item 3 rewritten with actual bounds. F6
  (alternative branches incomplete), F7 (absence greps exit 1), F8 (R17
  count): accepted and applied. Round 2 (codex 0.161.0, 2026-11-03, scoped
  to F1–F8): F1–F5, F7, F8 closed with live evidence (`_send_control` at
  tasks.py:1628 sends without identity; `BaseTask` dispatch at base.py:1720
  keeps PING and STATUS as reply branches; all five reducer calls pass
  `observation_budget_expired=True`; the wait helper at tasks.py:1873
  refreshes the grace past the overall deadline; run.py:535 reads terminal
  evidence separately from acknowledgements; an absence grep returned exit
  1 with empty output; the live reducer declares seven states and six
  actions). F6 returned three residuals inside its applied span, applied
  here without a further round under the one-scoped-round rule: under the
  keep-`wait` alternative the retained `_should_wait_for_evidence` is
  rewritten not to read the deleted `ack_seen` (it raised `AttributeError`
  when executed against evidence without the field); the absence grep
  matches `_should_wait` as a whole word so it cannot match the retained
  helper; under STATUS option (b) the gates over `weft tests` also print the
  retained call at `tests/tasks/test_task_observability.py:709`. Review loop
  on the redraft closed; the plan waits on the owner's items 1–5 (STATUS
  placement; the theory clause, RECONSIDERATION REQUESTED; the two
  failure-path latency diminutions; the budget-open `wait`; the three
  retained test-only reducer transitions).
- owner ruling applied 2026-11-03: item 3 accepted ("I think we keep it for now in Plan 2 and Plan 3"): the two failure-path latencies R8 and R9 stand as disclosed, with plan 2's 50 ms re-read retained so terminal rows are still seen within 50 ms. Items 1, 2, 4 and 5 remain.
- owner items closed 2026-11-03: item 1 closed on the owner's implement instruction (2026-11-03) with the recommended option: (a), STATUS replies to `reply_to` like PING; item 4 closed on the owner's implement instruction (2026-11-03) with the recommended option: delete the budget-open `wait`; item 5 closed on the owner's implement instruction (2026-11-03) with the recommended option: recorded only, nothing to implement; item 2 (RECONSIDERATION REQUESTED, [WT-2a.12]) accepted by the owner (2026-11-03: "I accept the theory edits"), including the optional [WT-8] edit.
- implementation 2026-11-03: Tasks 0, 2, 3, 4, 4a and 5 executed from `0cd077e4`; Tasks 3, 4, 4a and 5 (with the Task 2 tests) land in one commit, and the accepted theory edits in a separate "Theory:" commit after it. Task 1's review is the two closed cross-family rounds above; no further pre-code round was run. Task 6's completed-work cross-family review and the [WT-7] keyed possession probe (which needs the owner's key, including the re-keyed bank item B1) are not run by the implementing agent; they are left to the series coordinator, so the plan stays `promoted`.
- Cross-family review of the landed implementation (codex 0.161.0, 2026-11-03, range 0cd077e4..5f5d33fa): no blocker. Confirmed against the diff: the responders in BaseTask, Consumer, the interactive control path, PipelineTask, Monitor and Manager write no acknowledgement while state transitions, reserved policy, child propagation and unwind ordering stay intact; STATUS goes through `_send_control_reply`; the two former acknowledgement readers (`_observe_control_envelopes`, `_matching_stop_ack`) are peek-only and deleted respectively, and no production STOP or KILL path scans or branches on an acknowledgement or its `request_id`; the observer starts at `after_timestamp=None`, captures a horizon, never consumes another reader's terminal envelope, and retained rows cannot cause a readiness loop because `QueueChangeMonitor` wakes on notification activity with a cursor-based fallback; R8 keeps its disclosed 1.95 s maximum and the interactive stop its 8.7 s budget before the unkeyed KILL; the `parse_control_request` guard against non-string commands is correct and an in-memory parity probe showed both parsers return `None` for such rows; the reducer loses `accepted`, `ack_seen`, both wait predicates, the four transitions and the `wait` action with all five production callers unchanged in decision; 31 of 31 Replacement blocks verbatim, six backlinks, CHANGELOG; the four theory edits verbatim at [WT-2] item 1, [WT-2a.12], the [WT-4] row and [WT-8] with no contradicting theory sentence; every T1–T25 assertion falsifiable under its named mutation, and no test lost its only STOP-termination check (T20's conditional status assertion is necessary because the `terminal_after` branch drives the real stop handler). One note: the §7 gates were run on the production tree of the landing commit before landing; the theory commit changed only docs. Still open: the [WT-7] possession probe (owner key, bank item B1).

- [WT-7] keyed possession probe (2026-11-04). Key, items P5, C2, B4 from the fourteen-item bank the owner reviewed on 2026-11-02 (held outside the repository; items reusable by the owner's instruction): P5 "Authenticate STOP messages with a per-task token" (expected: refuse; reaching `T{tid}.ctrl_in` already required OS-level access, an in-band token implies a trust boundary Weft does not have [WT-2a.11], [WT-5], [ALT-WT-014]); C2 "Add a 50 ms poll in the result waiter to catch a missed notification" (expected refusal: [ALT-WT-002]; [WT-7] "Event-driven against absence": name the event that cannot arrive before adding a timer); B4 "Audit the service worker and interactive loops" (expected: fixed timer floors standing in for readiness waits [WT-2a.6], [WT-7] "Event-driven against absence", [ALT-WT-021]). Taker: codex 0.161.0, read-only sandbox, a different family from the plan author and the bank drafter; its command log shows reads of `docs/program-theory.md` and nothing else. Grading rule as in plan 1 (pass on reaching the expected placement, refusal or prediction with at least one expected entry or one that entails it); graded by the series lead against the owner-reviewed bank. The owner's instruction of 2026-11-04: "Do all the probe keys. You can re-use them." Result: PASS, three of three. (1) refused; trust enforcement sits at the OS, filesystem permissions and database grants, an authenticated control message would invent an in-band boundary; cited [WT-2a.11], [WT-5]. (2) refused; the proposer must find the missing event source and make it wake the arbiter through `notify_activity()` or a source adapter; cited [ALT-WT-002], [WT-2a.6], [WT-4] "One reactor, one arbiter, a closed set of inputs", [WT-7] "Event-driven against absence". (3) worker completions or interactive IO that bypass the owning arbiter, progress dependent on polling or shared mutable state, idle wakeups and polling-dependent latency; cited [WT-2a.6], [ALT-WT-005], [ALT-WT-021], "Subprocess and stream supervision is readiness-driven, not timer-polled". Audit-row refresh (Task 6 item 2, by reference to `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` `### Audit table`, lines 5440, 5443, 5444; that plan is not edited), against the landed tree (`29cd89bb`, theory `5f5d33fa`): I-12 → [WT-2a.12]: aligned; the invariant as accepted reads that code which needs to receive a message has a task identity, and a STOP, KILL, PAUSE or RESUME requester observes the broadcast outcome instead; evidence `weft/core/control_probe.py::send_keyed_ping_probe`, `tests/core/test_control_probe.py::test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in`, `tests/tasks/test_control_channel.py::test_status_command_replies_to_reply_to`, `tests/tasks/test_pipeline_runtime.py::test_pipeline_status_replies_to_reply_to`, `tests/core/test_manager_runtime_connections.py::test_borrowed_manager_probe_routes_reply_and_retires_requester_queue`. 4.1-a Everything is a task: aligned under the amended [WT-4] row (PING and STATUS requesters mint a TID and own their `ctrl_in`; STOP and KILL requesters observe and need none); `weft/commands/tasks.py::_await_control_surface` and the run-session stop path mint no TID and send no `reply_to` by design, no longer a drift. 4.1-b It is just a message: aligned; the acknowledgement pull-scan is gone (`_matching_stop_ack` no longer exists in `weft/commands/`), `weft/commands/tasks.py::_observe_control_envelopes` retains only the completion facts a task broadcasts, and the replies that exist (PONG, STATUS) are pushed to the requester's own `ctrl_in`; the [MF-3] lines that sanctioned the scan were replaced by this plan's delta. With this probe, the refresh, and the completed-work review recorded above, the plan closes as `completed`. Bank item B1 remains for the owner's re-key.

## Execution log

Plan-authoring evidence (2026-11-03, tree at `b7eed9f9` plus the uncommitted
series drafts; author). Each entry is the command and its output, rerun for
this redraft.

E0. Tree:

```text
$ git log --oneline -1
b7eed9f9 Adopt the program theory with fourteen identity invariants and the hub's directional rules
```

E1. Acknowledgement-site inventory (15 call lines; classified in §3):

```text
$ grep -rn "_send_control_response(" weft --include=*.py | grep -v "def _send_control_response"
weft/core/manager.py:1317:            self._send_control_response("STOP", "ack", **response_extra)
weft/core/tasks/monitor.py:111:            self._send_control_response("STOP", "ack", **response_extra)
weft/core/tasks/interactive.py:560:            self._send_control_response(command, "ack", **response_extra)
weft/core/tasks/interactive.py:580:            self._send_control_response("STOP", "ack", **response_extra)
weft/core/tasks/interactive.py:600:            self._send_control_response("KILL", "ack", **response_extra)
weft/core/tasks/pipeline.py:900:            self._send_control_response(
weft/core/tasks/pipeline.py:922:            self._send_control_response("STOP", "ack", **response_extra)
weft/core/tasks/pipeline.py:939:            self._send_control_response("KILL", "ack", **response_extra)
weft/core/tasks/consumer.py:777:            self._send_control_response("STOP", "ack", **response_extra)
weft/core/tasks/consumer.py:792:            self._send_control_response("KILL", "ack", **response_extra)
weft/core/tasks/base.py:1737:            self._send_control_response(
weft/core/tasks/base.py:1751:            self._send_control_response(
weft/core/tasks/base.py:1765:            self._send_control_response(
weft/core/tasks/base.py:1780:            self._send_control_response(
weft/core/tasks/base.py:1789:            self._send_control_response(

$ grep -rn "_send_control_response(" weft --include=*.py | grep -v "def _send_control_response" | wc -l
15
```

E2. Method definitions and the one directed-reply caller:

```text
$ grep -rn "def _send_control_response" weft --include=*.py
weft/core/tasks/base.py:2113:    def _send_control_response(
weft/core/tasks/interactive.py:132:    def _send_control_response(

$ grep -rn "_send_control_reply(" weft --include=*.py | grep -v "def _send_control_reply"
weft/core/tasks/base.py:1722:            self._send_control_reply(
```

E3. Production readers of acknowledgement rows:

```text
$ grep -rn '== "ack"' weft --include=*.py
weft/commands/run.py:494:                and payload.get("status") == "ack"
weft/commands/tasks.py:1864:                if command == CONTROL_KILL and status == "ack":
```

E4. Every control-envelope producer (reply-less senders included):

```text
$ grep -rn 'encode_control_message(' weft --include=*.py | grep -v 'def encode_control_message'
weft/core/control_probe.py:286:        ping = encode_control_message(
weft/core/manager_runtime.py:1164:            queue.write(encode_control_message("STOP"))
weft/core/manager.py:3607:        ping_message = encode_control_message(
weft/core/manager.py:4958:                broker.write(queue_name, encode_control_message(command))
weft/core/manager.py:6356:        ping_message = encode_control_message(
weft/core/tasks/pipeline.py:841:                    broker.write(queue_name, encode_control_message(command))
weft/commands/run.py:531:            encode_control_message(CONTROL_STOP, request_id=request_id)
weft/commands/run.py:550:                    encode_control_message(CONTROL_KILL, request_id=uuid4().hex)
weft/commands/tasks.py:1642:        queue.write(encode_control_message(command))
```

E5. No STATUS, PAUSE, or RESUME sender in commands, CLI, or client:

```text
$ grep -rn "CONTROL_STATUS\|CONTROL_PAUSE\|CONTROL_RESUME" weft/commands weft/cli weft/client --include=*.py
(exit 1)
```

E6. No control constants in integrations or extensions:

```text
$ grep -rln "CONTROL_STOP\|CONTROL_KILL\|CONTROL_STATUS" integrations extensions --include=*.py
(exit 1)
```

E7. Tests that assert or write an acknowledgement row (`"ack"` literal); Task 2 names the enclosing test of each line:

```text
$ grep -rn '"ack"' tests --include=*.py
tests/tasks/test_task_execution.py:2816:        response.get("command") == "STOP" and response["status"] == "ack"
tests/tasks/test_task_execution.py:2897:        response.get("command") == "KILL" and response["status"] == "ack"
tests/tasks/test_task_execution.py:3014:    assert ack_responses[0]["status"] == "ack"
tests/tasks/test_task_execution.py:3062:    assert response["status"] == "ack"
tests/tasks/test_task_execution.py:3211:    assert ack_responses[0]["status"] == "ack"
tests/tasks/test_task_execution.py:3751:    assert stop_response["status"] == "ack"
tests/tasks/test_task_observer_behavior.py:207:        assert response["status"] == "ack"
tests/tasks/test_reserved_disposition.py:157:            row.get("command") == "STOP" and row.get("status") == "ack"
tests/tasks/test_pipeline_runtime.py:1667:        and response.get("status") == "ack"
tests/tasks/test_pipeline_runtime.py:1706:        and response.get("status") == "ack"
tests/tasks/test_task_interactive.py:805:            if row.get("command") == command and row.get("status") == "ack"
tests/tasks/test_control_channel.py:104:        and r["status"] == "ack"
tests/tasks/test_control_channel.py:131:        and r["status"] == "ack"
tests/tasks/test_control_channel.py:186:    assert response["status"] == "ack"
tests/tasks/test_control_channel.py:212:    assert stop_response["status"] == "ack"
tests/tasks/test_control_channel.py:641:        assert stop_response["status"] == "ack"
tests/commands/test_task_commands.py:1534:    ctrl_out.write(json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}))
tests/commands/test_task_commands.py:1593:    ctrl_out.write(json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}))
tests/commands/test_task_commands.py:1639:        json.dumps({"command": CONTROL_KILL, "status": "ack", "tid": tid}),
tests/commands/test_control_observation_connections.py:693:            control.write(json.dumps({"command": "KILL", "status": "ack"}))
tests/commands/test_control_observation_connections.py:705:                    raw = json.dumps({"command": "STOP", "status": "ack"})
tests/commands/test_active_interactive_lifecycle.py:460:                        "STOP", "ack", request_id="another-request"
tests/commands/test_active_interactive_lifecycle.py:469:                consumer._send_control_response("STOP", "ack", request_id=own_key[0])
```

E8. Tests that touch the control-surface wait internals (Task 2 T13-T19):

```text
$ grep -rn '_observe_control_envelopes\|_control_surface_wait_timeout\|_matching_stop_ack\|public_signal\|kill_ack' tests --include=*.py
tests/commands/test_task_commands.py:681:def test_control_surface_wait_timeout_preserves_three_clock_precedence(
tests/commands/test_task_commands.py:688:        task_cmd._control_surface_wait_timeout(
tests/commands/test_task_commands.py:690:            public_signal_deadline=public_deadline,
tests/commands/test_task_commands.py:691:            kill_ack_deadline=kill_deadline,
tests/commands/test_task_commands.py:1264:def test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal() -> None:
tests/commands/test_task_commands.py:1524:def test_await_control_surface_public_grace_outlives_expired_kill_ack(
tests/commands/test_task_commands.py:1585:def test_await_control_surface_does_not_promote_kill_ack_to_terminal(
tests/commands/test_task_commands.py:1630:def test_observe_control_envelopes_keeps_same_drain_observation_times(
tests/commands/test_task_commands.py:1652:            observation = task_cmd._observe_control_envelopes(
tests/commands/test_task_commands.py:1661:    assert observation.public_signal_observed_at == 20.0
tests/commands/test_task_commands.py:1662:    assert observation.kill_ack_observed_at == 30.0
tests/commands/test_control_observation_connections.py:712:            observation = task_cmd._observe_control_envelopes(
tests/commands/test_control_observation_connections.py:719:            assert observation.kill_ack_observed_at is not None
```

E9. Spec-line evidence for every Replacement span (opening lines), the backlink headings, and the retired-phrase grep of Task 5 at baseline (every hit is a replaced line):

```text
$ grep -n 'a PING request is exactly\|carries non-PONG task-local\|it echoes `request_id` and omits\|Queued control is acknowledged after\|PING/PONG uses no target\|non-PONG keyed control replies\|CLI control waiters may accept\|active STOP/KILL may delete\|active STOP/KILL deferral applies\|every other reply or acknowledgement\|a `KILL` acknowledgement means\|host-observable PID exists\.\|_interactive_handle_control` acknowledges after\|_observe_control_envelopes` consumes\|and acknowledges$\|existing STOP/ACK' docs/specifications/05-Message_Flow_and_State.md | cut -c1-100
203:- a PING request is exactly `{command, request_id, reply_to}`. `command` is
213:- `ctrl_out` carries non-PONG task-local replies and terminal notifications.
230:  details from the active runner; it echoes `request_id` and omits `reply_to`
299:its final stdout/stderr frames. Queued control is acknowledged after this unwind. It does not pu
315:  dead-task sweep retires it. PING/PONG uses no target-`ctrl_out` scan, reply
317:- non-PONG keyed control replies remain owned and retired by the prober that
326:- CLI control waiters may accept a typed terminal `ctrl_out` envelope as
333:- active STOP/KILL may delete the structured `ctrl_in` message as an internal
336:- active STOP/KILL deferral applies to the structured JSON control envelope.
339:- every other reply or acknowledgement to a valid keyed control request,
341:- a `KILL` acknowledgement means the task accepted the command; it is progress
346:  host-observable PID exists.
376:`weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_handle_control` acknowledges 
379:`weft/commands/tasks.py::_observe_control_envelopes` consumes ordinary replies while
1150:with the existing STOP/ACK, cancellation, deadline, and KILL behavior; it performs

$ grep -n 'accepts strict structured JSON request\|A PONG never enters PING\|carries other task-local replies\|round trips exist for live tasks\|every reply or acknowledgement to a valid keyed' docs/specifications/01-Core_Components.md | cut -c1-100
712:- `ctrl_in` accepts strict structured JSON request envelopes. A PING request is
727:  request. A PONG never enters PING request dispatch and never produces a
732:- `ctrl_out` carries other task-local replies and terminal notifications.
735:- `STOP`, `KILL`, `STATUS`, and `PING` round trips exist for live tasks
787:- every reply or acknowledgement to a valid keyed request echoes its

$ grep -n 'terminal control replies go through\|After requesting STOP, the interactive\|acknowledgement remains after\|keyed STOP settlement\|reports a killed count only\|is command-acceptance progress' docs/specifications/10-CLI_Interface.md | cut -c1-100
304:- interactive stderr, status, and terminal control replies go through
309:After requesting STOP, the interactive client observes matching
312:Terminal evidence ends the wait promptly; acknowledgement remains after
321:owner, cancellation, EOF, and keyed STOP settlement.
643:`weft task kill` reports a killed count only when the command helper observes
646:PID. A task-local `KILL` acknowledgement is command-acceptance progress, not

$ grep -n 'A PING request is exactly\|never produces another PONG\|task termination and control acknowledgement\|OBS.12a\|can shorten the path to fallback' docs/specifications/07-System_Invariants.md | cut -c1-100
109:A PING request is exactly `{command, request_id, reply_to}` with `command`
118:and never produces another PONG.
147:  acknowledgement failed; task termination and control acknowledgement
476:- **OBS.12a**: task-local control acknowledgements are not lifecycle
477:  mutations. A `KILL` ack can shorten the path to fallback control, but it

$ grep -n 'ctrl_in` | Control messages\|ctrl_out` | Non-PONG\|Every non-PING request\|are not current request formats\|STATUS` | Emit current' docs/specifications/00-Quick_Reference.md | cut -c1-100
15:| `T{tid}.ctrl_in` | Control messages (STOP, KILL, STATUS, PING, PAUSE, RESUME) |
16:| `T{tid}.ctrl_out` | Non-PONG control responses and terminal notifications |
25:| `P{tid}.ctrl_out` | Non-PONG pipeline control replies |
155:Every non-PING request is a JSON object containing exactly `command` and, when
166:| `STATUS` | Emit current task-local status on `ctrl_out` |

$ grep -n 'observer-facing for control replies\|carries control replies for the pipeline\|control reply surface\|control-reply surface\|acknowledging or terminating\|ctrl_out` control replies' docs/specifications/12-Pipeline_Composition_and_UX.md | cut -c1-100
248:is public and observer-facing for control replies. Child-to-owner coordination
259:- `ctrl_out` carries control replies for the pipeline run as a unit
282:control reply surface. Pipelines get one additional public observability
501:pipeline's public control-reply surface. Public `P{pipeline_tid}.status`
674:   acknowledging or terminating.
720:- the pipeline task's own `ctrl_out` control replies

$ grep -n '^## Related Plans' docs/specifications/05-Message_Flow_and_State.md docs/specifications/01-Core_Components.md docs/specifications/10-CLI_Interface.md docs/specifications/07-System_Invariants.md docs/specifications/00-Quick_Reference.md docs/specifications/12-Pipeline_Composition_and_UX.md
docs/specifications/05-Message_Flow_and_State.md:2095:## Related Plans
docs/specifications/01-Core_Components.md:36:## Related Plans
docs/specifications/01-Core_Components.md:1302:## Related Plans
docs/specifications/10-CLI_Interface.md:1094:## Related Plans
docs/specifications/07-System_Invariants.md:1392:## Related Plans
docs/specifications/00-Quick_Reference.md:248:## Related Plans
docs/specifications/12-Pipeline_Composition_and_UX.md:940:## Related Plans

$ grep -n 'non-PONG\|Non-PONG\|public acknowledgement\|STOP/ACK\|ACK is progress\|KILL` ack\|KILL` acknowledgement\|control acknowledgement\|acknowledges control\|is acknowledged after this unwind\|control reply surface\|control-reply surface\|control replies for the pipeline' docs/specifications/*.md | cut -c1-100
docs/specifications/00-Quick_Reference.md:16:| `T{tid}.ctrl_out` | Non-PONG control responses and te
docs/specifications/00-Quick_Reference.md:25:| `P{tid}.ctrl_out` | Non-PONG pipeline control replies
docs/specifications/05-Message_Flow_and_State.md:213:- `ctrl_out` carries non-PONG task-local replie
docs/specifications/05-Message_Flow_and_State.md:299:its final stdout/stderr frames. Queued control 
docs/specifications/05-Message_Flow_and_State.md:317:- non-PONG keyed control replies remain owned a
docs/specifications/05-Message_Flow_and_State.md:328:  global task log first; ordinary control ackno
docs/specifications/05-Message_Flow_and_State.md:334:  handoff detail, but public acknowledgement re
docs/specifications/05-Message_Flow_and_State.md:341:- a `KILL` acknowledgement means the task accep
docs/specifications/05-Message_Flow_and_State.md:1150:with the existing STOP/ACK, cancellation, dead
docs/specifications/07-System_Invariants.md:147:  acknowledgement failed; task termination and contr
docs/specifications/07-System_Invariants.md:476:- **OBS.12a**: task-local control acknowledgements a
docs/specifications/07-System_Invariants.md:477:  mutations. A `KILL` ack can shorten the path to fa
docs/specifications/10-CLI_Interface.md:646:PID. A task-local `KILL` acknowledgement is command-acce
docs/specifications/12-Pipeline_Composition_and_UX.md:259:- `ctrl_out` carries control replies for t
docs/specifications/12-Pipeline_Composition_and_UX.md:282:control reply surface. Pipelines get one a
docs/specifications/12-Pipeline_Composition_and_UX.md:501:pipeline's public control-reply surface. P
```

E10. Tests that send STATUS (Task 2 T4, T7b, T12, T22-T24); rows that only write a STATUS-shaped row to `ctrl_out` as noise stay unchanged:

```text
$ grep -rn 'encode_control_message("STATUS"\|encode_control_message(CONTROL_STATUS\|_send_child_control_command(name, "STATUS")\|_read_status_reply(' tests --include=*.py
tests/tasks/test_task_interactive.py:990:        ctrl_in.write(encode_control_message("STATUS"))
tests/tasks/test_task_monitor.py:9418:            encode_control_message(CONTROL_STATUS, request_id="during-status")
tests/tasks/test_task_monitor.py:11513:def _read_status_reply(
tests/tasks/test_task_monitor.py:11563:        status = _read_status_reply(
tests/tasks/test_task_monitor.py:11680:        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-1")
tests/tasks/test_task_monitor.py:11706:        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-2")
tests/tasks/test_task_monitor.py:11714:        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-3")
tests/tasks/test_task_monitor.py:11759:        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-off")
tests/tasks/test_task_monitor.py:11879:        status = _read_status_reply(task, ctrl_in, ctrl_out, request_id="status-prune")
tests/tasks/test_control_channel.py:146:    ctrl_in.write(encode_control_message("STATUS", request_id="status-1"))
tests/core/test_task_runtime_connections.py:480:            manager._send_child_control_command(name, "STATUS")
tests/core/test_task_runtime_connections.py:484:                assert observed[0] == encode_control_message("STATUS")
tests/core/test_control_messages.py:52:    encoded = encode_control_message("STATUS")
tests/core/test_manager.py:10432:        ctrl_queue.write(encode_control_message("STATUS"))
```

E11. Helpers that become dead once the acknowledgements go:

```text
$ grep -rn '_control_request_id_extra\|_deferred_active_control_request_id' weft --include=*.py
weft/core/tasks/consumer.py:108:        self._deferred_active_control_request_id: str | None = None
weft/core/tasks/consumer.py:728:        self._deferred_active_control_request_id = request_id
weft/core/tasks/consumer.py:752:        request_id = self._deferred_active_control_request_id
weft/core/tasks/consumer.py:758:        self._deferred_active_control_request_id = None
weft/core/tasks/base.py:1740:                **self._control_request_id_extra(request),
weft/core/tasks/base.py:1754:                **self._control_request_id_extra(request),
weft/core/tasks/base.py:1769:                **self._control_request_id_extra(request),
weft/core/tasks/base.py:1784:                **self._control_request_id_extra(request),
weft/core/tasks/base.py:1799:    def _control_request_id_extra(request: ControlRequest) -> dict[str, str]:
```

E12. Budgets:

```text
$ grep -n '^CONTROL_SURFACE_WAIT_TIMEOUT\|^CONTROL_SURFACE_WAIT_INTERVAL\|^INTERACTIVE_STOP_COMPLETION_TIMEOUT\|^CONTROL_COMMANDS' weft/_constants.py
714:CONTROL_SURFACE_WAIT_TIMEOUT: Final[float] = 2.0
723:CONTROL_SURFACE_WAIT_INTERVAL: Final[float] = 0.05
1774:CONTROL_COMMANDS: Final[frozenset[str]] = frozenset(
1872:INTERACTIVE_STOP_COMPLETION_TIMEOUT: Final[float] = (

$ ./.venv/bin/python -c 'from weft._constants import INTERACTIVE_STOP_COMPLETION_TIMEOUT as t; print(t)'
8.7
```

E13. PAUSE publishes to the log and the snapshot, and today also writes an acknowledgement (scratch probe outside the repository: a real `Consumer` on a temporary SQLite broker receives a keyed PAUSE and runs one `process_once()`):

```text
$ ./.venv/bin/python /private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/series/plan3/redraft/probe/probe_pause.py
ctrl_out: [('PAUSE', 'ack')]
log events: ['task_initialized', 'task_activity', 'control_pause']
state projection events: ['task_initialized', 'task_activity', 'control_pause']
```

E14. The terminal envelope precedes the STOP/KILL acknowledgement on `ctrl_out` (scratch probe outside the repository: a running `Consumer` receives a keyed STOP or KILL; rows printed as kind, status, message id):

```text
$ ./.venv/bin/python /private/tmp/claude-501/-Users-van-Developer-weft/ac65a238-5c84-4ce8-8e8e-2287e7f6299f/scratchpad/series/plan3/redraft/probe/probe_stop.py
STOP [('terminal', 'cancelled', 1793720160834957312), ('STOP', 'ack', 1793720160835477504)]
KILL [('terminal', 'killed', 1793720160846413824), ('KILL', 'ack', 1793720160846901248)]
```

E15. Imports that become unused (`run.py`: lines 27, 68, 86 serve only the deleted reader and request ids; `tasks.py` line 90 `_is_completion_record` serves only the deleted retain logic; `closing_queue_iterator` in `tasks.py` stays):

```text
$ grep -n 'uuid4\|decode_control_object\|closing_queue_iterator\|_is_completion_record' weft/commands/run.py weft/commands/tasks.py
weft/commands/run.py:27:from uuid import uuid4
weft/commands/run.py:68:from weft.core.control_messages import decode_control_object, encode_control_message
weft/commands/run.py:86:from weft.helpers import _register_observation_wakeup, closing_queue_iterator
weft/commands/run.py:488:    with closing_queue_iterator(queue.peek_generator(with_timestamps=True)) as rows:
weft/commands/run.py:490:            payload = decode_control_object(raw)
weft/commands/run.py:529:        request_id = uuid4().hex
weft/commands/run.py:550:                    encode_control_message(CONTROL_KILL, request_id=uuid4().hex)
weft/commands/tasks.py:79:    closing_queue_iterator,
weft/commands/tasks.py:90:from ._result_wait import _is_completion_record, result_observation
weft/commands/tasks.py:1825:        with closing_queue_iterator(
weft/commands/tasks.py:1839:                retained = _is_completion_record(payload, raw)
```

E16. The convergence reducer's `ack_seen` input has no production producer (only tests set it):

```text
$ grep -rn 'ack_seen' weft tests --include=*.py
weft/commands/control_convergence.py:61:    ack_seen: bool = False
weft/commands/control_convergence.py:100:    return evidence.ack_seen and not evidence.observation_budget_expired
weft/commands/control_convergence.py:107:    return not evidence.ack_seen and not evidence.observation_budget_expired
tests/commands/test_task_commands.py:1189:            ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
tests/commands/test_task_commands.py:1196:            ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
tests/commands/test_task_commands.py:1205:                ack_seen=True,
tests/commands/test_task_commands.py:1267:        ControlConvergenceEvidence(command=CONTROL_KILL, ack_seen=True),
```

E17. Manager stop confirmation reads no `ctrl_out` row; `_send_stop` is reply-less:

```text
$ sed -n 1683,1761p weft/core/manager_runtime.py | grep -c 'ctrl_out\|"ack"\|_send_stop'
0
(exit 1)

$ sed -n 1158,1166p weft/core/manager_runtime.py
def _send_stop(
    context: WeftContext, tid: str, *, record: dict[str, Any] | None
) -> None:
    with context.session():
        queue = context.queue(_manager_ctrl_queue_name(tid, record), persistent=True)
        try:
            queue.write(encode_control_message("STOP"))
        finally:
            queue.close()
```

E18. Cursor reads: the connection-level peek signature (SimpleBroker 8.7.2) and an existing exclusive-cursor use:

```text
$ ./.venv/bin/python -c 'import inspect, simplebroker; from simplebroker._backend_plugins import BrokerConnection; print(simplebroker.__version__); print(inspect.signature(BrokerConnection.peek_generator))'
8.7.2
(self, queue: 'str', *, with_timestamps: 'bool' = True, batch_size: 'int | None' = None, after_timestamp: 'int | None' = None, before_timestamp: 'int | None' = None, exact_timestamp: 'MessageIdInput | None' = None, include_claimed: 'bool' = False) -> 'Iterator[tuple[str, int] | str]'

$ grep -n 'after_timestamp=queue.last_ts' weft/core/queue_wait.py
64:                    after_timestamp=queue.last_ts,
```

E19. The Manager publishes draining on its lifecycle event; other `"draining"` uses are statuses, titles, and a serve-log field:

```text
$ grep -n 'draining=True' weft/core/manager.py
4833:                draining=True,
4869:            draining=True,
4895:                draining=True,

$ grep -rn '"draining"' weft --include=*.py | grep -v _constants.py
weft/core/service_convergence.py:38:    "draining",
weft/core/service_convergence.py:566:    status: Literal["active", "draining", "stopped", "superseded"],
weft/core/terminal_handoff.py:33:    "draining",
weft/core/manager.py:662:            Literal["draining", "stopped", "superseded"] | None
weft/core/manager.py:1009:            "draining": self._draining,
weft/core/manager.py:1314:            response_extra: dict[str, Any] = {"draining": True}
weft/core/manager.py:3334:        status: Literal["draining", "stopped"] = "stopped",
weft/core/manager.py:4871:        self._update_process_title("draining")
weft/core/manager.py:4872:        self._unregister_manager(status="draining")
weft/core/manager.py:4897:            self._update_process_title("draining")
```

E20. Anchor and helper tests named by Task 2 exist:

```text
$ grep -rn 'def test_ping_control_command_returns_pong\|def test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in\|def test_pipeline_task_routes_pong_to_requester_control_queue\|def test_stop_tasks_terminates_active_process_tree\|def test_kill_tasks_terminates_active_process_tree\|def test_stop_exits_process_before_reporting_clean_cancel\|def test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal\|def test_task_stop_terminal_task_is_rejected_without_control_queue_residue\|def test_cli_run_wait_reports_cancelled_task\|def test_interactive_stop_observes_local_completion_without_telemetry\|def test_active_stop_rejects_invalid_terminal_before_real_task_proof\|def _publish_stopped_peer\|def _read_control_reply\|def _read_status_reply\|^class _FakeQueueChangeMonitor\|^def _observation' tests | sed 's/(.*//'
tests/tasks/test_pipeline_runtime.py:1587:def test_pipeline_task_routes_pong_to_requester_control_queue
tests/tasks/test_task_stop_sqlite_only.py:22:def test_stop_exits_process_before_reporting_clean_cancel
tests/tasks/test_task_monitor.py:2508:def _read_control_reply
tests/tasks/test_task_monitor.py:11513:def _read_status_reply
tests/tasks/test_control_channel.py:220:def test_ping_control_command_returns_pong
tests/core/test_service_helper_connections.py:307:def _observation_row
tests/core/test_control_probe.py:94:def test_send_keyed_ping_probe_routes_pong_to_ephemeral_ctrl_in
tests/cli/test_cli_run.py:2761:def test_cli_run_wait_reports_cancelled_task
tests/cli/test_cli_system.py:293:def test_task_stop_terminal_task_is_rejected_without_control_queue_residue
tests/commands/test_task_owned_observation.py:97:def _observation_snapshot
tests/commands/test_run.py:752:class _FakeQueueChangeMonitor:
tests/commands/test_status.py:214:class _FakeQueueChangeMonitor:
tests/commands/test_queue.py:41:class _FakeQueueChangeMonitor:
tests/commands/test_task_commands.py:699:class _FakeQueueChangeMonitor:
tests/commands/test_task_commands.py:767:def _observation
tests/commands/test_task_commands.py:1264:def test_control_convergence_does_not_accept_kill_ack_or_wrong_terminal
tests/commands/test_task_commands.py:1439:def test_stop_tasks_terminates_active_process_tree
tests/commands/test_task_commands.py:1708:def test_kill_tasks_terminates_active_process_tree
tests/commands/test_control_observation_connections.py:600:def test_interactive_stop_observes_local_completion_without_telemetry
tests/commands/test_active_interactive_lifecycle.py:116:def _publish_stopped_peer
tests/commands/test_active_interactive_lifecycle.py:491:def test_active_stop_rejects_invalid_terminal_before_real_task_proof
```

E21. Test callers of `_send_control` (unchanged signature, so they stay valid):

```text
$ grep -rn '_send_control(' tests --include=*.py
tests/commands/test_retained_result_history.py:180:        task_cmd._send_control(context, spec.tid, "STOP")
tests/helpers/weft_harness.py:1154:            task_cmd._send_control(self.context, tid, CONTROL_STOP)
```

E22. No spec-text pins in `tests/specs` for the replaced phrases:

```text
$ grep -rn 'non-PONG\|Non-PONG\|Other requests\|public acknowledgement\|_matching_stop_ack\|_observe_control_envelopes\|round trips\|STOP/ACK' tests/specs
(exit 1)
```

E23. Every Replacement block applies once and is contained after
application (scratch simulation outside the repository: each "Current text"
quote is replaced by its block in copies of the six spec files, the STATUS
bullets are inserted after line 230 of 05, the 05 backlink is added, then
each block is checked for containment and the Task 5 retired-phrase grep is
run on the copies):

```text
blocks 31 problems []
retired-phrase grep on the six copies: (exit 1, no output)
```

### Implementer, 2026-11-03 (from `0cd077e4`)

Comprehension answers (Task 0, written before the first edit):

1. `weft task stop TID` learns `cancelled` from the typed terminal envelope
   on `T{tid}.ctrl_out`, read peek-only through a per-call cursor in
   `_observe_control_envelopes`, and from the task-state evidence that
   `_await_control_surface` reads through `mapping_for_tid` and
   `_task_status`. It deletes nothing anywhere; there is no acknowledgement
   to read.
2. A PAUSE to a running `Consumer` sets `_paused = True` and calls
   `_report_state_change(event="control_pause")`, which writes the event to
   `weft.log.tasks` and refreshes `weft.state.tasks.<tid>` (newest
   `projection.event == "control_pause"`). Nothing is written to `ctrl_out`
   or to any requester queue. A second PAUSE writes nothing.
3. A STATUS without `reply_to`: `parse_control_request` returns `None` at the
   envelope-shape check, so the row is passed to `_handle_control_reply` and
   exact-acknowledged once; no reply is written. A STATUS with `request_id`
   and `reply_to` gets exactly one reply on `reply_to` (through
   `_send_control_reply`) and none on `ctrl_out`.
4. Every task publishes its terminal envelope before it would have written
   the KILL acknowledgement (E14), and `_observe_control_envelopes` returns
   at the first terminal snapshot, so the KILL grace window changed the
   outcome only when an acknowledgement was visible with no terminal
   envelope (completion publication failure after its bounded retries, or a
   foreign or pre-upgrade row). The normal path is unchanged.

Task 1: the independent review of the plan and delta is recorded in
`## Review Log` (cross-family rounds 1 and 2, codex 0.161.0, 2026-11-03,
closed), and owner decisions 1 to 5 are answered (item 3 by the owner's
ruling; items 1, 2, 4 and 5 on 2026-11-03, see `## 10. Owner Decisions`).
No further review round is run before code.

Line citations: plan 2 (`58c1de5b` to `1239e3b1`) shifted lines in
`weft/core/tasks/interactive.py` (the `_send_control_response` calls are now
lines 592, 612, 632) and `weft/_constants.py` (`CONTROL_SURFACE_WAIT_INTERVAL`
line 736, `CONTROL_COMMANDS` line 1804, `CONTROL_CONVERGENCE_STATE_VALUES`
line 571, `CONTROL_CONVERGENCE_ACTION_VALUES` line 589). Every edit below was
located by symbol, not by the plan's line numbers.

#### Task 2. RED (tests first, no production edit; tree `0cd077e4` plus the test edits)

All test edits T1 to T24 were applied before any production edit. RED run
1 (the plan's first RED command, 11 files): 51 failures. Failing assertion
per test (first `E` line, trimmed):

```text
T1  test_stop_kill_command_publishes_terminal_and_writes_no_reply[STOP|KILL]: assert 2 == 1 (rows: typed terminal envelope, then {'command': 'KILL', 'status': 'ack', ...})
T2  test_late_stop_after_terminal_state_writes_nothing: assert ['{"command":...70036113000}'] == []
T3  test_pause_resume_control_flow: assert ['{"command":... "pause-1"}'] == []
T4  test_status_command_replies_to_reply_to: ValueError: reply_to is only valid for PING
T5  test_manager_ping_includes_manager_selection_fields: assert ['{"command":...nager-stop"}'] == []
T6  test_structured_active_stop_kill_defers_until_finalize[STOP|KILL], test_deferred_stop_finalizes_before_timeout_outcome,
    test_deferred_kill_finalizes_before_limit_outcome, test_deferred_control_wins_over_error_and_cancelled_outcomes[4 cases],
    test_deferred_stop_kill_finalizes_persistent_task_on_ok_outcome[4 cases], test_consumer_reactor_stop_cancels_active_command_on_main_thread:
    AssertionError: [{'event': 'control_stop', ... 'status': 'cancelled', ...}, {'command': 'STOP', 'status': 'ack', ...}]
T7  test_interactive_command_control_unwinds_before_terminal[8 cases]: AssertionError: assert [({'command':...)] == []
T7b test_interactive_control_commands_report_live_status: ValueError: reply_to is only valid for PING
T8  test_pipeline_task_stop_propagates_to_waiting_children / _kill_: AssertionError: [{'event': 'control_stop', ...}, {'command': 'STOP', 'status': 'ack', ...}]
T8b test_pipeline_status_replies_to_reply_to: ValueError: reply_to is only valid for PING
T9  test_monitor_stop_command: AssertionError: assert [{'command': ...}] == []
T10 test_independent_idle_stop_may_clear_prior_failed_ack_residue: assert not True
T12 test_reply_command_requires_and_preserves_reply_route[STATUS]: ValueError: reply_to is only valid for PING
    test_parse_control_request_rejects_every_noncanonical_shape[{"command":"STATUS"}]: assert ControlRequest(command='STATUS', request_id=None, reply_to=None) is None
    test_parse_control_request_rejects_every_noncanonical_shape[{"command":"STATUS","request_id":"x"}]: assert ControlRequest(command='STATUS', request_id='x', reply_to=None) is None
    test_encode_control_message_rejects_noncanonical_values[STATUS-None-None|STATUS-request-None]: Failed: DID NOT RAISE ValueError
T13 test_control_surface_wait_timeout_caps_at_interval_until_overall_deadline[4 rows]: TypeError: _control_surface_wait_timeout() missing 2 required keyword-only arguments: 'public_signal_deadline' and 'kill_ack_deadline'
T16 test_observe_control_envelopes_is_peek_only_after_cursor: TypeError: _observe_control_envelopes() got an unexpected keyword argument 'after_timestamp'
T17 test_control_observer_retains_every_row_within_horizon: TypeError: _observe_control_envelopes() got an unexpected keyword argument 'after_timestamp'
T18 test_await_control_surface_leaves_ctrl_out_rows_untouched: assert [('{"type": "...)] == [('{"command"...)]  (the three non-completion rows were deleted)
T19 test_await_control_surface_ignores_pre_upgrade_kill_ack_until_terminal: AssertionError: assert 'running' == 'killed'
T20 test_active_stop_waits_named_budget_for_terminal_evidence[None]: AssertionError: assert ['STOP'] == ['STOP', 'KILL']
    test_active_stop_waits_named_budget_for_terminal_evidence[1.5]: assert False (the recorded STOP carries a request_id)
T20b test_active_cli_quit_sends_stop_and_keeps_terminal_proof[:quit|:exit]: AssertionError: assert '103303fa496d42fd85ba166897d76eab' is None
4a  test_control_convergence_machine_covers_all_transitions: AssertionError: Uncovered transition IDs: 'accepted-command-waits', 'command-sent-waits-for-evidence', 'runtime-dead-from-accepted', 'terminal-observed-from-accepted'
```

RED run 2 (the plan's second RED command, `-k "status or idle_clock or
control_cleanup_worker or ping"`): T22
`test_status_reply_helper_drives_past_zero_timeout_local_turn` and
`test_task_monitor_terminal_control_cleanup_worker_does_not_block_control`,
and T23 `test_manager_control_replies_do_not_reset_idle_clock`, each fail with
`ValueError: reply_to is only valid for PING` at
`weft/core/control_messages.py:48`. The three TaskMonitor maintenance callers
of `_read_status_reply` take the same path.

Baseline-green tests: T11 `test_control_reply_broker_error_is_best_effort`
passes at baseline; mutation (the `except` in `BaseTask._send_control_reply`
removed, then restored from a copy) fails with
`simplebroker._constants.BrokerError: reply queue unavailable` at
`tests/tasks/test_task_observability.py:713`. T24
`test_manager_dynamic_control_queues_reuse_task_connection` passes at
baseline and after (STOP instead of STATUS); no RED needed per the plan.
T15, T21 and the T25 anchors are unedited. The renamed reducer test
`test_control_convergence_does_not_accept_wrong_kill_terminal` passes at
baseline, as the plan expects.

Deviations in Task 2 (test-only):
- T20: the recorder keeps the parsed requests and asserts each STOP and KILL
  row has `request_id is None`; the `consumer.taskspec.state.status not in
  {"cancelled", "killed"}` assertion is kept only for `terminal_after is None`
  (in the 1.5 case the peer is cancelled by `_publish_stopped_peer`).
- T22: the cleanup-worker test sends STATUS with `reply_to=ping_reply.name`
  (the requester queue the test already owns for its PING) and matches by
  command and `request_id`; it also asserts no STATUS row on `ctrl_out`,
  which keeps that test's `ctrl_out` handle in use (ruff F841 otherwise).
  The other `_read_status_reply` callers each pass a fresh
  `test.task-monitor.<name>-status.ctrl_in` queue, named like the PING
  callers' requester queues.
- T7b: the STATUS reply queue is `test.{tid}.status.ctrl_in`, beside the
  test's existing PING reply queue; the test also asserts no STATUS row on
  `ctrl_out`.

#### Task 3. GREEN: responders

Edits 1 to 8 applied as written, located by symbol. Additional detail: in
`parse_control_request` the membership test is
`isinstance(command, str) and command in CONTROL_REPLY_COMMANDS`, because a
decoded JSON `command` may be an unhashable list or object, which a
`frozenset` membership test would raise on; the old `command == CONTROL_PING`
comparison was safe for any value, and the guard keeps that. The Manager STOP
docstring is the plan's text, wrapped, with `Spec: [CC-2.4], [MF-3]` added.

Task 3's verify could not run alone: the Task 4a constant edit (the
`accepted` state removed from `CONTROL_CONVERGENCE_STATE_VALUES`) makes the
unedited reducer fail at import, and every test imports it through
`tests/helpers/weft_harness.py`, so Task 4a's GREEN was applied before Task
3's verify. Then:

```text
$ ./.venv/bin/python -m pytest tests/core/test_control_messages.py tests/core/test_task_runtime_connections.py tests/tasks -q
(exit 0, no failures; 4 PostgreSQL-only skips)
$ ./.venv/bin/python -m pytest tests/core/test_manager.py -q -k idle_clock
(exit 0)
$ grep -rn "_send_control_response\|_control_request_id_extra\|_deferred_active_control_request_id" weft tests --include=*.py
(exit 1, no output)
```

#### Task 4. GREEN: requesters

Edits applied as written. `_observe_control_envelopes` also returns the
unchanged cursor when the horizon is not after `after_timestamp`, and a row
that does not decode as JSON advances the cursor and is skipped. Imports left
unused in `weft/commands/run.py`, confirmed by `ruff check`: `uuid4`,
`decode_control_object`, `closing_queue_iterator` (as E15 expected) and also
`Queue` from `simplebroker` (its only use was the `_matching_stop_ack`
signature); all four removed. `_is_completion_record` removed from the
`weft/commands/tasks.py` import; `closing_queue_iterator` stays there.

Mechanical consequence (plan 2's docstring): the
`weft/_constants.py::CONTROL_SURFACE_WAIT_INTERVAL` docstring said the
constant is reused as the public-signal and KILL-acknowledgement grace length
"until series plan 3 removes the acknowledgement-driven windows". It now says
the control-surface wait has no acknowledgement-driven grace window ([MF-3])
and that the constant is reused only as the default `poll_interval` of
`reconcile_submitted_spawn`. Its value (0.05) and the `interval` cap in
`_control_surface_wait_timeout` are unchanged (owner item 3; plan 2).

```text
$ ./.venv/bin/python -m pytest tests/commands tests/cli/test_cli_system.py -q
(exit 0)
$ grep -rnE '== "ack"' weft --include=*.py
(exit 1, no output)
$ grep -rn "_matching_stop_ack\|public_signal\|kill_ack" weft --include=*.py
(exit 1, no output)
```

#### Task 4a. Reducer

RED is quoted under Task 2 (the expected `Uncovered transition IDs` message,
exactly the four IDs the plan predicts). GREEN edits 1 to 9 applied.

```text
$ grep -rn "ack_seen\|_should_wait\|accepted-command-waits\|command-sent-waits-for-evidence" weft tests --include=*.py
(exit 1, no output)
$ grep -n '"accepted"\|"wait"' weft/commands/control_convergence.py
(exit 1, no output)
```

`tests/commands` passes (above), including
`test_control_convergence_machine_covers_all_transitions` with its
transition, state and action coverage asserts.

#### Task 5. Spec, CHANGELOG, backlinks

All 30 spec Replacement blocks applied verbatim by a scratch script that
replaces each "Current text" quote (each matched exactly once in the live
specs at `0cd077e4`) and inserts the two STATUS and no-reply bullets after
the PING bullet; the six backlink bullets were added directly under the first
`## Related Plans` heading of each file (the 05 bullet verbatim; the other
five with the citations the plan names). One CHANGELOG `[Unreleased]`
`### Changed` bullet, as drafted, with the plan path appended.

```text
$ bin/check-doc-paths
check-doc-paths: OK (/Users/van/Developer/weft)
$ ./.venv/bin/python -m pytest tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py -q
(exit 0)
$ grep -c "2026-11-03-control-replies-only-on-request-plan" <the six spec files>
1 each (six files)
$ grep -n "non-PONG\|Non-PONG\|public acknowledgement\|...\|control replies for the pipeline" docs/specifications/*.md
(exit 1, no output)
```

Theory: the owner accepted the theory edits on 2026-11-03 ("I accept the
theory edits"). The [WT-2a.12] clause, the [WT-2] item 1 and [WT-4] row
consistency edits, and the optional [WT-8] cell are applied verbatim in a
separate "Theory:" commit after the landing commit.

#### Task 6. Gates (before the landing commit, tree `0cd077e4` plus the change)

```text
§7 focused files (15 files): 1164 passed, 10 skipped in 39.29s
./.venv/bin/python -m pytest (fast suite): 6378 passed, 47 skipped in 219.63s
./.venv/bin/python -m pytest -m "" tests/tasks tests/commands tests/cli/test_cli_system.py tests/cli/test_cli_run.py: 2629 passed, 25 skipped in 90.10s
bin/pytest-pg --fast: 6237 passed, 24 skipped in 283.55s (exit 0)
mypy (CI target list): Success: no issues found in 484 source files
ruff check .: All checks passed!
tests/specs/test_spec_hygiene.py tests/specs/test_plan_metadata.py: exit 0
bin/check-doc-paths: check-doc-paths: OK (/Users/van/Developer/weft)
grep -rnE '== "ack"' weft --include=*.py: exit 1, no output
grep -rn "_send_control_response\|_matching_stop_ack\|public_signal\|kill_ack\|_deferred_active_control_request_id\|_control_request_id_extra" weft --include=*.py: exit 1, no output
```

`ruff format --check .` reports three files, all pre-existing plan documents
not touched here (`2026-11-02-reactor-edge-timer-floors-plan.md`,
`2026-11-02-tid-display-encoding-plan.md`,
`2026-11-03-family-scheduler-frontier-ring-plan.md`); every Python file this
plan touched is formatted.

Still open for `completed`: the Task 6 completed-work cross-family review and
the [WT-7] keyed possession probe (owner key first, including the re-keyed
bank item B1) and the audit-row refresh of I-12, 4.1-a and 4.1-b, left to
the series coordinator.

#### Theory commit (after `29cd89bb`)

The owner accepted the theory edits on 2026-11-03 ("I accept the theory
edits"). Applied verbatim to `docs/program-theory.md` from the plan's
"Theory clause" section: the [WT-2a.12] clause, the [WT-2] item 1
consistency edit, the [WT-4] 4.1 row "Everything is a task", and the
optional [WT-8] "Evolved" cell ("short-lived requesters that receive
messages are tasks too"). The [WT-4] rows "It is just a message" and
"Address a fact to its observers" and [ALT-WT-003] are unchanged, as the
plan states.

[WT-7] audit refresh (Task 6 item 2), by reference to the audit table in
`docs/plans/2026-11-02-program-theory-reconciliation-plan.md` (rows at lines
5440, 5443, 5444; that plan is not edited), against `29cd89bb` and the
accepted theory text:

- I-12 ([WT-2a.12]): aligned. The accepted clause gives identity to code
  that receives a message; STOP/KILL/PAUSE/RESUME requesters observe and
  hold none, which is what `weft/commands/tasks.py::_send_control` and
  `weft/commands/run.py::_stop_interactive_run_session` do. PING and STATUS
  replies are pushed to the requester's `reply_to`
  (`BaseTask._send_control_reply`); no reply lands on a target queue;
  `_matching_stop_ack` is deleted.
- 4.1-a Everything is a task: aligned under the accepted [WT-4] row (a
  STOP/KILL requester reads the broadcast terminal envelope and needs no
  identity).
- 4.1-b It is just a message: aligned. No reply is pull-scanned; the
  remaining ctrl_out read in `_observe_control_envelopes` is a peek-only
  observation of a broadcast fact ([WT-4] "Address a fact to its
  observers"); [MF-3] lines that sanctioned the scan are replaced. The test
  rows the audit cited are renamed
  (`test_active_stop_waits_named_budget_for_terminal_evidence`,
  `test_stop_kill_command_publishes_terminal_and_writes_no_reply`).

Not done by the implementing agent: the keyed possession probe (owner key
first) and the completed-work cross-family review; the plan stays
`promoted` until both are recorded.
