# Reaped Wrapper Exit Proof Retires Its Resident

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-1.8]
Superseded by: none

Class: 4 (hardening applied: the change is on the Manager's durable-spine
execution path, the child reap and admission retirement). No normative spec
text changes. Escalation history: declared Class 2 at the first edit (restores
intended same-turn retirement, contract test
`test_manager_reaps_exited_child_on_its_sentinel_turn`); escalated to Class 4
after review finding F4, because the hardening trigger "execution touches the
durable spine" fires. See the deviation log. Plan type: bug fix.

Theory: [WT-2a.13] "death needs proof": a reaped wrapper's exit code is that
proof, held locally by the Manager; [WT-4.4] "No over-armoring" (no new state,
lifecycle or deadline).

## 1. Goal

On Windows CI (run `42751770206`, Python 3.12 and 3.14, `core-commands`), the
Manager reaped a child on its sentinel turn but did not retire the child's
scheduler resident, so `test_manager_reaps_exited_child_on_its_sentinel_turn`
and `test_handed_off_child_reap_reads_proof_once_and_acknowledges_nothing_again`
failed. The resident's admission slot stays held until a later refresh.

Cause: after the reap, `_record_reaped_admission` refreshes the resident and
`_retire_scheduler_residents` retires it only when every recorded host process
(`host_processes`: the wrapper's own `_task_pid` and the task's managed PIDs)
reads stale through host-PID liveness (`analyze_liveness` ->
`inspect_host_process`). Windows keeps an exited process in the process table
while any handle to it is open, and psutil's Windows backend reads such a PID
as present. The Manager's own `Process` object still holds the wrapper's handle
during the reap, so the wrapper it just reaped read live. The task's worker was
not a factor in these tests: on the normal return path the host runner joins or
stops it before the wrapper reports and exits (`weft/core/runners/host.py`, end
of `run_with_hooks`). That is not guaranteed on every exceptional or forced
exit; a worker still alive then keeps the resident held, by design.

Outcome: when the reap holds the wrapper's exit code, the Manager uses that
local exit proof for the wrapper's recorded identity and asks the host only
about the task's other recorded processes, so the reap turn retires the
resident on every platform.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a]: "After the
  sentinel fires, the Manager confirms exit from the local process view and,
  when that view lags ..., from host PID liveness on the published PID-liveness
  recheck."
- [MA-1.8] admission custody: retirement releases "positively exited
  processes"; a failed snapshot read changes nothing.
