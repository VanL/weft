# Drain and Cleanup Signals Verify Process Identity

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-3]; docs/specifications/07-System_Invariants.md [LIVENESS.R5]
Superseded by: none

Class: 4 (risky trigger: the Manager's system-STOP and cleanup paths signal
running task processes, which is durable-spine execution control), with
hardening applied. No normative spec text changes: [MA-3] already requires
the behavior ("Host SIGTERM and forced reap apply only to same-host processes
whose `(pid, create_time)` identity is verified"), and LIVENESS.R5 requires
`(pid, create_time)` with zombie rejection for host identity. Only a Related
Plans backlink is added to spec 03. Escalation history: none (slices 2 to 4
were added at the coordinator's direction, slice 4 on the owner's decision;
same class, owner, and rule).
Plan type: bug fix restoring specified behavior.

Theory: [WT-2a.13] (death needs proof; a failed or missing identity is
`unknown`, never permission), [WT-4.3] "Availability and death are different
questions", [WT-4.1] "Keep an identity you already have", [WT-4.4] "As few
concepts as possible" and "No over-armoring".

## 1. Goal

The Manager signals processes by PID in three places during a system STOP
and its cleanup. Each one can act on a PID that, by the time of the signal,
names a process the Manager never launched, and `terminate_process_tree`
then signals that process and its whole descendant tree. After this plan,
every one of them signals only the process whose recorded
`(pid, create_time)` still verifies at signal time: the SIGTERM rung checks
with `inspect_host_process` and signals only on `live`; the two forced-reap
sites signal through `weft/helpers::terminate_verified_process_tree`
[CC-3.2], which verifies the same identity and signals through the verified
`psutil` instance (slice 4, owner decision 2026-11-09).

| Slice | Commit | Signal site | How the PID goes stale |
|-------|--------|-------------|------------------------|
| 1 | `cc1613f5` | Drain SIGTERM rung (`_signal_children_to_stop` via `_child_stop_escalation_pids`) for a tracked child's wrapper | The rung returned `child.process.pid` unconditionally. A wrapper that has exited stays tracked while the stopping drain holds it for a live managed survivor (`_hold_for_managed_survivor`), during terminal-proof grace, or during a publication retry; `_child_has_exited` has already reaped it, so its PID is free for reuse |
| 2 | `63d1383d` | Forced reap of the wrapper in `_terminate_children` (drain-window expiry, final cleanup, `SIGUSR1`): `terminate_process_tree(child.process.pid, kill_after=True)` and `child.process.kill()` | Gated only on `child.process.is_alive()`. Once another waiter (`psutil.wait_procs` in the rung or in this reap) has reaped the wrapper, `multiprocessing` gets `ECHILD`: `is_alive()` keeps returning True and `kill()` still sends SIGKILL to the remembered PID |
| 3 | `74a5bcb0` | Forced reap of the child's managed processes in `_terminate_children` | The PIDs were collected (create-time gated) before the graceful wait, which lasts up to half the cleanup deadline, and signalled afterwards without re-verification; the creation time was dropped at collection |
| 4 | this commit | Both forced-reap sites (slices 2 and 3) | Not a new hazard: slices 2 and 3 checked with `inspect_host_process` and then signalled by PID through `terminate_process_tree`, leaving a check-to-use window, and that helper's `psutil.wait_procs` reaps a direct child, which creates the `ECHILD` lag slice 2 had to handle. Slice 4 replaces check-then-signal with `terminate_verified_process_tree` |

The sibling rung for drain stop targets (`_drain_target_live_pids`) already
re-verified `(pid, create_time)` before signalling; these three did not.

RED evidence, each on the commit before its slice:

- Slice 1, `b78506f1`:
  `test_drain_sigterm_rung_signals_a_child_pid_only_while_its_identity_matches[reused-pid]`
  failed with `AssertionError: signalled a process it never launched`.
- Slice 2, `cc1613f5`: `test_forced_reap_never_kills_a_reused_child_pid`
  failed with `AssertionError: force-reaped a process it never launched`.
- Slice 3, `63d1383d`:
  `test_forced_reap_never_kills_a_managed_pid_reused_during_the_wait` failed
  with `AssertionError: force-reaped a reused managed PID`.
- Slice 4 changes the mechanism, not the outcome, so its evidence is the
  sensitivity controls in section 7: bypassing the verified helper at either
  forced-reap site reproduces the slice 2 and slice 3 failures.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-3] (phase 1, STOP
  then host SIGTERM after the escalation delay; forced reap at window expiry;
  host SIGTERM and forced reap only for verified `(pid, create_time)`
  identity), [MA-1] item 4 (create-time-gated managed PIDs).
