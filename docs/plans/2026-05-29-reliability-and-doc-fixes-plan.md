# Reliability And Doc Fixes Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-5]; docs/specifications/06-Resource_Management.md [RM-0], [RM-1], [RM-5.2]; docs/specifications/07-System_Invariants.md [STATE.1], [STATE.2], [EXEC.3], [MANAGER.8], [OBS.4], [OBS.7]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]
Superseded by: none

## Goal

Fix a small set of concrete reliability defects and documentation-honesty gaps
surfaced by a 2026-05-29 critical code evaluation, without enlarging scope into
refactors the codebase philosophy explicitly rejects. The headline is one real
correctness race: a genuinely-completed task can be reported `failed` when its
durable terminal write lands late, because terminal `ctrl_out` evidence is
reconciled purely latest-timestamp-wins: the manager's terminal-proof visibility
check misses the task's not-yet-visible terminal write and writes a `wrapper_lost`
envelope that lands with a *later* queue timestamp than the task's real
`completed` envelope, so latest-wins picks the manager's and reports `failed` for
a task that actually completed. The recurring
"widen the terminal-proof grace" commits in recent history treat the symptom;
this plan fixes the cause at the read-side reconciliation rule so the grace
constant returns to being a pure liveness knob.

The remaining slices are two sibling runtime races, one behavior-sensitive
de-duplication of a drifted authority predicate, low-risk dead-code/duplication
cleanup, and a set of documentation corrections where the docs currently
describe a boundary that does not exist or oversell an enforcement guarantee.

The triggering evaluation was a review session, not a normative document. Specs
in `docs/specifications/` remain the only source of truth for behavior. Where a
fix changes behavior the spec does not yet describe (Slice 1), this plan
temporarily states the intended spec delta; the slice is not done until the spec
text is updated and the normative boundary is restored.

Strategic note for the author: Slices 1–4 are the concrete path to reducing the
manager/monitor timing-hardening churn that dominates recent history. They are
not a 1.0 gate by themselves; 1.0 readiness is a separate decision. Slice 7
carries two now-decided identity items: the author confirmed the identity is "the
durable task substrate for agent systems" and that the `llm` dependency stays.

## Implementation Record

Completed 2026-05-29. All seven slices landed; the full suite passes
(`./.venv/bin/python -m pytest -q`: 0 failures, 2 environment-gated skips),
`mypy` and `ruff` clean across 176 source files.

- Slice 1: `select_terminal_envelope` in `weft/core/task_evidence.py` routes all
  three terminal-ctrl_out readers (`peek_terminal_ctrl_out_evidence`,
  `weft/commands/_result_wait.py`, `weft/commands/result.py`); `[MF-5]`
  source-precedence sub-rule + backlink. Tests:
  `test_task_terminal_ctrl_out_beats_manager_wrapper_lost`,
  `test_await_one_shot_result_prefers_task_completed_over_manager_wrapper_lost`.
- Slice 2: `weft/core/runners/host.py` re-checks the result queue before declaring
  a function-target timeout. Test:
  `test_function_timeout_honors_result_already_on_queue` (+ negative control).
- Slice 3: `Manager._managed_pids_for_child` resolves PIDs through
  `live_host_processes_from_handle` (create-time gated). Test:
  `test_managed_pids_for_child_excludes_create_time_mismatch`. Spec 03/07 mappings.
- Slice 4: shared `pong_proves_dispatch_eligible` in `weft/core/control_probe.py`;
  both predicates delegate; empty `weft_context` resolves to fallback. Tests in
  `tests/core/test_control_probe.py` (gate table + runtime convergence). Spec
  03/07 mappings + backlink.
- Slice 5: duplicate `_tick_task_monitor` removed (one wrapper remains);
  `_start_manager` de-duplicated via `_acknowledge_competing_and_return` and
  `_reconcile_competing_manager_start`.
- Slice 6: AGENTS.md §4.11 phantom `validate_safe_path` replaced with the real
  argv-based model; stale `weft/helpers.py` paths fixed; RM sample-window caveat;
  Quick-Reference process-title pointer; `docker_args` security note.
- Slice 7: `weft --help`, `weft/__init__.py`, and AGENTS.md §1 identity aligned to
  "the durable task substrate for agent systems"; `llm` documented as intentional.

## Source Documents

Read these before implementation. They define the local engineering taste this
plan must preserve. Do not skim them as a checklist.

- `AGENTS.md`: repo philosophy (`§1.1` "size is not a smell / absence is not a
  gap / patterns have reasons"), file layout, style rules, agent boundaries
  (`§8`). Note `§4.11` is itself edited by Slice 6.
- `docs/agent-context/decision-hierarchy.md`: specs > canonical context > plans
  > root agent files > code patterns. Plans never override specs on behavior.
- `docs/agent-context/principles.md` and
  `docs/agent-context/engineering-principles.md`: extend the existing durable
  spine; queues are canonical state; canonicalize at boundaries; real
  broker/process tests beat mock-heavy tests; keep traceability bidirectional.
- `docs/agent-context/runbooks/writing-plans.md` and
  `docs/agent-context/runbooks/hardening-plans.md`: the standards this plan is
  written against.
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`: the
  required self-review and external-review loop (see Independent Review Loop).
- `docs/lessons.md` (especially `## 2026-05-20 Manager Reuse Replacement Drain`,
  the `## 2026-04-16` exception/sleep/constants lessons, and the
  Terminal Proof Grace lesson) and `docs/agent-context/lessons.md`.

Governing spec sections (exact codes, confirmed present):

- `docs/specifications/05-Message_Flow_and_State.md` [MF-3] terminal envelope
  schema (`source="task"|"manager"`, lines ~163–165, ~180–182), [MF-5] evidence
  reconciliation precedence (lines ~540–550), implementation mapping at
  ~193–196 and ~590–607, `## Related Plans` at ~1104.
- `docs/specifications/07-System_Invariants.md` [STATE.1]/[STATE.2] forward-only
  transitions (lines ~55–58; note the authoritative chain includes the
  intermediate `spawning` state and `cancelled`, not the `created → running`
  shorthand in `AGENTS.md §3`), [EXEC.3] PID publication (lines ~124–129),
  Manager invariants incl. [MANAGER.8] one-leader-per-context (lines ~345–391),
  [OBS.4]/[OBS.7] process-title format and sanitization (lines ~142–148).
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 4 create-time PID
  validation and dispatch-eligibility predicate (lines ~160–182), [MA-1] item 7
  force-reap authority (lines ~278–282), [MA-3] drain/forced-reap on shutdown
  (lines ~399–402), implementation mapping at ~302–309 (MA-1.4 at ~306) and
  ~409–420.
- `docs/specifications/06-Resource_Management.md` [RM-0] monitor-and-react model
  (lines ~26–34), [RM-1]/[RM-2] host psutil vs Docker native (lines ~40–58),
  [RM-5.2] timeout boundary (lines ~128, ~136–137, ~148–150), implementation
  snapshot at ~7–10.

Comprehension questions (you must be able to answer these before editing
runtime code):

1. Which queue/log currently proves a task reached a terminal state, and what
   are the two distinct producers of a terminal `ctrl_out` envelope?
2. In which interleaving does the manager's `wrapper_lost` envelope commit with a
   later queue timestamp than a late-but-real task `completed` envelope (the task
   write lands between the manager's terminal-proof visibility check and its own
   `wrapper_lost` write), and why does latest-timestamp-wins reconciliation then
   invert the verdict?
3. Where is the canonical create-time-validated PID liveness helper, and which
   shutdown code path currently bypasses it?
4. What is the manager dispatch-eligibility predicate for a matched PONG, and in
   which two modules does a near-identical copy of it live today?

If you cannot answer these from the files above, stop and reread before editing.

## Context and Key Files

Ownership map for the surfaces this plan touches (so you do not rediscover it):

