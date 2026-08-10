# Terminal Handoff Adapter Refactor Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-3.4], [CC-3.5]; docs/specifications/06-Resource_Management.md [RM-5], [RM-5.2]; docs/specifications/07-System_Invariants.md [REDUCER.1]-[REDUCER.8], [EXEC.5]-[EXEC.10]; docs/specifications/08-Testing_Strategy.md [TS-1], [TS-3], [TS-3.1]; docs/specifications/13-Agent_Runtime.md [AR-6]
Superseded by: none

Class: 4 — this refactors two process/session execution loops that observe live
processes, private response channels, deadlines, cancellation, resource limits,
and cleanup. Intended behavior is unchanged, but a bad split can change terminal
outcome priority, extend a drain deadline, leak a child/session, lose metrics or
diagnostics, or make the one-shot and persistent policies accidentally converge.

Plan type: implementation without a normative behavior change.

Hardening: required. Both target functions sit on the durable execution spine and
apply side effects selected by the terminal-handoff reducer.

## 1. Goal

Remove the dedicated-plan `C901` suppressions from:

- `weft/core/runners/host.py::HostTaskRunner._run_one_shot_terminal_handoff`
  (`RUFF-SUP-034`)
- `weft/core/tasks/sessions.py::AgentSession.execute` (`RUFF-SUP-048`)

The existing pure terminal-handoff state machine remains the decision authority.
The refactor should make each adapter's observation, effect, and finalization
phases easier to inspect without hiding live state behind a generic execution
framework. The baseline audit found the state machine already complete, so this
plan authorizes no state-machine refinement. A concrete candidate that proves a
missing current transition seam stops for plan revision.

Primary success is two target functions whose grounded helpers pass Ruff's
complexity-10 gate without `C901`, with current behavior/public results unchanged
and both suppression rows removed exactly. Policy-compliant fallback success is
an explicit per-adapter owner decision to retain a suppression after the bounded
experiment and one concrete rework fail the locality/comprehensibility review.

This is not a mandate to make the source look abstract. Resolve each adapter
independently. A NET POSITIVE candidate lands and closes its suppression. A NET
NEGATIVE candidate receives one concrete bounded rework when the reviewer can
name it. If reaching complexity 10 still requires a callback bundle, generic
driver, state carrier, second reducer, monitor-catch move, wide union type, or
tests coupled to new helper layout, stop and request owner disposition. The
owner may approve retaining that adapter's existing suppression as the better
design. This plan can therefore complete with zero, one, or two groups removed;
the disposition and evidence for each adapter must be explicit.

## 2. Requested Outcomes

- [x] Preserve the complete state/event table and both existing same-turn
  selection policies in `weft/core/terminal_handoff.py`.
- [x] Preserve one-shot priority: cancellation, ready outcome, timeout, limit,
  transport failure, channel seal, producer exit, drain expiry.
- [x] Preserve persistent-session priority: cancellation, timeout, ready
  outcome, limit, transport failure, channel seal, producer exit, drain expiry.
- [x] Preserve first-accepted-stop authority and one absolute drain deadline;
  stop or cleanup duration must not reset or extend it.
- [x] Preserve ordered channel/process evidence, bounded post-seal exit-code
  refinement, outcome typing, diagnostics, resource metrics, and cleanup.
- [x] Preserve the ownership difference: one-shot returns a `RunnerOutcome` and
  always reaps its producer; a successful persistent result leaves the session
  live, while every invalidating result closes it before return.
- [x] Keep channel parsing and terminal result construction owner-local. The
  one-shot carrier is a `RunnerOutcome`; the session carrier is a versioned JSON
  response parsed into `SessionExecutionResult`.
- [x] Add no behavior or test solely to protect against a theoretical issue.
  Every new mechanism and every new test must point to a current branch, current
  spec invariant, reproduced defect, or live complexity finding.
- [x] Remove `RUFF-SUP-034` or `RUFF-SUP-048` only when the corresponding
  source refactor passes its own clean review. An owner-approved retention has
  zero policy delta and records why the bounded experiment was net negative.
- [x] Change no public API, private protocol version or payload shape, state or
  event vocabulary, process policy, timeout, diagnostic schema, or output text.

## 3. Source Documents And Historical Context

Normative owners:

- `docs/specifications/01-Core_Components.md` [CC-3.4] owns host monitor
  lifecycle and [CC-3.5] owns the pure terminal-handoff reducer, its states,
  events, actions, and adapter-owned I/O boundary.
- `docs/specifications/06-Resource_Management.md` [RM-5] owns enforcement at
  the active runner and [RM-5.2] owns the deliberately different one-shot and
  persistent deadline-turn priorities.
- `docs/specifications/07-System_Invariants.md` [REDUCER.1]-[REDUCER.8] require
  a pure named state machine with complete firing proof while leaving clocks,
  process probes, channel reads, monitors, and cleanup in the adapters.
  [EXEC.5]-[EXEC.10] own ordered delivery, bounded drain, transport failure,
  cleanup before return, session validity, and edge consumption.
- `docs/specifications/08-Testing_Strategy.md` [TS-1] owns the existing complete
  reducer/adapter proof. [TS-3] owns the complexity-10 simplify-or-register
  policy. [TS-3.1] owns exact suppression identities and reconciliation.
- `docs/specifications/13-Agent_Runtime.md` [AR-6] owns persistent-session
  readiness, versioned private messages, per-work-item outcome selection, and
  session continuity/invalidation.
- `docs/ruff-suppression-registry.md` records the two current exceptions. It is
  operational, not normative, and is read only for the suppression-close tasks.

Historical plans are rationale, not behavior contracts:

- `docs/plans/2026-08-01-terminal-handoff-reducer-plan.md` is completed. It
  introduced the current state machine, ordered private transport, complete
  state/selector proof, and the two owner adapters. This effort reuses that
  architecture and does not reopen its behavior decisions.
- `docs/plans/2026-05-13-internal-state-machine-helper-plan.md` explains the
  existing pure `StateMachine` helper. It does not authorize a second runtime
  control framework.
- `docs/plans/2026-08-04-ruff-complexity-and-suppression-registry-plan.md`
  explains why these two adapter shells were deferred to a dedicated
  execution-path plan rather than split opportunistically.

## 4. Context And Key Files

Required repository guidance for every task:

- `AGENTS.md`
- `docs/agent-context/README.md`
- `docs/agent-context/decision-hierarchy.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`, especially the rule that a
  named state machine needs a contract test but cohesive owner code should not
  be fragmented just to shrink a function
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/lessons.md` and `docs/lessons.md`

Task 1, branch and proof inventory:

- modify `tests/tasks/test_runner.py` or
  `tests/tasks/test_agent_execution.py` only when a current production branch
  has no existing firing owner test
- read first: the complete two target functions, their adjacent wait/reduce and
  cleanup helpers, `weft/core/terminal_handoff.py`,
  `weft/core/terminal_handoff_transport.py`, and
  `tests/core/test_terminal_handoff.py`
- reuse the existing real spawned-process, real pipe, fake-clock, monitor, and
  session fixtures in `tests/tasks/test_runner.py`; do not add a second test
  harness or a generalized observation simulator

Task 2, one-shot host refactor:

- modify `weft/core/runners/host.py` and, only for uncovered current branches,
  `tests/tasks/test_runner.py` or `tests/tasks/test_agent_execution.py`
- read first: `HostTaskRunner.run_with_hooks`,
  `_run_one_shot_terminal_handoff`, `_stop_process`,
  `_terminal_handoff_wait_seconds`, `_reduce_terminal_observations`,
  `_start_optional_monitor`, `runner_diagnostics`, and the tests named in
  Section 10.2
- reuse `TerminalHandoffProgress`, `TerminalHandoffEvent`,
  `drive_terminal_handoff_turn`, `receive_terminal_payload`, `safe_cancel`, and
  current monitor helpers; do not duplicate their policies
- read `RUFF-SUP-313` and its exact review evidence before moving any of the
  five configured-monitor catches inside the target. That approved group says
  observation/finalization policy remains inline for locality; this plan does
  not silently overrule that judgment

Task 3, persistent-session refactor:

- modify `weft/core/tasks/sessions.py` and, only for uncovered current branches,
  `tests/tasks/test_runner.py`
- read first: `AgentSession.wait_ready`, `execute`, `poll_limits`,
  `_read_response_payload`, `_finish_invalid_result`, `close`, `terminate`,
  `_terminal_handoff_wait_seconds`, `_reduce_terminal_observations`, and the
  session rows in Section 10.2
- reuse the existing agent-session protocol parsers and result builders; do not
  create a second session message representation

Task 4, disposition, suppression, and traceability reconciliation:

- modify `docs/ruff-suppression-registry.md`,
  `tests/specs/test_ruff_policy.py`, this plan, `docs/plans/README.md`, and the
  Related Plans or implementation-mapping paragraphs in the five source specs
  named in the metadata block
- read first: `docs/specifications/08-Testing_Strategy.md` [TS-3], [TS-3.1],
  `bin/ruff_suppression_index.py`, `tests/specs/test_ruff_policy.py`,
  `tests/specs/test_ruff_suppression_index.py`, and
  `tests/specs/test_plan_metadata.py`
- use `./.venv/bin/python bin/ruff_suppression_index.py --write`; do not
  hand-edit the generated index
- `RUFF-SUP-313` is fixed context, not a permitted collateral change. If a
  concrete candidate cannot meet C901 without moving one of its directives,
  stop and revise this plan from that concrete evidence or retain
  `RUFF-SUP-034`

Task 5, final hardening:

- modify only files required to close a named gate or reviewer finding
- read first: the complete diff, the mechanism-evidence table, the Rework
  Queue, the Evidence Log, and the Deviation Log
- any new production mechanism or behavior requires renewed scope review before
  implementation

Comprehension questions before editing:

1. Why does a same-turn ready result beat timeout for one-shot work but lose to
   timeout for a persistent-session work item?
2. Which facts belong to the pure state machine, and which live effects must
   remain in `host.py` or `sessions.py`?
3. Why is producer exit only a drain trigger, while channel seal, transport
   failure, or drain expiry can produce a terminal protocol verdict?
4. Which session outcomes preserve the live conversation, and which must close
   and invalidate it before returning?

An implementer who cannot answer all four should not start the refactor.

## 5. Spec Baseline

Repository baseline at plan authoring:

- commit: `75cc1f688b3fc2e1ce93572d95fd17b2d48d3a2c`
- Ruff: `0.16.2`
- landing uses explicit file-list staging against this plan's owned delta

Raw complexity evidence:

```text
weft/core/runners/host.py:526:9: C901 `_run_one_shot_terminal_handoff` is too complex (42 > 10)
weft/core/tasks/sessions.py:432:9: C901 `execute` is too complex (32 > 10)
```

Reproduce with:

```bash
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/runners/host.py weft/core/tasks/sessions.py
```

That command also reports `start_session`, `wait_ready`, and `terminate`, which
have separate approved groups and are out of scope. Acceptance is by the two
named symbols plus every new helper, not by requiring the two files' raw C901
output to become empty.

The authoring baseline contains 234 suppression groups, 377 directives, and
143 raw `C901` directives. Other active efforts may legitimately change those
absolute values before implementation. Each close slice therefore recomputes
the live baseline and applies the exact delta in Section 13 rather than copying
these authoring totals blindly.

## 6. Spec And Traceability Strategy

Proposed Spec Delta: **none**. The current specs already define the intended
behavior and the existing state machine. There is no spec-promotion slice.

Each `NET POSITIVE` refactor, its direct tests if needed, clean-review result,
source directive removal, human registry-row removal, expected policy count
change, raw inventory change, and generated-index rewrite land as one atomic
suppression-close slice. A retained adapter instead lands a zero-delta
disposition slice with the approved baseline source and all suppression policy
artifacts unchanged. Traceability-only plan backlinks land with the last adapter
disposition even if both adapters are retained: the backlinks record the
completed design evaluation, not a behavior or ownership change. The plan remains
`draft` until both adapter dispositions and final hardening are complete; zero,
one, or two suppression groups may close.

If implementation changes any event, state, action, precedence, timeout, drain
budget, public error, diagnostic field, protocol payload, resource lifetime, or
session validity rule, stop. Reclassify as Class 5, add the exact Proposed Spec
Delta, run owner review, and promote the spec before continuing.

Final traceability edits:

- add this plan beside the completed reducer plan in the Related Plans lists of
  `docs/specifications/01-Core_Components.md`,
  `06-Resource_Management.md`, `07-System_Invariants.md`,
  `08-Testing_Strategy.md`, and `13-Agent_Runtime.md`
- update implementation mappings only if private ownership changes; do not
  rewrite normative behavior for a no-behavior-change refactor

## 7. Current Architecture

Both adapters already use the same named decision machine:

```text
owner reads clock / cancel / channel / monitor / process
                    |
                    v
          current-turn observation batch
                    |
                    v
      drive_terminal_handoff_turn(policy=owner policy)
                    |
                    v
 owner applies selected process/session effect and builds owner result