- `docs/specifications/07-System_Invariants.md` [LIVENESS.R5].
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5] (the Manager
  retains exact launch process identity in its scheduler evidence).
- Prior art: `Manager._drain_target_live_pids`,
  `Manager._settle_drain_stop_targets` (`inspect_host_process`),
  `tests/cli/test_cli_system.py::test_system_stop_never_signals_a_reused_pid`.

## 3. Context and Key Files

Identity records the checks read (both exist before this plan):

- Wrapper: every committed launch goes through
  `Manager._checkpoint_spawned_admission`, which records the wrapper's
  `{"pid", "create_time"}` (from `process_create_time` at commit) in
  `self._scheduler_residents[tid]["host_processes"]`. A resident is never
  retired while its TID is in `_child_processes`
  (`_retire_scheduler_residents`), so every tracked child has the record.
- Managed processes: the task's own `weft.state.tasks.<tid>` snapshot names
  them in its runtime handle's `host_processes`, each with the creation time
  the task recorded. `live_host_processes_from_handle` returns the
  `(pid, create_time)` pairs that still match at collection.
- The identity check: `weft/liveness/host.py::inspect_host_process(pid,
  create_time)` returns `live` (creation time matches within 1 ms, not a
  zombie), `stale` (absent, zombie, or mismatched: positive proof the
  recorded process is gone), or `unknown` (no recorded creation time, or the
  process cannot be read). Only `live` permits a signal. The SIGTERM rung
  and the survivor report use it.
- The verified signal: `weft/helpers::terminate_verified_process_tree(pid,
  create_time, kill=..., timeout=...)` [CC-3.2] acquires the `psutil`
  instance only if the same identity verifies (no recorded creation time,
  zombie, absent or mismatched: no signal), signals that instance and the
  descendants acquired from it, and waits by polling the instances without
  `waitpid`. With `kill=False` it sends SIGTERM, waits up to `timeout`,
  SIGKILLs survivors and waits up to `timeout` again, the same two phases as
  `terminate_process_tree(..., kill_after=True)`. The host runner, `BaseTask`
  and `weft task` control already use it. The forced reap uses it.

Files changed:

- `weft/core/manager.py`:
  - `Manager._launched_child_liveness(tid, child)` (slice 2): the wrapper's
    verdict, `inspect_host_process` on its PID and the creation time in its
    scheduler evidence. Slice 1 used the equivalent `pid_is_live(pid) and
    pid_matches_create_time(pid, created)`; slice 2 replaced it.
  - `Manager._child_stop_escalation_pids(tid, child)`: the rung's PID only on
    a `live` verdict (slice 1, reworded in slice 2).
  - `Manager._terminate_children`: the wrapper's tree terminate and its
    second kill (formerly `child.process.kill()`) are
    `terminate_verified_process_tree(pid, launch create time, kill=False,
    timeout=remaining / 2)` and, if the wrapper is still alive,
    `terminate_verified_process_tree(..., kill=True, timeout=remaining)`;
    managed PIDs keep their recorded creation times from collection
    (slice 3) and each is signalled with
    `terminate_verified_process_tree(pid, recorded create time, kill=False,
    timeout=remaining / 2)` (slice 4). Slice 2's check-then-signal gates and
    its `child.process.kill()` call are gone. The survivor report omits a
    wrapper or managed PID whose identity is `stale` (slices 2 and 3; still
    needed because the SIGTERM rung's `psutil.wait_procs` can still reap a
    wrapper and leave `is_alive()` lagging).
  - `Manager._launched_child_create_time(tid, child)` (slice 4): the
    creation time recorded at launch, shared by `_launched_child_liveness`
    and the wrapper's verified signals.
  - `Manager._managed_processes_for_child(tid)` (slice 3): the child's live
    managed PIDs mapped to their recorded creation times;
    `_managed_pids_for_child` is now its key set (the survivor hold and
    `_cleanup_children` keep using it unchanged).
