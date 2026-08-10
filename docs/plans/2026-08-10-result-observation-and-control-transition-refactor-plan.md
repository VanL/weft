# Result Observation And Control Transition Refactor Plan

Status: completed
Source specs: docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.3], [OBS.12a], [OBS.14]; docs/specifications/08-Testing_Strategy.md [TS-1], [TS-3], [TS-3.1]; docs/specifications/09-Implementation_Plan.md [IP-1.1]; docs/specifications/10-CLI_Interface.md [CLI-1.2], [CLI-1.3]
Superseded by: none

Class: 4 — this disposition covers public result observation, non-consuming
realtime observation, persistent result-batch consumption, and task-control
evidence acquisition. Intended behavior is unchanged, but a wrong split can
consume evidence from a read-only observer, merge persistent work batches,
misclassify an acknowledgement as terminal, miss a late custom queue, or leak
queue and watcher resources.

Plan type: behavior-preserving refactor and suppression disposition without a
normative behavior change.

Hardening: required. These functions sit on durable result and control paths,
own live queue handles, and encode timeout, grace, evidence-priority, and
cleanup behavior.

## 1. Goal

Resolve the dedicated-plan deferrals for four `C901` suppressions:

- `weft/commands/_result_wait.py::await_one_shot_result`
  (`RUFF-SUP-106`)
- `weft/commands/events.py::iter_task_realtime_events`
  (`RUFF-SUP-107`)
- `weft/commands/result.py::_await_single_result`
  (`RUFF-SUP-109`)
- `weft/commands/tasks.py::_await_control_surface`
  (`RUFF-SUP-121`)

The authoring audit recommends two different outcomes.

First, retain `RUFF-SUP-106`, `RUFF-SUP-107`, and `RUFF-SUP-109`. Their raw
scores are 34, 35, and 42. Source/locality review found no small extraction that
can remove any suppression. Reaching 10 would require passing a large mutable
temporal frame among helpers or putting queue reads, generator yields, message
consumption, and cleanup behind a misleading transition model. These functions
are complex because they own distinct current protocols with documented
evidence priority and concrete race fixes. This plan does not authorize source
churn that leaves the suppression in place while splitting its protected
invariant.

Second, attempt one bounded refactor of `RUFF-SUP-121`. Its raw score is 23.
An in-memory feasibility edit shows that three current seams can bring the
owner to 10 or lower: local monitor resource ownership, local control-envelope
observation, and pure wait-budget selection. The actual STOP/KILL transition
policy already lives in `weft/commands/control_convergence.py`; this plan must
not add a second state machine.

Primary success is the smallest truthful result: three independently reviewed
retention dispositions and, only if it is net positive, one control-surface
refactor with atomic suppression removal. If the control candidate cannot meet
the score gate within the exact mechanism budget in Section 9, retain
`RUFF-SUP-121` as well.

## 2. Requested Outcomes

- [x] Preserve one-shot result priority among task-owned terminal proof,
  manager `wrapper_lost`, readable/emitted output, log completion, quiet grace,
  timeout, and final cleanup.
- [x] Preserve realtime iteration as a read-only observer. It must peek, not
  consume, result and control surfaces.
- [x] Preserve realtime snapshot, three-cursor progression, cancellation,
  timeout, terminal grace, final result, and end-event order.
- [x] Preserve persistent result semantics: consume exactly one work-item
  batch, tolerate the documented boundary timestamp skew, do not replay stream
  chunks, and leave later batches queued.
- [x] Keep `RUFF-SUP-108` on `_await_result_materialization` unchanged. It is
  adjacent evidence acquisition with its own approved suppression, not spare
  McCabe budget for this effort.
- [x] Preserve dynamic control monitor rebinding when a TaskSpec later names a
  custom `ctrl_out` or pipeline status queue.
- [x] Preserve typed terminal `ctrl_out` as terminal proof and preserve KILL
  acknowledgement as progress only.
- [x] Preserve public-signal and kill-ack grace deadlines, overall timeout,
  latest mapping/snapshot evidence, and exact resource cleanup.
- [x] Add a characterization that actually fires late control-surface queue
  rebinding. Demonstrate sensitivity by temporarily disabling rebinding and
  observing that test fail; do not call the initially green test red-first.
- [x] Add one focused cleanup proof for initial and replacement control-monitor
  resources. Assert exact-once closure without depending on queue or mapping
  iteration order.
- [x] Do not add a generalized queue-monitor owner, result protocol, temporal
  frame, event bus, retry layer, new state machine, or cross-command framework.
- [x] Do not add behavior for a theoretical failure. Every production seam and
  test must correspond to a branch already present in the four owners.
- [x] After the control refactor, use a clean Python expert to compare baseline
  and candidate for logical locality and comprehensibility. A negative result
  enters the bounded Rework Queue; it is not immediately reverted.
- [x] Obtain separate owner dispositions for the three retention groups and the
  control candidate.
- [x] Change no public API, queue naming rule, queue persistence mode, result
  shape, event shape/order, timeout or grace value, control message, snapshot
  classification, diagnostic text, or cleanup policy.

## 3. Source Documents And Historical Context

Normative owners:

- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] owns
  backend-neutral queue activity waiting and watcher lifecycle. Weft may
  rebuild a watcher when watched names change, but must not call private or
  backend-specific notification APIs.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-3] owns control
  messages and typed terminal `ctrl_out`. [MF-5] owns shared result evidence,
  terminal priority, persistent batch boundaries, emitted-stream completion,
  result materialization, and non-consuming observation.
- `docs/specifications/07-System_Invariants.md` [OBS.3] keeps task state
  observable through task-local queues and the global task log. [OBS.12a]
  distinguishes a KILL acknowledgement from terminal killed proof and assigns
  classification to `control_convergence.py`. [OBS.14] prohibits treating
  claimed outbox residue as a decoded result.
- `docs/specifications/08-Testing_Strategy.md` [TS-1] owns real queue and
  process-boundary proof. [TS-3] owns the complexity-10 simplify-or-register
  rule. [TS-3.1] owns exact suppression identity and atomic reconciliation.
- `docs/specifications/09-Implementation_Plan.md` [IP-1.1] owns the public
  client task result/event/control adapters and states that realtime iteration
  is non-consuming.
- `docs/specifications/10-CLI_Interface.md` [CLI-1.2] owns `result` and task
  inspection behavior. [CLI-1.3] owns STOP/KILL truthfulness.
