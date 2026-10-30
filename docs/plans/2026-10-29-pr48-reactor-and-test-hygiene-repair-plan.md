# PR #48 Subprocess Reactor and Test Hygiene Repairs

Status: draft
Source specs: docs/specifications/06-Resource_Management.md [RM-5.1], [RM-5.2]; docs/specifications/01-Core_Components.md [CC-2.5], [CC-3.2], [CC-3.4]; docs/specifications/08-Testing_Strategy.md [TS-0], [TS-1], [TS-3.1]
Superseded by: none

Class: 5 (edits normative spec text in `[RM-5.2]` and `[TS-0]` and revises `docs/program-theory.md`, per `[DOM-15]` row 5; the execution-path risky trigger also fires because the shared command subprocess runner spine changes, so the hardening-plans checklist applies and review precedes implementation)
Plan type: implementation with spec revision

## 1. Goal

Repair the remaining architectural, test-performance, and repository-hygiene defects introduced on the PR #48 branch (`v0.9.118..540707a3`) while preserving the branch's validated runtime fixes (`weft/core/family_scheduling.py` leftist-heap compaction, `weft/core/manager.py` Arm B `_dispatch_cleanup_scope` duplicate-sweep elimination, child-sentinel pre-reservation yield, and serviced retry-deadline retirement, `weft/core/tasks/consumer.py` logical stream sealing, and `bin/pytest-pg` TCP + schema preflight). Specifically:

1. Replace the 100 Hz per-pipe polling loop (`_PipeAdapter` + `SUBPROCESS_POLL_INTERVAL_FLOOR = 0.01`) and timer-polled supervisor sleep in `weft/core/runners/subprocess_runner.py` with an opaque, platform-dispatched readiness adapter (`_open_process_io`) that performs **0 Hz** pipe polling on both POSIX and Windows.
2. Delete the 96-second negative PostgreSQL preflight test (`test_schema_preflight_requires_real_backend_operation`), switch `bin/pytest-pg::_wait_for_postgres` to `time.monotonic()` with a named 200 ms poll constant (`POSTGRES_READY_POLL_INTERVAL_SECONDS = 0.2`) and documented Docker/PostgreSQL (`postgres:18`) readiness battle scars, and remove the brittle `SimpleNamespace` clock replacement in `tests/system/test_pytest_pg_script.py`.
3. Retire the 1,722-line always-skipped dispatch experiment harness (`tests/system/test_manager_dispatch_costs.py` and its `RUFF-SUP-379`/`RUFF-SUP-380` suppression entries), keep `tests/helpers/run_diagnostics.py` (including `_RetainedOutput`, which is the only path that captures `pytest-timeout` thread dumps before `os._exit(1)` under `pytest-xdist`), and replace the verbose manual `record_phase` `try/finally` blocks in `tests/cli/test_cli_run_installed_entrypoint.py` with a `phase(config, name, **details)` context manager that keeps the config, command metadata, and error detail, while preserving `test_installed_console_init_failure_does_not_materialize_harness_context`.
4. Condense the duplicate raw subagent transcript dumps and reconcile dirty worktree edits in `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md` and `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`, correct the inaccurate "surface immediately" preflight claim, keep both output-size evidence records with their roles (the `128 KiB` controlled-sink payload in `test_terminal_capture_delivers_backlog_after_capture_budget` is the mutation proof; the separate integrated Consumer-and-broker reproduction at `134,217,728` characters, with retained `runtime-integrated-stream-flood-*-134217728.json` artifacts recorded in the 2026-10-27 plan, is the integration evidence), and close completed plan statuses in `docs/plans/README.md` per `[DOM-15]`.

---

## 2. Theory Alignment: Why Each Change Is Better

This section explains to the implementing agent *why* the existing shapes on the branch fell short of Weft's operating concepts and *why* each replacement is more consistent with `docs/program-theory.md`, `AGENTS.md`, and systems engineering principles.

### 2.1 Subprocess I/O: Readiness-Driven Reactor vs. 100 Hz Polling Loops (`[WT-2]`, `[WT-4]` §4.3, `[ALT-WT-002]`, `[WT-7]`)

- **What the branch did (`4c490043`, `540707a3`):**
  To fix a real CPython deadlock where calling `stream.close()` from the supervisor thread blocked on CPython's internal buffered `TextIOWrapper` lock while a reader thread was blocked in `stream.read()` (triggered when a background grandchild inherited `stdout`/`stderr` so the pipe never saw OS EOF on direct-child exit), `weft/core/runners/subprocess_runner.py` introduced `_PipeAdapter`. `_PipeAdapter` puts each pipe descriptor into non-blocking mode (`os.set_blocking(fd, False)`) and runs a dedicated thread per pipe (`stdout`, `stderr`, `stdin`) that catches `BlockingIOError` and sleeps for `SUBPROCESS_POLL_INTERVAL_FLOOR = 0.01` (`10 ms` = **100 Hz** per pipe). Meanwhile, `run_monitored_subprocess` drains `stdout_queue` and `stderr_queue` and calls `time.sleep(sleep_for)` (`10 ms–50 ms`). Finally, because the supervisor thread cannot tell whether a sleeping reader thread has finished reading the direct child's last bytes after `process.poll() is not None`, `_finish_streams` calls `adapter.thread.join(timeout=SUBPROCESS_STREAM_DRAIN_TIMEOUT)` (`0.25s`), stalling every inherited-pipe completion by 250 ms while the reader threads spin 25 more times waiting for an EOF that never comes.
- **Why that violates Weft's program theory and house rules:**
  1. **`docs/program-theory.md` (`[WT-2]` item 4, `[WT-4]` §4.3, `[ALT-WT-002]`):** Weft's reactor law (`[WT-2]` item 4) states that a quiet unit of supervision runs zero turns unless an input arrives or an explicit deadline is due, and `[ALT-WT-002]` explicitly rejected fixed polling wait-caps (*"A local source exists that cannot call the arbiter and has no adapter. Then add the adapter, not a cap"*). Turning `run_monitored_subprocess` into stacked timer-polled loops (`100 Hz` in `_PipeAdapter` + `20–100 Hz` in `run_monitored_subprocess`) costs ~35 ms of CPU and ~890 context switches per 3-second idle child on macOS, delays output delivery by up to `50 ms`, and buffers child output into an **unbounded** `queue.Queue` in parent RAM when callbacks are slow.
  2. **`AGENTS.md` (§1.1 Sleep rule) & `[WT-7]` (Timer test):** `AGENTS.md` §1.1 requires every sleep to use a named constant with a docstring explaining why no event-driven alternative exists, and `[WT-7]` requires every timer to answer: *"which event cannot arrive, and why?"* `SUBPROCESS_POLL_INTERVAL_FLOOR = 0.01` cannot pass that test because OS pipe readiness (`selectors` on POSIX; blocking `ReadFile` on Windows) exists on every platform Weft supports.
- **Why the opaque `_open_process_io` platform readiness adapter is better:**
  `run_monitored_subprocess` becomes a single-arbiter readiness loop over an opaque platform adapter `_open_process_io(...)`:
  - **Explicit `[WT-7]` accounting of supervision deadlines in `run_monitored_subprocess`:**
    - **Pipe I/O:** **0 Hz** polling. When the child writes to `stdout`/`stderr` or closes its write ends, the kernel signals readiness (`selector.select()` with `EVENT_READ` on POSIX; the overlapped read's completion event on Windows) and the supervisor wakes without any timer expiring. Wakeup latency is whatever the OS scheduler delivers; it is measured in the Task 1 gate, not promised as a figure.
    - **Child exit is its own arbiter input, never inferred from EOF.** A root that exits while a backgrounded descendant still holds `stdout`/`stderr` produces no EOF, so EOF cannot stand in for exit. The exit source is a kernel object on every platform, never a thread: `os.pidfd_open(process.pid)` registered for `EVENT_READ` on Linux; a `select.kqueue()` holding `select.kevent(process.pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT)` on macOS and BSD, whose own descriptor (`kq.fileno()`) is registered for `EVENT_READ` in the selector; and `process._handle` in the Windows wait set. None of these reaps the child or touches `Popen._waitpid_lock`, so the supervisor thread remains the sole caller of `process.poll()`. A thread parked in `process.wait()` was considered and rejected: CPython's `Popen.wait()` holds `_waitpid_lock` across its blocking `waitpid`, and `Popen.poll()` returns `None` whenever it cannot acquire that lock, so such a thread would blind every `poll()` for the whole run and misclassify a child that exits at its timeout boundary. **Ordering rule:** register the exit watch first, then call `process.poll()` once; an exit before registration is caught by that poll, an exit after it by the watch. `ESRCH`/`ProcessLookupError` from the kqueue registration means the child is already gone and is treated as an immediate exit wake.
    - **When `cancel_requested is None`:** The supervisor's wait ceiling is purely the next deadline from `(timeout, monitor_interval)`—passing `None` when both are `None` so `selector.select(None)` / `WaitForMultipleObjects(..., INFINITE)` waits with **0 Hz** wakeups and no fallback cap at all (`1 Hz` when psutil resource monitoring is active). Exit, output, and EOF each wake the wait on their own, so the no-deadline wait cannot hang on an inherited pipe.
    - **When `cancel_requested is not None`:** Under Weft's theory (`[WT-2]`, `[WT-7]`), the ideal is **event-driven cancellation** (where `Consumer._defer_active_control` signals a waitable cancellation event/descriptor passed into the runner so the supervisor wakes in 0 ms with zero polling). Today, however, the public `RunnerPlugin.run_with_hooks` protocol (`weft/ext.py` and `01-Core_Components.md` `[CC-3.1]`, shared across `HostTaskRunner`, `DockerRunner`, `MacOSSandboxRunner`, and `MicrosandboxRunner`) passes `cancel_requested: Callable[[], bool] | None = None` as a pull predicate. Changing `RunnerPlugin.run_with_hooks` across all built-in and extension runners is deferred as a separate cross-plugin contract slice (recorded in §10); while `cancel_requested` remains a pull predicate, `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s` = 20 Hz) is the `[WT-7]` ceiling for that one un-notified condition. No other condition needs a timer: exit, output, and EOF are all arbiter inputs.
  - **On POSIX (`_PosixSelectorProcessIO`):** Uses `selectors.DefaultSelector()` (`kqueue` on macOS/BSD, `epoll` on Linux) on the supervisor thread over non-blocking `stdout` (`EVENT_READ`), `stderr` (`EVENT_READ`), and `stdin` (`EVENT_WRITE`, unregistered and closed as soon as all input bytes are written or the child closes `stdin`).
    - **Zero helper threads, 0 `queue.Queue`s, 0 Hz pipe polling, no timer between output arrival and wakeup.** Child exit is a pidfd on Linux and a kqueue process filter on macOS/BSD, so no exit-wait thread exists on any POSIX platform.
    - **Bounded per-turn stream work & backpressure (`[CC-2.5]`, `[WT-4]` §4.3):** Each turn reads a bounded batch of ready bytes from the kernel pipe and delivers up to `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` (`16`) chunks per stream. If `on_stdout_chunk`/`on_stderr_chunk` is slow and a child floods output continuously, once the bounded in-memory batch and 64 KiB kernel pipe buffer fill, the kernel blocks the child's `write()`—applying natural OS backpressure instead of growing parent memory without bound.
    - **Immediate post-exit drain on idle grandchild:** When `process.poll() is not None`, the kernel has already closed the direct child's descriptors before `waitpid` reaped it, so 100% of the direct child's bytes are already in the kernel pipe buffer. If an inherited descendant is idle (`BlockingIOError`), `finish_after_exit()` drains the kernel buffer and returns as soon as the pipe reports nothing readable, instead of sleeping out `250 ms` (`SUBPROCESS_STREAM_DRAIN_TIMEOUT`); the saved time is measured, not asserted. The `0.25s` ceiling is used *only* when an inherited descendant is actively flooding the pipe after root exit (`flood=True`).
  - **On Windows (`_WindowsOverlappedProcessIO`):** Win32 `CreatePipe()` creates synchronous anonymous pipe handles that WinSock `select()` rejects, and a synchronous-handle design (blocking `ReadFile` threads torn down with `PeekNamedPipe` and `CancelSynchronousIo`) was reviewed and rejected: `PeekNamedPipe` on a handle another thread is reading can block, and `CancelSynchronousIo` only cancels an operation already in flight, so a stop that races the reader's next `ReadFile` submission finds nothing to cancel and the reader blocks again. Instead the Windows leaf uses **overlapped I/O**, the same mechanism `asyncio.windows_utils.pipe` and its `Popen` subclass use in the standard library: the leaf creates each parent-side pipe end with `FILE_FLAG_OVERLAPPED` via `_winapi.CreateNamedPipe` and hands the child-side ends to `subprocess.Popen` through `msvcrt.open_osfhandle`, so `Popen` never creates synchronous handles for supervised commands. Reads and writes are issued with `ov, err = _winapi.ReadFile(handle, n, overlapped=True)` / `ov, err = _winapi.WriteFile(handle, data, overlapped=True)`, exactly as `multiprocessing.connection.PipeConnection` does: `err` is `0` when the operation completed synchronously or `ERROR_IO_PENDING` when it is in flight, and `ov.event` is a waitable handle. `wait_and_drain(timeout)` is one `_winapi.WaitForMultipleObjects([...pending ov.event handles..., process_handle], False, timeout_ms)`: **zero helper threads**, **0 Hz** when idle, child exit is the process handle itself. A completed read is collected with `nread, err = ov.GetOverlappedResult(False)` and its bytes with `ov.getbuffer()`; a broken pipe surfaces as an `OSError` whose `winerror` is `ERROR_BROKEN_PIPE`, which is EOF, because `GetOverlappedResult` returns an error code only for `ERROR_MORE_DATA` and `ERROR_OPERATION_ABORTED` and raises for everything else (as `multiprocessing.connection._recv_bytes` relies on). Writes retire separately by count and never read `getbuffer()`, which is `None` for a write. Teardown cancels an in-flight operation with `ov.cancel()`, then calls `ov.GetOverlappedResult(True)`: `ERROR_OPERATION_ABORTED` means the operation was cancelled, otherwise it completed first and its bytes are collected (the race `multiprocessing.connection.wait` handles the same way). Neither outcome says anything about the pipe buffer, since cancellation does not change handle state, so emptiness is observed separately: with no read outstanding, `_winapi.PeekNamedPipe(handle, 0)` on the overlapped handle reports the bytes available, and a zero result is the terminator; a non-zero result issues another read through the same `ReadFile` / `GetOverlappedResult` completion and error handling as every other read, never assuming it completes synchronously. The peek is safe in this design because no other thread can be blocked in `ReadFile` on the handle. The pipes are byte-mode, so `ERROR_MORE_DATA` does not arise. The remaining Windows battle scar is documented on the class: anonymous `CreatePipe` handles cannot be overlapped, so the leaf must own pipe creation.

### 2.2 PostgreSQL Preflight & Readiness: Falsification Cost and Battle-Scar Documentation

- **Why `test_schema_preflight_requires_real_backend_operation` must be deleted:**
  Pointing `bin/pytest-pg::_initialize_broker_schema` at a closed port (`127.0.0.1:1`) triggers `simplebroker`'s hardcoded 3-attempt connection opener (`0.05s`, `0.10s` backoff) against `simplebroker-pg`'s hardcoded 30-second `ConnectionPool(..., timeout=30.0)` checkout timeout (`3 × 30s + backoff ≈ 96s`). That single negative test adds **96 seconds** to every PostgreSQL suite run. Worse, the positive test in `tests/system/test_pytest_pg_script.py` (`test_postgres_readiness_rejects_bootstrap_then_initializes_schema`, lines 833–930) already falsifies the pre-`540707a3` defect (where `_initialize_broker_schema` constructed and closed a lazy `Queue("weft.pg.preflight", ...)` without calling `queue.peek_one()`) by asserting `SELECT count(*) FROM simplebroker_pg_v1.meta` == `"1"`. Under `[TS-0]`, a 96-second negative test that proves no additional invariant should be removed.
- **Why `bin/pytest-pg::_wait_for_postgres` and its unit test need cleanup:**
  `_wait_for_postgres` used `time.time()` while every other deadline in `bin/pytest-pg` uses `time.monotonic()`, and `test_wait_for_postgres_requires_published_port_and_successful_tcp_query` replaced `pytest_pg.time` with `SimpleNamespace(time=..., sleep=...)`—making a switch to `time.monotonic()` fail with `AttributeError`. In addition, `_wait_for_postgres` used a bare `time.sleep(1)` without a named constant or battle-scar docstring (`AGENTS.md` §1.1), quantizing all 16 isolated `postgres:18` container startups in the suite to 1-second steps. Switching to `time.monotonic()` and `POSTGRES_READY_POLL_INTERVAL_SECONDS = 0.2` with a battle-scar docstring explaining (1) transient `docker port` absence right after `docker run -d` and (2) `postgres:18`'s temporary Unix-socket init server vs. final loopback TCP server cuts startup quantization by 5× and satisfies `AGENTS.md` §1.1.

### 2.3 Retiring the Skipped Dispatch Experiment Harness While Preserving Diagnostic Capabilities

- **Why `tests/system/test_manager_dispatch_costs.py` (1,722 lines) should be deleted rather than relocated:**
  Weft's design philosophy (`AGENTS.md` §1.1, `[TS-0]`) rejects dead or ceremonial scaffolding. `tests/system/test_manager_dispatch_costs.py` is an opt-in A/B/C1/C2 measurement harness gated by `PYTEST_WEFT_DISPATCH_ARTIFACT_DIR`. Its docstring is explicit that the variable "never selects production arm behavior": each arm was a separate checkout, and the harness's `Manager` wrappers are forwarding observers that record real calls, not simulations of rejected arms. It is retired because the experiment it served is complete and its decision is recorded, not because normal CI skips it: it still carries a `Draft integration target` docstring, requires two registered Ruff suppressions (`RUFF-SUP-379`, `RUFF-SUP-380`), and shells out to `git ls-files` with a self-exclusion for its own filename. The harness remains recoverable at commit `4a98f26b`, and the measurement limitations the 2026-10-22 plan recorded (historical package provenance limits causal attribution; one of three PostgreSQL samples beat baseline) stay in that plan's record. Relocating it under the `[TS-0]` benchmark convention is the alternative; deletion is chosen because no open arm remains to measure. By contrast, the retained **Arm B** change in `weft/core/manager.py` (~25 lines across `_process_manager_reactor_turn`, `_process_queue_message`, and `_handle_work_message` per `[MA-3]`, which reuses the turn-start `_cleanup_children()` sweep across `_handle_work_message` during ordinary turn dispatch and yields before reservation in `_process_queue_message` if `_child_sentinel_adapter.wait(0)` or a child timeout becomes ready, with no wall-clock speed claim) is directly tested by `tests/core/test_manager_family_maintenance.py`, and the full A/B/C1/C2 measurement tables are preserved in `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md` (and recoverable via git commit `4a98f26b`).
- **Why `_RetainedOutput` in `tests/helpers/run_diagnostics.py` is kept, while `record_phase` in `tests/cli/test_cli_run_installed_entrypoint.py` is simplified to a `phase()` context manager:**
  Under `pytest-xdist`, `execnet` redirects worker `stdout` away from the OS descriptor, and `pytest-timeout` (`--timeout-method=thread`) writes its thread dump to `terminalreporter._tw` and immediately calls `os._exit(1)` before any `pytest_runtest_logreport` hook can run. Therefore, `_RetainedOutput` in `tests/helpers/run_diagnostics.py` (which is completely inert unless `WEFT_TEST_DIAGNOSTICS_DIR` is set and is fenced by `tests/system/test_run_diagnostics.py`) is a legitimate diagnostic capability and is **retained**. In `tests/cli/test_cli_run_installed_entrypoint.py`, commit `359d8d8f` added two real improvements that we **keep**—the pre-enter harness cleanup block (lines 120–132) and `test_installed_console_init_failure_does_not_materialize_harness_context` (lines 298–322)—plus verbose `record_phase("...:start")` / `try...finally: record_phase("...:done")` ceremony. `record_phase` has the signature `record_phase(config: pytest.Config, phase: str, **details: object)`, so the helper is `phase(config, name, **details)`: it records `{name}:start` with the caller's details (argv, cwd), and `{name}:done` with `elapsed` and, when the block raised, `error=type(exc).__name__` taken from `sys.exception()`. It shortens the wrappers without dropping the command and error metadata the Windows timeout investigation needs.

### 2.4 Plan Corpus Hygiene (`[DOM-15]` & `writing-plans.md` §8)

- **Why the two plan files must be condensed and marked `completed`:**
  `AGENTS.md` `[DOM-15]` explicitly requires: *"Closing a class ≥3 plan requires flipping that index row to `completed` in the same change."* And `docs/agent-context/runbooks/writing-plans.md` §8 requires recording *"the actual reviewer metadata and disposition table... the review findings and the author's disposition for each finding"*, not 5,000+ lines of raw copy-pasted subagent chat transcripts, space-stripped terminal pastes (`FullSQLite5987passed51skipped296.31s`), and unstaged dirty worktree edits currently sitting in `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md` and `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`.

---

## 3. Source Documents

- Governing specifications:
  - `docs/specifications/06-Resource_Management.md` `[RM-5.1]`, `[RM-5.2]`
  - `docs/specifications/01-Core_Components.md` `[CC-2.5]`, `[CC-3.2]`, `[CC-3.4]`
  - `docs/specifications/08-Testing_Strategy.md` `[TS-0]`, `[TS-1]`, `[TS-3.1]`
- Program theory and agent guidance:
  - `docs/program-theory.md` (`[WT-2]` item 4, `[WT-4]` §4.3–§4.4, `[WT-6]` `[ALT-WT-002]`, `[WT-7]`)
  - `AGENTS.md` (§1.1 Design Philosophy, sleep-constant rule, house style, `[DOM-15]` plan closure)
  - `docs/agent-context/engineering-principles.md`
  - `docs/agent-context/runbooks/writing-plans.md`
  - `docs/agent-context/runbooks/hardening-plans.md`
- Related branch plans being cleaned up and closed:
  - `docs/plans/2026-10-27-runtime-and-test-repair-plan.md` (landed on branch, currently `draft` with unstaged edits, raw transcript bloat, and an inaccurate fast-fail preflight claim)
  - `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md` (landed Arm B `_dispatch_cleanup_scope` on branch, rejected Arms C1/C2, currently `draft` with unstaged edits and raw transcript bloat)

---

## 4. Context and Key Files

### Files to modify or delete

- Program theory, subprocess reactor, and constants:
  - `docs/program-theory.md` (`[WT-2]` item 4, the `[WT-3]` platform readiness adapter row, two `[WT-4]` §4.3 rows, and `[WT-6]` `[ALT-WT-021]`; already applied in the working tree with owner approval)
  - `docs/specifications/06-Resource_Management.md` (`[RM-5.2]`)
  - `weft/_constants.py`
  - `weft/core/runners/subprocess_runner.py`
  - `weft/core/runners/host.py` (one-shot `Popen` at line 936 only) and `extensions/weft_docker/weft_docker/plugin.py` (`Popen` at line 231): both construct through `open_supervised_process`
  - `weft/helpers/__init__.py` (its bounded wait loop keeps a local named constant when `SUBPROCESS_POLL_INTERVAL_FLOOR` is removed)
  - `tests/core/test_subprocess_runner.py` and `tests/tasks/test_runner.py` (process construction sites)
- PostgreSQL harness and preflight tests:
  - `docs/specifications/08-Testing_Strategy.md` (`[TS-0]`)
  - `bin/pytest-pg`
  - `tests/system/test_pytest_pg_script.py`
- Benchmark harness removal, diagnostic `phase()` helper, and Ruff suppression cleanup:
  - `tests/system/test_manager_dispatch_costs.py` (**delete**)
  - `tests/helpers/run_diagnostics.py`
  - `tests/cli/test_cli_run_installed_entrypoint.py`
  - `tests/system/test_run_diagnostics.py`
  - `docs/ruff-suppression-registry.md`
  - `tests/specs/test_ruff_policy.py`
- Plan corpus cleanup and closure:
  - `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md`
  - `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`
  - `docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` (this plan)
  - `docs/plans/README.md`

### Files that MUST NOT be modified (validated branch work to preserve and why)

- `weft/core/family_scheduling.py` and `tests/core/test_family_scheduling.py`: Amortized $H \le 2L$ leftist-heap compaction for stale path-queue entries is sound, clean, and well-tested.
- `weft/core/manager.py`, `tests/core/test_manager.py`, `tests/core/test_manager_family_maintenance.py`, and `tests/helpers/manager_dispatch.py`:
  - Preserves **Arm B (`_dispatch_cleanup_scope`)**: during `_process_manager_reactor_turn`, reuses the completed turn-start `_cleanup_children()` sweep across `_handle_work_message` while yielding before reservation in `_process_queue_message` if `_child_sentinel_adapter.wait(0)` or a child timeout is ready (`[MA-3]`, with no speed claim). Note: `_dispatch_cleanup_scope` is a scoped turn-mode flag rather than a recorded turn-sweep fact, which is a minor stylistic wart, but we leave `weft/core/manager.py` untouched in this slice to avoid unnecessary churn in `Manager` while repairing `subprocess_runner.py` and test hygiene.
  - Preserves `S2` serviced retry-deadline retirement (`child.terminal_retry_after_ns = 0` in `_cleanup_children`).
- `weft/core/tasks/consumer.py` and `tests/tasks/test_consumer_terminal_events.py`: Logical stream final-chunk sealing (`closed=True`) on `ok` outcomes is sound and tested.

### Required Reading & Comprehension Check

Before making any edits, the implementer must read:
1. `docs/program-theory.md` (`[WT-2]` item 4, `[WT-4]` §4.3–§4.4, `[WT-6]` `[ALT-WT-002]`, and `[WT-7]`)
2. `docs/specifications/06-Resource_Management.md` `[RM-5.1]`, `[RM-5.2]`
3. `weft/core/runners/subprocess_runner.py` (entire file, lines 1–721) and `weft/_constants.py` (lines 1908–1930)
4. `bin/pytest-pg` (lines 1–280) and `tests/system/test_pytest_pg_script.py` (lines 780–946)
5. `tests/specs/test_ruff_policy.py` (lines 1–160) and `bin/ruff_suppression_index.py`

**Comprehension questions (record answers in execution notes before editing):**
1. *Why did `_finish_streams` in `weft/core/runners/subprocess_runner.py` previously need to wait 250 ms (`SUBPROCESS_STREAM_DRAIN_TIMEOUT = 0.25`) on every exit where a background grandchild held `stdout` or `stderr` open, and why does `_PosixSelectorProcessIO.finish_after_exit()` return without waiting out the budget when that grandchild is silent?*
   - **Expected answer:** Previously, `_PipeAdapter` ran on a separate thread that slept for 10 ms (`SUBPROCESS_POLL_INTERVAL_FLOOR`) whenever `os.read` raised `BlockingIOError`, so the supervisor thread could not distinguish "direct child's last bytes haven't been read by the sleeping adapter thread yet" from "pipe is empty and held open by an idle grandchild" without joining the thread for 250 ms. In `_PosixSelectorProcessIO`, the supervisor thread owns the non-blocking pipe fds directly: once `process.poll() is not None`, the kernel has already closed the direct child's descriptors before `waitpid` reaped it, so all bytes written by the direct child are already in the kernel pipe buffer. A non-blocking `os.read` loop until `BlockingIOError` drains 100% of those bytes immediately, and `SUBPROCESS_STREAM_DRAIN_TIMEOUT` (`0.25s`) is only needed as a time cap if a descendant is actively flooding the pipe (`os.read` keeps returning data without hitting `BlockingIOError` or `b""`).
2. *Under `[WT-7]`, why does `run_monitored_subprocess` still bound `wait_timeout` by `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s`) when `cancel_requested is not None`, and what is `wait_timeout` when `cancel_requested is None`?*
   - **Expected answer:** `RunnerPlugin.run_with_hooks` passes `cancel_requested: Callable[[], bool] | None = None`—a pull predicate with no push notification or selectable fd. Therefore, when `cancel_requested is not None`, `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s` = 20 Hz) is the `[WT-7]` absence-of-push-notification ceiling. When `cancel_requested is None`, `wait_timeout` is bounded only by the remaining execution `timeout` and `monitor_interval`, or is `None` when both are absent (0 Hz extra wakeups when unmonitored); early EOF on both output pipes does not arm a timer, because child exit is an arbiter input of its own.
