<!-- /autoplan restore point: /Users/van/.gstack/projects/VanL-weft/main-autoplan-restore-20260801-082611.md -->

# Terminal Handoff Reducer Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-1.1], [CC-3], [CC-3.4], [CC-3.5]; docs/specifications/06-Resource_Management.md [RM-5.2]; docs/specifications/07-System_Invariants.md [REDUCER.1]-[REDUCER.8], [EXEC.1]-[EXEC.10]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1]; docs/specifications/10-CLI_Interface.md [CLI-1.1.1]; docs/specifications/13-Agent_Runtime.md [AR-6]
Superseded by: none

Class: 5. This changes the private execution/result protocol and the normative
rules that select public terminal outcomes. It crosses process, timeout,
resource-limit, cancellation, testing, and specification boundaries. The full
risky-change hardening and independent-review loops are mandatory.

## 1. Goal

Make Weft's response to result/liveness ordering deterministic without claiming
that OS scheduling, process exit, IPC visibility, cancellation, or timeout races
can be made deterministic.

The implementation will introduce one pure **terminal handoff reducer** for the
built-in host runner's private result channels. Producer liveness, response
channel state, timeout, cancellation, limit, and payload observations become
typed reducer events. The runner owner applies only the reducer's selected
action. Producer exit is never treated as proof that no result exists.

The first release gate is non-negotiable: the two red CLI regressions already in
`tests/cli/test_cli_run.py` must turn green through the reducer-backed host
runner path:

1. `test_cli_run_fast_standard_library_function`
2. `test_cli_run_stored_spec_preserves_fast_function_result`

A longer sleep, a retry loop around `get_nowait()`, a larger CLI timeout, a
special case for `json:dumps`, or a delay added to the target does not satisfy
that gate.

The second release gate is equally hard: every declared reducer state/event
cell, including invalid cells and the terminal state's cells, must have one
firing table-driven test.

## 2. Confirmed Premises

The user confirmed these premises before this plan was written:

1. Scheduling nondeterminism is inherent and remains in scope as an input.
2. The defect is indeterminate response policy, not the existence of races.
3. The response policy should be modeled as a state machine and implemented as
   a pure reducer.
4. State-machine tests must cover the full state/event table.
5. The current full suite passing means the plan must audit adjacent result and
   completion transports, not only patch the reported call shape.
6. The existing red tests must become green because production routes the
   observations through the reducer.

No additional product premise is assumed. This is a correctness change to an
existing CLI and runner contract, not a new execution feature.

## 3. Source Documents and Required Reading

Governing specifications:

- `docs/specifications/01-Core_Components.md` [CC-1.1], [CC-3], [CC-3.4]
- `docs/specifications/06-Resource_Management.md` [RM-5.2]
- `docs/specifications/07-System_Invariants.md` [REDUCER.1]-[REDUCER.4],
  [EXEC.1]-[EXEC.4]
- `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1]
- `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]
- `docs/specifications/13-Agent_Runtime.md` [AR-6]

Required implementation reading:

- `weft/core/state_machines.py`
- `weft/core/runners/host.py`, especially `_worker_entry`,
  `_agent_session_worker_entry`, `_put_terminal_mp_queue`, and
  `HostTaskRunner.run_with_hooks`
- `weft/core/tasks/sessions.py`, especially `AgentSession.wait_ready`,
  `AgentSession.execute`, `terminate`, and `close`
- `weft/core/runners/subprocess_runner.py`, especially its process-exit plus
  stdout/stderr EOF drain
- `weft/core/tasks/agent_session_protocol.py`
- `weft/core/tasks/consumer.py` outcome-to-lifecycle mapping
- `tests/core/test_state_machines.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_agent_execution.py`
- `tests/cli/test_cli_run.py`
- new `tests/cli/test_cli_run_installed_entrypoint.py`

Planning and hardening guidance:

- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/lessons.md`
- `docs/lessons.md`

Historical context, not authority:

- `docs/plans/2026-05-29-reliability-and-doc-fixes-plan.md`, Slice 2. That
  slice deliberately caught only results already visible at the timeout check
  and explicitly deferred results still in multiprocessing transit.
- `docs/plans/2026-05-08-agent-session-and-task-startup-observability-plan.md`.
  Its startup path is a positive example because it joins and drains after
  observing child death.
- `docs/plans/2026-05-13-internal-state-machine-helper-plan.md`.

## 4. Current System and Failure Class

### 4.1 Observed failure

The current non-command host path uses a spawned worker and a
`multiprocessing.Queue`:

```text
child target
    -> result_queue.put(RunnerOutcome)
    -> child queue feeder / pipe visibility
    -> process exit

parent loop
    -> while process.is_alive(): queue.get(timeout=...)
    -> observe process dead
    -> join
    -> one result_queue.get_nowait()
    -> "Worker produced no result" if empty at that instant
```

The invalid inference is the last step. The parent observes one source of
evidence, producer liveness, and treats it as a conclusion about a different
source, result transport visibility. The fast function gives a small reliable
reproduction of that protocol ambiguity. It does not prove that import speed or
queue-feeder lag is the low-level cause.

The exact low-level contributor to the reported macOS behavior may include
multiprocessing feeder timing, spawn bootstrap, or transport teardown. That
uncertainty does not weaken the protocol diagnosis. `EXITED + empty-now` is not
proof of `no terminal payload` on this transport.

### 4.2 Adjacent path audit

| Surface | Producer/channel | Current response rule | Plan action |
|---|---|---|---|
| One-shot host function/agent | spawned process -> multiprocessing response queue | process exit then one nonblocking read | migrate to sealable response channel and reducer |
| Persistent agent `execute` | spawned session -> multiprocessing response queue | loop only while alive; no post-exit result drain | migrate response channel and use the same reducer |
| Persistent agent startup | spawned session -> private startup responses | join and late drain after death | preserve as a positive control; adapt transport only as required |
| One-shot command output | subprocess pipes -> reader threads -> bounded queues | process exit plus both EOF sentinels and final drain | keep; add/retain fast-tail characterization |
| Interactive command output | subprocess pipes -> reader threads -> bounded queues | drain until stdout/stderr closed | keep; add/retain immediate-exit tail characterization |
| BaseTask worker lanes | thread -> bounded `queue.Queue` | synchronous put precedes worker deregistration | keep; existing ownership rule is sound |
| Public task outbox/result wait | durable broker queues | lifecycle evidence plus completion grace and final drain | out of this private runner slice |
| Manager child-launch evidence | thread/process plus durable lifecycle events | separate manager reconciliation contract | audit note only; not the same private terminal channel |

The audit is classification work, not permission to rewrite every asynchronous
path. Only paths that infer payload absence from producer death without a seal
or drain proof enter the reducer migration.

### 4.3 Existing code to reuse

| Subproblem | Existing code | Required use |
|---|---|---|
| Pure state selection | `weft/core/state_machines.py` | reuse `StateMachine`, `Transition`, and `StateDecision`; no second framework |
| Lifecycle mutation | `Consumer` and `TaskSpec` lifecycle methods | keep outside the reducer |
| Process stop/reap | `HostTaskRunner._stop_process`, `AgentSession.terminate` | remain adapter-owned effects |
| Resource observations | resource monitor APIs | emit reducer events; do not move monitor I/O into reducer |
| Command stream completion | process plus stdout/stderr EOF logic | use as a positive protocol pattern, not shared code |
| Runner diagnostics | `weft/core/runner_diagnostics.py` | include bounded terminal-handoff failure context |

## 5. Alternatives and Decision

### Approach A: post-exit sleep and repeated queue reads

- Effort: small.
- Benefit: likely turns the current repro green.
- Defect: time is used as a proxy for channel closure. A slower machine or
  larger payload recreates the same ambiguity. It also gives no common policy
  for session execution, timeout, cancellation, and limit races.
- Decision: rejected.

### Approach B: reducer over the existing multiprocessing queue

- Effort: medium.
- Benefit: centralizes response policy and can bound all terminal paths.
- Defect: a normal `multiprocessing.Queue` receiver has no clean per-producer
  EOF while the parent still owns queue endpoints. A drain deadline would be
  the ordinary correctness mechanism rather than a safety backstop.
- Decision: rejected as the final architecture. It may be used only as a
  temporary diagnostic spike that is removed before the green gate.

### Approach C: reducer plus a sealable private response channel

- Effort: medium.
- Benefit: one-way `multiprocessing.Pipe(duplex=False)` gives ordered payloads,
  synchronous serialization failure, and receiver-visible EOF after the child
  sender closes. The reducer can distinguish `outcome`, `producer exited`,
  `channel sealed`, `transport failed`, and stop intents without guessing from
  time alone.
- Cost: the private host/session response transport changes, so endpoint
  ownership and cross-platform cleanup need direct tests.
- Decision: chosen. It is the smallest complete fix for both vulnerable host
  result paths and reuses the existing state-machine helper.

No public TaskSpec, queue, runner-plugin, CLI flag, exit-code, or result schema
changes. The private agent-session JSON payloads remain versioned and retain
their current shapes; only their carrier changes.

## 6. Target Domain Model

### 6.1 Canonical terms

- **Producer**: the child process that executes one work item or owns one
  persistent session.
- **Terminal payload**: the one private message that contains the work-item
  outcome used to select lifecycle state.
- **Terminal channel**: the private one-producer response transport carrying
  terminal payloads and, for a persistent session, its startup protocol.
- **Channel seal**: receiver-visible proof that this producer can send no more
  payloads. For the selected host transport this is EOF after all unused sender
  endpoints are closed.
- **Stop intent**: the first accepted timeout, cancellation, or confirmed limit
  event for the work item.
- **Drain**: continued observation after producer exit or stop intent until a
  terminal payload, channel seal, transport failure, or internal drain deadline.
- **Protocol failure**: the channel sealed, failed, or exhausted its internal
  drain budget without a terminal payload and without a stronger accepted stop
  intent.

These terms will live in the canonical spec section when promoted. A separate
root `CONTEXT.md` is not created because this repository keeps normative domain
language in `docs/specifications/`.

### 6.2 Ownership diagram

```text
OS / monitor / control observations
          |
          v
