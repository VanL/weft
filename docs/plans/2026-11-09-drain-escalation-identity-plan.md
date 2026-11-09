# Drain SIGTERM Rung and Forced Reap Verify Child Launch Identity

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]; docs/specifications/07-System_Invariants.md [LIVENESS.R5]
Superseded by: none

Class: 4 (risky trigger: the Manager's system-STOP path signals running task
processes, which is durable-spine execution control), with hardening applied.
No normative spec text changes: [MA-3] already requires the behavior ("Host
SIGTERM and forced reap apply only to same-host processes whose
`(pid, create_time)` identity is verified"), and LIVENESS.R5 requires
`(pid, create_time)` with zombie rejection for host identity. Only a Related
Plans backlink is added to spec 03. Escalation history: none. Plan type: bug
fix restoring specified behavior.

Theory: [WT-2a.13] (death needs proof; a failed or missing identity is
`unknown`, never permission), [WT-4.3] "Availability and death are different
questions", [WT-4.1] "Keep an identity you already have", [WT-4.4] "As few
concepts as possible" and "No over-armoring".

## 1. Goal

Stop the drain's SIGTERM rung (slice 1, `cc1613f5`) and the forced reap
(slice 2) from signalling a PID that no longer belongs to the child the
Manager launched.

`Manager._signal_children_to_stop` escalates a tracked user-work child that
has not ended `MANAGER_CHILD_STOP_ESCALATION_SECONDS` after its STOP by
calling `terminate_process_tree(pid, ...)` on the PID from
`Manager._child_stop_escalation_pids`. That helper returned
`child.process.pid` unconditionally. A child whose wrapper has exited stays in
the child table while the stopping drain holds it for a live managed survivor
(`_hold_for_managed_survivor`), during terminal-proof grace, or during a
publication retry, and `_child_has_exited` has already reaped it, so its PID
is free for reuse. `terminate_process_tree` builds a fresh
`psutil.Process(pid)` and signals that process and its whole descendant tree.
The sibling rung for drain stop targets (`_drain_target_live_pids`) already
re-verifies `(pid, create_time)`; the child rung did not.

Confirmed on `perf/fix-drain-spin` (b78506f1) by
`tests/core/test_manager.py::test_drain_sigterm_rung_signals_a_child_pid_only_while_its_identity_matches[reused-pid]`:
a live process whose recorded launch creation time is older than its real one
(a reused PID) was signalled (`AssertionError: signalled a process it never
launched`, `poll()` returned 0).

Slice 2, same bug class and owner: `Manager._terminate_children` (forced reap
at drain-window expiry, final cleanup, and the `SIGUSR1` pass) gated the
wrapper kill only on `child.process.is_alive()`, then called
`terminate_process_tree(child.process.pid, kill_after=True)` and
`child.process.kill()`. After another waiter (`psutil.wait_procs` in the
SIGTERM rung or in this reap) has reaped the wrapper, `multiprocessing` gets
`ECHILD`: `is_alive()` keeps returning True, and `Process.kill()` still sends
SIGKILL to the remembered PID. Confirmed by
`test_forced_reap_never_kills_a_reused_child_pid` on `cc1613f5`
(`AssertionError: force-reaped a process it never launched`).

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-3] (phase 1, STOP
  then host SIGTERM after the escalation delay; host SIGTERM and forced reap
  only for verified `(pid, create_time)` identity).
- `docs/specifications/07-System_Invariants.md` [LIVENESS.R5].
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (the Manager
  retains exact launch process identity in its scheduler evidence).
- Prior art in this file: `Manager._drain_target_live_pids`,
  `Manager._managed_pids_for_child` (create-time gated, [MA-1] item 4),
  `tests/cli/test_cli_system.py::test_system_stop_never_signals_a_reused_pid`.

## 3. Context and Key Files

Current structure:

- Every committed launch goes through `Manager._checkpoint_spawned_admission`,
  which records the wrapper's `{"pid", "create_time"}` (from
  `process_create_time` at commit) in `self._scheduler_residents[tid]
  ["host_processes"]` before startup feedback releases. A resident is never
  retired while its TID is in `_child_processes`
  (`_retire_scheduler_residents`), so the record exists for every tracked
  child.