- `docs/ruff-suppression-registry.md` records the current approved exceptions.
  It is an operational ledger, not a normative behavior source, and is read
  only for exact suppression disposition and reconciliation.

Historical plans and lessons are rationale, not new requirements:

- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`
  deferred these four owners because opportunistic extraction would split
  temporal evidence without a dedicated transition/locality review.
- `docs/plans/2026-05-13-internal-state-machine-helper-plan.md` requires a
  reducer to stay pure and explicitly says not to force a generic machine when
  a rich domain state is clearer in its owner.
- `docs/plans/2026-05-10-control-and-service-convergence-state-machine-plan.md`
  and `weft/commands/control_convergence.py` already own STOP/KILL convergence.
  Queue reads, waits, and resource cleanup deliberately remain in
  `weft/commands/tasks.py`.
- `docs/plans/2026-08-01-terminal-handoff-reducer-plan.md` used a reducer for
  an observed producer-exit/result-delivery race with a pure event boundary.
  It is not precedent for wrapping every temporal loop in a machine.
- `docs/lessons.md` records concrete fixes behind current result branches:
  completion and result visibility may race; result waiters must share one
  evidence path; producer liveness and result-channel completion are separate;
  control acknowledgements are progress, not terminal proof; claimed residue
  is not decoded output.

The implementation audit must use source and `git log` to confirm these are
real current contracts. Do not turn historical prose into extra behavior.

## 4. Context And Key Files

Required repository guidance for every task:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/lessons.md` and `docs/lessons.md`

Task 1, baseline and feasibility inventory:

- modify only this plan's Evidence Log if measured facts differ
- read first: all four complete target functions, their adjacent private
  helpers, `weft/commands/control_convergence.py`,
  `weft/core/queue_wait.py::QueueChangeMonitor`, and all four registry rows
- read: `weft/commands/result.py::_await_result_materialization` only to
  understand ownership. Do not edit it
- read: every exact test named in Sections 10 and 11
- reproduce raw scores with the repo Ruff binary and validate Section 9 using
  temporary or in-memory candidates, not source churn

Task 2, result-owner retention:

- make no source or test edits for `RUFF-SUP-106`, `RUFF-SUP-107`, or
  `RUFF-SUP-109`
- read first: `_result_wait.py` output/control/log drain helpers and public
  output aggregation helpers used by `await_one_shot_result`
- read first: `events.py` snapshot/result peek helpers and all cursor updates in
  `iter_task_realtime_events`
- read first: `result.py` persistent boundary helpers,
  `_await_result_materialization`, and the public `await_task_result` adapter
- present each retention disposition separately. The owner may retain one and
  request a revised plan for another

Task 3, control characterization and candidate:

- modify only `weft/commands/tasks.py` and
  `tests/commands/test_task_commands.py`
- read first: `_ctrl_out_for_tid`, `pipeline_status_queue_name`,
  `_snapshot_from_terminal_ctrl_out`, `mapping_for_tid`, `task_status`,
  `QueueChangeMonitor`, and all `_await_control_surface` callers
- read first: `ControlConvergenceEvidence`, `reduce_control_convergence`, and
  STOP/KILL callers after `_await_control_surface`; the refactor must not move
  classification into evidence acquisition
- preserve current context/broker resolution and queue persistence flags

Task 6, suppression reconciliation:

- modify the human registry rows for every owner-approved retention so their
  rejected-alternative and approval text records this dedicated review rather
  than saying it is still pending
- for retention, leave source directives, cardinalities, raw inventory, and
  generated index unchanged
- if the control candidate earns removal, modify only its exact source
  directive, the `RUFF-SUP-121` human row, raw inventory, generated index, and
  the exact policy fixtures in `tests/specs/test_ruff_policy.py`:
  `EXPECTED_GROUP_IDS`, `EXPECTED_GROUP_COUNT`, `EXPECTED_DIRECTIVE_COUNT`,
  and `EXPECTED_C901_DIRECTIVE_COUNT`
- read first: `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1],
  `bin/ruff_suppression_index.py`, `tests/specs/test_ruff_policy.py`,
  `tests/specs/test_ruff_suppression_index.py`,
  `tests/specs/test_plan_metadata.py`, and
  `tests/specs/test_spec_hygiene.py`
- if `RUFF-SUP-121` retention wins, update only that human row's completed
  dedicated-review disposition and approval; leave its policy counts and
  derived index unchanged
- traceability-only Related Plans backlinks may be reconciled at close without
  changing normative behavior or implementation mappings

Task 7, traceability and final hardening:

- modify only files required to close a named gate or reviewer finding
- read first: the complete diff, the Mechanism Ledger, the Rework Queue, the
  Evidence Log, and the Deviation Log
- any new behavior, abstraction, or moved policy outside Section 9 requires a
  plan revision and another independent plan review before source work

Comprehension questions before editing:

1. Why can task-owned terminal proof override a manager `wrapper_lost`
   envelope, and when can visible output complete a one-shot wait?
2. Why must realtime events peek all three surfaces and keep three independent
   cursors?
3. What marks exactly one persistent work-item batch, and why must a later
   batch remain in the outbox?
4. Why is a KILL acknowledgement not terminal proof, even after its short grace
   window expires?
5. Why can `_await_control_surface` rebuild its queue monitor without becoming
   a second control-convergence state machine?
6. Which queues are non-persistent and which optional pipeline queue is
   persistent during control observation?

An implementer who cannot answer all six should not start Task 3.

## 5. Spec Baseline

Repository baseline at plan authoring:

- commit: `07c66fa29b5d3045b610ae0c0d04a11bdf202ab7`
- Ruff: `0.16.2`
- targeted registry proof: 12 tests passed serially
- raw C901 scores: 34, 35, 42, and 23 for groups 106, 107, 109, and 121
- landing uses explicit file-list staging against this plan's owned delta

Behavioral baseline:

| Owner | Normative behavior that must not move |
| --- | --- |
| `await_one_shot_result` | Task-owned terminal proof and usable output beat wrapper-loss fallback; completed/log/output grace and timeout remain local; all three queues and the monitor close. |
| `iter_task_realtime_events` | Snapshot, outbox, ctrl-out, and log evidence are observed without consuming public result/control rows; cursor and emitted-event order stay stable. |
| `_await_single_result` | Persistent mode consumes one batch only, uses completion/boundary evidence and quiet fallback, and leaves later work queued. Non-persistent mode delegates to the one-shot helper. |
| `_await_control_surface` | Dynamic queue names are re-read; monitor membership follows them; terminal ctrl-out can finish observation; ack-only KILL cannot; deadlines select waiting, not lifecycle truth. |

No normative spec delta is proposed. This is a refactor and suppression
disposition. If implementation discovers that current code violates the
baseline, stop and create a separate behavior-change plan rather than folding
the fix into this effort.

Implementation mappings remain unchanged. No module, public symbol, or
behavior owner moves.

## 6. Current Architecture And Authority

```text
result/run caller
  -> materialize queue names/evidence
  -> one-shot or persistent wait owner
       -> read outbox / ctrl_out / task log
       -> apply current evidence precedence
       -> return one public result

