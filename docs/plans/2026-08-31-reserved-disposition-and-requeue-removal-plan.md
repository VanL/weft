# Reserved Disposition and REQUEUE Removal Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [QUEUE.6]; docs/specifications/05-Message_Flow_and_State.md [MF-2]; docs/specifications/02-TaskSpec.md [TS-1], [TS-1.1]
Superseded by: none

Class: 5 — normative spec edits ([QUEUE.6], [MF-2], [TS-1.1], TaskSpec
field docs) plus a durable-spine execution-path change, so the
hardening checklist applies. Plan type: implementation with spec
revision. Promotion strategy: **A** (in-file text before link claims) for
[QUEUE.6], [MF-2], [TS-1.1]; field documentation in spec 02 is promoted
with that text, and README edits land with the code slice. Program ledger:
[2026-08-31-guard-and-custody-simplification-plan.md](./2026-08-31-guard-and-custody-simplification-plan.md).

## 1. Goal

Two changes to reserved-queue disposition, decided 2026-08-31:

1. **Delete the backstop drains.** Every stop/kill/error path in the task
   classes runs `_apply_reserved_policy` → `_ensure_reserved_empty`
   (destructive `read_many` drain) → `_cleanup_reserved_if_needed` (second
   drain); the Manager has the same shape (`_apply_spawn_reserved_policy`
   → `_ensure_reserved_queue_empty` → `_cleanup_reserved_queue_if_needed`).
   Three task sites run the drain after a *success-path* exact ack. A row
   that survives a failed policy application or a failed ack is destroyed
   by the drain — the opposite of [MF-2]'s "crash leaves the message in
   reserved state for explicit operator recovery." The successful-outcome
   path also skips reserved policy when finalizing deferred STOP/KILL:
   otherwise CLEAR can perform a second exact delete after a failed ack,
   even after all drains are removed. Terminal state, envelope, and
   control acknowledgement still complete.
2. **Remove `requeue` from task-level reserved policies.** A task-level
   policy is applied once per reserved row at its disposition point: the
   task's terminal transition (the Consumer error path
   `_finalize_terminal_outcome` sends the terminal envelope, applies the
   policy, and then sets `should_stop = True`; every `work_failed` site
   ends terminal, persistent tasks included), or
   — the one live-task case — a persistent service task's per-message
   rejection (`HeartbeatTask._handle_work_message`, heartbeat.py ~:231,
   per [MF-3.2]). In the terminal case a row moved to `T{tid}.inbox` has
   no consumer, ever; in the Heartbeat case `requeue` would cycle the
   rejected row inbox→reserved→inbox forever. No *documented* same-TID
   rerun facility exists (review found one undocumented, unguarded API
   path — see §4); the two tests pinning `requeue` assert only the row
   move. Task policies become `keep | clear`. **Manager REQUEUE is kept in
   full** (Van, 2026-08-31): the shared `weft.spawn.requests` lane *does*
   have a live consumer, so the Manager's spawn-request return on
   stop/yield and its policy-driven restore of a failed child launch
   (`reserved_policy_on_error=requeue` on the Manager's own spec, pinned
   by `test_failed_child_launch_restores_source_and_retries_on_admission_deadline`)
   both stay exactly as they are. The `ReservedPolicy` enum therefore
   keeps `REQUEUE`; what is removed is its task-level *application*
   (`BaseTask._apply_reserved_policy` and the Heartbeat site) and its
   acceptance at the user-facing TaskSpec boundary.

## 2. Source Documents

- 07 [QUEUE.6] (line ~130): "`keep` leaves the reserved message in place,
  `requeue` moves it back to inbox, and `clear` deletes it".
- 05 [MF-2] (lines ~113–129): reservation flow, "crash leaves the message
  in reserved state for explicit operator recovery", "direct
  `run_work_item()` execution has no reserved message and must not mutate
  unrelated reserved backlog".