- `weft/core/task_evidence.py`: typed task-evidence snapshots and the read-side
  reconciliation helpers. `peek_terminal_ctrl_out_evidence` (around line 448)
  selects the terminal `ctrl_out` envelope by latest timestamp; this is the
  Slice 1 edit point.
- `weft/core/manager.py`: the background manager reactor. `_child_terminal_proof_visible`,
  `_child_terminal_proof_still_within_grace`, and `_write_manager_terminal_envelope`
  (around lines 3017–3132) own the wrapper-lost envelope write; the shutdown
  child-termination path is around lines 3216–3276; `_managed_pids_for_child`
  is around line 5767. One copy of the PONG dispatch-eligibility predicate lives
  around line 2123. The dead/redundant `_tick_internal_services` /
  `_tick_task_monitor` pair is around lines 5473–5491.
- `weft/core/manager_runtime.py`: out-of-process manager lifecycle (launch,
  wait, stop, discovery). `_start_manager` (around lines 1499–1713) is the
  215-line outlier with three near-duplicate competitor-acknowledge blocks. The
  second copy of the PONG predicate (`_matched_pong_proves_manager_record`,
  around line 690) lives here.
- `weft/core/control_probe.py`: shared PONG coercion/probe helpers, already
  imported by both `manager.py` and `manager_runtime.py`. This is the home for
  the shared predicate extracted in Slice 4.
- `weft/core/runners/host.py`: the in-process function/callable runner.
  `run_with_hooks` (non-command branch, around lines 458–475) returns
  `status="timeout"` without re-checking the result queue. The command path in
  `weft/core/runners/subprocess_runner.py` (around lines 176–184) does the
  correct re-poll; mirror it.
- `weft/core/tasks/consumer.py`: maps a runner outcome to a terminal state and
  publishes the result (timeout → `mark_timeout`; completed → `mark_completed`).
- `weft/helpers/__init__.py` and `weft/ext.py`: create-time-validated PID
  liveness (`pid_matches_create_time`, `live_host_processes_from_handle`) and
  the runner-handle scoped-PID accessors (`scoped_host_pids` returns bare PIDs;
  `scoped_host_processes` carries create_time). Slice 3 routes the kill path
  through the create-time-gated variant.
- `weft/_constants.py`: single source for constants. `MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS`
  (currently 15.0, line ~353) is the grace knob. Do not change its value in this
  plan; Slice 1 makes it non-load-bearing for correctness, which is the right
  way to stop the widening cycle.

Test homes (confirmed to exist):

- `tests/commands/test_task_evidence.py`, `tests/core/test_manager.py`,
  `tests/commands/test_result.py` — terminal proof / wrapper-lost / evidence.
- `tests/specs/resource_management/test_timeout_return_code.py`,
  `tests/tasks/test_runner.py` — timeout/runner behavior.
- `tests/core/test_manager.py`, `tests/tasks/test_runner.py`,
  `tests/core/test_heartbeat_helpers.py` — PID liveness and shutdown reap.
- `tests/core/test_control_probe.py`, `tests/core/test_manager.py`,
  `tests/commands/test_manager_commands.py`, `tests/commands/test_run.py` —
  manager runtime, leadership, PONG.
- `tests/specs/quick_reference/test_queue_names.py`,
  `tests/specs/test_plan_metadata.py`,
  `tests/architecture/test_import_boundaries.py` — doc/structure guards.

## Invariants and Constraints (apply to the whole plan)

- Specs are normative. If a slice and a spec disagree, fix the disagreement; do
  not silently implement the plan over the spec. Slice 1 changes a spec on
  purpose and says so.
- Queue-first execution stays intact. Do not create a second durable path around
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`. Every fix
  here is on the existing spine.
- TID format and immutability stay intact. `spec`/`io` stay frozen after
  resolved TaskSpec creation.
- State transitions remain forward-only per [STATE.1]/[STATE.2]. Slice 1 must not
  weaken this: it changes which terminal verdict is *reported* from durable
  evidence, never re-opens a terminal state.
- Reserved-queue policy remains correct and observable.
- Runtime-only `weft.state.*` queues stay runtime-only.
- Append-only queue histories (`weft.log.tasks`, `weft.state.*`) keep
  generator-based / bounded scanning. Do not introduce fixed-limit snapshot
  reads.
- New or moved constants live in `weft/_constants.py` with a docstring.
- No new dependency, and no dependency removal. The `llm` dependency stays (it is
  the bounded-agent call path); the earlier "make `llm` optional" idea is dropped
  per author decision.
- No public CLI shape, queue name, TaskSpec field, result-payload, or persisted
  format change. Slice 1 changes a read-side *interpretation* of existing
  envelopes, not their schema.
- No drive-by refactor. The monitor subsystem size, `manager.py` size, and
  `BaseTask` size are explicitly out of scope (see Out of Scope); they are
  cohesive by the project's own rule and no separable shared state was shown.

Hidden couplings to hold in mind (named here so they are not discovered
mid-change):

- terminal-proof visibility ↔ manager child reap ↔ the `weft result` / `weft
  task status` read path all consume the same terminal evidence; Slice 1 changes
  the verdict only in the case where a task-sourced terminal envelope and a
  manager `wrapper_lost` envelope coexist.
- manager lifecycle ↔ `weft.state.services` rows ↔ create-time PID liveness;
  Slices 3 and 4 sit on this coupling.
- task completion ↔ outbox/result visibility ↔ completion-event timing; Slice 2
  sits here.

Per-slice review gates (each slice must satisfy these before it lands):

- no new execution path; no drive-by refactor; no new dependency; no public CLI
  shape change; no spec drift between touched docs and code; no mock-heavy
  substitute for a real broker/process proof when the real path is practical.
- self-driven fresh-eyes review completed for the slice.
- external (preferably cross-family) review completed for Slices 1, 2, 3, and 4
  (runtime behavior changes); Slices 5 and 6 require self-review only.

## Rollout and Rollback

This is a multi-slice plan. Each slice is independently landable and must leave
the repo green on its own. Recommended execution order: 1 → 2 → 3 → 4 → 5 → 6,
with Slice 6 (docs) safe to land first if a low-risk warm-up is preferred. Slice 7
(identity alignment) is decided and is a doc/string change. The slices are
otherwise independent.

Rollback per slice:

- Slice 1 (terminal evidence): pure read-side interpretation change plus a spec
  edit. No envelope schema, queue name, or persisted format changes, so a reader
  without the change (old) and one with it (new) can coexist during rollout —
  they differ only in which verdict they report when both a task terminal and a
  manager `wrapper_lost` envelope are present, and the new behavior is strictly
  the more correct one. **Not a one-way door.** Rollback = revert the
  `task_evidence.py` change and the `[MF-5]` spec edit.
- Slice 2 (timeout re-poll): revert the `host.py` change. Backward compatible;
  the worst case after revert is the pre-existing dropped-result behavior.
- Slice 3 (create-time reap): revert the kill-path routing change. Backward
  compatible.
- Slice 4 (PONG predicate): the shared helper is additive; both call sites keep
  their own context/outbox nuance. Rollback = inline the helper back into each
  site. The one intentional behavior change is the deliberate reconciliation of
  the empty-string-context divergence; record the chosen semantics so a revert
  is a conscious decision, not an accident.
- Slice 5 (dead code / `_start_manager`): pure refactor; trivial revert.
- Slice 6 (docs): per-file doc revert.

One-way doors: none. Slice 7 is a help-text / governance-doc string alignment.

## Out of Scope

- Splitting or "modularizing" `manager.py`, `manager_runtime.py`, the monitor
  subsystem (`task_monitor.py`, `store.py`), `BaseTask`, `TaskSpec`, or
  `_constants.py` because they are large. The evaluation confirmed the cohesion
  claim holds for the reactor core; no separable shared state was demonstrated.
  The monitor subsystem is the one place size-vs-cohesion is strained, but
  splitting it is not justified by this plan and would violate the project's own
  rule without an evidence-backed separability argument.
- Changing `MANAGER_CHILD_TERMINAL_PROOF_GRACE_SECONDS` value, or removing the
  grace mechanism. Slice 1 makes it non-load-bearing for correctness; tuning it
  further is unnecessary once the precedence rule is fixed.
- Converting host resource limits to OS-level hard caps (`setrlimit`/cgroups).
  That contradicts the spec's deliberate monitor-and-react model ([RM-0]) and is
  a design change, not a fix. Slice 6 only clarifies the existing model.
- Any monitor cleanup-policy, retention, pipeline, or SimpleBroker-integration
  rewrite.
- Any change to the `llm` dependency or agent-runtime behavior. `llm` stays; the
  Slice 7 identity alignment is help-text / doc only.

Stop and re-plan if: a second execution path appears; a test needs broad mocks
around queues/process lifecycle/state/reservations/results; a fix wants to
mutate frozen resolved TaskSpec state; rollback cannot be described cleanly; or
fresh-eyes review finds a latent ambiguity a zero-context engineer could read
two ways.

## Tasks

### Slice 1 — Terminal-evidence source precedence (HIGH; runtime + spec delta)

Outcome: a task-sourced terminal envelope (`source="task"`) takes precedence
over a manager-sourced `wrapper_lost` envelope (`source="manager"`) regardless
of timestamp when both are present on the same `T{tid}.ctrl_out`, across **every**
terminal-ctrl_out reader — not just one. A genuinely completed task whose durable
terminal write lands after the manager's grace window is no longer reported
`failed` by `weft result`, `weft task status`, or any other reader. The manager
`wrapper_lost` envelope remains authoritative only when no task-sourced terminal
proof exists.

Scope note (do not under-scope this — a cross-family review caught it): three
independent terminal-ctrl_out readers exist. `weft/commands/tasks.py:238` goes
through `peek_terminal_ctrl_out_evidence`, so editing that helper covers `weft
task status`, the internal `known_tid_evidence` path, and `pruning/retention.py`.
But `weft result` has TWO separate drain-and-loop readers —
`weft/commands/_result_wait.py:80-158` and `weft/commands/result.py:565-579` —
that build a list via `drain_ctrl_out_stream_messages` and iterate
`terminal_status_from_event` in queue order, bypassing the helper. Fixing only
`peek_terminal_ctrl_out_evidence` would leave `weft result` still able to report
`failed`. The fix must therefore put the source-precedence rule in ONE shared
selection helper and route all three readers through it.

Wrapper vs core: this is core work on the read-side reconciliation, plus a spec
delta. It is not wrapper logic.

Files to touch:

- `weft/core/task_evidence.py` — add a shared `select_terminal_envelope(candidates)`
  helper that takes/returns `(payload, message_id)` candidates (the broker
  timestamp is needed for same-source latest-wins AND for the downstream
  `observed_at` / ack target), encodes the source-precedence rule, and is used by
  `peek_terminal_ctrl_out_evidence` (around line 448). Single source of the rule.
- `weft/commands/task_evidence.py` — the compatibility re-export shim. Add
  `select_terminal_envelope` to its `from weft.core.task_evidence import (...)`
  block and `__all__` (the result readers import the other evidence helpers via
  this module, so the new helper must be exported here too — or state that result
  code imports it directly from core).
- `weft/commands/_result_wait.py` (the `drain_ctrl_out_stream_messages` +
  `terminal_status_from_event` loop, ~67–158) — retain `(envelope, message_id)`
  when draining (it already reads `with_timestamps=True` and has `message_id` in
  scope; it just discards it at line 88) and select via the shared helper instead
  of acting on envelopes in queue order.
- `weft/commands/result.py` (the same loop, ~565–579) — same change.
- `docs/specifications/05-Message_Flow_and_State.md` ([MF-5] precedence text
  ~540–550; add the source-precedence sub-rule; update `_Implementation
  mapping_` ~193–196 / ~590–607 if ownership notes drift; add a plan backlink
  under `## Related Plans` ~1104)