realtime client observer
  -> materialize names
  -> peek outbox / ctrl_out / task log
  -> emit snapshot, stream/state, result, end

STOP/KILL caller
  -> send command
  -> _await_control_surface (acquire mapping/status/ctrl evidence)
  -> reduce_control_convergence (select policy action)
  -> caller applies fallback process/runner effects
```

Authority boundaries:

- task-local queues and the global task log own durable evidence
- result wait owners decide when enough evidence exists to return public output
- realtime events own observation and ordering, but never acknowledgement or
  consumption
- `_await_control_surface` owns queue handles and observation budgets only
- `reduce_control_convergence` owns STOP/KILL result classification
- callers after reduction own runner and process fallback effects
- the suppression registry owns approved lint exceptions, not runtime behavior

No target owns a generalized result or control state model. Do not create one
to make the code look uniform.

## 7. Non-Goals And Guardrails

Out of scope:

- changing completion, timeout, cancellation, wrapper-loss, result, event, or
  control precedence
- changing SimpleBroker or `QueueChangeMonitor`
- changing queue names, persistence modes, payload schemas, or delete/peek/read
  semantics
- merging one-shot and persistent waits
- merging consuming result waits with read-only realtime observation
- editing `_await_result_materialization` or `RUFF-SUP-108`
- moving STOP/KILL reduction out of `control_convergence.py`
- adding retry, debounce, rate limit, error recovery, or malformed-payload
  behavior not already present
- adding a state-machine, transition table, protocol object, result frame,
  event framework, generic resource registry, or generalized test fake
- introducing a shared abstraction just because multiple functions use queues,
  clocks, or terminal evidence
- changing diagnostic privacy, truncation, or formatting
- asserting incidental mapping, set, or close iteration order
- opportunistic refactors in adjacent command helpers

The lint threshold is a constraint, not the design goal. A refactor that lowers
the score but scatters one protocol across scopes is a failure.

## 8. State-Machine And Mechanism Audit

### 8.1 Result owners

The three result targets have temporal state, but that does not make a generic
state machine their cleanest abstraction.

`await_one_shot_result` performs I/O and mutation on every turn: it consumes
control and outbox rows, streams output, scans logs, updates grace clocks, and
closes owned resources. A pure reducer would need a large observation frame and
would return action lists for the owner to interpret. That duplicates the
current branches rather than clarifying an independent policy.

`iter_task_realtime_events` is a generator protocol. Its branches are tied to
when a value is yielded, when a cursor advances, and when a non-consuming peek
is repeated. Moving scanners or yields into helpers would make readers
reconstruct one ordered stream across scopes.

`_await_single_result` has real batch states, but they are inseparable from
which rows have already been consumed and which timestamps remain visible in
the queue. A table would not own those effects and would leave most complexity
in the adapter while adding a second representation of the boundary protocol.

No observed bug asks for a new result transition mechanism. Existing branches
were added for concrete evidence races and are directly tested. Therefore this
plan rejects a new state machine for all three result owners.

### 8.2 Control owner

Control convergence is already a named pure state machine. The candidate does
not replace or wrap it. It only makes three existing adapter responsibilities
explicit:

1. own and replace the exact current monitor/queue set;
2. turn currently drained control envelopes into current observation facts;
3. choose the next bounded wait from the three existing deadlines.

These mechanisms correspond to current code blocks. None predicts future queue
types, control messages, retry modes, or deadline classes.

### 8.3 Explicit anti-invention question

Before Task 3 and in every clean review, answer:

> Is this mechanism required to express a branch that exists today, or is it
> being invented to guard against a failure that has not occurred?

If the answer is the latter, remove it. Correctness here means preserving the
current contract, not armoring the code against theoretical extensions.

## 9. Feasibility And Mechanism Budget

### 9.1 Reproducible score gate

Run from the repository environment:

```bash
. ./.envrc
./.venv/bin/ruff check --select C901 --ignore-noqa --output-format json \
  weft/commands/_result_wait.py \
  weft/commands/events.py \
  weft/commands/result.py \
  weft/commands/tasks.py