```

The state machine and private transport are not missing abstractions. They were
introduced by the completed reducer plan and have exhaustive independent tests.
The live complexity findings are in the adapter shells:

| Current responsibility | One-shot owner | Session owner | Actually identical? |
|---|---|---|---|
| reducer progress and accepted-stop state | yes | yes | yes, already shared |
| same-turn event priority | one-shot order | persistent order | no, intentionally different |
| cancel/timeout edge eligibility | yes | yes | structurally similar, policy timing differs |
| response decode | `RunnerOutcome` frame | versioned JSON result | no |
| monitor checks | scheduled host monitor | session `poll_limits` | no |
| producer-exit/drain evidence | process | session process | similar evidence, different cleanup owner |
| stop effect | `_stop_process` | `terminate` | no |
| success result | final `RunnerOutcome`; reap producer | `SessionExecutionResult`; keep session live | no |
| invalid result | return after one-shot cleanup | attach metrics, close session, then return | no |
| protocol diagnostic | runner target type and handle | agent session and invalidation | no |

This table is the design constraint. Similar spelling does not prove a shared
abstraction. Only the first row is already identical enough to share, and it is
already owned by `TerminalHandoffProgress` and the reducer.

## 8. Protected Invariants

### 8.1 Shared reducer and timing

1. `weft/core/terminal_handoff.py` remains pure and performs no process,
   channel, clock, monitor, log, cleanup, or result mutation.
2. The six states, eight events, actions, transition IDs, invalid cells, and
   both priority orders remain unchanged.
3. Observations are gathered for one turn before reduction. Branch layout must
   not replace the selector's declared priority.
4. Accepted cancellation/timeout/limit and producer exit remain edge-triggered;
   consumed facts do not starve later outcome, seal, failure, or expiry.
5. The first stop/drain action establishes one absolute deadline before the
   stop effect. Repeated observations and slow cleanup cannot reset it.
6. `spec.timeout=None` adds no task timeout. The internal drain timeout remains
   independent of `spec.timeout`.

### 8.2 One-shot host ownership

1. The adapter observes a ready terminal payload before a due same-turn timeout.
2. Channel decode/type failure is `transport_failed`, not an outcome.
3. Producer exit without terminal channel proof begins drain; it does not fail
   immediately.
4. Channel seal may use one bounded join to refine an evidence-true numeric exit
   cause. It does not guess or loop indefinitely.
5. Timeout, cancellation, limit, and protocol failure keep their exact status,
   error, duration, return code, metrics, runtime handle, and diagnostics shape.
6. Before return, a live producer is stopped when required, every producer is
   reaped, metrics are finalized best-effort, the monitor is stopped, and the
   caller's surrounding owner closes the private endpoints/process handle.
7. Existing broad monitor-boundary suppressions are not part of this effort.
   Their protected behavior, qualified symbols, and cardinality do not change.
   Moving one to a new helper is prohibited in this plan.

### 8.3 Persistent-session ownership

1. A due timeout precedes a same-turn ready work-item result; cancellation
   remains first and confirmed limit remains after both.
2. Request send and versioned response parsing retain their exact current
   behavior and errors.
3. A successful `ok` result attaches metrics and leaves the session live.
4. Timeout, cancellation, limit, protocol failure, and non-ok session-ending
   results attach metrics, close and invalidate the session, then return.
5. A later `execute()` on an invalidated session rejects as closed.
6. Limit polling does not run after producer exit is observed.
7. `wait_ready`, `close`, and `terminate` remain separate lifecycle owners and
   are not pulled into a generalized handoff driver.

### 8.4 Cross-cutting constraints

- No new dependency, public API, private protocol version, queue, persistence,
  process model, or result schema.
- No generic runner/session strategy object, callback dictionary, visitor,
  plugin hook, or inheritance hierarchy.
- No helper accepts both `RunnerOutcome` and `SessionExecutionResult` through a
  union merely to make code shared.
- No helper owns a live process/session/channel unless it remains in the same
  current owner module and corresponds to one current I/O or cleanup phase.
- No correctness assertion depends on dict or set iteration order. Observation
  priority comes from the reducer policy, not the order of the mapping used to
  collect current-turn events.
- No behavior is added to make a hypothetical future adapter easier.

## 9. Proposed Internal Design And Anti-Overengineering Gate

Names below are illustrative. The ownership and evidence rules are mandatory.

### 9.1 Keep the existing state machine

Do not add a second state machine. `TerminalHandoffProgress`,
`drive_terminal_handoff_turn`, and the current event/action table already own
the behavioral transition model. The adapter refactor may call them through a
smaller owner-local method, but must not copy their conditions.

The baseline audit found no second shared transition mechanism: the one exact
shared decision operation is already in this module. This plan does not
authorize expanding it. If a concrete candidate later reveals a missing exact
shared operation, stop and revise the plan from that evidence. A state machine
would be acceptable only when it models a current transition that the existing
machine does not already own; no such transition is known here.

### 9.2 One-shot owner-local seams

Keep the existing locals. This plan does not authorize a new state/frame
dataclass: the baseline has cohesive local state, not an existing
parameter-threading defect. If a concrete candidate later demonstrates that
explicit parameters/returns are worse, stop and revise the plan using that
candidate as evidence rather than letting the proposed extraction justify its
own carrier.

Split only at current ownership boundaries:

1. **Observe one turn:** a bounded owner-local helper may collect the existing
   cancel, channel, timeout, producer-exit, and drain-expiry observations. A
   smaller channel poll/decode leaf is also grounded in the current channel I/O
   branch. Keep monitor sampling inline under the existing `RUFF-SUP-313`
   locality decision. Do not make one helper per `if`.
2. **Apply one selected step:** set the first deadline before stop/drain effects,
   apply `_stop_process`, continue waiting, or construct the selected terminal
   result. Keep terminal result construction in `host.py`.
3. **Finalize one result:** keep producer stop/reap, metric fallback,
   runtime-handle attachment, and monitor stop order inline. This plan does not
   authorize a finalization helper because it would move three approved
   `RUFF-SUP-313` boundaries.

Five broad monitor catches in this method belong to approved group
`RUFF-SUP-313`. Its current rationale records a prior NET NEGATIVE hidden-boundary
candidate and the owner decision to keep one-call observation/finalization
policy inline. Leave all five catches adjacent to their current operations. If
the bounded host experiment cannot pass C901 under that constraint, stop. The
next action is plan revision from the concrete candidate or owner-approved
retention of `RUFF-SUP-034`, not pre-authorized catch movement.

The target method should read as the existing protocol:

```text
initialize existing owner state
repeat: observe one turn -> reduce -> apply selected step
finalize one-shot result
```

### 9.3 Session owner-local seams

Use the same phase vocabulary but keep the existing session locals. Do not add
a session frame or copy a host decomposition mechanically. If a concrete
candidate cannot remain readable with explicit parameters/returns, stop and
revise from that evidence.

Split only at current ownership boundaries:

1. **Observe one turn:** collect cancel, timeout, parsed session response,
   current limit result, producer-exit evidence, and drain expiry. Keep JSON
   parsing in the session owner.
2. **Apply one selected step:** set the first deadline before `terminate`,
   continue, return a successful live-session result, or route an invalidating
   result through `_finish_invalid_result`.
3. **Build protocol failure:** keep bounded exit-code refinement and session
   diagnostics next to session invalidation.

Do not share these helpers with host merely because the control flow looks
similar. The current audit found no unshared exact operation, so this plan
prohibits adding a new shared helper. Stop and revise the plan if a concrete
candidate later proves that owner-local code cannot remain comprehensible.

### 9.4 Mechanism-evidence ledger

Every proposed mechanism must survive this table during implementation and
clean review:

| Mechanism | Current evidence it addresses | Allowed disposition |
|---|---|---|
| existing terminal-handoff state machine | current six-state/eight-event transition contract | retain and reuse |
| owner-local observation helper | current batch-building branches in each target | allowed |
| owner-local selected-effect helper | current reducer-action branches in each target | allowed; result and cleanup ownership remains adapter-specific |
| host finalization helper | would move three approved monitor catches despite current locality evidence | prohibited in this plan |
| owner-local mutable frame | no baseline parameter-threading defect | prohibited; revise plan if a concrete candidate supplies evidence |
| shared pure deadline/turn refinement | current audit found no unshared exact transition operation | prohibited; revise plan if a concrete candidate supplies evidence |
| movement of a `RUFF-SUP-313` monitor catch | current approved evidence favors inline locality | prohibited; stop/revise or retain host group |
| new state/event/action | no current uncovered behavior | prohibited |
| generic adapter driver or callback strategy | owner result, monitor, and cleanup semantics differ | prohibited |
| retry, extra timeout, fallback, warning, diagnostic, or cleanup phase | no reproduced current defect in this effort | prohibited |
| new generalized test harness/property model | existing exhaustive reducer and real process fixtures already fire the contract | prohibited |

The clean reviewer must list every mechanism introduced by the candidate and
point to its row and current evidence. A mechanism with no current evidence is
a blocking finding, even if it looks defensive or future-proof.

### 9.5 Complexity is a constraint, not the design oracle

All changed production functions must pass raw `C901` at the configured
threshold. Moving a directive to a new helper, splitting one coherent branch
into tiny wrappers, or replacing visible control flow with dynamic dispatch is
not acceptance. The clean reviewer has authority to mark a Ruff-clean candidate
net negative.

## 10. Testing Plan

### 10.1 Test policy

This is a behavior-preserving refactor. Run the existing tests before editing
and after every slice. Add a test only when the branch-to-proof inventory finds
a current production branch with no firing proof. A proposed new helper is not
itself a new behavioral contract and does not earn a white-box test.

Do not add states, pair permutations, timing races, cleanup failures, or
malformed payload cases merely because they are conceivable. The completed
reducer plan already provides 48 state/event cells, 510 non-empty selector
subsets, 56 adapter pair routes, multi-turn edge consumption, real spawn/pipe
examples, and fake-clock deadline proof.

Tests assert observable results, process/session lifetime, exact call order
where order is contract, and resource closure. They do not assert any new
helper name, frame layout, or helper-call count. The two existing
`_reduce_terminal_observations` methods are an explicit exception: the completed
reducer plan established them as adapter-routing contract seams, and the 28-pair
tests call them directly to prove each adapter selects its declared policy.
Preserve those seams and tests unless a separate plan replaces them with equally
complete production-driver proof.

### 10.2 Exact branch-to-proof matrix

All node IDs below are in `tests/tasks/test_runner.py` unless another file is
named. Parameterized nodes run in full; do not select one convenient parameter.

| Current target branch or invariant | Exact existing proof |
|---|---|
| complete six-state/eight-event reducer, invalid cells, both selector policies, consumed edges | full `tests/core/test_terminal_handoff.py` |
| one-shot adapter routes all 28 pairs through `one_shot` policy | `test_host_runner_routes_all_event_pairs_through_one_shot_policy` |
| session adapter routes all 28 pairs through `persistent_session` policy | `test_agent_session_routes_all_event_pairs_through_persistent_policy` |
| accepted stop levels are consumed before later seal | `test_both_adapters_consume_stop_levels_before_later_seal` |
| producer exit is consumed before drain expiry | `test_both_adapters_consume_dead_producer_before_drain_expiry` |
| one-shot ordinary outcome and producer reap | `test_task_runner_executes_function_successfully` |
| one-shot exit observed before delayed channel outcome | `test_real_pipe_exit_then_outcome_uses_production_handoff_driver` |
| one-shot transport failure and bounded diagnostic | `test_real_pipe_write_failure_reaches_bounded_parent_transport_verdict` |
| one-shot producer exit with leaked sender reaches drain expiry | `test_one_shot_leaked_sender_reaches_bounded_drain_expiry` |
| one-shot abrupt exit/exit-code diagnostic | `test_task_runner_reports_abrupt_worker_exit_diagnostics` |
| one-shot timeout | `test_function_timeout_reports_timeout_when_no_result_is_ready`; `tests/tasks/test_agent_execution.py::test_task_runner_agent_timeout` |
| one-shot cancellation | `tests/tasks/test_agent_execution.py::test_task_runner_agent_can_be_cancelled` |
| one-shot confirmed limit | `tests/tasks/test_agent_execution.py::test_task_runner_agent_limit_violation` |
| one-shot receive/decode transport exception | add `tests/tasks/test_runner.py::test_host_terminal_receive_failure_is_transport_failure` |
| one-shot decoded payload has the wrong type | add `tests/tasks/test_runner.py::test_host_terminal_wrong_decoded_payload_type_is_transport_failure` |
| one-shot blocking poll fails and is consumed on the next turn | add `tests/tasks/test_runner.py::test_host_blocking_poll_failure_is_consumed_as_next_turn_transport_failure` |
| one-shot first absolute drain deadline | `test_one_shot_stop_effect_cannot_reset_absolute_drain_deadline` |
| one-shot stop accepted after ordinary drain preserves the first deadline | add `tests/tasks/test_runner.py::test_one_shot_stop_after_begin_drain_preserves_first_deadline` |
| one-shot channel seal while producer is initially live gets one bounded exit-code refinement | add `tests/tasks/test_runner.py::test_host_channel_seal_refines_exit_after_bounded_join` |
| host monitor startup and missing-PID cleanup, three sites | `test_host_monitor_start_failure_attempts_stop_without_replacing_outcome`; `test_host_monitor_without_pid_attempts_stop_and_reports_failure`; `test_host_monitor_without_pid_reports_disable_after_successful_cleanup` |
| host monitor poll check/metrics failures and cached outcome | `test_host_monitor_poll_failures_preserve_cached_metrics_and_outcome` |
| host final metrics fallback | `test_host_monitor_final_metrics_failure_uses_snapshot_before_stop` |
| host snapshot and final stop failures | `test_host_monitor_snapshot_and_stop_failures_do_not_replace_outcome` |
| remaining two `RUFF-SUP-313` agent-startup cleanup sites | `test_agent_session_monitor_load_failure_cleans_started_resources`; `test_agent_session_construction_failure_reports_monitor_cleanup_failure` |
| one-shot prewrite serialization and large framed result | `test_host_runner_reports_prewrite_result_serialization_failure`; `test_host_runner_large_result_exceeds_pipe_buffer_without_deadlock` |
| one-shot spawn endpoint cleanup | `test_one_shot_spawn_failure_closes_both_response_endpoints` |
| one-shot process stop/reap operational and unexpected failures | `test_host_process_stop_reports_join_failure_and_escalates`; `test_host_process_stop_propagates_unexpected_join_failure` |
| session successful work item remains usable | `test_task_runner_agent_session_continues_conversation` |
| session post-ready error result survives immediate exit and invalidates | `test_production_agent_worker_post_ready_error_survives_immediate_exit` |
| session timeout/cancel/limit close before second execute | `test_agent_session_invalidating_verdict_closes_session` |
| session metrics failure cannot obstruct invalidation | `test_agent_session_monitor_metrics_failure_cannot_block_invalid_cleanup` |
| session seal with dead/unproved producer | `test_agent_session_eof_without_result_closes_session`; `test_agent_session_live_eof_is_channel_failure` |
| session wrong or malformed response is transport failure | `test_agent_session_wrong_payload_type_is_transport_failure`; `test_agent_session_malformed_nested_result_is_transport_failure` |
| session response read raises a transport exception | add `tests/tasks/test_runner.py::test_agent_session_response_read_failure_is_transport_failure` |
| session blocking poll fails and is consumed on the next turn | add `tests/tasks/test_runner.py::test_agent_session_blocking_poll_failure_is_consumed_as_next_turn_transport_failure` |
| session does not poll limit after producer exit | `test_agent_session_does_not_poll_limits_after_producer_exit` |
| session first absolute drain deadline | `test_session_stop_effect_cannot_reset_absolute_drain_deadline` |
| session producer exit starts ordinary bounded drain and the wait honors that deadline | add `tests/tasks/test_runner.py::test_agent_session_producer_exit_starts_bounded_drain` |
| session stop accepted after ordinary drain preserves the first deadline | add `tests/tasks/test_runner.py::test_session_stop_after_begin_drain_preserves_first_deadline` |
| session monitor limit/metrics and ordinary adapter fail-open | `test_session_limit_verdict_survives_optional_metrics_read_failure`; `test_session_monitor_adapter_failure_fails_open_and_releases_monitor`; `test_session_monitor_poll_propagates_non_exception_failure_identity` |
| session close/IPC cleanup | `test_agent_session_close_releases_multiprocessing_handles`; `test_agent_session_ipc_cleanup_reports_supported_process_close_failure`; `test_agent_session_ipc_cleanup_propagates_unexpected_process_close_failure` |
| session spawn cleanup | `test_agent_session_spawn_failure_closes_queue_and_response_endpoints` |

The inventory found nine current owner mechanisms without direct firing proof,
so Task 1 adds the nine named characterization tests before source edit. The
session response-read test must drive the real `_read_response_payload`
nonblocking poll failure rather than monkeypatch that method; the outer
`execute` transport catch was already fired by the wrong-payload test. The
adapter `action == "wait"`
arms and unknown-action assertions are closed-world defensive guards, not live
branches through a consistent `TerminalHandoffProgress`: the pure reducer table
proves the `wait` actions, while edge consumption prevents those events from
reaching the adapter twice. Do not add synthetic owner tests that bypass the
driver merely to mark those guards covered. A proposed new helper is not a
reason to add a white-box test.

For the two blocking-poll proofs, follow the existing narrow
`tests/tasks/test_runner.py::_DeferredFirstPollConnection` boundary-fake pattern:
wrap only `poll()`, fail the blocking call, and delegate the remaining connection
behavior. Do not introduce a reusable handoff harness or model a new protocol.

### 10.4 Review fidelity probes

After each source refactor, temporarily make one local mutation and prove an
existing test fails, then restore it verbatim:

- host: set the drain deadline after the stop effect; the absolute-deadline test
  must fail
- session: return an invalidating result without `_finish_invalid_result`; the
  invalidation/second-execute proof must fail

These mutations prove the current suite protects the moved seams. Do not create
a mutation framework or expand the mutation list without a concrete review
question.

## 11. Error And Rescue Registry

| Current path | Failure/evidence | Current owner response to preserve | Public result |
|---|---|---|---|
| response read/decode/type | EOF, transport error, invalid payload | emit seal/failure event; reducer decides | bounded current protocol error |
| producer exits before result is observed | dead process plus no terminal proof | begin bounded drain | later outcome or evidence-true failure |
| stop intent | cancel, timeout, or confirmed limit | accept first stop, set deadline, stop/terminate | current cancel/timeout/limit result |
| channel seals before exit code is published | seal with unproved producer state | one bounded join may refine cause | generic channel failure or numeric-exit failure |
| host monitor check/sample/finalize | configured adapter raises ordinary exception | existing best-effort warning/cache/fallback | primary runner result unchanged |
| successful session work item | valid `ok` response | attach metrics; keep session live | current success result |
| invalid session work item or terminal failure | non-ok, stop verdict, protocol failure | attach metrics; close/invalidate before return | current invalid result; later execute rejects |
| unexpected programming defect | outside named operational boundaries | propagate | traceback/defect evidence retained |

No new rescue path is planned. A proposal to catch, retry, time out, warn, or
clean up an additional failure is a behavior change, not refactor hardening.

## 12. Failure Modes Registry

| Refactor risk | How it could fail | Existing proof / required gate | User-visible impact if missed |
|---|---|---|---|
| priority becomes branch order | same-turn event chosen before reducer policy | 510 selector cases plus 56 adapter pairs | wrong timeout/result/cancel/limit verdict |
| deadline moves after stop | slow cleanup extends bounded drain | two fake-clock deadline tests and mutations | hang or late return |
| producer exit becomes terminal proof | result loses exit/outcome race | real pipe exit-then-outcome and leaked-sender tests | false worker-exit failure |
| channel parser is generalized | session JSON or `RunnerOutcome` validation weakens | wrong/malformed payload and real pipe tests | bad payload treated as result |
| one-shot cleanup moves out of order | live child, monitor, or handle leaks | owner cleanup and monitor tests | leaked process/resources |
| session success is invalidated | successful conversation closes | continuation test | persistent conversation breaks |
| invalid session remains live | second work starts after terminal verdict | invalidating-verdict test | work sent to poisoned worker |
| metrics/diagnostics are dropped | result construction detached from finalization | current monitor and protocol diagnostics tests | degraded observability |
| suppression is removed too early | negative or incomplete candidate becomes policy-clean | clean-review-before-removal sequence | hidden design regression |
| complexity is displaced | new helper receives C901 or dynamic dispatch | raw symbol scan plus clean review | lint passes without clarity gain |
| speculative mechanism lands | new state/retry/framework has no current owner need | mechanism-evidence review gate | brittle code and tests |

No row may finish with neither a firing proof nor an explicit scope stop.

## 13. Implementation Tasks

### Task 1: inventory current branches and proofs

Outcome: every current conditional in the two targets maps to an existing
firing test or one narrowly added characterization test.

Actions:

1. Add the nine exact characterization tests named in Section 10.2. They must
   fire the existing adapter branches directly and assert the current bounded
   `transport_failed` result; the two blocking-poll tests must prove that the
   stored failure is consumed on the next turn rather than inventing a retry or
   a new event. The response-read test must use the real owner read/poll path. The
   four deadline/refinement additions must prove the current live branch and
   exact absolute-deadline or bounded-join behavior without adding a new owner
   seam.
2. Walk both target functions against the completed Section 10.2 matrix and
   record the verification result in the Evidence Log. Do not defer the matrix
   design to implementation.
3. Run Section 10's baseline suite serially, including
   `tests/tasks/test_agent_execution.py`.
4. If another current live branch is unmapped, stop and amend this plan. Use the
   real owner seam named in Section 4; do not test a proposed helper.
5. Record every proposed mechanism in Section 9.4 before writing it. If it has
   no current evidence, remove it from the implementation design.

Verify:

```bash
./.venv/bin/python -m pytest -q -n 0 \
  tests/core/test_terminal_handoff.py tests/tasks/test_runner.py \
  tests/tasks/test_agent_execution.py