3. *What happens if you delete `tests/system/test_manager_dispatch_costs.py` (`RUFF-SUP-379`, `RUFF-SUP-380`) without updating `docs/ruff-suppression-registry.md` and `tests/specs/test_ruff_policy.py`?*
   - **Expected answer:** `pytest tests/specs/test_ruff_policy.py` fails because `EXPECTED_GROUP_IDS`, `EXPECTED_GROUP_COUNT`, `EXPECTED_DIRECTIVE_COUNT`, and the generated index block in `docs/ruff-suppression-registry.md` (maintained by `python bin/ruff_suppression_index.py --write`) must match the exact live `# noqa` directives in the tree.

---

## 5. Invariants and Constraints

1. **Zero idle pipe polling (`0 Hz`):** Neither POSIX nor Windows may poll idle subprocess pipes on a fixed timer (`SUBPROCESS_POLL_INTERVAL_FLOOR` is removed). While a subprocess is silent, `wait_and_drain(timeout)` blocks in the OS selector (POSIX) or in `WaitForMultipleObjects` over overlapped completion events and the process handle (Windows), with no helper threads on any platform, until pipe readiness, EOF, child exit, or the next supervision deadline (`timeout`).
1a. **Child exit is an arbiter input:** The exit of the direct child wakes the wait on its own through a kernel object (pidfd on Linux, kqueue process filter on macOS/BSD, process handle on Windows), never through a thread in `process.wait()`. A wait with no deadlines armed must still return promptly when the root exits while a descendant holds the output pipes open. EOF is never used as a proxy for exit, and the supervisor thread is the sole caller of `process.poll()`.
1b. **One buffering bound, stated end to end, with its terminal-phase exception:** While execution is active, in-flight output held by the supervisor between kernel pipe and callback delivery is bounded per stream by `SUBPROCESS_STREAM_TURN_READ_BYTES` of undelivered bytes; a stream at its bound is not read again until delivery frees capacity, so the child blocks on the full kernel pipe. On Windows an overlapped read is not re-issued for a stream at its bound. Capacity is released only when a chunk is delivered to the callback, never when it moves between internal structures. Exit, EOF, and adapter-error events are never subject to data capacity and are always deliverable. **Terminal capture is the one exception:** after the terminal decision, `finish_after_exit()` captures without delivering callbacks, so its backlog is bounded by time (`SUBPROCESS_STREAM_DRAIN_TIMEOUT`) and by how fast a descendant can write in that window, not by the in-flight byte bound; it is finite and is delivered in full afterwards. The accumulated result strings (`collected`) are the command's output and grow with it by design; they are not in-flight buffering and are not bounded by this rule.
1c. **Fairness budget is a production rule, not a fixture accommodation:** Per turn, each output stream is read up to `SUBPROCESS_STREAM_TURN_READ_BYTES` and delivered up to `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` chunks, then supervision runs. The budget value, 64 KiB, is the chosen fairness budget; it matches a common default pipe buffer size but is not a portable fact about kernel pipe capacity, and nothing in the design depends on the two being equal. A child that produces faster than its sink is legitimately slowed by backpressure and may reach a test marker later; tests set expectations from this budget rather than the budget being sized to a test's timing.
2. **Input backpressure cannot stall supervision (`[RM-5.2]`):** A child that never reads a multi-megabyte `stdin_data` payload must not block `cancel_requested`, `spec.timeout`, `monitor.check_limits()`, or `stdout`/`stderr` drainage.
3. **Root completion vs. inherited pipe handles (`[RM-5.2]`):** Direct child exit (`process.poll() is not None`) is the execution completion boundary. An idle grandchild holding `stdout` or `stderr` open must not block completion or incur a 250 ms sleep; a flooding grandchild holding `stdout` or `stderr` open is bounded by `SUBPROCESS_STREAM_DRAIN_TIMEOUT` (`0.25s`).
4. **Capture before slow-callback delivery at termination (`[RM-5.2]`):** When execution ends (`ok`, `error`, `cancelled`, `timeout`, `limit`), `finish_after_exit()` must freeze/drain remaining kernel pipe output into memory and close parent pipe endpoints *before* draining captured chunks through `on_stdout_chunk` / `on_stderr_chunk`, so a slow callback (`15 ms`/chunk) cannot cause already-produced output to be dropped by the capture deadline.
5. **Exception precedence and resource rollback (`[RM-5.1]`, `[RM-5.2]`):**
   - Non-`Exception` `BaseException`s from start callbacks or monitor teardown propagate with their exact identity unless a primary execution failure is already unwinding.
   - Any failure during pipe session setup, stdin encoding, pipe read/write, or callback delivery reaps the child process (`cleanup_after_execution_failure`) and closes all parent pipe descriptors before propagating.
   - On Windows, an `OSError` whose `winerror` is `ERROR_BROKEN_PIPE` or `ERROR_NO_DATA` on an overlapped `stdin` write is treated as closed-pipe (matching POSIX `BrokenPipeError`); the former MSVCRT `errno.EINVAL` case no longer arises because the leaf writes through `_winapi.WriteFile`, not `os.write`. Other `OSError`s (`EIO` on POSIX, any other `winerror` on Windows) raise.
6. **Difference Register (`_PipeAdapter` vs. `_open_process_io`):**

| Behavior / Edge Case | Previous (`_PipeAdapter` + `time.sleep`) | New (`_open_process_io`: `_PosixSelectorProcessIO` / `_WindowsOverlappedProcessIO`) | Disposition |
|---|---|---|---|
| Root exit while a descendant holds the pipes, no deadline armed | Noticed on the next supervisor tick (≤ 50 ms) because `process.poll()` ran every loop | Noticed through the exit source (pidfd / kqueue process filter / Windows process handle); never depends on EOF; no thread in `process.wait()` | **Improved; required for correctness of the no-deadline wait** |
| Idle child pipe polling rate | 200 Hz across `stdout`/`stderr` threads (`0.01s` sleep) + 20–100 Hz supervisor `time.sleep` (~890 context switches / 35 ms CPU per 3 s idle child on macOS) | **0 Hz** pipe polling with no helper threads on any platform. Supervisor wakes only on pipe readiness/EOF, child exit, `timeout`, `monitor_interval`, or (when `cancel_requested is not None`) `ACTIVE_CONTROL_POLL_INTERVAL` (`50 ms` = 20 Hz) | **Improved (theory alignment `[WT-2]`, `[ALT-WT-002]`)** |
| Output arrival wakeup latency | Up to `50 ms` (`time.sleep(sleep_for)`) | Bounded by OS wakeup only (`selector.select` on POSIX, `WaitForMultipleObjects` on Windows); measured in the Task 1 gate, not asserted as a figure | **Improved (theory alignment)** |
| Child output backpressure during slow callbacks | Reader threads buffered child output into an **unbounded** in-memory `queue.Queue[str \| None]` in the parent process | In-flight undelivered output per stream is bounded by `SUBPROCESS_STREAM_TURN_READ_BYTES` (invariant 1b); capacity is released on callback delivery only; a stream at its bound is not read, so the kernel blocks the child's `write()` on the full pipe. Exit/EOF/error events bypass data capacity. Accumulated result strings still grow with output | **Intentional improvement (`[CC-2.5]`, `[WT-4]` §4.3); backpressured children may finish later** |
| Supervised `Popen` pipe creation on Windows | `Popen` creates synchronous anonymous pipes | The Windows leaf creates overlapped pipe pairs (`asyncio.windows_utils.pipe` shape) and hands the child ends to `Popen`; POSIX unchanged | **New narrow platform leaf; `[WT-4]` §4.4 one path on all platforms** |
| Post-exit drain with idle grandchild holding pipe open | Sleeps full `250 ms` (`SUBPROCESS_STREAM_DRAIN_TIMEOUT = 0.25`) | Stops as soon as the kernel reports no readable bytes (`BlockingIOError` on POSIX; on Windows a `PeekNamedPipe` reporting zero bytes available after the pending read is settled, with any read that completed first captured); measured, not asserted | **Improved** |
| Post-exit drain with flooding grandchild | Bounded by `SUBPROCESS_STREAM_DRAIN_TIMEOUT` (`0.25s`), logs warning if EOF missing | Bounded by `SUBPROCESS_STREAM_DRAIN_TIMEOUT` (`0.25s`), logs `"Subprocess stream capture ended before stdout/stderr EOF"` if EOF missing | **Preserved** |
| Incremental UTF-8 + universal newline decoding (`\r\n` / `\r` -> `\n`) | `io.IncrementalNewlineDecoder(codecs.getincrementaldecoder(...), translate=True)` | Identical decoder chain and true-EOF flush; raw decoder input charged and limited to64KiB, including terminal capture | **Preserved for bounded codec sequences; oversized valid stateful sequences now fail explicitly** |
| Line-ending translation on `stdin` (`\n` -> `os.linesep`) | `codecs.getincrementalencoder(...).encode(chunk.replace("\n", os.linesep), final)` | Identical incremental encoding and `os.linesep` translation | **Preserved** |
| Closed-pipe `stdin` write (`BrokenPipeError`, or Windows MSVCRT `EINVAL` under `os.write`) | Ignored; closes `stdin` cleanly | Same outcome; on Windows the closed-pipe signal is now an `OSError` with `winerror` `ERROR_BROKEN_PIPE` or `ERROR_NO_DATA` from `_winapi.WriteFile`, because the leaf no longer writes through `os.write` | **Preserved behavior; Windows error surface changes with the primitive** |
| Per-turn stream fairness under continuous output flood | `_drain_stream_queue` caps at `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` (`16`) | Each reactor turn caps callback deliveries at `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` (`16`) per stream and yields on `turn_deadline` so supervision checks (`cancel`, `timeout`, `monitor`) are never starved | **Preserved** |

---

## 5a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

---

## 5b. Spec Baseline

- `540707a370d32cf9475832099fabd52fa1dc7f0a` — `docs/program-theory.md`, `docs/specifications/06-Resource_Management.md`, and `docs/specifications/08-Testing_Strategy.md` at plan authoring time.

---

## 5c. Proposed Spec Delta

Promotion strategy: **B — Atomic** (update `docs/program-theory.md`, spec text, code, and reciprocal backlinks together during Task 1–2 execution before merge).