- `tests/commands/test_task_evidence.py` (new helper regression),
  `tests/commands/test_result.py` (new regression that `weft result` reports
  `completed` in the both-envelopes case — this guards the path a helper-only fix
  would miss)
- `tests/core/test_manager.py` (verify existing terminal-proof tests still pass;
  extend if they assert the old both-envelopes-present behavior)

Read first:

- `docs/specifications/05-Message_Flow_and_State.md` [MF-3] (envelope schema,
  ~163–182), [MF-5] (precedence, ~540–550)
- `weft/core/task_evidence.py` `peek_terminal_ctrl_out_evidence` and the helpers
  that rank task-log lifecycle proof vs typed terminal `ctrl_out` vs PONG
- `weft/commands/_result_wait.py` and `weft/commands/result.py` — the
  `drain_ctrl_out_stream_messages` / `terminal_status_from_event` loops that `weft
  result` uses; confirm they currently act per-envelope in queue order, and find
  where `drain_ctrl_out_stream_messages` / `terminal_status_from_event` are defined
  so the shared helper slots in without duplicating their parsing
- `weft/core/manager.py` `_write_manager_terminal_envelope` (~3099) to confirm
  the manager only writes `wrapper_lost` when no task proof was visible at write
  time, and that it stamps `time.time_ns()` at write time
- `docs/lessons.md` Terminal Proof Grace lesson

Spec delta (required — this is why the slice is risky): [MF-5] currently defines
a priority list (task-log lifecycle proof > typed terminal `ctrl_out` > final
outbox > live evidence > stale fallback) and a write-side rule that the manager
must not author its envelope when task-owned terminal proof is already visible
(~180–182). It does **not** state a read-side rule for the case where a
task-sourced terminal envelope and a manager `wrapper_lost` envelope coexist in
the same `ctrl_out` and are reconciled by timestamp. Add a sub-rule to [MF-5]
(near ~548–550): among terminal `ctrl_out` envelopes, a `source="task"` terminal
envelope takes precedence over a `source="manager"` `wrapper_lost` envelope
regardless of timestamp; the manager `wrapper_lost` envelope classifies the task
as terminal only when no task-sourced terminal envelope is present. State that
this keeps the grace window a liveness mechanism, not a correctness arbiter.

Reuse: the existing `coerce_terminal_envelope`, `TaskEvidenceSnapshot`,
`reconciliation_for_terminal_ctrl_out`, and the `WRAPPER_LOST_ERROR` /
`source == "manager"` classification already in `task_evidence.py`. Do not add a
new evidence type. The shared `select_terminal_envelope` helper consolidates the
per-reader selection into one place — that is the opposite of a parallel
reconciliation path; it replaces the ad-hoc per-envelope handling in the
result-wait loops.

Approach (red-green):

1. Write a failing test in `tests/commands/test_task_evidence.py` using
   `broker_env` / a real `Queue`, modeled on the existing
   `test_wrapper_lost_ctrl_out_classifies_status_without_consuming` (running-only
   task log). Write NO terminal task-log event in the fixture: the bug only
   surfaces when `bounded_log_terminal_evidence` is None and `ctrl_out` is the
   selected source — a `work_completed` log event would let log proof win and the
   `ctrl_out` selection would never run. On the same `ctrl_out`, write a
   `source="task"`, `status="completed"` terminal envelope, then a
   `source="manager"`, `status="failed"`, `error=WRAPPER_LOST_ERROR` envelope at a
   strictly later queue timestamp. Assert the reconciled snapshot reports
   `completed` (currently `failed`). Add a second assertion proving
   order-independence: the same two envelopes committed in the opposite order also
   resolve to `completed`.