./.venv/bin/ruff check tests/core/test_terminal_handoff.py \
  tests/tasks/test_runner.py tests/tasks/test_agent_execution.py
```

### Task 2: refactor the one-shot host adapter

Outcome: `_run_one_shot_terminal_handoff` is a short, readable owner loop over
observe, reduce, apply, and finalize phases; every introduced helper is under
complexity 10 and current behavior is unchanged.

Actions:

1. Attempt one bounded owner-local decomposition using only the grounded
   non-monitor observation/channel and selected-effect seams in Sections 9.2
   and 9.4. Do not add a frame or shared driver.
2. Keep channel, process, monitor, diagnostics, and result construction in
   `host.py`; reuse the existing reducer and transport functions.
3. Preserve all five `RUFF-SUP-313` catches in their current visible phases and
   qualified symbol. A candidate that requires moving one stops for plan
   revision or owner-approved `RUFF-SUP-034` retention.
4. Run the one-shot rows in Section 10.2, full
   `tests/tasks/test_runner.py`, and full `tests/tasks/test_agent_execution.py`.
5. Run the host drain-deadline mutation proof from Section 10.4.
6. Ask a clean Python-expert subagent to review the cohesive source/test
   refactor before removing any suppression. The reviewer must answer:
   - net positive or net negative for logical locality and comprehensibility
   - whether each new mechanism addresses a current observed issue
   - whether complexity moved rather than fell
   - whether owner effects or ordering became harder to inspect
7. Resolve the review with one of the three per-adapter outcomes in Section
   15.2: land/close; one bounded concrete rework; or owner-approved retention.

Focused verification:

```bash
./.venv/bin/python -m pytest -q -n 0 tests/core/test_terminal_handoff.py \
  tests/tasks/test_runner.py tests/tasks/test_agent_execution.py
