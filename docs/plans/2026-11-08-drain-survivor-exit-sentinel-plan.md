# Stopping-Drain Survivor Exit Sentinel

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-3]; docs/specifications/06-Resource_Management.md [RM-5.2]
Superseded by: none

Class: 5 (normative spec text edited in [MA-1.6a]; promotion strategy B,
atomic) with hardening applied, because risky triggers fire: the change is on
the Manager's durable-spine execution path (stopping drain, child reap) and it
introduces a new cleanup lifecycle (kernel process-exit descriptors and
handles owned by the child-sentinel adapter). Escalation history: none; the
class was declared before the first edit. Plan type: bug fix with spec
revision.

Theory: [WT-4.3] "One reactor, one arbiter, a closed set of inputs" (OS
sentinel adapters, never defensive polling wait-caps) and "A quiet task costs
nothing an operator can see" (a supervising task runs no turns); [ALT-WT-002]
("Reconsider when: A local source exists that cannot call the arbiter and has
no adapter. Then add the adapter, not a cap."); [WT-4.4] "Shared function,
shared code", "One path on all platforms", "No over-armoring";
[WT-2a.13] (a system STOP is the Manager's responsibility to stop active
tasks).

## 1. Goal

Fix a pre-existing busy spin in the Manager's stopping drain (recorded as out
of scope by Track C, `perf/manager-reaping` 82024660).

