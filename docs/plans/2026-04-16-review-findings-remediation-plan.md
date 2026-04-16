# Review Findings Remediation Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## 1. Goal

Turn the validated code-review findings into one sequenced remediation slice:
fix the real runtime and CLI correctness bugs first, close the two false
positives with explicit contract coverage instead of risky behavior changes,
then finish with the narrow maintainability and plan-metadata cleanup work that
can safely trail behind the bug fixes.

## 2. Source Documents

Source specs:

- [`docs/specifications/01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.4], [CC-2.5]
- [`docs/specifications/02-TaskSpec.md`](../specifications/02-TaskSpec.md) [TS-1], [TS-1.1], [TS-1.2]
- [`docs/specifications/03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md) [MA-1.4], [MA-1.5], [MA-1.6], [MA-3]
- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md) [MF-2], [MF-3], [MF-5], [MF-6], [MF-7]
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md) [QUEUE.4], [QUEUE.5], [QUEUE.6], [OBS.1], [OBS.2], [OBS.3], [IMPL.1], [MANAGER.1], [MANAGER.6]
- [`docs/specifications/10-CLI_Interface.md`](../specifications/10-CLI_Interface.md) [CLI-1.1.1], [CLI-1.2]

Related current plans:

- [`docs/plans/2026-04-15-maintainability-and-boundary-remediation-plan.md`](./2026-04-15-maintainability-and-boundary-remediation-plan.md) — active umbrella plan; this document narrows one validated review slice out of that broader program.
- [`docs/plans/2026-04-15-docs-audit-and-alignment-plan.md`](./2026-04-15-docs-audit-and-alignment-plan.md) — active docs plan; this document only carries the plan-status bookkeeping needed to close the present review slice.
- [`docs/plans/2026-04-07-active-control-main-thread-plan.md`](./2026-04-07-active-control-main-thread-plan.md) — partially landed rationale for deferred STOP/KILL ownership. Read it before changing active-control semantics.
- [`docs/plans/2026-04-09-manager-lifecycle-command-consolidation-plan.md`](./2026-04-09-manager-lifecycle-command-consolidation-plan.md) — broader manager control-plane cleanup. This plan only fixes the validated idle/autostart/bookkeeping gaps and does not try to finish that whole consolidation.
- [`docs/plans/2026-04-13-result-stream-implementation-plan.md`](./2026-04-13-result-stream-implementation-plan.md) — broader `result --stream` plan. This plan only fixes current streaming-marker lifecycle bugs that already affect runtime truth.

Repo guidance:

- [`AGENTS.md`](../../AGENTS.md)
- [`docs/agent-context/decision-hierarchy.md`](../agent-context/decision-hierarchy.md)
- [`docs/agent-context/principles.md`](../agent-context/principles.md)
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
- [`docs/agent-context/runbooks/runtime-and-context-patterns.md`](../agent-context/runbooks/runtime-and-context-patterns.md)
- [`docs/agent-context/runbooks/writing-plans.md`](../agent-context/runbooks/writing-plans.md)
- [`docs/agent-context/runbooks/hardening-plans.md`](../agent-context/runbooks/hardening-plans.md)
- [`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`](../agent-context/runbooks/review-loops-and-agent-bootstrap.md)
- [`docs/lessons.md`](../lessons.md)

No current product spec exists for:

- plan-status bookkeeping under `docs/plans/`

Treat that part of this work as repo-governance cleanup. Do not pretend it is a
runtime contract.

## 3. Context and Key Files

### Validated Review Disposition

The findings from the deep-read review break down as follows:

- Fix in this slice:
  - Consumer deferred reserved-policy handling for direct `run_work_item()` execution.
  - Manager autostart-state garbage collection for deleted manifests.
  - Manager idle-timeout broker probe freshness.
  - `weft run --spec ...` timeout exit-code mismatch.
  - Persistent live-stream session lifecycle cleanup.
- Address in this slice without changing current behavior:
  - active STOP/KILL early raw `ctrl_in` deletion while public ACK stays deferred
  - append-only manager-registry duplicates that readers are expected to reduce
- Address as maintainability or bookkeeping after correctness is green:
  - `run.py` manager-lifecycle duplication
  - stale `docs/plans/` status metadata