./.venv/bin/mypy weft/core/runners/host.py --config-file pyproject.toml
./.venv/bin/ruff check weft/core/runners/host.py \
  tests/tasks/test_runner.py
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/runners/host.py
./.venv/bin/ruff format --check weft/core/runners/host.py \
  tests/tasks/test_runner.py
git diff --check -- weft/core/runners/host.py tests/tasks/test_runner.py
```

Acceptance inspects the target and every new helper. The separate approved
`start_session` finding may remain.

### Task 3: refactor the persistent-session adapter

Outcome: `AgentSession.execute` is a short, readable owner loop over observe,
reduce, apply, and invalidate/return phases; every introduced helper is under
complexity 10 and current behavior is unchanged.

Actions:

1. Attempt one bounded owner-local decomposition using only the grounded
   observation, selected-effect/invalidation, and protocol-failure seams in
   Sections 9.3 and 9.4. Do not add a frame or shared driver.
2. Keep request/response parsing, process/monitor ownership, result construction,
   and invalidation in `sessions.py`.
3. Do not reuse host effect or result helpers. Reuse only the existing pure
   reducer/transport/protocol surfaces.
4. Run the session rows in Section 10.2 and full
   `tests/tasks/test_runner.py`.
5. Run the local invalidation mutation proof.
6. Ask a different clean Python-expert subagent to apply the same four review
   questions from Task 2 before any suppression removal.
7. Resolve the review with one of the three per-adapter outcomes in Section
   15.2: land/close; one bounded concrete rework; or owner-approved retention.

Focused verification:

```bash
./.venv/bin/python -m pytest -q -n 0 tests/core/test_terminal_handoff.py \
  tests/tasks/test_runner.py