host.py or sessions.py adapter
  - poll response connection
  - observe process transition
  - observe timeout/cancel/limit
  - own clock, process handles, monitor, cleanup
          |
          | typed event
          v
weft/core/terminal_handoff.py
  - pure StateMachine table
  - no process, connection, queue, clock, sleep, log, or cleanup I/O
          |
          | StateDecision(state, action, transition_id, reason)
          v
existing adapter owner
  - stop/reap/drain
  - attach metrics/diagnostics
  - return exactly one RunnerOutcome/SessionExecutionResult
          |
          v
Consumer lifecycle and durable queue publication
```

### 6.3 States, events, and actions

States:

- `observing`: producer and channel may still produce a terminal payload.
- `draining`: producer exit was observed before a terminal payload or channel
  seal.
- `stopping_timeout`: timeout is the first accepted stop intent.
- `stopping_cancel`: cancellation is the first accepted stop intent.
- `stopping_limit`: a confirmed limit violation is the first accepted stop
  intent.
- `decided`: one terminal classification has been selected. It is terminal and
  has no legal outgoing event.

Events:

- `outcome_received`
- `producer_exited`
- `channel_sealed`
- `timeout_requested`
- `cancel_requested`
- `limit_reached`
- `drain_expired`
- `transport_failed`

Actions:

- `return_outcome`
- `begin_drain`
- `stop_for_timeout`
- `stop_for_cancel`
- `stop_for_limit`
- `return_timeout`
- `return_cancelled`
- `return_limit`
- `return_protocol_failure`
- `wait`

### 6.4 Complete transition matrix

This matrix is normative after promotion. Each cell means
`next-state / action`. `INVALID` means the reducer must reject the event as a
programming error. `FIRST` means the already accepted stop intent remains in
force.

| Current state | outcome | producer exited | channel sealed | timeout | cancel | limit | drain expired | transport failed |
|---|---|---|---|---|---|---|---|---|
| `observing` | `decided / return_outcome` | `draining / begin_drain` | `decided / return_protocol_failure` | `stopping_timeout / stop_for_timeout` | `stopping_cancel / stop_for_cancel` | `stopping_limit / stop_for_limit` | `INVALID` | `decided / return_protocol_failure` |
| `draining` | `decided / return_outcome` | `draining / wait` | `decided / return_protocol_failure` | `stopping_timeout / stop_for_timeout` | `stopping_cancel / stop_for_cancel` | `stopping_limit / stop_for_limit` | `decided / return_protocol_failure` | `decided / return_protocol_failure` |
| `stopping_timeout` | `decided / return_timeout` | `stopping_timeout / begin_drain` | `decided / return_timeout` | `stopping_timeout / wait` | `stopping_timeout / wait (FIRST)` | `stopping_timeout / wait (FIRST)` | `decided / return_timeout` | `decided / return_timeout` |
| `stopping_cancel` | `decided / return_cancelled` | `stopping_cancel / begin_drain` | `decided / return_cancelled` | `stopping_cancel / wait (FIRST)` | `stopping_cancel / wait` | `stopping_cancel / wait (FIRST)` | `decided / return_cancelled` | `decided / return_cancelled` |
| `stopping_limit` | `decided / return_limit` | `stopping_limit / begin_drain` | `decided / return_limit` | `stopping_limit / wait (FIRST)` | `stopping_limit / wait (FIRST)` | `stopping_limit / wait` | `decided / return_limit` | `decided / return_limit` |
| `decided` | `INVALID` | `INVALID` | `INVALID` | `INVALID` | `INVALID` | `INVALID` | `INVALID` | `INVALID` |

Key precedence rules:

1. `producer_exited` and `outcome_received` commute for classification. Either
   observation order returns the outcome.
2. Each adapter has one explicit compatibility policy for observations gathered
   in the same turn. One-shot host order is cancellation, a ready terminal
   payload, timeout, a confirmed resource limit, transport failure, channel
   seal, producer exit, then drain expiry. Persistent-session order is
   cancellation, timeout, a ready terminal payload, a confirmed resource limit,
   transport failure, channel seal, producer exit, then drain expiry. Reading
   the channel to distinguish a payload from EOF does not itself reduce an
   event; the adapter buffers that observation and emits the highest-priority
   eligible event. The policies preserve each current boundary instead of
   silently unifying their deadline semantics.
3. Once cancellation or a confirmed limit is the accepted stop intent, it wins
   over a later terminal payload. An outcome already reduced to `decided`
   remains final.
4. After a stop state is entered, the first accepted intent wins. The declared
   adapter policy, rather than incidental branch order, selects that first
   intent.
5. Stop observations and `producer_exited` are edge-triggered for one handoff.
   After any is reduced once, the adapter excludes it from later selector input.
   After a stop is accepted, all stop-intent observations are ineligible. This
   prevents persistent callbacks, expired clocks, confirmed limits, or dead
   producer state from starving channel seal, outcome, or `drain_expired`.
6. `decided` is not an idempotent sink. The adapter must stop reducing. A later
   reducer call is a programming error, which makes duplicate terminal
   classification visible in tests.

### 6.5 Boundedness

The adapter owns a monotonic internal drain deadline whenever it enters
`draining` or a stopping state. Add a named internal constant in
`weft/_constants.py`; the starting proposal is
`TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS = 0.25`, aligned with existing bounded
subprocess stream drains.

The bound is a deadlock/leaked-endpoint safety net, not the ordinary proof that
no message remains. Normal completion uses terminal payload or EOF. Tests drive
deadline events with fixed evidence or a fake clock; they do not sleep for the
constant.

`spec.timeout=None` may still permit a live target to run indefinitely. It must
not permit indefinite waiting after producer exit, transport failure, or an
accepted stop intent.

## 7. Spec Baseline

- Baseline commit: `c75a930ba30a89478487975fadc2302a5aa7fa57`.
- Behavioral spec baseline:
  `docs/specifications/01-Core_Components.md`,
  `docs/specifications/06-Resource_Management.md`,
  `docs/specifications/07-System_Invariants.md`,
  `docs/specifications/08-Testing_Strategy.md`,
  `docs/specifications/10-CLI_Interface.md`, and
  `docs/specifications/13-Agent_Runtime.md` at that commit.
- Test baseline: the current worktree adds the two intentionally red tests in
  `tests/cli/test_cli_run.py`. They are part of the acceptance baseline even
  though they are not in the baseline commit.
- Plan type: implementation with material spec revision.
- Promotion baseline identifier: `c75a930ba30a89478487975fadc2302a5aa7fa57`.
  Spec promotion began from that exact HEAD; the plan, plan index, and two red
  acceptance tests were already present as uncommitted worktree additions.

## 8. Proposed Spec Delta

Task 1 promoted this section into the named specification files on 2026-08-04.
The canonical specifications are normative; the retained text below is the
implementation audit record.

Promotion strategy: **A, in-file requirement text before implementation-link
claims**. Promote the behavior and plan backlinks first. Update implementation
mappings only in the slice that lands the reducer and adapters, so the specs do
not claim code that does not exist.

| Spec file | Strategy | Sections touched |
|---|---|---|
| `docs/specifications/01-Core_Components.md` | A | new [CC-3.5] after [CC-3.4] |
| `docs/specifications/06-Resource_Management.md` | A | [RM-5.2] |
| `docs/specifications/07-System_Invariants.md` | A | [REDUCER.5]-[REDUCER.8], [EXEC.5]-[EXEC.10] |
| `docs/specifications/08-Testing_Strategy.md` | A | [TS-0], [TS-1] |
| `docs/specifications/10-CLI_Interface.md` | A | [CLI-1.1.1] |
| `docs/specifications/13-Agent_Runtime.md` | A | [AR-6] |

### `docs/specifications/01-Core_Components.md` [CC-3.5]

Insert after [CC-3.4]:

> ### 3.5 Private Terminal Handoff Reducer [CC-3.5]
>
> Host-runner work completion is reduced from independent producer and private
> response-channel observations. Producer liveness is not result-channel
> state: observing a producer exit while the response channel is currently
> empty starts terminal drain and must not by itself produce a no-result
> failure.
>
> The terminal handoff reducer is pure. Its states are `observing`, `draining`,
> `stopping_timeout`, `stopping_cancel`, `stopping_limit`, and terminal
> `decided`. Its events are `outcome_received`, `producer_exited`,
> `channel_sealed`, `timeout_requested`, `cancel_requested`, `limit_reached`,
> `drain_expired`, and `transport_failed`. The host runner and session adapter
> own process, channel, clock, monitor, and cleanup I/O and apply only the
> reducer's selected action.
>
> A terminal classification requires one of: a valid terminal payload; a
> receiver-visible channel seal; an explicit transport failure; or expiration
> of the named internal drain deadline. The drain deadline is a bounded safety
> net for a leaked or failed private channel, not a delay used to guess whether
> a normal result will become visible.
> A receiver decode/unpickle failure or a decoded object of the wrong terminal
> payload type is `transport_failed`; it is never `outcome_received`.
>
> The transition and precedence contract is:
>
> | Current state | outcome | producer exited | channel sealed | timeout | cancel | limit | drain expired | transport failed |
> |---|---|---|---|---|---|---|---|---|
> | `observing` | `decided/outcome` | `draining/drain` | `decided/protocol_failure` | `stopping_timeout/stop` | `stopping_cancel/stop` | `stopping_limit/stop` | invalid | `decided/protocol_failure` |
> | `draining` | `decided/outcome` | `draining/wait` | `decided/protocol_failure` | `stopping_timeout/stop` | `stopping_cancel/stop` | `stopping_limit/stop` | `decided/protocol_failure` | `decided/protocol_failure` |
> | `stopping_timeout` | `decided/timeout` | `stopping_timeout/drain` | `decided/timeout` | `stopping_timeout/wait` | `stopping_timeout/wait` | `stopping_timeout/wait` | `decided/timeout` | `decided/timeout` |
> | `stopping_cancel` | `decided/cancelled` | `stopping_cancel/drain` | `decided/cancelled` | `stopping_cancel/wait` | `stopping_cancel/wait` | `stopping_cancel/wait` | `decided/cancelled` | `decided/cancelled` |
> | `stopping_limit` | `decided/limit` | `stopping_limit/drain` | `decided/limit` | `stopping_limit/wait` | `stopping_limit/wait` | `stopping_limit/wait` | `decided/limit` | `decided/limit` |
> | `decided` | invalid | invalid | invalid | invalid | invalid | invalid | invalid | invalid |
>
> Outcome and producer-exit observation order must converge on the outcome.
> The one-shot adapter's same-turn order is cancellation, a ready terminal
> payload, timeout, a confirmed resource limit, transport failure, channel seal,
> producer exit, then drain expiry. The persistent-session adapter preserves its
> existing deadline boundary with cancellation, timeout, a ready terminal
> payload, confirmed limit, transport failure, channel seal, producer exit, then
> drain expiry. Reading the channel to distinguish a payload from EOF may buffer
> an observation, but only the policy's highest-priority eligible event is
> reduced in that turn.
> Once timeout, cancellation, or a confirmed limit is accepted, that stop
> intent wins over a later payload. The first accepted stop intent remains in
> force. Stop intents and `producer_exited` are edge-triggered: after one is
> reduced it is excluded from later selector input, and an accepted stop makes
> every stop-intent observation ineligible. Exactly one transition may select a
> terminal action.
>
> The built-in host implementation uses a private response transport that can
> provide ordered payload delivery and receiver-visible seal/EOF. This is not a
> public queue or runner-plugin protocol.

Do not add the implementation mapping in Task 1. Task 6 adds
`weft/core/terminal_handoff.py`, `weft/core/runners/host.py`, and
`weft/core/tasks/sessions.py` after those owners exist.

### `docs/specifications/06-Resource_Management.md` [RM-5.2]

Append after the current timeout behavior bullets:

> Before emitting `timeout_requested` in a host-runner observation turn, the
> one-shot adapter consumes a terminal payload that is already ready on the
> private response channel. The persistent-session adapter preserves its current
> boundary by accepting a due timeout before a same-turn ready payload. Once the
> reducer accepts timeout, timeout remains the terminal outcome while the
> adapter stops, drains, and cleans up; a later payload does not replace it. In
> both policies cancellation is first, confirmed resource limit follows both
> timeout and payload, and the first accepted stop remains final.

### `docs/specifications/07-System_Invariants.md`

Append to Deterministic Reducer Helper Invariants:

> - **REDUCER.5**: the terminal handoff reducer has one explicit expected row
>   for every state/event Cartesian-product cell. Valid, no-op, and invalid
>   cells are all contract, and every cell has a firing table test.
> - **REDUCER.6**: producer lifecycle and terminal-channel lifecycle are
>   independent reducer evidence. Producer exit alone cannot classify a
>   missing terminal payload.
> - **REDUCER.7**: terminal handoff reaches exactly one terminal action. Events
>   reduced after `decided` are programming errors rather than a second verdict.
> - **REDUCER.8**: adapter clocks, process probes, channel reads, monitor checks,
>   and cleanup effects stay outside the terminal handoff reducer.

Append to Execution Invariants:

> - **EXEC.5**: host function, one-shot agent, and persistent agent work-item
>   results use ordered private response delivery with receiver-visible seal or
>   an explicit bounded failure path.
> - **EXEC.6**: observing producer exit while no result is currently visible
>   enters terminal drain; it is not no-result proof.
> - **EXEC.7**: a terminal handoff drain is bounded independently of
>   `spec.timeout`, including when the producer has already exited.
> - **EXEC.8**: private terminal transport or serialization failures become a
>   bounded error outcome with runner diagnostics; they do not hang or disappear.
>   Receiver decode failure and wrong terminal payload type are transport
>   failures and cannot be reduced as outcomes.
> - **EXEC.9**: before a one-shot adapter returns any terminal handoff decision,
>   it stops a still-live producer when required, reaps it, stops its monitor,
>   and closes all private endpoints under existing bounded cleanup rules. A
>   persistent-session timeout, cancellation, limit, protocol failure, or
>   non-ok work-item result whose worker contract ends the session invalidates
>   and cleans up that session before returning. A second execute on an invalid
>   session rejects as closed. Normal successful work-item results keep the
>   session live.
> - **EXEC.10**: stop-intent observations and producer exit are edge-triggered
>   within one terminal handoff. After one is reduced, the adapter excludes it
>   from later selector input; an accepted stop excludes every later stop
>   intent. Persistent level signals cannot starve outcome, channel seal,
>   transport failure, or drain expiry.

### `docs/specifications/08-Testing_Strategy.md` [TS-0], [TS-1]

Append to [TS-0] after the current property-test bullets:

> The terminal handoff reducer requires exhaustive table-driven tests. Its test
> table must equal the Cartesian product of all declared states and event kinds,
> contain no duplicate cells, and assert the exact next state, action, and
> transition ID for valid cells plus the exact rejection for invalid cells.
> Every selected reason must be non-empty. Structural reachability and aggregate
> coverage helpers do not replace this cell-by-cell proof.
>
> The terminal handoff same-turn selector has one strict order per declared
> adapter policy across all eight event kinds. Its table tests cover every
> non-empty observation subset under both policies, 510 cases, and each host
> adapter routes all 28 unordered event pairs through its declared policy.
> Expected priorities are independent test data, not values derived from the
> production priority table. Multi-turn cases prove already-reduced stop and
> producer-exit level signals cannot starve outcome, seal, or drain expiry.

After Tasks 2-5 land the named tests, Task 6 appends to [TS-1]'s
`tests/core/` bullet:

> Terminal handoff coverage pairs the full pure state/event table with real
> spawn/IPC examples. It includes both `outcome -> exit` and `exit -> outcome`,
> channel seal without outcome, transport/serialization failure, timeout/result
> orderings, every non-empty same-turn observation subset, abrupt child exit,
> persistent session error-then-exit, and the public CLI fast-function and
> stored-spec regressions. Installed-workflow coverage invokes the environment's
> `weft` console script from a fresh initialized external directory with no
> test-added `PYTHONPATH`; it covers a standard-library function, a local module
> before and after manager reuse, a stored spec, and no-wait/result collection.
> Preloaded queues, target sleeps, retry-only assertions, and `python -m` test
> adapters are not substitutes for those paths.

### `docs/specifications/10-CLI_Interface.md` [CLI-1.1.1]

Append to the `run` failure behavior:

> Private worker-result protocol failures are rendered in user terms. Plain
> output and the `error` field of `weft run --json` use one of these bounded
> categories: `Worker exited before returning a result (exit code N)` only when
> producer exit and a numeric exit code are observed without a result; `Worker
> result channel failed before a result was received` when EOF occurs while the
> producer is live or exit is unproved, delivery or decoding fails, the payload
> type is invalid, or bounded drain expires; and `Task returned a value that
> Weft could not serialize:
> <bounded cause>` when result serialization fails before any frame is written.
> Reducer state, event, transition ID, channel seal, and drain-budget details are
> private diagnostics and do not appear in `weft run` output.
>
> Each category is an ordinary task `failed` result and `weft run` exits 1.
> Timeout remains status `timeout` with exit 124. Existing cancellation,
> resource-limit, target-exception, and success rendering is unchanged. Success
> JSON remains `{tid,status,result}` and failure JSON remains
> `{tid,status,error}`; terminal-handoff diagnostic fields do not expand this
> command result schema.

### `docs/specifications/13-Agent_Runtime.md` [AR-6]

Append after the private protocol paragraph:

> Persistent session work-item completion uses the same host terminal handoff
> reducer as one-shot host work. A session child may publish an error result and
> exit immediately; the parent must return that result rather than replace it
> with a generic unexpected-exit error. Session startup remains a distinct
> readiness protocol, but its response transport must preserve ordered delivery,
> terminal serialization failure, and receiver-visible seal when the child exits.
> For observations gathered in the deadline turn, cancellation is selected
> first and timeout is selected before a ready result, preserving the existing
> persistent-session deadline boundary. Accepted stop and producer-exit signals
> are consumed once so their persistent level state cannot starve bounded drain.

## 9. Invariants and Constraints

Preserve:

- queues remain the only durable task lifecycle and result source of truth
- spawn context; no broker connection crosses process creation
- existing `RunnerOutcome` and `SessionExecutionResult` semantics
- existing public success, target-error, timeout, cancellation, and limit
  output/exit codes; private protocol failures use §10's bounded categories
- existing agent-session private JSON payload shapes and protocol version
- Consumer ownership of lifecycle mutation, outbox publication, and reserved
  policy
- current one-shot same-turn precedence made explicit as cancellation, ready
  outcome, timeout, then confirmed limit; current persistent-session precedence
  remains cancellation, timeout, ready outcome, then confirmed limit
- command/subprocess stream EOF behavior
- no task timeout added when `spec.timeout` is `None`

Required new constraints:

- use `weft/core/state_machines.py`; add no state-machine dependency
- reducer inputs are typed observations, never live process/connection objects
- reducer code does no I/O, sleeping, logging, time reads, mutation, or cleanup
- the parent closes its unused sender endpoint immediately after spawn; the
  child closes its sender in `finally`; otherwise EOF is not trustworthy
- child terminal serialization is synchronous and occurs before the first
  channel write with `multiprocessing.reduction.ForkingPickler`, matching the
  spawn transport's serializer. A pre-write value serialization failure may
  send one pre-serialized bounded error outcome. A `send_bytes`/OS error after
  writing starts never retries on that connection; the child closes it and the
  parent classifies transport failure or EOF
- the parent uses `recv_bytes`, decodes with the matching multiprocessing
  serializer, and validates the terminal payload type before emitting
  `outcome_received`. Decode/unpickle failure or a wrong payload type emits
  `transport_failed`, records bounded type/exception context, and follows the
  ordinary cleanup path
- no adapter returns directly from a raw `process.is_alive()`/empty-channel
  conjunction
- successful paths do not emit extra durable lifecycle events

Endpoint ownership is explicit:

| Phase | Parent owns | Child owns | Required cleanup |
|---|---|---|---|
| before `Process.start()` | response receiver and sender; session request queue when applicable | nothing | a start exception closes both response endpoints and every created session queue |
| after successful start | response receiver; session request sender/queue | response sender; session request receiver/queue | parent closes its sender copy immediately; child closes its sender in `finally` |
| terminal or exceptional parent path | resources only until cleanup completes | sender only while still live | parent closes receiver on every path; cleanup stops/reaps as required and closes session IPC |

Rollback is a code-unit revert: restore queue-based host/session response
transport, remove the reducer module/tests, and retain all red and
characterization tests. There is no database migration, persisted schema,
public protocol migration, feature flag, or mixed-version wire compatibility
requirement.

Reversibility: 4/5. The private carrier is easy to revert, but the promoted
behavioral contract should not be weakened after release.

## 10. Error and Rescue Registry

Public rendering and private diagnostics are separate contracts:

| Failure category | Plain and JSON `error` | Status / exit | Private bounded diagnostics only |
|---|---|---|---|
| producer exit plus numeric exit code, no result | `Worker exited before returning a result (exit code N)` | `failed` / 1 | reducer state/event/transition, PID, exit code, channel state, drain bound |
| live/unproved-producer EOF, delivery/decode/type failure, or drain expiry | `Worker result channel failed before a result was received` | `failed` / 1 | bounded OS/decode/type cause, endpoint state, reducer fields, PID, exit code when known, drain bound |
| pre-write result serialization fails | `Task returned a value that Weft could not serialize: <bounded cause>` | `failed` / 1 | serializer and exception type; no arbitrary value dump |
| timeout | existing timeout text | `timeout` / 124 | terminal-handoff fields may augment task diagnostics only |
| cancel, kill, or limit | existing text | existing status / exit | terminal-handoff fields may augment task diagnostics only |

Reducer vocabulary such as `terminal handoff`, `channel_sealed`, `draining`, and
transition IDs never appears in default output or the `weft run --json` result.

| Codepath | Failure | Boundary/error | Required response | Public result |
|---|---|---|---|---|
| child pre-write serialization | terminal envelope/value cannot be serialized with `ForkingPickler` | serialization exception before any frame write | pre-serialize and send one small pre-serialized error outcome, then close | serialization category above |
| child transport write | `send_bytes` fails after write begins | `OSError`/broken connection | do not retry the framed stream; close sender | transport category above |
| parent response decode | bytes cannot be unpickled or decoded object has wrong terminal type | decode/type error -> `transport_failed` | never emit `outcome_received`; bounded cleanup and diagnostics | transport category above |
| parent response receive | channel closes without payload | `EOFError` -> `channel_sealed` | reducer selects protocol failure unless stop intent owns verdict | worker-exited category only with observed numeric exit; otherwise transport category |
| parent response receive | OS/connection read fails | `OSError` -> `transport_failed` | reducer selects protocol or accepted stop outcome | transport category above |
| child exits after send | exit is observed before payload | `producer_exited` | reducer enters drain and continues channel observation | original result |
| child exits abruptly | no terminal send | EOF and nonzero exit code | reducer selects protocol failure with PID/exit code | worker-exited category above |
| timeout races with result | result is ready at the deadline turn or arrives after timeout acceptance | one-shot chooses a ready result before timeout; persistent session chooses timeout first; accepted timeout wins later | policy-selected outcome in deadline turn, otherwise timeout | compatibility-preserving result or timeout for each adapter |
| cancel races with result | cancel accepted before result | typed cancel and outcome events | reducer returns cancelled | cancellation result |
| limit races with result | limit accepted before result | typed limit and outcome events | reducer returns limit | existing limit result |
| leaked endpoint | EOF never arrives after producer exit | `drain_expired` | reducer selects protocol failure or accepted stop result | transport category above, not indefinite wait |
| persistent worker errors then exits | result precedes EOF | ordered response connection | reducer returns session error result | original traceback/error, not `Agent session exited unexpectedly` |

Defensive broad catches remain allowed only at process/connection cleanup
boundaries and must carry the existing pragma form. Classification catches name
the specific `EOFError`, `OSError`, and empty/poll outcomes.

## 11. Failure Modes Registry

| Path | Failure mode | Error handling | Test | User-visible |
|---|---|---|---|---|
| fast one-shot function | child exits before parent sees result | reducer drains to outcome/EOF | two existing red CLI tests plus real runner test | correct JSON result |
| local module function | import and return complete before next parent poll | same reducer path | installed console-entry-point matrix from a fresh initialized directory, without test-added `PYTHONPATH` | returned dict/JSON, no hang across first and reused-manager calls |
| one-shot exception | error outcome then immediate exit | ordered send and reducer | real spawn exception test | original target traceback/error |
| unpicklable return | pre-write terminal serialization fails | one pre-serialized error outcome; no partial frame | real spawn unpicklable-result test | bounded serialization category, status `failed`, exit 1 |
| child transport write failure | framed write starts but fails | close without retry; parent reduces transport failure/EOF | injected real-connection write failure | bounded delivery category, status `failed`, exit 1 |
| malformed or wrong-type received payload | bytes arrive but cannot become the expected terminal object | adapter emits `transport_failed`, never outcome | real invalid bytes plus well-formed wrong-type payload | bounded delivery category, status `failed`, exit 1 |
| sender closes while producer is live | EOF does not prove producer exit | protocol failure plus mandatory producer cleanup | live-producer EOF test | bounded delivery category, never false exit claim |
| abrupt `os._exit(73)` | no Python cleanup or send | OS closes endpoint; reducer sees seal | real spawn abrupt-exit test | worker-exited category includes exit code 73 |
| agent execute error | response published then worker breaks/exits | outcome before EOF | real persistent-session regression | original session error |
| endpoint leak | parent retains sender | internal drain deadline | endpoint-ownership and fake-clock expiry tests | protocol failure, not hang |
| same-turn observations | cancellation, result, timeout, limit, transport, seal, exit, or expiry coincide | explicit one-shot and persistent policies | 510 subset cases plus each adapter's 28 unordered pairs | one-shot: cancel > result > timeout > limit; persistent: cancel > timeout > result > limit; remaining order is transport failure > seal > exit > expiry |
| persistent level signals | accepted cancel/timeout/limit or observed exit remains true | consume once and filter from later selector inputs | multi-turn persistent signal sequences in both adapters | outcome, seal, or drain expiry eventually wins; no hang |
| cancel/result race | result appears after accepted cancel | cancel precedence | reducer path test and adapter test | cancelled |
| limit/result race | result appears after confirmed violation | limit precedence | reducer path test and adapter test | limit violation |
| command stdout/stderr tail | process exits before reader queues drain | existing EOF protocol | immediate-exit command characterization | complete output |

No row may finish implementation with no handling, no test, and silent user
impact.

## 12. Implementation Tasks

### Task 1: promote the behavioral specification

Outcome: the exact requirements in §8 become normative before code relies on
them.

Files:

- the six specification files in §8
- this plan

Actions:

1. Re-read each target section at the recorded promotion baseline.
2. Insert the exact proposed requirement text, adjusting only local grammar or
   table width. Defer the [TS-1] current-coverage claim to Task 6, after the
   named tests exist.
3. Add this plan to each touched spec's Related Plans section.
4. Record the promotion baseline identifier in §7.
5. Do not add implementation mappings yet.

Stop if review changes any precedence rule or transport boundary. Revise this
plan and re-run independent review before promotion.

Verify: `./bin/check-doc-paths`, plan/spec metadata tests, and `git diff --check`.

### Task 2: add the pure reducer and exhaustive table tests

Outcome: one production transition table expresses §6.4 with no I/O.

Files:

- new `weft/core/terminal_handoff.py`
- `weft/_constants.py`
- new `tests/core/test_terminal_handoff.py`
- `tests/core/test_state_machines.py` only if a generic helper assertion is
  missing and is useful beyond this reducer

Implementation shape:

```python
TerminalHandoffState = Literal[
    "observing",
    "draining",
    "stopping_timeout",
    "stopping_cancel",
    "stopping_limit",
    "decided",
]