| File | Strategy | Sections touched |
|------|----------|------------------|
| `docs/program-theory.md` | B — Atomic, **already applied in the working tree with owner approval on 2026-10-29** | `[WT-2]` item 4 (supervision loops inside a task's worker lane), a `[WT-3]` row (platform readiness adapter), two `[WT-4]` §4.3 rows (readiness-driven supervision; bounded per-turn stream work and backpressure), and `[WT-6]` `[ALT-WT-021]`. Task 1 step 1 must not re-apply it; it commits with this plan. |
| `docs/specifications/06-Resource_Management.md` | B — Atomic | `[RM-5.2]` paragraphs 2–5 (lines 224–255) and `## Related Plans` |
| `docs/specifications/08-Testing_Strategy.md` | B — Atomic | `[TS-0]` PostgreSQL harness paragraph (lines 62–68) and `## Related Plans` |

### `docs/program-theory.md` — already applied (owner-approved, 2026-10-29)

The theory delta is the working-tree diff of `docs/program-theory.md` at plan
authoring time and is not restated here. It adds: a paragraph under `[WT-2]`
item 4 extending the reactor law to supervision loops inside a task's worker
lane (readiness demultiplexing on POSIX, overlapped completion waits on
Windows, child exit through a kernel object on every platform, deadlines only
as protective ceilings); a `[WT-3]` row defining the
platform readiness adapter; two `[WT-4]` §4.3 rows (readiness-driven
supervision with the drain-budget clause; bounded per-turn stream work and
backpressure); and `[WT-6]` `[ALT-WT-021]`, which rejects non-blocking pipe
polling loops and records the rejected synchronous-handle Windows design. No
`[WT-4]` §4.4 row is edited. Task 1 verifies the diff is present and commits it
with the `[RM-5.2]` promotion.

### `docs/specifications/06-Resource_Management.md` `[RM-5.2]` — replace paragraphs from `"For one-shot command execution through the shared subprocess supervisor,"` through `"Existing Consumer shutdown rules still govern a publisher that has stopped accepting worker events."` (lines 224–255)

> For one-shot command execution through the shared subprocess supervisor,
> delivering the work item's stdin is part of execution. The execution clock
> starts no later than input delivery, and input backpressure must not prevent
> cancellation, timeout, or resource-limit supervision. Subprocess pipe I/O is
> owned by an opaque platform readiness adapter behind the supervisor reactor;
> while execution is active, the supervisor waits on platform pipe readiness
> bounded by the next scheduled supervision deadline rather than polling pipes
> on a fixed timer, and cancellation or teardown must not depend on another
> thread acquiring a buffered I/O lock held by a blocked operation.
>
> Root process completion and output-pipe EOF are separate observations. Once
> root completion is accepted, the supervisor performs a bounded post-exit
> capture pass over `stdout` and `stderr`: capture finishes immediately when
> both streams reach EOF or have no remaining buffered bytes in the kernel pipe,
> and is bounded by the private stream-drain budget when an inherited
> descendant continues writing. The session then closes its owned parent pipe endpoints, freezing a finite set of captured output, and delivers
> that captured output before forming its returned outcome; capture-budget
> expiry must not discard output already captured in memory. The same capture,
> endpoint-release, and delivery sequence follows the existing runtime
> stop/kill hooks for cancellation, timeout, and resource-limit outcomes,
> preserving those outcomes.
>
> The capture budget is not a total result-delivery or teardown timeout. Delivery
> duration depends on captured volume and sink throughput and may exceed the
> private budget. Output produced after capture is stopped is not promised.
> Budget expiry neither converts a completed root into an execution timeout nor
> asserts physical EOF. Missing physical EOF remains a bounded diagnostic, with
> no new terminal state or outcome field.
>
> While execution is active, output drainage performs finite work per stream
> before returning to other streams and supervision, and slow downstream sinks
> apply pipe backpressure rather than buffering child output without bound in
> parent memory. After capture is frozen, delivery drains the captured chunks
> without idle waits. These rules do not preempt an arbitrarily blocking
> callback. A callback exception follows the existing execution-failure cleanup
> and exception-precedence contract; it does not guarantee delivery to the
> failed callback. Existing Consumer shutdown rules still govern a publisher
> that has stopped accepting worker events.

### `docs/specifications/08-Testing_Strategy.md` `[TS-0]` — replace paragraph from `"PostgreSQL harness startup must distinguish"` through `"[2026-10-27-runtime-and-test-repair-plan.md, CI-D3](../plans/2026-10-27-runtime-and-test-repair-plan.md)."` (lines 62–68)

> PostgreSQL harness startup must distinguish the official `postgres:18` image's
> temporary Unix-socket initialization server from its final TCP server. The
> canonical `bin/pytest-pg::_wait_for_postgres` polls with a monotonic deadline
> (`POSTGRES_READY_POLL_INTERVAL_SECONDS = 0.2`) until `docker port` publishes
> the host port and an in-container loopback TCP query succeeds; its subsequent
> `_initialize_broker_schema` performs a non-destructive queue read
> (`queue.peek_one()`) through the consumer's host target before tests start.
> Constructing and closing a lazy Queue is not a schema or connectivity check.
> Repair and verification:
> [`2026-10-27-runtime-and-test-repair-plan.md`, CI-D3](../plans/2026-10-27-runtime-and-test-repair-plan.md)
> and [`2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`](../plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md).

---

## 6. Rollback and Sequencing

- **Sequencing:**
  1. Task 1 promotes the `[RM-5.2]` spec clarification and replaces `_PipeAdapter` + `time.sleep` in `weft/core/runners/subprocess_runner.py` and `weft/_constants.py` with `_open_process_io` (`_PosixSelectorProcessIO` and `_WindowsOverlappedProcessIO`, with `open_supervised_process(...)` owning pipe creation at the host and Docker `Popen` sites), updating `tests/core/test_subprocess_runner.py` and recording before/after idle CPU, context-switch, and latency measurements on all three platforms.
  2. Task 2 promotes the `[TS-0]` spec clarification and fixes `bin/pytest-pg` and `tests/system/test_pytest_pg_script.py` (deleting the 96-second negative test, switching `_wait_for_postgres` to `time.monotonic()` + `POSTGRES_READY_POLL_INTERVAL_SECONDS = 0.2` with battle-scar comments, and fixing the unit test monkeypatch).
  3. Task 3 deletes `tests/system/test_manager_dispatch_costs.py`, adds the `phase(config, name, **details)` context manager to `tests/helpers/run_diagnostics.py` (while keeping `_RetainedOutput`), simplifies `tests/cli/test_cli_run_installed_entrypoint.py` to use `with phase(...):`, and reconciles `docs/ruff-suppression-registry.md` + `tests/specs/test_ruff_policy.py`.
  4. Task 4 condenses the two bloated plan files, reconciles their dirty worktree state and preflight/memory claims, flips completed plan statuses in `docs/plans/README.md`, and runs the full verification gates (including `bin/pytest-pg --all`).
- **Rollback:** All changes are internal to `subprocess_runner.py`, test harnesses, and documentation on the PR branch; no queue names, wire formats, or public `TaskSpec` / CLI contracts change. Any individual task can be reverted cleanly without affecting durable broker state.

---

## 7. Tasks

### Task 1: Replace `_PipeAdapter` and supervisor polling with `_open_process_io` readiness adapter

- **Outcome:** `run_monitored_subprocess` in `weft/core/runners/subprocess_runner.py` is a readiness-driven reactor over `_open_process_io(...)`, with 0 Hz idle pipe polling on both POSIX and Windows and no `SUBPROCESS_POLL_INTERVAL_FLOOR`.
- **Files to touch:**
  - `docs/program-theory.md`
  - `docs/specifications/06-Resource_Management.md`
  - `weft/_constants.py`
  - `weft/core/runners/subprocess_runner.py`
  - `weft/core/runners/host.py` (the one-shot `subprocess.Popen` at line 936 only; the interactive session `Popen` at line 1082 is out of scope) and `extensions/weft_docker/weft_docker/plugin.py` (the `Popen` at line 231): both construct their supervised process through the new `open_supervised_process(...)` helper so the Windows leaf owns pipe creation
  - `tests/core/test_subprocess_runner.py`
- **Read first:**
  - `docs/program-theory.md` (`[WT-2]` item 4, `[WT-4]` §4.3, `[ALT-WT-002]`, `[WT-7]`)
  - `asyncio/windows_utils.py` in the running interpreter (`pipe()` and `Popen`) for the overlapped-pipe construction the Windows leaf mirrors
  - `docs/specifications/06-Resource_Management.md` `[RM-5.1]`, `[RM-5.2]`
  - `weft/_constants.py` (lines 1908–1930)
  - `weft/core/runners/subprocess_runner.py` (lines 1–721)
  - `tests/core/test_subprocess_runner.py`
- **Implementation details:**
  1. **Apply the `[RM-5.2]` delta** in `docs/specifications/06-Resource_Management.md` (from §5c above) and add a backlink to this plan under `## Related Plans`. The `docs/program-theory.md` delta is already applied in the working tree (owner-approved, 2026-10-29); verify it is present and commit it with this task, do not re-apply it.
  2. **Constants in `weft/_constants.py`:** remove `SUBPROCESS_POLL_INTERVAL_FLOOR` (its only other user, `weft/helpers/__init__.py:485`, keeps a local named constant for its bounded wait loop); keep `SUBPROCESS_STREAM_READ_SIZE = 65536`, `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS = 16`, and `SUBPROCESS_STREAM_DRAIN_TIMEOUT = 0.25`, updating the drain-timeout docstring to say it bounds post-exit capture only while an inherited descendant keeps writing; add `SUBPROCESS_STREAM_TURN_READ_BYTES: Final[int] = 64 * 1024` with a docstring stating it is one kernel pipe buffer, the per-stream per-turn read budget and the in-flight undelivered bound (invariants 1b and 1c). It is a fairness rule, not a fixture accommodation; tests that shrink `SUBPROCESS_STREAM_READ_SIZE` must derive their expectations from it.
  3. **Design `_open_process_io` in `weft/core/runners/subprocess_runner.py`:**
     - Define a shared `_OutputStreamState` helper holding the `IO[str]` wrapper, raw file descriptor, `io.IncrementalNewlineDecoder`, `collected: list[str]`, `pending_chunks: collections.deque[str | None]`, `on_chunk: Callable[[str, bool], None] | None`, `eof_reached: bool`, and `closed_callback_sent: bool`.
     - Define `_StdinStreamState` holding the `IO[str]` wrapper, raw file descriptor, incremental encoder (`codecs.getincrementalencoder(encoding)(errors=errors)`), `input_data: str`, `offset: int`, and `pending: bytes`. Validate/encode the first chunk at session construction (before entering the reactor loop) so `UnicodeEncodeError` on invalid `stdin_data` (such as `"\ud800"` with `errors="strict"`) raises deterministically and triggers `cleanup_after_execution_failure`.
     - **POSIX backend (`_PosixSelectorProcessIO`):**
       - Accepts an optional `selector_factory: Callable[[], selectors.BaseSelector] = selectors.DefaultSelector` parameter (defaulting to `selectors.DefaultSelector`) so unit tests can inject a forwarding spy selector to assert exact `select(timeout)` calls without mocking OS pipes.
       - Sets `os.set_blocking(fd, False)` on `stdout`, `stderr`, and (if `stdin` is present and `stdin_data` is non-empty) `stdin`; if `stdin` is present and `stdin_data` is `None` or `""`, closes `stdin` immediately on startup.
       - Registers `stdout` and `stderr` with `selectors.EVENT_READ` and `stdin` with `selectors.EVENT_WRITE`.
       - Registers a child-exit source with `selectors.EVENT_READ`, chosen at session construction: on Linux, `os.pidfd_open(process.pid)` (closed in `finish_after_exit()`); on macOS and BSD, a `select.kqueue()` to which `kq.control([select.kevent(process.pid, filter=select.KQ_FILTER_PROC, flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT, fflags=select.KQ_NOTE_EXIT)], 0, 0)` has been applied, registering `kq.fileno()` in the selector (the kqueue is closed in `finish_after_exit()`); a `ProcessLookupError` from that `control` call means the child already exited and is recorded as an immediate wake. No thread calls `process.wait()`: `Popen.wait()` holds `_waitpid_lock` across its blocking `waitpid` and `Popen.poll()` returns `None` while that lock is held, so a waiting thread would blind the supervisor's polls. Ordering: register the watch, then `process.poll()` once before entering the loop. Readiness on the exit source makes `wait_and_drain` return so the supervisor's existing `process.poll()` check observes the exit; the source carries no data the supervisor consumes.
       - `wait_and_drain(timeout: float | None) -> None`:
         - If neither `stdout.pending_chunks` nor `stderr.pending_chunks` has undelivered chunks, calls `selector.select(None if timeout is None else max(0.0, timeout))`. If undelivered chunks are already queued in `pending_chunks`, calls `selector.select(0.0)` to service any newly ready kernel pipe buffers without sleeping.
         - **Initial write pass at session construction:** before the first `wait_and_drain`, perform one non-blocking write pass on `stdin` under the same per-turn budget as every later turn (encode and `os.write` slices until `SUBPROCESS_STREAM_TURN_READ_BYTES` have been written this pass, `BlockingIOError`, or all input is written); register `stdin` for `EVENT_WRITE` only if unwritten bytes remain. The construction pass must not bypass the fairness rule the later turns obey. This completes small inputs with no selector registration and, when the child has already exited before supervision begins (as `test_early_root_exit_does_not_turn_unread_input_into_failure` arranges by calling `process.wait()` first), still attempts the write so a closed pipe is observed as `BrokenPipeError` rather than silently never written.
         - When `stdin` is ready for `EVENT_WRITE`, encodes and writes slices up to `SUBPROCESS_STREAM_READ_SIZE`, bounded per turn by `SUBPROCESS_STREAM_TURN_READ_BYTES` total so a fast reader consuming a large input cannot postpone output delivery or supervision, until the budget, `BlockingIOError` (buffer full), or all input is written (unregister and close `stdin`). If `os.write` raises `BrokenPipeError`, unregister and close `stdin` without error.
         - When `stdout` or `stderr` is ready for `EVENT_READ` and the stream's undelivered in-flight bytes are below `SUBPROCESS_STREAM_TURN_READ_BYTES`, reads with each request sized `min(SUBPROCESS_STREAM_READ_SIZE, remaining_in_flight_capacity, remaining_turn_budget)` so a request can never carry the backlog past the bound (48 KiB pending admits a 16 KiB read, not a 64 KiB one), until the per-turn read budget (`SUBPROCESS_STREAM_TURN_READ_BYTES`) is reached, `BlockingIOError`, or `b""` (true EOF: decode with `final=True`, append the `None` sentinel to `pending_chunks`, unregister, and close the stream). A stream at its in-flight bound is simply not read that turn; it stays registered. This is sufficient because the bound is non-zero only while `pending_chunks` is non-empty, and in that state the wait is `select(0.0)`, so a readable-but-unread descriptor never holds the supervisor in a blocking select; unregister/re-register churn is avoided. The kernel pipe fills and the child blocks. No read allowance is sized to any fixture.
         - Delivers up to `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` (`16`) items per stream from `pending_chunks` to `collected` and `on_chunk`, yielding back to the reactor if `time.monotonic() >= turn_deadline` after at least one chunk is delivered so a slow callback (`15 ms`/chunk) never starves supervision checks (`cancel_requested`, `timeout`, `monitor.check_limits()`).
       - `finish_after_exit() -> tuple[str, str]`:
         - Unregisters and closes `stdin` if still open.
         - Capture phase (bounded by `deadline = time.monotonic() + SUBPROCESS_STREAM_DRAIN_TIMEOUT`): while `stdout` or `stderr` has not reached EOF and `time.monotonic() < deadline`, drains currently readable bytes from `stdout`/`stderr` into `pending_chunks` without invoking callbacks yet. **Crucial reactor rule:** if `os.read` on all remaining open output streams returns `BlockingIOError` (the kernel pipe buffer is empty after `process.poll() is not None` or process termination), break immediately—do **not** sleep or wait for `deadline`! Only if a stream keeps returning bytes continuously (a flooding grandchild) does the capture loop continue until `BlockingIOError`, `EOF`, or `deadline`.
         - If a capture-phase read raises an adapter error, the remaining open streams are still drained and every endpoint closed before the error is raised, so a competing stream's captured bytes and the descriptors are not lost to the exception.
         - Unregisters and closes `stdout`, `stderr`, the exit source (the pidfd or the kqueue), and the selector.
         - Delivery phase: drains all frozen `pending_chunks` from `stdout` and `stderr` in interleaved batches of `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` (`16`) into `collected` and `on_chunk` without any sleep. If either stream did not reach true OS EOF (`None`), logs `logger.warning("Subprocess stream capture ended before stdout/stderr EOF")`. Returns `("".join(stdout.collected), "".join(stderr.collected))`.
     - **Process construction helper (`open_supervised_process(...)`)**: a module-level function in `subprocess_runner.py` that every supervised one-shot command uses in place of a bare `subprocess.Popen` (host.py line 936 and the Docker plugin line 231), and that the tests call **directly in place of `subprocess.Popen(...)`** at each site: the 21 sites in `tests/core/test_subprocess_runner.py` and the three in `tests/tasks/test_runner.py` (lines 1464, 1538, 1615) that pass a process to `run_monitored_subprocess`. `_supervise_real_process(process, ...)` keeps accepting an already-created process, because tests interact with it before supervision and reap it afterwards. This way tests obtain the same handles production does and the Windows CI jobs exercise the overlapped path in every real-process test. On POSIX it is `subprocess.Popen(..., stdin=PIPE, stdout=PIPE, stderr=PIPE, text=...)` unchanged. On Windows it follows `asyncio.windows_utils.Popen` endpoint for endpoint, with these departures from the stdlib text: each `pipe()` call is wrapped individually so a failure creating the second or third pair closes the pairs already made (the stdlib `try` begins only after all three); `bufsize=SUBPROCESS_STREAM_TURN_READ_BYTES` (65536) is passed instead of the stdlib default of 8192 so the pipe buffer matches the fairness budget; the stdlib `assert`s on `universal_newlines` and `bufsize` become explicit checks; once `msvcrt.open_osfhandle(child_h, flags)` has returned an fd, that fd owns `child_h` and is released with `os.close(fd)`, and `_winapi.CloseHandle(child_h)` is called only if `open_osfhandle` itself failed. The parent-side handles are wrapped in a small `_OverlappedPipe` object exposing `handle`, `fileno()`, `close()`, a `closed` property, and `encoding`/`errors` taken from the `Popen` text configuration, because the stdlib `PipeHandle` has none of `closed`, `encoding`, or `errors`, and the supervisor's cleanup (`subprocess_runner.py:436`) and the tests (`test_subprocess_runner.py:197`, `:901`) read `stream.closed`:

       | Stream | Pipe call (from `asyncio.windows_utils.pipe`) | Parent end | Child end | Close responsibility |
       |---|---|---|---|---|
       | `stdin` | `pipe(overlapped=(False, True), duplex=True)` | write handle, **overlapped**, owned by the leaf | read handle, synchronous, wrapped with `msvcrt.open_osfhandle(h, os.O_RDONLY)` and passed as `stdin` | child fd closed by the parent in a `finally` after `Popen` returns; parent handle closed by the leaf in `finish_after_exit()` or on rollback |
       | `stdout` | `pipe(overlapped=(True, False))` | read handle, **overlapped**, owned by the leaf | write handle, synchronous, wrapped with `msvcrt.open_osfhandle(h, 0)` and passed as `stdout` | same |
       | `stderr` | `pipe(overlapped=(True, False))` | read handle, **overlapped**, owned by the leaf | write handle, synchronous, wrapped and passed as `stderr` | same |

       If `Popen` raises, the parent-side handles are closed before re-raising (the `except:` branch in `windows_utils.Popen`); if attaching the parent handles to the returned process fails, the process is reaped through `cleanup_after_execution_failure` and all six handles are closed. Rollback tests inject a failure at each of the three points: pipe creation, process creation, and handle attachment. Encoding and newline settings are read from the `Popen` text configuration so the decoder chain is identical on both platforms; `bufsize=0` and no `universal_newlines`, as the stdlib recipe requires.
     - **Windows backend (`_WindowsOverlappedProcessIO`):**
       - Class docstring records the battle scar and the rejected design: anonymous `CreatePipe` handles are synchronous, `PeekNamedPipe` on a handle another thread is reading can block, and `CancelSynchronousIo` cannot cancel a read that has not yet been submitted, so the synchronous-thread design is not used.
       - **Operation state per endpoint:** each of `stdout`, `stderr`, `stdin` holds at most one `Overlapped` (`ov`) and a state in `{idle, pending, eof, closed}`. **Read sizing:** a read is issued only while the stream's undelivered in-flight bytes plus bytes reserved by its outstanding read are below `SUBPROCESS_STREAM_TURN_READ_BYTES` (invariant 1b), and its size is `min(SUBPROCESS_STREAM_READ_SIZE, remaining_in_flight_capacity, remaining_turn_budget)`; the single-outstanding-read rule prevents another read before retirement, so its issued size remains within the capacity observed at issuance (48 KiB pending cannot be joined by a64KiB read). Issue: `ov, err = _winapi.ReadFile(handle, size, overlapped=True)`, inside `try/except OSError as exc`: `exc.winerror == ERROR_BROKEN_PIPE` is EOF (state `eof`, decode `final=True`, append `None`); any other `OSError` is the adapter error. If `err == 0` the read completed synchronously and is collected at once (loop: issue the next read under the same sizing rule until `ERROR_IO_PENDING`, EOF, or the bound); if `err == ERROR_IO_PENDING` the state is `pending` and `ov.event` joins the wait set. At the bound the stream stays `idle` with no read pending, so the kernel pipe fills and the child blocks. `stdin` issues `ov, err = _winapi.WriteFile(handle, slice, overlapped=True)` for the next slice, with a per-turn write budget of `SUBPROCESS_STREAM_TURN_READ_BYTES` bytes so a fast reader of a large input cannot postpone output and supervision; an `OSError` with `winerror in (ERROR_BROKEN_PIPE, ERROR_NO_DATA)` on issue or on retirement closes `stdin` without error; `stdin` is `closed` once all input is written.
       - **Wait-set rule:** the wait set is rebuilt every turn and contains exactly the `ov.event` handles of endpoints in state `pending` plus `process._handle`. Completed operations are collected and their `ov` discarded the turn they complete, so no retired manual-reset event stays in the set; an absent `stdin`, a closed `stdin`, and a stream at `eof` contribute nothing. Because `process._handle` is always present, exit and error are never data-gated.
       - `wait_and_drain(timeout: float | None)`: **the wait is non-blocking (`timeout_ms = 0`) whenever `stdout.pending_chunks` or `stderr.pending_chunks` is non-empty**, exactly as the POSIX leaf uses `select(0.0)`; otherwise `timeout_ms` is `INFINITE` for `None` or `ceil(timeout * 1000)`. A synchronously completed read therefore never leaves queued delivery waiting behind a full-timeout wait, and a child that blocks until a callback-driven release cannot hang the supervisor. `WaitForMultipleObjects` returns only the lowest signalled index, so after the first signalled handle at index `i` the leaf re-waits `handles[i + 1:]` with `timeout_ms = 0` until `WAIT_TIMEOUT`, collecting every signalled handle this turn (the batched-wait pattern in `multiprocessing.connection`); without this a flooding `stdout` at index 0 would starve `stderr`, `stdin`, and the process handle. **Read retirement:** `nread, err = ov.GetOverlappedResult(False)` inside `try/except OSError as exc`: `exc.winerror == ERROR_BROKEN_PIPE` is EOF; a returned `err == 0` yields `ov.getbuffer()[:nread]` to decode and queue and releases the reserved bytes; `ERROR_MORE_DATA` cannot occur on byte-mode pipes; any other outcome is the adapter error. **Write retirement is separate:** `nwritten, err = ov.GetOverlappedResult(False)` advances the input offset by `nwritten` and issues the next slice or closes; `ov.getbuffer()` is never consulted for a write (it returns `None`). For the process handle: return so the supervisor's `process.poll()` observes exit. Then deliver up to `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` chunks per stream, releasing in-flight capacity per delivered chunk, yielding on `turn_deadline`, and issue new reads for streams that are `idle` and below their bound.
       - `finish_after_exit()`: capture phase bounded by `SUBPROCESS_STREAM_DRAIN_TIMEOUT`, with the terminator being an explicit emptiness observation, not an inference from a wait or a cancellation. For each output stream not at `eof`, repeat while the budget remains: (1) **settle** any pending read: `ov.cancel()` then `nread, err = ov.GetOverlappedResult(True)` inside `try/except OSError`; `ERROR_OPERATION_ABORTED` means the operation was cancelled (which says nothing about the buffer, since cancellation does not change handle state); a completed read (`err == 0`) means data won the race and its bytes are captured; `ERROR_BROKEN_PIPE` is EOF and ends the stream. (2) **observe** the buffer with no read outstanding: `navail, _ = _winapi.PeekNamedPipe(handle, 0)`, which is safe here because the handle is overlapped and this is the only thread and there is no pending read on it (the earlier objection to `PeekNamedPipe` concerned synchronous handles with another thread blocked in `ReadFile`); `ERROR_BROKEN_PIPE` from the peek is EOF. (3) If `navail > 0`, issue a read and retire it through the existing completion and error handling (`err == 0` collect now; `ERROR_IO_PENDING` wait on `ov.event` under the remaining budget, then `GetOverlappedResult`; `ERROR_BROKEN_PIPE` is EOF), never assuming a positive availability query makes the read complete synchronously, collect it, and go to (2); if `navail == 0`, that is the justified empty-pipe observation and capture of that stream ends. The budget bounds a descendant that keeps writing; an idle inherited pipe ends capture on the first zero peek. Pending writes are retired by the same cancel-then-collect sequence without reading any buffer. If a capture-phase read raises an adapter error, the remaining open streams are still drained and every endpoint closed before the error is raised, so a competing stream's captured bytes and the handles are not lost to the exception. Close the parent-side handles, mark endpoints `closed`, and deliver the frozen backlog. No thread is joined because none exists. `RUFF-SUP-354` is retired with `_PipeAdapter._run` unless another worker-thread boundary remains.
       - **Windows test obligation:** the adapter tests in Task 1 step 5 run on the Windows CI matrix jobs and must execute (verified JUnit cases), not skip; a Windows-only test holds the child immediately before its first write and then cancels, proving teardown with a pending overlapped read and with no pending read.
  4. **Update `run_monitored_subprocess`:**
     - Compute `wait_timeout: float | None`:
       - Start with `deadlines: list[float] = []`.
       - When `cancel_requested is not None`, append `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s`) to `deadlines`. No other condition appends a timer: early EOF on both output pipes is not a reason, because child exit is an arbiter input of its own.
       - When `timeout is not None`, append `max(0.0, timeout - elapsed)` to `deadlines`.
       - When `monitor is not None`, append `max(0.0, next_monitor_at - time.monotonic())` to `deadlines`.
       - Set `wait_timeout = min(deadlines) if deadlines else None` (no `1.0s` fallback cap when `deadlines` is empty; `wait_and_drain(None)` blocks in `selector.select(None)` or `WaitForMultipleObjects(..., INFINITE)` until pipe readiness, EOF, or child exit).
     - Call `io_session.wait_and_drain(wait_timeout)` instead of `time.sleep(sleep_for)`.
     - On `cancel_requested`, `timeout`, `limit`, or normal exit (`process.poll() is not None`), call `io_session.finish_after_exit()`.
  5. **Update `tests/core/test_subprocess_runner.py` and run the measurement gate:**
     - Update `test_stream_reader_normalizes_crlf_split_across_chunks` to test through `_open_process_io` instead of `_PipeAdapter`. It currently builds an `os.pipe()`, which is synchronous on Windows and cannot feed the overlapped leaf; it is rewritten to spawn a trivial child through `open_supervised_process` that writes the raw bytes (`sys.stdout.buffer.write(raw)`), so the same test exercises both leaves.
     - Update `test_input_backpressure_cannot_block_supervision` so it works with selector-driven non-blocking `stdin` on POSIX as well as Windows.
     - Add a unit test `test_idle_subprocess_waits_on_readiness_without_polling` that passes a forwarding spy selector into `_PosixSelectorProcessIO` (on POSIX) while supervising a child that sleeps `0.2s` then prints `"ready\n"`, with oracles that are valid on every platform:
       1. The session's own `owned_threads()` inventory is empty on every platform; the global thread count is not used as an oracle.
       2. With `cancel_requested=None` and `monitor_class=None`, every recorded `select(timeout)` argument is `None` or the computed deadline, never a fixed interval; and during a controlled silent window (the child blocked on a barrier the test holds for `0.2s`) the number of wakes is **zero**, which is the absence-of-idle-polling invariant and is deterministic.
       3. Eventual response to controlled events: after the test writes to the child's stdin, the output chunk is delivered; after the test releases the child to exit, completion is observed. Both are asserted as "happens", with a generous outer timeout as the failure bound, not as "within N wakes", because level-triggered readiness, partial reads, and simultaneous completions can legitimately produce extra wakes. Wake counts outside the silent window are recorded as diagnostics only. No assertion names a millisecond figure; latency numbers belong to the measurement gate.
     - **Existing tests coupled to removed or POSIX-only seams**, each with its disposition:
       - `test_interrupted_adapter_start_keeps_join_ownership` and the `owned_pipe_threads` fixture patch `subprocess_runner.threading.Thread` and assert two started threads. No pipe thread exists on any platform after Task 1, so both are replaced by a resource-rollback test for `_open_process_io` that injects a failure mid-initialization (after the first descriptor is registered) and asserts every registered descriptor, exit source, and selector is closed and the child reaped.
       - `test_stream_queue_drain_yields_with_continuing_production` calls `_drain_stream_queue` directly, which Task 1 removes. It is rewritten against `wait_and_drain` to prove the per-turn delivery cap (`SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS`) and the `turn_deadline` yield with a real producer.
       - `test_input_backpressure_cannot_block_supervision`, `test_partial_pipe_acquisition_or_read_failure_rolls_back`, `test_early_root_exit_does_not_turn_unread_input_into_failure`, `test_closed_input_einval_is_only_ignored_on_windows`, and `test_capture_join_reports_adapter_error_before_delivering_callbacks` inject faults through `subprocess_runner.os` (`os.read`, `os.write`, `os.set_blocking`). On POSIX `_PosixSelectorProcessIO` still uses those calls, so the injections stand. On Windows the leaf uses `_winapi.ReadFile`/`_winapi.WriteFile`, so each gains a Windows variant that injects through a `_winapi` forwarding shim (`OSError` with `winerror` `ERROR_BROKEN_PIPE` or `ERROR_NO_DATA`, a failing `GetOverlappedResult`), selected by `sys.platform`; the EINVAL case becomes "`ERROR_BROKEN_PIPE`/`ERROR_NO_DATA` on write is closed-pipe on Windows" with the POSIX branch unchanged.
       - **Two single-thread ordering traps** in those tests, which passed only because `_PipeAdapter` ran I/O on other threads: (a) `test_early_root_exit_does_not_turn_unread_input_into_failure` calls `process.wait()` before supervising, so the child has exited before the first turn; the initial write pass at session construction (above) is what still attempts the write and observes the closed pipe. (b) `test_capture_join_reports_adapter_error_before_delivering_callbacks` makes its injected `os.read` wait on an `accepted_exit` event that is set from `process.poll()`; on a single supervisor thread an `os.read` issued before that poll would wait on itself for five seconds. The test is rewritten so the injected failure fires only when `accepted_exit.is_set()` is already true, and its stderr assertion relies on the capture-phase rule that all remaining open streams are drained and endpoints closed before a capture-phase read error is raised.
       - The 21 bare `subprocess.Popen(` sites in this file, and the three in `tests/tasks/test_runner.py` that feed `run_monitored_subprocess`, call `open_supervised_process(...)` directly in place of `subprocess.Popen(...)`.
     - Adjust `test_terminal_capture_delivers_backlog_after_capture_budget` to the production fairness budget: with `SUBPROCESS_STREAM_READ_SIZE` shrunk to `1024`, a child writing 128 KiB into a 64 KiB pipe against a 15 ms sink is legitimately backpressured and reaches its marker later. The test waits for the marker without a timing assumption, and the barrier remains the proof. Production buffering is not enlarged to keep this fixture's old timing.
     - Add `test_root_exit_with_inherited_pipe_and_no_deadlines_completes_within_drain_budget`: a root that spawns a backgrounded grandchild holding `stdout` open, then exits, supervised with `cancel_requested=None`, `timeout=None`, `monitor_class=None`. Assert the outcome is `ok` and returns within `SUBPROCESS_STREAM_DRAIN_TIMEOUT` plus a small margin, and that the grandchild is still alive afterwards. This is the RED for the exit source: without it the wait is `select(None)` with no EOF and never returns.
     - Add three state-machine tests required by Review Pass 6, each run on both leaves: (a) a child that writes once and then blocks until a release the output callback issues, supervised with no deadlines, proving queued delivery never waits behind a full-timeout wait; (b) a partially delivered backlog (48 KiB pending) followed by more output, proving no request carries in-flight bytes past `SUBPROCESS_STREAM_TURN_READ_BYTES`; (c) output buffered before root exit with an inherited writer still open and enough output to require multiple reads, proving terminal capture collects everything the kernel had before the zero-peek terminator and loses nothing to the cancellation race, including the case where data lands between the cancel request and its completion.
     - **Measurement gate:** Measure before/after for a 3-second idle child (`python -c "import time; time.sleep(3)"`), output-to-callback latency, and cancel latency, and record the exact table in §12 of this plan for Linux, macOS, and Windows. CPU time and context switches come from `psutil.Process()` (`cpu_times().user + .system`, `num_ctx_switches().voluntary + .involuntary`), which is already a dependency and works on all three platforms; `resource.getrusage` is POSIX-only and is not used. Latencies are wall-clock `time.monotonic()` differences between the controlled event and its observation. (Baseline before Task 1 on macOS: ~35 ms parent CPU and ~890 context switches per 3 s idle child.)
