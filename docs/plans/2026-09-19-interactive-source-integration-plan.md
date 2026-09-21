# Interactive Source Integration Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-2.3], [CC-3.1]; docs/specifications/06-Resource_Management.md [RM-5.1]; docs/specifications/07-System_Invariants.md [IMPL.10]; docs/specifications/14-Python_API_Surfaces.md [PY-1]
Superseded by: none

Class: 5. This changes the public `TaskRunnerBackend.start_session()` contract
and the normative task-reactor and interactive resource-timer mappings. It also
changes the interactive execution path, so hardening is required.

Plan type: implementation with spec revision.

Hardening: required. The change crosses the Consumer, runner facade, public
runner protocol, host subprocess, and retained watcher boundary. A wrong change
can leave an interactive task asleep after output or process exit, sample limits
at the wrong cadence, or create a second task wait path.

## Goal

Remove the interactive Consumer's 50 ms reactor wait cap. Route interactive
stream publication and tracked-process exit through the retained watcher's
existing local-activity notification, and route resource checks through the
existing `next_wait_timeout()` timer contract.

The implementation extends one existing runner operation. It adds no queue
type, callback-registration API, session protocol, event bus, generic source
adapter, or second reactor.

## Source Documents and Current Contract

- `docs/specifications/01-Core_Components.md` [CC-2.2.1] defines the single
  task reactor and its three wait-input classes: backend events, local events,
  and timers. [CC-2.3] assigns interactive lifecycle policy to Consumer.
  [CC-3.1] assigns runtime-specific process mechanics to runner backends while
  core retains lifecycle, queue, and control policy.
- `docs/specifications/06-Resource_Management.md` [RM-5.1] defines
  `spec.polling_interval` as the resource-monitor sampling cadence.
- `docs/specifications/07-System_Invariants.md` [IMPL.10] requires BaseTask's
  watcher-centered task loop to remain the only task-driving path.
- `docs/specifications/14-Python_API_Surfaces.md` [PY-1] makes
  `TaskRunnerBackend` and `CommandSessionProtocol` public extension contracts.
- `../simplebroker/docs/specs/16-python-library-api.md` [SB-API-6] defines the
  retained `PollingStrategy.notify_activity()` latch. A foreign thread may arm
  it after publishing local state. The wait owner observes it within the normal
  strategy pass; it does not synchronously interrupt a native wait.
- `docs/plans/2026-09-17-watcher-reactor-restoration-plan.md` is historical
  rationale for restoring the watcher-centered reactor. Its implemented rule
  remains intended: local sources publish state, then notify the retained
  strategy; task policy does not gain a sampling loop.

## Current Defect

`54874c2f` added `Consumer._wait_for_reactor_activity()` with a 50 ms cap while
an interactive session exists. That fixed a real liveness failure: interactive
output and local subprocess exit can occur without a watched broker queue
write. It fixed the failure by periodically running full Consumer policy turns,
which bypasses the reactor's source classification.

The current host runner already has two blocking source owners. Its stdout and
stderr reader threads publish decoded chunks into ordinary in-process queues.
They do not notify the retained strategy after publication. The tracked
`subprocess.Popen` exit is also a positive local event, but no blocking waiter
publishes it to the task reactor. `Consumer._interactive_flush_outputs()` then
calls `poll_limits()` on every manufactured turn even though the configured
monitor cadence is normally 1 second.

The uncommitted worktree currently contains a broader draft [CC-2.2.1]
"source adapter" recipe. That text is not a promotion baseline. Slice 1 replaces
it with the narrower delta in this plan.

## Existing Owners and Files

Read before editing:

- `weft/core/tasks/base.py`: owns the retained strategy, the final public
  `wait_for_activity()` template, local worker publication, and
  `next_wait_timeout()`.
- `weft/core/tasks/consumer.py`: owns Consumer turns and currently owns the
  interactive 50 ms protected-wait override.
- `weft/core/tasks/interactive.py`: owns interactive session creation, stream
  draining, limit decisions, terminal state, and session cleanup.
- `weft/core/tasks/debugger.py`: overrides interactive session creation for an
  in-process session whose publication is synchronous on the reactor owner.