2. Put the rule in ONE place: a shared `select_terminal_envelope(candidates)`
   helper in `task_evidence.py`. Because both same-source latest-wins AND the
   downstream `observed_at` / ack target need the broker timestamp, the helper
   operates on candidates of shape `(payload, message_id)` and RETURNS the chosen
   candidate (not just the payload). Rule: a `source="task"` terminal envelope
   wins over a `source="manager"` `wrapper_lost` envelope regardless of timestamp;
   within the same source class, latest-wins by `message_id`. Make
   `peek_terminal_ctrl_out_evidence` build candidates from its
   `peek_generator(with_timestamps=True)` loop, call the helper, and keep using the
   returned candidate's `message_id` for `observed_at` and the `QueueAckTarget`.
   Confirm the broader reconciliation (task-log lifecycle proof already ranked
   highest by [MF-5]) still composes correctly.
3. Route the two `weft result` readers (`_result_wait.py`, `result.py`) through the
   same helper. `drain_ctrl_out_stream_messages` already reads `with_timestamps=True`
   and has `message_id` in scope but currently discards it (payload-only append at
   `_result_wait.py:88`) — change it to retain `(envelope, message_id)` candidates,
   select with the shared helper, then derive status, instead of letting a
   `wrapper_lost` envelope that sorts first in the drain short-circuit a task-owned
   `completed`. Add a `tests/commands/test_result.py` regression proving `weft
   result` reports `completed` when both envelopes are present.
4. Update the [MF-5] spec text, add the plan backlink, refresh nearby
   `_Implementation mapping_` notes, and keep `task_evidence.py` docstrings
   pointing at [MF-5].

Already handled — do not add redundant guards: non-terminal statuses on
`ctrl_out` are already filtered by `coerce_terminal_envelope` (only
`TERMINAL_TASK_STATUSES` become candidates); multiple task-sourced terminal
envelopes resolve by latest-wins within the same class; and the manager has no
independent failure verdict to mask — its only `wrapper_lost` write is the
generic failsafe, emitted only when no task proof was visible.

What not to mock: use a real `Queue`/`broker_env` for the envelopes; do not mock
`peek_generator`. The bug is in real timestamp ordering.

Stop and re-evaluate if: the fix appears to need a change to the envelope schema
or to the manager write-side; that would be a different slice. Or if a
task-sourced terminal envelope and a manager `wrapper_lost` envelope can
legitimately both be authoritative (they cannot — the manager only writes
`wrapper_lost` as a failsafe when no task proof was visible).

Done when: the source-precedence rule lives in one shared helper that
`peek_terminal_ctrl_out_evidence`, `_result_wait.py`, and `result.py` all use; the
new helper regression AND the new `weft result` both-envelopes regression pass;
existing terminal-proof tests in `tests/core/test_manager.py` and
`tests/commands/test_result.py` stay green (the `wrapper_lost`-only path, where no
task envelope exists, must still classify as `failed`); [MF-5] states the
source-precedence rule with a plan backlink.

### Slice 2 — Honor a result that arrives in the function-target timeout race (MED; runtime)

Outcome: a callable/function target whose result is already visible on the result
queue at the timeout deadline is reported `completed`, not `timeout`. The
function-runner path re-checks the result queue before declaring a timeout. (This
closes the common case where the worker finished and enqueued its result just
before the deadline; it does not promise to catch a result still in
multiprocessing transit — see the scope bound below.)

Files to touch:

- `weft/core/runners/host.py` (`run_with_hooks`, non-command branch ~458–475)
- `tests/tasks/test_runner.py` and/or
  `tests/specs/resource_management/test_timeout_return_code.py` (regression)

Read first:

- `weft/core/runners/host.py` non-command timeout branch (~458–475) AND its own
  normal loop-exit drain at ~545–547 (`result_queue.get_nowait()`) — that drain
  is the exact pattern to reuse. The command path in `subprocess_runner.py`
  (~176–184) re-polls `process.poll()` (OS exit status), a different mechanism; it
  is the conceptual analog only, not the code to copy.
- `weft/core/tasks/consumer.py` outcome→state mapping (timeout → `mark_timeout`,
  completed → `mark_completed`)
- `docs/specifications/06-Resource_Management.md` [RM-5.2] (~136–137, ~148–150)

Spec delta: none required. No spec states completion-vs-timeout precedence for a
result arriving in the race window; this is a bug fix. Optionally note the
clarification in [RM-5.2], but do not block the fix on it.

Reuse: the existing `result_queue` and the runner's existing outcome
construction. Do not add a second result channel.

Approach (red-green): attempt a non-blocking `result_queue.get_nowait()` BEFORE
`_stop_process(process)` in the timeout branch (~459–460); if a result is
present, stop the process and return that outcome (so the consumer marks it
`completed`). Reuse the existing drain at `host.py:545–547`. Write the failing
test first using a deterministic seam — enqueue a result on the runner's result
queue and then drive the timeout check — rather than racing a real `time.sleep`,
so the test is not timing-flaky.

Scope bound: this honors a result already enqueued and visible at the deadline. A
result still in multiprocessing-queue transit can lag `get_nowait()`; catching
that would need a bounded post-deadline `join`/drain, which is intentionally out
of scope for this slice (it adds a timeout-path delay knob). If the lagging case
turns out to matter in practice, raise it as a separate slice rather than widening
here.

What not to mock: drive a real `Consumer`/runner outcome where practical; do not
mock the state-transition mapping and then claim timeout-vs-completion behavior
is tested.

Stop and re-evaluate if: honoring the late result would require reordering the
kill/cleanup so the process is left running; it must not. The result is only
honored if it is already on the queue at the check.

Done when: the regression proves a result present at the deadline yields
`completed`; existing timeout tests (a target that truly never finishes still
times out with return code 124) stay green.

### Slice 3 — Create-time-validated shutdown reap (MED; runtime)

Outcome: the forced-shutdown child-termination path resolves descendant PIDs
through the create-time-validated liveness helper, so a recycled PID held by a
stale `weft.state.tid_mappings` row is never terminated. This makes the kill path
honor [MA-1] item 4 (a PID is live only when it still matches the recorded process
creation time when available — the create-time validation already used everywhere
liveness is *checked*), within the force-reap authority scope of [MA-1] item 7.

Files to touch:

- `weft/core/manager.py` (shutdown `_cleanup_children` / forced-termination path
  ~3216–3276; `_managed_pids_for_child` ~5767)
- `weft/ext.py` only if the create-time-carrying accessor needs to be the one
  called (`scoped_host_processes` carries create_time; `scoped_host_pids`
  returns bare PIDs)
- `tests/core/test_manager.py` (regression)

Read first:

- `weft/helpers/__init__.py` `pid_matches_create_time`,
  `live_host_processes_from_handle` (the canonical create-time-gated path)
- `weft/ext.py` `scoped_host_pids` vs `scoped_host_processes`
- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 4 (~160–162) and
  item 7 (~278–282); [MA-3] drain/forced-reap (~399–402)
- `docs/specifications/07-System_Invariants.md` [EXEC.3] (~124–129)

Spec delta: none. The change makes the code conform to existing spec; do not
broaden force-reap authority beyond "tracked child process or scoped `host-pid`
runtime handle" (that would require editing [MA-1] item 7 and would be a
different slice).

Reuse: the existing create-time-gated liveness helper used by
`_manager_record_liveness` and `manager_runtime.py`. Do not write a new liveness
routine.

Approach (red-green): write a failing test that constructs a managed-child /
mapping record whose recorded PID is live but whose create_time does not match
(simulating PID reuse), and assert the shutdown path does not call
`terminate_process_tree` on it. Then route the termination PID set through the
create-time-validated accessor. The live multiprocessing-handle path
(`child.process.pid`) is already safe and is not changed.