- `docs/program-theory.md` [WT-2a.13].
- Prior plan: `2026-11-08-manager-event-driven-reaping-plan.md` (same-turn
  reap and refresh through the Manager's own connection).

## 3. Context and Key Files

- `weft/core/manager.py`:
  - `_cleanup_children` reap block: after `child.process.join`, the `finally`
    pops the child, removes its sentinel, and calls `_record_reaped_admission`.
  - `_record_reaped_admission(tid, *, exited_pid)`: refreshes the resident from
    the task snapshot, then calls `_retire_scheduler_residents`.
  - `_retire_scheduler_residents(tids, *, exited_pids)` and
    `_scheduler_evidence_live(tid, evidence, *, exited_pid)`.
- Rejected alternative: closing the wrapper's `Process` at reap to release the
  handle. `_terminate_children` snapshots children before calling
  `_cleanup_children` and then calls `.process.is_alive()` and `.pid` on them;
  a closed `Process` raises `ValueError` there. Using the exit proof changes no
  object lifecycle.

## 4. Invariants and Constraints

Must not change:

- A resident retires only on positive evidence: every recorded identity is
  either the reaped wrapper with a known exit code or reads stale on the host.
- Unknown identity stays resident (no `host_processes`, a foreign hostname, or
  an identity whose liveness is `unknown`), except the existing [MA-1.8]
  retirement of a no-receipt resident whose reserved request is no longer
  pending (`_retire_scheduler_residents`), which is unchanged. The reaped
  wrapper's own identity is the one exception this plan adds: its known exit
  code replaces the host reading, including an `unknown` one.
- A managed process that outlives its wrapper (for example after a forced
  kill) keeps the resident held; only the wrapper's own identity is dropped.
- A wrapper whose exit status another waiter consumed (`exitcode` unknown) is
  still judged by the host, as before.
- A failed snapshot read during the reap changes nothing.
- The reap opens no broker; no new queue, row field, constant, deadline or
  config knob.

## 5. Observable-Difference Register

| Change | Class | Theory basis |
|--------|-------|--------------|
| On Windows the reap turn retires the reaped resident; before, it stayed held until a later refresh after the `Process` object was collected | C (admission capacity returns on the reap turn, as on POSIX) | [WT-2a.13] the exit code is death proof; [MA-1.6a] local process view |
| On POSIX, when the host reading for the reaped wrapper was `unknown` (for example access denied), the exit proof now retires the resident on the reap turn instead of holding it; an ordinary stale reading is unchanged | C (the exit code is stronger evidence than an unknown reading) | [WT-2a.13] |
| Same-turn retirement still requires the task's other recorded identities to read stale; a worker alive after a forced wrapper exit keeps the resident held | N | [MA-1.8] positive evidence |

## 6. Spec Baseline and Delta

No normative change. [MA-1.6a] already makes the local process view the exit
confirmation after the sentinel fires, and [MA-1.8] already retires on positive
exit evidence; this plan makes admission retirement use that view for the
wrapper. The helpers are not named in the spec's implementation mapping, so no
mapping note changes; code docstrings cite [MA-1.6a] and [MA-1.8].

## 7. Tasks

1. Regression test first: `test_reaped_wrapper_exit_proof_retires_resident_the_host_still_lists`
   in `tests/core/test_manager.py` applies the Windows host rule on every
   platform with a double on `weft.liveness.analysis.inspect_host_process`
   (the wrapper's PID reads live while the Manager's `Process` object for it is
   open), asserts the double is consulted (positive control), then asserts the
   reap turn retires the resident.
2. `_scheduler_evidence_live` gains keyword `exited_pid`: drop that PID's
   recorded identity; if nothing remains, the evidence is not live.
3. `_retire_scheduler_residents` gains keyword `exited_pids` (TID -> PID) and
   passes the TID's PID through.
4. `_record_reaped_admission` gains required keyword `exited_pid`; the reap
   passes `child.process.pid` when `child.process.exitcode` is known, else
   `None`. Direct test callers pass `exited_pid=None`, keeping what they
   measure.

## 8. Testing Plan

- The regression test fails before the change with the CI symptom (resident
  still present after the reap turn) and passes after it.
- Existing contract tests: `test_manager_reaps_exited_child_on_its_sentinel_turn`,
  `test_handed_off_child_reap_reads_proof_once_and_acknowledges_nothing_again`,
  `tests/core/test_admission_recovery.py`,
  `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_family_dispatch_scaling.py`.
- The three reap tests and the regression test also pass under a throwaway
  plugin that emulates the Linux sentinel-before-waitpid window.

## 9. Verification and Gates

- `tests/core/test_manager.py`, `test_manager_family_maintenance.py`,
  `test_admission_recovery.py`, `test_manager_burst_bookkeeping.py`,
  `test_family_dispatch_scaling.py`, `tests/cli/test_cli_serve.py` pass; ruff,
  format and mypy are clean on touched files.
- Release gates (`bin/release.py all`): full SQLite, `bin/pytest-pg --all`,
  extension and live-provider suites, then CI on Linux, macOS and Windows.

## 10. Rollback

Revert `57ba1724` together with the dependent caller and test changes in
`232afdf1` (the `exited_pid=None` call sites, the regression test and its
positive control); reverting `57ba1724` alone leaves callers passing an
unsupported keyword. No queue shape, persisted data or configuration changes;
rollback restores host-only liveness for the reaped wrapper.

## 11. Out of Scope

- Releasing process handles earlier (rejected above).
- Other observers (`weft status`, LivenessMonitor) may still see the reaped
  wrapper's PID on Windows until the Manager's `Process` object is collected,
  at the end of the reap pass; they hold no admission and need no change.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [DOM-15] process | Class 4: plan and review before implementation | Implemented and reviewed under a Class 2 claim; this plan was written after review finding F4 escalated the class | The durable-spine hardening trigger fires for any Manager reap-path change; the Class 2 claim relied on "conforms to intended behavior" and missed it | none |

## Review and execution record

Author family: Claude. Implementation commit: `57ba1724` (plus review fixes) on
`tid-sweep`.

Cross-family review (Tier 1), round 1, 2026-11-09, over `57ba1724` and the
serve-test identity fix `8ca0aa7c`:

- Reviewer: Codex, `codex exec -s read-only`, reasoning effort high.
- Verdict: `blocker: F1, F2`.
- F1 (P2): `tests/core/test_family_dispatch_scaling.py` called
  `_record_reaped_admission` without `exited_pid`. Accepted; it passes
  `exited_pid=None`, keeping the broker-call measurement unchanged.
- F2 (P2): the serve helper's PID fallback did not cover an identity with no
  recorded create time. Accepted; such identities use the PID.
- F3 (P3): the regression test had no activation check. Accepted; it now
  asserts the ordinary host check reads the exited wrapper as live before the
  reap.
- F4 (P3): Class 2 underjustified against the hardening trigger. Accepted;
  escalated to Class 4 with this plan and the deviation-log entry.

Round 2 (final scoped round), over `232afdf1`: verdict `no blocker`.

- F3 (P3): the positive control proved "not stale", not that the double was
  consulted. Accepted; the double records its calls and the test asserts the
  wrapper PID was inspected.
- F5 (P3): worker shutdown is guaranteed on the normal return path only, and
  an `unknown` wrapper reading on POSIX is now overridden. Accepted; §1 and the
  register say so.
- F6 (P3): the invariant omitted the existing no-receipt retirement. Accepted;
  §4 keeps it explicitly.
- F7 (P3): rollback named one commit. Accepted; §10 names both.