- `pid_matches_create_time(pid, create_time)` (`weft/helpers`) is the shared
  identity check: equal creation time within 1 ms, zombie rejected.

Files changed:

- `weft/core/manager.py`:
  - `Manager._launched_child_liveness(tid, child)` (slice 2 extraction, the
    one shared check): `inspect_host_process(pid, launch create_time)` from
    the child's scheduler evidence, so `live` / `stale` / `unknown`; no
    recorded creation time is `unknown`. Before slice 2 the rung used the
    equivalent `pid_is_live(pid) and pid_matches_create_time(pid, created)`.
  - `Manager._child_stop_escalation_pids(tid, child)`: the rung's PID only
    when the verdict is `live`.
  - `Manager._terminate_children`: both wrapper kills (the tree terminate
    and `child.process.kill()`) require `is_alive()` and a `live` verdict,
    re-checked before each; a child whose verdict is `stale` is not reported
    as a survivor (its launched process is provably gone; only the view
    lags).
- `tests/core/test_manager.py`: four new tests (section 7) and two staged
  tests given the launch record every real child has (section 7).
- `docs/specifications/03-Manager_Architecture.md`: Related Plans backlink.
- `docs/plans/README.md`: index row and count.

Comprehension questions:

1. Why is `child.process.exitcode is None` not enough to prove the PID is
   still the child? Expected: `terminate_process_tree` reaps a direct child
   through `psutil.wait_procs`, after which `multiprocessing` gets `ECHILD`
   and `exitcode` stays `None` although the PID is free.
2. Why read the scheduler evidence rather than add a field to
   `ManagedChild`? Expected: the Manager already retains the exact launch
   identity there ([MF-5]) for every tracked child; a second copy would be a
   second record of the same fact.

## 4. Invariants and Constraints

- A live child the Manager launched is still SIGTERMed at the rung and still
  force-reaped (each proved by a real launch, section 7).
- The managed-PID loop in `_terminate_children` is unchanged (its PIDs come
  from the create-time-gated `_managed_pids_for_child`).
- The rung still runs once per child (`term_attempted` is set whether or not
  a PID qualified); the STOP rung and the drain-window forced reap
  (`_terminate_children`) are unchanged.
- No new identity mechanism, field, constant, or queue shape.

## 5. Observable-Difference Register