Other adjacent cleanup items discovered while planning:

- `weft/commands/result.py` and the related specs still have broader
  `result --stream` cleanup pending in
  [`2026-04-13-result-stream-implementation-plan.md`](./2026-04-13-result-stream-implementation-plan.md).
  This slice should only pull that work in when the current streaming-marker
  fix materially touches the same behavior boundary.
- manager-reader stale-record cleanup remains broader in
  [`2026-04-09-manager-lifecycle-command-consolidation-plan.md`](./2026-04-09-manager-lifecycle-command-consolidation-plan.md).
  This slice should only absorb the narrow parts required by the validated
  broker-freshness or registry-clarification work.
- touched specs must refresh nearby implementation mappings and plan backlinks
  in the same patch. Do not land code fixes while leaving neighboring
  traceability stale.

### Files To Modify

Runtime correctness:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `weft/commands/result.py` only if stale streaming markers still affect reader surfaces after the producer fix
- `weft/commands/run.py`
- `tests/tasks/test_task_execution.py`
- `tests/core/test_manager.py`
- `tests/commands/test_run.py`
- `tests/commands/test_result.py` only if reader-side stale-stream filtering is needed

Contract clarification and non-regression coverage:

- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/07-System_Invariants.md` only if implementation notes need tightening, not to change the contract
- `docs/lessons.md`

Plan bookkeeping:

- `docs/plans/README.md`
- plan files in `docs/plans/` whose status can be proved from landed code/spec/tests

### Read First

- `weft/core/tasks/consumer.py`
  - owns direct work-item execution, deferred active STOP/KILL finalization, and live command streaming.
- `weft/core/tasks/base.py`
  - owns reserved-policy application, control ACK writes, and streaming-session bookkeeping.
- `weft/core/manager.py`
  - owns autostart scan state, broker-activity tracking, idle-timeout decisions, and registry writes.
- `weft/commands/run.py`
  - owns the three manager-backed run modes whose lifecycle skeleton is duplicated today.
- `weft/commands/result.py`
  - owns `weft.state.streaming` reader interpretation for result surfaces.
- `tests/tasks/test_task_execution.py`
  - current task-level output, reserved-queue, and streaming-session proofs.
- `tests/core/test_manager.py`
  - current manager autostart, idle-timeout, and child-lifecycle proofs.
- `tests/commands/test_run.py`
  - current manager submission, wait, and exit-code command coverage.
- `docs/plans/README.md`
  - current plan index and status taxonomy.

### Style And Guidance

- `AGENTS.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/testing-patterns.md`

### Shared Paths And Helpers To Reuse

- `weft/core/tasks/consumer.py` :: `_finalize_deferred_active_control()`, `_finalize_message()`, `run_work_item()`
- `weft/core/tasks/base.py` :: `_apply_reserved_policy()`, `_begin_streaming_session()`, `_end_streaming_session()`
- `weft/core/manager.py` :: `_read_broker_timestamp()`, `_update_idle_activity_from_broker()`, `_tick_autostart()`
- `weft/commands/run.py` :: `_ensure_manager_after_submission()`, `_wait_for_task_completion()`
- `weft/commands/result.py` :: `_active_streaming_queues()`
- `weft.helpers` :: `iter_queue_json_entries()`, `pid_is_live()`

### Current Structure

- `Consumer._finalize_deferred_active_control()` applies reserved policy using
  `self._active_message_timestamp`. Queue-driven work sets that field. Direct
  `run_work_item()` execution does not.
- `BaseTask._apply_reserved_policy()` treats `message_timestamp=None` as a
  whole-queue operation for `CLEAR` and `REQUEUE`.
- During active STOP/KILL, `Consumer._poll_active_control_once()` deletes the
  raw `ctrl_in` message immediately, but public ACK on `ctrl_out` is emitted
  later on the main task thread when `_finalize_deferred_active_control()`
  runs.
- `Manager._read_broker_timestamp(force=True)` bypasses the manager's own
  throttle but still reads `Queue.last_ts`, which is cached by SimpleBroker
  until explicitly refreshed.
- `Manager._tick_autostart()` keys `_autostart_state` and `_autostart_launched`
  by manifest source and never prunes deleted manifests.
- Live command streaming opens a `weft.state.streaming` entry before chunk
  emission, but the persistent successful completion path returns to `waiting`
  before `_end_streaming_session()` runs.
- `weft/commands/result.py` currently treats `weft.state.streaming` as a live
  ownership signal and skips those outboxes in `--all` style aggregation.
- `_run_inline()`, `_run_spec_via_manager()`, and `_run_pipeline()` repeat
  the manager bootstrap -> submit -> optional wait -> conditional manager stop
  skeleton with different local payload shaping and error text.

### Comprehension Checks

1. Which queue or log is the public proof that a task reached terminal state,
   and why is raw `ctrl_in` deletion not itself a public ACK?
2. When does `_apply_reserved_policy()` operate on one reserved message versus
   the whole reserved queue, and which paths legitimately have no active
   reserved message at all?
3. Why can a forced broker-activity check still return stale data today even
   though the manager bypasses its own `_broker_probe_interval_ns` gate?
4. Which existing proposed plans are broader than this slice, and which review
   claims should be closed with characterization coverage rather than runtime
   behavior changes?

## 4. Invariants and Constraints

- Keep one durable execution spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Preserve TID generation, TID immutability, and forward-only state
  transitions.
- Preserve reserved-queue semantics for queue-driven work. The direct
  `run_work_item()` fix must not weaken the normal exact-message reserved path.
- Preserve the current active STOP/KILL ownership rule: terminal task state,
  reserved-policy application, and public `ctrl_out` ACK remain main-thread
  work after the runtime unwinds.
- Do not change active STOP/KILL into "ACK first, finalize later." The review
  finding that asks for earlier public ACK is rejected for this slice.
- Preserve manager-registry append-only semantics. Raw duplicate `active` and
  `stopped` entries are acceptable history; readers must continue reducing to
  the latest relevant record per `tid`.
- Keep `weft.state.streaming` runtime-only. Fix stale or leaked entries by
  producer cleanup and reader tolerance, not by making the queue durable state.
- The manager idle-timeout fix must refresh broker activity without changing
  ordinary probe throttling for the non-forced path.
- The autostart cleanup must not change the existing success boundary: launch
  and ensure-state accounting still advance only after the synthesized spawn
  request is successfully written.
- The `run.py` extraction must stay narrow. Share only the manager-lifecycle
  skeleton. Keep mode-specific input shaping, interactive handling, pipeline
  compilation, and user-facing error text local.
- No new dependency.
- No queue-name change, payload-format change, or second control plane.
- No drive-by rewrite of `Manager`, `Consumer`, `BaseTask`, or `run.py`.
- Do not relabel historical plans from `proposed` to `completed` without proof
  from landed code, nearby spec updates, and relevant tests.

Out of scope:

- redesigning STOP/KILL semantics around earlier public ACK
- rewriting manager registry storage into a mutable snapshot model
- broader `result --stream` UX or protocol changes beyond stale-stream truth
- broad CLI cleanup unrelated to the duplicated manager-lifecycle skeleton
- plan-corpus automation beyond status cleanup and index synchronization

## 5. Review and Rollout Strategy

This work crosses runtime control, manager lifecycle, result observation, CLI
exit behavior, and plan metadata. Independent review is required:

1. after the characterization tests and bug-fix plan are written
2. after the runtime correctness slice lands
3. after the `run.py` maintainability slice lands
4. again before plan-status relabeling is treated as complete

Prefer a different agent family than the author when one is available. If only
the same family is available, record that limitation in review notes.

Rollout order:

1. land characterization tests first, including tests that prove which claimed
   issues are not actual contract bugs
2. land the `Consumer` and `Manager` correctness fixes next
3. land the streaming-session cleanup and any needed reader tolerance after the
   producer-side bug is green
4. land the `run.py` exit-code fix before the lifecycle extraction
5. perform the plan-status audit last, after the code and spec proof is in place

Rollback principle:

- keep runtime bug fixes independently revertible from the `run.py`
  maintainability extraction
- keep plan-status edits independently revertible from runtime changes
- do not mix broad code motion with correctness changes in the same commit

## 6. Tasks

1. Lock the validated bugs and non-bugs with characterization coverage first.
   - Outcome:
     - the real runtime defects are pinned by failing tests, and the two
       rejected review claims are covered by explicit characterization tests or
       contract notes so they do not keep reappearing as "fixes."
   - Files to touch:
     - `tests/tasks/test_task_execution.py`
     - `tests/core/test_manager.py`
     - `tests/commands/test_run.py`
     - `tests/commands/test_result.py` only if stale-stream reader behavior is part of the fix
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/03-Manager_Architecture.md`
   - Read first:
     - `weft/core/tasks/consumer.py`
     - `weft/core/tasks/base.py`
     - `weft/core/manager.py`
     - `weft/commands/run.py`
     - `weft/commands/result.py`
   - Required action:
     - add a regression showing deferred STOP/KILL finalization during
       `run_work_item()` does not clear or requeue unrelated reserved messages
       when no work item was reserved from the inbox
     - add a manager regression showing a forced broker probe sees an external
       queue write even after `Queue.last_ts` was previously cached
     - add a timeout exit-code regression for `_run_spec_via_manager()`
     - add a persistent streaming regression showing successful streamed work
       items clear their session marker before the task returns to `waiting`
     - add characterization coverage or a nearby spec note showing that active
       STOP/KILL may delete raw `ctrl_in` early while public ACK remains
       deferred to main-thread finalization
     - add characterization coverage or a nearby spec note showing that manager
       registry history is append-only and raw duplicate records are reduced by
       readers rather than rewritten away
   - Keep real:
     - broker-backed queues
     - manager idle-timeout logic
     - reserved-queue behavior
     - task-log and `ctrl_out` surfaces
   - Stop if:
     - the tests need a mock-only substitute for queue reservation or broker
       timestamp behavior when a real queue-backed proof is practical
   - Done when:
     - the red tests isolate the intended behavior sharply enough that the code
       changes can stay minimal