- `weft/core/tasks/runner.py`: is the existing facade over a selected runner
  backend.
- `weft/ext.py`: owns public `TaskRunnerBackend` and
  `CommandSessionProtocol`.
- `weft/core/runners/host.py`: owns `subprocess.Popen`, stdout/stderr reader
  threads, the concrete command session, and runtime-specific process exit.
- `weft/core/tasks/sessions.py`: owns the private concrete `CommandSession`;
  its public structural protocol is declared in `weft/ext.py`.
- `extensions/weft_docker/weft_docker/plugin.py`,
  `extensions/weft_docker/weft_docker/agent_runner.py`,
  `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py`, and
  `extensions/weft_microsandbox/weft_microsandbox/plugin.py`: implement
  `start_session()` but currently reject interactive sessions.
- `tests/fixtures/public_extension_contract.py` and
  `tests/core/test_public_extension_contracts.py`: fire the public structural
  runner contract.
- `tests/tasks/test_task_interactive.py` and `tests/tasks/test_runner.py`: own
  the real interactive task and host-session behavior.

Comprehension gates before editing:

1. Why must a host producer publish its queue item or process return code before
   invoking the callback, and why must Consumer still re-read session state?
2. Why is stream EOF insufficient to prove tracked-process exit when a
   descendant inherits stdout or stderr?
3. Why must output and exit use local notification while resource sampling uses
   `next_wait_timeout()`?

## Design

### One existing runner operation carries the local wake callback

Extend the existing public operation:

```python
def start_session(
    self,
    *,
    on_activity: Callable[[], None],
) -> CommandSessionProtocol: ...
```

`TaskRunner.start_session()` forwards the required keyword unchanged. Consumer
passes its retained strategy's `notify_activity` method when it creates an
interactive session.

Thread that callable from `Consumer._handle_work_message()` through
`InteractiveTaskMixin._interactive_maybe_handle_message()` to
`_interactive_ensure_session()`. Do not add a BaseTask forwarding method or make
the mixin import the concrete SimpleBroker strategy type merely to reach the
callback.

This follows the existing `TaskRunnerBackend.run_with_hooks()` callback shape.
The runner observes runtime-specific state; core supplies the task-local wake
hint. The callback carries no payload, owns no broker resource, and grants no
task-policy authority to a runner thread.

The callback is required. There is no optional capability check, signature
inspection, compatibility fallback, or second behavior for a backend that omits
it. Weft is in beta and the current users are internal; all in-repository
implementations and the public contract fixture change together. Backends whose
`start_session()` always raises still accept the required keyword before raising
their existing unsupported-session error.

`CommandSessionProtocol` does not change. A post-construction setter would let
the child publish output or exit between thread start and callback registration.
Passing the callback into `start_session()` makes source construction and wake
wiring one operation.

### Host stream publication uses the existing in-process queues

Keep the existing ordinary stdout and stderr queues. Each existing host reader
thread performs this order for a chunk and for its EOF marker:

1. `target_queue.put(value)`;
2. `on_activity()`.

The queue remains authoritative. The callback is only a coalescing hint. Do not
add `ActivityQueue`, another local event queue, or direct broker publication from
the reader thread.

### Host process exit uses one private blocking wait

After `Popen` creation, the host runner starts one daemon thread that calls
`process.wait()` and then invokes the same `on_activity()` callback. `Popen`'s
stored return code is the authoritative state; the thread publishes no new
event object.

This private wait preserves current completion behavior when the tracked child
exits but a descendant retains a pipe, so neither reader has reached EOF. It is
not a reusable sentinel adapter. It has no membership, wake pipe, result queue,
registry, broker handle, or task reference beyond the supplied callback. Normal
session shutdown already terminates the tracked process, so the blocking wait
ends when Consumer's existing shutdown path terminates the tracked process.

Do not reuse the Manager child-sentinel implementation. The Manager observes a
changing set of `multiprocessing.Process` sentinels and must suppress
level-triggered readiness. An interactive host session observes one fixed
`subprocess.Popen`; only the notification contract is shared.

### Resource checks use the existing task timer