| Change | Class | Theory basis |
|--------|-------|--------------|
| The child SIGTERM rung signals a tracked child's PID only while its `(pid, create_time)` matches the identity recorded at launch; an exited, reaped wrapper's reused PID (and that process's descendants) is never signalled | C (restores [MA-3] and LIVENESS.R5) | [WT-2a.13]; [WT-4.3] availability and death are different questions |
| The forced reap (drain-window expiry, final cleanup, `SIGUSR1`) terminates and SIGKILLs a tracked child's PID only while its launch identity verifies; a reused PID behind a lagging `is_alive()` is never killed | C (restores [MA-3] "forced reap apply only to ... verified" and LIVENESS.R5) | [WT-2a.13]; [WT-4.3] availability and death are different questions |
| A tracked child whose launch creation time is unknown is neither SIGTERMed at the rung nor force-reaped; it is reported as a survivor (survival uncertain) | D (narrow) | [MA-3] "only ... whose identity is verified"; [WT-2a.13] unknown is not permission. Every tracked child gets a launch record at commit, and `process_create_time` fails there only when the process has already exited (or is a zombie on macOS), so a live child practically always has a known identity |
| A child whose launch identity is `stale` is no longer reported as a surviving process by the cleanup warning; before, a wrapper reaped by `psutil.wait_procs` was reported alive (`kill_issued: True` on a vanished PID) | C (the report is now accurate) | [WT-2a.13] stale identity is positive proof of exit (LIVENESS.R5) |
| A zombie wrapper (exited, not yet reaped) is no longer signalled | N (a signal to a zombie has no effect) | LIVENESS.R5 zombie rejection |
| One identity read per escalated child per drain and up to three per force-reaped child | N | [WT-4.4] measure; bounded to the rungs |

## 6. Rollout and Rollback

No rollout ordering. Rollback: revert the single commit; no persisted or
queue shape changes.

## 7. Testing Plan and Verification

- `test_drain_sigterm_rung_signals_a_child_pid_only_while_its_identity_matches`
  (parametrized `reused-pid` / `same-process`): owner-boundary test of
  `_signal_children_to_stop` with a real live process and the recorded launch
  identity in the scheduler evidence; the reused case must leave the process
  running, the same-process case must make it exit. Only the Manager's
  `Process` handle is a stand-in (it supplies the PID).
- `test_drain_sigterm_rung_reaches_a_child_this_manager_launched`: a real
  command task launched through the Manager's own path is escalated and
  exits, so the launch commit's identity record and the rung stay wired.
- `test_forced_reap_never_kills_a_reused_child_pid` (slice 2 RED): a tracked
  child whose process view lags (`_ReapedWrapperView`: `is_alive()` True,
  `kill()` signals the remembered PID, as `multiprocessing` does after
  `ECHILD`) and whose recorded launch creation time is older than the live
  process at that PID. `_terminate_children` must leave that process
  running and report no survivor.
- `test_forced_reap_still_kills_a_child_this_manager_launched` (slice 2
  positive control): a real command task launched through the Manager whose
  STOP goes to a queue it does not read is still force-reaped by
  `_terminate_children` inside its deadline.
- Two existing tests staged tracked children without the launch record every
  real child has, so slice 2 would treat them as unknown identity:
  `test_manager_terminate_children_kills_sigterm_trapping_descendant_tree`
  now records its real parent process's identity, and
  `test_manager_sentinel_failure_during_shutdown_still_reaps_children`
  records a real holder process for both fakes and replaces host signalling
  with a no-op (the fakes record `kill()`). Their assertions are unchanged;
  both pass on the pre-slice-2 code too.

Sensitivity controls (recorded):

- Pre-fix `weft/core/manager.py` (b78506f1): `reused-pid` fails with
  `AssertionError: signalled a process it never launched`.
- Rung disabled (`evidence = None` in the helper): the real-launch test fails
  (`_process_running(pid)` still True after a 30 s join), as does
  `same-process`. No other test in `tests/core/test_manager.py`,
  `tests/cli/test_cli_system.py`, `tests/cli/test_cli_serve.py` or
  `tests/cli/test_cli_manager.py` detected the disabled rung.
- Restored file verified by checksum; all pass.
- Slice 2, pre-fix (`cc1613f5`): `test_forced_reap_never_kills_a_reused_child_pid`
  fails with `AssertionError: force-reaped a process it never launched`; the
  positive control and the two restaged tests pass.
- Slice 2, verdict forced to `unknown` (`evidence = None` in
  `_launched_child_liveness`): the positive control fails
  (`_process_running(pid)` still True after `_terminate_children`).
- Slice 2 restored file verified by checksum (`942141f1`); all pass.

Gates: SQLite `-n 4` and `./bin/pytest-pg` on `tests/core/test_manager.py`
and the CLI stop files; `ruff check .`; `ruff format --check` on touched
files; the repo mypy command; `bin/check-doc-paths`.

## 8. Out of Scope

- The managed-PID loop in `_terminate_children` collects create-time-gated
  PIDs from `_managed_pids_for_child` at the start of the pass and signals
  them after waiting up to half the deadline without re-verifying; the PIDs
  carry no creation time past collection. Not changed here.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| none | | | | |

## Review and execution record

Author family: Claude. Branch `perf/fix-drain-escalation-identity`, cut from
`perf/fix-drain-spin` (b78506f1). Slice 1 (SIGTERM rung): `cc1613f5`.
Slice 2 (forced reap, shared check extracted) added at the coordinator's
direction after slice 1 recorded the forced-reap exposure. Worked in the author's isolated worktree
because the harness refused git operations in a second worktree; the
reviewed branch `perf/fix-drain-spin` is unchanged. Cross-family review: to
be recorded by the coordinator.
