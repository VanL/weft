# Verified CI Root-Cause Fixes

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-3]; docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]; docs/specifications/01-Core_Components.md [CC-2.2.1], [CC-3.1]
Superseded by: none

Class: 4 (hardening applied: two of the four fixes are on the durable-spine
execution path, the Manager's stopping drain and the TaskRunner's metric
sampling; the other two are test-only). No normative spec text changes. Plan
type: bug fix, four independent slices.

Theory: [WT-2a.13] "death needs proof": a fired kernel exit source and a
zero-memory reading of an unreaped process are both facts about a process that
has exited, and each fix makes the code treat them as such; [WT-4.3] and
[ALT-WT-002]: no polling deadline is added; [WT-4.4] "No over-armoring": no
new lifecycle, deadline or configuration.

## 1. Goal

The third CI run of the 0.9.125 release commit (GitHub Actions run
`43217697466`, commit `1462a983`) failed three tests, each in one or two of 41
jobs, and the run before it (`43208313297`) left a fourth unexplained.
Independent root-cause analyses produced a deterministic red test for each;
the author verified each analysis and each red test. This plan lands the four
fixes, test first. Slice D was added after the plan review, when its analysis
arrived.

| Slice | CI failure | Verdict |
|-------|-----------|---------|
| A | `test_task_runner_sampling_clock_follows_configured_limits[limit-command]`, ubuntu 3.13: `memory_mb == 0.0` | Product defect, Linux only |
| B | `test_stopping_drain_survivor_hold_publishes_no_due_clock_and_wakes_on_exit`, Windows 3.14 and 3.15: wrapper still in `_child_processes` | Product defect, latent since before 0.9.124 |
| C | `test_interactive_drain_waits_once_while_descendant_holds_pipes`, PostgreSQL lane: `assert 9 <= 8` | Test defect |
| D | `test_manager_stop_drains_user_work_then_manager_leaving_services`, ubuntu 3.12 (run `43208313297`): no terminal record for the user task | Test defect |

Causes:

- A. On Linux, psutil does not raise for an exited, unreaped process: it
  returns `rss=0`, `num_fds() == 0` and no connections. `ResourceMonitor.snapshot`
  counts that as a measured process, builds an all-zero `ResourceMetrics` and
  stores it as the last sample. `run_monitored_subprocess` polls the root and
  then samples; when the root exits between those two steps the zero sample
  replaces every sample taken while the work ran and becomes
  `RunnerOutcome.metrics`. The 0.9.124 guard (`measured_processes == 0`) covers
  only the platforms where psutil raises `ZombieProcess`.
- B. `Manager._hold_for_managed_survivor` waits on a one-shot kernel exit
  source for a survivor but decides whether that survivor is still live from
  the host process table (`pid_is_live`). On Windows the exit source can fire
  while psutil still lists the PID. An evaluation in that window takes the
  `survivor_hold_pid in live` shortcut and keeps holding, although the exit
  event has been consumed: the adapter suppresses a fired input and a held
  child publishes no deadline, so nothing wakes the drain for that child
  again. The drain then stalls until an unrelated clock (stop escalation at
  2 s, or the 15 s drain window, which logs a false "drain timed out").
- C. The test bounds every `poll_stdout` call, including the flush each
  reactor turn makes. The test's own driver runs a turn about every 20 ms
  while the fixture's parent process is alive, so the total depends on how
  quickly that process exits after its pidfile appears. The product's drain
  loop made exactly two calls with no wake-ups; nothing spins.
- D. The test enables the TaskMonitor in its default `delete` mode and then
  asserts on raw rows of `weft.log.tasks`. In that mode each monitor cycle
  ingests the log rows into its store and deletes them; the first cycle runs
  when the monitor process finishes booting, unordered against the user task.
  A cycle that lands between the user task's terminal broadcast and the
  test's read removes the row. The product published the record: the wrapper
  exits 0 and the task's terminal envelope stays on its `ctrl_out`. That this
  is what happened in the CI job is inferred by elimination (the run printed
  no diagnostics); the diagnostic added in `1462a983` separates it from a
  wrapper that died before its terminal write.

Outcome: a work item sampled while alive never reports a zero sample taken
after its exit; a stopping drain releases a held wrapper on the turn its
survivor's exit source wakes, on every platform; the interactive drain test
bounds the drain's own calls on a forced schedule; the Manager drain test
reads a task log that nothing deletes from.

## 2. Source Documents

- `docs/specifications/06-Resource_Management.md` [RM-5.1]: "Platform-specific
  file descriptor, handle, and process-disappearance behavior remains part of
  the current monitor implementation." [RM-5.2]: a violation reported after
  exit is not a confirmed limit.