./.venv/bin/mypy weft/core/tasks/sessions.py --config-file pyproject.toml
./.venv/bin/ruff check weft/core/tasks/sessions.py \
  tests/tasks/test_runner.py
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/tasks/sessions.py
./.venv/bin/ruff format --check weft/core/tasks/sessions.py \
  tests/tasks/test_runner.py
git diff --check -- weft/core/tasks/sessions.py tests/tasks/test_runner.py
```

Acceptance inspects the target and every new helper. The separate approved
`wait_ready` and `terminate` findings may remain.

### Task 4: reconcile adapter dispositions, policy, and traceability

Outcome: the source, human registry, raw inventory, generated index, policy
fixtures, plan metadata, and source-spec backlinks agree exactly.

Actions:

1. Record `removed` or `retained by owner` for each adapter. A retained group
   keeps its exact source directive, registry row, counts, raw inventory, and
   generated index.
2. Close only groups whose source refactor has a NET POSITIVE review. Apply the
   exact per-group deltas in Section 14.
3. Rewrite only the generated registry index with the repository tool.
4. Add the five source-spec backlinks for either removal or retention outcomes;
   they record this completed evaluation. Update mappings only if ownership
   actually moved.
5. Change this plan to `completed` only after both adapters have an explicit
   resolved disposition, every rework item is resolved, and the final reviewer
   passes. Zero, one, or two groups may remain.

### Task 5: final hardening and completed-work review

Outcome: a clean reviewer can trace every branch to the existing state machine,
owner effect, and firing proof without learning a new framework.

Actions:

1. Run all focused and repository gates in Section 18.
2. Ask a clean Python-expert reviewer, not used for Tasks 2 or 3, to inspect the
   complete diff and answer:
   - Did either adapter change behavior, precedence, cleanup, metrics,
     diagnostics, or session validity?
   - Is every helper a real owner phase rather than lint-driven fragmentation?
   - Does every introduced mechanism address an issue that exists in the
     baseline code, rather than a theoretical future concern?
   - Would reverting any new abstraction make the code easier to understand
     without restoring the complexity finding?
   - Are private tests behavioral rather than coupled to helper layout?
3. Resolve every blocking finding and obtain a new clean verdict.
4. Record final evidence, close the Deviation Log, update plan status/index,
   and commit the finished slice before calling it ready to land.

## 14. Suppression Reconciliation

Each group is one directive and one raw `C901` finding.

| Close slice | Group delta | Directive delta | Raw inventory delta |
|---|---:|---:|---:|
| remove host directive | -1 group (`RUFF-SUP-034`) | -1 total, -1 C901 | `C901=-1` |
| remove session directive | -1 group (`RUFF-SUP-048`) | -1 total, -1 C901 | `C901=-1` |
| both complete | -2 groups | -2 total, -2 C901 | `C901=-2` |
| retain either group by owner disposition | 0 | 0 | 0 |

For each close slice:

1. Remove the exact source `noqa` only after clean review.
2. Remove the matching human registry row.
3. Exclude the group from `EXPECTED_GROUP_IDS` and decrement the live expected
   group/directive/C901 counts by one. Recompute first if another effort landed.
4. Decrement only `C901` in the global raw-`noqa` inventory.
5. Run:

```bash
./.venv/bin/python bin/ruff_suppression_index.py --write
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_ruff_policy.py tests/specs/test_ruff_suppression_index.py
./.venv/bin/ruff check --extend-select RUF100 \
  weft/core/runners/host.py weft/core/tasks/sessions.py \
  tests/specs/test_ruff_policy.py