2. Fix Consumer deferred reserved-policy handling for direct work-item execution.
   - Outcome:
     - `run_work_item()` no longer risks clearing or requeueing unrelated
       reserved work when STOP/KILL finalizes without an active reserved
       message.
   - Files to touch:
     - `weft/core/tasks/consumer.py`
     - `tests/tasks/test_task_execution.py`
   - Read first:
     - `Consumer.run_work_item()`
     - `Consumer._finalize_deferred_active_control()`
     - `BaseTask._apply_reserved_policy()`
   - Required action:
     - treat `_active_message_timestamp is None` as "no reserved message is
       currently owned" on the direct work-item path
     - skip reserved-queue mutation in that case while still publishing the
       terminal state and public control ACK on the main thread
     - do not reuse the control-message timestamp as a fake reserved-message ID
     - keep the queue-driven exact-message policy path unchanged
   - Stop if:
     - the implementation starts adding synthetic timestamps or a second direct
       control-flow path rather than tightening the existing finalization path
   - Done when:
     - direct execution preserves unrelated reserved entries and queue-driven
       execution still applies policy to the exact reserved message

3. Fix manager broker-activity freshness and autostart-state pruning.
   - Outcome:
     - idle-timeout checks see fresh broker activity on forced re-checks, and
       deleted autostart manifests stop accumulating stale in-memory state.
   - Files to touch:
     - `weft/core/manager.py`
     - `tests/core/test_manager.py`
     - `docs/specifications/03-Manager_Architecture.md` if the current
       implementation note needs clarifying text
     - `docs/specifications/02-TaskSpec.md` if the autostart implementation
       snapshot needs to mention pruning of removed manifest sources
   - Read first:
     - `Manager._read_broker_timestamp()`
     - `Manager._update_idle_activity_from_broker()`
     - `Manager._tick_autostart()`
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.5], [MA-1.6]
     - `docs/specifications/02-TaskSpec.md` [TS-1.2]
   - Required action:
     - on forced broker probes, explicitly refresh the broker timestamp instead
       of relying on cached `Queue.last_ts`
     - keep the non-forced probe path throttled as it is today
     - prune deleted manifest sources from `_autostart_state` and
       `_autostart_launched` during scan, without disturbing still-running
       active children
     - preserve the current enqueue-success boundary for autostart launch and
       ensure accounting
   - Error-path priorities:
     - stale broker activity is correctness-critical for idle shutdown
     - autostart pruning is a bookkeeping fix and must not downgrade a
       successful launch path
   - Stop if:
     - the fix starts redesigning registry history or autostart policy instead
       of addressing the validated freshness and pruning gaps
   - Done when:
     - a forced broker re-check sees external activity immediately
     - deleted manifests no longer leave durable in-memory entries behind