```

Expected authoring scores:

| Group | Raw score | Disposition |
| --- | ---: | --- |
| `RUFF-SUP-106` | 34 | Retain. Small extraction cannot remove the suppression; a temporal frame/reducer would split effect order. |
| `RUFF-SUP-107` | 35 | Retain. Scanner/yield extraction does not expose a reusable policy seam. |
| `RUFF-SUP-109` | 42 | Retain. Removing the suppression requires moving the persistent batch protocol and mutable cursors. |
| `RUFF-SUP-121` | 23 | Attempt one bounded candidate using only Sections 9.2-9.4; the independently reproduced plan-shaped candidate reaches exactly 10. |

Task 1 must reproduce the raw scores and inspect the live ownership boundaries.
The retention decision rests on source/locality review, not on a claimed unique
numeric floor for hypothetical extractions. If the control candidate does not
reach 10 using the exact mechanism budget, revise its disposition before any
suppression edit.

### 9.2 Local control-monitor resource owner

Allow one private, file-local resource owner in `weft/commands/tasks.py` for
the exact queue set currently created by `_await_control_surface`.

Required contract:

- construct mapping, global-log, current ctrl-out, and optional pipeline-status
  queue handles with their current persistence flags
- construct one `QueueChangeMonitor` over those handles
- acquire replacement queue handles into owned state one at a time so a later
  construction failure cannot leak an earlier handle
- expose the current ctrl-out queue without scanning by incidental list order
- compare current ctrl-out and pipeline names with newly materialized names
- when names change, close the old monitor and every old queue exactly once,
  then install the replacement set
- close the current monitor and every current queue exactly once on every
  return or exception path
- close the monitor before its owned queues; on partial construction failure,
  close every handle acquired so far and propagate the original exception
- remain private to `tasks.py`; no protocol, inheritance, generic type, or
  extension hook

Acceptable shape: a small private class or frozen resource record plus two
methods when that is clearer than parallel locals. The implementation reviewer
must judge the concrete code, not require a class by plan fiat.

Rejected shapes: a reusable queue registry, context-manager framework,
pluggable queue descriptor list, mutation callback, or changes to
`QueueChangeMonitor`.

### 9.3 Local control-envelope observation

Allow one private immutable record and one decoder/drain helper for facts the
current loop already computes:

- optional terminal snapshot
- the monotonic time at which a public control signal was observed, if any
- the monotonic time at which a KILL acknowledgement was observed, if any

The helper may read the current ctrl-out queue and use
`_snapshot_from_terminal_ctrl_out`. It must preserve current malformed JSON and
non-dict skipping, drain semantics, and terminal-return priority: stop reading
at the first typed terminal envelope and leave later queue rows unconsumed. It
must not
decide whether STOP/KILL succeeded, set deadlines, read mapping/status, or call
the convergence reducer. It may sample `time.monotonic()` at the exact existing
public-signal and KILL-ack branches so the owner can derive each deadline from
the real per-envelope observation time. A boolean-only post-drain result is not
acceptable because it would restart grace after a trailing backlog has drained.

The record is a local value object for one current observation turn. It is not
a public event, state-machine input framework, or future control protocol.

### 9.4 Pure wait-budget selection

Allow one private pure function for the exact existing clocks:

- overall deadline
- public-signal grace deadline
- KILL-ack grace deadline
- current monotonic time and `CONTROL_SURFACE_WAIT_INTERVAL`

It may return either a small named result or `float | None` only if the meaning
of every return is unambiguous at the call site. It must preserve:

- on the tail path where mapping/status acquisition produced no snapshot, when
  the overall deadline is expired, unexpired public-signal grace still waits
  and takes priority over KILL-ack expiry; otherwise observation ends
- when the overall deadline is still live, expired KILL-ack grace ends
  observation with the latest evidence
- otherwise wait no longer than the shortest live budget or the configured
  interval

The existing inline return for “a snapshot exists and KILL-ack grace has
expired” remains in `_await_control_surface` before wait-budget selection. It
is not an input to, or responsibility of, this helper. The helper owns only the
tail decision reached after mapping/status acquisition has not already
returned.

Do not add another clock, enum, transition table, or generalized deadline
utility. The function is justified by three existing interacting decisions,
not by future reuse.

### 9.5 Hard stop

The complete budget is:

- at most one local monitor resource owner
- at most one local observation record plus one drain/decoder
- at most one local pure wait-budget function
- no new module
- no new state machine
- no production edits outside `weft/commands/tasks.py`

After the candidate, run raw Ruff before cleanup or registry edits. If
`_await_control_surface` remains above 10, or if meeting 10 requires another
mechanism, stop. Retain `RUFF-SUP-121`. Do not add a one-`if` helper or move
collateral branches solely to shave the score.

## 10. Testing Strategy

### 10.1 Test architecture

```text
real broker-backed queues + narrow clock/status seams
  -> initial TaskSpec queue names
  -> late TaskSpec names change
  -> production control monitor owner replaces watched resources
  -> terminal/control evidence is read from the replacement queue
  -> latest mapping/status or terminal snapshot is returned
  -> all displaced and current resources close exactly once