- `docs/specifications/03-Manager_Architecture.md` [MA-3] (stopping drain,
  item on the survivor hold): the drain hands "one live survivor's
  process-exit source to the sentinel adapter as that TID's input, so the
  survivor's exit wakes the drain, which then holds for another live survivor
  or releases the wrapper to ordinary settlement."
- `docs/program-theory.md` [WT-2a.13], [WT-4.3], [WT-4.4], [ALT-WT-002].
- Prior plans: `2026-11-08-drain-survivor-exit-sentinel-plan.md` (the hold),
  `2026-11-09-reaped-wrapper-exit-proof-plan.md` (same Windows host rule on
  the reap path: local exit proof outranks a lagging host reading).
- Evidence: the three agent reports and red tests are summarized in the
  Review Log; CI logs are run `43217697466`, jobs `131181319132` (A),
  `131181318843` and `131181318934` (B), `131181317929` (C).

## 3. Context and Key Files

- A. `weft/core/resource_monitor.py::ResourceMonitor.snapshot` (the per-process
  loop and the `measured_processes == 0` guard), `check_limits` (fails open on
  the guard's `RuntimeError`); `weft/core/runners/subprocess_runner.py`
  `run_monitored_subprocess` (poll, then `monitor.check_limits()`, then
  `monitor.last_metrics()`); the same pattern in `weft/core/runners/host.py`
  for function targets.
- B. `weft/core/manager.py`: `_ManagerChildSentinelAdapter` (`watch_exit`,
  `_owned_sources`, `_membership`, `_suppressed`, `_retire_owned_source_locked`,
  `_observe`), `ManagedChild.survivor_hold_pid`, `_hold_for_managed_survivor`,
  `_cleanup_children` (the only caller), `_manager_child_timeouts` (no clock
  for a held child). `weft/core/process_exit.py::open_process_exit_source`.
- C. `tests/tasks/test_task_interactive.py`
  `test_interactive_drain_waits_once_while_descendant_holds_pipes`;
  `weft/core/tasks/consumer.py::_process_reactor_turn` (one flush per turn);
  `weft/core/tasks/interactive.py::_interactive_drain_remaining`;
  `tests/helpers/reactor_driver.py` (`wait_slice=0.02`).

Hidden couplings:

- The adapter's observer thread and the reactor share `_suppressed` and
  `_membership` under `_lock`; the fix reads them under the same lock and
  stores the watched PID in the same `_owned_sources` entry as its source, so
  the two cannot have different lifetimes.
- On Windows an exited process can stay in the system process list while a
  handle to it is open, and the adapter's own exit source is such a handle.
  Retiring a fired source when its exit is consumed closes that handle on
  the observer's next pass.
- `drain()` does not filter queued events against current membership, so the
  fix must not attribute a queued event to the current hold by position. It
  asks the adapter which PID's owned source has fired instead.
- The unwatched branch of the hold (no exit source could be opened) sets
  `survivor_hold_pid` without arming anything; it must never be treated as
  fired because an earlier survivor's source did.

Rejected alternatives:

- B: clearing the hold on the exit event and re-arming. On Windows
  `OpenProcess` on an exited process still succeeds while any handle is open
  (the adapter's own source is one), so the new source is already signalled
  and the reactor would spin for as long as the PID stays listed.
- B: a recheck deadline for a held child. [ALT-WT-002] and the hold's own
  contract exclude a polling deadline in place of the sentinel.
- A: re-polling the root after the sample in `run_monitored_subprocess` and
  discarding a sample taken after exit. It leaves the function-target path
  and any direct monitor caller reporting zeros; the monitor is the one place
  that reads the process.

## 4. Invariants and Constraints

Must not change:

- A. A sample is stored only when at least one process of the tree was
  measured. A tree with a live process and an exited, unreaped one still
  yields a sample of the live part. `check_limits` still fails open when
  nothing can be measured and still propagates unexpected collection defects.
- A. No new sampling, timing or configuration; the monitor protocol
  (`start`, `stop`, `snapshot`, `check_limits`, `last_metrics`) is unchanged.
- B. A wrapper is released only when no managed process it names is live.
  The only new evidence of exit is the fired kernel exit source for the PID
  that source was opened on; a PID that was never watched is still judged by
  the host alone.
- B. No deadline is published for a held child; no polling. The adapter
  still owns and closes every exit source.
- B. An exit recorded for one child never affects another child, and is
  dropped with the child.
- C. The test still fails when the drain polls on a cadence (the regression
  it was written for) and still asserts that the drain never sleeps.

## 5. Observable-Difference Register

| Change | Class | Theory basis |
|--------|-------|--------------|
| On Linux, a sample taken after the root exited but before it was reaped is no longer reported; the last sample taken while the work ran is reported instead, as on macOS | C (metrics describe the running work, [RM-5.1]) | [WT-2a.13] a zero reading of an unreaped process is exit evidence, not a measurement |
| A tree whose every process reads zero resident memory raises the existing "process tree disappeared" error inside `snapshot`; `check_limits` already fails open on it | N for callers of `check_limits`; C for a direct `snapshot` caller, which previously got zeros | [RM-5.1] |
| On Windows, a stopping drain releases a held wrapper on the turn its survivor's exit source wakes even while the host still lists the PID; before, it could stall up to the stop-escalation or drain window | C | [MA-3] the survivor's exit wakes the drain, which then releases the wrapper |
| No behavior change on POSIX for the hold: an exited survivor already read dead | N | |
| On Linux, in a tree with a live process and an exited, unreaped one, the exited process is left out of memory, file and connection totals but its final CPU time still enters that sample's CPU delta (psutil reads CPU times for a zombie on Linux and raises on macOS) | N (unchanged: the CPU it used since the last sample is real consumption by the tree) | [RM-5.1] |
| A fired survivor exit source is closed when its exit is consumed, not when the next source replaces it or the child is removed | C (a Windows process handle is released one turn earlier) | [MA-1.6a] the adapter owns and retires sources |
| Test C counts drain calls on a forced schedule; test D runs its TaskMonitor in `report_only` mode | N (tests only) | |

## 6. Spec Baseline and Delta

No normative change. [RM-5.1] leaves process-disappearance behavior to the
monitor implementation and the 0.9.124 changelog states the intent ("a process
exit during collection preserves the last valid sample"). [MA-3] already
states the behavior slice B restores. Code docstrings cite [RM-5.1], [MA-1.6a]
and [MA-3].

## 7. Tasks

Each slice is test first: add the red test, run it and record the failure,
then make the change, then record the pass. Stop and re-plan if a red test
passes before its fix, or if a fix needs a change outside the files named in
section 3.

Slice A (resource monitor):

1. Add to `tests/specs/resource_management/test_resource_metrics.py`:
   `test_exited_unreaped_root_reading_does_not_replace_last_live_metrics`
   (process double returning the Linux readings; sibling of the 0.9.124 test)
   and, Linux only, `test_real_exited_unreaped_root_does_not_replace_last_live_metrics`
   (real child, real zombie, real psutil).
2. Add to `tests/tasks/test_runner.py`:
   `test_command_runner_keeps_live_metrics_when_root_exits_between_poll_and_sample`.
   A monitor subclass takes one live sample, then on its second `check_limits`
   releases the child and waits until it has exited and is unreaped before
   sampling. POSIX only: Windows has no unreaped-exit state to wait for. On
   Linux nothing is doubled. On other POSIX platforms the monitor's process
   tree is the root read through a wrapper that substitutes the Linux
   readings where the platform raises `ZombieProcess`; the test asserts the
   forcing happened and whether a substitution was made.
3. Add one positive-control test: a tree of one live process and one
   zero-reading process still stores a sample equal to the live process's
   memory.
4. In `ResourceMonitor.snapshot`, skip a process whose resident memory reads
   non-positive, before it is counted as measured.

Slice B (survivor hold):

5. Add to `tests/core/test_manager.py`:
   `test_survivor_hold_releases_on_exit_wake_while_host_still_lists_the_pid`.
   Real wrapper, survivor, exit source and adapter; the two liveness oracles
   the hold consults report the exited survivor live for the one evaluation
   that its exit wake causes. Asserts the double was consulted, that the
   wrapper is released on that turn and that the drain completes.
6. Add an attribution test: after a watched survivor's source has fired, a
   second survivor that cannot be watched (the exit-source opener raises) is
   still held.
7. Adapter test first: `test_manager_child_sentinel_adapter_reports_a_fired_exit_source_once`
   (a real exit source on a real child: `None` before exit; the child's PID
   once after; `None` again; the source closed on the observer thread).
8. `_ManagerChildSentinelAdapter`: `_owned_sources` holds `(source, pid)`;
   `watch_exit` takes the watched PID; `take_exited_pid(tid)` returns that PID
   once the owned source has been observed ready and retires the source
   (drops its membership and suppression entry), else returns `None`.
9. `ManagedChild` gains the set of survivor PIDs whose exit source fired.
   `_hold_for_managed_survivor` adds the adapter's taken PID to that set and
   excludes the set from the live survivors.

Slice C (test):

10. Rewrite `test_interactive_drain_waits_once_while_descendant_holds_pipes`:
   the fixture parent waits for a release file; the driver runs four turns
   while it is alive (the count that breaks the old bound); the test then
   releases it, waits for the exit publication and drives to completion. It
   counts the `poll_stdout` calls made inside `_interactive_drain_remaining`
   and the activity publications (`_interactive_activity.set()` calls, not
   waits) after the drain's first clear, and asserts
   `1 <= drain calls <= 2 + publications`, `publications <= 1`, one flush per
   turn, and no sleeps. It keeps the existing checks that the descendant is
   still running and the task completed.

Slice D (test):

11. Red first: run the unmodified test with a barrier on
    `Manager._finish_graceful_shutdown` that wakes the real TaskMonitor and
    waits for its store checkpoint to pass the user task's terminal row; the
    original assertion fails. The barrier is evidence, not a committed test.
12. In `test_manager_stop_drains_user_work_then_manager_leaving_services`,
    add `WEFT_TASK_MONITOR_MODE: report_only` to the Manager's config, with a
    comment saying why. The monitor still runs as a supervision-only service.

Closing:

13. CHANGELOG entries under 0.9.125 (Fixed for A and B, Development for C
    and D).
14. Completion review by a different agent family; apply or answer findings.

## 8. Testing Plan

- Red first: tests 1, 2 and 5 fail at `1462a983` with the CI symptom (zero
  metrics; wrapper still held); test 7 fails because the method does not
  exist. Test 10's forced schedule fails the old assertion with
  `assert 9 <= 8` before the assertion is replaced. Task 11's barrier fails
  the unmodified test D.
- Mutation checks after the fixes: reverting the `snapshot` change fails
  tests 1 and 2; making the hold ignore the taken PID fails test 5; crediting
  a fired source to every managed PID fails test 6; adding a 10 ms polling
  wait to the drain loop fails test 10; with `report_only` removed, task
  11's barrier fails test D again.
- No mocks of the unit under test: the monitor, runner, Manager, adapter and
  exit source are real. The doubles are the platform readings that cannot be
  produced on the development platform (Linux zombie readings off Linux;
  the Windows process-table lag), each with an assertion that it was used.
- Existing contract tests that must stay green:
  `tests/specs/resource_management/`, `tests/tasks/test_runner.py`,
  `tests/core/test_manager.py` (in particular the two existing survivor-hold
  tests), `tests/tasks/test_task_interactive.py`.
- Linux: slice A's three tests and the original
  `test_task_runner_sampling_clock_follows_configured_limits` run in a
  `python:3.13-slim` container.

## 9. Verification and Gates

- The modules above pass; `ruff check`, `ruff format --check` and `mypy` are
  clean on touched files; `bin/check-doc-paths` and the plan-index tests pass.
- Release gates (`bin/release.py all`): full SQLite suite, `bin/pytest-pg
  --all`, extension suites, then CI on Linux, macOS and Windows. Windows is
  reachable only through CI; slice B's Windows behavior is confirmed there.

## 10. Rollback, Rollout and Success Signals

- Rollback: revert the fix commit. No queue shape, persisted data,
  configuration or public API changes; `watch_exit` is private to the
  Manager module. Rollback restores the three CI failure modes.
- Rollout: none beyond the 0.9.125 release; the changes are in-process.
- Success signals: the three CI tests stop failing; on Windows no "drain
  timed out" warning follows a survivor that exited; `RunnerOutcome.metrics`
  for a completed command on Linux is never all zeros when a live sample was
  taken.
- One-way doors: none.

## 11. Out of Scope

- The CI failures whose root cause is not yet established or not yet
  verified: a Manager `KeyError` on child launch failure, and a Windows job
  that hung between two tests of `tests/system/test_django_fixture_cleanup.py`
  (location established, trigger a hypothesis).
- Other tests in `tests/core/test_manager.py` that enable the TaskMonitor;
  whether they read the task log was not checked.
- The function-target sampling path in `weft/core/runners/host.py` gets the
  monitor-level fix; no separate red test is added for it.
- Custom monitor classes that return zeros for an exited process.
- Why Windows 3.14 and 3.15 hit the lag in one run and 3.12 and 3.13 did not.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| none | | | | |

## Review Log

Author family: Claude.

### Root-cause evidence (2026-11-12)

Three independent agents, one per failure, each with no hypothesis supplied,
produced a root cause and a red test. The author then verified each:

- A: red test failed 3 of 3 on macOS and, with its no-doubles Linux case, 3 of
  3 in a `python:3.13-slim` container; a separate probe confirmed psutil
  returns `rss=0` for a zombie on Linux without raising.
- B: red test failed 3 of 3 with "lost wakeup"; the hold, the adapter and the
  failing CI test are byte-identical to `v0.9.124`; psutil's Windows `wait()`
  documents that a PID "may still stick around" after the handle signals. Not
  observed directly: psutil reporting the survivor live in the two CI jobs.
- C: red test failed 3 of 3 with `assert 9 <= 8` after asserting one flush per
  turn, two drain calls and no wake-ups; a drain-only bound passed on the same
  schedule.
- D (a fourth agent, reported after this plan was drafted): red test failed
  3 of 3 with the CI assertion under the forced monitor ingest; a control
  with the monitor in `report_only` passed 3 of 3. The author's earlier
  check, which watched the log for 8 s after the Manager stopped and saw the
  row stay, only showed that no second cycle ran (the next is 300 s later).

### Plan review (2026-11-12)

- Reviewer family: Google (Antigravity `agy` 1.5.6, gemini-4-argon-medium),
  read-only plan mode, before any product change. Verdict: CHANGES REQUESTED.
- F1 (P2): the runner-level test waits for a zombie state Windows does not
  have. Accepted: the test is POSIX only; task 2 says so.
- F2 (P2): a fired source stayed owned after its exit was consumed, and a
  second dictionary for the PID could outlive `_owned_sources` on `close()`.
  Accepted: `(source, pid)` is one entry and `take_exited_pid` retires the
  source; tasks 7 to 9 and the register say so.
- F3 (P3): a skipped zombie's CPU time still enters the CPU delta on Linux.
  Answered: kept, and recorded in the register. That time was consumed by
  the tree during the interval; dropping it would undercount.
- F4 (P3): task 9 did not say the descendant-running and completed checks
  stay, or that wake-ups are publications rather than waits. Accepted: task
  10 says both.
- The reviewer found no path that releases a wrapper while a managed process
  it names is alive, and none that reports a fired PID for the wrong source.

### Completion review (2026-11-12)

- Reviewer family: Google (Antigravity `agy` 1.5.6, gemini-4-argon-medium),
  read-only, over the full uncommitted diff against `1462a983` and the final
  files. No findings.
- After the review the author made one test-only change: the
  unwatched-survivor test waits, bounded, for the host to report the second
  survivor's exit before its last evaluation, because nothing watches that
  survivor and the host reading can trail the exit on Windows.

Final Completion Review Verdict: APPROVE

## Execution Evidence

Run in an isolated worktree of `1462a983` (other agents were running tests in
the main tree), macOS, Python 3.14, unless stated.

- Red before the fixes: tests 1 and 2 failed with `memory_mb=0.0` /
  `assert 0.0 > 0`; test 5 failed with the wrapper still in
  `_child_processes` (the CI assertion shape); test 7 failed on the missing
  `watch_exit` PID argument; test 10's forced schedule failed the old bound
  with `assert 9 <= 8`, 3 of 3; task 11's barrier failed the unmodified test
  D, 3 of 3. Test 3 (mixed tree) and test 6 (attribution guard) passed
  before and after, as intended.
- Green after: every new and changed test, plus the barrier test against
  the unmodified body of test D.
- Mutations: ignoring the taken PID re-failed test 5 and left test 6 green;
  crediting a fired source to every managed PID failed test 6 and left test
  5 green; a 10 ms polling wait in the drain loop failed test 10 with
  `assert 20 <= (2 + 0)`.
- Modules: `tests/core/test_manager.py`, `test_manager_family_maintenance.py`,
  `test_admission_recovery.py`, `tests/specs/resource_management`,
  `tests/tasks/test_runner.py`, `tests/tasks/test_task_interactive.py`,
  `tests/cli/test_cli_serve.py`: 706 passed, 5 skipped. `tests/specs` and
  `tests/architecture` pass. `ruff check`, `ruff format --check`, `mypy weft`
  and `bin/check-doc-paths` are clean.
- Linux (`python:3.13-slim` container, the worktree): the
  `tests/specs/resource_management` module, the runner-level test and the
  original `test_task_runner_sampling_clock_follows_configured_limits`:
  28 passed, three runs, with the real-zombie test and no doubles.
- Not verified here: Windows. Slice B's behavior on Windows and the new
  tests' Windows runs are confirmed only by CI.