TerminalHandoffEventKind = Literal[
    "outcome_received",
    "producer_exited",
    "channel_sealed",
    "timeout_requested",
    "cancel_requested",
    "limit_reached",
    "drain_expired",
    "transport_failed",
]

TerminalHandoffObservationPolicy = Literal["one_shot", "persistent_session"]

@dataclass(frozen=True, slots=True)
class TerminalHandoffEvent:
    kind: TerminalHandoffEventKind
    outcome: object | None = None
    detail: str | None = None

def reduce_terminal_handoff(
    current: TerminalHandoffState,
    event: TerminalHandoffEvent,
) -> StateDecision[TerminalHandoffState, TerminalHandoffAction]:
    ...

def select_terminal_handoff_event(
    observations: Collection[TerminalHandoffEvent],
    *,
    policy: TerminalHandoffObservationPolicy,
) -> TerminalHandoffEvent:
    """Select one event using the policy's normative same-turn order."""
    ...
```

Use repository constants for state/action vocabularies and transition specs
where that matches `task_lifecycle.py`. Do not put live payload objects in a
module-level table.

Full-table test contract:

```python
all_cells = set(product(terminal_handoff_states, terminal_handoff_event_kinds))
assert len(TERMINAL_HANDOFF_CASES) == len(all_cells) == 48
assert {case.cell for case in TERMINAL_HANDOFF_CASES} == all_cells
```

Define all expected cells as literal test data rather than deriving them from
the production transition table. For all 48 rows, including each of the eight
`decided` cells, independently call the production reducer. Assert exact source,
target, action, and transition ID plus a non-empty reason for valid rows. Assert
the exact `ValueError` contract for invalid rows. The oracle contains 39 valid
decisions and nine rejections: all eight `decided` cells plus
`observing + drain_expired`. Also assert:

- no duplicate case keys; transition IDs are unique among valid production
  transitions
- every state and action is covered
- every nonterminal state is reachable from `observing`
- `decided` is declared terminal and has no production outgoing transitions
- every production transition ID is fired by at least one row
- `outcome_received` requires an outcome payload; other event kinds reject a
  stray outcome payload

The pure same-turn selector has eight event kinds and two strict policies. Its
separate table-driven oracle covers all `2 * (2**8 - 1) == 510` non-empty
policy/subset cases and selects exactly one event. Tests do not import production
priority constants to compute expected values. Each host adapter also routes all
`C(8, 2) == 28` unordered event pairs through its declared policy, which proves
branch layout cannot reintroduce a different local priority.

Add sequence tests as a second layer, not a replacement for the 48 cells:

- outcome then stop observation: outcome
- producer exit then outcome: outcome
- outcome then producer exit: outcome
- producer exit then seal: protocol failure
- one-shot ready outcome plus due timeout: outcome
- persistent-session ready outcome plus due timeout: timeout
- every non-empty same-turn subset selects the exact event required by its
  one-shot or persistent-session policy
- persistent cancellation, timeout, limit, and dead-producer signals are
  consumed once; in later turns outcome or seal is selected, otherwise the
  absolute drain deadline produces `drain_expired`
- timeout then outcome: timeout
- timeout then seal/expiry/failure: timeout
- cancel then outcome: cancelled
- limit then outcome: limit
- every route stops at the first `decided` action

Mutation proof: temporarily invert producer-exit handling, each same-turn
policy's timeout/result order, edge consumption, and accepted-timeout
precedence. The table, selector, sequence, and adapter suites must fail. Record
the commands and failures in the implementation handoff.

### Task 3: migrate one-shot host results and turn the CLI regressions green

Outcome: function and one-shot agent work use ordered response delivery, seal,
and reducer classification.

Files:

- `weft/core/runners/host.py`
- `weft/_constants.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_agent_execution.py`
- `tests/cli/test_cli_run.py`

Actions:

1. Create `response_recv, response_send = self._ctx.Pipe(duplex=False)` for
   the non-command one-shot path.
2. Pass only the sender to `_worker_entry`. Close the parent's sender after a
   successful `process.start()` and close both unused endpoints on startup
   failure.
3. Replace `_put_terminal_mp_queue` for this path with a synchronous terminal
   send helper. Pre-serialize with `ForkingPickler` before the first write and
   use `send_bytes`. Only a pre-write serialization error may send one small,
   pre-serialized error outcome. If `send_bytes` fails, do not write a second
   frame on that connection; close it in `finally`.
4. Convert parent observations into reducer events. Keep process, monitor,
   clock, stop, drain, metrics, and `RunnerOutcome` mutation in `host.py`.
   Gather same-turn observations before reduction and apply §6.4's `one_shot`
   policy; do not let branch layout create an implicit priority. Track consumed
   stop/exit edges and filter them from later turns.
5. Set one absolute internal drain deadline on first entry into `draining` or a
   stopping state. Do not reset it on repeated observations.
6. Read channel payloads before declaring EOF when both are ready. Do not infer
   an empty channel from `poll(False)` plus dead process. Decode bytes and
   validate the expected terminal type before emitting `outcome_received`;
   decode failure or wrong type emits `transport_failed`.
7. On protocol failure, retain exit code, PID, last reducer state/event,
   transition ID, drain budget, and transport error in bounded runner
   diagnostics.
8. Before every terminal return, stop a still-live one-shot producer when the
   selected action requires it, reap the process, stop the monitor, and close
   endpoints under one bounded cleanup path. Channel seal or failure while the
   producer is live must not leak the process.
9. Implement the endpoint ownership ledger in §9, including full cleanup when
   `Process.start()` raises.

Tests:

- preserve the two red CLI tests unchanged except update their spec references
  to [CC-3.5] after promotion
- direct real-spawn fast `json:dumps` success
- real-spawn fast project-local module success from a temporary external
  project root
- fast target exception
- unpicklable return value
- injected transport-write failure after serialization
- invalid received bytes and a well-formed object of the wrong terminal type
- abrupt `os._exit(73)`
- parent endpoint ownership/closure
- injected `Process.start()` failure closes both response endpoints
- real one-shot host agent success through `tests/tasks/test_agent_execution.py`
- deterministic fake-observation adapter tests for exit-before-result,
  accepted-timeout-before-result, and all 28 same-turn unordered pairs from
  Task 2 under the one-shot policy
- multi-turn persistent cancel, timeout, limit, and producer-exit observations;
  each is consumed once and cannot starve later outcome, seal, or drain expiry
- a real spawned child and real pipe, passed through the driver's deterministic
  observation-source seam. The test withholds the already-real receiver's ready
  observation for exactly the first turn after joining the child, then exposes
  the real payload. Record and assert `observing + producer_exited -> draining`,
  then `draining + outcome_received -> decided`. This proves response policy,
  not OS pipe-delay behavior
- a public-path routing test proving `HostTaskRunner.run_with_hooks` delegates
  terminal observation to that same private handoff driver, rather than merely
  calling the reducer symbol somewhere
- live-producer plus channel-failure cleanup: the process is stopped/reaped and
  the monitor/endpoints close before the protocol error returns; its public
  error does not claim the producer exited
- dead producer plus EOF and numeric exit code selects the distinct public
  worker-exited category

Hard gate:

```bash
./.venv/bin/python -m pytest -n0 \
  tests/cli/test_cli_run.py::test_cli_run_fast_standard_library_function \
  tests/cli/test_cli_run.py::test_cli_run_stored_spec_preserves_fast_function_result \
  -q