- `tests/core/test_manager.py`: new tests and restaged tests (section 7).
- `docs/ruff-suppression-registry.md` and `tests/specs/test_ruff_policy.py`
  (slice 4): `RUFF-SUP-214` retired. Removing a test fake's now-dead `kill()`
  dropped `test_manager_terminate_children_kills_sigterm_trapping_descendant_tree`
  below the C901 threshold, so its `noqa` was unused; the registry row, the
  generated index, the raw inventory (`C901=110`) and the policy test's
  approved groups and counts are updated together.
- `docs/specifications/03-Manager_Architecture.md`: Related Plans backlink.
- `docs/plans/README.md`: index row and count.

Comprehension questions:

1. Why is `child.process.exitcode is None` not enough to prove the PID is
   still the child? Expected: `terminate_process_tree` reaps a direct child
   through `psutil.wait_procs`, after which `multiprocessing` gets `ECHILD`
   and `exitcode` stays `None` (and `is_alive()` True) although the PID is
   free.
2. Why read the scheduler evidence rather than add a field to
   `ManagedChild`? Expected: the Manager already retains the exact launch
   identity there ([MF-5]) for every tracked child; a second copy would be a
   second record of the same fact.
3. Why is the create-time gate at collection not enough for managed PIDs?
   Expected: collection precedes a graceful wait of up to half the cleanup
   deadline; a managed process can exit and its PID be reused during it.

## 4. Invariants and Constraints

- A live child the Manager launched is still SIGTERMed at the rung and still
  force-reaped, and a live managed process that is still the recorded one is
  still force-reaped (each proved by a positive control, section 7).
- The rung still runs once per child (`term_attempted` is set whether or not
  a PID qualified); the STOP rung, the graceful wait, the deadline split and
  the survivor report's shape are unchanged.
- The survivor hold (`_hold_for_managed_survivor`) and `_cleanup_children`
  keep their PID sets; only `_terminate_children` reads identities.
- The SIGTERM rung stays SIGTERM-only with its `inspect_host_process` check
  (slice 4 leaves it unchanged); the forced reap keeps its deadline split.
- No new identity mechanism, field, constant, or queue shape.

## 5. Observable-Difference Register