```

The result groups use existing owner tests because the recommended source delta
is zero. The control candidate adds only proof for an existing branch and the
resource seam introduced by the refactor.

### 10.2 Existing result retention proofs

Run at minimum:

- `tests/commands/test_result.py::test_await_one_shot_result_retains_terminal_ctrl_out_proof`
- `tests/commands/test_result.py::test_await_one_shot_result_prefers_task_completed_over_manager_wrapper_lost`
- `tests/commands/test_result.py::test_await_one_shot_result_accepts_emitted_stream_when_log_event_is_missed`
- `tests/core/test_ops_shared.py::test_realtime_events_uses_terminal_state_seen_during_materialization`
- `tests/core/test_ops_shared.py::test_realtime_events_emits_state_when_terminal_derived_from_snapshot`
- `tests/commands/test_result.py::test_iter_task_realtime_events_falls_back_on_malformed_io`
- `tests/commands/test_result.py::test_await_single_result_persistent_returns_one_work_item_batch`
- `tests/commands/test_result.py::test_await_single_result_persistent_stream_mode_keeps_next_batch`
- `tests/commands/test_result.py::test_await_single_result_tolerates_materialized_boundary_timestamp_skew`

These prove representative current branches. Retention does not authorize new
tests merely to make the loops look more formal.

### 10.3 Existing control proofs

Preserve:

- `test_await_control_surface_uses_queue_monitor`
- `test_await_control_surface_does_not_promote_kill_ack_to_terminal`
- `test_await_control_surface_accepts_terminal_ctrl_out_without_log_replay`

The first proves initial monitor construction, not dynamic rebinding. Do not
overstate it. Change its exact queue-name list assertion to `Counter`; watched
queue order is not part of this contract, while multiplicity is.

### 10.4 New branch characterization

Add one test in `tests/commands/test_task_commands.py` that:

1. starts with the default `T{tid}.ctrl_out` and no pipeline status queue;
2. uses a stateful, call-counted `load_latest_taskspec_payload` seam: the
   pre-loop call returns the default surface, and the first loop call returns a
   later payload whose declared control/pipeline names differ;
3. places malformed JSON and a JSON scalar before typed terminal evidence on
   the late ctrl-out queue, proving both skip behavior and that only the
   replacement surface can finish observation;
4. asserts the initial and replacement monitor memberships with exact
   `Counter` equality, not incidental iteration order;
5. asserts the terminal result came from the replacement surface;
6. asserts every queue's persistence flag matches the current contract:
   mapping/log/ctrl-out are nonpersistent and pipeline status is persistent;
7. uses the existing fake monitor or a narrowly extended local fake. Do not add
   a general watcher simulator.

The test begins green against current code. Demonstrate sensitivity with a
temporary mutation that skips the replacement branch; record the expected
failure, then restore source immediately. The mutation must not be committed.

### 10.5 New cleanup proof

Add one focused test, combined with Section 10.4 if it stays readable, that
instruments queue and monitor `close()` calls and proves:

- every initial resource closes exactly once when replaced
- every replacement resource closes exactly once at final exit
- terminal return does not skip final cleanup
- no assertion depends on relative close order among independent queue handles

It is acceptable to assert monitor-before-owned-queue order only if the
resource owner explicitly requires that order to avoid a live watcher holding
closed queues. Otherwise assert cardinality only.

Use a test-local delegating queue wrapper, installed by monkeypatching
`WeftContext.queue` at the class method, that records `close()` by queue-object
identity and delegates every other operation to the real broker-backed queue.
Extend the existing `_FakeQueueChangeMonitor` only with per-instance
`close_calls`. Do not patch `Queue.close` globally, and do not add a reusable
fake framework. This seam distinguishes the initial and replacement handles
without inferring anything from their equal or different names.

Add a separate candidate-contract test immediately before implementing the
resource owner. Script replacement construction to fail after at least one new
queue handle has been acquired. The test must begin red against the inline
baseline, then prove that the old surface and every partially acquired new
handle close exactly once and that the original construction exception
propagates unchanged. This is a new resource-owner guarantee, not a claim about
the baseline implementation.

### 10.6 Pure helper tests

If Section 9.4 lands, table-test only the current deadline combinations:

| Overall | Public grace | KILL-ack grace | Expected decision |
| --- | --- | --- | --- |
| live | absent | absent | wait at most interval/overall remainder |
| live | live | absent | wait at most interval/overall remainder |
| live | expired | absent | wait at most interval/overall remainder |
| expired | live | absent | wait within public grace |
| expired | expired/absent | absent | finish |
| live | any | live | wait at most KILL-ack remainder |
| live | any | expired | finish |
| expired | live | live | wait within public grace |
| expired | expired/absent | live | finish |
| expired | live | expired | wait within public grace |
| expired | expired/absent | expired | finish |

Use literal monotonic values. Do not test hypothetical fourth deadlines,
negative system clock jumps, NaN, infinity, or impossible combinations unless
current code already accepts them through a real owner path.

Add one deterministic `_await_control_surface` owner test for the non-obvious
reachable precedence cell: the overall deadline is expired, public-signal grace
is live, KILL-ack grace is expired, and no snapshot exists at the first status
probe. Use a test-local mutable clock and scripted current ctrl-out reads. The
first turn reads a KILL acknowledgement. Its first monitor wait advances the
clock past KILL-ack grace and injects a later public control signal. The second
turn reads that signal and must perform a second wait within renewed public
grace rather than return at the already-expired KILL deadline; that wait then
advances beyond public grace so the next turn exits. Assert both semantic waits.
Do not queue both signals for one drain pass, use `sleep`, or use a long iterator
of incidental `time.monotonic()` call positions.

If Section 9.3 lands, direct helper tests are optional when owner tests fire all
three facts. Prefer owner behavior over duplicating implementation details.
One direct same-drain timing test is required: place a public signal and KILL
acknowledgement in one drain with distinct scripted monotonic samples and a
trailing nonterminal backlog row. Assert the observation record preserves the
two per-envelope timestamps rather than replacing them with one post-drain
time. The owner tests must still fire all three facts.

### 10.7 Test-quality rules

- use real broker-backed queues where the branch depends on queue visibility
- patch only the clock, TaskSpec materialization, task status, or monitor wait
  needed to make the current branch deterministic
- do not use sleep as correctness proof
- do not assert private dataclass field order or collection insertion order
- do not assert a mapping's key order
- do not duplicate production priority logic in the expected-value builder
- every new helper must be fired through `_await_control_surface`, not only by
  an isolated unit test
- no test may claim to prove a backend-native waiter unless it uses that
  backend; these tests prove command owner behavior

## 11. Implementation Tasks

### Task 1: reproduce baseline and feasibility

Files:

- this plan only, if measured evidence needs correction

Steps:

1. Record current commit and Ruff version in the Evidence Log.
2. Run the raw C901 command in Section 9.1.
3. Run the 12-test authoring baseline.
4. Capture `/tmp/weft-effort4-backstitch-before.json` with the exact explicit
   roots in Section 14 before any source, test, registry, or spec edit.
5. Inspect the three retained owners for a small seam that can remove their
   suppression without moving effect order; record the clean review, not a
   fabricated unique numeric floor.
6. Reproduce the plan-shaped control candidate in temporary or in-memory source
   and confirm that the exact mechanism budget reaches 10. Do not edit tracked
   source.
7. Confirm `RUFF-SUP-108` and all other adjacent directives are unchanged.
8. Confirm no existing test fires late control queue rebinding before adding
   the Section 10.4 characterization.

Done when the score table and evidence gap are independently reproducible.

### Task 2: obtain result-owner retention dispositions

Files:

- this plan only

Steps:

1. Present `RUFF-SUP-106`, `RUFF-SUP-107`, and `RUFF-SUP-109` separately with
   raw score, source/locality audit, state-machine audit, and existing proof.
2. Ask a clean Python reviewer to evaluate each retention recommendation,
   focusing on locality and whether a mechanism would address any observed
   problem.
3. Obtain owner approval for each retention.
4. Record approval in the Evidence Log. Task 6, not this task, updates the
   durable human registry rows after all dispositions are known.

If any retention is rejected, stop that group and revise this plan. Do not
start a speculative reducer under the present mechanism budget.

### Task 3: add honest control characterization

Files:

- `tests/commands/test_task_commands.py`
- `weft/commands/tasks.py` for the two temporary sensitivity mutations only;
  restore it exactly before Task 4 begins

Steps:

1. Add the late queue-name rebinding characterization from Section 10.4.
2. Add or combine the successful-replacement and terminal-return exact-close
   proof from Section 10.5.
3. Convert the existing initial-monitor queue-name assertion to
   order-independent exact membership.
4. Add the owner-level deadline-precedence test from Section 10.6.
5. Run these baseline-characterization tests against unchanged production
   code; they should pass. The partial-construction exception proof belongs to
   Task 4 because it specifies the new resource owner rather than the baseline.
6. Apply the temporary skip-rebind mutation, show that the characterization
   fails for the intended reason, then restore production source.
7. Apply a temporary wait-priority mutation that finishes at KILL-ack expiry
   before live public grace, show that the owner test fails, then restore
   production source.
8. Run the full control owner test file serially.

No production refactor begins until this current branch is honestly proven.

### Task 4: implement the bounded control candidate

Files:

- `weft/commands/tasks.py`
- `tests/commands/test_task_commands.py`

Steps:

1. Add the partial-construction exception proof from Section 10.5 and show that
   it fails against the inline baseline for the intended leaked-handle reason.
2. Introduce only the mechanisms allowed by Sections 9.2-9.4.
3. Keep mapping/status reads and deadline mutation visible in
   `_await_control_surface`.
4. Keep the snapshot-present KILL-ack-expiry return inline before the new
   wait-budget helper. Keep terminal return and KILL-ack non-terminal behavior
   exact.
5. Keep `reduce_control_convergence` and all fallback effects unchanged.
6. Add the wait-budget table and same-drain observation-timing tests required
   by Section 10.6; fire every helper through the owner tests as well.
7. Run the focused tests after each cohesive edit.
8. Run raw Ruff. If the owner remains above 10, stop and choose retention.
9. Run focused mypy, Ruff excluding only the still-present approved directive,
   formatter check, and diff check.

Do not remove the source directive or registry row yet.

### Task 5: clean Python locality review and bounded rework

Files:

- no edits by the first reviewer
- candidate files only during approved rework

The clean reviewer receives baseline source, candidate diff, plan Sections
7-10, raw scores, and focused test results. The reviewer must answer:

1. Is the refactor net positive or net negative?
2. Did it reduce logical locality or make the control loop harder to scan?
3. Does each mechanism correspond to current behavior, or was any mechanism
   invented for a problem that has not occurred?
4. Does the resource owner make exact cleanup clearer without becoming a
   generalized framework?
5. Does the observation record clarify current facts without duplicating
   `control_convergence.py`?
6. Is the wait-budget helper clearer than the inline three-clock branch?
7. Are the new tests behavior-focused and resistant to harmless internal
   change?

Verdict must be exactly `NET POSITIVE` or `NET NEGATIVE`, with concrete
findings.

- `NET POSITIVE`: proceed to Task 6.
- `NET NEGATIVE`: put the candidate in the Rework Queue. Before editing it,
  save the exact candidate diff outside the repository at
  `/tmp/weft-effort4-ruff-sup-121-attempt-1.patch`, record its SHA-256 plus
  focused test/reviewer evidence in the queue, and keep the suppression active.
  Perform at most one reviewer-directed rework and send it to a different clean
  reviewer.
- second `NET NEGATIVE`, or rework outside the mechanism budget: retain
  `RUFF-SUP-121`. Do not keep iterating until a reviewer agrees.

### Task 6: reconcile the suppression atomically

Files for approved retention and, conditionally, candidate removal:

- `docs/ruff-suppression-registry.md`
- `weft/commands/tasks.py` only if the candidate is removed
- `tests/specs/test_ruff_policy.py` exact group/directive fixtures only if the
  candidate is removed
- this plan

Steps:

1. Update retained human rows for 106, 107, and 109 with the completed
   dedicated-review and owner-approval disposition. If 121 is retained, update
   its human row the same way.
2. For retention, prove directives, cardinalities, raw inventory, generated
   index, and every exact policy fixture are unchanged.
3. Only if the 121 candidate is `NET POSITIVE` and raw C901 is gone, remove its
   source directive and human row.
4. In that removal case, exclude `RUFF-SUP-121` from `EXPECTED_GROUP_IDS`,
   decrement `EXPECTED_GROUP_COUNT`, `EXPECTED_DIRECTIVE_COUNT`, the matching
   global raw `C901` inventory count, and `EXPECTED_C901_DIRECTIVE_COUNT` by
   exactly one.
5. Regenerate the delimited index with the checker.
6. Run normal Ruff, raw Ruff, checker `--check`, and policy tests as one slice.

If any reconciliation step fails, restore the whole suppression slice. Never
leave source, human row, raw inventory, generated index, or fixtures partially
reconciled.

Retention updates lasting human rationale and approval only; it makes no
derived or source-policy change.

### Task 7: traceability and completion

Files:

- this plan
- `docs/plans/README.md`
- Related Plans paragraphs in the source specs named in metadata, only as
  needed for bidirectional traceability
- `docs/lessons.md` only if implementation exposes a repeated correction

Steps:

1. Record actual outcomes, tests, reviews, owner approvals, and any deviations.
2. Keep normative spec behavior and implementation mappings unchanged.
3. Add only traceability backlinks required by repo policy.
4. Run plan metadata, spec hygiene, DOM-15, and backstitch checks.
5. Run all final gates in Section 14.
6. Obtain owner authorization before committing. If authorization is absent,
   report the exact uncommitted file set and do not call the work complete.

## 12. Error, Rescue, And Cleanup Registry

| Boundary | Current policy to preserve | Proof |
| --- | --- | --- |
| one-shot manager wrapper loss races with task result | Task-owned terminal or usable output wins; wrapper loss is fallback | named one-shot result tests |
| one-shot completion precedes final outbox visibility | wait within completion grace and do a final drain | owner result suite |
| realtime observer sees malformed I/O metadata | fall back without consuming queues | malformed I/O test |
| realtime terminal seen during materialization | retain and emit terminal/result/end path | ops-shared tests |
| persistent boundary appears before/after visible row | tolerate documented timestamp skew and consume one batch | persistent result tests |
| control TaskSpec names materialize late | replace watched queue set and continue on new surface | new rebind characterization |
| typed terminal ctrl-out arrives without log replay | return terminal snapshot | existing control test |
| KILL ack arrives without terminal proof | wait short grace, then return latest nonterminal evidence | existing ack test |
| overall deadline expires after public signal | honor current short public-signal grace | wait-budget table + owner test path |
| monitor or queue owner exits by any return/exception | close current monitor and all current queues; displaced set already closed | new exact-close proof |

No new rescue path is authorized. In particular, do not catch new exceptions,
retry monitor construction, or silently continue after cleanup failure unless
current code already does so.

## 13. Observability, Privacy, Performance, And Order

Observability:

- result and event payloads remain unchanged
- no new logging, metrics, diagnostics, or warnings are required
- do not expose internal deadline or resource-owner fields
- the plan Evidence Log is implementation evidence, not runtime telemetry

Privacy:

- this effort does not alter exception or payload rendering
- do not add claims that result/control diagnostics are sanitized or bounded
- tests may use sentinel values to prove exact behavior but must not invent a
  new secrecy contract

Performance:

- no extra queue reads or monitor constructions on the steady-state path
- one replacement remains permitted only when current queue names change
- do not poll faster, add sleeps, or replace native activity waiting with a
  manual scan
- no benchmark is required because the candidate preserves the number and kind
  of queue operations. If the diff adds operations, stop and re-plan

Order:

- event and result order is observable and must remain exact
- persistent batch boundary order is semantic and must remain exact
- declaration order used to choose explicit queue names remains current policy
- mapping-key order, set order, and independent queue close order are not
  semantic. New tests must not depend on them
- last-writer-wins is acceptable only where current evidence precedence
  declares an order. This plan adds no new merge

## 14. Verification And Completion Gates

Load the repo environment first:

```bash
. ./.envrc
```

Focused result/control proof:

```bash
./.venv/bin/python -m pytest -q -n0 \
  tests/commands/test_result.py \
  tests/core/test_ops_shared.py \
  tests/commands/test_task_commands.py
