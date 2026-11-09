# Manager Event-Driven Child Reaping

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-1.8], [MA-3]; docs/specifications/07-System_Invariants.md [IMPL.8], [IMPL.10]
Superseded by: none

Class: 5 (spec clarification, promotion strategy B, atomic) with hardening
applied, because a risky trigger fires: the change is on the Manager's
durable-spine execution path (child exit detection, reap, custody
acknowledgement). Escalation history: none. Plan type: implementation with
spec revision (clarification of [MA-1.6a] and [MA-1.8]; the governing text
already says child exit "reaches the Manager reactor as a local event from one
Manager-owned sentinel adapter", and this plan makes the code match it).

Theory: [WT-4.3] "One reactor, one arbiter, a closed set of inputs" (OS
sentinel adapters, never defensive polling), [WT-4.3] "Availability and death
are different questions", [WT-4.2] "Process concurrency, short transactions"
(on PostgreSQL the scarce resource is connection count), [WT-4.4] "No
over-armoring" and "Measure; do not bake in inefficiency", [ALT-WT-002] (fixed
polling caps rejected), [WT-2a.6], [WT-2a.13].

## 1. Goal

Manager child reaping is the second-largest Manager phase in the nested
admission stress run (`child_cleanup` = 15.5% of 296 s wall, about 11 ms per
reactor turn). Two causes, both in `weft/core/manager.py`:

1. Every reactor turn, and every service-convergence pass, calls
   `_child_has_exited` for **every** child. For a live child that is two
   `waitpid` calls (`Process.exitcode`, `Process.is_alive()`) plus a psutil
   host-PID probe every `MANAGER_PID_LIVENESS_RECHECK_INTERVAL` (0.5 s), even
   though the Manager already registers each child's OS exit sentinel with
   `_ManagerChildSentinelAdapter` and receives a reactor wake when it fires.
2. Each reap calls `_record_reaped_admission`, which reads the reaped task's
   state snapshot through `read_task_state_snapshot(self._manager_context(),
   tid)` with no broker, so `_state_broker` opens a fresh broker: a new
   PostgreSQL connection, schema validation queries, and SimpleBroker plugin
   discovery. The Manager opened about 1,252 connections per 1,250-task run.

This plan makes child-exit evidence event-driven (only a child whose sentinel
has fired is examined) and makes the reap-time snapshot read reuse the
Manager's own broker connection, as `_reconcile_launch_now` and
`_refresh_scheduler_evidence` already do.

## 2. Source Documents

