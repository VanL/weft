# Manager And BaseTask Exception Hardening

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Narrow the broad `except Exception` catches in `weft/core/manager.py` and
`weft/core/tasks/base.py` so Weft keeps swallowing only true best-effort
cleanup and broker/process boundary failures, while unexpected programmer bugs
in load-bearing lifecycle code become visible again. The point is not to make
these files exception-free. The point is to make fatal vs best-effort behavior
explicit and defensible.

## 2. Source Documents

Source specs:
- `docs/specifications/01-Core_Components.md` [CC-2.2], [CC-2.4], [CC-2.5],
  [CC-3.2], [CC-3.4]
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-1.5],
  [MA-1.6], [MA-2], [MA-3]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-2], [MF-3], [MF-5],
  [MF-6], [MF-7]
- `docs/specifications/06-Resource_Management.md` [RM-5]
- `docs/specifications/07-System_Invariants.md` `STATE.1`-`STATE.5`,
  `QUEUE.1`-`QUEUE.6`, `OBS.1`-`OBS.8`, `MANAGER.1`-`MANAGER.7`,
  `IMPL.5`-`IMPL.6`

Related current plans:
- `docs/plans/2026-04-15-maintainability-and-boundary-remediation-plan.md`
  (umbrella remediation plan; still active)

Local guidance:
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

No dedicated spec currently defines the internal exception taxonomy for
best-effort queue writes, process-title updates, cleanup, or runtime-handle
shutdown. This plan is therefore a hardening/refactor slice constrained by the
existing lifecycle and queue-behavior specs above. Do not quietly turn it into
a behavior change.

## 3. Context and Key Files

Validated current inventory:
- `weft/core/manager.py` currently has 25 broad `except Exception` catches.
- `weft/core/tasks/base.py` currently has 21 broad `except Exception` catches.
- The catches are not equivalent. Some are real best-effort cleanup at a queue,
  OS-process, or optional-observability boundary. Others sit close enough to
  load-bearing lifecycle code that swallowing arbitrary exceptions hides bugs.

Files to modify:
- `weft/core/manager.py`
- `weft/core/tasks/base.py`
- targeted tests in `tests/core/test_manager.py`
- targeted tests in `tests/tasks/test_runner.py`,
  `tests/tasks/test_task_observability.py`, and/or nearby task-runtime tests
- `docs/lessons.md`

Read first:
- `weft/core/manager.py`
- `weft/core/tasks/base.py`
- `weft/core/tasks/consumer.py`
- `tests/core/test_manager.py`
- `tests/tasks/test_runner.py`
- `tests/helpers/weft_harness.py`
- `simplebroker.ext` in the installed package for exported broker exception
  types (`BrokerError`, `OperationalError`, `IntegrityError`, `DataError`,
  `MessageError`, `QueueNameError`, `TimestampError`)

Current structure to understand before editing:
- `Manager` owns registry publication, child launch, autostart replay,
  leadership checks, drain/stop, and idle shutdown. Broad catches today appear
  in registry writes/deletes, inbox seeding, autostart file reads, child reap,
  broker idle probing, message acknowledgement, and best-effort `atexit`
  cleanup.
- `BaseTask` owns queue caching, state-log writes, control responses,
  runtime-handle shutdown, process-title updates, reserved-queue policy,
  streaming session markers, and cleanup of queues/spilled output. Broad
  catches today appear in queue close/write/delete paths, optional resource
  monitor callbacks, optional `setproctitle` integration, runner-handle stop,
  reserved-queue maintenance, and spill cleanup.

Shared paths to reuse:
- `simplebroker.ext` exported broker exceptions for broker-backed operations
- `contextlib.suppress(Exception)` only where the boundary is truly destructor
  or best-effort finalization and there is no caller to report to
- existing harness and runtime tests instead of new synthetic helpers whenever
  broker-backed lifecycle proofs are practical

Comprehension questions for implementers:
1. Which catches protect optional observability or cleanup only, and which ones
   sit on the durable lifecycle path where unexpected bugs should surface?
2. Which queue operations are advisory side effects, and which ones are part of
   the contract that keeps manager drain, task control, or task state correct?

## 4. Invariants and Constraints

This slice must preserve:
- TID generation, format, and immutability
- forward-only state transitions and existing terminal-state semantics
- queue names, reserve/claim semantics, and manager registry payload shape
- spawn-based process behavior for broker-connected code
- process-title sanitization and current optional-dependency behavior
- current public CLI and queue-visible behavior

Review gates:
- no queue-name, payload-shape, or CLI contract change
- no new dependency
- no big-bang refactor of `Manager` or `BaseTask`
- no helper abstraction just to avoid writing two or three explicit catches
- no swallowing of unexpected internal state/logic bugs merely to keep tests
  green