```

Focused source quality:

```bash
./.venv/bin/mypy \
  weft/commands/_result_wait.py \
  weft/commands/events.py \
  weft/commands/result.py \
  weft/commands/tasks.py \
  --config-file pyproject.toml
./.venv/bin/ruff check \
  weft/commands/_result_wait.py \
  weft/commands/events.py \
  weft/commands/result.py \
  weft/commands/tasks.py \
  tests/commands/test_result.py \
  tests/core/test_ops_shared.py \
  tests/commands/test_task_commands.py
./.venv/bin/ruff format --check \
  weft/commands/_result_wait.py \
  weft/commands/events.py \
  weft/commands/result.py \
  weft/commands/tasks.py \
  tests/commands/test_result.py \
  tests/core/test_ops_shared.py \
  tests/commands/test_task_commands.py
```

Suppression policy:

```bash
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n0 \
  tests/specs/test_ruff_policy.py \
  tests/specs/test_ruff_suppression_index.py
```

Plan/spec hygiene:

```bash
./.venv/bin/python -m pytest -q -n0 \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py
bin/check-dom15-fixtures
bin/check-doc-paths
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json \
  > /tmp/weft-effort4-backstitch-after.json || test $? -eq 1
python3 - \
  /tmp/weft-effort4-backstitch-before.json \
  /tmp/weft-effort4-backstitch-after.json <<'PY'