Record one absolute next-limit-check time after session creation, using
`spec.polling_interval`. While a session exists:

- `Consumer.next_wait_timeout()` returns the minimum of the BaseTask timeout
  and the remaining limit-check time;
- `_interactive_flush_outputs()` drains streams and checks process state on
  every real reactor turn, but calls `session.poll_limits()` only when the
  absolute deadline is due;
- after `poll_limits()` returns, advance the deadline from a fresh
  `time.monotonic()` reading by one interval rather than running catch-up
  samples;
- output, control, backend, and exit activity do not move the deadline; and
- finalization clears the deadline with the session.

This is the existing timer class. It adds no timer thread and no private wait.

### Delete only the task-policy cap

After all three inputs are connected, remove:

- `Consumer._wait_for_reactor_activity()`;
- Consumer's import and use of `ACTIVE_CONTROL_POLL_INTERVAL`; and
- the test that expects the 50 ms cap.

Do not delete or rename `ACTIVE_CONTROL_POLL_INTERVAL` globally. It still owns
separate bounded operations in host terminal handoff, subprocess execution, and
session finalization. Those uses do not schedule idle Consumer reactor turns and
are outside this plan.

## Invariants and Constraints

- BaseTask and its retained `MultiQueueWatcher` remain the only Consumer wait
  owner. Consumer must not replace the public task-loop or protected wait hook.
- Source threads publish authoritative in-process state before notification.
  Consumer alone reads that state and performs TaskSpec, broker, control, and
  terminal effects.
- Notifications may coalesce. No code infers event count or event kind from a
  callback invocation.
- The production callback is SimpleBroker's no-fail latch assignment. Do not
  add a retry, error queue, or alternate wake path around callback invocation.
- `notify_activity()` preserves SimpleBroker's nominal first-wakeup bound. The
  plan does not promise synchronous interruption or a tighter wall-clock bound.
- Resource checks retain `spec.polling_interval`; incidental activity must not
  increase their sample rate.
- Interactive queue formats, TaskSpec shape, state transitions, reserved
  policy, stream envelopes, runtime handles, and cleanup priority do not change.
- One-shot runner execution, agent sessions, and command-side interactive
  clients do not change.
- No new dependency, queue, thread pool, session protocol method, reusable
  adapter module, or compatibility path is allowed.
- The public runner contract changes in one coordinated release. No persisted
  payload or rolling mixed-version state exists, so there is no one-way data
  migration.

Stop and revise this plan if implementation requires any of the following:

- a second wait or polling loop in Consumer;
- an optional `start_session` callback;
- a callback setter on the returned session;
- a new queue or event object whose only purpose is to wake the reactor;
- runner-thread broker access or TaskSpec mutation; or
- a generic process-observer abstraction shared with Manager.

## Spec Baseline

- `a599af4b3aafd4c7f0d96a03106f7c6cfad8ec96` is the committed spec baseline at
  plan revision time.
- Baseline blobs:
  - `fed91098d13cae2df32c108a5dd64a39588210f2`:
    `docs/specifications/01-Core_Components.md`
  - `e9f3ac57fa29f0cb99e77b36119256d7af0e6aa2`:
    `docs/specifications/06-Resource_Management.md`
  - `e2e3eaecff0734d5c5cbaea6c94bacec7789ddd9`:
    `docs/specifications/07-System_Invariants.md`
  - `21eefc4020a7af87524aaf9f740ae66f1e96476a`:
    `docs/specifications/14-Python_API_Surfaces.md`
- The current uncommitted [CC-2.2.1] source-adapter recipe is draft material,
  not the baseline or the target. Slice 1 replaces it with the exact delta
  below and records the promotion baseline before implementation begins.

## Proposed Spec Delta

Promotion strategy A: promote the reviewed contract text before implementation
cites it.

| Spec file | Strategy | Sections touched |
|---|---|---|
| `docs/specifications/01-Core_Components.md` | A | [CC-2.2.1], [CC-3.1] |
| `docs/specifications/06-Resource_Management.md` | A | [RM-5.1] |
| `docs/specifications/14-Python_API_Surfaces.md` | A | [PY-1] |