```

Both must pass through `HostTaskRunner`, the production private handoff driver,
and `reduce_terminal_handoff`. The CLI tests prove public behavior; the forced
real-spawn ordering test and routing test prove the mechanism. Reject a green
result caused by carrier replacement alone, target delay, retry-only code, or a
bypass.

Installed-workflow gate: do not use `run_cli()`, `python -m weft.cli`, or the
test helper's repo-root `PYTHONPATH`. Resolve the current environment's `weft`
console script next to `sys.executable`, remove `PYTHONPATH` from the child
environment, and run from a fresh external temporary directory. Use the normal
isolated broker/harness environment and a hard subprocess timeout for every
call. The table is mandatory:

| Invocation in fresh `weft init` directory | Required assertion |
|---|---|
| `weft run --function json:dumps --arg '[1,2]'` | exit 0 and exact result |
| first `weft run --function registry_probe:ping` | local module dict result, no hang |
| identical second local call through the reused manager | same result, no hang |
| `weft run --spec .weft/tasks/fire-check.json` targeting `registry_probe:ping` | same result, no hang |
| `weft run --no-wait --function registry_probe:ping`, then `weft result TID` | bounded completion and same result |

Write `registry_probe.py` and the stored spec only inside that directory. If
this matrix reveals a distinct installed-entry-point or import-resolution
defect, stop the slice and either expand the spec/plan explicitly or split a
linked red-test plan. The repo-injected harness must not be used to dismiss it.

### Task 4: migrate persistent agent work-item responses

Outcome: an agent session error result followed by immediate worker exit is
returned as that error result, not replaced by `Agent session exited
unexpectedly`.

Files:

- `weft/core/runners/host.py`
- `weft/core/tasks/sessions.py`
- `weft/core/tasks/agent_session_protocol.py` only if type annotations need a
  carrier-neutral protocol helper
- `tests/tasks/test_runner.py`
- the existing agent session tests under `tests/tasks/` or `tests/core/`

Actions:

1. Use an ordered one-way response connection for booted, ready, startup error,
   and result responses. Keep the request queue unless a firing defect proves a
   need to change it.
2. Close the response sender only in the child; close the parent's sender copy
   after spawn. Preserve the existing private JSON envelope shapes and version.
3. Adapt `wait_ready` to connection polling while preserving its distinct
   readiness budget and late startup error behavior.
4. Route only per-work-item terminal classification through the terminal
   handoff reducer, using §6.4's `persistent_session` observation policy and the
   same edge-consumption rule as one-shot execution.
5. Preserve monitor and session cleanup ownership.
6. Define the session's post-verdict state. A normal `ok` result keeps the
   session valid. Timeout, cancellation, limit, protocol failure, and a non-ok
   result whose worker contract ends the session invalidate it. Before those
   verdicts return, stop/reap the worker as required, stop the monitor, and
   close response and request IPC. A second `execute()` rejects deterministically
   as a closed session.
7. Apply §9's endpoint ownership ledger to session startup. If
   `Process.start()` raises, close both response endpoints and all created
   request-queue resources. Test channel failure both before and after the
   worker becomes live.

Required red-green regression:

- a real spawned session child sends `ready`, accepts one execute request,
  sends an error `result`, and exits immediately
- before the change, `AgentSession.execute()` can return
  `Agent session exited unexpectedly`
- after the change, it returns the original error payload

Also test success while the session remains alive, startup error then EOF,
unexpected EOF without a result, timeout, cancel, limit, deterministic second
execute rejection after every invalidating verdict, real EOF after spawn,
injected `Process.start()` failure, all 28 same-turn unordered pairs under the
persistent-session policy, persistent level-signal multi-turn cases, and
endpoint/monitor cleanup.

### Task 5: close adjacent-path blind spots

Outcome: the test suite states why correct neighboring transports are safe and
fires their critical orderings.

Actions:

1. Add an immediate-exit one-shot command test that proves final stdout and
   stderr tail precede completion after both EOF sentinels.
2. Add or retain an interactive immediate-exit tail test without using fixed
   sleeps as correctness proof.
3. Retain agent startup's exit-before-startup-error/ready late-drain tests as a
   positive control after the carrier change.
4. Record BaseTask worker-lane and public result-wait paths as audited, not
   migrated, with links to their firing tests.
5. If this audit finds another real `producer dead + empty-now => terminal`
   branch in the direct blast radius, add a red test and bring that path under
   this reducer before completion. If the branch is a distinct durable
   reconciliation contract, add it to the deviation log and write a separate
   plan rather than widening silently.

Do not add timing loops solely to make tests pass. Real process examples use
eventual bounded harness waits only to observe the specified terminal event;
pure reducer and fake-clock tests own order/precedence proof.

#### Task 5 implementation audit (2026-08-04)

- The BaseTask worker lane remains an in-process bounded `queue.Queue`. Its
  producer publishes before deregistration, and its consumer drains on the task
  reactor. The firing tests are
  `tests/tasks/test_task_execution.py::test_base_task_worker_result_wakes_background_reactor_after_real_wait`,
  `::test_base_task_worker_lane_delivers_errors_on_main_thread`,
  `::test_base_task_worker_result_queue_backpressures_when_full`, and
  `::test_base_task_cleanup_stops_worker_lane`. It does not use producer death
  plus queue emptiness as terminal proof, so it was audited and not migrated.
- Public result waiting remains a durable broker reconciliation contract. The
  firing tests include
  `tests/commands/test_result.py::test_await_single_result_reads_outbox_after_completion_event`,
  `::test_await_single_result_returns_visible_one_shot_result_at_deadline`,
  `::test_await_one_shot_result_accepts_prewritten_outbox_when_log_event_is_missed`,
  and `::test_await_one_shot_result_does_not_infer_completion_from_ambiguous_outbox`.
  It was audited and not migrated because durable outbox/log reconciliation is
  distinct from the private spawned-worker handoff.
- Command and interactive response paths retain their existing EOF-owned drain.
  Their immediate-exit tail tests fire as
  `tests/tasks/test_runner.py::test_task_runner_collects_immediate_command_stdout_and_stderr_tail`
  and `::test_interactive_session_collects_immediate_exit_stream_tail`. Agent
  startup retains separate `booted`/`ready` handshake behavior through
  `::test_agent_session_startup_error_survives_immediate_child_exit` and the
  dedicated readiness-timeout tests. The blast-radius search found no other direct
  `producer dead + empty-now => terminal` classification branch.

### Task 6: diagnostics, traceability, and lessons

Outcome: operators can tell a target failure from a terminal transport failure,
and docs point both ways.

Actions:

1. Replace generic no-result handling with §10's public categories. Put state,
   event, transition, PID, exit code, transport status, and drain bound only in
   bounded `runner_diagnostics`.
2. Add plain and `--json` CLI tests for each public category. Assert exact
   status and exit code, the preserved top-level JSON key sets, and stable
   category text with substring-level cause matching. Also assert result-only
   success stdout, target-exception preservation, timeout exit 124, and
   unchanged cancellation/limit rendering. Fire dead-producer plus numeric-exit
   EOF separately from live-producer/unproved-exit EOF so neither message can
   make a false causal claim.
3. Add implementation mappings to [CC-3.5], [RM-5.2], [REDUCER.5]-[REDUCER.8],
   [EXEC.5]-[EXEC.10], [TS-1], [CLI-1.1.1], and [AR-6].
4. Update module docstrings with reciprocal spec references.
5. Add the repeated lesson to `docs/lessons.md`: producer liveness and channel
   completion are independent evidence; tests must fire both observation
   orders and all state/event cells.
6. Add a next-release `CHANGELOG.md` Fixed entry: fast function and stored-spec
   execution no longer loses a result or hangs when worker exit races private
   result delivery. State that no TaskSpec, `.weft`, queue, or data migration is
   required. No README migration guide is needed because invocations are
   unchanged.
7. Close every deviation-log row and update this plan's status only after the
   completed-work review passes.

### Task 7: full verification and completed-work review

Run the smallest suites after each task, then the full repository gates. No
completion claim before the independent completed-work review and clean current
git status accounting.

## 13. Testing Plan

```text
PURE CONTRACT
  6 states x 8 events = 48 explicit rows
    -> exact decision or exact invalid result
    -> structural reachability/coverage
    -> sequence/confluence checks