from collections import Counter
import json
import sys

touched = {
    "docs/plans/2026-08-10-result-observation-and-control-transition-refactor-plan.md",
    "docs/plans/README.md",
    "docs/lessons.md",
    "docs/ruff-suppression-registry.md",
    "docs/specifications/04-SimpleBroker_Integration.md",
    "docs/specifications/05-Message_Flow_and_State.md",
    "docs/specifications/07-System_Invariants.md",
    "docs/specifications/08-Testing_Strategy.md",
    "docs/specifications/09-Implementation_Plan.md",
    "docs/specifications/10-CLI_Interface.md",
    "tests/commands/test_task_commands.py",
    "tests/specs/test_ruff_policy.py",
    "weft/commands/tasks.py",
}


def keyed_issues(path: str) -> Counter[tuple[object, ...]]:
    payload = json.loads(open(path, encoding="utf-8").read())
    return Counter(
        (
            issue.get("severity"),
            issue.get("code"),
            issue.get("path"),
            issue.get("section_id"),
            issue.get("symbol"),
            issue.get("message"),
        )
        for issue in payload["issues"]
        if issue.get("severity") in {"error", "warning"}
        and issue.get("path") in touched
    )


added = keyed_issues(sys.argv[2]) - keyed_issues(sys.argv[1])
for issue, count in sorted(added.items(), key=repr):
    print(count, issue)