Behavior-preserving safety: when a handle carries no `create_time`,
`scoped_host_processes` yields `(pid, None)` and `pid_matches_create_time(pid,
None)` falls back to `pid_is_live`, so routing through the create-time-gated
accessor preserves current behavior for handles without create-time observations
and only tightens when create_time is present.

What not to mock: use the real liveness helper and a real (or faithfully faked)
process-handle record; the bug is precisely about bare-PID vs create-time-gated
resolution, so mocking the helper would hide it.

Stop and re-evaluate if: the change wants to alter what counts as a "live" child
for the normal (non-shutdown) reap path; that path already validates create-time
and is out of scope.

Done when: the regression proves a create-time-mismatched PID is not terminated
on shutdown; existing shutdown/drain tests stay green.

### Slice 4 — Consolidate the drifted PONG dispatch-eligibility predicate (HIGH-correctness; refactor across two contexts → hardening mandatory)

Outcome: the manager dispatch-eligibility predicate for a matched PONG exists as
one shared helper, eliminating the two copies that have already drifted on
empty-string-context handling. Both the in-process manager and the out-of-process
runtime evaluate the same authority gate; the empty-context divergence is
resolved by a deliberate, documented decision rather than by accident.

Wrapper vs core: the shared status/role/`weft.spawn.requests`/ctrl-queue gate is
core; each caller keeps a thin context/outbox nuance. This is the hardening
runbook's "one shared core path with thin wrappers" pattern.

Files to touch:

- `weft/core/control_probe.py` (new shared predicate — both callers already
  import this module)
- `weft/core/manager.py` (the copy around line 2123)
- `weft/core/manager_runtime.py` (`_matched_pong_proves_manager_record` around
  line 690)
- `docs/specifications/03-Manager_Architecture.md` [MA-1.4] mapping (~306): note
  that the shared dispatch-eligibility gate now lives in
  `weft/core/control_probe.py` and that the in-process `_pong_dispatch_eligible`
  and runtime `_matched_pong_proves_manager_record` both delegate to it (do NOT
  rename the containing `_evaluate_dispatch_ownership` /
  `_manager_pong_dispatch_proof`). Add `weft/core/control_probe.py` to the spec 07
  Manager `_Implementation mapping_` module list (~347–351)
- `tests/core/test_control_probe.py` and `tests/core/test_manager.py`
  (predicate-level table tests + a broker-backed agreement test)

Read first:

- `docs/specifications/03-Manager_Architecture.md` [MA-1] item 4 (~178–182): the
  canonical predicate — "manager role, `weft.spawn.requests`, matching control
  queues, matching context when present, non-terminal `task_status`, and
  `should_stop` not true." Spec maps this to `Manager._evaluate_dispatch_ownership`
  / `Manager._manager_pong_dispatch_proof`.
- both current copies; confirm by reading exactly which two functions share the
  ~24 identical leading lines and where they diverge (empty-string `record_context`
  treated as falsy in one, authoritative in the other; the runtime copy also adds
  an `outbox == WEFT_MANAGER_OUTBOX_QUEUE` check)
- `docs/specifications/07-System_Invariants.md` [MANAGER.8] (~369–391)

First task within the slice: re-confirm by reading, but an independent review
already verified the duplicated pair is `manager.py::_pong_dispatch_eligible`
(~2123, called only from `_manager_pong_dispatch_proof` at ~2359) and
`manager_runtime.py::_matched_pong_proves_manager_record` (~690).
`_evaluate_dispatch_ownership` (~2747) and `_manager_pong_dispatch_proof` are the
*containing* ownership/proof methods, NOT the duplicated predicate — do not touch
their names. Extract the shared status / role / `weft.spawn.requests` / ctrl-queue
gate the two predicates hold in common.

Spec delta: none to the normative predicate (it is fully specified in [MA-1]
item 4 and [MANAGER.8]). Behavior-preserving contract to keep (a cross-family
review flagged this): the current predicates ACCEPT absent manager-selection
fields (`role`, `requests`, `ctrl_in`, `ctrl_out`, `weft_context`) — each check is
`if value is not None and value != expected: reject`, so only present-but-mismatched
values reject. The extraction must preserve accept-missing. The ONLY intentional
behavior change in this slice is the empty-context reconciliation, and the
decision is MADE here (do not defer it): an empty `weft_context` string means
ABSENT → fall back to the resolved context. No spec says an empty context is
meaningful (spec: "matching context when present"), and the in-process manager
already behaves this way (`manager.py:2156` treats empty as falsy → fallback), so
`manager_runtime.py:723` is the side that changes (it currently treats an empty
string as authoritative). After the change both sides fall back to the resolved
context on empty. Name this change in the test, and note in rollback that
reverting restores the runtime's empty-as-authoritative quirk. If you conclude absent
fields should instead be *invalid*, that is a separate, named behavior change —
out of scope here; do not fold it in silently. Update the two `_Implementation
mapping_` lists to point at the new single owner.

Reuse: put the shared gate next to `coerce_pong_response` in `control_probe`.
Do not invent a new module or a generic "predicate registry."

Approach (behavior-preserving except the deliberate empty-context fix):

1. Write predicate-level table tests in `tests/core/test_control_probe.py`
   covering: accepted record (all fields valid); accepted record with ABSENT
   manager-selection fields (current code accepts missing `role` / `requests` /
   `ctrl_in` / `ctrl_out` / `weft_context` — the extraction must preserve this);
   each rejection reason (terminal `task_status`, `should_stop`, wrong `role`,
   **mismatched** `weft.spawn.requests`, mismatched ctrl queues); and the
   empty-context edge from both callers' perspectives. The empty-context case is
   the red test for the decided semantics: empty `weft_context` → fall back to the
   resolved context, both callers agreeing.
2. Extract the shared gate into `control_probe`; have both call sites delegate,
   each adding only its own context/outbox nuance.
3. Add a broker-backed agreement test (in `tests/core/test_manager.py`) that the
   in-process and runtime evaluations agree on a representative set of registry
   records, including the empty-context record.
4. Update both spec implementation-mapping lists and the function docstrings.

What not to mock: use real registry-record dicts and the real predicate; this is
an authority gate, so a mock that always returns eligible would defeat the test.

Stop and re-evaluate if: the two predicates turn out not to share a reconcilable
core (they appear to — ~24 identical lines — but verify); if so, document why and
do not force a merge. Also stop if extraction starts requiring many mutable
runtime objects to be threaded into `control_probe` — the gate operates on
record dicts and small scalars only.

Done when: one predicate owns the shared gate; both callers delegate; the
empty-context semantics are decided, tested, and documented; both spec mapping
lists name the new owner; `tests/core/test_control_probe.py`,
`tests/core/test_manager.py`, `tests/commands/test_manager_commands.py`, and
`tests/commands/test_run.py` stay green.

### Slice 5 — Remove dead code and reduce `_start_manager` duplication (LOW risk; refactor)

Outcome: the two redundant periodic-ensure wrappers are collapsed into one. There
is no spec contract named "tick" (a spec grep finds none); production scheduling
of internal-service convergence is owned by `_run_managed_service_convergence`
(reactor, ~6381/6412) plus direct `_reconcile_managed_services` calls (~428,
~5974, ~6170), so the `_tick_*` wrappers are NOT on the production path.
`_tick_internal_services` (~5473) has zero callers; `_tick_task_monitor` (~5483)
is test-only (18 sites in `tests/core/test_manager.py`), with a body
byte-identical to `_tick_internal_services` and a docstring that under-describes
what it does (it reconciles all internal services, not just the TaskMonitor). Keep
ONE accurately-named wrapper as a documented test/diagnostic seam (recommended:
keep `_tick_internal_services`, fix its docstring to say it drives one
internal-service convergence pass and that production scheduling lives in
`_run_managed_service_convergence`), delete the duplicate, and repoint the 18
tests at the survivor. Do NOT touch `_tick_managed_service` (~5462) — that is the
live per-service reconcile worker called inside `_reconcile_managed_services`, a
different method. Separately, `_start_manager`'s three near-duplicate
competitor-acknowledge blocks are collapsed into one local helper with reduced
nesting. No behavior change.