ADAPTER CONTRACT
  fake observations + fake clock
    -> reducer events
    -> one effect per decision
    -> one absolute drain deadline

REAL IPC
  spawned child -> ordered response connection -> EOF
    -> fast success
    -> error then exit
    -> unpicklable result
    -> abrupt exit
    -> leaked-endpoint safety bound

PUBLIC REGRESSION
  weft run --function / --spec
    -> Manager -> Consumer -> HostTaskRunner -> reducer
    -> outbox / CLI result

INSTALLED WORKFLOW
  real weft console script, no repo PYTHONPATH
    -> fresh init -> stdlib function
    -> local module twice through reused manager
    -> stored spec and no-wait/result
```

Test layers:

| Layer | Files | Proof |
|---|---|---|
| pure unit | `tests/core/test_terminal_handoff.py` | all 48 cells, all paths, invalid terminal events |
| adapter unit | `tests/tasks/test_runner.py`, agent-session tests | observation mapping, effect ownership, deadline behavior |
| real process | runner/session tests | spawn, pipe order, EOF, serialization, exit behavior |
| public CLI | `tests/cli/test_cli_run.py` | public success/failure rendering, JSON key sets, status, and exit codes |
| installed CLI | `tests/cli/test_cli_run_installed_entrypoint.py` | actual console entry point, fresh init, no test-added `PYTHONPATH`, local module, manager reuse, stored spec |
| architecture | focused source/import guard if needed | reducer stays pure and adapters do not bypass it |
| adjacent characterization | command/interactive/startup tests | correct EOF/late-drain paths remain correct |

Full-table tests are examples with exact expected cells. Property tests may add
sequence fuzzing, but they do not replace the 48-row oracle.

## 14. Verification and Gates

### Baseline red proof

Keep the existing evidence command and result in the implementation handoff:

```bash
. ./.envrc
./.venv/bin/python -m pytest -n0 \
  tests/cli/test_cli_run.py::test_cli_run_command_inline \
  tests/cli/test_cli_run.py::test_cli_run_function_inline \
  tests/cli/test_cli_run.py::test_cli_run_fast_standard_library_function \
  tests/cli/test_cli_run.py::test_cli_run_stored_spec_preserves_fast_function_result \
  -q