- **Constraints:**
  - Do not add third-party dependencies.
  - Keep `run_monitored_subprocess` complexity within its existing `C901` budget (`RUFF-SUP-036`) or lower. Retire `RUFF-SUP-354` with `_PipeAdapter._run` (reconciled in Task 3) unless a worker-thread boundary remains; the overlapped Windows leaf has none.
- **Stop if:** Any existing test in `tests/core/test_subprocess_runner.py` or `tests/tasks/test_runner.py` requires mocking away real OS pipes to pass.
- **Verify with:**
  - `./.venv/bin/pytest tests/core/test_subprocess_runner.py tests/tasks/test_runner.py tests/tasks/test_consumer_terminal_events.py -q`

---

### Task 2: Fix `bin/pytest-pg` readiness loop and remove the 96-second negative preflight test

- **Outcome:** `bin/pytest-pg::_wait_for_postgres` uses `time.monotonic()` and a named 200 ms poll constant (`POSTGRES_READY_POLL_INTERVAL_SECONDS = 0.2`) with documented battle scars; `tests/system/test_pytest_pg_script.py` runs in seconds instead of 96+ seconds on PostgreSQL and does not pin `SimpleNamespace` over the `time` module.
- **Files to touch:**
  - `docs/specifications/08-Testing_Strategy.md`
  - `bin/pytest-pg`
  - `tests/system/test_pytest_pg_script.py`
- **Read first:**
  - `docs/specifications/08-Testing_Strategy.md` `[TS-0]` (lines 50–75)
  - `bin/pytest-pg` (lines 1–280)
  - `tests/system/test_pytest_pg_script.py` (lines 780–946)
- **Implementation details:**
  1. **Apply the `[TS-0]` spec delta** in `docs/specifications/08-Testing_Strategy.md` (from §5c above) and add a backlink to this plan under `## Related Plans`.
  2. **In `bin/pytest-pg`:**
     - Add a named module constant with battle-scar docstring right after `PYTEST_WAIT_POLL_SECONDS` (line 38):
       ```python
       POSTGRES_READY_POLL_INTERVAL_SECONDS: Final[float] = 0.2
       """Poll interval while waiting for the disposable PostgreSQL container.

       Docker has no synchronous readiness event for `docker port` host-port
       publication after `docker run -d`, and the official postgres:18 image
       restarts after running docker-entrypoint-initdb.d on a temporary
       Unix-socket-only server before opening loopback TCP connections.
       """
       ```
     - In `_wait_for_postgres`: replace `deadline = time.time() + timeout_seconds` and `while time.time() < deadline:` with `deadline = time.monotonic() + timeout_seconds` and `while time.monotonic() < deadline:`, and replace `time.sleep(1)` with `time.sleep(POSTGRES_READY_POLL_INTERVAL_SECONDS)`.
  3. **In `tests/system/test_pytest_pg_script.py`:**
     - Delete `test_schema_preflight_requires_real_backend_operation` (lines 932–946). Keep `test_postgres_readiness_rejects_bootstrap_then_initializes_schema` (lines 833–930), which already proves real PostgreSQL schema creation (`SELECT count(*) FROM simplebroker_pg_v1.meta` == `"1"`).
     - In `test_wait_for_postgres_requires_published_port_and_successful_tcp_query` (lines 786–830): patch only `monkeypatch.setattr(runtime.time, "sleep", lambda _seconds: None)` instead of replacing `runtime.time` with `types.SimpleNamespace(time=..., sleep=...)`; the existing `subprocess.run` and `_docker_port` patches stay as they are, and `time.monotonic` is left real.
- **Verify with:**
  - `./.venv/bin/pytest tests/system/test_pytest_pg_script.py -q`
  - `./bin/pytest-pg tests/system/test_pytest_pg_script.py` (the wrapper accepts pytest target paths only; it adds its own pytest options, so `-q` and other flags are rejected by its argument parser)

---

### Task 3: Retire skipped benchmark harness, replace manual `record_phase` try/finally with `phase()` context manager, and reconcile Ruff suppression registry

- **Outcome:** The 1,722-line always-skipped `tests/system/test_manager_dispatch_costs.py` is deleted; `tests/helpers/run_diagnostics.py` retains `_RetainedOutput` and adds the `phase(config, name, **details)` context manager; `tests/cli/test_cli_run_installed_entrypoint.py` uses `with phase(config, ...):` cleanly while preserving `test_installed_console_init_failure_does_not_materialize_harness_context`; and `docs/ruff-suppression-registry.md` + `tests/specs/test_ruff_policy.py` match the live codebase.
- **Files to touch:**
  - `tests/system/test_manager_dispatch_costs.py` (**delete**)
  - `tests/helpers/run_diagnostics.py`
  - `tests/cli/test_cli_run_installed_entrypoint.py`
  - `tests/system/test_run_diagnostics.py`
  - `docs/ruff-suppression-registry.md`
  - `tests/specs/test_ruff_policy.py`
- **Read first:**
  - `tests/cli/test_cli_run_installed_entrypoint.py`
  - `tests/helpers/run_diagnostics.py`
  - `tests/system/test_run_diagnostics.py`
  - `docs/ruff-suppression-registry.md` (lines 190–220)
  - `tests/specs/test_ruff_policy.py` (lines 60–155)
- **Implementation details:**
  1. **Delete `tests/system/test_manager_dispatch_costs.py`.**
  2. **Add `@contextmanager def phase(config: pytest.Config, name: str, **details: object) -> Iterator[None]` to `tests/helpers/run_diagnostics.py`:**
     - Keep `_RetainedOutput` and all existing hooks in `tests/helpers/run_diagnostics.py` intact.
     - Add:
       ```python
       @contextmanager
       def phase(config: pytest.Config, name: str, **details: object) -> Iterator[None]:
           """Record `{name}:start` and `{name}:done` with elapsed time and error type."""
           record_phase(config, f"{name}:start", **details)
           started = time.monotonic()
           try:
               yield
           finally:
               exc = sys.exception()
               record_phase(
                   config,
                   f"{name}:done",
                   elapsed=round(time.monotonic() - started, 3),
                   error=None if exc is None else type(exc).__name__,
                   **details,
               )
       ```
     - `record_phase` keeps its `(config, phase, **details)` signature; the helper only wraps it. Callers pass the command's argv and cwd as details so the Windows worker-timeout investigation keeps its evidence, and on the failure branch of `_run_console` the `TimeoutExpired.output` tail is recorded as a detail.
     - Add a brief assertion for `with phase(config, "step", argv=[...]):` in `tests/system/test_run_diagnostics.py::test_run_diagnostics_records_phases_without_replacing_failure`, checking that `step:done` carries `elapsed` and, on a raising block, `error`.
  3. **Simplify `tests/cli/test_cli_run_installed_entrypoint.py`:**
     - Replace `_record_command_phase` and the four nested `record_phase("...:start")` / `try...finally: record_phase("...:done")` wrappers around `with harness:` with `with phase(config, ...):`, while keeping the pre-enter cleanup block (`try: harness = WeftTestHarness() ... except BaseException:`) and `test_installed_console_init_failure_does_not_materialize_harness_context` intact.
  4. **Reconcile Ruff suppression registry (`[TS-3.1]`):**
     - Remove `RUFF-SUP-379` and `RUFF-SUP-380` (the deleted harness) and `RUFF-SUP-354` (the deleted `_PipeAdapter._run`) from the narrative table in `docs/ruff-suppression-registry.md` and from `EXPECTED_GROUP_IDS`, `EXPECTED_GROUP_COUNT` (`198` -> `195`), and `EXPECTED_DIRECTIVE_COUNT` (`329` -> `325`) in `tests/specs/test_ruff_policy.py`.
     - Run `./.venv/bin/python bin/ruff_suppression_index.py --write` so the generated index block in `docs/ruff-suppression-registry.md` is regenerated deterministically. The `Global raw-noqa inventory:` line is human-approved and is not rewritten by the tool: lower `BLE001` by the four retired directives (`136` -> `132`) by hand, then confirm with `--check`. (Corrected during execution; the original text claimed `--write` regenerates that line.)
- **Verify with:**
  - `./.venv/bin/pytest tests/cli/test_cli_run_installed_entrypoint.py tests/system/test_run_diagnostics.py tests/specs/test_ruff_policy.py -q`

---

### Task 4: Condense bloated plan transcripts, reconcile plan claims, and close plan statuses

- **Outcome:** Both branch plans (`2026-10-22-bounded-concurrent-manager-launches.md` and `2026-10-27-runtime-and-test-repair-plan.md`) have clean working trees and concise, high-signal review/execution summaries instead of thousands of lines of raw chat transcripts; the false fast-fail preflight claim is corrected; and all three completed plans are marked `completed` in their headers and in `docs/plans/README.md` per `[DOM-15]`.
- **Files to touch:**
  - `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md`
  - `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`
  - `docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md`
  - `docs/plans/README.md`
- **Read first:**
  - `docs/plans/README.md`
  - `tests/specs/test_plan_metadata.py`
  - `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md`
  - `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`
- **Implementation details:**
  1. In `docs/plans/2026-10-27-runtime-and-test-repair-plan.md`:
     - Replace the inaccurate claim that host forwarding or auth failures *"surface immediately in the existing preflight"* with the accurate behavior: `_initialize_broker_schema` surfaces host/auth failures before pytest starts, after `simplebroker`'s 3-attempt connection retry against `simplebroker-pg`'s 30s pool checkout timeout (~96s), and note that `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` replaced the 100 Hz `_PipeAdapter` loop with `_open_process_io` and removed the 96s negative test.
     - Condense the duplicate raw verbatim subagent transcript blocks and space-stripped terminal paste lines into concise reviewer metadata, findings/disposition tables, and verification command summaries (`writing-plans.md` §8).
     - Set `Status: completed` in the metadata block.
  2. In `docs/plans/2026-10-22-bounded-concurrent-manager-launches.md`:
     - Condense the duplicate raw verbatim subagent transcript blocks into concise reviewer metadata, findings/disposition tables, and benchmark summary tables (noting that the temporary 1,722-line `tests/system/test_manager_dispatch_costs.py` harness was retired by `2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` after Arm B was selected and C1/C2 were rejected, with the full harness recoverable at commit `4a98f26b`).
     - Set `Status: completed` in the metadata block.
  3. In `docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md` and `docs/plans/README.md`:
     - Once Tasks 1–3 and verification gates pass, set `Status: completed` in this plan's header and update `docs/plans/README.md` so all three plans have `| \`completed\` |` in the index table and the plan file count in `docs/plans/README.md` line 6 is accurate.
- **Verify with:**
  - `./.venv/bin/pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q`
  - `./.venv/bin/python bin/check-doc-paths`
  - `./.venv/bin/python bin/check-dom15-fixtures`
  - `./.venv/bin/python bin/coalesce-check`

---

## 8. Testing Plan

- **Harnesses and fixtures:**
  - Use real `subprocess.Popen` processes and OS pipes in `tests/core/test_subprocess_runner.py` and `WeftTestHarness` in `tests/tasks/test_runner.py` and `tests/tasks/test_consumer_terminal_events.py`.
  - **What must NOT be mocked:** Do not mock `subprocess.Popen` pipes for the core lifecycle, backpressure, multibyte UTF-8 split, inherited grandchild pipe, or slow-sink backlog tests. In `test_idle_subprocess_waits_on_readiness_without_polling`, wrap the real `selectors.DefaultSelector()` in a forwarding spy selector that delegates to the real OS selector while recording `select(timeout)` calls. Only fault-injection tests (such as simulating `OSError(errno.EINVAL)` on non-Windows or mid-read `OSError` after exit) may monkeypatch `os.read` / `os.write`.