| Change | Class | Theory basis |
|--------|-------|--------------|
| The child SIGTERM rung signals a tracked child's PID only while its `(pid, create_time)` matches the identity recorded at launch; an exited, reaped wrapper's reused PID (and that process's descendants) is never signalled | C (restores [MA-3] and LIVENESS.R5) | [WT-2a.13]; [WT-4.3] availability and death are different questions |
| The forced reap (drain-window expiry, final cleanup, `SIGUSR1`) terminates and SIGKILLs a tracked child's PID only while its launch identity verifies; a reused PID behind a lagging `is_alive()` is never killed | C (restores [MA-3] "forced reap apply only to ... verified" and LIVENESS.R5) | [WT-2a.13]; [WT-4.3] |
| The forced reap signals a managed PID only while the creation time recorded for it still matches at signal time; a managed PID reused during the graceful wait is never killed | C (restores [MA-3] and [MA-1] item 4 at the moment of the signal) | [WT-2a.13]; [WT-4.3] |
| A tracked child whose launch creation time is unknown is neither SIGTERMed at the rung nor force-reaped; it is reported as a survivor (survival uncertain) | D (narrow) | [MA-3] "only ... whose identity is verified"; [WT-2a.13] unknown is not permission. Every tracked child gets a launch record at commit, and `process_create_time` fails there only when the process has already exited (or is a zombie on macOS), so a live child practically always has a known identity |
| A managed PID whose recorded creation time is unknown (collected on liveness alone) is no longer force-reaped; while live it is still reported as a surviving managed PID | D (narrow) | [MA-3]; [WT-2a.13]. Tasks record a creation time for each managed process when they register it (`BaseTask`, host runner); unknown means the process was already gone or unreadable then |
| A wrapper or managed PID whose identity is `stale` is not reported as surviving by the cleanup warning; before, a wrapper reaped by `psutil.wait_procs` was reported alive (`kill_issued: True` on a vanished PID), and a reused managed PID would have been reported as a survivor | C (the report is now accurate) | [WT-2a.13] stale identity is positive proof of exit (LIVENESS.R5) |
| A zombie wrapper or managed process (exited, not yet reaped) is no longer signalled | N (a signal to a zombie has no effect) | LIVENESS.R5 zombie rejection |
| The forced reap signals through the identity-verified `psutil` instance (`terminate_verified_process_tree`) instead of checking and then signalling by PID, so a PID reused between check and signal cannot be hit; its waits poll the verified instances instead of calling `waitpid`, so the forced reap no longer reaps the wrapper itself and `multiprocessing` keeps an accurate `is_alive()` for it | C (closes the check-to-use window the cross-family review noted) | [WT-4.4] shared function, shared code ([CC-3.2] helper); [WT-2a.13] |
| When the forced reap's budget is exhausted, the wrapper's second kill is a verified SIGKILL of the wrapper and the descendants acquired from it, not `child.process.kill()` on the wrapper alone | C ([MA-3] forcefully reaps the remaining user-work process trees) | [MA-3]; [CC-3.2] |
| A forced-reap PID skipped for an unknown or mismatched identity is logged at warning level by the helper ("no exact creation-time signal authority" / "creation-time identity mismatch"); slice 3 skipped silently | N (diagnostic only) | [WT-4.4] try, catch, log |
| One identity read per escalated child per drain; one verified acquisition per forced-reap signal (wrapper: up to two, managed PID: one), plus the survivor-report check | N | [WT-4.4] measure; bounded to the signal sites |

## 6. Rollout and Rollback

No rollout ordering. Rollback: revert the slice commits, newest first (slice
4 alone, then 3, 2, 1); no persisted or queue shape changes. Reverting slice
4 restores `RUFF-SUP-214` with its test fake's `kill()`.

## 7. Testing Plan and Verification

New tests (`tests/core/test_manager.py`), each at the owner boundary with
real processes and real identity checks; a stand-in replaces only the
Manager's `Process` handle where a test must reproduce its lagging view:

- Slice 1: `test_drain_sigterm_rung_signals_a_child_pid_only_while_its_identity_matches`
  (`reused-pid` must leave a live process with an older recorded creation
  time running; `same-process` must make it exit) and
  `test_drain_sigterm_rung_reaches_a_child_this_manager_launched` (a real
  command task launched through the Manager is escalated and exits, so the
  launch record and the rung stay wired).
- Slice 2: `test_forced_reap_never_kills_a_reused_child_pid`
  (`_ReapedWrapperView`: `is_alive()` True and `kill()` signals the
  remembered PID, as `multiprocessing` does after `ECHILD`; the live process
  at that PID must survive and no survivor is reported) and the positive
  control `test_forced_reap_still_kills_a_child_this_manager_launched` (a
  real launched child whose STOP goes to a queue it does not read is still
  force-reaped inside the deadline).
- Slice 3: `test_forced_reap_never_kills_a_managed_pid_reused_during_the_wait`
  (the task snapshot names a PID with a creation time older than the live
  process now holding it; collection is stubbed to report that identity as
  verified before the wait, so only the signal-time check can stop the kill;
  the process must survive and no survivor is reported) and the positive
  control `test_forced_reap_still_kills_a_live_managed_survivor` (a real
  process named with its real creation time is killed).

Slice 4 adds no test: its outcome is the slices 2 and 3 contracts, which
the same reused-PID tests and positive controls hold; its sensitivity
controls (below) show the verified helper is what holds them.

Restaged existing tests (test, not product: they built tracked children
without the identity records every real child has, or observed a signal call
the code no longer makes; their claims are unchanged):

- Slice 2: `test_manager_terminate_children_kills_sigterm_trapping_descendant_tree`
  records its real parent process's launch identity;
  `test_manager_sentinel_failure_during_shutdown_still_reaps_children`
  records a real holder process for both fakes and replaces host signalling
  with a no-op (the fakes record `kill()`). Both pass on `cc1613f5`.
- Slice 3: the `_terminate_children` tests that stubbed
  `_managed_pids_for_child` now stub `_managed_processes_for_child`:
  `test_manager_terminate_children_kills_sigterm_trapping_managed_pid`
  (records the trapping process's real creation time),
  `test_manager_cleanup_terminates_reaped_child_managed_pids` (a real holder
  process instead of the fabricated PID 515151; host signalling stays
  recorded), `test_manager_cleanup_retains_live_managed_pid_after_wrapper_exit`
  (PID 515151 with an unknown creation time, still reported as surviving),
  and three tests that stub an empty set.
- Slice 4: `test_manager_sentinel_failure_during_shutdown_still_reaps_children`
  now records `terminate_verified_process_tree` calls (fabricated launch
  identities, no host process) and asserts the user-work child, and never
  the service, is signalled TERM-then-KILL with its launch creation time;
  `test_manager_cleanup_terminates_reaped_child_managed_pids` records the
  helper and asserts the recorded creation time reaches it with the split
  budget and `kill=False` (its real holder process is no longer needed);
  the now-dead `kill()` methods of `_ReapedWrapperView` and of the
  descendant-tree test's fake are removed.

Sensitivity controls (each restored by checksum afterwards; all tests pass on
the restored file):

- Slice 1: pre-fix `b78506f1` fails `reused-pid` as quoted in section 1.
  With the rung disabled (`evidence = None`), the real-launch test fails
  (child still running after a 30 s join), as does `same-process`; no other
  test in `tests/core/test_manager.py` or the CLI stop/serve/manager files
  detected it.
- Slice 2: pre-fix `cc1613f5` fails the reused-PID test as quoted; the
  positive control and the restaged tests pass. With the verdict forced to
  `unknown`, the positive control fails (`_process_running(pid)` still True).
  Restored checksum `942141f1`.
- Slice 3: pre-fix `63d1383d` fails the reused-managed-PID test as quoted;
  the positive control passes. With every managed signal skipped (the
  `live` test made unsatisfiable), the positive control fails
  (`TimeoutExpired`: the survivor is still running after 15 s). Restored
  checksum `b2a3d9e2`.
- Slice 4, helper bypassed at the wrapper site (its first signal reverted to
  `terminate_process_tree(pid, timeout=remaining / 2.0, kill_after=True)`):
  `test_forced_reap_never_kills_a_reused_child_pid` fails with
  `AssertionError: force-reaped a process it never launched`.
- Slice 4, helper bypassed at the managed site (same reversion):
  `test_forced_reap_never_kills_a_managed_pid_reused_during_the_wait` fails
  with `AssertionError: force-reaped a reused managed PID`.
- Slice 4, launch creation time withheld from the wrapper's helper calls
  (`created = None`): `test_forced_reap_still_kills_a_child_this_manager_launched`
  fails (`_process_running(pid)` still True). Restored checksum `9be3e358`.

Gates, run for each slice: SQLite `-n 4` on `tests/core/test_manager.py`,
`tests/cli/test_cli_system.py`, `tests/cli/test_cli_manager.py`,
`tests/cli/test_cli_serve.py` (`-m ""`) and the sibling Manager files;
`./bin/pytest-pg` on `tests/core/test_manager.py`,
`tests/cli/test_cli_system.py`, `tests/cli/test_cli_manager.py` and
`tests/core/test_manager_burst_bookkeeping.py`; `ruff check .`;
`ruff format --check` on touched files; the repo mypy command;
`bin/check-doc-paths`. Slice 4 also ran
`tests/tasks/test_runtime_identity_signals.py` (the helper's own callers),
`tests/specs/test_ruff_suppression_index.py`,
`tests/specs/test_ruff_policy.py` and
`bin/ruff_suppression_index.py --check`.

## 8. Out of Scope and Decisions

- Owner decision, 2026-11-09 (the cross-family reviewer's option, recorded
  below): the forced kills use `weft/helpers::terminate_verified_process_tree`
  [CC-3.2] at both forced-reap sites, `kill=False` with the existing deadline
  split; the SIGTERM-only rung keeps its `inspect_host_process` check.
  Implemented as slice 4. The helper cannot serve the rung, which must not
  escalate to SIGKILL, so the rung's window between check and signal
  remains (microseconds; the reviewer noted it predates this plan), as does
  the rung's `psutil.wait_procs` reap of the wrapper, which is why the
  survivor report keeps its `stale` override.
- On Windows a process handle held by `multiprocessing` prevents PID reuse
  while a child is tracked, so the wrapper hazards are POSIX ones; the gates
  are harmless there.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| none | | | | |

## Review and execution record

Author family: Claude. Branch `perf/fix-drain-escalation-identity`, cut from
`perf/fix-drain-spin` (`b78506f1`). Slice 1 `cc1613f5`; slice 2 `63d1383d`;
slice 3 `74a5bcb0`; review record `e9262232`; slice 4 is the commit that
adds this sentence. Slices 2 and 3 were added at the coordinator's direction
after each previous slice recorded the next exposure; slice 4 implements the
owner's 2026-11-09 decision on the reviewer's option below. Work happened in
the author's isolated worktree because the harness refused git operations in
a second worktree; `perf/fix-drain-spin` is unchanged. Slices 1 to 3 were
reviewed together (below); review of slice 4: to be recorded by the
coordinator.

### Cross-family review (coordinator)

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-09,
scoped to the three commits `b78506f1..74a5bcb0` (base `perf/fix-drain-spin`,
already reviewed PASS). Verdict: no blocker, no in-scope findings; both D rows
consistently serve [WT-2a.13] (unknown identity grants no signal authority).
Reviewer answer to the §8 question, recorded as an owner option, not a
blocker: prefer `weft/helpers::terminate_verified_process_tree(..., kill=False)`
at the two forced-reap sites (`_terminate_children`), preserving the deadline
split, because it keeps the verified `psutil` instance across check and use
and does not steal the wrapper's reap; the SIGTERM-only rung keeps its current
check. The remaining check-to-use race and reap interference predate this
change. Process tests and mutation controls were inspected, not rerun.

### Slice 4 cross-family review (coordinator)

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-09,
scoped to `e9262232..0538cca6` (owner decision: forced reaps use
`terminate_verified_process_tree`). Verdict: no blocker, no in-scope findings.
The reviewer confirmed the shared verified helper with recorded creation times
and the existing budget split, that both D rows serve [WT-2a.13], and the
suppression totals (191 groups, 320 directives, 110 C901; `RUFF-SUP-214` has no
remaining source directive). Process tests and mutation controls were
inspected, not rerun. Review loop closed.