raise SystemExit(bool(added))
PY
```

`bin/check-doc-paths` may reproduce only a separately established baseline of
unrelated findings. Compare keyed findings, not only the aggregate count.

Backstitch also has known repository debt and exits 1 at authoring: 45 errors,
1,025 warnings, and 610 infos. Task 1 must capture the before-report outside the
repository with the same command and roots used above. Completion captures the
after-report and runs the executable keyed comparison. The gate allows existing
touched-surface findings to remain or resolve, but permits no new error or
warning keyed by severity, code, path, section, symbol, and message. Aggregate
counts are informational because unrelated mapping work may resolve existing
debt. If the sibling Backstitch checkout is absent, record a tooling blocker;
metadata/spec-hygiene tests are not a substitute for claiming this gate passed.

Full repository gates:

```bash
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy \
  weft \
  bin \
  integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 .
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
uv lock --check
git diff --check
```

Completion requires all of the following:

- three result retention dispositions have clean review and owner approval
- control candidate is either clean-review `NET POSITIVE` with C901 removed,
  or explicitly retained after the bounded review/rework loop
- all new tests fire current branches and pass their named sensitivity checks
- no result/event/control behavior changed
- no incidental order dependency was introduced
- suppression source/registry/index state reconciles exactly
- plan/spec traceability is bidirectional
- final changed files are committed only after owner authorization

## 15. Independent Review Loop

### 15.1 Draft-plan review

Use a clean subagent with no authorship role. The review brief must explicitly
ask:

- Does the plan invent mechanisms to address issues that have not actually
  occurred?
- Is direct retention for 106/107/109 supported by score feasibility and
  locality, or does one have a small honest seam the author missed?
- Is the control mechanism budget sufficient to reach 10 without hiding
  policy?
- Does the plan accidentally duplicate the existing control-convergence state
  machine?
- Are new tests limited to current unproven branches rather than theoretical
  cases?
- Do any assertions depend on mapping, set, or incidental queue order?
- Are file ownership, review sequencing, rollback, suppression reconciliation,
  and final gates executable by a zero-context engineer?

Verdict must be `NET POSITIVE` or `BLOCKED`, with blocking findings ranked.

### 15.2 Outside-model review

After subagent findings are reconciled, run one read-only outside-model review
with repository access. The prompt must include the same anti-invention and
state-machine questions, ask the reviewer to inspect source rather than trust
the score table, and require `PASS` or `BLOCKED` with concrete citations.

Do not call this plan ready until both reviews are positive.

### 15.3 Per-refactor review

The implementation loop in Task 5 is mandatory even after plan approval. A
plan review says the candidate is plausible; only a baseline/candidate code
comparison can decide whether the actual refactor is net positive.

## 16. Rework Queue

| Candidate | Negative finding | Required rework | Reviewer | Status |
| --- | --- | --- | --- | --- |

Queue rules:

- preserve the first coherent candidate as the exact external patch and digest
  named in Task 5 before editing it, rather than deleting comparison evidence
- keep the approved suppression active while a candidate is queued
- perform only the reviewer-directed rework; no opportunistic redesign
- use a different clean reviewer for the reworked candidate
- after a second negative verdict, retain the suppression and stop
- never broaden the mechanism budget silently

## 17. Rollback And One-Way Doors

There is no public one-way door. The candidate uses private file-local
mechanisms and changes no stored data, queue payload, or API.

Rollback unit for a positive control candidate:

1. restore the original `_await_control_surface` body;
2. remove the private candidate helpers and candidate-only tests;
3. restore the exact `RUFF-SUP-121` source directive;
4. restore its human registry row, raw inventory count, generated index, and
   policy fixture state together;
5. run the focused control, Ruff, and suppression policy gates.

Do not roll back only the directive or only the registry row. Suppression state
is one atomic slice.

Retention has no production rollback because it makes no production change.

## 18. Evidence Log

| Date | Group | Evidence | Result |
| --- | --- | --- | --- |
| 2026-08-10 | baseline | HEAD `07c66fa29b5d3045b610ae0c0d04a11bdf202ab7`; Ruff 0.16.2; raw C901 authoring scan | 106=34, 107=35, adjacent 108=15, 109=42, 121=23 |
| 2026-08-10 | baseline | Nine named result proofs, three named control proofs, suppression checker, and policy suites | 12 named proofs passed; 84 suppression-policy tests passed |
| 2026-08-10 | plan | Clean review found incomplete exact fixtures, boolean timing loss, missing exception cleanup proof, and weak set membership | `BLOCKED`; plan amended for all four findings, then clean re-review `PASS` |
| 2026-08-10 | plan | Gemini read-only review and authenticated Claude read-only review | both `PASS`; Claude wrapper preflight was a false negative, direct authenticated invocation succeeded |
| 2026-08-10 | 106/107/109 | Independent source/locality and feasibility audit | no small honest seam; retain recommendation upheld and owner authorized by the implementation request |
| 2026-08-10 | characterization | Current production tests plus temporary skip-rebind and KILL-before-public mutations | baseline green; each mutation failed its intended test; both mutations restored immediately |
| 2026-08-10 | 121 | Three-mechanism candidate, partial-construction red/green proof, raw Ruff, full control owner file | partial cleanup failed on baseline then passed; `_await_control_surface` 23 -> 10; 52 control tests passed |
| 2026-08-10 | 121 | Clean baseline/candidate Python locality review | `NET POSITIVE`; suppression removal accepted |
| 2026-08-10 | suppression | Source/human row/raw inventory/generated index/exact fixtures reconciled atomically | 121 removed; 231 groups, 374 directives, 140 C901 directives; checker and 84 policy tests passed |
| 2026-08-10 | final review | Authenticated Claude read-only review of code, tests, registry, fixtures, and spec backlinks | `PASS`; three-mechanism budget and behavior/order preservation confirmed |
| 2026-08-10 | final review | Clean repository review of the final source/test/suppression/spec/plan state | `READY`; no blocking findings and no temporary mutation remains |
| 2026-08-10 | deterministic gates | Focused result/control, full default pytest, all-markers pytest, mypy, Ruff, RUF100, format, lock, plan/spec hygiene, DOM-15, suppression, doc paths, Backstitch, diff check | focused passed; default 3,659 passed/3 skipped; all-markers 3,660 passed/14 skipped; all remaining deterministic gates passed; doc paths retained the same 8 unrelated claims; Backstitch remained 45/1,025/610 with no keyed addition |
| 2026-08-10 | PostgreSQL diagnosis | Weft and SimpleBroker wrappers inspected during overlapping runs | containers were independent by unique name, random host port, and database (`weft_test` versus `simplebroker_test`); concurrent suites still shared host CPU and scheduling |
| 2026-08-10 | PostgreSQL readiness | Red/green wrapper proof plus actual container startup | in-container `pg_isready` could accept the official image's temporary Unix-socket bootstrap server before its planned restart; readiness now requires the published host TCP port and `SELECT 1` against `weft_test`; 27 wrapper tests passed |
| 2026-08-10 | PostgreSQL control test | Full gate failure reproduced in the focused TaskMonitor proof, then the owning module rerun | the test assumed one waiter wake and one reactor turn exposed its exact PONG even though waiters are hints; bounded observation of the PONG passed, then all 136 TaskMonitor tests passed with four workers |
| 2026-08-10 | PostgreSQL gate | `./.venv/bin/python bin/pytest-pg --all` at the wrapper's default logical parallelism | 3,600 passed, 12 skipped in 240.97s |

Completion status is `completed`. Implementation, review, suppression
reconciliation, traceability, deterministic gates, and the exact full
PostgreSQL gate are complete.

## 19. Mechanism Ledger

| Mechanism | Existing problem/branch | Why this size | Rejected expansion |
| --- | --- | --- | --- |
| local monitor resource owner | current branch closes/rebuilds four parallel resources when queue names change | one owner makes lifetime and exact closure explicit | generic queue registry or watcher framework |
| local control observation record | current drain produces terminal/public-signal/KILL-ack facts | three facts cross one current drain boundary | public event model or convergence input framework |
| pure wait-budget function | current loop combines three real deadlines | one pure calculation exposes existing priority | generic deadline scheduler or fourth clock |

Any implementation mechanism not listed here is out of scope until the plan is
revised and reviewed.

## 20. Deviation Log

Use the repository-required schema. Do not put ordinary test evidence here.

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

If a deviation affects result, event, control, queue, timeout, or cleanup
behavior, stop implementation. Record the observation, propose a separate spec
change, and obtain owner direction.

## 21. Authorization And Commit Boundary

This plan authorizes only the bounded work above after owner approval. It does
not authorize a public API change, a new dependency, a SimpleBroker change, a
new state machine, or source work for the three retained result owners.

Do not commit merely to satisfy the handoff gate. Obtain owner authorization to
commit. Stage only this effort's explicit files and verify the resulting commit
contains no concurrent or unrelated work.