During a system STOP, `Manager._drain_user_work_phase_holds` calls
`_cleanup_children(retain_managed_survivors=True)`. A child wrapper that has
exited while a managed process named by its task snapshot is still live (for
example the runner's subprocess, or a TERM-resistant grandchild) is held in
the child table so the drain keeps custody and can force-reap the survivor at
window expiry. The hold `continue`s before any child bookkeeping advances, so
`_manager_child_timeouts` keeps publishing the wrapper's PID-liveness recheck,
`max(sentinel_observed_ns, last_liveness_probe_ns) + 0.5 s`, which is in the
past 0.5 s after the wrapper's sentinel fired. `next_wait_timeout()` therefore
returns `0.0` and the reactor runs back-to-back turns (each re-reading the
child's task snapshot and probing managed PIDs) until the survivor exits or
the 15 s drain window expires. A held wrapper whose proof grace or
publication retry had started before the drain spins the same way once that
deadline passes.

Reproduced on base 08c2f87b by
`tests/core/test_manager.py::test_stopping_drain_survivor_hold_publishes_no_due_clock_and_wakes_on_exit`:
with a real exited wrapper and a real live survivor, and the Manager's wall
clock shifted five recheck intervals past the wrapper's exit,
`next_wait_timeout()` returned `0.0` (`AssertionError: 0.0`).

The fix makes the hold event-driven: the survivor's own kernel exit source
becomes the held TID's input in the existing child-sentinel adapter, and a
held wrapper publishes no child deadline.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a] (child exit
  reaches the reactor from one Manager-owned sentinel adapter; drain progress
  is deadline-driven; "These clocks must not be replaced by an unconditional
  zero timeout"), [MA-3] (phase 1 lasts until no user-work child remains or
  the window expires; forced reap of remaining process trees).
- `docs/specifications/06-Resource_Management.md` [RM-5.2] (platform
  readiness adapter; kernel child-exit source; no timer fallback).
- `docs/program-theory.md` [WT-4.3], [WT-4.4], [ALT-WT-002], [WT-2a.13].
- History: `949d6b3a` (2026-10-21, "Retain descendant custody during manager
  drain") introduced the hold; the recheck deadline came with the retired
  `2026-09-17-watcher-reactor-restoration-plan`.
- Related branch: `perf/manager-reaping` (Track C) plan
  `2026-11-08-manager-event-driven-reaping-plan.md`, section 13 (this bug).

## 3. Context and Key Files

Current structure (base 08c2f87b):

- `weft/core/manager.py::Manager._cleanup_children` holds an exited wrapper
  when `retain_managed_survivors` and any PID from
  `_managed_pids_for_child(tid)` (the create-time-verified host processes in
  the task's `weft.state.tasks.<tid>` runtime handle) is `pid_is_live`.
- `Manager._manager_child_timeouts` publishes, per child, the publication
  retry, the post-sentinel PID-liveness recheck, or the proof-grace end.
- `_ManagerChildSentinelAdapter` keeps `tid -> int` membership, waits with
  `multiprocessing.connection.wait` on one observer thread, suppresses a fired
  `(tid, sentinel)` and calls `notify_activity`. It never owned an OS object.
- `weft/core/runners/subprocess_runner.py::_open_child_exit_source` opened a
  pidfd (Linux) or kqueue `NOTE_EXIT` (macOS/BSD) for a PID; Windows raised
  `NotImplementedError` because the runner's Windows leaf waits on
  `Popen._handle`.

Files changed:

- `weft/core/process_exit.py` (new): the exit-source leaf moved out of the
  runner as `open_process_exit_source` / `ProcessExitSource`, plus a Windows
  leaf (`_winapi.OpenProcess(SYNCHRONIZE)`; winerror 87 means gone).
- `weft/core/runners/subprocess_runner.py`: imports the shared leaf; no
  behaviour change.
- `weft/core/manager.py`: `ManagedChild.survivor_hold_pid`;
  `Manager._hold_for_managed_survivor`; `_cleanup_children` uses it and
  clears the hold otherwise; `_manager_child_timeouts` skips a held child;
  `_ManagerChildSentinelAdapter.watch_exit` plus owned-source retirement.
- `docs/specifications/03-Manager_Architecture.md`,
  `docs/specifications/06-Resource_Management.md`: delta below, mappings,
  Related Plans.
- `tests/core/test_manager.py`, `tests/core/test_subprocess_runner.py`.

Comprehension questions:

1. Why is the wrapper's PID-liveness recheck meaningless for a held child?
   Expected: the hold is only entered after `_child_has_exited` returned
   True; the recheck exists to settle a lagging process view of the
   wrapper's own exit, which is already settled.
2. Why must the adapter, not the Manager, close a survivor source?
   Expected: the observer thread may be inside a wait that holds the
   descriptor or handle; closing a handle during `WaitForMultipleObjects` is
   undefined on Windows. The adapter retires the source under its lock and
   the observer closes it between waits.

## 4. Invariants and Constraints

Must not change:

- [MA-3] drain semantics: the held wrapper stays a phase-1 member until its
  survivors exit or the window expires; window expiry still force-reaps the
  child's managed PIDs through `_terminate_children`.
- [MA-1.6a] terminal-evidence-before-custody: once released, a held wrapper
  settles through the unchanged retry/grace/envelope path.
- Only the stopping drain holds (`retain_managed_survivors=True`); ordinary
  turns, the non-stopping drain and forced termination never hold, and they
  clear `survivor_hold_pid`.
- [IMPL.8]: the adapter stays broker-free; the Manager reactor alone reads
  snapshots, probes PIDs and mutates the child table.
- Runner exit-source semantics (Linux binding selection, errno
  classification, `None` for a gone process) are moved verbatim.
- No new queue, field on a queue row, constant, config knob or dependency;
  no polling deadline anywhere ([ALT-WT-002]).

## 5. Observable-Difference Register

| Change | Class | Theory basis |
|--------|-------|--------------|
| A stopping drain holding an exited wrapper for a live managed survivor publishes no child deadline for it; the wait timeout is the next drain deadline (escalation or window) instead of `0.0`, so the Manager runs no turns while it only supervises | C (no loss: the wrapper's exit is settled; the recheck re-asked a settled question) | [WT-4.3] quiet supervising task; [MA-1.6a] "must not be replaced by an unconditional zero timeout" |
| The survivor's exit wakes the reactor through its kernel exit source in the existing sentinel adapter; the spin noticed it on its next back-to-back turn | C (wake latency is one kernel event; CPU and broker reads during the hold drop from continuous to none) | [WT-4.3] OS sentinel adapters; [ALT-WT-002] add the adapter, not a cap |
| Several survivors: one is watched at a time (lowest PID); each survivor exit costs one turn that re-targets or releases | N | [WT-4.4] as few concepts as possible |
| If no exit source can be opened (unexpected `OSError`/`RuntimeError`), the hold logs a warning and is bounded by the drain window or any other wake; the spin would have noticed the exit at once | D (narrow) | [WT-4.3] never defensive polling; [WT-4.4] no over-armoring. Linux pidfd is already required by subprocess supervision ([RM-5.2]), kqueue always exists on macOS, and same-user `SYNCHRONIZE` access is normal on Windows, so the branch guards an environment where command tasks cannot run either |
| The exit-source leaf moves to `weft/core/process_exit.py`; its `RuntimeError` text now starts "Process exit supervision" instead of "Subprocess supervision" (cause chaining and the `Linux 5.3` / `libc ... pidfd_open` content unchanged) | N | [WT-4.4] shared function, shared code |
| Windows gains a process-exit source leaf, used only by the drain hold | N (new leaf; the runner's Windows path is unchanged) | [WT-4.4] one path on all platforms, narrow adapter leaves |

## 6. Spec Baseline and Delta (applied, strategy B)

Baseline: `docs/specifications/03-Manager_Architecture.md` and
`docs/specifications/06-Resource_Management.md` at 08c2f87b.

Delta (landed in the same commit as code and tests):

- [MA-1.6a] adapter paragraph: a TID's input may be a kernel process-exit
  source handed over for a managed process that outlived its wrapper
  (`weft/core/process_exit.py` leaves named); the adapter owns it and the
  observer closes a retired source between waits.
- [MA-1.6a] drain-clock paragraph: an exited wrapper held by a stopping drain
  for a live managed process publishes no child deadline; one live survivor's
  exit source wakes the drain, which re-targets or releases; if no source can
  be opened the hold is logged and bounded by the drain window, with no
  polling deadline.
- [MA-1.6a] implementation mapping: `Manager._hold_for_managed_survivor`,
  `_ManagerChildSentinelAdapter.watch_exit`,
  `weft/core/process_exit.py::open_process_exit_source`.
- [RM-5.2] implementation mapping: the kernel exit source is owned by
  `weft/core/process_exit.py::open_process_exit_source`.
- Related Plans backlinks in both specs.

## 7. Tasks

1. RED: add the drain-seam test (real wrapper, real survivor, real snapshot,
   real adapter; only the Manager's wall-clock binding is shifted). Confirm it
   fails on base with `AssertionError: 0.0`. Done.
2. Move the exit-source leaf to `weft/core/process_exit.py`, add the Windows
   leaf, and retarget the runner and its unit tests. Gate: runner tests pass.
3. Adapter: `watch_exit`, owned-source retirement on `add`/`watch_exit`/
   `remove`, closing between waits and at `close()`. Gate: adapter ownership
   test passes.
4. Manager: `_hold_for_managed_survivor`, hold reset, and the timeout skip.
   Gate: RED test passes; existing hold test adjusted (below).
5. Spec delta, mappings, plan index row.

The existing `test_shutdown_cleanup_retains_exited_wrapper_until_managed_pids_exit`
scripted liveness for a fabricated PID (515151). The hold now watches the
managed process's exit, so a PID with no process is released at once (the
exit source reports it gone). The test keeps its claim and scripted liveness
but names a real process (the test process itself).

## 8. Testing Plan

New or changed tests (no mock of queues, process lifecycle, or the adapter):

- `test_stopping_drain_survivor_hold_publishes_no_due_clock_and_wakes_on_exit`:
  the hold publishes no due clock long after the wrapper's sentinel fired,
  and the survivor's exit wakes the adapter, after which the drain releases
  the wrapper and finishes (`should_stop`).
- `test_manager_child_sentinel_adapter_closes_owned_exit_sources_off_wait`:
  replaced and removed sources are closed on the observer thread; `close()`
  releases a source in use.
- Runner exit-source unit tests now target `weft.core.process_exit`.

Sensitivity controls (recorded):

- Pre-fix `weft/core/manager.py` (base) with the new test:
  `AssertionError: 0.0` at the wait-timeout assertion.
- Fix with `watch_exit` replaced by `source.close()`:
  `AssertionError: survivor exit not observed` (`adapter.wait(15.0)` False).
- Restored file verified by checksum; the test passes.

## 9. Verification and Gates

- SQLite, `-n 4`: `tests/core/test_manager.py`,
  `tests/core/test_subprocess_runner.py`,
  `tests/core/test_manager_burst_bookkeeping.py`,
  `tests/core/test_manager_family_maintenance.py` (also with `-m ""`), and
  `tests/cli/test_cli_manager.py`, `tests/cli/test_cli_system.py`,
  `tests/test_harness_registration.py`.
- PostgreSQL: `./bin/pytest-pg` on the same core files and the two CLI files.
- `ruff check .`, `ruff format --check` on touched files, the repo mypy
  command, `bin/check-doc-paths`, `tests/specs/test_plan_metadata.py`.
- Not run here: the full suite and `tests/system/test_admission_stress.py`
  (coordinator-owned). Windows is verified by type checking with
  `--platform win32` only; CI's Windows job is the first runtime proof of
  the `OpenProcess` leaf.

## 10. Rollback

Revert the single commit. No queue shape, persisted data, or configuration
changes; rollback restores the spin and the runner-local leaf.

## 11. Composition with Track C (`perf/manager-reaping`)

Track C changes `_child_has_exited` (no probe until the sentinel fires),
`_record_reaped_admission` (Manager's own connection) and
`_child_summaries_for_log`. This plan changes `_cleanup_children`'s hold
branch, `_manager_child_timeouts`, the adapter, and a new helper. A held
wrapper has a fired sentinel, so Track C's gate passes it through unchanged,
and a survivor event fails `_drain_child_sentinel_events`' identity check, so
it never stamps `sentinel_observed_ns`. The two compose; no textual overlap in
`weft/core/manager.py`. Both edit [MA-1.6a] text, the [MA-1.6a] mapping line
and Related Plans in spec 03, and both add tests to `tests/core/test_manager.py`;
expect mechanical merge conflicts there.

Verified on 2026-11-08: `git merge-tree` against `perf/manager-reaping`
(82024660) conflicts only in `docs/plans/README.md` and spec 03;
`weft/core/manager.py` and `tests/core/test_manager.py` auto-merge. On a
throwaway merge, `tests/core/test_manager.py`,
`tests/core/test_manager_burst_bookkeeping.py`,
`tests/core/test_manager_family_maintenance.py` and
`tests/core/test_admission_recovery.py` passed on SQLite with `-n 4`
(408 passed, 3 skipped).

## 12. Out of Scope

- The drain's SIGTERM rung (`_child_stop_escalation_pids`) still signals
  `child.process.pid` of an already-exited wrapper without an identity check;
  a recycled PID could receive it. Pre-existing; not changed here.
- Watching every survivor at once (one source per survivor) instead of one at
  a time.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| none | | | | |

## Review and execution record

Author family: Claude. Implementation commit: `487c0671` on
`perf/fix-drain-spin`.

Cross-family review (Tier 1), 2026-11-08:

- Reviewer: Codex, invoked as `codex exec -s read-only`, reasoning effort
  high, over the plan, the spec delta and the implementation diff.
- Verdict: PASS, no findings ("no blocker").
- Reviewer evidence: confirmed the causal premise against baseline code; 20
  tests passed, 1 skipped; touched-file lint passed; 100 macOS
  exit-to-release probes passed; in-memory comparisons showed all three
  expired child-clock cases (PID-liveness recheck, proof grace, publication
  retry) return `0.0` on baseline and publish no deadline while held on the
  candidate. Consulted program theory, agent-context guidance and runbooks,
  lessons, and specs 03/06/07.
- Not rerun by the reviewer (read-only constraint): the broker-backed drain
  test and the reported PostgreSQL and Track C merge results.
- Dispositions: none required.