Files to touch:

- `weft/core/manager.py` (`_tick_internal_services` / `_tick_task_monitor`
  ~5473–5491)
- `weft/core/manager_runtime.py` (`_start_manager` ~1499–1713)
- `tests/core/test_manager.py` (the ~18 call sites of `_tick_task_monitor`),
  `tests/commands/test_manager_commands.py`, `tests/commands/test_run.py`
  (manager-start regressions)

Read first:

- both target functions; confirm `_tick_internal_services` has zero production
  callers (`rg "_tick_internal_services" weft tests`) and that `_tick_task_monitor`
  is referenced only by tests before removing/renaming
- the three competitor-acknowledge blocks in `_start_manager` (one inside the
  loop, two post-loop) to confirm they are the same acknowledge-or-abort →
  cleanup → return sequence

Spec delta: none. Pure refactor / dead-code removal.

Reuse: `_start_manager` should call `_reconcile_managed_services` and existing
launcher helpers; the new local helper is a private function in
`manager_runtime.py`, not a new module.

Approach: this is the one slice where red-green is not the natural shape (no new
behavior). The proof is that the existing manager-start and tick tests stay green
before and after, and that `rg` confirms the duplicate `_tick_task_monitor` is
gone and exactly one wrapper remains. Make the
`_start_manager` extraction first (behavior-preserving), run the start
regressions, then do the `_tick_*` removal/rename and update the test call sites.

What not to mock: keep the manager-start tests on the real launch/registry path
they already use; do not replace them with mocks to make the refactor easier.

Stop and re-evaluate if: the `_start_manager` extraction changes any of the three
terminal return paths' observable behavior (it must not — same record, same
abort/ack, same cleanup), or if removing `_tick_task_monitor` would require
rewriting many tests rather than a mechanical call-site swap.

Done when: exactly one internal-service-ensure wrapper remains (accurately named
and documented as a test/diagnostic seam); the duplicate is deleted; the 18 test
call sites point at the survivor; the live `_tick_managed_service` worker is
untouched; `_start_manager` no longer repeats the acknowledge-or-abort block;
`tests/core/test_manager.py`, `tests/commands/test_manager_commands.py`, and
`tests/commands/test_run.py` stay green; `mypy` and `ruff` pass on the touched
files.

### Slice 6 — Documentation honesty fixes (LOW risk; doc-only)

Outcome: the documentation no longer describes a security boundary that does not
exist, no longer implies host resource limits are OS-enforced ceilings, names
the `docker_args` sandbox-escape caveat, and fixes two stale cross-references.

Files to touch:

- `AGENTS.md` `§4.11`: the `validate_safe_path` example function does not exist
  anywhere in the codebase (`rg validate_safe_path weft extensions` returns
  nothing) and the command path relies on argv (no `shell=True`), so path
  sanitization is genuinely unneeded there. Replace the phantom example with an
  honest note: Weft passes argv lists to subprocesses (no shell), so the trust
  boundary is the OS/filesystem under the documented user-level trust model, not
  a path sanitizer. (Editing `AGENTS.md` is a governance change — it is proposed
  here for author approval, not made unilaterally; see Agent Boundaries `§8`.)
- `docs/specifications/06-Resource_Management.md` [RM-1]/[RM-2]: add one sentence
  making explicit that "hard limit" describes the *response* (terminate on
  confirmed violation, no throttling — [RM-0]), while *detection* is bounded by
  the psutil sample window, so a fast allocator may transiently exceed `memory_mb`
  between samples before termination; Docker maps limits to native runtime quotas.
  This is a precision clarification, not a correction — the monitor-and-react
  model ([RM-0]) is already accurate. Check `README.md` and `weft --help` wording
  for "limit" language that implies an instantaneous ceiling and soften only if it
  overstates.
- `docs/specifications/13-Agent_Runtime.md` or the Docker extension docs: note
  that `spec.runner.options.docker_args` is a passthrough that can defeat the
  Docker sandbox (e.g. `--privileged`, `--cap-add`, `-v /:/host` are not
  blocked), acceptable under the user-level trust model but worth stating.
- Stale `weft/helpers.py` path (it is now the `weft/helpers/` package): fix in
  `docs/specifications/01-Core_Components.md` (~398),
  `03-Manager_Architecture.md` (~415), `05-Message_Flow_and_State.md` (~828),
  `07-System_Invariants.md` (~350), and `AGENTS.md` (~215, ~226).
- Stale process-title cross-reference: `docs/specifications/00-Quick_Reference.md`
  (~137) points to `01-Core_Components.md` for format/sanitization rules, but the
  authoritative rules are [OBS.4]/[OBS.5]/[OBS.7]/[OBS.8] in
  `07-System_Invariants.md`. Fix the pointer.

Read first:

- `AGENTS.md §4.11` and `§8`; `docs/specifications/06-Resource_Management.md`
  [RM-0]/[RM-1]/[RM-2]; `docs/specifications/00-Quick_Reference.md` around the
  process-title section; the Slice 1/Slice 4 spec edits if landing after them
  (to avoid backlink churn).

Spec delta: these are doc corrections, not behavior changes. Keep them
status-neutral and do not turn implementation notes into normative behavior.

Approach: make each edit as an independent, separately-revertible change. Run the
queue-name and spec-hygiene guards to confirm no guard regresses. There is no
runtime test for `AGENTS.md`; record the manual review.

What not to do: do not implement a real `validate_safe_path` to make the doc
true unless the author wants a path boundary added — the honest fix is to
describe the actual (argv-based) model. Do not change resource-limit behavior.

Done when: `rg validate_safe_path` returns no claim of an existing helper in
`AGENTS.md`; the resource-limit sample-window caveat is present in [RM-1]/[RM-2];
the `docker_args` caveat is documented; no `weft/helpers.py` file path remains in
the listed docs; the Quick Reference points at [OBS.4]/[OBS.7] in `07`;
`tests/specs/quick_reference/test_queue_names.py` and the spec-hygiene guards
pass.

### Slice 7 — Align stated identity to "durable task substrate for agent systems" (LOW risk; doc/string)

Author decisions (2026-05-29): the canonical identity is "the durable task
substrate for agent systems" (already in `pyproject.toml` and `README.md`). The
`llm` dependency STAYS — it is the deliberate, valued bounded-agent call path, not
a contradiction to remove; the earlier "make `llm` optional" idea is dropped.

Outcome: the shipping surfaces state one identity. Reconcile the two that diverge:
`weft --help`'s top line ("The Multi-Agent Weaving Toolkit") and `AGENTS.md §1.1`
("SimpleBroker for processes"). Keep §1.1's zero-config / safe-defaults /
no-cleverness *philosophy* (that framing is accurate and stays); only the identity
claim changes to "durable task substrate for agent systems". Add a one-line note in
§1.1 that the `llm` dependency is intentional (the bounded-agent path) so future
readers do not re-flag it as scope creep.

Files to touch:

- `weft/cli/app.py` (the Typer app help / top-line string)
- `AGENTS.md` `§1.1` (identity sentence + a one-line `llm`-is-intentional note;
  keep the philosophy bullets). Editing `AGENTS.md` is governance — author-approved
  here.

Read first:

- `README.md` opening and `pyproject.toml` description (the canonical phrasing to
  match), `weft/cli/app.py` help string, `AGENTS.md §1.1`.

Constraints: this is a branding / help-text string change, not a CLI command,
flag, or JSON-shape change (the "no public CLI shape change" invariant is about
commands/flags/payloads, not the help tagline). Do not touch agent-runtime
behavior or the `llm` dependency.