4. Fix streaming-session lifecycle for persistent streamed work items and add
   stale-marker tolerance only if producer cleanup is insufficient.
   - Outcome:
     - successful persistent streamed work items close their
       `weft.state.streaming` marker promptly, and dead stale markers do not
       mislead result surfaces indefinitely.
   - Files to touch:
     - `weft/core/tasks/consumer.py`
     - `weft/commands/result.py` only if stale-reader tolerance is needed
     - `tests/tasks/test_task_execution.py`
     - `tests/commands/test_result.py` only if reader logic changes
     - `docs/specifications/01-Core_Components.md` if the streaming-marker note
       needs a tighter implementation snapshot
   - Read first:
     - `Consumer._run_task()`
     - `Consumer._finalize_message()`
     - `BaseTask._begin_streaming_session()`
     - `BaseTask._end_streaming_session()`
     - `weft/commands/result.py` :: `_active_streaming_queues()`
   - Required action:
     - end live command streaming sessions on the persistent successful
       completion path before the task returns to `waiting`
     - preserve the current timeout, error, cancel, and kill cleanup paths
     - only add reader-side stale-marker filtering if a dead producer can still
       leave result surfaces materially wrong after the producer fix
     - if reader filtering is needed, use durable task status or PID/liveness
       corroboration rather than treating a raw runtime-only queue entry as
       authoritative forever
   - Stop if:
     - the fix starts building a new stale-session recovery subsystem or
       persistent metadata store
   - Done when:
     - successful persistent streamed work items clear their session marker
     - `weft.state.streaming` no longer makes a dead task look actively
       streaming for ordinary result surfaces