Explicit out of scope:
- redesigning manager/task ownership boundaries
- rewriting the runtime shutdown model
- replacing every broad catch in the repo
- changing watchdog timing or queue-wait behavior

Rollback principle:
- every catch change must be independently revertible
- do not mix exception-hardening with unrelated code motion
- if a narrowed catch causes unexpected cross-backend failures, revert that
  local classification instead of widening unrelated boundaries

## 5. Review And Rollout Strategy

This is boundary hardening on the durable spine. Independent review is
required:
1. after this plan is written
2. after the `BaseTask` slice
3. after the `Manager` slice

Prefer a different agent family than the author when one is available. If only
the same family is available, note that limitation in the review notes.

Rollout order:
1. classify every broad catch first
2. harden `BaseTask` next because its boundaries are narrower and more local
3. harden `Manager` after the task-side categories are stable
4. then update lessons and close the slice with targeted review

Stop and re-evaluate if:
- a catch cannot be narrowed without first changing public behavior or queue
  contracts
- the implementation wants a shared exception-wrapper helper that hides local
  semantics
- the real proof seems to require heavy mocking because the local boundary is
  not yet understood

## 6. Tasks

1. Build a catch inventory and classify each site before editing.
   - Files:
     - `weft/core/manager.py`
     - `weft/core/tasks/base.py`
   - Required action:
     - classify each broad catch into one of these buckets:
       - broker/backend boundary
       - JSON or payload parse/validation
       - OS/process lifecycle
       - optional observability integration
       - destructor/atexit best-effort finalization
       - incorrect broad catch that should no longer swallow arbitrary errors
     - use `simplebroker.ext` exported exception types where they actually map
       to the queue/broker operation being guarded
     - prefer operation-local built-ins (`json.JSONDecodeError`, `ValueError`,
       `TypeError`, `OSError`, `ProcessLookupError`, `PermissionError`) when
       the failure source is not the broker itself
   - Done when:
     - every current broad catch has an explicit classification and there is no
       “we will figure it out while editing” bucket

2. Harden `BaseTask` first, without changing behavior.
   - Files:
     - `weft/core/tasks/base.py`
     - nearby task-runtime tests
   - Required action:
     - narrow queue close/write/delete catches to broker-facing exception sets
       that match the real operation
     - keep optional integrations best-effort, but make the reason explicit:
       `setproctitle`, spill cleanup, streaming-session cleanup, resource
       monitor callbacks, and destructor cleanup should not take down the task
     - remove any broad catch that is only masking internal logic errors close
       to lifecycle/state transitions
     - if one destructor-style boundary still truly needs
       `contextlib.suppress(Exception)`, keep it narrow and comment why
   - Tests to add or update:
     - prove best-effort queue/cleanup failures do not clobber the primary task
       outcome
     - prove unexpected non-boundary exceptions propagate where the code now
       intends them to
   - Stop gate:
     - if the slice starts changing terminal-state behavior or reserved-queue
       policy, stop and re-plan

3. Harden `Manager` after the task-side categories are stable.
   - Files:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
   - Required action:
     - narrow registry, inbox-seeding, idle-probe, autostart file-read, child
       reap, and `atexit` catches based on the same classification rules
     - keep best-effort registry and shutdown cleanup best-effort where the
       spec allows it, but stop swallowing arbitrary internal bugs in child
       spawn/validation and state transitions
     - do not silently broaden `ValueError`/parse handling around
       `_build_child_spec()` or autostart manifests beyond the actual malformed
       input cases
   - Tests to add or update:
     - manager registry cleanup still stays best-effort on broker failures
     - malformed autostart/spec payloads still fail as warnings/rejections
     - unexpected injected internal exceptions on lifecycle paths are no longer
       hidden
   - Stop gate:
     - if a change needs manager/control-flow redesign instead of local catch
       hardening, stop and split that work into a separate plan

4. Update lessons and close with a targeted review pass.
   - Files:
     - `docs/lessons.md`
   - Required action:
     - record the accepted exception taxonomy and the narrow cases where broad
       suppression is still justified
     - document any same-family review limitation if independent review could
       not use a different model family
   - Done when:
     - a reviewer can explain why each remaining broad suppression exists and
       why the rest were removed or narrowed

## 7. Verification

Minimum verification for the slice:
- `./.venv/bin/ruff check weft/core/manager.py weft/core/tasks/base.py docs/lessons.md`
- `./.venv/bin/mypy weft/core/manager.py weft/core/tasks/base.py`
- `./.venv/bin/python -m pytest -n0 tests/core/test_manager.py -q`
- `./.venv/bin/python -m pytest -n0 tests/tasks/test_runner.py tests/tasks/test_task_observability.py -q`

Prefer real broker/runtime proofs over mocks for lifecycle behavior. Use
monkeypatching only to inject the specific boundary failure being classified.