Done when: `weft --help`, `pyproject.toml`, `README.md`, and `AGENTS.md §1.1` all
state the same "durable task substrate for agent systems" identity; the `llm`
dependency is documented as intentional; no behavior change.

## Testing Plan

Use the repo-managed toolchain; do not assume global `pytest`/`mypy`/`ruff`.

Setup:

```bash
. ./.envrc
uv sync --all-extras
```

Per-slice focused verification:

- Slice 1:
  ```bash
  ./.venv/bin/python -m pytest -q tests/commands/test_task_evidence.py tests/commands/test_result.py
  ./.venv/bin/python -m pytest -q tests/core/test_manager.py -k "terminal or proof or wrapper"
  ```
- Slice 2:
  ```bash
  ./.venv/bin/python -m pytest -q tests/tasks/test_runner.py tests/specs/resource_management/test_timeout_return_code.py
  ```
- Slice 3:
  ```bash
  ./.venv/bin/python -m pytest -q tests/core/test_manager.py -k "cleanup or shutdown or reap or liveness"
  ```
- Slice 4:
  ```bash
  ./.venv/bin/python -m pytest -q tests/core/test_control_probe.py tests/core/test_manager.py tests/commands/test_manager_commands.py tests/commands/test_run.py
  ```
- Slice 5:
  ```bash
  ./.venv/bin/python -m pytest -q tests/core/test_manager.py tests/commands/test_manager_commands.py tests/commands/test_run.py
  ```
- Slice 6:
  ```bash
  ./.venv/bin/python -m pytest -q tests/specs/quick_reference/test_queue_names.py tests/specs/test_plan_metadata.py
  ```
- Slice 7 (identity strings):
  ```bash
  ./.venv/bin/python -m weft --help   # top line states "durable task substrate for agent systems"
  rg -n "Multi-Agent Weaving Toolkit" weft/cli || echo "old identity string gone (good)"
  ```

What to keep real (do not mock): SimpleBroker queue semantics and timestamp
ordering (Slice 1), the runner outcome→state mapping (Slice 2), create-time PID
liveness (Slice 3), the PONG authority predicate and registry records (Slice 4),
and the real manager launch/registry path (Slice 5). Mock only genuinely
external or nondeterministic boundaries.

Red-green expectation: Slices 1–4 each begin with a failing test that reproduces
the defect (for Slice 4 the "failing" test pins the chosen empty-context
semantics). Slice 5 is a behavior-preserving refactor proven by existing tests
staying green plus an `rg` check that the duplicate `_tick_task_monitor` is gone
(one wrapper remains). Slice 6 is
doc-only, proven by the spec guards plus manual review of `AGENTS.md`.

Postgres backend check when a slice touches backend-neutral queue/liveness/manager
behavior and PG support is available locally (Slices 1, 3, 4 are candidates):

```bash
bin/pytest-pg --all
```

Observable success beyond local tests:

- Slice 1: under load (or a faked late durable write), a completed task reports
  `completed` via `weft result` / `weft task status`, and the
  "widen the grace" failure mode no longer reproduces.
- Slice 2: a callable finishing at the deadline reports `completed`.
- Slice 3: shutdown never terminates a create-time-mismatched PID.
- Slice 4: the in-process and runtime managers agree on dispatch eligibility for
  the same registry rows.

## Verification and Gates

Before each slice: `git status --short`; do not revert unrelated user changes;
write/identify the focused red test.

After each slice: run the slice's focused tests, `ruff` and `mypy` on touched
files, `git diff --check`, and inspect `git diff` for unintended behavior
changes.

Final gates before claiming the whole plan done:

```bash
. ./.envrc
./.venv/bin/python -m pytest -q
./.venv/bin/mypy weft extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft extensions/weft_docker extensions/weft_macos_sandbox tests
./.venv/bin/python -m pytest -q tests/specs/test_plan_metadata.py
git diff --check
```

Backlink/traceability gate (definition of done for spec-touching slices 1, 3, 4,
6): each touched spec has a `## Related Plans` backlink to this plan; nearby
`_Implementation mapping_` / `_Implementation snapshot_` notes name the current
owner; touched module docstrings point at the governing spec section. When this
plan's slices land, flip this plan's `Status` to `completed` only if it still
describes current behavior, and keep the `docs/plans/README.md` row in sync.

## Independent Review Loop

This plan is boundary-crossing (durable-spine reconciliation, manager shutdown,
an authority predicate across two contexts) and must not be implemented without
review.

Reviewer bootstrap: in this environment a cross-family reviewer should be used
when available. Preferred: an OpenAI Codex review pass (the `codex` review
stance) as a different agent family; Gemini if present. If only same-family
review is available, note that limitation in the review notes. A same-family
fresh-eyes Claude reviewer has already reviewed this draft and its findings were
incorporated (see Self-Review Log, Pass 3); that pass is preliminary and not a
substitute for cross-family (Codex/Gemini) review of the risky slices. That
cross-family pass has since been completed (an OpenAI Codex review; see Self-Review
Log, Pass 5) and its findings are incorporated.

Reviewer should read: this plan; `AGENTS.md`; the spec sections cited in Source
Documents; `weft/core/task_evidence.py`, `weft/core/manager.py` (terminal-proof
and shutdown regions), `weft/core/manager_runtime.py` (`_start_manager`, the PONG
predicate), `weft/core/runners/host.py`; and the named test files.

Review prompt:

> Read the plan at docs/plans/2026-05-29-reliability-and-doc-fixes-plan.md.
> Carefully examine the plan and the associated code. Look for errors, bad ideas,
> and latent ambiguities. Don't do any implementation, but answer carefully:
> Could you implement this confidently and correctly if asked? Pay special
> attention to Slice 1 (is the source-precedence rule actually correct and
> complete vs the full [MF-5] precedence list?) and Slice 4 (is the empty-context
> reconciliation safe?).

The author must respond to every finding by updating the plan, explaining why the
current path is still best, or recording the point as out of scope. Run review
again after Slice 1 and after Slice 4 land, since those change runtime behavior.

## Fresh-Eyes Self-Review Log

Pass 1 (zero-context implementer check):

- Verified every cited code location against the files (`task_evidence.py:448`,
  `manager.py` terminal-proof ~3088–3132 and shutdown ~3216–3276,
  `host.py` ~458–475, `manager_runtime.py` `_start_manager` and the PONG copy,
  `control_probe.py` exists). Verified every spec code exists.
- Corrected an over-claim from the source evaluation: the resource-limit fix is a
  doc *clarification*, not a correction of a false spec — [RM-0]/[RM-1] already
  state the monitor-and-react polling model. Slice 6 now scopes this honestly and
  Out of Scope forbids converting to OS-level hard caps.
- Noted the [STATE.1] authoritative chain includes `spawning`/`cancelled`, not
  the `AGENTS.md §3` shorthand, so Slice 1 reviewers do not mis-state the state
  machine.
- Flagged the Slice 4 function-name uncertainty (evaluation vs spec naming) and
  made "confirm the exact duplicated pair first" an explicit in-slice step rather
  than asserting a name pair that might be wrong.

Pass 2 (drift / vague-verb / scope check):

- Searched for vague directives ("update the logic", "clean up"); the remaining
  instances are in explicit stop-gates and Out-of-Scope warnings, not in task
  instructions.
- Confirmed no slice introduces a second execution path, a new dependency, a
  queue-name/payload change, or a file split. (Slice 7 was decisions-only at this
  pass; superseded by Pass 4 — it is now a concrete doc/string task.)
- Confirmed rollback is described for every slice and that only Slice 1 carries a
  spec delta (with a clean, non-one-way-door rollback).

Pass 3 (independent same-family fresh-eyes review incorporated):

An independent reviewer (fresh context, fed only the plan plus the code and
specs) raised one blocker and two majors, all accepted and fixed in this
revision:

- Blocker: the plan lacked a `docs/plans/README.md` row and would have failed its
  own `tests/specs/test_plan_metadata.py` gate. Fixed: row added (`draft`), count
  bumped to 130.
- Major (M1): the Goal's "the manager envelope is always stamped later" causal
  claim was imprecise. Corrected to the real interleaving — the task terminal
  write commits after the manager's visibility check but its `wrapper_lost` write
  lands first; the Slice 1 test now also asserts order-independence.
- Major (M2): the Slice 1 red-test fixture must contain NO terminal task-log
  event, or `bounded_log_terminal_evidence` wins and the `ctrl_out` selection
  never runs. Added explicitly, modeled on
  `test_wrapper_lost_ctrl_out_classifies_status_without_consuming`.
- Major (M3): the Slice 4 spec-mapping instruction was re-aimed. The duplicated
  predicate is `_pong_dispatch_eligible` / `_matched_pong_proves_manager_record`,
  not the containing `_evaluate_dispatch_ownership` / `_manager_pong_dispatch_proof`;
  spec 03:306 gets the note and `control_probe.py` is added to the spec 07 module
  list.
- Minors (m1–m6) folded in: Slice 2 mirrors `host.py:545–547` (not the command
  runner's `process.poll()`) and drains before `_stop_process`; Slice 3 notes the
  `create_time is None` → `pid_is_live` graceful fallback and cites [MA-1] item 4
  as the create-time basis; Slice 6 RM phrasing separates hard-response from
  sampled-detection to avoid self-contradiction; the Slice 1 "already handled"
  edge cases are recorded so no redundant guards are added.

The reviewer independently verified (and the plan relies on) these facts: no
import cycle from placing the shared predicate in `control_probe`; the
empty-context divergence is exactly as described; `_tick_internal_services` has
zero production callers and `_tick_task_monitor` is test-only; `scoped_host_pids`
(bare) vs `scoped_host_processes` (`(pid, create_time)`) differ as claimed;
`validate_safe_path` exists nowhere; and the stale `weft/helpers.py` paths are at
the cited lines.

Residual risk: Slice 1's source-precedence rule must compose correctly with the
broader [MF-5] priority list (task-log lifecycle proof already outranks typed
`ctrl_out`); the slice limits its change to the `ctrl_out` selection, but the
implementer must confirm the higher-level reconciliation still behaves. This is
called out in the slice and is the first thing the external reviewer is asked to
check. External (cross-family) review of the risky slices has now been completed
(an OpenAI Codex pass; see Pass 5) and its findings are incorporated. A second
Codex pass then re-reviewed Slice 1's expanded scope and judged the plan
implementable confidently after two now-incorporated decisions — the
timestamp-candidate shape and the empty-context choice (see Pass 6). The plan is
implementation-ready.

Pass 4 (author decisions incorporated, 2026-05-29):

- `llm` dependency: KEEP. The author confirmed it is the easiest path to a bounded
  agent call and is proving useful. Dropped the former Slice 7(b) (make `llm`
  optional) and the related invariant/scope notes.
- Project identity: "the durable task substrate for agent systems". Slice 7 is now
  a concrete doc/string task to align `weft --help` and `AGENTS.md §1.1` to it, not
  a decision point.
- `_tick_*` functions: verified there is NO spec contract named "tick"; production
  internal-service scheduling is owned by `_run_managed_service_convergence` plus
  direct `_reconcile_managed_services`, so the `_tick_*` wrappers are off the
  production path. `_tick_internal_services` is fully dead; `_tick_task_monitor` is
  a test-only, identically-bodied, mis-described duplicate. Slice 5 now collapses
  the two into one accurately-named, documented test seam (keeping the survivor),
  deletes the duplicate, repoints the 18 tests, and leaves the live
  `_tick_managed_service` worker untouched.

Pass 5 (cross-family Codex review incorporated, 2026-05-29):

An OpenAI Codex pass (different agent family) raised five findings; all verified
against the code and accepted:

- P1 (Slice 1 under-scoped): `peek_terminal_ctrl_out_evidence` is only one of three
  terminal-ctrl_out readers. `weft result` uses two separate drain-and-loop readers
  (`_result_wait.py`, `result.py`) that bypass the helper, so a helper-only fix
  would still let `weft result` report `failed`. Fixed: Slice 1 now puts the
  source-precedence rule in one shared selection helper and routes all three readers
  through it, with a new `weft result` regression.
- P1 (Slice 4 hidden behavior change): the predicates ACCEPT missing
  manager-selection fields (only present-but-mismatched values reject). The test
  list said "missing `weft.spawn.requests`" as a reject reason, which would have
  silently changed behavior. Fixed: reworded to "mismatched", and accept-missing is
  now an explicit behavior-preserving constraint; the only intentional change stays
  the empty-context reconciliation.
- P2 (Slice 1 causal narrative backwards): the Goal said the manager envelope
  "commits first", but latest-wins only mis-reports `failed` when the manager
  envelope is *later*. Fixed the narrative to match the comprehension question and
  test (manager envelope lands with the later timestamp).
- P2 (Slice 2 overstated): the fix only catches a result already visible at the
  deadline, not anything "within the race window" (multiprocessing visibility can
  lag). Narrowed the outcome wording and added an explicit scope bound.
- P3 (stale text): fixed the rollout line and self-review note that still called
  Slice 7 a decision point, and the Slice 5 "rg confirms the dead method is gone"
  phrasing (now: the duplicate is gone, one wrapper remains).

Pass 6 (second cross-family Codex review incorporated, 2026-05-29):

A follow-up Codex pass (verdict: implementable confidently after two explicit
decisions) raised five precision findings; all accepted:

- P1 (Slice 1 timestamp carrier was implicit): same-source latest-wins and the
  downstream `observed_at` / ack target both need the broker `message_id`, but
  `drain_ctrl_out_stream_messages` discards it (payload-only at `_result_wait.py:88`).
  Fixed: the shared helper now takes/returns `(payload, message_id)` candidates and
  the drain retains `message_id`.
- P1/P2 (Slice 4 empty-context deferred): the plan now MAKES the call — empty
  `weft_context` means absent → fall back to the resolved context (no spec says
  empty is meaningful; `manager.py` already does this, so `manager_runtime.py` is
  the side that changes).
- P2 (Slice 5 contradiction): completion criteria said "dead
  `_tick_internal_services` removed" while the slice keeps it as the survivor.
  Fixed to "duplicate `_tick_task_monitor` removed; one wrapper remains."
- P3 (Slice 1 re-export): `_result_wait.py` imports evidence helpers via the
  `weft/commands/task_evidence.py` re-export shim; added that file (import +
  `__all__`) to Slice 1.
- P3 (Slice 7 verification): added a `weft --help` + `rg` identity-string check to
  the testing plan.

## Completion Criteria

- Slice 1: terminal-evidence source precedence implemented; [MF-5] states the
  rule with a plan backlink; completed-task-with-late-write no longer reports
  `failed`.
- Slice 2: function-target timeout honors a result present at the deadline.
- Slice 3: shutdown reap uses create-time-validated liveness; no recycled PID is
  terminated.
- Slice 4: one shared PONG dispatch-eligibility predicate; empty-context
  semantics decided, tested, documented; both spec mappings updated.
- Slice 5: duplicate `_tick_task_monitor` removed; exactly one internal-service
  wrapper remains (the live `_tick_managed_service` worker untouched);
  `_start_manager` de-duplicated; no behavior change.
- Slice 6: documentation honesty fixes landed; guards pass.
- Slice 7: `weft --help`, `pyproject.toml`, `README.md`, and `AGENTS.md §1.1`
  state the same "durable task substrate for agent systems" identity; `llm` kept.
- Final gates pass; traceability is bidirectional for every spec-touching slice;
  external review findings resolved for Slices 1–4.