```

Baseline expectation: the two existing controls pass; both new regressions fail
with `Worker produced no result`.

### Per-slice gates

```bash
./.venv/bin/python -m pytest -n0 tests/core/test_terminal_handoff.py -q
./.venv/bin/python -m pytest -n0 tests/tasks/test_runner.py -q
./.venv/bin/python -m pytest -n0 tests/tasks/test_agent_execution.py -q
./.venv/bin/python -m pytest -n0 tests/cli/test_cli_run.py -q
./.venv/bin/python -m pytest -n0 tests/cli/test_cli_run_installed_entrypoint.py -q
./.venv/bin/ruff check weft/core/terminal_handoff.py weft/core/runners/host.py weft/core/tasks/sessions.py tests/core/test_terminal_handoff.py tests/tasks/test_runner.py tests/tasks/test_agent_execution.py tests/cli/test_cli_run.py tests/cli/test_cli_run_installed_entrypoint.py
./.venv/bin/mypy weft --config-file pyproject.toml
```

### Reducer-use acceptance gate

All of these must be true:

1. the two named red CLI tests are green without changing their behavior
   assertions
2. the installed-entry-point matrix passes with `PYTHONPATH` removed
3. a real spawned fast-function test observes the production reducer being
   called
4. the full reducer table and the forced real-spawn
   `producer_exited -> draining -> outcome_received` test fail under the
   documented mutation and pass when restored
5. `rg "Worker produced no result" weft/core/runners/host.py
   weft/core/tasks/sessions.py` finds no migrated direct-classification branch
6. review confirms no raw `process.is_alive()` plus empty response check selects
   a terminal outcome outside the reducer
7. plain and JSON CLI compatibility tests cover success, target failure,
   serialization failure, transport failure, abrupt exit, timeout,
   cancellation, and limit without exposing reducer fields
8. no sleep/retry/special-target workaround appears in the diff

### Final gates

```bash
. ./.envrc
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check weft
./bin/check-doc-paths
./bin/check-dom15-fixtures
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
git diff --check
```

Run the real process/CLI subset repeatedly under ordinary xdist scheduling to
look for residual flakes. Repetition is a confidence probe, not the proof of
correctness; the full table, fake-observation tests, and sealable transport are
the proof.

### Mutation evidence (2026-08-04)

The `observing + producer_exited` production row was temporarily changed from
`draining/begin_drain` to `decided/return_protocol_failure`. Running
`pytest -n0 -q tests/core/test_terminal_handoff.py
tests/tasks/test_runner.py::test_real_pipe_exit_then_outcome_uses_production_handoff_driver`
then failed five firing checks: the literal table row, structural reachability,
the pure exit-then-outcome sequence, consumed-exit drain expiry, and the real
spawned-pipe adapter path. The row was restored verbatim and the same command
passed. `git diff --check` also passed after restoration.

Four further mutations completed the approved proof:

- Swapping one-shot `outcome_received` and `timeout_requested` precedence made
  `pytest -n0 -q tests/core/test_terminal_handoff.py::test_terminal_handoff_selector_all_nonempty_subsets
  tests/core/test_terminal_handoff.py::test_terminal_handoff_driver_preserves_deadline_policy
  tests/tasks/test_runner.py::test_host_runner_routes_all_event_pairs_through_one_shot_policy`
  fail 34 cases: 32 selector subsets, the policy sequence, and the adapter pair.
- Swapping persistent-session timeout/result precedence made the corresponding
  selector, deadline-policy, and persistent adapter-pair command fail the same
  34 cases.
- Disabling `producer_exited` edge consumption made the two policy drain-expiry
  sequences and both adapter drain-expiry sequences fail (four cases).
- Changing `stopping_timeout + outcome_received` to `return_outcome` made the
  literal table row and persistent accepted-timeout sequence fail (two cases).

Each mutation was applied alone, reverted verbatim, and followed by the
combined literal table, selector, sequence, and adapter suite. The restored
combined suite and `git diff --check` passed.

## 15. Hardening Checklist

### Current structure

- one-shot non-command execution and persistent agent execution use different
  parent loops over the same vulnerable response-queue pattern
- command streams already use explicit EOF and bounded drain
- generic reducer infrastructure already exists and is spec-governed

### Boundary semantics

- public API: unchanged
- private protocol payload schema: unchanged
- private carrier: multiprocessing queue -> one-way ordered connection for
  host responses
- owner: host/session adapters acquire observations and apply effects; reducer
  selects only
- persistence: none
- deployment sequencing: one atomic package release; no mixed-process version
  because parent and spawned child run the same installed code

### One-way doors and reversibility

- no persisted migration or external protocol
- promoted behavioral invariant is intentional and should remain even if the
  carrier is later replaced
- carrier rollback is a single-slice revert

### Rollout

1. land spec text and reducer tests
2. land one-shot host migration and red-test gate
3. land persistent session migration
4. land adjacent characterization and mappings
5. run full suite and completed-work review

Do not feature-flag the private carrier. A flag would double the race-sensitive
paths and weaken the gate. Roll back the coherent slice if a platform defect is
found.

### Performance

- pipe reads remain bounded by the existing active-control poll interval while
  the producer is live
- no new durable queue traffic or database access
- synchronous child send introduces backpressure for large pickled outcomes;
  that is desirable because it prevents child exit before transport acceptance
- parent reads concurrently, so payloads larger than the OS pipe buffer must
  not deadlock
- measure fast-call overhead and one large pickled outcome; no benchmark gate
  is needed unless latency regresses materially from the current runner process
  startup cost

Implementation measurement (2026-08-04, macOS, repo virtualenv, `pytest -n0`):
the spawned fast-function call and a 4 MiB returned string each completed in
0.18 seconds. The large-result test is a firing liveness gate, not a latency
threshold; both durations remain dominated by ordinary spawned-worker startup.

### Security

- no new input, endpoint, privilege, shell, or trust boundary
- private payloads remain user-level trusted process data
- diagnostics must not add secrets or full arbitrary payload values

### Anti-mocking

- use literal values for the pure reducer table
- use fake observation sequences only for exact order/deadline adapter proof
- use real spawned children and real connections for IPC correctness
- use the real CLI harness for public regressions
- do not preload a response channel and claim that late visibility was tested

## 16. Independent Review Loop

Review is mandatory before implementation because this is Class 5 and changes
terminal outcome selection.

### Draft-plan review

Use independent reviewers sequentially:

1. strategy/spec reviewer: challenge whether sealable transport plus reducer is
   the smallest complete answer and whether the proposed spec overreaches
2. engineering reviewer: verify every matrix cell, precedence rule, transport
   lifecycle, error path, and gate
3. developer-experience reviewer: verify CLI failures remain bounded and
   actionable, with no new user concepts on success

Each reviewer reads the complete plan, exact proposed spec delta, current
`host.py` and `sessions.py`, the state-machine helper, and the red tests.
Findings are fixed and re-reviewed by the same reviewer until PASS. If the same
finding remains after three consecutive correction rounds, persist the
disagreement in §18 and block promotion.

### Completed-work review

An independent reviewer must answer PASS or BLOCKED:

> Does the implementation match the promoted [CC-3.5] table exactly? Does every
> one of the 48 state/event cells fire? Do both selector policies fire all 510
> non-empty subset cases and both adapters route their 28 pairs? Do the two named
> CLI regressions pass through the reducer with real spawn/IPC? Can producer
> exit, a persistent level signal, channel failure, timeout, cancellation,
> limit, or endpoint leakage still produce a hang, duplicate verdict, silent
> failure, or direct classification bypass? Does the installed-entry-point
> matrix pass, and are public schemas and correct neighboring transports
> unchanged?

The completed-work reviewer also inspects the full diff, test output, mutation
evidence, spec mappings, and git history. A passing full suite without this
review is insufficient.

## 17. Out of Scope

- making OS scheduling, IPC timing, cancellation timing, or timeout timing
  deterministic
- a universal state-machine framework or second runtime control plane
- public runner-plugin protocol changes
- changing command target execution, except characterization tests
- changing durable result/outbox reconciliation or its completion grace
- changing manager startup, work stealing, or child lifecycle ordering
- adding automatic retries for target work
- changing local-module import resolution after the installed-entry-point matrix
  proves that resolution itself is sound; a distinct failure stops this slice
  for explicit scope revision or a linked plan
- feature flags or dual private carriers
- broad cleanup of `host.py` or `sessions.py`

If investigation proves the manager or public broker paths violate the exact
same invariant, record a red test and a follow-up plan. Do not silently expand
this private host-carrier slice across durable queue semantics.

## 18. Deviation Log

| Spec ref or gate | Planned behavior | Actual behavior | Rationale | Required action |
|---|---|---|---|---|
| [CC-3.5] red CLI gate | Carrier and reducer changes make the two named regressions green | The stdlib/stored-spec case also exposed nested `FrozenList` bootstrap failure before `_worker_entry` | The symptom had two causes on the same required production path; ignoring the bootstrap fault would leave the hard gate red | Closed: [CC-3.5] now requires spawn-safe container normalization; `_plain_spawn_value` and CLI/unit regressions fire it |
| [CC-3.5], [EXEC.5] | Reducer in `terminal_handoff.py`; carrier work in host/session owners | Shared framing lives in new `terminal_handoff_transport.py` | Keeping transport I/O out of the pure reducer and avoiding duplicated one-shot/session framing requires one narrow internal owner | Closed: reciprocal mappings, malformed/write/serialization tests, and full type/lint gates cover the module |
| [CC-3.5], CLI taxonomy | EOF plus immediate exit exposes the numeric-exit cause consistently | Under xdist load, EOF could be observed just before multiprocessing published the exit code, yielding the evidence-true generic category | EOF is already terminal channel evidence, so one bounded join may refine cause without guessing about payload visibility or changing the reducer verdict | Closed: [CC-3.5] specifies the bounded post-seal evidence step; one-shot, session, startup, live-EOF, and CLI tests distinguish proved exit from live/unproved exit |
| full-suite xdist gate | Reducer tests collect identically on every worker | One payload-contract parametrization iterated a `frozenset`, producing worker-specific test order | Table-driven tests need deterministic collection as well as deterministic assertions | Closed: the parametrization now uses the literal eight-event tuple and the xdist subset is repeated before completion |
| documentation path gate | `check-doc-paths` has no failures introduced by this slice | The repository baseline already contains eight unrelated dangling claims in agent runbooks, testing-strategy planned paths, and the sample CLI-test path | Repairing unrelated documentation claims would widen this execution-path slice | Closed for this slice: the exact eight-item baseline was reproduced before and after implementation; every new plan/spec/test/code path resolves |

Every row must be closed before the plan is marked completed. A changed
precedence rule requires spec revision and renewed independent review, not a
code-only deviation.

## 19. Fresh-Eyes Review

| Reviewer | Round | Verdict | Findings and resolution |
|---|---:|---|---|
| `audit_process_channels` (strategy/spec) | 1 | BLOCKED | Corrected timeout overreach, globally scoped reducer-test policy, mechanism proof, cleanup ownership, causal overclaim, and alternate-runner scope. |
| `audit_process_channels` | 2 | PASS | Verified all six corrections. |
| `audit_process_channels` | 3 | BLOCKED | Found level-trigger starvation and an unapproved persistent-session timeout precedence change. Added edge consumption and distinct compatibility policies. |
| `audit_process_channels` | 4 | PASS | Verified policy compatibility, bounded progress, scope, and proposed spec coherence. |
| `audit_test_blindspots` (engineering) | 1 | BLOCKED | Added total precedence proof, safe pre-serialization, full session cleanup, endpoint ownership, correct agent test ownership, and literal 48-row expectations. |
| `audit_test_blindspots` | 2 | BLOCKED | Named pair cases did not cover the declared priority. Replaced them with exhaustive policy/subset and per-adapter pair tables. |
| `audit_test_blindspots` | 3 | PASS | Verified the 48-cell reducer, selector proof, real observation seam, cleanup, and adapter routing. |
| `audit_test_blindspots` | final recheck | PASS | Verified the two compatibility policies and edge-trigger multi-turn progress. |
| `audit_thread_channels` (developer experience) | 1 | BLOCKED | Added public/private error taxonomy, installed console-entry workflow, distinct serialization/transport handling, changelog, and CLI compatibility gates. |
| `audit_thread_channels` | 2 | BLOCKED | Made messages target-neutral and evidence-true; classified invalid received payloads; removed duplicated AR text. |
| `audit_thread_channels` | 3 | PASS | Verified CLI, local-module/stored-spec, diagnostics, and migration contracts. |
| `audit_thread_channels` | final recheck | PASS | Verified adapter-specific deadline policies and edge consumption do not change the public taxonomy. |
| `audit_process_channels` (completed work) | 1 | BLOCKED | Found malformed nested session payload escape, late limit polling after producer exit, post-spawn monitor-load leakage, and drain deadlines set after stop effects. All gained firing tests and were corrected. |
| `audit_process_channels` (completed work) | 2 | BLOCKED | Found metrics sampling could still obstruct invalid-session cleanup. Sampling became best-effort and close now guarantees IPC cleanup through nested `finally` blocks. |
| `audit_process_channels` (completed work) | 3 | PASS | Verified reducer exactness, adapter effects, deadline placement, monitor/session cleanup, post-seal evidence refinement, and removal of direct classification. |
| `audit_test_blindspots` (completed work) | 1 | BLOCKED | Required the real production-driver exit/outcome route, real broken-pipe failure, all planned mutation probes, and both-adapter multi-turn level-signal proof. Added each firing test and mutation record. |
| `audit_test_blindspots` (completed work) | 2 | PASS | Verified all 48 cells, 510 subsets, 56 adapter pairs, real IPC/failure paths, mutation proof, and adjacent-path audit. |
| `audit_thread_channels` (completed work) | 1 | BLOCKED | Required real CLI generic transport, target, cancel, and limit coverage in both output modes. Added exact schema/category gates. |
| `audit_thread_channels` (completed work) | 2 | BLOCKED | Required JSON timeout exit-124 coverage and explicit rejection of private reducer details in public error strings. Added both. |
| `audit_thread_channels` (completed work) | 3 | PASS | Verified public taxonomy/privacy, installed path, mappings, changelog, lessons, and no-migration statement. |

Final verification on 2026-08-04: the full repository run completed with
3,173 passed and 14 intentionally skipped tests in 275.20 seconds. Full mypy
passed for 197 source files; Ruff, DOM-15 fixtures, plan metadata, test-audit
classification, harness registration, and `git diff --check` passed. The
doc-path command reproduced only the eight pre-existing claims recorded in the
deviation log.

An auxiliary CLI-driven review attempt did not produce a bounded verdict and
was terminated. No finding or approval from that attempt is used. The three
independent repository reviews above are the recorded draft-plan review.

## 20. Decision Audit Trail

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | premise | treat nondeterminism as input and response policy as the defect | user-confirmed | explicit policy | matches the stated goal and observed evidence | scheduling determinism |
| 2 | architecture | reuse `StateMachine` and add a terminal handoff reducer | mechanical | DRY | existing internal reducer helper already owns this pattern | new framework |
| 3 | transport | use a sealable ordered private response channel | architecture | completeness | EOF supplies proof that a queue-empty timing loop cannot | queue sleep/retry |
| 4 | scope | migrate one-shot host and persistent agent response paths | scope | blast radius | both use the same producer-death/response-visibility pattern | rewrite correct command/public queue paths |
| 5 | tests | require all 48 reducer cells, 510 selector cases, per-adapter pair routing, real IPC, and public regressions | user-confirmed | completeness | structural coverage alone cannot prove invalid, no-op, priority, or multi-turn cells | branch-only tests |
| 6 | UX | preserve success output; make protocol failure bounded and actionable | mechanical | fight uncertainty | developers need problem, cause, and bounded diagnostics only on failure | new success verbosity |
| 7 | compatibility | preserve distinct one-shot and persistent deadline precedence | review correction | least surprise | unifying the adapters would silently change persistent-session timeout behavior | one shared priority |
| 8 | liveness | consume stop and producer-exit level observations once | review correction | bounded progress | repeated high-priority facts must not starve EOF, outcome, or drain expiry | level-triggered reselection |
| 9 | errors | pre-serialize before framing and never retry a failed write | review correction | protocol integrity | a second frame after partial write can corrupt the stream | generic send fallback |
| 10 | acceptance | exercise the actual console entry point without repo `PYTHONPATH` | review correction | test what ships | the existing helper can mask installed/local-module defects | repo-only CLI helper |

## 21. Implementation Parallelism

Sequential implementation is required through the first green slice. The spec,
reducer table, one-shot adapter, and persistent adapter share the same contract
and core modules; parallel worktrees would create review and merge risk at the
exact boundary being hardened.

After Task 3 is green, adjacent command/interactive characterization in Task 5
may run in parallel with Task 4 because it touches different tests and does not
change the reducer. Merge Task 4 first, then rebase and verify Task 5.

## 22. Definition of Done

The plan is complete only when:

- the proposed spec text is promoted and all mappings point both ways
- the reducer table has exactly 48 unique state/event cases and every case fires
- both selector policies fire all 510 non-empty subset cases; each adapter routes
  all 28 unordered event pairs and passes the multi-turn level-signal cases
- the two named red CLI tests pass through the reducer
- the installed console-entry matrix passes without test-added `PYTHONPATH`
- the real local-module, session error-then-exit, serialization, malformed
  payload, transport-write, live/dead EOF, abrupt-exit, timeout, cancel, limit,
  and drain-expiry paths pass
- plain and JSON CLI taxonomy, schema, status, and exit-code gates pass without
  exposing reducer details
- correct command, interactive, startup, worker-lane, and public-result paths
  are characterized or linked to firing tests
- no migrated direct `dead + empty-now` terminal branch remains
- `CHANGELOG.md` records the fix and the absence of migration work
- full pytest, mypy, Ruff, doc-path, DOM-15, metadata, and diff gates pass
- the completed-work independent reviewer returns PASS
- every deviation is closed
- the finished slice is committed before anyone calls it ready to land

## GSTACK REVIEW REPORT

- Premise gate: PASS. The user explicitly chose a reducer, deterministic
  response policy, full table-driven state-machine tests, and a red-to-green
  reducer gate.
- Strategy/spec: PASS after four rounds.
- Design: skipped because there is no visual or interaction-design scope.
- Engineering: PASS after three rounds plus a final coherence recheck.
- Developer experience: PASS after three rounds plus a final coherence recheck.
- Auxiliary CLI review voice: unavailable because it did not return a bounded
  verdict; it contributed no finding or approval.