```

Do not create a red window where the source directive, human row, policy
counts, raw inventory, and generated index disagree. A candidate in bounded
rework retains its directive and row. An owner-approved retention restores the
production source to the approved baseline shape, archives the candidate's
diff/review identifier in the Evidence Log, and leaves all suppression policy
files unchanged for that group.

`RUFF-SUP-313` is fixed and unchanged in this plan: nine directives, nine raw
`BLE001` findings, the current human rationale, and the current qualified
symbols. Any required move is evidence for plan revision, not an authorized
part of this implementation.

## 15. Review Loop And Rework Queue

### 15.1 Draft-plan review

Before implementation, obtain:

1. a clean Codex/subagent review focused on zero-context executability,
   logical locality, and invented mechanisms
2. an outside-model review from a different model family focused on whether the
   plan overfits lint, overbuilds around hypothetical failures, or underprotects
   existing execution behavior

Both reviewers read this complete plan, the two target functions, the completed
reducer plan, the state-machine module, the source specs, and the named tests.
Every finding is reconciled and the reviewer reruns until NET POSITIVE/PASS.

The clean reviewer must explicitly produce a mechanism audit:

| Proposed mechanism | Baseline evidence | Needed now? | Simpler current alternative | Verdict |
|---|---|---|---|---|

An empty baseline-evidence cell is a blocking finding. “Could happen,” “more
robust,” “future adapter,” and “defensive” are not evidence for this refactor.

### 15.2 Per-refactor review

After each cohesive source refactor, use a fresh Python-expert subagent. The
reviewer compares baseline and candidate, not just candidate style, and returns
NET POSITIVE or NET NEGATIVE for logical locality and comprehensibility.

Resolve each adapter independently:

1. **NET POSITIVE:** land the source refactor and close only its group.
2. **NET NEGATIVE with one concrete bounded correction:** record the candidate
   and correction in the Rework Queue, keep the suppression active, make that
   correction without resetting the candidate, and send it to a different clean
   reviewer.
3. **Still NET NEGATIVE, prohibited mechanism required, or no bounded correction:**
   stop and request owner disposition. If the owner approves retention, archive
   the candidate diff/review identifier in the Evidence Log, restore the
   production source to its approved baseline behavior/structure, retain every
   suppression artifact, and mark that adapter resolved by retention.

This preserves the user's rework rule without creating endless churn. A first
negative candidate is queued and improved, not immediately discarded. A valid
[TS-3] suppression remains a legitimate final design when the bounded rework
cannot improve locality.

### 15.3 Rework Queue

| Candidate | Review verdict | Locality/comprehensibility defect | Invented mechanism, if any | Rework target | Status |
|---|---|---|---|---|---|
| host blob `4c3bd9cbe351575ba8302f7c179cd82d2aaf209e` (diff SHA-256 `a5a196738356ed7e8d79c533b36ad13a699258fc38318b51bf78ee4a8ac7a93b`) | NET NEGATIVE | Observation batch split around monitor code; 8-input/5-output effect boundary plus nested result builders; target remains C901 18 and aggregate target/helper complexity rises 42→47 | none ungrounded, but the extra seams exist primarily to route around fixed `RUFF-SUP-313` locality | none admissible under Sections 9.2 and 14 | resolved by owner-approved retention of `RUFF-SUP-034`; production source restored to baseline blob `b24d24ed960058fb347509d28811024d5de26915` |
| session blob `19afc4e2995109716d4bd1cec33a7f0932993822` (diff SHA-256 `6ee48bf8ea61d33497b2cd46801c614951e708fd37a2cf974f61689ffbd95c38`) | NET NEGATIVE | One-turn observation moved producer-exit, exit-code, deadline, clock, pending transport, reducer, limit, and cancellation state through a 9-input/4-output boundary; aggregate target/helper complexity rose 32→37 | none ungrounded; the owner-local seams are authorized, but this boundary reduced locality | keep producer-exit/join/exit-code and drain-expiry state in `execute`; narrow the observer to cancellation, timeout, response, and limit | resolved: bounded rework blob `95c5d57349245564bafc1d0c64f9c8a566537e34` received a different clean NET POSITIVE review; `RUFF-SUP-048` removed exactly |

The queue must be empty or every row marked resolved before completion.

## 16. Rollout, Rollback, And One-Way Doors

Rollout is two sequential adapter experiments and dispositions: host first,
session second. The host result informs review questions but is not a template
to copy into the session. Do not implement the two source refactors concurrently
or remove either suppression group in advance.

Rollback is a code-unit revert of the affected owner refactor and its exact
suppression-close delta. There is no persistence, queue, protocol, data, config,
or public migration. Existing characterization tests remain useful after a
rollback.

Reversibility: 5/5. Every change is private and behavior-preserving. The only
one-way risk is social: a generic framework can become a new extension point
once other code starts using it. This plan prevents that by rejecting generic
drivers and speculative state before landing.

Each slice lands by explicit file-list staging. Do not stage unrelated files.

## 17. Out Of Scope

- changing the terminal-handoff state/event/action table or either priority
  policy
- changing private transport framing, session JSON protocol, result payloads,
  timeout values, drain budget, public errors, diagnostics, or resource policy
- fixing `RUFF-SUP-035`, `RUFF-SUP-047`, `RUFF-SUP-049`, or any other C901
  finding in the two modules
- refactoring agent-session startup/readiness, close, or terminate
- refactoring command/subprocess runner handoff paths
- adding retries, extra joins, new catches, new warnings, or new cleanup phases
- a universal handoff runner, adapter plugin API, observer framework, or second
  state-machine library
- performance tuning; the current work is control-flow organization only
- broad cleanup of `host.py`, `sessions.py`, or `tests/tasks/test_runner.py`
- tests for theoretical schedules or failures not represented by a current
  branch, current spec requirement, reproduced defect, or introduced seam

If implementation discovers a real behavior defect, record a firing test and
stop for scope review. Do not smuggle a correctness change into this refactor.

## 18. Verification And Completion Gates

Focused gates after each slice:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q -n 0 tests/core/test_terminal_handoff.py \
  tests/tasks/test_runner.py tests/tasks/test_agent_execution.py
./.venv/bin/mypy weft/core/runners/host.py \
  weft/core/tasks/sessions.py --config-file pyproject.toml
./.venv/bin/ruff check weft/core/terminal_handoff.py \
  weft/core/runners/host.py weft/core/tasks/sessions.py \
  tests/core/test_terminal_handoff.py tests/tasks/test_runner.py \
  tests/tasks/test_agent_execution.py
./.venv/bin/ruff check --ignore-noqa --select C901 --output-format concise \
  weft/core/runners/host.py weft/core/tasks/sessions.py
./.venv/bin/ruff format --check weft/core/terminal_handoff.py \
  weft/core/runners/host.py weft/core/tasks/sessions.py \
  tests/core/test_terminal_handoff.py tests/tasks/test_runner.py \
  tests/tasks/test_agent_execution.py
./.venv/bin/python bin/ruff_suppression_index.py --check
git diff --check
```

Final repository gates:

```bash
. ./.envrc
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/python bin/pytest-pg --all
./.venv/bin/mypy weft bin integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/ruff check --extend-select RUF100 .
./.venv/bin/ruff format --check weft tests integrations/weft_django \
  extensions/weft_docker extensions/weft_macos_sandbox \
  extensions/weft_microsandbox
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python -m pytest -q -n 0 \
  tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
bin/check-dom15-fixtures
bin/check-doc-paths
../backstitch/.venv/bin/backstitch check --repo-root . --no-config \
  --spec-root docs/specifications --plan-root docs/plans \
  --code-root weft --code-root tests --code-root bin \
  --code-root integrations --code-root extensions --format json
uv lock --check
git diff --check
```

`bin/check-doc-paths` may reproduce only a separately established baseline of
unrelated claims; this plan may add none. Compare keyed findings, not just the
aggregate count.

The plan is complete only when:

- each adapter has one explicit resolved disposition: a NET POSITIVE refactor
  whose target/new helpers pass complexity 10, or owner-approved retention of
  its exact existing suppression after bounded rework
- every group selected for removal is removed exactly; every retained group and
  its counts/index remain exact
- existing terminal-handoff behavior and public output are unchanged
- each landed source refactor received a fresh NET POSITIVE Python-expert
  review; each retained adapter records the negative evidence and owner decision
- the final clean reviewer found no invented mechanism lacking baseline evidence
- the Rework Queue is empty or resolved
- every Deviation Log row is closed
- traceability and all final gates pass
- the finished slice is committed before it is called ready to land

## 19. Evidence Log