- 02 [TS-1.1] (lines ~620–632) and the field docs at lines ~154–155
  (`"reserved_policy_on_stop"`/`"_on_error"`: "Options: keep, requeue,
  clear"); the `Implementation status` line ~305 mapping
  `spec.cleanup_on_exit` to `BaseTask._cleanup_reserved_if_needed()`.
  The [TS-1.1] link to `05-…#8-failure-recovery-flow` is live (05:~1075
  "### 8. Failure Recovery Flow") — no fix needed; that section's "move or
  requeue work intentionally", README:~698 "explicit requeue", and
  06:~265 are operator `weft queue move` language: reword to "queue
  move", do not delete.
- 05 [MF-3.2] (~:359): the Heartbeat applies `reserved_policy_on_error`
  per rejected registration message and continues — the one live-task
  policy site.
- 05 [MF-6]: a failed child launch's "message remains governed by the
  manager's reserved-queue policy".
- `README.md` line ~705 lists the three values.
- `CLAUDE.md` §3 invariant line: "Reserved queue policy must be honored
  (keep/requeue/clear)".
- 05 [MF-5] / 07 [OBS.13.7]: preserved reserved rows are harvested by
  monitor reserved cleanup or `weft system prune --family task-local
  --task TID --apply --force --archive PATH`.

## 3. Context and Key Files

Files to modify:
- `weft/core/taskspec/model.py` — `ReservedPolicy` enum (:287–292)
  **unchanged** (keeps `REQUEUE` for the Manager's spec); the task-spec
  rejection lives at the public transport boundary (see below), not in
  the model
- `weft/core/tasks/base.py` — `_ensure_reserved_empty` (:2831–2848,
  delete), `_apply_reserved_policy` (:2864–2910, drop the REQUEUE branch),
  `_move_reserved_to_inbox` (:2912–2935, delete),
  `_cleanup_reserved_if_needed` (:2936–2946, delete — see invariants for
  why this is a no-op removal), call sites :2080–2085, :2114–2119
- `weft/core/tasks/consumer.py` — sites :738–744, :756–762, :847,
  :1172–1177, :1194–1199, `_apply_reserved_policy_on_error` :1276–1283;
  `_handle_active_work_result`'s successful-outcome call at :533 and
  `_finalize_deferred_active_control` at :707. The success caller must
  explicitly skip reserved policy while retaining deferred task/control
  finalization; failed outcomes keep their existing policy path.
- `weft/core/tasks/heartbeat.py` — `_delete_reserved_message` (:319–324,
  the `finally` drain after a success ack) and the per-message policy
  site in `_handle_work_message` (~:231–235, stays; KEEP/CLEAR only)
- `weft/core/tasks/interactive.py` — :250–252, ~:296–297 (limit-violation
  kill in `_interactive_flush_outputs`), :439–442, :543–546, :557–561; the
  abstract declarations of `_ensure_reserved_empty`/`_cleanup_reserved_if_needed`
  on the mixin interface (~:130–137)
- `weft/core/tasks/pipeline.py` — :256–257 (the success-ack drain in
  `_handoff_payload` — this is the third success-ack site) and :281–284
  (`_fail_edge`, an error site)
- Dead overrides to delete with the helpers: `SelectiveConsumer._cleanup_reserved_if_needed`
  (consumer.py ~:1420), `Monitor._cleanup_reserved_if_needed`
  (`weft/core/tasks/monitor.py:115`), `Observer._cleanup_reserved_if_needed`
  (`weft/core/tasks/observer.py:59`); the code comment at consumer.py
  ~:517 that cites `_ensure_reserved_empty` and `requeue`. Note
  `BaseTask.handle_termination_signal` passes `apply_reserved_policy=False`;
  the Consumer override supplies the policy application
- `weft/core/manager.py` — **REQUEUE logic untouched**:
  `_apply_spawn_reserved_policy` (:4433–4497) keeps all three branches;
  `_release_unlaunched_spawn_request` (~:4688), `_requeue_public_reserved_spawn_requests_before_yield`
  (~:3383), `_handle_child_launch_failure` (~:1318–1362) and
  `_schedule_admission_retry_for_source` stay as they are, and
  `tests/core/test_manager.py::test_failed_child_launch_restores_source_and_retries_on_admission_deadline`
  (~:2545) stays green. The only Manager edits are the backstop drains
  and their wrappers — four methods: `_ensure_reserved_queue_empty`
  (:4270–4293), `_cleanup_reserved_queue_if_needed`,
  `Manager._ensure_reserved_empty` (:~4520), `Manager._cleanup_reserved_if_needed`
  (:~4526) — and their callers at :1352–1354, :3893–3897, :4588–4590,
  :4613–4615. **Not a backstop, keep:** `_cleanup_own_internal_reserved_queue`
  (:~4331) is the Manager's own `internal_reserved` lifecycle cleanup
  under [OBS.13.9]
- Where `requeue` is rejected for task specs (review corrected the first
  draft on three facts: `transport.py::validate_taskspec_payload` is
  **not** the sole public validation owner — `model.py::validate_taskspec`
  (:1879, `model_validate_json`) backs `weft spec create/validate` via
  `commands/specs.py` :220/:287/:612; the Manager spec **does** cross the
  transport boundary at detached startup (`weft/manager_process.py:48`
  decodes it); and its only marker is advisory `metadata.role == "manager"`).
  Design that follows:
  - **Authoritative check at the task runtime boundary:** `BaseTask.__init__`
    rejects `REQUEUE` on either policy field with an explicit invariant
    error naming `keep`/`clear`, via a class-level hook (`_allowed_reserved_policies`)
    that `Manager` overrides to include `REQUEUE`. This closes the
    "internally constructed Consumer with REQUEUE silently behaves like
    KEEP" case and needs no trusted-context plumbing: a public payload
    that fakes `metadata.role` still spawns a `Consumer` (class dispatch
    uses the internal runtime-class key, which public submission strips)
    and fails at construction with the clear error; the request row stays
    in the manager's reserved queue under the manager's policy.
  - **Early courtesy rejection at every public validation surface**, all
    calling one shared predicate: `transport.py::validate_taskspec_payload`,
    `model.py::validate_taskspec`, and the `commands/specs.py` callers.
    The predicate rejects `requeue` unless `metadata.role == "manager"`;
    it is advisory only — the runtime check above is the contract.
- Docs: 07, 05, 02, README.md, CLAUDE.md §3
- Tests: `tests/tasks/test_task_execution.py` (`test_reserved_policy_requeue_on_stop`
  :4924, `_on_error` :5032 — replaced by rejection tests;
  `test_deferred_stop_on_ok_outcome_does_not_requeue_completed_work` :3107
  — delete its REQUEUE-specific scenario; extend the existing
  `test_deferred_stop_kill_finalizes_persistent_task_on_ok_outcome` :2906
  with failed-ack/CLEAR coverage), `tests/core/test_manager.py`
  (`test_manager_stop_mid_handler_requeues_reserved_work_unlaunched` :7806,
  `test_manager_leadership_requeues_reserved_public_work_before_yield`
  :7963 — keep; they pin the manager-internal move)

Read first: [QUEUE.6], [MF-2], `_apply_reserved_policy`, and the Consumer
error path at consumer.py:985–1008 (note the order: terminal envelope,
then policy, then `should_stop`).

## 4. Invariants and Constraints

- Reserved policy is applied **once within each disposition operation**
  (terminal transition or the Heartbeat's per-message rejection); leftover
  rows stay in `T{tid}.reserved` for operator/monitor recovery. No
  task-side or manager-side code drains a reserved queue after policy or
  acknowledgement has run.
- A successful work outcome has already chosen success acknowledgement as
  its row disposition. Deferred STOP/KILL still finalizes the task and
  acknowledges control, but applies no reserved policy to that completed
  input, whether the exact acknowledgement succeeded or failed. Do not
  infer permission for a second delete in that operation from the row still
  being present. A later independent idle control operation may apply its
  configured bulk CLEAR to remaining backlog; this is not lifetime immunity.
  Failed/cancelled outcomes continue to apply the configured policy once.
- `keep` and `clear` semantics are unchanged.
- `cleanup_on_exit`'s reserved-queue effect: every current
  `_cleanup_reserved_if_needed` call site is gated `policy is not KEEP` or
  follows a success ack (queue already empty), so its only live effect
  today is draining failed-policy/failed-ack residue — exactly what this
  plan removes on purpose. Deleting the method changes nothing else;
  update the 02 mapping line accordingly.
- A leftover *completed* reserved row is never re-executed (reservation is
  an inbox→reserved move; `_handle_reserved_message` only logs), so
  removing the success-ack fallback drain cannot cause duplicate execution.
- Manager REQUEUE semantics are unchanged (owner decision): stop/yield
  return of unlaunched requests, and policy-driven restore of failed
  launches under the Manager's own `reserved_policy_on_error=requeue`.
- Public contract change: task specs carrying `"requeue"` are rejected at
  the public validation surfaces with a message naming `keep` and `clear`,
  and unconditionally at `BaseTask.__init__` for non-Manager classes (the
  enum member remains for the Manager's spec). Release-note the
  consequence for stored specs and spawn payloads: `_build_child_spec`
  fails validation → `spawn_spec_validation_failed`, and the request row
  stays in the manager's reserved queue under its policy.
- "Backstop" is defined precisely: a drain that runs after policy
  application or after an exact acknowledgement. The Manager's own
  `internal_reserved` lifecycle cleanup ([OBS.13.9]) is not a backstop
  and stays.
- `cleanup_on_exit` removal is the removal of a *second destructive
  attempt*, not literally a no-op: if `_ensure_reserved_empty`'s
  `has_pending()` raises transiently, `_cleanup_reserved_if_needed` can
  still drain, and it can remove an older unrelated row after the active
  row was acknowledged. Both effects are exactly what the custody rule
  forbids.
- Same-task internal moves stay legal and are **not** reserved policy:
  `Consumer._handle_work_message`'s paused branch (~:174–195),
  `Consumer._requeue_reserved_message` (~:205, second reservation while
  work is in flight), `PipelineEdgeTask._requeue_reserved_message` (~:215)
  return a reserved row the live task has not begun executing to its own
  inbox — reservation bookkeeping under [QUEUE.5]. The [MF-2] delta says
  so explicitly.
- `clear` covers both shapes: the exact active row, or every row when no
  single active row is identified (the bulk `read_many` branch reached
  from `_handle_stop_request(apply_reserved_policy=True)`, base.py
  ~:1563/:1577, and all interactive sites). That bulk branch is the
  policy, not a backstop.
- Same-TID relaunch is reachable today through an undocumented, unguarded
  path: `Client.submit(resolved_spec)` with `tid` set →
  `submission.py` (`template = taskspec.tid is None`) →
  `submit_spawn_request(tid=…)` exact insert (SimpleBroker accepts the id
  once the old row is gone) → `Manager._build_child_spec` has no
  terminal-evidence guard. This does not rescue `requeue`. Per the Codex
  review, this plan does **not** promote a normative prohibition it does
  not enforce ([MF-3.2]'s terminal-proof sentence governs heartbeat
  singleton summaries, not relaunch in general): the [MF-2] delta states
  only that re-execution of preserved work is a new task plus a queue
  move. Whether to guard the API path is an **open owner decision**
  recorded in the ledger.
- Logging: the CLEAR branch in `_apply_reserved_policy` and the
  success-ack catch in `_finalize_message` log at DEBUG (the latter under
  `# pragma: no cover`). Raise both to WARNING and remove the pragma —
  otherwise the red tests' "warning was logged" is satisfied only by the
  drain's own warning that it is deleting.

Review gates: no new execution path; no mock of queue semantics
(failure injection at the queue-object seam only); external review before
implementation.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Spec Baseline

- `bcea628e` — 07, 05, 02 at plan authoring time (2026-08-31).
  Promotion baseline identifier: recorded after task 1 lands.

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/07-System_Invariants.md | A | [QUEUE.6] replace |
| docs/specifications/05-Message_Flow_and_State.md | A | [MF-2] bullet + insertion |
| docs/specifications/02-TaskSpec.md | A (text) + code slice (mapping line, field docs) | [TS-1.1], field docs, status line |

### [QUEUE.6] — replace the bullet

> - **QUEUE.6**: reserved-policy handling is explicit and applied once within
>   each disposition operation — the task's terminal
>   transition, or a persistent service task's per-message rejection
>   ([MF-3.2]): `keep` leaves the reserved row in place; `clear` deletes
>   the active row, or every row when no single active row is identified.
>   A row that survives a failed policy application or a failed
>   success-path acknowledgement delete remains in `T{tid}.reserved` for
>   operator recovery ([MF-5] prune). That operation must not retry its
>   disposition through a backstop or error policy. A successful work outcome
>   does not apply stop/error policy as part of that completion, including
>   when STOP/KILL was deferred during execution and the success
>   acknowledgement failed; task termination and control acknowledgement
>   still complete. A later independent idle control operation may apply its
>   configured bulk CLEAR to backlog. Residue has no lifetime exemption.
>   Monitor reserved cleanup covers
>   terminal non-completed families ([OBS.13]). No task-side or
>   manager-side code drains a reserved queue as a backstop, where a
>   backstop is a drain after policy application or after an exact
>   acknowledgement (the manager's own `internal_reserved` lifecycle
>   cleanup under [OBS.13.9] is not a backstop). Manager
>   spawn-request queues are not task reserved queues: the shared spawn
>   lane has a live consumer, so `requeue` remains valid for the manager's
>   own spawn-request disposition — unlaunched requests return to their
>   source queue on manager stop or leadership yield, and a failed child
>   launch is restored per the manager's reserved policy ([MF-6]).

### [MF-2] — replace "error, timeout, or external control applies the configured reserved policy" bullet and extend

> - error, timeout, or external control applies the configured reserved
>   policy (`keep` or `clear`) once within that disposition operation; rows
>   that survive a failed application, or a failed success-path
>   acknowledgement delete, are left in `T{tid}.reserved` for the explicit
>   recovery path below
> - a successful work outcome uses only success acknowledgement for its
>   input; deferred STOP/KILL completes task termination and acknowledges
>   control without applying reserved policy to that completed input,
>   even when its acknowledgement delete failed. A later independent idle
>   control operation may apply configured bulk CLEAR to remaining backlog
> - a live task may return a reserved row it has not begun executing to
>   its own inbox (pause, or a second reservation arriving while a work
>   item is in flight); that is reservation bookkeeping under [QUEUE.5],
>   not a reserved policy
> - crash leaves the message in reserved state for explicit operator
>   recovery; no policy is applied at restart. Re-execution of preserved
>   work is a new task plus a queue move

### [MF-2] ~128–129 — replace "A crash still leaves the active input in `T{tid}.reserved` for the explicit [QUEUE.6] recovery policy."

> A crash still leaves the active input in `T{tid}.reserved` for operator
> recovery ([MF-5] prune); no reserved policy is applied at restart.

### [TS-1.1] — replace lines ~624–625

> TaskSpec exposes `reserved_policy_on_stop` and `reserved_policy_on_error`.
> For task specs each accepts `keep` (default) or `clear`; the former
> task-level `requeue` value was removed on 2026-08-31 because a task's
> policy applies at the row's disposition point (terminal transition, or
> a service task's per-message rejection). A terminal task has no consumer
> for a returned row; a live Heartbeat would repeatedly reject the same row. Task specs carrying `requeue` are
> rejected at the public validation surfaces and at task construction
> with a message naming `keep` and `clear`. `requeue` remains valid on the
> manager's own spec for spawn-request disposition ([QUEUE.6], [MF-6]).
> The field-level JSON schema documentation for these two fields carries
> the same task/manager split. (Replace this section's link to
> `05-…#8-failure-recovery-flow` with direct references to [QUEUE.6] and
> [MF-2]; section 8 defines operator inspection and moves, not exact
> versus bulk `clear`.)

## 5. Tasks

1. **Independent review** of the plan and deltas (§8) — before promotion (runbook order: plan → delta review → promotion); the Codex and Claude rounds recorded below satisfy this for the current text, and any later revision re-enters it.
2. **Spec-promotion slice.** Apply all deltas — including the 02
   field-doc lines (~154–155, "Options: keep, clear" for task specs with
   the manager note) and the [MF-2] crash sentence — in this slice, so no
   window exists in which the JSON schema says `keep | requeue | clear`
   while [TS-1.1] says `keep | clear`. No mapping-claim changes yet. Add
   this plan under the existing `## Related Plans` headings of 07, 05,
   02 (do not create `## Plans`). Record the promotion baseline
   identifier here. Verify: `./.venv/bin/python -m pytest tests/specs/ -q`.
3. **Red tests** (seam: `_get_reserved_queue` is an uncached
   `self._queue(name)`, so wrap `_queue` for the reserved name).
   - `tests/tasks/test_task_execution.py`: (a) **success-ack fallback** —
     the exact `delete(message_id=...)` on the success path raises once;
     assert the row is still present, the task still completes, and the
     outbox holds the result, and a WARNING was logged. Red today
     (fallback drain consumes the row). (b) **failed CLEAR** — under
     `reserved_policy_on_error=CLEAR`, the reserved queue's `delete`
     raises once on the error path; assert the row persists, status is
     `failed`, WARNING logged. Red today (`_ensure_reserved_empty`
     silently achieves the delete). (c) `"requeue"` in a task-spec
     payload through the public boundary → rejection naming `keep` and
     `clear`; a Manager spec with `requeue` still validates. Red today.
   - `tests/core/test_manager.py`: a **failed** CLEAR (or REQUEUE) policy
     operation on the manager's spawn reserved queue leaves the residue
     row in place (red today: `_ensure_reserved_queue_empty` drains it).
     The default-`keep` case is green today and stays as characterization.
   - `tests/tasks/test_heartbeat.py`: exercise `_delete_reserved_message`'s
     `finally` — a failed exact ack must leave the row and re-raise the
     original error, with no drain.
   - Success ack with an unrelated reserved row present (consumer and
     `pipeline.py::_handoff_payload`): the unrelated row survives.
   - Public surface: `weft spec validate` / stored-spec rejection of
     `requeue`, not only a direct transport-validator test.
   - Extend the existing persistent successful-outcome deferred-control
     test with {STOP, KILL} × {ack succeeds, exact ack fails once}, both
     relevant policies CLEAR. On failed ack, the active reserved row
     survives with exactly one delete attempt; on successful ack it is
     absent. Both cases retain the result, expected terminal state and
     envelope, and control acknowledgement. The failure cells remain red
     even with the backstop drains disabled; they catch the second exact
     delete in `_finalize_deferred_active_control`. Keep non-ok outcome
     coverage proving its configured policy still fires once.
   - Delete `test_reserved_policy_requeue_on_stop`/`_on_error` and the
     REQUEUE-specific `test_deferred_stop_on_ok_outcome_does_not_requeue_completed_work`
     outright: `test_deferred_stop_kill_finalizes_persistent_task_on_ok_outcome`
     (:2906) already covers the CLEAR case end to end.
4. **Boundary.** Keep the enum; add the `BaseTask.__init__` runtime
   rejection with the `Manager` override, and the shared predicate at the
   three public validation surfaces (see §3); update README ~:705 and
   CLAUDE.md §3 (invariant line becomes "keep/clear for tasks; the
   manager's spawn lane may requeue"); reword the three "requeue"
   operator-language mentions to "queue move".
5. **Task-side consolidation.** One disposition method on `BaseTask`
   (`_apply_reserved_policy`, KEEP/CLEAR only, both CLEAR shapes) called
   from all listed sites; delete the three helpers, the mixin abstract
   declarations, the three dead overrides, and the stale comment. Raise the
   two DEBUG logs to WARNING and drop the pragma. Enumerate the three
   success-ack sites in the commit message. Give
   `_finalize_deferred_active_control` a keyword-only
   `apply_reserved_policy: bool = True`; its successful-outcome caller in
   `_handle_active_work_result` passes `False`. That parameter gates only
   reserved-policy application, never terminal transitions, envelopes, or
   control acknowledgement. Existing non-ok outcome callers keep the
   default. No new persisted flag, queue-presence probe, or retry path.
   Update the 02 mapping line.
6. **Manager side.** Delete the two backstop drains and their four
   call sites. Nothing else in the Manager changes; the three manager
   requeue tests stay green untouched.
7. **Traceability reconciliation.** Mapping claims + `Spec:` backlinks;
   deviation log closed; gates rerun.

Stop if: any caller needs reserved rows drained for correctness that the
tests cannot attribute to a failed policy/ack — report it, do not keep a
drain "just in case".

## 6. Testing Plan

`broker_env` with real `Consumer`/`Manager`; failure injection by wrapping
the queue object returned from `_queue(...)`, never by mocking SimpleBroker
internals. Existing KEEP/CLEAR observable tests stay green untouched. The
regression named: "a failed ack or failed policy application must not
destroy the reserved row".

## 7. Verification and Gates

Per task: `./.venv/bin/python -m pytest tests/tasks/test_task_execution.py tests/core/test_manager.py -q`.
Final: full suite + mypy + ruff (commands as in the ledger's child plans).
Rollback: revertable; the only persisted-format effect is rejecting
`"requeue"`, which is intended.

## 8. Independent Review Loop

Different agent family. Read the deltas and `_apply_reserved_policy`,
`_apply_spawn_reserved_policy`, the Consumer error path, and the manager
yield path. Stance: find any live consumer of a task's `T{tid}.inbox`
after that task is terminal (which would make `requeue` meaningful), and
any site where removing a drain leaves a row that some *other* invariant
requires gone.

## 9. Out of Scope

Monitor reserved cleanup rules ([OBS.13]); `weft system prune`; interactive
terminal sequencing (plan 3); any resurrection/same-TID relaunch facility
(explicitly rejected).

## 10. Fresh-Eyes Review

Author pass 2026-08-31: fixed the red-test seam (task side is `move_many`
batches, manager side `move_one`-then-fallback — now moot for REQUEUE, and
the surviving red test targets the success-ack delete); made the
`cleanup_on_exit` no-op proof explicit; separated the manager-internal
move from the enum so removing the value cannot break spawn return.

## Review Record (append-only)

**2026-08-31 — independent pre-promotion review of the deltas (Claude-family subagent; the cross-family review §8 asks for has NOT yet been run).** Verdict:
decisions sound; not promotable as first written. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — `test_failed_child_launch_restores_source_and_retries_on_admission_deadline` builds the Manager with `REQUEUE` and asserts auto-restore of a failed launch; `_schedule_admission_retry_for_source` assumes restoration | **Owner decision (Van, 2026-08-31): Manager REQUEUE is kept in full**, including policy-driven launch-failure restore. Enum keeps `REQUEUE`; Manager code and the test untouched; removal scoped to task-level application and the public task-spec boundary. Author's keep-only recommendation withdrawn |
| S1 — Heartbeat applies the policy per rejected message on a live task ([MF-3.2]); "once on the way to terminal" overclaims | Applied: §1, [QUEUE.6] and [MF-2] deltas reworded to "once per row at its disposition point"; heartbeat site added to §3 |
| S2 — inventory gaps (interactive limit-kill site, mixin abstract declarations, dead `Selector`/`monitor.py` overrides, pipeline success-ack site is :256–257 not :281–284, stale comment) | Applied to §3 |
| S3 — yield path already moves directly with different `require_unclaimed` semantics and its own logging | Applied: only `_release_unlaunched_spawn_request` repoints |
| S4 — delta would read as banning same-task pause/in-flight moves; `clear` wording must cover the bulk branch | Applied: [MF-2] sentence added; [QUEUE.6] `clear` wording covers both shapes |
| S5 — no red test for the failed-CLEAR half; DEBUG logs would not satisfy "warning logged" | Applied: red test (b) added; logs raised to WARNING, pragma removed, listed in task 5 |
| S6 — same-TID relaunch reachable via `Client.submit` with a resolved-spec `tid` (verified: exact re-insert succeeds; no terminal-evidence guard) | Applied: prohibition stated in [MF-2]; API guard recorded as follow-up, out of scope |
| Notes: anchor `#8-failure-recovery-flow` is live; operator "requeue" language is queue-move language; bespoke validator is performative; [QUEUE.6] monitor-recovery wording narrowed to terminal non-completed families | All applied |

**2026-08-31 — Codex (cross-model) pre-promotion review.** Verdict:
blocked as first written; deletion decision defensible; no case found where
a listed drain is the only protection. Dispositions:

| Finding | Disposition |
|---------|-------------|
| B1 — promotion slice would leave the 02 JSON schema saying `requeue` while [TS-1.1] says otherwise | Applied: field docs and crash sentence move into task 1 |
| B2 — delta prohibited same-TID relaunch while leaving the path working; [MF-3.2] does not justify a general prohibition | Applied: prohibition removed from the delta; API guard recorded as an open owner decision in the ledger |
| B3 — `validate_taskspec_payload` is not the sole public validation owner (`model.validate_taskspec` backs `weft spec create/validate`) | Applied: shared predicate at all three surfaces |
| B4 — task-side REQUEUE fallthrough undefined for internally constructed specs | Applied: authoritative `BaseTask.__init__` rejection with `Manager` override |
| B5 — manager exemption ambiguous; manager spec crosses the transport boundary; `metadata.role` is advisory | Applied: runtime check is the contract; public predicate is a courtesy; fake-role case terminates at construction |
| S — Heartbeat rationale, crash sentence, backstop definition excluding [OBS.13.9] cleanup, four Manager methods, `Observer` override, `SelectiveConsumer` name, cleanup_on_exit wording, missing tests, delete-not-retarget, order-of-operations fact, [TS-1.1] link target, `## Related Plans` heading | All applied |

**2026-09-07 — independent claim review at `bcea628e`; plan revision only.**
Initial verdict: BLOCKED. Class 5 revision of this implementation plan;
governing specs and code remain unchanged. Revised text requires independent
review before promotion; this record does not claim implementation or a
passing final review.

| Finding | Evidence and disposition |
|---------|--------------------------|
| R1 — deferred STOP/KILL with CLEAR retries disposition after a successful outcome's failed acknowledgement | Real SQLite probe, with only the two backstop drains disabled and one exact-delete failure injected: `After failed ack with plan drains removed: True`; `After deferred STOP CLEAR: False delete calls: 2`. Accepted: the successful-outcome caller disables reserved policy in deferred finalization while retaining terminal state, envelope, and control acknowledgement. Goal, invariant, exact [QUEUE.6]/[MF-2] deltas, implementation step, and STOP/KILL × ack-success/failure coverage now state the same rule; non-ok outcomes retain their policy path. |
| Adjacent contradictions in the changed slice | Corrected the stale terminal-only invariant for the already accepted Heartbeat exception, aligned the REQUEUE-test inventory with its deletion decision, and aligned spec field-doc promotion timing with task 2. Manager REQUEUE and the existing owner decisions remain unchanged. |


## Implementation Record (2026-09-07)

Class 5, hardened. Baseline: `caa1513b` (plan 1 committed); governing sections
remain those in Source specs. Independent same-family review: initially BLOCKED,
then PASS on these accepted corrections before promotion:

- Pipeline override handoff already writes the payload downstream before its
  reserved acknowledgement. Catch only that exact acknowledgement failure,
  log WARNING, preserve the source row, and complete the existing checkpoint
  and success bookkeeping without invoking error policy. Downstream write
  failure keeps the existing error path. Regression: CLEAR, one failed delete,
  exactly one downstream payload and delete attempt, preserved residue,
  warning, successful checkpoint and completed state.
- Correct the [TS-1.1] rationale: terminal tasks lack a consumer; a live
  Heartbeat would cycle the rejected row. The removal decision is unchanged.
- The shared public predicate must preserve validate_taskspec's
  `(False, errors)` return contract rather than let a ValueError escape.

The user requires one commit for this plan; strategy A promotion occurs in
this worktree before code edits and is committed with the full implementation.
Full pytest including slow tests, Ruff and all mypy targets are commit gates.
The root agent owns docs/validation and final registry reconciliation; delegated
runtime slices own their files and formatting. Full-suite testing runs only
once implementation and independent review fixes have settled.

Promotion baseline: governing spec files at `caa1513b` plus the promoted
[QUEUE.6], [MF-2], [TS-1.1] and field-doc edits in this worktree.


Completed-work review clarification (accepted, operation-scoped contract):
The original once-per-row wording conflicted with the explicit preserved bulk
CLEAR rule on a later independent idle STOP. Single-disposition protection
covers completion and its deferred finalization, not lifetime immunity from
later control operations. Updated [QUEUE.6], [MF-2], and the invariant above;
no new residue tracking or altered bulk policy. Independent reviewer: PASS on
this correction. A real failed-success-ack followed by later idle STOP test
pins both sides of the boundary.

Final source inventory also found a no-op `TaskMonitor._cleanup_reserved_if_needed`
override outside the original task-class list. Removed with the deleted base
hook; independent review confirmed no caller remained. The removal needs no
new behavioral test because the method returned without action.

Retired `RUFF-SUP-011` after Manager backstop removal made the guarded method
fall below the complexity limit; reconciled the suppression registry and its
counting tests. Focused boundary tests (76), runtime characterization tests,
and spec/suppression policy tests (92) passed. Full Ruff and mypy (192 source
files) passed after the final source edit.

Verification investigation: two full 12-worker runs failed startup/result waits
across unrelated process paths (7 failures under work-stealing, 10 under the
repository default scheduler). The first seven all passed unchanged at two
workers. Independent read-only diagnosis measured host load averages
208 / 127 / 123 on 16 logical CPUs and found no new persistent environment or
configuration mutation. Concurrency pressure is the leading explanation, not
proven pre-existing behavior. Launcher exit 11 is its parent-abort path, not a
segmentation fault. The next full gate uses two workers with every test,
assertion, and deadline unchanged.

Final gate: `python -m pytest -m '' -n 2` passed all 4,417 tests; 16
existing backend/provider opt-in skips. Full Ruff and mypy (192 source files)
passed on the final source. Independent completed-work review: no blocker,
including operation-scoped disposition and the final dead override removal.
No open spec deviations remain.