5. Fix timeout exit-code consistency, then extract the narrow shared
   manager-lifecycle skeleton from `run.py`.
   - Outcome:
     - all three manager-backed run paths return `124` for timeout, and the
       shared bootstrap -> submit -> wait -> optional stop skeleton lives in one
       helper without flattening the mode-specific paths.
   - Files to touch:
     - `weft/commands/run.py`
     - `tests/commands/test_run.py`
     - `docs/specifications/07-System_Invariants.md` only if the implementation
       mapping note needs a refreshed owner path
   - Read first:
     - `_run_inline()`
     - `_run_spec_via_manager()`
     - `_run_pipeline()`
     - `docs/plans/2026-04-15-maintainability-and-boundary-remediation-plan.md`
   - Required action:
     - land the exit-code fix first, with a direct regression
     - extract only the manager-owned skeleton:
       ensure-manager-after-submission, started-here tracking, reuse-aware stop
       cleanup, and resolved status/error handoff
     - keep payload shaping, spec materialization, interactive handling,
       pipeline compilation, and user-facing message text local to each mode
     - do not silently normalize the current exception asymmetries between the
       three modes unless a test and an explicit contract decision say to do so
   - Stop if:
     - the helper starts pulling mode-specific parsing or output rendering into
       a generic wrapper just to reduce line count
   - Done when:
     - timeout exit codes are consistent
     - the repeated lifecycle skeleton is shared through one narrow helper
       without changing public behavior outside the intended timeout fix