`docs/specifications/07-System_Invariants.md` [IMPL.10] remains unchanged and
is a review oracle.

### [CC-2.2.1]

Replace the wait-input paragraph with:

> Every task wait input is a backend event, a local event or a timer. Local
> events (worker result publication, finite worker-lane retirement, deferred
> signals, parent loss, stop, child-process exit, interactive child stream
> publication, and a task's own write to a watched queue) publish authoritative
> state before waking the wait through the retained strategy's coalescing
> local-activity notification; no task-reactor wait cap exists for them. Timers
> are published through `next_wait_timeout()` and passed as the wait deadline. A
> task that publishes no timer waits without a deadline and runs no policy turn
> while all wake inputs are silent. Only durable eligible work, a local event,
> stop or a due timer returns control to the policy loop. Direct Consumer work
> uses the same driver with its own result-settled completion predicate, so a
> persistent Consumer returns from `run_work_item()` after that item without
> terminating the task.

Append to the [CC-2.2.1] implementation mapping:

> Interactive command sessions use the same path. The selected runner publishes
> stream-buffer or tracked-runtime state before invoking the required activity
> callback supplied by Consumer. Consumer re-reads the session through
> `CommandSessionProtocol` on the next reactor turn. Interactive resource checks
> remain timers published through `Consumer.next_wait_timeout()`.

### [CC-3.1]

Append to the runner boundary rules:

> An interactive runner receives one required task-local activity callback when
> `start_session()` is called. The runner invokes it only after publishing
> session-observable stream or runtime-exit state. The callback carries no
> payload and grants no queue, TaskSpec, control, or terminal-state authority to
> the runner.

### [RM-5.1]

Append to the enforcement-mechanics list:

> For an interactive command session, Consumer publishes the next resource
> sample through `next_wait_timeout()` and calls the session's `poll_limits()`
> only when that absolute deadline is due. Stream, control, backend, and process
> exit activity do not move the resource-sampling deadline or cause additional
> samples.

### [PY-1]

Append to the extension protocol paragraph:

> `TaskRunnerBackend.start_session(*, on_activity)` requires a zero-argument
> callback and returns `CommandSessionProtocol`. An interactive backend
> publishes session-observable stream or tracked-runtime-exit state before
> invoking the callback. `TaskRunner.start_session()` forwards it unchanged.
> `CommandSessionProtocol` gains no notification registration or wait method.

## Implementation Slices

### S1. Promote the narrow specification delta

Files to modify:

- `docs/specifications/01-Core_Components.md`
- `docs/specifications/06-Resource_Management.md`
- `docs/specifications/14-Python_API_Surfaces.md`
- this plan's Execution Log

Replace the uncommitted broad source-adapter recipe with the exact text above.
Add this plan's backlink beside the touched implementation mappings. Record the
worktree diff base plus spec diff, or the promotion commit SHA, in the Execution
Log.

Gate:

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
git diff --check
```

Independent review must confirm that the promoted text creates no new reactor,
adapter, session, or timer concept before S2 begins.

### S2. Extend the existing session-start contract and wire host events

Files to modify:

- `weft/ext.py`
- `weft/core/tasks/runner.py`
- `weft/core/tasks/interactive.py`
- `weft/core/tasks/debugger.py`
- `weft/core/runners/host.py`
- the four in-repository extension backend files listed under Existing Owners
- `tests/fixtures/public_extension_contract.py`
- `tests/core/test_public_extension_contracts.py`
- `tests/tasks/test_runner.py`
- `tests/tasks/test_task_interactive.py`
- `tests/core/test_debugger.py`

Write failing tests first, then:

1. Add required `on_activity` to `TaskRunnerBackend.start_session()` and
   `TaskRunner.start_session()`.
2. Update the public extension fixture and all in-repository backend methods.
   Unsupported interactive backends retain their existing error.
3. Pass the retained strategy callback from `Consumer._handle_work_message()`
   through the existing interactive helper calls into session creation.
   Update Debugger's override to accept and intentionally ignore the callback;
   its in-process publication completes synchronously on the reactor owner.
4. In HostRunner, notify after each stdout/stderr queue publication, including
   EOF, and after one private `Popen.wait()` returns.

Required proofs:

- the public-only backend receives the callback through `TaskRunner`;
- host output is present in the existing session buffer before the callback is
  observed;
- separate quiet stdout-only and stderr-only children wake and drain through
  the real retained strategy without broker traffic; and
- a tracked child that exits while a descendant retains a pipe for longer than
  `INTERACTIVE_OUTPUT_DRAIN_TIMEOUT` wakes and reaches terminal state before
  descendant exit or EOF. Capture the descendant PID and kill/reap it in test
  cleanup so failure cannot leak a process.

Do not add `ActivityQueue`, `set_activity_callback`, a new session protocol
method, or a reusable process observer.

### S3. Publish the resource timer and remove the cap

Files to modify:

- `weft/core/tasks/interactive.py`
- `weft/core/tasks/consumer.py`
- `tests/tasks/test_task_interactive.py`

Write deterministic deadline tests first. Store the absolute interactive
limit-check deadline, publish its remaining time from
`Consumer.next_wait_timeout()`, and gate `poll_limits()` on that deadline. Use a
controlled monotonic clock for exact timer tests; wall-clock tests assert
observable wake/terminal behavior rather than a narrow remainder.

After output, exit, and limit inputs all have their owning paths, delete the
Consumer protected-wait override, its constant import, and the cap-specific
test. Keep other uses of `ACTIVE_CONTROL_POLL_INTERVAL` unchanged.

Required proofs:

- an idle interactive session schedules policy at its configured resource
  interval rather than 50 ms;
- output turns do not cause extra `poll_limits()` calls or move the deadline;
- a due limit check still produces the existing killed state and event; and
- `Consumer.__dict__` no longer owns `_wait_for_reactor_activity`.

### S4. Restore focused architecture coverage and close the slice

Files to modify:

- `tests/specs/test_reactor_architecture.py`
- this plan's Execution Log and Deviation Log

Restore the independent PING-owner and retired-reactor-cap tests deleted by
`54874c2f`. Do not restore the exact global inventory of every
`wait_for_activity()` spelling. Keep the narrow Consumer ownership assertion
from S3 and the behavioral event/timer proofs as the regression boundary.

Run SQLite and PostgreSQL verification from the current tree. Review the final
diff against [IMPL.10] and the promoted specs. Record independent implementation
review and disposition every finding before completion.

## Verification

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/core/test_public_extension_contracts.py tests/tasks/test_runner.py tests/tasks/test_task_interactive.py tests/tasks/test_task_execution.py -q
./.venv/bin/python -m pytest
bin/pytest-pg --fast
./.venv/bin/ruff check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py
git diff --check
```

Do not run pytest with `-n 0`. Use the repository's configured parallelism. Use
`bin/pytest-pg` as the PostgreSQL owner rather than treating a Weft install as a
SimpleBroker release gate.

The latency contract is one retained-strategy pass at the configured quiet
cadence, nominally 100 ms, followed by burst behavior after useful work. Tests
must allow scheduler and backend variance and must not encode an exact
wall-clock remainder across a real PostgreSQL reactor turn.

## Rollout and Rollback

This is one coordinated Weft contract release. Update core, the public fixture,
and all in-repository runner implementations together. There is no compatibility
shim and no mixed-signature runtime path.

S2 and S3 are not independently revertible after the cap is removed. To roll
back:

1. restore the Consumer 50 ms cap first;
2. revert the required callback and host notification wiring;
3. revert the interactive resource deadline gating; and
4. revert the promoted spec text as one contract correction.

A single revert of the completed implementation commit is preferred. There is
no persisted data migration, queue-format change, or cleanup migration.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|
| [CC-2.3] | Preserve the host reader queues and notify after each published chunk. | The same queues and notification order are used, and the host reader changed from bounded `read()` to bounded `readline()`. | `TextIOWrapper.read(size)` withheld a short flushed line until the buffer filled or EOF. The documented interactive contract is line-oriented, so bounded `readline()` makes a complete line observable at the event boundary without adding a source or buffer concept. | None. This conforms to the existing line-oriented contract. |

## Decision Log

| Date | Decision |
|---|---|
| 2026-09-19 | Treat the interactive 50 ms cap as a temporary liveness repair, then connect its actual local events and timer before deleting it. |
| 2026-09-21 | Extend existing `TaskRunnerBackend.start_session()` with one required construction-time callback. Do not add a protocol, callback setter, optional capability, or compatibility path. |
| 2026-09-21 | Keep ordinary session stream queues authoritative. Reader threads publish first and invoke the shared callback second; no notifying queue type is introduced. |
| 2026-09-21 | Observe one fixed host `Popen` with a private blocking wait. Share the local-notification rule with Manager, not Manager's dynamic sentinel implementation. |
| 2026-09-21 | Keep resource sampling on the existing reactor timer contract with one absolute deadline. Activity does not manufacture samples. |
| 2026-09-21 | Remove the generalized source-adapter recipe from the spec delta. Preserve the existing three input classes and record only the interactive mapping and runner callback contract. |
| 2026-09-21 | Restore only architecture gates that protect stable ownership rules. Do not restore a brittle exact inventory of wait call spellings. |

## Execution Log

- 2026-09-21 author revision: replaced `ActivityQueue`, post-construction
  callback registration, optional fallback, and generic adapter machinery with
  one required callback on the existing runner operation.
- Spec promotion baseline: committed base
  `a599af4b3aafd4c7f0d96a03106f7c6cfad8ec96` plus the uncommitted spec diff
  whose SHA-256 is
  `efb5d81ccd10110e81b91a29b093bd69e3d4dec91b68eeafacee0f7481c5d821`.
- 2026-09-21 independent plan review: PASS after adding the Debugger override,
  discriminating/self-cleaning inherited-pipe proof, separate stdout/stderr
  proofs, post-check timer advancement, and exact Consumer shutdown wording.
- S2 result: the required callback now flows through the existing public
  `start_session()` operation. Host stdout, stderr, EOF, and tracked-process
  exit publish state before notifying the retained strategy. Debugger accepts
  and intentionally ignores the callback because its publication is
  synchronous on the reactor owner.
- S3 result: Consumer publishes one absolute interactive resource deadline,
  samples only when it is due, advances it after the check returns, and no
  longer overrides `_wait_for_reactor_activity()`.
- S4 result: restored the PING-owner and retired-cap architecture gates. Added
  SQLite and PostgreSQL proofs for stdout, stderr, and tracked-process exit
  while a live descendant holds inherited pipes. The public contract,
  deterministic timer composition, limit violation, and callback identity all
  have firing coverage.
- Verification from the completed tree:
  - SQLite full suite: `5190 passed, 28 skipped`;
  - PostgreSQL fast shared suite: `5041 passed, 13 skipped`;
  - focused PostgreSQL post-review tests: `5 passed`;
  - Ruff: all checks passed;
  - mypy: no issues in 435 source files;
  - plan/spec hygiene: `6 passed`; and
  - `git diff --check`: clean.
- 2026-09-21 independent implementation review: PASS after three test-only
  findings were resolved. The final tests assert the exact retained-strategy
  callback identity, exact timer composition under a controlled clock, and
  acquire the inherited-pipe descendant before the fallible drive so cleanup
  remains self-contained on failure. The reviewer found no product-code race
  or architecture defect.
- 2026-09-21 follow-up review: made the fixed-process observer notify from
  `finally`, matching the stream readers, so an observation failure cannot
  suppress the wake hint that makes Consumer re-read authoritative state.
- 2026-09-21 closeout review: narrowed the resource-timer contract to the
  built-in Debugger case the implementation can prove. Unmonitored TaskRunner
  sessions also publish no sampling deadline; monitored TaskRunner sessions
  keep the existing deadline. Manager idle accounting now treats only parsed
  control requests as activity, so replies and malformed rows do not extend
  manager life. The muted-callback test waits for a child-written marker before
  asserting non-delivery, which proves the event occurred instead of allowing
  a scheduling false pass. Focused SQLite tests passed (`29 passed`), focused
  PostgreSQL tests passed (`27 passed`), and Ruff, targeted mypy, and
  `git diff --check` passed.
- Implementation and verification completed as one coordinated contract
  change.