| Date | Evidence | Result |
|---|---|---|
| 2026-08-08 | Raw Ruff C901 scan of the two owner modules | Confirmed target scores 42 and 32; confirmed the three other module findings are separately owned. |
| 2026-08-08 | `tests/core/test_terminal_handoff.py` plus the named real-pipe, deadline, pair-policy, producer-exit, session-invalidation, and drain-expiry owner tests, serially | PASS at authoring baseline. |
| 2026-08-08 | Source/spec/history audit | Confirmed the named pure state machine already exists; remaining complexity is adapter-owned observation/effect/finalization, not an unnamed transition model. |
| 2026-08-08 | Clean draft-plan review, round 1 | BLOCKED: removed pre-authorized frames, shared refinement, and `RUFF-SUP-313` movement; added zero/one/two-group completion outcomes, the existing private routing-test exception, and the exact branch-to-proof matrix. |
| 2026-08-08 | Clean draft-plan review, round 2 | BLOCKED: removed the last conditional permission for monitor-catch movement and shared helpers, made zero-delta retention wording consistent, and named five characterization tests for existing unproved adapter branches. |
| 2026-08-08 | Clean draft-plan review, round 3 | NET POSITIVE: no speculative mechanism remains authorized; the five characterization additions target current observable branches without depending on proposed layout. |
| 2026-08-08 | Claude outside-model review | PASS: confirmed bounded owner-local attempts, valid zero/one/two-group outcomes, unchanged reducer and `RUFF-SUP-313` locality, and no generalized improvement mechanism. Added only a pointer to the existing narrow connection-fake pattern and explicit backlinks-on-retention wording. |
| 2026-08-08 | Authoring governance gates | Plan metadata/spec hygiene 8 passed; suppression checker and DOM-15 fixture checker passed; scoped whitespace checks passed. `check-doc-paths` reproduced the established eight unrelated dangling claims and found no claim from this plan. |
| 2026-08-08 | Live-baseline Task 1 branch coverage at `07c66fa29b5d3045b610ae0c0d04a11bdf202ab7` | Falsified the draft's five-test completeness claim. Four additional live owner branches lacked direct proof: host stop-after-drain deadline retention, host bounded seal refinement, session ordinary producer-exit drain, and session stop-after-drain deadline retention. The planned session response-read test also had to traverse the real lower read seam. Task 1 and Section 10.2 were amended to nine tests; unreachable adapter `wait`/unknown-action guards were classified explicitly. |
| 2026-08-08 | Task 1 nine-test characterization slice and serial branch coverage | PASS. The nine named tests fire the previously missing receive/type/poll, first-deadline, seal-refinement, and ordinary-drain branches. Current target-method branch gaps are limited to the explicitly classified unreachable `wait` and unknown-action guards. |
| 2026-08-08 | Task 1 deadline-reset fidelity mutation | PASS. Temporarily resetting the deadline on every later stop made both new stop-after-drain tests fail at fake time `0.30` versus the preserved `0.25`; restoring the production guards made both pass. |
| 2026-08-08 | Scoped Task 1 plan/test-delta review and F1 round 2 | PASS. F1 required the host seal-refinement test to prove exactly one drain-timeout join; the accepted fix counts that timeout while permitting distinct cleanup joins. The reviewer confirmed all nine tests are grounded and the unreachable-guard classification is sound. |
| 2026-08-08 | Host bounded candidate `4c3bd9cbe351575ba8302f7c179cd82d2aaf209e` | Focused suite, normal Ruff, mypy, format, and diff checks pass. Raw target C901 falls 42→18, but five new helpers score 10+3+6+6+4, so aggregate complexity rises to 47 and the target still cannot close `RUFF-SUP-034`. An attempted final-reap simplification caused two firing deadline tests to fail by invoking the patched stop seam twice; the original finalization branch was restored. |
| 2026-08-08 | Host drain-deadline review mutation | PASS. Moving first-deadline creation after the stop effect made `test_one_shot_stop_effect_cannot_reset_absolute_drain_deadline` fail at fake time `1.25` versus `1.0`; restoring the candidate made it pass. |
| 2026-08-08 | Host clean candidate review | NET NEGATIVE with no admissible bounded correction. Reviewer confirmed observation/effect locality worsened, complexity was displaced, and reaching 10 would require prohibited monitor/finalization movement, a frame/shared driver, or lint-oriented fragmentation. Owner disposition required before restoring baseline and retaining `RUFF-SUP-034`. |
| 2026-08-10 | Owner disposition: `RUFF-SUP-034` | `RETAIN`. The owner declared all five efforts implemented and authorized review, closure, and commit. Two independent clean reviews confirmed that the preserved host candidate reduced logical locality and raised aggregate target/helper complexity from 42 to 47 while still scoring 18. The production source was restored to baseline blob `b24d24ed960058fb347509d28811024d5de26915`; the nine grounded characterization tests remain. No suppression artifact changed. |
| 2026-08-10 | Session bounded candidate `19afc4e2995109716d4bd1cec33a7f0932993822` | Focused terminal/session suites, mypy, format, and diff checks passed. Raw target C901 fell 32→6 and every helper passed 10, but the helper boundary threaded 9 inputs and 4 outputs and aggregate target/helper complexity rose to 37. The invalid-result mutation made the timeout invalidation test fail before exact restoration. |
| 2026-08-10 | Session clean candidate review | NET NEGATIVE with one admissible bounded correction. The reviewer found behavior and cleanup faithful, but the wide observation boundary reduced locality. The queued correction keeps producer-exit and drain-deadline ownership in the loop and narrows the observer to cancellation, timeout, response, and limit. |
| 2026-08-10 | Session bounded rework `95c5d57349245564bafc1d0c64f9c8a566537e34` | Applied the one queued correction. `execute` scores 8; the observation helper scores 6 with 6 inputs/2 outputs; response/effect/result/protocol helpers score 7/5/7/4. The 810-case focused suite, mypy, format, and diff checks passed. No behavior, test, frame, shared helper, retry, protocol, warning, or cleanup mechanism was added. |
| 2026-08-10 | Session bounded-rework clean review | NET POSITIVE. The reviewer found the prior locality defect closed: live producer and deadline state remain visible in `execute`, while every helper maps to one current owner phase. Cancellation/timeout/response/limit order, first-deadline retention, bounded channel-seal refinement, metrics, cleanup, invalidation, and successful-session liveness remain exact. Aggregate McCabe remains 37 versus 32, but the owner loop falls to 8 with coherent phase boundaries rather than score-only fragments. |
| 2026-08-10 | `RUFF-SUP-048` reconciliation | Removed the exact source directive and human registry row, regenerated the source index, and updated the global inventory and policy fixture from 230/373/139 to 229 groups, 372 directives, and 138 C901 directives. Suppression checker, 84 policy/index tests, and scoped `RUF100` passed. `RUFF-SUP-034` and all other groups remain unchanged. |
| 2026-08-10 | Claude outside-model bounded-rework review | NET POSITIVE. The outside reviewer independently traced every baseline branch through the rework, confirmed behavior/cleanup equivalence and the 6-input/2-output observer correction, and found no frame, shared driver, new transition, retry, warning, or cleanup mechanism. It endorsed landing the rework and closing `RUFF-SUP-048` atomically. |
| 2026-08-10 | Isolated final verification | In a detached worktree containing only this closeout and the effort-3 documentation, the default suite passed 3,670 tests with 3 skips. The all-markers xdist run exposed three unrelated TaskMonitor maintenance timing failures; all three passed serially, and the slow complement passed with 1 test and 11 expected live-provider skips. A fresh PostgreSQL all-suite rerun passed 3,610 tests with 12 skips. Full configured mypy, Ruff, `RUF100`, formatter, suppression checker, 92 plan/spec/policy tests, DOM-15, lock, and whitespace checks passed. `check-doc-paths` reproduced only the established eight-item baseline. |
| 2026-08-10 | Isolated Backstitch keyed-baseline comparison | Compared findings by severity/code/path/line/message against untouched HEAD. Both scans retained the established 45 errors, 1,023 warnings, and 631 infos; the closeout added and removed zero keyed findings. |
| 2026-08-10 | Final clean closeout review | PASS / NET POSITIVE. The reviewer confirmed exact host retention, session locality and behavior, atomic `RUFF-SUP-048` removal, effort-3 zero-source-delta retention, declared mapping/merge order, grounded tests, coherent traceability, and sufficient isolated verification. The all-markers xdist caveat is recorded as aggregate host-load contention without claiming it is pre-existing; no TaskMonitor surface changed. |

Closeout recorded focused test output, mutation proof, per-refactor clean
reviews, exact suppression reconciliation, isolated repository gates,
traceability, and final review. No transient worktree state is part of the
plan record.

## 20. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

Every row must be closed before completion. A behavior or contract deviation
requires Class 5 replanning and an exact spec proposal; it is not closed by a
code comment or test alone.