- **Key observable proofs:**
  1. `test_idle_subprocess_waits_on_readiness_without_polling` (new in `tests/core/test_subprocess_runner.py`): proves via a forwarding selector and the session's owned-thread inventory that supervising a silent child creates no helper threads, passes only `None` or the computed deadline to `select`, performs zero wakes during a held silent window, and eventually responds to controlled stdin and exit events; wake counts outside the silent window are diagnostics. A Windows counterpart proves the same through a forwarding `WaitForMultipleObjects` wrapper and the Windows-only held-before-first-write cancellation case.
  1a. `test_root_exit_with_inherited_pipe_and_no_deadlines_completes_within_drain_budget` (new): proves child exit is an arbiter input by completing a no-deadline supervision whose root exits while a grandchild keeps `stdout` open, within the drain budget, without killing the grandchild.
  2. Existing `tests/core/test_subprocess_runner.py` suite: proves multibyte UTF-8 + CRLF split across chunk boundaries, 8 MiB `stdin` backpressure under timeout/cancel, inherited grandchild pipe drain (`flood=False` completing without sleeping out the 250 ms budget, asserted as completion well inside the test's outer timeout rather than as a millisecond figure, and `flood=True` bounded by `SUBPROCESS_STREAM_DRAIN_TIMEOUT = 0.25`), and 128 KiB slow-sink callback backlog delivery across `ok`, `error`, `cancelled`, `timeout`, and `limit`.
  3. `tests/system/test_pytest_pg_script.py`: proves `_wait_for_postgres` requires both published host port (`docker port`) and TCP loopback query using `time.monotonic()`, and (when run under `./bin/pytest-pg`) `test_postgres_readiness_rejects_bootstrap_then_initializes_schema` proves `_initialize_broker_schema` creates the real `simplebroker_pg_v1.meta` row in PostgreSQL without the 96-second negative timeout test.

---

## 9. Verification and Gates

### Per-task verification

```bash
# Task 1
./.venv/bin/pytest tests/core/test_subprocess_runner.py tests/tasks/test_runner.py tests/tasks/test_consumer_terminal_events.py -q

# Task 2
./.venv/bin/pytest tests/system/test_pytest_pg_script.py -q
./bin/pytest-pg tests/system/test_pytest_pg_script.py

# Task 3
./.venv/bin/pytest tests/cli/test_cli_run_installed_entrypoint.py tests/system/test_run_diagnostics.py tests/specs/test_ruff_policy.py -q

# Task 4
./.venv/bin/pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -q
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/check-dom15-fixtures
./.venv/bin/python bin/coalesce-check
```

### Final gates before completion

```bash
./.venv/bin/ruff check .
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/pytest -m "not slow" -q
./bin/pytest-pg --all
```

Exact-head CI is a required gate, not a courtesy: all 27 OS/Python matrix jobs including every Windows job, the three PostgreSQL slices, lint, coverage, and the four dependent extension jobs must complete with success. The nine required Windows adapter cases must appear exactly once and pass in retained JUnit evidence for each Windows Python version; a skipped, failed, errored, missing, or duplicate required case fails this gate. The measurement table from Task 1 is recorded for Linux, macOS, and Windows before the plan is closed.

---

## 10. Out of Scope

- Changing `weft/core/family_scheduling.py`, `weft/core/manager.py` (including `_dispatch_cleanup_scope`), or `weft/core/tasks/consumer.py` (those repairs from PR #48 are sound and remain untouched).
- Changing the public `RunnerPlugin.run_with_hooks` signature (`weft/ext.py`, `01-Core_Components.md` `[CC-3.1]`) from `cancel_requested: Callable[[], bool] | None` to a waitable cancellation event/descriptor across `Consumer`, `HostTaskRunner`, `DockerRunner`, `MacOSSandboxRunner`, and `MicrosandboxRunner` (named as the ideal under `[WT-2]` / `[WT-7]`, deferred to a dedicated runner-plugin contract slice).
- Modifying `simplebroker` or `simplebroker-pg` connection pool retry/timeout defaults (owned by the upstream SimpleBroker repository, not Weft).
- Interactive `CommandSession` in `weft/core/runners/host.py` (lines 1096–1128): unlike `_PipeAdapter`, `CommandSession` already uses blocking `stream.readline()` source-adapter threads that wait at 0 Hz when idle and call `on_activity()` when a line arrives. Post-exit inherited-pipe teardown for interactive `CommandSession` is a separate follow-on if ever needed.
- Adding `asyncio` or third-party event loops to `weft/core/runners/subprocess_runner.py`.

---

## 11. Fresh-Eyes Self-Review & Independent Review Loop

### Fresh-Eyes Self-Review (Completed 2026-10-29)

1. **Slow-callback interaction with kernel pipe buffer capacity (`test_terminal_capture_delivers_backlog_after_capture_budget`):**
   - *Check:* In `test_terminal_capture_delivers_backlog_after_capture_budget`, the child writes `128 KiB` (`128 × 1024` bytes) before touching `ready`, and `slow_sink` sleeps `15 ms` per 1 KiB chunk. Since OS pipe buffers are typically `64 KiB`, if `_PosixSelectorProcessIO.wait_and_drain` only read 16 KiB per turn when `SUBPROCESS_STREAM_READ_SIZE` is monkeypatched to `1024`, draining the first 64 KiB would take `4 × 16 × 15 ms = 960 ms` before the child could unblock and touch `ready`.
   - *Resolution in Task 1 (revised in Review Pass 4):* the read budget is the production fairness rule `SUBPROCESS_STREAM_TURN_READ_BYTES` (one kernel pipe buffer per stream per turn), not a figure derived from this fixture. Under that rule a child writing 128 KiB against a 15 ms sink is backpressured and reaches its marker later, which is correct behavior; the test waits on the marker without assuming the old timing. Production buffering is not enlarged to preserve a fixture's schedule.
2. **Ruff suppression registry coupling (`tests/specs/test_ruff_policy.py`):**
   - *Check:* Deleting `tests/system/test_manager_dispatch_costs.py` removes `RUFF-SUP-379` and `RUFF-SUP-380`, and deleting `_PipeAdapter._run` removes `RUFF-SUP-354` (one directive at `subprocess_runner.py:535`); the overlapped Windows leaf has no worker-thread boundary, so no replacement suppression is registered.
   - *Resolution in Task 3:* Explicitly listed the exact `RUFF-SUP-*` IDs, counts (`198` -> `195` groups, `329` -> `325` directives), files, and generator command.

---

## 12. Review Loop Log

### Review Pass 1 (Cross-Agent Plan Review — 2026-10-29)

- **Reviewer:** External agent review provided by repository owner
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | Plan quoted strings/section names not present in `docs/program-theory.md` and omitted `[WT-2]` item 4, `[WT-4]` §4.3, `[WT-7]`, and `[ALT-WT-002]` | **Accepted** | Grounded the plan in `[WT-2]` item 4, `[WT-4]` §4.3–§4.4, `[ALT-WT-002]`, `[ALT-WT-021]`, and `[WT-7]`. |
| 2 | Section 2.2 / Task 2 misnamed the positive test (`test_postgres_readiness_rejects_bootstrap_then_initializes_schema`) and helper (`_initialize_broker_schema`) | **Accepted** | Updated §2.2, §5c, Task 2, and §8 to cite `test_postgres_readiness_rejects_bootstrap_then_initializes_schema` and `_initialize_broker_schema`. |
| 3 | `[TS-0]` spec delta and Task 2 docstring said `docker compose port` and `postgres:17` instead of `docker port` and `postgres:18` | **Accepted** | Corrected to `docker port` and `postgres:18` in §2.2, §5c, and Task 2. |
| 4 | Constants misquoted as `32` and `0.2` instead of `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS = 16` and `SUBPROCESS_STREAM_DRAIN_TIMEOUT = 0.25` | **Accepted** | Corrected throughout §2.1, §4, §5, and Task 1. |
| 5 | Section 2.3 overstated `record_phase` calls as 296 lines and Task 3 risked removing `_RetainedOutput` (needed for xdist `pytest-timeout` dumps before `os._exit(1)`) and `test_installed_console_init_failure_does_not_materialize_harness_context` | **Accepted** | Retained `_RetainedOutput` in `tests/helpers/run_diagnostics.py` and `test_installed_console_init_failure_does_not_materialize_harness_context` in `tests/cli/test_cli_run_installed_entrypoint.py`; replaced manual `record_phase` `try/finally` blocks with `@contextmanager def phase(name: str)`. |
| 6 | Task 1 claimed "0 Hz when idle" without accounting for `ACTIVE_CONTROL_POLL_INTERVAL` (`50 ms` = 20 Hz) when `cancel_requested` is a pull predicate | **Accepted** | Separated `cancel_requested is None` (0 Hz extra wakeups; waits only on `timeout` and `monitor_interval`) from `cancel_requested is not None` (`ACTIVE_CONTROL_POLL_INTERVAL` = 20 Hz ceiling while `RunnerPlugin.run_with_hooks` passes `cancel_requested` as `Callable[[], bool]`, naming event-driven cancellation as the ideal). |
| 7 | Missing backpressure row in Difference Register, Windows handle details (`msvcrt.get_osfhandle`, `OpenThread`, `CancelSynchronousIo`), forwarding-selector test oracle, and before/after measurement gate | **Accepted** | Added all four to §5 Difference Register, Task 1, and §8. |
| 8 | Reviewer noted that condensing verbatim review records and flipping the two landed PR #48 plans to `completed` are owner decisions rather than automatic hygiene | **Accepted (owner authorized)** | Owner explicitly authorized Task 4; updated §12 to record that `writing-plans.md` §8 requires a disposition table (without forbidding verbatim records) and that condensation/closure here is owner-directed per `[DOM-15]`. |
| 9 | Document why `_dispatch_cleanup_scope` (`Arm B`) in `weft/core/manager.py` is preserved, note `CommandSession` in `host.py` as out-of-scope, note PR body `128 MiB` typo for the `128 KiB` test payload, and include `bin/pytest-pg --all` + `mypy ... tests` in final gates | **Accepted** | Added to §1, §2.3, §4, Task 4, §9, and §10. |

### Review Pass 2 (Follow-Up Grounding & Theory Accuracy Review — 2026-10-29)

- **Reviewer:** External agent review provided by repository owner
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | `SUBPROCESS_TERMINAL_CAPTURE_MAX_BYTES` does not exist in `weft/_constants.py`; the PR body's `128 MiB` claim was a typo for the `128 KiB` test payload in `test_terminal_capture_delivers_backlog_after_capture_budget`; and `docs/program-theory.md` `[WT-4]` §4.3 should not attribute a byte ceiling to the owner | **Accepted** | Removed all `SUBPROCESS_TERMINAL_CAPTURE_MAX_BYTES` and byte-ceiling references from `docs/program-theory.md` and the plan, and noted the `128 MiB` -> `128 KiB` test-payload typo in §1 item 4. |
| 2 | `[ALT-WT-021]` cited commit `4dc118b6` (which does not exist) instead of `4c490043` (where `_PipeAdapter` landed) and `540707a3` (measured commit), and `[WT-2]` item 4 listed `"un-notified control predicate"` as a legitimate ceiling in the theory | **Accepted** | Corrected `[ALT-WT-021]` evidence to commits `4c490043` and `540707a3`, and removed `"or un-notified control predicate"` from `[WT-2]` item 4 in `docs/program-theory.md`. |
| 3 | `_dispatch_cleanup_scope` preservation rationale cited non-existent `_spawn_child()` and `"~18%"` instead of `_process_manager_reactor_turn`, `_process_queue_message`, and `_handle_work_message` with `"no speed claim"` | **Accepted** | Corrected §2.3 and §4 to name `_process_manager_reactor_turn`, `_process_queue_message`, and `_handle_work_message` (`[MA-3]`, no speed claim) and noted the mode-flag vs. turn-fact trade-off. |
| 4 | Task 2 used `pytest ... --postgres` (not a flag) and referenced non-existent `POSTGRES_STARTUP_TIMEOUT_SECONDS` | **Accepted** | Replaced `pytest ... --postgres` with `./bin/pytest-pg tests/system/test_pytest_pg_script.py -q` and anchored `POSTGRES_READY_POLL_INTERVAL_SECONDS` right after `PYTEST_WAIT_POLL_SECONDS` (line 38 of `bin/pytest-pg`). |
| 5 | Task 1 included a `1.0s` "protective fallback" when `timeout`, `monitor`, and `cancel_requested` are `None`, which is an `[ALT-WT-002]` cap; and event-driven cancellation should be named as the ideal with its deferral reason recorded | **Accepted** | Removed the `1.0s` fallback (`wait_timeout` is `None` when `deadlines` is empty) and recorded event-driven cancellation across `RunnerPlugin.run_with_hooks` as the ideal with its cross-plugin deferral rationale in §2.1 and §10. |
| 6 | Review-log row 8 misstated the reviewer's finding on verbatim transcripts and plan closure | **Accepted** | Corrected Review Pass 1 row 8 above. |

### Review Pass 3 (Child-Exit Input and Reference Accuracy — 2026-10-29)

- **Reviewer:** External agent review provided by repository owner
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | With no deadlines armed, `select(None)` / `events.get(None)` waits for pipe readiness or EOF only. A root that exits while a backgrounded descendant holds `stdout`/`stderr` open produces no EOF, so the no-deadline wait never returns and `process.poll()` is never consulted. Child exit must be an arbiter input ([WT-2] item 4, [WT-3] source adapter, [ALT-WT-002]). | **Accepted** | Added the exit source to §2.1, Task 1 (POSIX: `os.pidfd_open` where available, otherwise one blocking `process.wait()` source adapter writing a wake pipe registered in the selector, the `CommandSession._wait_for_exit` shape; Windows: a `process.wait()` waiter posting `("exit", None)`), invariant 1a, a new difference-register row, and the RED test `test_root_exit_with_inherited_pipe_and_no_deadlines_completes_within_drain_budget`. Removed the "both output pipes hit EOF before exit" timer clause, which the exit source makes unnecessary. Adjusted "zero helper threads" to "no polling threads" wherever the exit-wait adapter may exist, including `[ALT-WT-021]` in `docs/program-theory.md`. |
| 2 | §10 cited `10-Builtin_and_Extension_Runners.md`, which does not exist; the runner plugin boundary is `[CC-3.1]` in `01-Core_Components.md`. | **Accepted** | Removed the nonexistent reference. |
| 3 | §5c's theory row listed `[WT-2]` item 4 and `[WT-4]` §4.4, but the approved edit touches `[WT-2]` item 4, a `[WT-3]` row, two `[WT-4]` §4.3 rows, and `[ALT-WT-021]`, and is already applied in the working tree; Task 1 step 1 risked applying it twice. | **Accepted** | Rewrote the §5c row to list the actual sections and mark the delta as already applied with owner approval; Task 1 step 1 now verifies and commits it rather than re-applying. |

### Review Pass 4 (Windows Protocol, Buffering Bound, Evidence Accuracy — 2026-10-29)

- **Reviewer:** External agent review provided by repository owner; dispositions verified against the tree before acceptance
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | Windows teardown as planned could hang: `PeekNamedPipe` on a handle another thread is reading may block, and `CancelSynchronousIo` followed by `join()` leaves a submission race (nothing to cancel, then a fresh blocking read). | **Accepted; design replaced** | The Windows leaf now uses overlapped I/O in the `asyncio.windows_utils` shape: the leaf creates overlapped pipe pairs through a new `open_supervised_process(...)` helper (used by host.py line 936 and the Docker plugin line 231), waits with `WaitForMultipleObjects` over the read/write completion events and the process handle, and cancels with `CancelIoEx`. Zero helper threads on Windows; no `PeekNamedPipe`, no `CancelSynchronousIo`, no thread-handle access. Recorded the rejected design in the class docstring and in `[ALT-WT-021]`. Added a Windows-only held-before-first-write cancellation test. |
| 2 | Buffering bound not defined across intermediate structures; on Windows the semaphore permit would be released on move into the deque, feeding an unbounded deque; exit/error events could be starved by data capacity. | **Accepted** | Invariant 1b: in-flight undelivered bytes per stream are bounded by `SUBPROCESS_STREAM_TURN_READ_BYTES`; capacity is released only on callback delivery; a stream at its bound is not read (POSIX unregisters `EVENT_READ`; Windows does not re-issue the overlapped read); exit/EOF/error are never data-gated; accumulated result strings are distinguished from in-flight buffering. |
| 3 | The `131072` read allowance was sized to the 128 KiB fixture, inverting the design relationship. | **Accepted** | Removed. Invariant 1c states the fairness budget as a production rule (one kernel pipe buffer per stream per turn, `SUBPROCESS_STREAM_TURN_READ_BYTES`); the fixture is adjusted to accept a backpressured child reaching its marker later. |
| 4 | "Exactly one selector wake" and global `threading.active_count()` are invalid oracles; "0 ms" / "< 1 ms" are aspirations, not portable guarantees. | **Accepted** | Oracles replaced: session-owned thread inventory, every `select` argument is `None` or the computed deadline, wakes ≤ distinct events + 1, response to controlled stdin and exit events within one wake. Millisecond figures moved out of assertions into the measurement gate and the difference register's wording. |
| 5 | The "128 MiB is a typo for 128 KiB" correction was wrong: the 2026-10-27 plan records an integrated Consumer-and-broker reproduction at 134,217,728 characters with retained JSON (`runtime-integrated-stream-flood-*-134217728.json`), separate from the 128 KiB controlled-sink regression. | **Accepted; reviewer error corrected** | §1 item 4 now keeps both records with their roles (128 KiB mutation proof; 128 MiB integration evidence). The Review Pass 1 row 9 and Review Pass 2 row 1 entries that called it a typo are superseded by this row and left in place as history. The error originated in a review that searched only the runner test file. |
| 6 | The harness does not simulate rejected arms; its docstring says the checkout selects production behavior and its wrappers observe real calls. | **Accepted** | §2.3 rewritten: retirement is because the experiment is complete, with the harness recoverable at `4a98f26b` and the 2026-10-22 plan's measurement limitations preserved; relocation under the `[TS-0]` benchmark convention named as the alternative. |
| 7 | The sample `phase(name)` helper called `record_phase` with one argument; the real signature is `record_phase(config, phase, **details)`. | **Accepted** | Helper is now `phase(config, name, **details)`, records `elapsed` and the error type name on exit, and callers pass argv/cwd; the console failure branch records the `TimeoutExpired.output` tail. |
| 8 | Classification is Class 5 (normative spec text changes); `bin/pytest-pg ... -q` fails argument parsing; final gates must require Windows adapter tests and the complete CI matrix. | **Accepted** | Header reclassified to Class 5 with the hardening checklist applying (execution-path trigger). Dropped `-q` from the `bin/pytest-pg` invocations. §9 now requires exact-head CI across all 27 matrix jobs including Windows, the three PostgreSQL slices, lint, coverage, and the four dependent jobs, with Windows adapter tests shown executed in the job logs and the measurement table recorded on all three platforms. |

### Review Pass 5 (CPython API Fidelity, Exit Source, Test Coupling — 2026-10-29)

- **Reviewers:** Two external agent reviews provided by repository owner; every disposition below was verified against CPython 3.14 sources shipped with the interpreter and against `tests/core/test_subprocess_runner.py` before acceptance
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | `_winapi.ReadFile(..., overlapped=True)` returns `(Overlapped, err)`; `GetOverlappedResult` returns `(count, err)` and bytes come from `ov.getbuffer()`; cancellation is `ov.cancel()`, not `_winapi.CancelIoEx(handle, overlapped)`; a cancel can race normal completion. Confirmed in `multiprocessing/connection.py`. | **Accepted** | §2.1 and Task 1 now state the exact calls, the `ERROR_IO_PENDING` / synchronous-completion split, byte collection via `getbuffer()`, `ov.cancel()` followed by `GetOverlappedResult(True)` with completed bytes preserved when `err != ERROR_OPERATION_ABORTED`, and byte-mode pipes (no `ERROR_MORE_DATA`). |
| 2 | The pipe recipe did not handle stdin orientation; `asyncio.windows_utils.Popen` uses `pipe(overlapped=(False, True), duplex=True)` for stdin. Rollback tests and the test path to overlapped handles were missing. | **Accepted** | Per-endpoint table (direction, ownership, close responsibility) matching `windows_utils.Popen`; rollback tests at pipe creation, process creation, and handle attachment; all 21 bare `Popen(` sites in the runner tests routed through `_supervise_real_process` → `open_supervised_process`. |
| 3 | Wait and terminal-capture protocol underspecified: wait set must hold only pending operations, retired events must leave, zero-timeout checks during terminal capture, bytes that won a cancel race must be collected. | **Accepted** | Per-endpoint `{idle, pending, eof, closed}` state, wait-set rebuilt per turn from pending operations plus the process handle, zero-timeout completion checks in `finish_after_exit()`, and cancel-race byte preservation. |
| 4 | Invariant 1b's unconditional in-flight bound conflicts with terminal capture; 64 KiB should be described as the chosen budget, not a kernel fact. | **Accepted** | Invariant 1b carries the terminal-phase exception (time-bounded, finite, delivered in full); invariant 1c calls 64 KiB the chosen fairness budget. |
| 5 | "Wakes ≤ distinct events + 1" and "within one wake" reject correct level-triggered or partial-read behavior. | **Accepted** | Oracles are now: zero wakes during a held silent window; every `select` argument is `None` or the computed deadline; eventual response to controlled events under a generous outer timeout; wake counts elsewhere are diagnostics. |
| 6 | `resource.getrusage` is unavailable on Windows; opening text still promised "0 ms" / "< 1 ms". | **Accepted** | Measurement uses `psutil.Process().cpu_times()` and `num_ctx_switches()` on all platforms; the "0 ms" and "< 1 ms" promises are replaced with "no timer between event and wakeup, measured not asserted". |
| 7 | The Pass 3 exit-wait thread is wrong: `Popen.wait()` holds `_waitpid_lock` across its blocking `waitpid` (subprocess.py lines 2081–2084) and `Popen.poll()` returns `None` while the lock is held (lines 2014–2017), so the thread would blind every supervisor poll and misclassify a child exiting at its timeout boundary. `select.kqueue` with `KQ_FILTER_PROC`/`KQ_NOTE_EXIT` is a pollable, thread-free exit source on macOS/BSD. Confirmed on this interpreter. | **Accepted; Pass 3 design corrected** | Exit source is a kernel object on every platform: pidfd (Linux), kqueue process filter registered by its own descriptor (macOS/BSD), process handle (Windows). Added the register-then-poll ordering rule and `ProcessLookupError` handling. Zero helper threads on all platforms; `[ALT-WT-021]` and every "exit-wait adapter" mention updated. |
| 8 | Five existing tests patch seams Task 1 removes or makes POSIX-only (`test_interrupted_adapter_start_keeps_join_ownership` + `owned_pipe_threads`, `test_stream_queue_drain_yields_with_continuing_production`, and four `os.*` fault-injection tests). | **Accepted** | Each named with its disposition in Task 1 step 5: thread-ownership test replaced by a mid-initialization rollback test; drain test rewritten against `wait_and_drain`; the four fault-injection tests keep their POSIX form and gain `_winapi` forwarding-shim variants selected by platform. (The feedback's `SUBPROCESS_STREAM_DRAIN_BUDGET_SECONDS` does not exist; the yield rule is `turn_deadline`.) |
| 9 | Unregister/re-register at the in-flight cap is churn; because the cap is reached only while `pending_chunks` is non-empty and the wait is then `select(0.0)`, skipping the read suffices. | **Accepted** | Streams at the cap stay registered and are simply not read that turn. |
| 10 | Stale-line claims: "no new constants" at line 89 and a `_cleanup_children_on_stop` parenthetical at line 132. | **Not applicable** | Neither string exists in the plan or the tree; both describe an earlier draft. The §5c theory description and the `bin/pytest-pg` unit-test note were refreshed as the same feedback asked. |

### Review Pass 6 (Windows State Machine, Construction Helper, Test Ordering — 2026-10-29)

- **Reviewers:** Two external agent reviews provided by repository owner; each disposition verified against CPython 3.14 sources shipped with the interpreter, `weft/core/runners/subprocess_runner.py`, `tests/core/test_subprocess_runner.py`, and `tests/tasks/test_runner.py` before acceptance. The first reviewer also withdrew their earlier assurance that sharing `Popen.wait()` and `poll()` across threads is safe, and reported testing the nested-kqueue exit watch on macOS (normal exit wakes the selector; registration after exit yields the `ESRCH` case).
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | A synchronously completed Windows read leaves bytes queued, yet the next wait uses the full timeout; a child that waits for a callback-driven release can hang indefinitely with no deadline. | **Accepted** | `wait_and_drain` on Windows is non-blocking (`timeout_ms = 0`) whenever either `pending_chunks` is non-empty, matching the POSIX `select(0.0)` rule; synchronous completions loop under the sizing rule. A write-once-then-wait-for-release child is a required test. |
| 2 | "Below 64 KiB" permits a further full 64 KiB read (48 KiB + 64 KiB). | **Accepted** | Both leaves size each request `min(read_size, remaining_in_flight_capacity, remaining_turn_budget)`; Windows permits one pending read per stream, sized against capacity at issuance; no second read can increase that capacity charge until retirement. A partially delivered backlog followed by more output is a required test. |
| 3 | `GetOverlappedResult` raises `OSError` for `ERROR_BROKEN_PIPE` rather than returning it; `ov.getbuffer()` returns `None` for writes, so a generic "collect `getbuffer()[:nread]` when cancel loses" rule fails on a completed stdin write. Consistent with `multiprocessing.connection._recv_bytes` and `_send_bytes`. | **Accepted** | Read and write retirement are specified separately; `OSError` with `winerror == ERROR_BROKEN_PIPE` is read EOF and `ERROR_BROKEN_PIPE`/`ERROR_NO_DATA` is closed stdin; completed writes advance the offset and never touch `getbuffer()`. |
| 4 | A zero-time wait returning `WAIT_TIMEOUT` proves only that the read has not completed, not that the pipe is empty; a cancel-lost read may leave further buffered bytes. | **Accepted** | The terminator is the kernel's own statement: capture of a stream ends only when `ov.cancel()` wins (`ERROR_OPERATION_ABORTED`), is EOF, or the budget expires; a cancel-lost completion captures its bytes and re-issues a read. The zero-time wait is only a pre-check. A Windows test with output buffered before root exit, an inherited writer still open, and enough output to need multiple reads is required. |
| 5 | `WaitForMultipleObjects` returns only the lowest signalled index; checking one index starves other handles. | **Accepted** | After the first signalled index the leaf re-waits the remaining handles with `timeout_ms = 0` until `WAIT_TIMEOUT`, the batched-wait pattern from `multiprocessing.connection`. |
| 6 | `asyncio.windows_utils.PipeHandle` has no `closed`, `encoding`, or `errors`; `subprocess_runner.py:436` and tests `:197`/`:901` read `stream.closed`. `windows_utils.Popen` wraps all three `pipe()` calls in one `try`, so a second-pipe failure leaks the first; `msvcrt.open_osfhandle` transfers handle ownership to the fd; the stdlib pipe buffer defaults to 8192. | **Accepted** | The helper wraps parent handles in `_OverlappedPipe` (`handle`, `fileno()`, `close()`, `closed`, `encoding`, `errors`), wraps each `pipe()` call individually, releases child ends through `os.close(fd)` and calls `CloseHandle` only when `open_osfhandle` failed, passes `bufsize=65536`, and turns the stdlib `assert`s into explicit checks. |
| 7 | Three `Popen` sites in `tests/tasks/test_runner.py` (lines 1464, 1538, 1615) feed `run_monitored_subprocess` and run on Windows CI; `_supervise_real_process` takes an existing process, so sites should call the helper directly; the CRLF test builds a synchronous `os.pipe()`. | **Accepted** | All 21 + 3 sites call `open_supervised_process(...)` in place of `subprocess.Popen(...)`; the CRLF test spawns a trivial child through the helper. |
| 8 | Two single-thread ordering traps: `test_early_root_exit_does_not_turn_unread_input_into_failure` waits for exit before supervising, so stdin would never be written; `test_capture_join_reports_adapter_error_before_delivering_callbacks` makes the injected `os.read` wait on a poll observation that the same thread must make. | **Accepted** | POSIX leaf performs an initial non-blocking write pass at construction and registers `EVENT_WRITE` only for the remainder; the capture-join test is rewritten to fire only after `accepted_exit.is_set()`, and `finish_after_exit()` drains remaining open streams and closes endpoints before raising a capture-phase read error. The fifth fault-injection test is now listed. |
| 9 | POSIX stdin wrote until would-block or completion with no per-turn budget; a fast reader of a large input could postpone output and supervision. | **Accepted** | Per-turn stdin write budget of `SUBPROCESS_STREAM_TURN_READ_BYTES` on both leaves. |
| 10 | §4 file list omitted host.py, the Docker plugin, `weft/helpers/__init__.py`, and `tests/tasks/test_runner.py`; invariant 5 and the register row still described Windows closed stdin as `errno.EINVAL` from `os.write`. | **Accepted** | File list completed; invariant 5 and the register row now describe the `_winapi` error surface and note that the MSVCRT `EINVAL` case no longer arises. |

### Review Pass 7 (Terminal-Capture Emptiness Observation — 2026-10-29)

- **Reviewer:** External agent review provided by repository owner; closes findings 1–3 of Pass 6 as resolved
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | "`ERROR_OPERATION_ABORTED` is the kernel's statement that no data was available" is an unsupported inference: cancellation settles the operation and does not change handle state, so data can be buffered after a cancel wins. Replacing "not signalled means empty" with "cancelled means empty" kept the proof gap. The earlier `PeekNamedPipe` objection concerned synchronous handles with another thread blocked reading, which this design has removed. | **Accepted** | Terminal capture now settles the pending read (cancel, collect if it completed, EOF on broken pipe) and then observes emptiness explicitly with `_winapi.PeekNamedPipe(handle, 0)` on the overlapped handle with no read outstanding; zero available bytes is the terminator, non-zero issues a read through the ordinary completion/error handling and repeats, all under the drain budget. §2.1, Task 1, and the difference register updated; required test (c) now names the land-after-cancel case. |
| 2 | The initial stdin pass at construction wrote until would-block or completion, bypassing the per-turn write budget just added for later turns. | **Accepted** | The construction pass applies the same `SUBPROCESS_STREAM_TURN_READ_BYTES` budget and registers the remainder for readiness. |

### Review Pass 8 (Owner Approval — 2026-10-29)

- **Reviewer:** Repository owner; plan receives a PASS and implementation is authorized with Opus implementers and a controller review.
- **Findings & Disposition:**

| # | Finding | Disposition | Action Taken |
|---|---------|-------------|--------------|
| 1 | After a positive `PeekNamedPipe` availability query, the follow-up read must still go through the existing completion/error handling; the plan must not assume it completes synchronously. | **Accepted** | §2.1, Task 1 `finish_after_exit()` step (3), and the Pass 7 action text now route the post-peek read through the ordinary `ReadFile` / `GetOverlappedResult` path (`err == 0`, `ERROR_IO_PENDING` with a bounded event wait, `ERROR_BROKEN_PIPE`). |

### Execution Record (2026-10-29)

Status: Tasks 1–4 implemented in the working tree; nothing committed or pushed. This plan stays `draft` until its §9 gate passes: exact-head CI with the Windows adapter tests shown executed and passed in retained Windows JUnit evidence, and the Linux and Windows measurement rows recorded.

#### Measurements

macOS rows measured with a scratch script on the current tree before any runner change ("Before") and after Task 1 ("After"). CPU and context switches come from `psutil.Process()` and cover the whole parent process; each idle figure is the median of 3 runs of `python -c "import time; time.sleep(3)"` with `timeout=None` and no monitor. Latencies are `time.monotonic()` differences, which share one clock across processes on macOS.

| Measurement (macOS, arm64, CPython 3.14.9) | Before (`_PipeAdapter`) | After (`_open_process_io`) |
|---|---|---|
| 3 s idle child, `cancel_requested=None`: parent CPU | 29.5 ms | 0.7 ms |
| 3 s idle child, `cancel_requested=None`: ctx switches | 725 | 1 |
| 3 s idle child, pull `cancel_requested` (20 Hz ceiling): parent CPU | 25.4 ms | 5.4 ms |
| 3 s idle child, pull `cancel_requested`: ctx switches | 761 | 83 |
| Output-to-callback latency (median / max, 10 samples) | 34.60 / 58.23 ms | 0.22 / 0.46 ms |
| Cancel latency, including terminate and reap (median / max, 5 samples) | 40.49 / 46.34 ms | 14.14 / 17.75 ms |
| Linux rows (all six measurements) | pending exact-head CI | pending exact-head CI |
| Windows rows (all six measurements) | pending exact-head CI | pending exact-head CI |

#### Execution notes

- The Windows leaf (`_WindowsOverlappedProcessIO`) and the Windows-only tests are written to the Task 1 brief and pass `mypy --platform win32`, but they have not executed anywhere; they remain unverified until Windows CI shows them run.
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py` was also routed through `open_supervised_process(...)`. The §4 file list named only `weft/core/runners/host.py` and the Docker plugin, but the plan's rule is "every supervised one-shot command", and the macOS sandbox plugin is one.
- Deviation (a repo test outranks plan text): Task 1 step 2 said `weft/helpers/__init__.py` keeps a local named constant once `SUBPROCESS_POLL_INTERVAL_FLOOR` is removed, but `tests/system/test_constants.py::test_production_constants_live_in_constants_module` rejects module-level constants outside `weft/_constants.py`. The constant is therefore `VERIFIED_PROCESS_EXIT_POLL_INTERVAL: Final[float] = 0.01` in `weft/_constants.py`, with the same explanatory docstring, imported by `weft/helpers/__init__.py`; the same gate also required allowlisting the runner's `_CHILD_EXIT` wait-set sentinel in that test's `_RUNTIME_OBJECT_ALLOWLIST`.
- The Ruff `Global raw-noqa inventory` line in `docs/ruff-suppression-registry.md` is human-approved text and was hand-edited (`BLE001` 136 -> 132); this is already reflected in the Task 3 text.
- Task 4 condensed the two branch plans under `writing-plans.md` §8 (uncondensed text recoverable with `git show 540707a3:<path>`), corrected the "surfaces immediately" preflight claim in the 2026-10-27 plan, and closed both plans and their index rows as `completed`.

#### Owner decision (2026-10-30)

The owner selected Linux5.3+ and usable pidfd support as a requirement. Missing
Python support, ENOSYS, EPERM or EACCES receives a clear RuntimeError preserving
the cause. No polling fallback is permitted; ESRCH remains the exited-child case.


#### Gates passed locally

- `./.venv/bin/ruff check .` and `./.venv/bin/ruff format --check .`
- Full mypy (the §9 command over `weft tests bin` and the integration/extension packages), plus `mypy --platform win32` on the runner, its tests and the Docker plugin
- The per-task pytest gates in §9 for Tasks 1–3, and the Task 4 documentation gates
- `./.venv/bin/python bin/ruff_suppression_index.py --check` and `./.venv/bin/python bin/check-doc-paths`
- `./bin/pytest-pg tests/system/test_pytest_pg_script.py`: 35 passed, 13 s wall (previously 96+ s, because of the removed negative test)

- `./.venv/bin/pytest -m "not slow" -q` on the final tree: 5991 passed, 47 skipped, 0 failed (xdist, local worker cap). An earlier run of the same suite on the pre-fix tree failed only `tests/system/test_constants.py` (the constants-placement deviation above); the fix is included in this result.
- `./bin/pytest-pg --all`: 4,026 passed, 24 skipped, 1 failed in 325 s. The one failure was `tests/specs/test_plan_metadata.py::test_plan_readme_statuses_match_plan_metadata`, caught while Task 4 was mid-edit (plan header flipped before its README row); the Task 4 documentation gates passed afterwards and no runtime test failed under PostgreSQL.
- Extension suites on the final tree: Docker, microsandbox, and macOS sandbox (whose two construction tests now patch `plugin.open_supervised_process` instead of `subprocess.Popen`) all pass.

Still open: commit, push, and the exact-head CI gate (all Windows jobs with the adapter tests executed, the three PostgreSQL slices, lint, coverage, the four dependent extension jobs), plus the Linux and Windows measurement rows.


### Review-repair amendment (2026-10-30, before implementation)

This Class5 slice implements [RM-5.2] in `docs/specifications/06-Resource_Management.md`, [TS-0] in `08-Testing_Strategy.md`, and the existing ALT-WT-021 reactor choice. Owner authorization keeps Linux5.3+ usable pidfd support mandatory; no timer fallback, helper thread, or concurrent launch workers.

Confirmed diagnosis and selected changes:

1. **Decoder accounting and bounded progress.** Actual `_OutputStreamState` probe: split UTF8 `abc` then incomplete byte `e2` feeds4bytes but charges3, then delivery charges0 while decoder retains1. UTF7 plus262143 shift bytes feeds262144 while charges0 and decoder retains262144. Charge every accepted byte, retain raw decoder-buffer credits across delivery, and transfer only consumed credits to emitted chunks. Use documented decoder.getstate()[0] for actual retained raw bytes. Bytes consumed without text (including errors=ignore) are released immediately; no separate carry counter. IncrementalNewlineDecoder's fixed one-character CR state remains constant-size codec metadata; its raw consumed input may be charged to the decoded chunk, but partial raw multibyte input remains charged until decoded. EOF transfers remaining credits and preserves the ordinary decode/error policy. Do not use re-encoding as a byte accounting oracle.

   Merely counting bytes would deadlock a valid UTF7 shift longer than64KiB: the decoder needs additional input while the pipe cannot be read. Introduce an explicit resource policy: if decoder-retained raw input reaches the existing64KiB stream bound regardless of pending decoded output, fail with a clear UnicodeError identifying the incremental-decoder buffer limit. Never silently replace/reset/truncate, grow unbounded lookahead, or wait forever. Check this after each accept including terminal capture, so a long sequence cannot bypass the cap after root exit. It is deliberately a bounded-decoding limit: a syntactically valid stateful sequence larger than the limit is rejected. Normal text, arbitrary chunk splits, UTF8 replacement/strict errors, CRLF translation, and all already-captured delivery semantics remain.

   TDD: extend owner-state table using real pipe/TextIOWrapper and codec inputs (split UTF8, CRLF, EOF partial bytes); prove byte credits and final text against independent literals. Real supervised child UTF7 large shifted sequence must fail through cleanup with explicit decoder limit, bounded reads, and all owned pipes/process released; small validUTF7 and large ordinaryUTF8 are positive controls. No timeout relaxation or codec whitelist.

2. **Exit source safety and Linux capability.** Inspect cached `process.returncode` before opening an exit source: already-reaped children need no PID lookup. Do not call poll before registering for a live child; preserve the existing register-then-supervisor-poll ordering. Linux missing `pidfd_open`, ENOSYS, EPERM/EACCES capability denial must produce a RuntimeError requirement error mentioning Linux5.3+ and allowed pidfd_open, chained from the real cause. ESRCH retains exited-child semantics; other resource errors remain their original errors, not capability misdiagnoses. Update spec/user-facing platform requirement. Test real pre-reaped child with exit-source acquisition forbidden, live child ordering, and actual owner opening with narrow syscall fault injection for denial/unsupported errno. No all-OSError fallback.

3. **Windows oracle repairs.** `_ConstructionFaults.open_osfhandle` calls its installed module hook recursively; save the real function before hook installation and call it. Existing real Windows acquisition rollback table must execute on CI. Add forwarding WaitForMultipleObjects observation to existing held-silent-child test so Windows asserts zero completed waits during silence and only infinite/zero active-phase waits without deadlines. Observe terminal transition separately; finite bounded capture waits are legitimate. Keep real kernel calls and existing barrier cleanup; don't replace the readiness owner. Final Windows logs must demonstrate these cases ran.

4. **Pre-supervisor process ownership.** Docker command startup creates a process before SDK acquisition; acquisition itself sits outside cleanup and startup failure currently leaves all3 pipe wrappers open even when process already exited. Acquire the Docker client before spawning, so acquisition failure cannot leave either a process or a newly created container; retain container removal inside client lifetime with nested finally ensuring removal even when process cleanup raises, and close all owned endpoints in cleanup finally even for an exited process/kill failure. Test actual supervised subprocess via patched creation factory and injected SDK/startup failures, asserting no spawn on client-acquisition failure and process reaped/pipes closed on later failures; update both tests patching subprocess.Popen to patch open_supervised_process. Host handle acquisition also needs rollback before supervisor ownership: exercise actual process with injected handle lookup failure and preserve original exception. Extract the existing shared pipe-close loop for reuse. Preserve Docker kill/wait exception rules; Host reuses existing execution-failure cleanup plus shared pipe closure.

5. **Windows refill and dead field dispositions.** Existing wait_and_drain subtracts bytes retired from its64KiB budget before post_reads; the review's claim of an extra full64KiB in the same turn is not supported. Keep finite cooperative turn behavior; verify with existing/new platform budget oracle where needed, not a speculative reorder. Remove `_OverlappedOp.reserved` only after full-tree search confirms no reader; single pending operation plus sized issuance already owns capacity.

Sequence: independent cross-family plan review and dispositions; focused intended RED before production edits; narrow repairs and restored GREEN/mutations; native fresh-eyes plus cross-family implementation review; full SQLite and PostgreSQL, statics/suppression/docs gates; commit/push PR48, exact-head all36 CI jobs and Windows firing evidence. Complete Linux/Windows measurement rows using the approved measurement procedure, without making speed claims from correctness tests. No checkout edits during tests. Preserve all existing uncommitted implementation; rollback only exact saved owner bytes for one mutation at a time. No merge.

Cross-family amendment review PASS397.22s. Accepted P2-1 throughP2-6 in the operative amendment above, with buffer-only accounting, terminal bound enforcement, acquire-client-before-spawn, shared pipe-close only, active-only Windows wait oracle, and atomic spec/plan failure-mode wording. Native fresh-eyes additionally required cachedreturncode assertions before poll; accepted. Local qualifiedRED logs are reactor-repair-red-valid.log,reactor-acquisition-red.log,reactor-pidfd-red.log. The review observed moving tests mid-read; production remained unchanged. Local module-scoped platform/syscall injection makes pidfd diagnostics RED executable on macOS; it already failed all4 intended cases.

Verbatim cross-family review:
```text
## Verdict: **PASS**

The amendment can be implemented correctly and keeps the robustness invariants. I checked each causal premise against the live code and found no P1. Six P2s below are worth folding in before the RED step. None of them reverses ALT-WT-021, the no-fallback rule or RM-5.2, so no RECONSIDERATION REQUESTED.

## Premises checked

| Item | Premise | Result |
|---|---|---|
| 1 | Bytes are charged only when text comes out, and are freed on delivery while the decoder still holds them | **Confirmed.** `accept` charges `len(data)` only if a chunk is produced; the EOF tail is charged 0 (`subprocess_runner.py:565-586`). |
| 1 | Without a limit, a long UTF-7 sequence stalls | **Confirmed, and it's worse than a stall on POSIX.** With `capacity()==0` and nothing pending, `select` keeps reporting the readable fd and `_read_ready` returns without reading, so the loop spins at full CPU until timeout. On Windows `_needs_turn` is false and no read is pending, so it waits forever if there's no deadline. |
| 1 | A UnicodeError during terminal capture still goes through cleanup | **Confirmed.** UnicodeError is a subclass of ValueError, which both capture loops already catch and re-raise after closing everything (`:888`, `:1421`). The existing error ordering is kept. |
| 2 | Opening an exit source for an already-reaped PID can hit an unrelated process | **Confirmed, and it happens in production.** Docker's `_wait_for_container` calls `process.poll()` before supervision starts (`plugin.py:1212`), so a fast `docker run` can already be reaped when the exit source is opened. On macOS, watching a reused PID that belongs to another user can fail with EPERM. |
| 3 | The `open_osfhandle` hook recurses | **Confirmed.** `test_subprocess_runner.py:1797` calls `_WINDOWS_RUNNER.msvcrt.open_osfhandle`, which after install is the hook itself. The result is a RecursionError, not the expected OSError, so that test fails on Windows. |
| 3 | Windows skips the wake-count check in the silent-child test | **Confirmed.** The check only runs when `spies` is non-empty (`:1155`, `:1166`). |
| 4 | Pipes are left open if Docker startup fails | **Confirmed.** `_cleanup_process` only kills and waits. On POSIX, garbage collection eventually closes the text wrappers. On Windows, `_OverlappedPipe` has no finalizer, so the handles leak permanently. That Windows leak is new with this PR, which is what puts the host and Docker fixes in scope. The client being acquired outside the try block was already true before this PR. |
| 5 | The refill does not add an extra 64 KiB per turn | **Confirmed.** Only one read is outstanding at a time and it is sized against capacity when issued. The in-flight count only grows when that same read is retired, so the in-flight total plus the outstanding read never exceeds the bound. |
| 5 | `reserved` is never read | **Confirmed.** It is only written, at `:1006`, `:1226`, `:1256` and `:1439`. It can be removed. |

## Findings

**[P2-1] Name the measurement tool, and stop carrying credits for bytes the decoder consumed without producing text.**
- The plan bans re-encoding but doesn't say how to measure what the decoder is holding. The tool is the documented `IncrementalDecoder.getstate()[0]` (the undecoded input buffer). Through `IncrementalNewlineDecoder` this returns the inner codec's buffer; the pending CR is a separate flag bit.
- The rule "credits from a decode producing no text carry forward" treats bytes the decoder *threw away* as if it were *holding* them. With `errors="ignore"` (or a custom handler that returns ""), more than 64 KiB of invalid input would wrongly trip the "decoder buffer limit" error, even though nothing is buffered.
- **Suggested change:** count in-flight as (sum of pending chunk sizes) + (current decoder buffer length).
  - Each emitted chunk is charged `previous buffer + len(data) − new buffer`.
  - Bytes consumed with no output are released immediately.
  - EOF charges the remaining buffer to the tail chunk.
- This is simpler (no separate carry counter). The two RED decoder tests already in the working tree, the split-UTF-8 and CRLF cases (`:2314-2350`), give the same numbers under this rule. All production callers use `errors="replace"`, which is why this is P2 and not P1.

**[P2-2] The limit check should look only at the decoder's buffer, and terminal capture needs its own test.**
- "Without releasable output" could be coded as `not pending_chunks`. During terminal capture no delivery happens, so earlier chunks stay pending and that check never fires. That is exactly the bypass the amendment says to prevent.
- **Suggested predicate:** raise when the decoder buffer length is at least `SUBPROCESS_STREAM_TURN_READ_BYTES`, checked in `_OutputStreamState.accept` / `accept_eof`, regardless of what is pending. While execution is active, this is exactly the condition that would otherwise spin or hang. During capture it limits memory to about 2× the bound and avoids the quadratic re-decoding cost of `BufferedIncrementalDecoder`.
- **Suggested test:** the planned TDD only covers the active phase, because a root writing 256 KiB blocks on the pipe and trips the limit early. Add a case where the root exits while a grandchild keeps flooding a UTF-7 shift sequence. Otherwise the "including terminal capture" clause is never tested.

**[P2-3] Docker: get the client before starting the process.**
- Moving `with _docker_client() as client:` above `open_supervised_process` removes the "client acquisition failed" cleanup path entirely.
- It also fixes something the amendment's version doesn't: killing the `docker run` CLI process does not stop a container it has already created, and without a client that container can't be removed.
- Inside the client block, use nested try/finally so the pipes are closed and `_remove_container` runs even if `_cleanup_process` raises.
- The test's `client` phase would then check that the process factory was never called.

**[P2-4] "Reuse shared cleanup" should mean sharing only the pipe-closing step, not the kill/wait policy.**
- Docker's `_cleanup_process` has its own tested rules: it swallows OSError and TimeoutExpired, lets other defects propagate, and waits 1.0 s (`test_docker_plugin.py:44-117`).
- `_cleanup_after_execution_failure` suppresses every BaseException, so swapping it in would break `test_cleanup_process_propagates_unexpected_defect`.
- **Suggested change:** pull the stdin/stdout/stderr close loop out of `run_monitored_subprocess`'s `finally` (`:390-393`) into one helper. Use it for Docker. For host, use `_cleanup_after_execution_failure` plus that helper, which is the same pairing `_attach_overlapped_pipes` already uses.
- Also note: the `FakeProcess` at `test_docker_plugin.py:1030-1031` has `stdout = object()`, so it needs real closable pipes once the helper runs in Docker's `finally`.

**[P2-5] The Windows "only infinite or zero waits" check is too strict for terminal capture.**
- `_capture_step` legitimately waits with a finite timeout bounded by the drain budget (`:1464`).
- Limit the check to waits made before the terminal decision, or allow finite waits up to `SUBPROCESS_STREAM_DRAIN_TIMEOUT` during capture. Otherwise it can flake when a read is still pending at exit.

**[P2-6] Spec and the plan's change table must record the new decoder limit.**
- Rejecting a valid stateful sequence over 64 KiB is a new user-visible failure mode. Under Class 5 it belongs in the same atomic RM-5.2 update as the pidfd requirement.
- The §5 row "Incremental UTF-8 + universal newline decoding — Preserved" needs a stateful-codec exception.
- Also specify the exception type for the pidfd requirement error so tests can assert on it.

## Observations (no action needed)
- Several RED tests are already in the working tree, even though the brief says tests haven't been edited:
  - the decoder credit tests, the UTF-7 limit test and the pre-reaped exit-source test (`test_subprocess_runner.py:2314-2390`);
  - the Docker startup-cleanup test (`test_docker_plugin.py:1460`).

  The RED step should record these rather than re-add them. The UTF-7 test still needs the bounded-read check and the two positive controls (small valid UTF-7, large ordinary UTF-8).
- "Both tests patching `subprocess.Popen`": only one Docker test does this (`test_docker_plugin.py:1083`). The macOS sandbox tests were already switched over.
- The macOS sandbox plugin has the same gap between starting the process and supervising it as host (`plugin.py:157-172`). It never runs on Windows, so this PR doesn't make it worse and it stays out of scope. Applying the shared helper there would be trivial.
- The pidfd fault-injection tests only go RED on Linux CI, not on this macOS checkout.
- After removing `reserved`, rewrite the plan's "reserved against the bound until the read retires" sentence (Task 1) to describe the one-outstanding-read rule.
- The "all 36 CI jobs" figure differs from Review Pass 4's 27. Check it against the workflow when committing.
- This review does not cover the Linux/Windows measurement procedure. That gate is still open.
```

Implementation refinement: the shared accept path fragments a terminal read at remaining decoder capacity before decoding. A single already-buffered kernel read can otherwise complete an oversized shift and evade a post-decode retained-buffer check. The terminal RED caught this exact bypass; final focused21pass1.63s includes both real active and terminal cases. Newline CR is explicitly fixed-size state outside raw-byte buffer accounting; UTF16 split/CR proof added.

### Core implementation closure evidence (2026-10-30)

Full SQLite6009passed58skipped208.16s and full PostgreSQL5961passed35skipped583.18s, normal12 workers/allmarkers, on repaired runtime before final test-only portability corrections. Initial focusedGREEN20passed1failed exposed a terminal-read bypass; capacity slicing fixed it. Final affectedmodules348passed8Windows-skipped8.68s after review corrections. Credit mutation failed0!=1; terminal fragmentation mutation failed intendedDIDNOTRAISEUnicodeError, including after the barrier-fixture correction (1failed1passed1.28s). Exact owner restored. Fullmypy479, Windows-targetmypy3, Ruff/format633, suppression-index and doc-path gates passed before final fixture corrections.

Core review F1 accepted: terminal fixture creates the actual64KiB Windows pipes and opens realIOsession while a child barrier holds output. Child then writes4098bytes and exits; only afterward is decodercapacity reduced to1024 before actualfinish_after_exit. Thus both leaves prove terminalcapture, without blocking writes into an artificially small Windows pipe. F2 accepted: existing operationalkill/wait and unexpecteddefect tests now assertpipeclosure. F3 accepted: unusedFakeContainer removed. The review observation claiming Host fixturefinally lackedwait/close is inapplicable: currentcode contains both. Final narrowexternalclosure pending.

Verbatim reactor-repair-implementation-review:
```text
I found one P1 that blocks the slice, and it's in a test, not production code. I found no production-code blocker. ALT-WT-021, RM-5.2 and the system invariants all hold, so there's no RECONSIDERATION REQUESTED. This was a read-only review: I ran no tests, and the Windows reasoning below is inferred from the code.

## Findings

**F1 [P1] blocker: the terminal-capture test will probably time out on Windows CI.**
- **Where:** `tests/core/test_subprocess_runner.py:2468`, the test of the terminal drain against the decoder limit.
- **Cause:** the test lowers `SUBPROCESS_STREAM_TURN_READ_BYTES` to 1024 *before* calling `open_supervised_process`. On Windows, `_create_overlapped_pair` (`subprocess_runner.py:1096`, `:1101`) reads that constant when it creates the pipe, so the child's stdout pipe is created with a 1024-byte buffer. The child's blocking `os.write` of the 4098-byte oversized payload cannot complete until the parent reads. Nothing reads before `process.wait(timeout=_EVENT_FAILURE_BOUND)`, so the test should hit `TimeoutExpired` after 30 s.
- **POSIX is unaffected:** pipe size there doesn't depend on the constant, so the local pass doesn't exercise this.
- **Confidence:** high that the patched value reaches `bufsize`. Moderately high that Windows blocks writes larger than the buffer. That's the classic subprocess deadlock on Windows, but CI hasn't run it.
- **Suggested fix:** move the `monkeypatch.setattr` to after `process.wait(...)`, so the pipe is created at its real 64 KiB size. The POSIX terminal path stays the same.
- **Related point, not a blocker:** even after that fix, the oversized case on Windows fails inside `_WindowsOverlappedProcessIO.__init__`. Its first read is capped at the 1024 capacity, so the limit trips before terminal capture starts. The shared slicing code in `accept` is still tested by the POSIX run, so the platform-neutral `UnicodeError` check is fine. Only the docstring's "terminal drain" wording is POSIX-specific.

**F2 [P2] not a blocker: pipe closing on Docker's kill-failure and defect paths isn't tested.**
- The plan's item 4 says endpoints close "even for an exited process/kill failure." The new table test proves this for the exited and alive/kill/wait paths.
- The kill-failure and defect-propagation unit tests (`test_docker_plugin.py:49`, `:102`) use fakes that have no pipes. So a version that closed pipes only on the normal paths would still pass them.
- **Suggested fix:** give those two existing fakes an `io.StringIO()` stdout and assert it's closed afterwards. No new test is needed.

**F3 [P2] not a blocker: the new Docker table test carries an unused fixture.**
- At `test_docker_plugin.py:1476-1507`, `FakeContainer` (its attrs, `reload`) and the `assert fake_container.attrs is not FakeContainer().attrs` check are never used. `wait_for_container_runtime_start` raises before anything reads the container.
- **Suggested fix:** return `object()` from the patched `_wait_for_container` and delete the class and the check.

## Checks that came back correct

- **Byte accounting:** the count of bytes in flight always equals the undelivered chunk sizes plus the decoder's buffered input (`getstate()[0]`). It never goes negative, and it can't exceed 64 KiB, because `_read_ready` and `_post_reads` size reads to the remaining capacity.
- **Decoder limit:** the `accept` slicing keeps buffered input below 64 KiB before each decode. So a single 64 KiB terminal read can't finish an oversized UTF-7 shift without tripping the limit. `UnicodeError` is a `ValueError`, so both capture loops record it and raise it after `close()`.
- **No spin or hang:** when nothing is pending, buffered input is under 64 KiB, so capacity is at least 1. On POSIX the next read either makes progress or raises. On Windows `_needs_turn` stays true and a read gets posted. Both deadlocks described in the plan review are gone.
- **`errors="ignore"`:** bytes the codec throws away are never charged. The test's final `capacity()` check would catch a version that charged them.
- **UTF-16 pending CR:** the CR's raw bytes are charged to the emitted chunk, and the CR itself only sets the flag bit. The test's literals match each step.
- **pidfd:** ESRCH (built as `ProcessLookupError`) means the child is gone. Missing `pidfd_open`, ENOSYS, EPERM and EACCES become a `RuntimeError` that keeps the original cause. EMFILE and other errors are re-raised unchanged. When this fails at session construction, the session closes and the child is killed through the normal failure cleanup.
- **Already-reaped child:** a cached `returncode` skips the PID lookup. The live-child test would catch a version that calls `poll()` before registering the exit watch. On Windows the process handle stays valid, so there's no PID-reuse risk there.
- **Windows waits and hooks:** `_WindowsWaitSpy` records only while the process is running, so the finite capture wait doesn't trip the zero-or-infinite timeout assertion. Both hooks keep a reference to the real function before patching, which avoids the earlier recursion.
- **`_OverlappedOp.reserved`:** no reader remains, so removing it is safe.
- **Docker:** no process is created if getting the client fails. The nested `finally` still removes the container. The kill/wait exception rules and the 1.0 s wait are unchanged.
- **Host:** the original exception is preserved, the process is reaped and the pipes are closed.

## Observations (no action needed)

- `pidfd_open` can also fail with ENODEV (the kernel lacks the anonymous-inode filesystem). It comes out as a raw `OSError`, which matches the deliberately narrow list. Add it only if you want it treated as a capability failure.
- The plan at lines 151 and 301 still uses the old private name `_cleanup_after_execution_failure`. Lines 791-792 are the quoted review text and should stay as they are.
- Host's rollback stops only the root process, not its process tree. That window is very short and `_host_handle` almost never fails, so it doesn't matter today.
- The Host test's `finally` neither waits on the process nor closes its pipes when the code under test is broken. The Docker test's `_reap_command_fixture` does both and could serve as the model.
- Running the Windows construction and active-wait tests is still a CI gate, along with the measurement rows.
```

Verbatim reactor-measurement-plan-review:
```text
## Verdict

- **Plan paragraph: BLOCKED on F1 only.** Two of the six rows (the context-switch rows), and the Linux CPU rows, would not measure the older runner's real cost on Linux, and probably not on Windows either. The fix is a short procedure amendment. Everything else is P2.
- **Implementation draft: blocker F1.** F2–F6 are P2. Your corrections are confirmed, including the 8 Windows-only cases plus the idle-wait case, dropping the PostgreSQL JUnit change, labelling the dirty local candidate honestly, the guard cleanup, and the Ruff/mypy refactor. F2 and F4 make two of them more precise.

No finding reverses ALT-WT-021, the System Invariants, RM-5.2 or TS-0. No timeout, load or worker settings change. No RECONSIDERATION REQUESTED.

## Findings

**[P1] F1: The context-switch counters miss the old runner's helper threads, and Linux CPU is too coarse.**
- **Linux:** psutil counts context switches by reading `/proc/<pid>/status` (`.venv/.../psutil/_pslinux.py:2034-2047`). That file covers only the process's main thread. The old `_PipeAdapter` did its 100 Hz polling on separate per-pipe threads, so the "before" row leaves out exactly the cost being measured. The new runner does everything on the main thread, so its count is complete. The comparison is biased by design.
- **Linux CPU:** psutil reads CPU time from `/proc/<pid>/stat` in 10 ms ticks (`:1828-1831`). An "after" value near 1 ms (macOS measured 0.7 ms) would read as 0 or 10.
- **Windows:** psutil reports only voluntary switches (`_pswindows.py:1093-1096`). As far as I know, it sums them over threads that still exist when sampled. The old runner's reader threads have exited by the end sample, so they would drop out. I couldn't read the C source here, which is why the fix below includes a check that settles it.
- **Suggested fix:**
  - On Linux, take both CPU and context switches from `resource.getrusage(RUSAGE_SELF)`. It counts all threads, including exited ones, and isn't limited to 10 ms ticks. Plan line 360 rejected getrusage only because it isn't portable, not because it's inaccurate.
  - On Windows, keep psutil CPU (`GetProcessTimes` counts exited threads, but only in about 15.6 ms steps; record that).
  - Keep the psutil values in the raw output on every platform for continuity.
  - Add one calibration check per run: a thread performs N short sleeps and is joined, and you record whether the counter rose by about N. If it didn't, that platform's context-switch row is labelled "excludes exited threads; before is a lower bound."
  - Add one sentence to the plan naming the counter source per platform.

**[P2] F2: Provenance needs to be exact.**
- **Wrong "after" commit in CI:** On `pull_request`, `actions/checkout` checks out the merge commit, so `git rev-parse HEAD` isn't the PR head. Record `GITHUB_SHA` and `${{ github.event.pull_request.head.sha }}`, and label the "after" arm as the merge tree.
- **sha256 alone doesn't prove which commit was imported:** On Windows with autocrlf, `git archive` and the checkout both write CRLF files, so their sha256 won't match the git blobs. For each module, also record `git -C root hash-object --path=<rel> <file>` and compare it with `git rev-parse <rev>:<rel>`. The same comparison produces the honest dirty flag for local runs, so one mechanism covers your labelling correction.
- **Check every imported module:** Confirm that every `weft*` entry in `sys.modules` resolves under the selected root, not just three modules. It's one loop.
- **Fetch less:** Use `git fetch --depth=1`. In a shallow clone, fetching by SHA otherwise pulls the baseline's whole history.

**[P2] F3: Say how long the measurement step lives.** As drafted, every future CI run would fetch the frozen 540707 commit and benchmark it. TS-0 (`08-Testing_Strategy.md:27-29`) classes benchmarks as outside the test contract, and a frozen baseline will eventually break on unrelated dependency or signature changes. State in the plan that the step and `BASELINE` are removed in the closing change. Decide separately whether the Windows JUnit check stays as a permanent guard against the Windows path silently skipping.

**[P2] F4: Cleanup details.**
- **Timeout guard:** `subprocess.run(timeout=)` kills the worker before raising, and once the worker is dead you can no longer find its children. Instead:
  1. Start the worker with `Popen` and call `wait(timeout)`.
  2. On timeout, snapshot `psutil.Process(pid).children(recursive=True)` before killing anything.
  3. Kill the worker and the snapshot, then call `psutil.wait_procs`.
  4. Do all of this inside the `TemporaryDirectory` block. On Windows the children inherit `cwd=tree`, which blocks deleting the directory and hides the original timeout error.
- **`child()` cleanup:** If `wait(timeout=10)` raises, the loop that closes the pipes never runs. Put the pipe closes in an inner `finally`.

**[P2] F5: Make both arms run the code they claim to.**
- **Stdin:** Production opens a stdin pipe only when there is stdin data (`weft/core/runners/host.py:941`). Use `pipe_stdin=False` in the "after" arm and `stdin=None` in the "before" arm, so the measurement doesn't depend on how the old runner handles an open stdin pipe it never writes to.
- **Constructor:** Pick it by arm label instead of the silent `getattr` fallback. Assert that "before" has `_PipeAdapter` and "after" has `_open_process_io`, and record the constructor used in the JSON.

**[P2] F6: Keep the plan and workflow consistent.**
- §9 (plan line 532) still requires "Windows job logs with durations" and "27 OS/Python matrix jobs", and lines 676 and 720 repeat the old gate. Replace them in the same amendment so there aren't two gates.
- Add the new script to TS-0's list of benchmark scripts (`08-Testing_Strategy.md:27`).
- Add `--junitxml` only on the Windows core-commands jobs, through a matrix expression. That leaves the other 24 job commands byte-identical, as your "keep existing args" correction intends.
- Have the check match the exact classname `tests.core.test_subprocess_runner` rather than `endswith`.

## Observations (no action needed)

- **Case IDs:**
  - Windows-only (8):
    - `test_windows_cancel_tears_down_overlapped_reads[pending-read|no-pending-read]` (`tests/core/test_subprocess_runner.py:1768`)
    - `test_windows_supervised_process_construction_rolls_back[pipe|process|attach]` (`:1877`)
    - `test_closed_input_write_errors_partition_closed_pipe_from_faults[windows-broken-pipe|windows-no-data|windows-access-denied]` (`:2086-2093`)
  - Idle-wait case (not parametrized): `test_idle_subprocess_waits_on_readiness_without_polling` (`:1125`).
  - No other Windows-only reactor tests exist, none are marked slow, and the conftest hook only adds `shared`/`sqlite_only` markers.
- **JUnit semantics:** A skip produces a `<skipped>` element. A test that errors in teardown gets an `<error>` element or a duplicate entry. Both fail the exactly-once, no-error rule, so the check fails closed.
- **Job count:** 27 test jobs + 3 PostgreSQL + lint + coverage + 4 extensions = 36, matching your figure.
- **Archived tree imports cleanly:** There is no root `.gitattributes`, so `export-ignore` can't drop files, and `weft/__init__.py` takes its version from `_constants` rather than a generated file.
- **No mixed trees:** The hatchling editable install adds the checkout to `sys.path` through a `.pth` file. `-I` still loads site-packages, so the same psutil and other dependencies are available to both arms, and the inserted root comes first. `weft/core/__init__.py`, `weft/core/runners/__init__.py` and `weft/ext.py` load no plugins, so nothing is pulled in from the other tree.
- **Script-path invocation is justified:** `-I` rules out the existing `python -m tests.<benchmark>` convention.
- **New top-level tests won't run in CI unless listed:** If you add verifier tests as `tests/test_*.py`, they must be added to the `remaining` targets in both matrices.
- **Comparability:** Using `process.terminate` as the stop hook, rather than production's `terminate_process_tree`, keeps the rows comparable with the macOS procedure. Your local macOS run of the committed script is a same-script cross-check against the earlier scratch-script rows; label it that way.
- **Covered by your refactor:** Ruff B023 (the timer closure inside the loop), mypy on `module.__file__` being Optional, and the bare `assert` checks.

I wrote no plan file. This session had only read-only tools, and you asked for review output only.
```

### Operative measurement-gate correction (2026-10-30)

This supersedes the earlier all-platform psutil-only procedure and duration-log firing evidence. The six rows remain: idleCPU/ctx with no cancellation and with pull cancellation, output-to-callback latency, cancellation-through-reap latency. Preserve3x3s idle samples per mode,10 framed output samples and5cancel samples, with raw observations and no performance threshold.

Linux primaryCPU/context-switch counters use resource.getrusage(RUSAGE_SELF), which aggregates the process's live and exited threads. Also retain psutil raw counters on every platform for continuity. Windows uses psutil/GetProcessTimes CPU and psutil context switches, explicitly recording CPU quantization (zero reported CPU is not proof of zero work). Before measuring, calibrate context-switch coverage with a joined helperthread performing100shortsleeps. Preserve raw counter deltas and expected sleeps; if the observed counter does not establish exited-thread coverage, label those context-switch rows lower-bound/coverage-unverified instead of claiming the old helper-thread cost was measured completely. Do not compare context-switch values across OSes. Existing macOS scratch rows retain their historical method label; new same-script local paired samples are a separate cross-check.

Use dev-only tests/subprocess_reactor_benchmark.py under TS-0. Each arm runs in a fresh isolated interpreter with a complete selectedsource tree before any Weft import. Assert the before arm has _PipeAdapter and uses Popen(stdin=None); after uses open_supervised_process(pipe_stdin=False) and _open_process_io. Record constructor, Python/platform/psutil, source hashes, normalizedgitblob IDs against the selectedrevision, rawsamples and allsixderivedrows. Validate every imported weft module belongs to the selectedroot before and after measurement. Current HEAD alone does not establish clean source: record imported-module mismatch/working-tree dirtiness. Record manual GitHubSHA/ref and link the normal PR run by its API head SHA; normal PR checkout may be a merge tree, whereas the requested manualrun uses the actual branchhead. Sourcehashes resolve equivalence rather than assuming PR and manualtreesidentical.

Lifetime: add optional workflow_dispatch stringinput reactor_baseline with emptydefault. NormalPR/push CI performs no benchmarking/fetch. One explicit manualrun on the repairedbranch passes frozenfullSHA540707a370d32cf9475832099fabd52fa1dc7f0a and runs the samefull36jobs, with pairedmeasurements after existingPython3.13core tests onLinux/Windows only. The script accepts a validated40hex --baseline argument, not a permanentfrozenconstant. Baselinerevision is supplied through argv/environment, never shell interpolation. Fetch exactSHA with --depth=1 onlyifmissing, archive fulltree, keep same dependencyenvironment forbotharms. This replaces F3's proposed closing-source-removal step: the reusableopt-in gate has no recurringfrozenbaseline; no secondsourcepush solelytoremoveinstrumentation isneeded. Both normalPR and manualrun mustfinish; skippedchecksnevercountaspasses.

A180s externalmeasurement-arm failureguard uses Popen.wait, snapshots descendants before terminating the worker, kills/reaps ownedworker/tree within theTemporaryDirectory lifetime, and preservesfailure. Innerchildcleanup closespipes evenifwaitfails. This guard does not supply a deadline to the measuredsupervisor or alter product/testtimeouts. AddJUnit onlytoexistingWindowscore invocations. Verify exactclassname tests.core.test_subprocess_runner and all8 Windows-only parametercases plus idle-wait case appear exactlyonce withoutskip/failure/error. Upload per-run artifacts usingexistingpinnedupload action. JUnitguard remains a Windows execution-evidence guard, independentofoptionalbenchmark. No worker/load reductions and no newmatrixjobs.

Validation: independentplanclosure and implementationreview; actualpaired localrun withprovenance/rawcounts checked; focused artifact-verifier cases includingmissing/skipped/failed/duplicate andvalidrecords; actual ownedworker guard cleanup proof; fullstatics andfinalCI36+36 with measurementartifacts and JUnitcaseverification. Newtests live in tests/system/ so both existingmatrices selectthem. Include script in TS-0 benchmark list; keepallgoverning gate wording aligned. Maincheckoutremainsfrozen whenever testsrun.


### Core repair closure (2026-10-30)

Verbatim independent cross-family review:

```text
**PASS, no blocker.** F1, F2 and F3 are all closed, and the fixes don't introduce any new defect. ALT-WT-021, RM-5.2 and the system invariants are unchanged, so no RECONSIDERATION REQUESTED. This was a read-only review: I ran nothing, and the Windows behavior below is inferred from the code, not observed.

## F1: terminal fixture (`tests/core/test_subprocess_runner.py:2464-2503`). Disposition: close.

The pipe is now created at its real size. The patch to 1024 happens after `process.wait` (`:2489`). So `_create_overlapped_pair` (`subprocess_runner.py:1096`, `:1101`) builds the pipes with 64 KiB, and the constructor's first read (`:1233`) is sized at 64 KiB. The child's 4098-byte write fits on both platforms, so there's no deadlock. Nothing in the session caches the constant: `capacity()`, `accept` and `_decode` all read the module global each time they run (`:575`, `:584`, `:604`).

**Capture is entered on both leaves:**
- **POSIX:** nothing is read before the patch, so all 4098 bytes are still in the kernel pipe. `_capture_remaining` (`:929`) does one 64 KiB `os.read`. `accept` slices the first 1024 bytes and `_decode` keeps the open UTF-7 shift, so 1024 bytes are buffered, which hits the limit and raises `UnicodeError`. It's caught as a `ValueError` (`:933`), the session closes, and the error is re-raised (`:917`).
- **Windows:** the constructor's 64 KiB read is still pending when the child writes, so all 4098 bytes complete into it. `finish_after_exit` → `_capture_remaining` → `_settle_read` (`:1464`) cancels and then collects it. The cancel does nothing because the read already finished, and the result is `(4098, 0)`. `accept` raises inside the capture loop's `except` (`:1465`). The error is no longer raised from `__init__`, so the earlier problem is gone.
- **Small-valid-shift case:** POSIX gets `"€\n"` and then EOF. On Windows, `_settle_read` collects the 7 bytes, then `_capture_step` sees the closed pipe through `PeekNamedPipe` and records EOF. Both leaves return `("€\n", "")`.

**The mutation checks fire on both leaves.** Without the slicing in `accept`, the whole 4098 bytes are decoded in one call, the shift closes, nothing stays buffered, and the test fails with DIDNOTRAISE. That matches your native Mac result. If the reads arrive in smaller pieces, the test still raises, because buffered input grows until it reaches 1024.

**Cleanup:** the error is raised only after `close()`, so `_assert_owned_pipes_released` holds. In the `finally`, `barrier.close` lets a child stuck at the barrier go, `session.close()` is safe to call twice on both leaves, and `_reap_test_process` reaps. The test thread calling `process.wait` doesn't break the POSIX rule against a thread parking in `Popen.wait`, because no supervisor thread exists in this test.

## F2: Docker kill-failure and defect tests (`test_docker_plugin.py:49-126`). Disposition: close.

All three fakes now have a `StringIO` stdout and assert it's closed, and `io` is imported (`:6`). `_cleanup_process` (`plugin.py:1265-1282`) has early returns on kill `OSError` and on wait `OSError`/`TimeoutExpired`, and a defect can propagate. If the closing ran after the `try` instead of in `finally`, every one of these tests would fail. The `poll() is not None` early return is covered by the table test's `exited` phase. The class-level `StringIO` is fine because each test call defines its own class.

## F3: unused fixture in the Docker table test (`test_docker_plugin.py:1478-1566`). Disposition: close.

The local `FakeContainer` and its `attrs` assertion are gone, and `_wait_for_container` now returns `object()`. The lambda takes `runtime_id` and `process` by keyword, matching the call at `plugin.py:243-247`. `wait_for_container_runtime_start` raises before anything uses the container. The module-level `_FakeContainer` (`:227`) is still used by the liveness tests.

**The cleanup checks fire in all three phases:**
- `client`: no process is created.
- `startup`: the process is killed and waited, so `returncode` is set and all pipes are closed.
- `exited`: the process returns early, but `finally` still closes the pipes.
- `_reap_command_fixture` still frees the real resources even if the cleanup code under test is broken.

## Observations (no action needed)

- On Windows, the oversized case trips the limit during cancel-then-collect (`_settle_read`). It doesn't reach the branch of `_capture_step` that reads the remaining data. The slicing that matters is shared code, so this is fine. The small case does reach `_capture_step`, but only for the EOF check.
- The `# pragma: no cover` markers at `plugin.py:1271` and `:1275` are on lines these tests now run. That predates this round and isn't made worse.
- Windows CI is still required for the Windows path of this test, the construction and active-wait tests, and the measurement rows.
- As instructed, I didn't revisit the retracted Host-test observation.
```


### Measurement plan closure (2026-10-30)

Verbatim independent cross-family review:

```text
I traced each accepted fix against the source. None of the corrections introduces a P1. I found five new P2 issues. Each is a small edit, and two of them remove work.

## Verdict

- **Planning gate: PASS.**
- **Draft gate: no blocker.** Findings R2-1 to R2-5 are all P2.
- F1–F6 are taken up as accepted, and the counter semantics hold (see Observations).
- No change to ALT-WT-021, the System Invariants, RM-5.2 or TS-0. No product, load, worker or deadline change. No RECONSIDERATION REQUESTED.

## Findings

**[P2] R2-1: On Linux, the main CPU reading includes psutil's own file reads.**
- `snapshot()` always reads psutil first and `getrusage` second.
- At the start of a sample, the psutil read happens before the `getrusage` window opens, which is correct.
- At the end of a sample, the psutil read happens inside the window. That adds two `/proc` file reads to every main CPU number, in both arms. The "after" idle value is around 1 ms, so this extra amount is not negligible.
- **Fix:** take the second `getrusage` reading before the second psutil read, so the psutil reads fall outside the window. `calibrate()` should use the same order.

**[P2] R2-2: Context-switch rows aren't labelled from the calibration result.**
- The plan says rows must be labelled lower-bound when calibration doesn't confirm the counter covers exited threads.
- The draft records the calibration result, but `rows` contains only plain medians plus a fixed limitation note.
- **Fix:** derive one label from `calibration["includes_exited_thread_evidence"][primary]`, such as `"complete"` or `"before-lower-bound"`, and attach it to the two context-switch rows.

**[P2] R2-3: The plan promises a PR-versus-manual comparison that can't be made, and the step carries a PR field that is always empty.**
- Normal PR runs no longer measure anything, so they produce no source hashes. "Source hashes resolve equivalence" therefore has nothing to compare against.
- The measurement step only runs on `workflow_dispatch`, so `WEFT_PR_HEAD_SHA` is always empty.
- **Fix:**
  - Drop that env var and its JSON field.
  - In the plan, say the measured tree is the dispatch run's `GITHUB_SHA` (the branch head), recorded with the per-module blob IDs.
  - Link the PR run by checking that its head SHA, taken from the run's metadata, equals that SHA. The merge tree is never measured.

**[P2] R2-4: `candidate_dirty_status` will look dirty on every clean Windows CI run.**
- `pytest-results.xml` isn't in `.gitignore`, so `git status --porcelain` lists it as untracked.
- **Fix:** use `--untracked-files=no`, or treat the per-module `dirty` flags as the authority and label the status string informational.

**[P2] R2-5: TS-0 would call a permanent CI check a dev-only benchmark.**
- `--verify-windows` is a pass/fail CI check, but TS-0 (`08-Testing_Strategy.md:27-28`) classes the scripts listed there as outside the test contract.
- **Fix (docs only):** in the TS-0 entry, say the script's measurement mode is dev-only and its `--verify-windows` mode is a permanent CI execution check.

## Scope expansions

None.

## Observations (no action needed)

**Counter semantics**
- **Linux:** `RUSAGE_SELF` totals context switches over live and exited threads. Its CPU time comes from the scheduler's runtime accounting, not 10 ms ticks.
- **psutil on Linux:** its context-switch count reads only the main thread, so its calibration result will be false. That is expected; it stays raw data only.
- **"After" arm:** both platform implementations return `()` from `owned_threads()` (`subprocess_runner.py:808`, `:1238`), so "after" counts are complete under any counter. Only "before" can be a lower bound.
- **Windows calibration:** as I understand it, on 3.13 and later `Thread.join` waits until the OS thread has exited, so the check tests the post-exit case. On 3.12 there may be a race. CI measures only on 3.13; local runs should use 3.13 or later (the earlier macOS rows used 3.14.9).

**Signatures and cleanup**
- The draft's `open_supervised_process` call (`:979-987`) and `run_monitored_subprocess` arguments (`:104-121`) match the current code.
- I couldn't read the 540707 signature with read-only tools. A mismatch would raise a `TypeError` in your local paired run.
- Closing the pipes twice is safe: `_close_pipe` and `_OverlappedPipe.close` (`:1079-1082`) are idempotent.

**JUnit check**
- The eight Windows-only case IDs and the idle-wait case are confirmed (`test_subprocess_runner.py:1125, 1768, 1877, 2086-2088`).
- All nine are module-level functions, so the exact classname match is correct.
- `--dist load` doesn't run tests twice, and no rerun plugin is installed.

**Workflow**
- On push and PR, `inputs.reactor_baseline` evaluates to null, and the `event_name` check guards the step anyway.
- The other 24 jobs get a trailing space in their pytest command. That is identical in effect, though not byte-identical.
- Dispatching needs `workflow_dispatch` in the default branch's copy of `test.yml`. Confirm main has it before you dispatch.

**Imports and environment**
- Nothing in `tests/*.py` shadows a standard-library module or psutil, so the non-isolated `--verify-windows` run is safe.
- `requires-python >=3.12` guarantees `extractall(filter="data")` is available.
- Hatchling's default editable install uses a `.pth` file, not an exact import hook, so the inserted root comes first on `sys.path`.

**Provenance**
- A file that is missing at the revision makes `rev-parse` fail, and a module loaded from the wrong root makes `relative_to` raise. Both fail closed, though the error messages are terse.
- The cost is about two git calls per imported module, per pass, per arm. On Windows that's seconds, well inside the 180 s guard.

**Expected lint and type fixes**
- `rows` mixes float and dict values, which mypy will flag.
- Some lines are too long.
- Depending on the enabled rules, Ruff may flag `S314`, `S202` or `S603`.

**Local run**
- Point `--output` outside the repository so the main checkout stays frozen.

I wrote no plan file, as you asked.
```

### Closure dispositions and local measurement acceptance (2026-10-30)

Core closure accepted with no remaining actionable findings. The Linux capability message now includes the original errno/reason as well as preserving the exception cause, so flattened task errors remain useful; its focused table passed six cases after three qualified RED assertions for missing error text. No polling fallback was added.

Measurement closure F1-F5 accepted: Linux end snapshots take RUSAGE_SELF before observer psutil reads; context-switch rows carry complete/before-lower-bound coverage labels; manual workflow provenance records its actual GITHUB_SHA and links normal PR evidence by run head metadata rather than claiming a merge tree was measured; dirty tracking excludes untracked artifacts; TS-0 distinguishes optional measurements from the permanent Windows execution guard. Native independent review found no blocker and two TS-0 formatting defects, both corrected.

The first new acceptance run ended in an xdist internal error before tests completed. Its log contains no explicit classification error, so that cause is unproven. The independently observed missing shared backend marker was corrected; all eight acceptance cases then passed (0.97 s). Removing JUnit status rejection caused three exact `incomplete Windows execution evidence passed` assertions. Removing descendant discovery caused the actual child `is_running()` assertion. Restored source passed all eight in 1.03 s. Logs: `reactor-measurement-acceptance.log`, `reactor-measurement-acceptance-classified.log`, `reactor-measurement-verifier-mutant.log`, `reactor-measurement-guard-mutant.log`, and `reactor-measurement-restored.log` under the session scratch directory. No mutation remains.

The same-script macOS paired smoke completed successfully with three idle samples per mode, ten output samples, five cancellation samples, all imported module paths checked, and honest dirty-import flags. External unrelated Docker tests were present, so these are execution/provenance smoke observations, not quiet performance acceptance. Linux/Windows native artifacts and all nine Windows JUnit cases remain CI gates; no local result substitutes for them. The final cross-family measurement implementation review is pending.


### Measurement implementation review closure (2026-10-30)

Verbatim cross-family final review:

```text
**Verdict: PASS, no blocker.**

I read the two new files, the current `test.yml`, and the TS-0 paragraph (`08-Testing_Strategy.md:82-86`). I traced them against the current `subprocess_runner.py`. I didn't treat the stray `#` or the changed heading as findings, since you're already fixing them. Nothing reopens TS-0, RM-5.2 or ALT-WT021, so I'm not requesting a reconsideration.

## Accepted refinements: all present
- **Rusage snapshot timing:** in the closing sample, `snapshot(closing=True)` reads rusage first, then psutil CPU, then psutil context switches (`subprocess_reactor_benchmark.py:116-121`). The opening sample uses the mirror order (`:121-127`), so the psutil reads stay outside the rusage window. `calibrate()` uses the same pair of calls (`:141-145`).
- **Coverage labels:** each label comes from that run's calibration of the primary counter. "after" is always `complete` (`:306-314`). That matches `owned_threads()` returning `()` on both platforms (`subprocess_runner.py:809`, `:1239`).
- **Provenance:**
  - It records GITHUB_SHA, GITHUB_REF and GITHUB_EVENT_NAME, and there's no PR-head field (`:449-452`).
  - The candidate is `HEAD`, which equals `GITHUB_SHA` on a manual run.
  - The `--untracked-files=no` flag stops `pytest-results.xml` from making the tree look dirty (`:446-448`).
- **TS-0:** the paragraph now describes the two modes separately, as the closure asked.
- **Workflow:**
  - Normal PR runs are unchanged. The measurement step is gated on a manual run, 3.13, core-commands and not macOS (`test.yml:85`).
  - The baseline reaches the script through an env var and a quoted argument, and the script requires a full 40-hex ID before `rev-parse` or `fetch` sees it (`:412-415`).
  - Windows verification runs on all three Python versions with `always()` (`:80-82`). Artifact names are unique because uploads only happen from core-commands.

## Correctness checks (no action needed)
- **JUnit check:** it filters on the exact classname, requires exactly one match per case, and rejects `skipped`, `error` or `failure` (`:46-61`). If xdist writes a duplicate entry for a teardown error, the check fails, which is the safe direction. The test covers all six faults plus the passing case, and its list of nine names is separate from the script's.
- **Worker isolation:**
  - `-I` keeps the script directory and the current directory off `sys.path`.
  - The inserted root comes before the editable `.pth` entry.
  - The script imports no weft code before that insertion.
  - The repo has no root `.gitattributes`, so `git archive` can't drop files with export-ignore.
  - Nothing in `weft/` is generated or gitignored.
  - `hash-object --path` applies the same line-ending conversion as the checkout, so the Windows autocrlf setting doesn't create false dirty flags.
- **Timeout cleanup:** it lists descendants before killing the worker, then kills them, waits and asserts none are left (`:368-379`). The test drives the real `execute_arm` and checks a real grandchild process.
- **Current-side signatures:**
  - The `open_supervised_process` call matches `:980-989`.
  - The `run_monitored_subprocess` arguments and the `(str, bool)` output callback match `:104-120`.
  - The `"ok"` and `"cancelled"` statuses match `:370` and `:197`.
  - On Windows, a trailing `\r` is harmless because `float()` strips whitespace.
- **pidfd:** the collector only calls the existing primitives. It adds no fallback.

## Non-blocking simplifications
1. **Delete the first `module_provenance` call (`subprocess_reactor_benchmark.py:349-350`).** Its result is thrown away, and `sys.modules` only grows, so the call after the run already covers every module. The comment's reason, "to include lazily imported modules", is met by the second call alone. The first call only fails earlier and costs about two git calls per module (seconds on Windows). Either delete both lines, or reword the comment to say it's a fail-fast check.
2. **Optional:** no test checks that `resolve_baseline` rejects a non-40-hex value. The regex is simple and the input is quoted, so I'm not adding this as a requirement.

## What I couldn't verify
- **Local pair artifacts:** the file tools are restricted to the repo, so I couldn't read `/tmp/weft-implementation-20261027/reactor-local-pair`.
- **Baseline-side compatibility:** whether the baseline's `run_monitored_subprocess` arguments, `RunnerHandle` fields and `_PipeAdapter` match what the "before" arm calls is only shown by a completed pair run. Until a pair run finishes, the local one if it's done, otherwise the native ones, this is open.
- **Native artifacts:** as you said, the Linux and Windows measurement artifacts are still required after you push.
```

Disposition: PASS/no blocker. Retain the pre-run provenance call as the approved fail-fast source-isolation check, and clarify its comment; post-run validation catches lazy imports. Optional baseline-regex test is not added: the simple validated argv boundary is unchanged, and the existing real paired run covers baseline resolution. The completed local pair resolves baseline-side signature compatibility; scratch artifact inspection by implementer and parent confirms both constructors, 12 imported modules each under selected roots, clean baseline/dirty candidate, and all sample counts. Native Linux/Windows execution remains a required CI gate. Native independent reviewer also found no blocker; both TS-0 formatting findings were corrected.

Final local SQLite all-marker validation: 6,017 passed, 58 skipped in 237.28 s with normal 12 workers. Full mypy 481, Windows-target mypy 3, Ruff, format 635, suppression registry, and documentation paths passed. PostgreSQL final run follows; earlier full PostgreSQL runtime repair run passed 5,961 with 35 skips.


Final PostgreSQL all-marker validation completed: 5,969 passed, 35 skipped in 557.80 s, normal 12 workers, no load or timeout reductions (`reactor-final-pg.log`). The new eight collector acceptance cases run in both backend lanes. All local implementation/review gates are satisfied. Plan remains draft pending native Windows execution, Linux/Windows six-row measurements, and both exact-head CI runs. Concurrent launch workers remain deferred.


### CI delivery-oracle correction (2026-10-30)

Exact-head manual CI d4381877 failed Ubuntu3.12 and3.14 core in `test_stream_queue_drain_yields_with_continuing_production`: the first turn delivered one chunk, and the immediately following zero-time turn correctly delivered none. The test asserted a queued backlog without establishing one. A deterministic real-child probe holds the producer after its first64-byte write and reproduces the exact line1596 assertion against unchanged runtime (`reactor-delivery-premise-red.log`). This establishes the fixture defect; it does not justify runtime changes or retrying the failed run.

Class2 test-only correction under [TS-0]/[RM-5.2]: retain the real continuous producer for bounded delivery-turn progress; separate the expired-deadline contract into the actual shared `_OutputStreamState` with two explicitly queued chunks. An already-expired monotonic deadline must deliver one and retain the other; removing deadline yield must fail by delivering both. Remove the slow15ms sink delay and scheduler-dependent backlog claim. No timeout/load/worker changes. Validate focused tests, the exact deadline-yield mutation, restored full module, statics, native fresh-eyes and cross-family narrow review, then push corrected tests and require fresh exact-head normal/manual CI. Earlier failed runs remain recorded and are not passes.


Verbatim narrow cross-family delivery review:

```text
**Verdict: PASS. I found no blockers.**

I only read code for this. I ran no commands or tests and made no edits. I checked the diff against `_OutputStreamState` at `weft/core/runners/subprocess_runner.py:544-636`, which d4381877 left unchanged.

## Correctness

**`test_stream_delivery_yields_at_expired_deadline_with_queued_work`** (`tests/core/test_subprocess_runner.py:1594`)
- `accept(b"first")` and `accept(b"second")` each queue one separate chunk (`_decode`, lines 591-600). Neither one ends in `\r` or a partial UTF-8 sequence, so the newline decoder holds nothing back. The backlog actually exists before `deliver` runs, which fixes the old problem: that fixture assumed a backlog without proving it.
- `deliver(16, 0.0)` takes and delivers `"first"`, then reaches the `time.monotonic() >= turn_deadline` check (line 635) and returns. So `delivered == ["first"]` and `pending_chunks == ["second"]` are both exact, with no timing involved.
- `deliver(16, inf)` takes `"second"`, finds the queue empty and returns (line 622). Checking `in_flight_bytes == 0` also proves that `pending_sizes` stayed in step with `pending_chunks`.
- This matches your mutation result: removing the deadline yield gives `['first','second'] != ['first']`.

**`test_stream_queue_drain_yields_with_continuing_production`** (`:1567`)
- With `wait_and_drain(None)`, `_turn_deadline` returns `math.inf` (line 676), so only `SUBPROCESS_STREAM_DRAIN_MAX_CHUNKS` limits the turn. With 64-byte reads and a 64 KiB per-turn read budget, up to 1024 chunks can be ready in one turn, so the cap of 16 really is the limit being tested.
- The new `process.poll() is None` check rules out the turn ending because the producer exited. The bound being tested is the chunk cap.
- Removing `slow` and `threading.Event().wait(0.015)` removes the last sleep from this test. `threading` is still used elsewhere in the file, so no import goes unused.

## Simplicity and deque use
- `list(state.pending_chunks) == ["second"]` is the right comparison. A deque never equals a list, so the `list(...)` conversion is needed. No extra deque behaviour is assumed beyond what the runtime uses (`append` and `popleft`).
- The pipe setup, the closed write end, `_OutputStreamState(pipe, …)`, the literal `16` and `float("inf")` all match the existing decoder tests at `:2355` and `:2374`. Nothing new is added to the test framework.

## Optional improvements (none blocking)
1. **Deadline value:** `0.0` counts as expired only because `time.monotonic()` is never negative. Python leaves the clock's starting point undefined, but in practice it is non-negative on CPython for Linux, macOS and Windows. Using `-math.inf` would make the test independent of the clock's origin. I'd call this cosmetic.
2. **Extra assertion:** after the first `deliver`, checking `state.in_flight_bytes == len(b"second")` would pin the byte credit at the moment of the yield. The final `== 0` check already covers most of this.

## Not actionable / limits
- The continuous-producer test's `0 < len(delivered)` check depends on stdout becoming readable first. It was already there before this delta, so it's out of scope.
- Native Windows is still a CI gate that I can't check from here. The new test uses only `os.pipe` and `_OutputStreamState`, which other decoder tests already use the same way.
- The delta touches no runtime code, load, workers, deadlines, speed thresholds or TS0/RM5.2 rules, and nothing in it asks to change those rules.
```

Disposition: PASS/no blocker. Accept clock-origin improvement using negative infinity for an unconditionally expired deadline. Native review and parent correctly noted that the real producer can supply fewer than the cap, so it cannot alone guarantee a cap-removal mutant. The shared primitive test is now parameterized for chunk-cap (1,infinity) and deadline (16,negative-infinity), with the same explicit two-chunk premise. Cap-removal mutant fails exact first-plus-second vs first assertion; deadline-removal mutant independently fails that same intended assertion. Both mutations restored; full module passed70 with8 Windows skips in5.68s. Optional intermediate byte-credit assertion is unnecessary because existing decoder-credit tests and finalzero check already cover that contract. Native reviewer found no blocker. Final narrow closure covers the parameter addition.


Verbatim final parameter-table closure (39.33 s):

```text
**Verdict: PASS. No blockers, no findings.** This was a read-only review: I ran no tests or commands, and native Windows still depends on CI, as you said.

I checked the working-tree code against your diff (`tests/core/test_subprocess_runner.py:1567-1617`) and against `_OutputStreamState.deliver` (`weft/core/runners/subprocess_runner.py:619-636`).

**Each case pins one boundary on its own:**
- `deliver` loops `for _ in range(max_chunks)` and checks `time.monotonic() >= turn_deadline` after each delivery.
- **`chunk-cap` (1, inf):** the deadline can never fire, so only the loop bound stops after "first". Removing the cap, or an off-by-one like `range(max_chunks + 1)`, delivers both chunks and fails `delivered == ["first"]`.
- **`expired-deadline` (16, -inf):** the cap of 16 is well above the 2 queued chunks, so only the deadline check stops after "first". Removing that check delivers both and fails the same assertion.
- If the deadline check were moved before delivery, the -inf case would deliver nothing. That also fails, so the test pins "deliver at least one, then yield".
- -inf avoids depending on where the monotonic clock starts, as your point 5 says.

**The setup is valid:**
- `b"first"` and `b"second"` are plain ASCII with no trailing CR, so the decoder holds nothing back. Each `accept` queues exactly one chunk with credits 5 and 6.
- After the second `deliver`, `in_flight_bytes == 0` is correct: credit is freed when a chunk is delivered (`:625`).
- The test reads the real `pending_chunks` deque and `in_flight_bytes`. No new helpers, sleeps or abstractions.
- The pipe/`fdopen` setup and `deliver(16, float("inf"))` copy the existing primitive tests at `:2366-2388` and `:2527-2532`.
- The lambda's `append` returns `None`, which matches `_OutputCallback = Callable[[str, bool], None]` (`:70`).
- The test uses only `fileno`, encoding and errors on the pipe, with no select or polling, so nothing in it depends on POSIX.

**The live-producer test is narrowed correctly:**
- The slow-sink and `wait_and_drain(0.0)` step is gone. That deadline proof now lives in the primitive test.
- What remains is the `0 < len(delivered) <= cap` bound plus a check that the producer is still running. That fits your finding that a live producer alone can't prove the cap.
- `threading` is still used elsewhere in the module, so removing the `threading.Event().wait` call leaves no unused import.

**Scope:** the change is test-only. Runtime, load and deadline behaviour are untouched, and there's nothing that would justify reopening TS0/RM5.2.
```

Final disposition: PASS, no findings. Focused three cases pass1.03s on negative-infinity final source; full module70pass8skip before that clock-origin-only refinement. Full Ruff/format/mypy481 and suppression registry pass. Runtime files remain byte-identical to d4381877; only tests and this plan change. Failed d4381877 Linux core jobs are retained as evidence; corrected-head full CI and paired native measurements remain required.


### Native Windows fault-hook correction (2026-10-30)

Manual d4381877 Windows3.13 core job124236426446 failed all three closed-input error-partition cases with TypeError: fault hook `closed_pipe_write` rejected the actual `overlapped=True` keyword from Win32 WriteFile before injecting its intended OSError. This is a confirmed test-hook signature defect, not evidence against runtime error handling. Correct the hook to two positional arguments plus keyword-only overlapped=False, and require true on its Windows branch. Keep all three native error-code cases and existing process/pipe cleanup assertions; no runtime changes. Their retained native CI failures are the RED; local POSIX controls and statics supplement, but cannot replace, fresh native Windows CI. The JUnit execution guard correctly rejects these failing cases. Narrow independent review and corrected-head full normal/manual CI remain required.


Verbatim Windows hook cross-family closure (44.58 s):

```text
**Verdict: PASS, no blocker.** I read the code only. I ran nothing, edited nothing and delegated nothing.

1. **Does the hook match the real call sites?**
   - On Windows, the runner calls `_winapi.WriteFile(self._stdin.descriptor, data, overlapped=True)` at `weft/core/runners/subprocess_runner.py:1324`. The new hook signature `(_handle, _data, *, overlapped=False)` accepts two positional arguments plus `overlapped` as a keyword, so it matches.
   - On POSIX, the runner calls `os.write(self._stdin.descriptor, data)` with two positional arguments at `subprocess_runner.py:894`. `overlapped` falls back to its default `False`, and the Windows-only check doesn't run. This leaf is unaffected.
   - This is the only `WriteFile` / `os.write` call in the runner. `_Forwarding` (`tests/core/test_subprocess_runner.py:157`) only replaces the runner module's own `os` / `_winapi` names. Everything else passes through to the real modules, so nothing else is intercepted.

2. **Does it explain the native RED?** Yes. The old `*_args` hook had no `**kwargs`, so `overlapped=True` raised a `TypeError` before the hook could raise its injected error. A `TypeError` isn't caught by the `except OSError` at `:1327`, so it skipped `_write_failed` and all three Windows cases failed without the injected error ever being raised. That's a test-hook defect, not a production one.

3. **Does the error split still reach production code?** Yes. Once the call binds, the injected `_winerror(code)` (`:204`, which sets `winerror` positionally) reaches `_write_failed` (`:1353`):
   - 109 (broken pipe) and 232 (no data) close stdin. The child then sees EOF, so the expected `"done\n"` / `status == "ok"` holds.
   - 5 (access denied) re-raises `exc` itself, which is what `caught.value is failure` asserts.
   - `write_attempted` is set before the raise, so `assert write_attempted.is_set()` still proves the hook ran.

4. **Does the new `assert overlapped` hold up?** Yes, it's a sound check. If it fails, an `AssertionError` escapes the `OSError` handler and the test fails loudly rather than passing falsely. It also matches the sibling hook `observed_write_file` (`:2053`), which already relies on `overlapped`.

5. **Is anything outside the test touched?** No. Production `WriteFile(..., overlapped=True)`, the error split, cleanup, deadlines, load and fallback are all unchanged. No new helper or test was added.

6. **Nit only:** the `_data: bytes` annotation is narrower than the sibling hooks' `bytes | memoryview` / `Any`. It has no effect at runtime because the hook ignores `_data`. No change needed.

Separately: this review only shows that the hook signature now matches both call sites. Whether the three Windows cases pass still depends on the next native Windows 3.13 CI run, and local POSIX runs don't count as proof for Windows. I'm not claiming any result for the full-module run that's still in progress.
```

Disposition: PASS/no blocker. Keep bytes annotation: actual `_StdinStreamState.next_slice` returns bytes or None, and no broader buffer is passed at either intercepted call site. Native independent closure also found no blocker. Final local module70pass8Windows-skipped7.14s; fullmypy481, Windows-targetmypy1, Ruff/format635, registry and docpath checks pass. Retained Windows3.12/3.13 JUnit artifacts each contain all nine required executions: six pass and the three hook cases fail; those are not accepted as a passing native gate. Corrected-head native CI must prove all nine pass.


### Active-command PING proof correction (2026-10-30)

Corrected-head39a3c1c8 normal PR Windows3.13 remaining job124248231382 failed `test_consumer_reactor_responds_to_ping_while_command_work_is_active` before sending PING: the first process_once took3.9528651s versus a1.5s bound derived from a2s child sleep. Manual same-platform remaining passed; that establishes intermittence only. The exact source of3.95s remains unknown. The turn includes strategy startup, queue reads, work-state publication, worker submission and result drain, so this elapsed assertion does not isolate command blocking or PING latency. The unchanged test is present in4370bb0f. Re-read program theory single-arbiter reactor and [CC-2.2]/[CC-2.5]: blocking target work belongs in a broker-free worker lane while the owner processes controls; no1.5s startup contract exists. No contention/load explanation is asserted, and no workers/timeouts are reduced.

Class2 oracle correction: reuse `tests.tasks.sample_targets.signal_ready_and_wait_for_release` in a real command, establish actual child entry, then require the original routed PONG/status fields and empty outbox while the child remains unreleased. Release only after that proof and keep the original5s completion guard and exact result. Finally always release and invoke task cleanup so early assertion failures cannot strand the worker. This replaces the unsupported sleep-ratio proxy with a causal progress assertion; production is unchanged. A negative control runs the actual work synchronously on the owner, which must fail the active-worker/PONG-before-release proof. Validate focused test, qualified negative control, restored owning module, SQLite/PostgreSQL original-load checks, native fresh-eyes and narrow cross-family review before pushing. Both current runs continue while collecting useful native artifacts; no blind rerun.


Verbatim held-command PING cross-family review (91.57 s):

```text
**Verdict: PASS. No blockers.**

I read the delta at `tests/tasks/test_task_execution.py:3644-3710`, the helper code it relies on and the new plan section at `docs/plans/2026-10-29-pr48-reactor-and-test-hygiene-repair-plan.md:1268-1272`. I ran nothing.

**Correctness**
- **Ordering:** The test proves each step in turn. The child really starts (`ready.exists`), the work sits in the worker lane, release hasn't happened and the outbox is empty. A routed PONG then comes back with `task_status == "running"` while the child is still held. That is the [CC-2.2]/[CC-2.5] contract (blocking work runs in the worker lane, the owner handles controls), checked as an order of events with no wall-clock ratio.
- **Helper reuse:** `signal_ready_and_wait_for_release` (`tests/tasks/sample_targets.py:98`) is used as-is. A real command child is kept and no new framework is added.
- **Timeouts:** The 5s readiness wait is only a failure bound. `_drive_consumer_until` (`:3601`) steps `process_once` with a 0.02s `wait_slice`, so polling for the file is bounded and still drives the reactor. The original 5s completion guard and the exact `"reactor-done"` result are kept.
- **Cleanup:** The `finally` calling `release.touch()` (safe to repeat) and then `task.cleanup()` means an early assertion can't leave the child stuck until its own 60s timeout. `task.cleanup()` matches the 36 other uses in the module.
- **Negative control:** Your mutant result fits the code. With inline execution, the first `process_once` blocks until the child's own 60s timeout, then fails at `_has_worker_activity() is True`. So it catches the owner blocking directly, before the 5s bound comes into play.

**Non-blocking notes, most important first**
1. **The child's import doesn't depend on its working directory.** The harness switches into a temp root (`tests/helpers/weft_harness.py:149`) and `working_dir` is None. So `from tests.tasks.sample_targets import …` works because of the editable install (CI runs `uv pip install --system -e` at `.github/workflows/test.yml:59`). The same pattern is already used at `tests/tasks/test_task_stop_sqlite_only.py:56`. If the import ever broke, the test would fail loudly at the readiness bound with diagnostics, not pass falsely. No change needed.
2. **The plan promises more checks than your evidence covers.** `…repair-plan.md:1272` lists "SQLite/PostgreSQL original-load checks" and "narrow cross-family review" as steps before pushing. Your evidence covers the focused 12-pass run, the mutant and the restored full module. If the PostgreSQL original-load run hasn't been recorded, either record it or narrow the plan's wording so it doesn't claim more than was done.
3. **`next(...)` can raise a bare `StopIteration`.** If PONG is missing, the error is unhelpful. This was there before and is unchanged in scope; leave it.

**The plan section is sound.** It keeps the 3.95s delay explicitly unexplained, claims no contention cause or runtime fix, notes that no 1.5s startup contract exists, and treats the bad oracle as a separate problem from the delay's cause. That matches what you described.

As you said, this local review doesn't prove native Windows behaviour. The full normal CI run on the corrected head is still required.
```

Disposition: PASS/no blockers. PostgreSQL evidence is now complete: owning module203passed34.03s; SQLite203passed8.76s, both all markers and normal12 workers. Focused GREEN was one test with12 workers, not12 tests. The actual owner-blocking mutation failed `_has_worker_activity() is True` withFalse in65.58s, before PING; it proves registered worker-lane participation, while GREEN directly proves PONG-before-release. Runtime restored byte-exact. Native independent review also passes. Fullmypy481, Ruff/format635, registry/docpath checks pass after the required nonsemantic string-parenthesis fix. Original3.95s startup delay remains unlocated; no runtime performance claim is made.

Measurement provenance decision: retain manual run41109475299 on39a3c1c8 through all36jobs and native artifacts. This Consumer-test/plan-only correction leaves runtime, extensions, workflow and collector blobs unchanged; successful measurements from39a may be reused with that explicit source equivalence, never called same-head evidence. Require full normal CI on the corrected test head. Superseded known-failed normal run41109485264 may be cancelled after corrected push; retain its failure log and record cancellation, never a pass.