- `docs/specifications/03-Manager_Architecture.md` [MA-1.6a] (child exit is a
  sentinel event; post-exit terminal-proof grace and publication retry are
  published deadlines; "A child whose sentinel has fired publishes the
  PID-liveness recheck only until terminal-proof grace begins"), [MA-1.8]
  ("A failed read of a task's state snapshot during reconciliation or reap is
  logged and changes nothing"), [MA-3] (drain and forced termination).
- `docs/specifications/07-System_Invariants.md` [IMPL.8] (reactor thread owns
  broker effects), [IMPL.10] ("sentinel-driven child-exit handling" consumes
  the cleanup deadline).
- `docs/program-theory.md` [WT-4.3], [WT-4.4], [ALT-WT-002], [ALT-WT-015]
  (Manager-local child tracking is a hint, never the liveness source for
  other components; this plan only changes how the Manager learns its own
  child's exit).
- `docs/agent-context/runbooks/writing-plans.md`,
  `docs/agent-context/runbooks/hardening-plans.md`.
- Historical: `0762fc42` (2026-04-08, "Fix preserve cleanup and manager drain
  races") introduced the `_child_has_exited` lag guard; `4370bb0f`
  (2026-09-19, retired plan `2026-09-17-watcher-reactor-restoration-plan`)
  introduced `_ManagerChildSentinelAdapter` and the sentinel recheck deadline;
  `5c690ef0` (2026-10-28) reused turn cleanup during spawn delivery.

## 3. Context and Key Files

Files to modify:

- `weft/core/manager.py`
  - `Manager._child_has_exited` (gate on sentinel evidence)
  - `Manager._record_reaped_admission` (reuse connected broker)
  - `Manager._child_summaries_for_log` (same rule for the diagnostic field)
- `docs/specifications/03-Manager_Architecture.md` ([MA-1.6a], [MA-1.8] text,
  [MA-1.6a] implementation mapping, Related Plans backlink)
- `tests/core/test_manager.py`, `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_manager_burst_bookkeeping.py`,
  `tests/core/test_admission_recovery.py`: tests that inject an exited child
  without exit evidence (see Task 3).
- `tests/helpers/manager_dispatch.py`: new `observe_child_exit` helper that
  delivers a real child's sentinel event the way a turn does.
- `docs/plans/README.md` (index row, file count).

Current structure (what exists today):

- `Manager._commit_child_launch_success` is the only production site that
  inserts into `self._child_processes`; it calls `_register_child_sentinel`,
  which adds `(tid, process.sentinel)` to the adapter. For a `spawn` Process
  the sentinel is the parent's read end of a pipe whose write end only the
  child holds (POSIX) or the process handle (Windows); it becomes ready when
  the child exits. `multiprocessing` itself implements `Process.join()` by
  waiting on this sentinel.
- `_ManagerChildSentinelAdapter` runs one observer thread; when a sentinel
  fires it queues `_ManagerChildSentinelEvent(tid, sentinel)`, suppresses that
  identity, and calls the reactor's `notify_activity`.
- `Manager._process_reactor_turn` calls `_drain_child_sentinel_events` before
  every turn; that sets `child.sentinel_observed_ns` and resets
  `child.last_liveness_probe_ns`. `_terminate_children` and
  `_wait_for_children_to_exit` drain the same way.
- `Manager._manager_child_timeouts` already publishes deadlines only for
  children with exit evidence: the PID-liveness recheck
  (`max(sentinel_observed_ns, last_liveness_probe_ns) + 0.5 s`) until
  proof grace starts, the proof-grace end, and a publication retry.
- `Manager._cleanup_children` (turn step `child_cleanup`, service convergence
  passes, drain, termination, `_handle_work_message` outside the delivery
  scope) loops over every child and calls `_child_has_exited`. That method
  today runs `exitcode` and `is_alive()` for every child and a psutil
  `pid_is_live` probe every 0.5 s per live child.
- `_tracked_service_candidate` and `_active_autostart_sources` also call
  `_child_has_exited` (service children only, on convergence cadence).
- `_record_reaped_admission` runs in `_cleanup_children`'s reap `finally`
  block after the child leaves the table.

Shared paths to reuse (do not duplicate):

- `self._get_connected_queue().get_connection()` yields a lease on the
  Manager's persistent broker connection (pattern:
  `Manager._reconcile_launch_now`, `Manager._terminal_envelope_visible`).
- `read_task_state_snapshot(ctx, tid, broker=...)` in
  `weft/core/task_state.py` accepts a caller broker.

Comprehension questions (answer in the execution record before editing):

1. Why can `Process.exitcode` stay `None` after a child's sentinel fired, and
   what in the current code reaps such a child?
   Expected: another waiter consumed the exit status. The Manager's own
   `terminate_process_tree` (drain SIGTERM rung, forced termination) waits
   with `psutil.wait_procs`, which calls `waitpid` on a direct child;
   `multiprocessing`'s `Popen.poll` then gets `ECHILD`, swallows it and keeps
   returning `None`. The sentinel still fired because the pipe closed at
   exit. `_child_has_exited` falls through to `pid_is_live(pid)` (psutil),
   finds the PID gone, and reports exit after the startup grace.
2. Which code turns a fired sentinel into a reap candidate, and what wakes the
   reactor for it?
   Expected: the observer thread queues the event and calls
   `notify_activity`; `_drain_child_sentinel_events` (start of
   `_process_reactor_turn`, and in the termination paths) sets
   `sentinel_observed_ns`.

## 4. Provenance triage of the defensive path

The `_child_has_exited` docstring warns that "the multiprocessing Process view
can lag behind the OS process table ... keeps exited children ... blocks
manager shutdown". `git log -S` traces it to `0762fc42` (2026-04-08), written
when cleanup polled `is_alive()` only and the same change removed
`terminate_process_tree` calls from the reap loop. The failure is real and
still reachable: `terminate_process_tree` (psutil `wait_procs`) is still called
on direct children in `_signal_children_to_stop` and `_terminate_children`, so
it can consume a child's exit status. The guard protects "an exited child does
not stay tracked forever and block shutdown".

That guarantee depends on noticing the exit, not on polling live children. The
sentinel fires on exit regardless of who later calls `waitpid`, so every
exited child, including the lagging case, becomes a candidate through its
sentinel, and the existing lag logic (host-PID probe, startup grace,
published 0.5 s recheck) runs for it unchanged. Decision: keep the whole lag
guard and the 0.5 s recheck for sentinel-fired children; drop only the
probing of children whose sentinel has not fired. No periodic sweep is added
([ALT-WT-002]): it would guard a state the stdlib's own `join()` treats as
impossible, the sentinel staying unready after exit. That would need a
grandchild to inherit the write end; Weft task wrappers start work only
through `spawn` and `subprocess.Popen` with the default `close_fds=True`, so
none does.

`MANAGER_CHILD_STARTUP_LIVENESS_GRACE_SECONDS` (`27d95959`) and
`MANAGER_PID_LIVENESS_RECHECK_INTERVAL` keep their current roles; both are
now consulted only after the sentinel fired.

## 5. Invariants and Constraints

Must not change:

- Terminal-evidence-before-custody rule [MA-1.6a]: a reaped child's
  reservation is acknowledged only after task-owned terminal proof or a
  committed Manager `wrapper_lost` envelope; proof grace, publication retry
  and their published deadlines are unchanged.
- [WT-2a.13] / [MA-1.8]: a failed reap-time snapshot read is logged and
  changes nothing (same exception tuple; `DatabaseError` is a `BrokerError`).
- Drain and forced termination [MA-3]: `_terminate_children`,
  `_wait_for_children_to_exit`, and the survivor reporting stay as they are.
- [IMPL.8]: all broker effects stay on the reactor thread; the adapter stays
  broker-free.
- No new queue, field, constant, config knob, dependency, or execution path.
  `ManagedChild` keeps its fields.
- Tracks B and D edit other functions in `weft/core/manager.py`; keep edits
  inside the three named methods.

Gates: no mock of queues, reservations or process lifecycle in the new tests;
OS probes may be observed through delegating wrappers. No test-only seam in
production code.

## 6. Observable-Difference Register

| Change | Class | Theory basis |
|--------|-------|--------------|
| A live child (sentinel not fired) gets no `waitpid` and no psutil probe from reactor turns, cleanup passes, or service convergence; previously two `waitpid`s per pass and one psutil probe per 0.5 s | C (no loss: the sentinel is exact exit evidence) | [WT-4.3] OS sentinel adapters as the input, never defensive polling; [ALT-WT-002]; [WT-4.4] measure |
| An exited child is reaped on the turn that drains its sentinel event (the adapter already wakes that turn); polling could notice it only on a turn that happened to start between exit and event publication | C | [WT-4.3] closed set of inputs; the wake already existed |
| Process-view lag (exit status consumed by `psutil.wait_procs`) is still reaped through the host-PID probe and 0.5 s recheck, now entered from the sentinel | N | [WT-4.3] availability and death are different questions |
| Service-convergence liveness (`_tracked_service_candidate`, `_active_autostart_sources`) treats a service child without a fired sentinel as live without probing | C | same as row 1 |
| Reap-time snapshot read uses the Manager's own broker connection instead of a fresh broker per reap (about 1,250 fewer PostgreSQL connections, schema validations and plugin discoveries per 1,250-task run) | C (same read, same failure handling) | [WT-4.2] process concurrency, short transactions; one owner per resource |
| Serve-log `children[].alive` is `True` without a `waitpid` for a child whose sentinel has not fired (it can read `True` for the sub-turn window between exit and event drain) | C (diagnostic log field; log formats are safe to change, `CLAUDE.md` §3) | [WT-4.3] |
| After a fatal sentinel-observer failure (the adapter thread is dead), a child that exits later is never examined by `_cleanup_children`; final cleanup still terminates and drops it through the `_terminate_children` loop, but no longer settles it through the terminal-proof path first, and `_wait_for_children_to_exit` waits out its stop-grace window even when every user child has already exited | D (narrow: only after an internal fatal error) | Observer failure is fatal ([MA-1.6a] "Adapter failure remains fatal"), so the Manager is crashing; [WT-2a.13] a crashed Manager never settles running tasks and its successor settles adopted launches under [MA-1.6a]; [WT-4.4] no second detection path for a failed detector. Owner decision requested (see §13) |

## 7. Spec Baseline

- `08c2f87b` — docs/specifications/03-Manager_Architecture.md,
  docs/specifications/07-System_Invariants.md at plan authoring time.

## 8. Proposed Spec Delta

Promotion strategy:

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| docs/specifications/03-Manager_Architecture.md | B — atomic (text, mapping and code in one change) | [MA-1.6a] sentence beginning "Child-process exit reaches"; [MA-1.8] sentence beginning "A failed read of a task's state snapshot"; [MA-1.6a] implementation mapping; Related Plans |

### [MA-1.6a] — replace the sentence "Child-process exit reaches the Manager reactor as a local event from one Manager-owned sentinel adapter; post-exit terminal-proof grace remains a published timer."

> Child-process exit reaches the Manager reactor as a local event from one
> Manager-owned sentinel adapter; post-exit terminal-proof grace remains a
> published timer. The sentinel is the Manager's only exit evidence: a child
> whose sentinel has not fired is live, and no reactor turn, cleanup pass or
> service convergence pass probes its process status. After the sentinel
> fires, the Manager confirms exit from the local process view and, when that
> view lags (another waiter consumed the exit status), from host PID
> liveness on the published PID-liveness recheck.

### [MA-1.8] — insert after "... and no Manager mode records the failure."

> Both reads use the Manager's own broker connection; reaping a child opens
> no broker.

### [MA-1.6a] implementation mapping — insert `Manager._child_has_exited` after `Manager._cleanup_children`.

## 9. Tasks

1. Spec and plan bookkeeping (strategy B, lands with the code).
   - Files: `docs/specifications/03-Manager_Architecture.md`,
     `docs/plans/README.md`.
   - Apply §8 verbatim; add the Related Plans backlink to this plan.
   - Done when `./.venv/bin/python -m pytest tests/specs -q` passes.
2. Gate `_child_has_exited` on sentinel evidence; reuse the connected broker
   in `_record_reaped_admission`; apply the same rule to
   `_child_summaries_for_log`.
   - `_child_has_exited`: first statement returns `False` when
     `child.sentinel_observed_ns <= 0`; the rest of the method is unchanged.
     Rewrite the docstring to state the event rule, keep the lag rationale,
     and add `Spec: ... [MA-1.6a]`.
   - `_record_reaped_admission`: wrap the read in
     `with self._get_connected_queue().get_connection() as broker:` and pass
     `broker=broker`; keep the exception tuple and the
     `_retire_scheduler_residents` call outside the `with` block.
   - `_child_summaries_for_log`: `"alive": child.sentinel_observed_ns <= 0
     or child.process.is_alive()`.
   - Do not add a helper, field, flag, constant, or periodic sweep. Do not
     touch `_cleanup_children`, `_manager_child_timeouts`, or the adapter.
   - Stop if a production path is found that inserts into
     `_child_processes` without `_register_child_sentinel`, or a Weft path
     that hands the sentinel write end to a grandchild.
3. Tests (write the behavior tests first; confirm the "not probed" test fails
   on the baseline for the intended reason).
   - New, `tests/core/test_manager.py`:
     `test_manager_turns_do_not_probe_a_live_child` and
     `test_manager_reaps_exited_child_on_its_sentinel_turn`, both on a real
     Manager (`manager_setup`) that launches a real child through its spawn
     inbox running `tests.tasks.sample_targets:signal_ready_and_wait_for_release`.
     The first wraps `os.waitpid` and `manager_mod.pid_is_live` with
     counting delegates filtered to the child's PID (and, for `waitpid`, to
     the reactor thread), drives turns while the child waits, and asserts
     zero calls. The second releases the child, waits for the adapter's
     event, runs one turn with a counting delegate around
     `WeftContext.broker`, and asserts the child left the table and the
     scheduler residents, no broker was opened, and its task-owned terminal
     envelope is on `ctrl_out`.
   - Existing tests that inject a fake exited child and call
     `_cleanup_children()` / `_child_has_exited()` directly model an exit the
     Manager has not observed. Give them the evidence the reactor would have:
     `sentinel_observed_ns=<now>` for fake processes, or
     `tests/helpers/manager_dispatch.py::observe_child_exit` (register,
     wait on the adapter, drain) for real processes. Do not weaken any
     assertion. Update the `wait_for_children` helper the same way. The two
     `test_child_has_exited_*` unit tests get `sentinel_observed_ns` so they
     keep exercising the lag and startup-grace branches instead of passing
     through the new gate.
4. Verification (§11) and commit.

## 10. Testing Plan

- Harness: `manager_setup` fixture (real broker, real Manager, real spawn
  child). No mocks of queues, reservations, or process lifecycle. The only
  wrappers are counting delegates around `os.waitpid` and
  `manager_mod.pid_is_live` that call the real function.
- RED: `test_manager_turns_do_not_probe_a_live_child` fails on the baseline
  because each turn's `child_cleanup` calls `waitpid` on the live child.
- Regression owners: `tests/core/test_manager.py`,
  `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_manager_burst_bookkeeping.py`,
  `tests/core/test_admission_recovery.py`,
  `tests/core/test_family_dispatch_scaling.py` (reap, proof grace, retry,
  sentinel gate, drain, shutdown, reaped admission).
- Out of scope for local runs: the full suite and
  `tests/system/test_admission_stress.py` (the coordinator benchmarks).

## 11. Verification and Gates

```bash
./.venv/bin/python -m pytest -n 4 tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/core/test_manager_burst_bookkeeping.py tests/core/test_admission_recovery.py tests/core/test_family_dispatch_scaling.py tests/specs -q
./bin/pytest-pg tests/core/test_manager.py tests/core/test_manager_family_maintenance.py tests/core/test_manager_burst_bookkeeping.py tests/core/test_admission_recovery.py tests/core/test_family_dispatch_scaling.py
./.venv/bin/ruff check .
./.venv/bin/ruff format --check weft/core/manager.py tests/core/test_manager.py
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Observable success after deploy: in the stress run, `child_cleanup` falls from
about 11 ms per turn to roughly the cost of the reaps themselves, and Manager
PostgreSQL connection opens per run fall from about 1,250 toward the constant
startup count.

## 12. Rollback and Rollout

Rollback is a revert of the code, test and spec commit; no queue, payload,
persisted format, or configuration changes, so old and new Managers coexist.
No rollout ordering.

## 13. Out of Scope and Open Questions

Out of scope: the double `ctrl_out` scan per clean reap
(`_child_terminal_proof_still_within_grace` then
`_commit_wrapper_lost_envelope`), the forced service convergence after every
reaping turn, and the stopping-drain zero-timeout when an exited wrapper keeps
live managed survivors (the 0.5 s recheck deadline stays in the past). These
are recorded for the coordinator, not changed here.

Open question for the owner: the `D` row in §6. A shutdown-only backstop
(examine every in-scope child once in `_terminate_children` when the observer
thread is dead) would restore terminal-proof settlement after a fatal
observer failure at the cost of a second detection path. Recommended: no
backstop.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

No deviation from the promoted [MA-1.6a] and [MA-1.8] text. Plan-only
adjustments made during execution (no spec effect): the shared
`observe_child_exit` test helper, the broker-open assertion added to the
sentinel-turn test, and the `docs/plans/README.md` file count corrected from a
stale 49 to the mechanical count 51 (it was already one short on the base).

## Fresh-Eyes Review

Author pass after drafting and again after implementation:

- Expert-developer lens: the change is one guard at the top of the existing
  exit predicate plus one `with` block, both reusing existing owners (the
  sentinel adapter, the connected queue). No helper, field, flag, or
  constant was added; the lag guard and its recheck deadline are untouched.
- CS lens: this is reactor demultiplexing with the OS readiness handle as the
  event source (the same mechanism `multiprocessing` uses for `join()`).
  Polling a set that the demultiplexer already covers is the defect
  [ALT-WT-002] rejects.
- Zero-context ambiguity checked: every caller of `_child_has_exited`
  (`_cleanup_children`, `_tracked_service_candidate`,
  `_active_autostart_sources`) was enumerated with `grep`; the only
  production insertion into `_child_processes` (`_commit_child_launch_success`)
  registers the sentinel.
- Residual risk: a child whose sentinel write end is inherited by a
  grandchild would be reaped only when that grandchild exits. No Weft path
  does this (spawn and `Popen` close inherited descriptors by default);
  user code cannot reach the wrapper's descriptor because task work runs in a
  separate spawned or `Popen` process.

## Review and execution record

### Implementer execution log

Comprehension answers (recorded before the first edit):

1. `exitcode` stays `None` after the sentinel fires when another waiter
   consumed the exit status: `terminate_process_tree` waits with
   `psutil.wait_procs`, which `waitpid`s a direct child, and
   `multiprocessing`'s `Popen.poll` swallows the resulting `ECHILD`. The
   sentinel (pipe EOF on POSIX, process handle on Windows) fired anyway, and
   `_child_has_exited` falls through to `pid_is_live` and reaps after the
   startup grace.
2. The observer thread queues `_ManagerChildSentinelEvent` and calls
   `notify_activity`; `_drain_child_sentinel_events` at the start of
   `_process_reactor_turn` (and in `_terminate_children` /
   `_wait_for_children_to_exit`) sets `sentinel_observed_ns`.

Evidence (base `08c2f87b`):

- RED, baseline code:
  `test_manager_turns_do_not_probe_a_live_child` failed with 27-29 reactor
  `waitpid` calls on the live child over 1.5 s of turns; the broker-open
  assertion in `test_manager_reaps_exited_child_on_its_sentinel_turn` failed
  with one `WeftContext.broker` open during the reaping turn when
  `_record_reaped_admission` was restored to the old read.
- SQLite, `-n 4`: `tests/core/test_manager.py`,
  `tests/core/test_manager_family_maintenance.py`,
  `tests/core/test_manager_burst_bookkeeping.py`,
  `tests/core/test_admission_recovery.py`,
  `tests/core/test_family_dispatch_scaling.py`, `tests/specs`: 573 passed,
  3 skipped (PostgreSQL-only). Adjacent:
  `tests/core/test_work_conserving_admission.py`,
  `tests/core/test_connection_admission.py`,
  `tests/core/test_manager_runtime_connections.py`,
  `tests/core/test_manager_services.py`,
  `tests/core/test_service_convergence.py`,
  `tests/core/test_family_source_reconciliation.py`,
  `tests/core/test_manager_start_observer_failure.py`,
  `tests/core/test_admission.py`: 311 passed, 1 skipped;
  `tests/commands/test_task_commands.py`, `tests/commands/test_run.py`,
  `tests/tasks/test_task_execution.py`: 367 passed.
- PostgreSQL, `./bin/pytest-pg` on the five owning files: 402 passed,
  12 skipped (SQLite-only usage observations); the two new tests and
  `test_pg_admission_real_child_reap_refreshes_progress_once` rerun after the
  last test edit: 3 passed.
- `./.venv/bin/ruff check .`: all checks passed; `ruff format --check` on the
  six touched Python files: already formatted; repo mypy command: no issues
  in 499 source files; `bin/check-doc-paths`: OK.

### Cross-family review

Tier 1, Codex (`codex exec -s read-only`, reasoning effort high), 2026-11-08,
scoped-change review of the plan and implementation together at f5c78324
against baseline 08c2f87b (perf/admission-base). Verdict: `no blocker`.

| ID | Severity | Finding | Disposition |
|---|---|---|---|
| F1 | P3 | The [MA-1.6a] sentence forbade all process-status probes, but explicit termination still calls `is_alive()` (stop/kill escalation, drain-timeout turns), which this change intentionally preserves. | Accepted, fixed in spec: the sentence now scopes the rule to ordinary exit detection and reaping and states that explicit termination keeps its own checks. |

The reviewer found no functional regression, judged the D row a
theory-consistent fail-stop tradeoff that warrants no fallback detector, and
confirmed both new tests are falsifiable. Benchmark results are recorded by the
coordinator's program summary.