6. Close the false positives with explicit contract documentation and durable
   lessons.
   - Outcome:
     - future reviewers have a short canonical explanation for why these two
       claims are not current bugs, and the codebase carries enough nearby
       contract text that the same confusion is less likely to recur.
   - Files to touch:
     - `docs/specifications/05-Message_Flow_and_State.md`
     - `docs/specifications/03-Manager_Architecture.md`
     - `docs/lessons.md`
   - Read first:
     - `docs/specifications/05-Message_Flow_and_State.md` [MF-3], [MF-7]
     - `docs/specifications/03-Manager_Architecture.md` [MA-1.4], [MA-3]
     - `docs/plans/2026-04-07-active-control-main-thread-plan.md`
   - Required action:
     - document that active STOP/KILL raw control-message deletion is an
       implementation detail and that public ACK plus terminal state remain
       main-thread-owned post-unwind surfaces
     - document that manager registry history is append-only and that readers
       are responsible for reducing the latest relevant record
     - add a short durable lesson if the implementation review confirms these
       misunderstandings were easy to make from a cold read
   - Stop if:
     - this turns into a spec rewrite of STOP/KILL or registry semantics rather
       than a clarification of current behavior
   - Done when:
     - the current contract is easier to recover from nearby docs than from
       code archaeology alone

7. Audit and update plan-status metadata with proof, not guesswork.
   - Outcome:
     - the overlapping plans touched by this review slice have accurate status
       and supersession metadata, and the plan index remains synchronized.
   - Files to touch:
     - `docs/plans/README.md`
     - any plan file whose status can be proved from landed code/spec/tests
   - Read first:
     - `docs/plans/README.md`
     - the overlapping plans listed in Source Documents
     - the touched specs and tests from earlier tasks
   - Required action:
     - update status or supersession markers only when the implementation proof
       is explicit
     - start with the plans directly overlapped by this slice rather than
       attempting a one-pass archaeology sweep of the whole corpus
     - keep the README index count, titles, and statuses synchronized with the
       file headers you edit
     - if a plan is only partially landed and no clean closure signal exists,
       leave it `proposed` and add a more precise `Superseded by` or note
       rather than guessing at `completed`
   - Stop if:
     - the work becomes a broad metadata migration with no objective proof for
       individual status changes
   - Done when:
     - the plan files directly implicated by this slice no longer advertise
       stale status metadata

8. Run a residual cleanup pass on directly touched boundaries before closing the slice.
   - Outcome:
     - adjacent cleanup items in the same files and specs are either folded into
       the landed work or explicitly recorded as follow-up work rather than
       being left half-addressed.
   - Files to touch:
     - whichever files changed in Tasks 2 through 7
     - `docs/plans/README.md` if a new follow-up plan or status note is needed
   - Read first:
     - the overlapping plans named in Source Documents
     - the touched spec sections and their implementation-mapping notes
   - Required action:
     - after each main code slice, scan the touched module and nearby spec text
       for small now-obvious cleanup that is directly required by the same
       behavior boundary
     - fold in adjacent cleanup only when it is local, testable, and does not
       widen the contract beyond this plan
     - if the cleanup is real but too broad for the current slice, record it
       explicitly in the final plan-status audit instead of silently leaving the
       drift in place
     - refresh nearby spec backlinks, implementation mappings, and plan-index
       entries whenever the owner path changes
   - Stop if:
     - the cleanup pass starts importing whole sections of the broader
       `result --stream` or manager-lifecycle consolidation plans without a
       clear local need
   - Done when:
     - there are no obvious unaddressed cleanup items left in the exact files
       and spec sections touched by this slice

## 7. Testing Plan

Run in order, using the repo-managed environment:

1. `. ./.envrc`
2. `./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/core/test_manager.py tests/commands/test_run.py -q`
3. `./.venv/bin/python -m pytest tests/commands/test_result.py -q` if reader-side stale-stream logic changes
4. `./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/cli/test_cli_manager.py tests/cli/test_status.py -q` if the characterization coverage for the rejected claims or the manager-reader notes changes
5. `./.venv/bin/python -m mypy weft extensions/weft_docker extensions/weft_macos_sandbox`
6. `./.venv/bin/python -m ruff check weft`

Keep real:

- broker-backed queue timestamps and reservations
- manager idle-timeout and autostart scans
- `ctrl_out` and `weft.log.tasks` public surfaces
- result-stream marker interpretation

## 8. Review Note

Independent review is required before implementation starts. The reviewer
should read this plan together with:

- `weft/core/tasks/consumer.py`
- `weft/core/tasks/base.py`
- `weft/core/manager.py`
- `weft/commands/run.py`
- `weft/commands/result.py`

Recommended reviewer question:

> Could you implement this confidently and correctly without inventing a second
> control path, registry model, or result-stream recovery subsystem?
