# Cross-Platform Process Titles with Deferred macOS GUI Registration

Status: completed
Source specs: docs/specifications/02-TaskSpec.md [TS-1], [TS-1.4]; docs/specifications/01-Core_Components.md [CC-2.4]; docs/specifications/07-System_Invariants.md [OBS.4], [OBS.5], [OBS.7], [OBS.8]
Superseded by: none

Class: 5. Proposed observability policy changes plus a deferred native-call lifecycle; hardening applies. The owner authorized implementation on 2026-09-10. Implementation and verification are recorded below; the owner requested a targeted commit.

## Goal

Isolate OS title application in `weft/core/process_title.py`. On macOS, use unmodified `processtitle` for immediate Unix titles, then unmodified `setproctitle` for GUI reporting once a deadline of one second plus a single uniform random offset of zero to two seconds has passed, executed on the task's own drive-loop turn rather than on a separate timer thread. Preserve complete Weft titles through a padded handoff. Skip native calls when the requested title equals the last applied title. Use the same initial/delayed-phase implementation on every platform: Linux and Windows use setproctitle throughout, and only macOS changes backend. Keep formatting and task-state selection in BaseTask. Exiting processes should not incur registration merely to report a title. The deferred registration is a stall moved out of startup, not removed: both native libraries hold the GIL for the duration of the LaunchServices call, and that is accepted.

## Outcomes

- [x] Carry the source-established native findings (GIL held, import-time registration, per-update LaunchServices calls, one-byte NUL reservation) into the spec delta as accepted contract, replacing the earlier probe gate.
- [x] Suppress native calls for unchanged titles so post-handoff LaunchServices traffic tracks actual title changes.
- [x] Promote the reviewed observability delta before production implementation.
- [x] Add one process-local title owner, preserving early Unix visibility and delayed best-effort GUI reporting.
- [x] Integrate all existing title callers and update dependency declarations without a forked wheel.
- [x] Prove capacity, lifecycle, platform compatibility, and load behavior without reducing test concurrency or increasing deadlines to hide stalls.

## Source Documents and Baseline

Plan type: implementation with spec revision. The native blocking question is resolved from upstream source (see Evidence and Limits); slice 1 verifies those citations against the installed versions before any production edit.

Spec baseline: `5114f14ef408091f7eea70560e8ce7eb6fb82358`, for the three governing spec files (01, 02, 07). Promotion baseline: `4bb12b5b1b097b7b31c0eed775a1ff13973497bf`. The governing delta is promoted in the working tree on 2026-09-10: CC-2.4 title policy, OBS.4 native-capacity limitations, and TS-1/TS-1.4 diagnostic schema and exposure. Existing plan backlinks in 01 and 07 were preserved; unrelated spec edits were not replaced.

- [Core components](../specifications/01-Core_Components.md), [CC-2.4]: titles reflect durable state and optional activity.
- [System invariants](../specifications/07-System_Invariants.md), [OBS.4–8]: format, ten-digit short TID, sanitation, allowed vocabulary.
- [Updated dependency repairs](2026-09-09-updated-dependency-suite-repairs-plan.md): related active repair plan; its native samples are evidence, not normative behavior. This plan replaces only the proposed headless dependency delivery approach if implemented, not its unrelated suite repairs.
- [Engineering principles](../agent-context/engineering-principles.md), [writing plans](../agent-context/runbooks/writing-plans.md), [hardening](../agent-context/runbooks/hardening-plans.md), [review loops](../agent-context/runbooks/review-loops-and-agent-bootstrap.md), [testing patterns](../agent-context/runbooks/testing-patterns.md), and [lessons](../lessons.md).

User decisions: shared initial/delayed phases; macOS processtitle then setproctitle. Recommended platform choice after the user reopened Linux selection: retain setproctitle on Linux and Windows; native Unix titles immediately; GUI registration is acceptable and necessary for GUI naming; schedule it uniformly within seconds 1–3 after the first enabled title update; use an isolated module; retain unmodified libraries and use calculated padding at handoff. LaunchServices use itself is an accepted tradeoff, not a blocker to re-litigate. Revision decisions (Van, 2026-09-10): accept the moved stall because ephemeral tasks never register and steady state is where a supervisor stall costs least; add the unchanged-title no-op; run the deferred activation on the drive-loop wake rather than a timer thread; keep two unmodified libraries over a ctypes LaunchServices path; superseded by the later owner clarification below: no separate thread-name maintenance is required.

## Evidence and Limits

Native samples from the repair investigation show macOS children blocked on the startup thread in setproctitle initialization through `_LSApplicationCheckIn` and synchronous XPC. Server-side deadlock has not been established. Staggering reduces a herd; it is not a concurrency cap and does not guarantee a latency ceiling under arbitrary load.

The isolated macOS probe used CPython 3.14.4, processtitle 1.2, and stock setproctitle 1.3.7. It executed the actual BaseTask formatter methods extracted from source without constructing tasks. With maximum context/name/details and known statuses, 71 ASCII bytes cover a complete title. Padding to 72 before stock import passed 25 external-ps/getproctitle checks from each of `init` and `running`; padding to 71 failed at the final byte. No argv restoration was needed. Local reproduction artifacts: `/tmp/weft-title-handoff-test/padded_probe.py`, `/tmp/weft-title-handoff-test/padded-results.json`, and its sibling `weft_probe.py`. These temporary artifacts are evidence aids, not durable test dependencies; recreate the proof in repository tests using production formatting during implementation.

These probes did not prove Activity Monitor display, all launch argv layouts, or load safety. A short title at handoff shrinks stock's discoverable capacity. Padding solves the tested case only when native space is sufficient. The formatter currently bounds context/name/details but does not impose an arbitrary-status cap; derive capacity from all actual call sites, not just TaskSpec's Literal.

Native findings established from the upstream sources below (stock 1.3.7, processtitle 1.2, read 2026-09-10), which the earlier draft left to a probe:

- Stock `setproctitle/__init__.py` calls `getproctitle()` at import on Darwin (fork-safety workaround for upstream issue 113). Import alone runs `spt_setup`, `init_ps_display`, `set_ps_display`, `darwin_set_process_title`, and `_LSApplicationCheckIn`. Padding must precede the import, and any earlier import by another caller has already registered.
- Stock `set_ps_display` calls `darwin_set_process_title` on every invocation. Each call performs `launch_services_init` plus a synchronous `_LSSetApplicationInformationItem` XPC; check-in happens once per process. After handoff, every applied macOS title change is a synchronous LaunchServices call. That is today's behavior, not a regression; the unchanged-title no-op is the only lever this plan has on it.
- Neither library releases the GIL around its native call: no `Py_BEGIN_ALLOW_THREADS` in stock `spt_setproctitle`/`spt_getproctitle` or in processtitle `platformSetProcessTitle`, which also serializes under `g_globalStateMutex`. A deferred registration on any thread stalls every Python thread in the process for the duration of the check-in. Deferral moves the stall out of startup; it does not remove it. Accepted (Van, 2026-09-10): the supervised child does the work, and a steady-state supervisor stall delays control handling and heartbeats, not the work.
- The one extra padding byte is stock's NUL reservation, not an experimental constant. `save_ps_display_args` sets `ps_buffer_size = end_of_area - argv[0]` where `end_of_area` excludes the terminator, and `set_ps_display` writes with `spt_strlcpy(..., ps_buffer_size)`, which reserves one byte. Maximum stock title equals padded length minus one. Encode it as a named NUL-reservation constant carrying this citation.
- Stock `darwin_set_process_title` names the calling thread with `pthread_setname_np` on every update. processtitle sets no thread name, `prepare()` raises on a second call, and `fork_safe_only=True` permanently disables LaunchServices for the process.
- processtitle is authored by gershnik, who also rewrote setproctitle's macOS LaunchServices layer (`darwin_set_process_name.c`); py-setproctitle's primary maintainer is dvarrazzo. Its README positions it as similar to py-setproctitle without backward-compatibility constraints and supports Python 3.10+. It has no deferred-GUI option. An upstream `prepare(defer_gui=True)` plus a later activation call would collapse the two-library handoff into one writer; that is the durable direction. An upstream request is optional follow-up, not an implementation prerequisite; do not publish without explicit authorization.

Upstream sources, to be re-read against the installed versions and pinned by citation in the code comments:

- [processtitle darwin.cpp](https://github.com/gershnik/processtitle/blob/master/src/cpp/darwin.cpp): fork-safe preparation skips GUI permanently; default first update registers; no thread name.
- [processtitle clobber.cpp](https://github.com/gershnik/processtitle/blob/master/src/cpp/clobber.cpp): relocates later argv and environment; owns native capacity.
- [processtitle main.cpp](https://github.com/gershnik/processtitle/blob/master/src/cpp/main.cpp): preparation once per process; global mutex; GIL held.
- [setproctitle wrapper](https://github.com/dvarrazzo/py-setproctitle/blob/master/src/setproctitle.c): lazy `spt_setup`; GIL held.
- [setproctitle spt_status.c](https://github.com/dvarrazzo/py-setproctitle/blob/master/src/spt_status.c): buffer sizing and per-call `darwin_set_process_title`.
- [setproctitle darwin_set_process_name.c](https://github.com/dvarrazzo/py-setproctitle/blob/master/src/darwin_set_process_name.c): once-per-process check-in, per-call information item, `pthread_setname_np`.
- [setproctitle __init__.py](https://github.com/dvarrazzo/py-setproctitle/blob/master/setproctitle/__init__.py): import-time `getproctitle()` on Darwin.

## Current Structure and Edit Surface

| File | Current responsibility / planned action |
| --- | --- |
| `weft/core/tasks/base.py` | Constructor eagerly checks/imports setproctitle before task_initialized. `_update_process_title` selects activity, formats, updates process and calling-thread titles. Keep task policy/formatting here; delegate OS effects. Integrate in shared process_once and wait_for_activity templates, covering both run_until_stopped and manual drivers. Tick after a live turn; cap the actual wait independently of subclass next_wait_timeout hooks. |
| `weft/core/deferred.py` (new) | Own `get_setproctitle()` and `get_processtitle()`: function-local optional imports returning the imported module. This is the only module where deferred imports are permitted; every other import in the codebase is top-level and eager, with no try/except guards in feature modules. No import-time native initialization and no title policy. This applies the owner's separately established repository import rule; migrating unrelated imports is outside this slice. |
| `weft/core/taskspec/model.py`, `docs/specifications/02-TaskSpec.md` | Add nullable StateSection.process_title_error; synchronize state serialization/reporting consumers and tests. |
| `weft/core/process_title.py` (new) | One internal process-local owner for native adapters, deadline, latest applied title, unchanged-title suppression, handoff, and failure containment. Exposes `set_process_title`, `seconds_until_due`, and `tick`. The task main/drive thread owns title updates; no separate thread-name API or bookkeeping. No queues, task objects, threads, or timers. |
| `weft/_constants.py` | Own shared segment limits, computed capacity, minimum delay 1.0 seconds and jitter span 2.0 seconds. Also update task-field ledgers when removing `_setproctitle_module`. No new env vars. The dedicated optional runtime state diagnostic is the only TaskSpec schema addition. |
| `weft/core/monitor/task_monitor.py` | Worker cloning currently resets `_setproctitle_module`; remove obsolete per-task library state consistently, without changing clone ownership. |
| `weft/core/tasks/interactive.py` | Read its abstract title seam; retain signature unless an actual required change is demonstrated. |
| `pyproject.toml`, `uv.lock` | Add processtitle for Darwin only; retain the existing stock setproctitle dependency on all current platforms. Resolve compatible tested versions; preserve unrelated dependency updates. |
| `tests/core/test_process_title.py` (new) | Internal scheduling/lifecycle contract and real native child tests. |
| `tests/tasks/test_task_observability.py`, `tests/cli/test_manager_proctitle.py` | Existing formatter/optional-dependency tests; retain full manager:running expectation and add actual BaseTask integration. |
| `tests/core/test_manager.py`, `tests/core/test_client.py`, `tests/cli/test_cli_manager.py` | Existing lifecycle/load regressions; reuse, do not weaken expectations. |
| All three governing specs and `docs/plans/README.md` | Spec delta, reciprocal links, ownership mapping, plan index. |

Read before edits: BaseTask constructor and title methods; all title call sites; monitor clone initialization; field-ledger enforcement tests; installed xdist remote module; both dependency native initializers. Comprehension checks: why does task_initialized currently wait for title setup? Which task main/drive thread owns the update? Which native buffer remains discoverable after processtitle preparation? Why does a deferred registration on any thread still block Python task execution, and why does running it on the drive-loop turn make the stall point deterministic?

## Invariants and Scope

Keep `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log` as the only durable execution path. TIDs, state transitions, reserved policy, immutable spec/io, runtime-only queues, execution result meanings, registry semantics, CLI flags, and task timeouts do not change. Runtime state gains the optional process_title_error diagnostic described below. No broker reads/writes in the new module. Title failures never change task outcome or replace lifecycle evidence.

Preserve current formatted title vocabulary and task enable_process_title opt-out. Linux and Windows retain existing setproctitle behavior and native title visibility. Space padding is a transient native handoff operation, not a new task-title format. A successful same-process handoff must replace padding before it returns; failures follow the diagnostic table and may leave padding visible. Do not shorten full state strings to accommodate the adapter.

No ctypes calls, no separate thread-name maintenance, no patched native dependencies, no registration thread or timer, no fork publication, generic scheduler, new subprocess/helper service, OS resource tuning, test-worker reduction, or broad BaseTask refactor. Early third-party stock imports cannot be undone: this module controls Weft's calls only. No fork-without-exec support is introduced; Weft continues to use spawn. Audit inherited PID state without claiming that resetting Python locks makes initialized native libraries fork-safe.

## Proposed Spec Delta

Promotion strategy A for all three existing spec files: add requirement text first, with no premature implementation mapping to the new file; add mapping plus reciprocal code references together with implementation. No new planned-spec file or scanner classification is needed. After promotion this section is historical review material only.

### 01-Core_Components.md [CC-2.4]

Replace the process-title bullet with:

> - process titles stay shell-friendly and reflect durable state plus optional live detail. OS title application is best-effort process-local observability; it does not own task state. Disabling process titles for a task prevents that task from initializing title support or requesting updates. An ordinary request whose formatted title equals the last successfully applied process title does not call the process-title setter again. This memoization does not suppress due backend activation or its mandatory unpadded title application. Separate thread names are not maintained; the task main/drive thread owns title updates. On macOS, when stock setproctitle has not already initialized in the process, Weft applies Unix titles without LaunchServices on the initial path and schedules one GUI activation per process, due at the first enabled update's monotonic time plus one second plus a uniform random offset from zero to two seconds. Due activation runs at the end of a still-live, enabled, owner-confined task turn at or after the deadline, after normal control and result processing, and never on a separate thread; the shared wait boundary limits sleep to the activation deadline, and a task busy in a long synchronous call activates at the end of its next completed turn. The task drive-owner thread is responsible for native updates. Later updates do not redraw or postpone the deadline. Activation uses the latest requested title even when no further update arrives. The activation and every later macOS title change are synchronous LaunchServices calls that hold the interpreter lock for their duration; this stall is accepted and is moved out of task startup, not removed. A process that exits before its deadline neither registers nor waits; normal interpreter shutdown may be delayed by an activation already in progress. Forced termination can interrupt the process without completing the native call. GUI failure does not fail a task or trigger repeated attempts. Detected process-title failures are exposed through state.process_title_error and do not fail task execution; failures the library swallows are not detected. If another caller has already initialized stock setproctitle, Weft retains it as the sole writer and does not schedule a second activation; Weft cannot retroactively defer registration performed by another caller. Linux and Windows use setproctitle for initial and subsequent updates with no deferred phase. The shared module owns both phases, with a delayed transition only where the backend changes. Other supported platforms retain their existing direct best-effort native behavior.

### 07-System_Invariants.md [OBS.4]

Append directly after the existing format rule:

> Context and task-name segments are limited to 8 and 20 ASCII characters respectively; the short TID follows OBS.5; optional details are limited to 15 ASCII characters. When the native buffer has sufficient capacity, the adapter preserves complete titles within the formatter's supported bound across initialization and backend handoff, including the longest supported status and details. Native buffer limitations may cause silent truncation; Weft does not probe capacity or verify titles by runtime read-back. The formatter and native adapter share the capacity calculation. Temporary trailing spaces used during native buffer discovery are not task metadata and are replaced by the current formatted title before a successful handoff returns. A failed import or setter may leave padding visible, an accepted best-effort limitation. GUI registration timing follows [CC-2.4]; delayed GUI visibility does not delay the initial Unix title.

Production status inventory (2026-09-10, every `_update_process_title` call site plus the `StateSection.status` Literal): `init`, `created`, `spawning`, `running`, `paused`, `draining`, `stopping`, `completed`, `failed`, `timeout`, `cancelled`, `killed`; the longest is nine characters. The formatter applies no status cap; the shared capacity constant derives from this inventory, and formatter tests cover the supported maximum-length statuses. Do not encode 71 as an independent magic number, and encode the extra handoff byte as the stock NUL reservation, not as a version-specific characterization.

## Platform Policy and Linux Compatibility

Recommended policy after the user considered both Linux options on 2026-09-10. This preserves shared phases without migrating a working Linux backend:

| Platform | Initial phase | Delayed/subsequent phase | Timed transition |
| --- | --- | --- | --- |
| macOS | processtitle, fork_safe_only=True | stock setproctitle | Once, on the task's drive-loop turn at or after 1 + uniform(0,2) seconds |
| Linux | setproctitle | setproctitle | None: same backend throughout |
| Windows | setproctitle | setproctitle | None: same backend throughout |
| Other existing platforms | Existing setproctitle path | Same backend | None |

Use one concrete module, not a backend plugin framework. Resolve this small platform policy once. Shared code owns lazy initialization, latest-title state, error containment, and updates. A differing delayed backend opts into the one-shot deadline. Do not create deadlines on Linux/Windows merely to call the same setter twice. Initial and delayed operations are internal phases, not new caller obligations or public APIs.

The alternative processtitle Linux backend uses PR_SET_MM_MAP for the full command line and PR_SET_NAME for the calling-thread name. It returns success if either succeeds, so True alone does not prove full Weft identity visibility; the short name is limited to 15 bytes. There is no argv-clobber fallback in this backend. Current Linux kernel source handles PR_SET_MM_MAP before the generic CAP_SYS_RESOURCE check when CONFIG_CHECKPOINT_RESTORE is enabled; do not incorrectly claim ordinary users always need elevated privileges. Kernel configuration or syscall filtering can still prevent the full-title operation.

Linux processtitle remains an alternative outside the recommended scope. To adopt it later, compare real unprivileged host and supported container runs, including a deliberately denied PR_SET_MM_MAP call, and inspect /proc/PID/cmdline plus ps. Report any lost full-title visibility rather than raising privileges or treating True as proof. Windows tests preserve its existing best-effort behavior; selecting the same dependency does not establish new Task Manager guarantees.

Sources: [Linux backend](https://github.com/gershnik/processtitle/blob/master/src/cpp/linux.cpp), [kernel prctl implementation](https://github.com/torvalds/linux/blob/master/kernel/sys.c), [PR_SET_NAME limit](https://man7.org/linux/man-pages/man2/PR_SET_NAME.2const.html).

## Module Design and Lifetime

Internal entry points:

- `set_process_title(title: str) -> str | None`: apply a changed title; return a caught error message, or None when no new error occurred (including a memoized no-op).
- `tick() -> str | None`: attempt the due one-time handoff; return a caught error message, or None when no new error occurred (including not due).
- `seconds_until_due() -> float | None`: remaining deadline, or None when no activation is pending; no imports or native work.

BaseTask owns error storage. After set_process_title or tick, assign a non-None result to state.process_title_error. None means no new error, not an instruction to erase an earlier one. This field records the last detected title error for that task's lifetime; it is not a backend health flag. No module-level diagnostic, error getter, recovery tracker, or per-operation error history. This deliberately avoids making a no-op indistinguishable from successful recovery.

The small process-local lifecycle consists of the current setter, whether initialization has been attempted, latest requested title, last successfully applied title, optional activation deadline, and one lock for serializing native writes from tasks sharing a PID. A disabled setter can be represented by None after an initialization failure; no separate seven-state enum or generic state-machine framework is needed. Test the lifecycle transitions, not a particular representation. Most recent request supplies the handoff title. No task references, error cache, title queue, timer, worker thread, or fork-recovery machinery. Spawn starts fresh.

Lazy imports occur only through deferred.py. Initialize on first enabled use, memoize successful titles, and bypass memoization only for the mandatory unpadded write during handoff. Clear the activation deadline before attempting handoff so failure does not create a per-turn retry loop.

Deferred mechanism: shared owner-confined turn and wait templates, not run_forever. In BaseTask.process_once(), after _process_reactor_turn() returns, recheck stop intent and run tick only if the task remains live and has process titles enabled, while the existing turn-ownership guard is active. A disabled task never touches the module; in the Manager process the enabled Manager task drives its own loop, so nothing is lost by the gate. Pending termination, control handling, and worker-result application precede optional GUI work. Do not activate during a stopping turn. No lifecycle lock is held across the native call. In wait_for_activity(), within the existing owner-checked wait wrapper, take min(requested timeout, seconds_until_due()) when a live enabled task has pending activation; None means unbounded, and an overdue deadline yields zero. Then invoke the existing _wait_for_reactor_activity so its other wake limits remain intact. Never run native activation from the wait method itself.

These templates serve run_until_stopped (called by run_forever), Consumer.run_work_item's manual loop, and other supported direct drivers. Subclass next_wait_timeout overrides cannot bypass the cap. An idle task wakes at eligibility and activates after its next live turn; a busy task does so at the end of its next completed turn. One wait without a following turn does not promise activation. Test both standard and direct drivers, plus stop arriving on the same wake. This does not introduce a thread or timer.

On Linux and Windows, lazily import stock setproctitle and use it for every update, retaining existing optional/native-failure behavior. Both paths reuse the common entry point and process-local owner. On Darwin, prepare processtitle with fork_safe_only=True once, write the initial Unix title, and draw one uniform jitter. At the deadline, pad the latest title to the shared maximum plus the one-byte stock NUL reservation, import stock setproctitle, and immediately apply the latest unpadded title. Serialize buffer mutation with the handoff; once native ownership transfers, never alternate argv writers. There is no cross-thread native request queue or activation coalescer. Native calls run only on the responsible task drive thread. `_set_activity` (base.py, the activity edge that calls the title setter) is the entry most likely to run off-owner; slice 1 lists every `_update_process_title` and `_set_activity` caller with the thread it runs on. Off-owner observations, if any are found, use the existing task-result delivery path and are applied on its next turn. The module serializes with its lock and does not assert thread identity, so a missed caller degrades to a stall rather than an exception on a best-effort path. On failure, select the still-safe owner based on whether stock initialization changed native state; do not blindly resume processtitle after a partial stock import. No retry loop. If stock setproctitle was already imported before the first Weft update on macOS, keep stock as the sole writer and mark GUI initialization as already attempted; do not prepare processtitle or claim registration was deferred. Verify this branch against the stock version and partial-import behavior in slice 1.

A pending registration does not keep an otherwise exiting process alive. No join in task cleanup or atexit; a pending deadline is dropped at exit. A native call already in progress can delay normal interpreter shutdown with no promised latency bound; forced process termination need not complete it. A quiet long-lived task activates because its wait is bounded by the deadline, not because another event arrives. A disabled task's update is a no-op, not global cancellation of an already active enabled owner's deadline.

Thread ownership: the task's main/drive thread is responsible for title changes and due activation. No separate thread names are maintained; remove the explicit BaseTask setthreadtitle call and do not replace it with `_thread.set_name`, ctypes, a per-thread cache, or another naming API. Incidental thread naming performed inside the selected library is accepted and does not create a separate Weft contract.

Memoization: compare each formatted title with the last successfully applied process title. If equal, skip the process-title setter. Update the cache only after successful application; a failed update must not suppress a subsequent request. Due backend activation is a separate one-time operation: it must still occur when the title text is unchanged, and must apply the latest unpadded title after initialization. That mandatory handoff write bypasses ordinary duplicate suppression. Do not reset or postpone the deadline on duplicate requests.

Temporary padding is explicitly accepted, including visibility while stock initialization is blocked. Restore the latest unpadded title as soon as initialization returns. Do not add machinery to hide padding or claim it cannot appear in Activity Monitor.

## Resolved Native Findings and Remaining Checks

The blocking question is resolved from source, not by a probe: both libraries hold the GIL for the native call, stock registers at import, and every later stock update is a synchronous LaunchServices call (see Evidence and Limits). The moved stall is accepted. No automatic native shim, private function calls, helper process, or registration thread is authorized by this plan.

Slice 1 still verifies the cited lines against the installed wheel versions and pins the citations in code comments; if an installed version differs from the cited behavior, stop and re-read before implementing. Slice 1 also confirms the status inventory recorded under OBS.4 against the call sites, and confirms every enabled title caller uses the shared process_once/wait_for_activity templates, including Consumer.run_work_item and manual drivers; tests must exercise those paths rather than assume run_forever owns them.

Also test small original argv/environment capacity, prior processtitle.prepare by another library, early stock import, partial native initialization, and UTF-8 sanitation. The supported launch cases are checked by slice-1 real-child padding tests at maximum title; this is not a proof of capacity for every possible process environment; runtime performs no capacity detection and no read-back verification. The module sets, then catches and reports what the library raises.

## Tasks and Stop Gates

1. **Contract inventory and native regression tests.** Read the files above, verify the cited upstream lines against the installed versions, confirm the status inventory and the drive-loop coverage of every enabled caller, produce the off-owner call-site list for `_update_process_title` and `_set_activity` with the thread each runs on, migrate the padded experiment into durable real-child tests using production formatting, and prepare any upstream request as optional follow-up; publishing it is not part of this implementation. Files: new test module and this plan only; isolated environments allowed. Red proofs: unpadded handoff truncates by exactly one byte; early stock import enters the GUI path; an unchanged title makes no native call. Done when the module can be implemented without guessing native ownership. Stop and revise if an installed version contradicts a cited finding, a second execution service or a registration thread is proposed.
2. **Spec-promotion slice.** After slice 1 and independent delta review, apply exact approved text to the three specs, add backlinks, record promotion baseline, and update this plan's decision record. No production code references new requirements before promotion. Verify metadata/spec hygiene and traceability. Stop for any unreviewed contract change, including accepted GIL stalls.
3. **Module and constants.** Implement the approved mechanism in the new module; extract shared numeric segment limits from BaseTask into constants without changing formatted output; add platform-conditional dependency and lock changes per the policy table. Share initial/subsequent dispatch and error handling; create a deadline only for the differing macOS delayed backend. Bound wait_for_activity by seconds_until_due and call tick at the end of each still-live, enabled process_once turn. Add deterministic lifecycle tests with injected clock/random/native boundary, including unchanged-title suppression. Keep actual lifecycle/process/native tests real. No pool, broker, or generic plugin abstraction. Stop on unexplained native errors, any registration thread or timer, or unsafe dual writers. Done when unit contracts and native handoff regressions pass.
4. **Integrate task callers.** Delegate BaseTask OS updates; remove obsolete per-task library cache and update monitor clones and field ledgers together. Preserve opt-out, error policy, full titles, and task main-thread ownership. Use real BaseTask and WeftTestHarness tests, including a quiet process blocked in its wait that still activates at its deadline, a Manager (which overrides `next_wait_timeout`) that activates on time, a short-lived child that never registers, multiple task objects in one PID, and shutdown with a pending deadline. Stop if cleanup gains a title join, a thread appears, or task outcomes depend on GUI success. Independently review this slice.
5. **Stress, reconcile, and close.** Run the targeted 24-worker cohort and both full default suites with stock dependencies. Verify Activity Monitor on a surviving owned task separately from Unix ps; report actual evidence rather than assuming the GUI name from a successful return. Reconcile spec mappings, module/function docstrings, plan/index, and the related repair plan's dependency section. Run final independent review. Land only an explicit reviewed file list when committing is requested; never stage unrelated repair/notes wholesale.

## Test Matrix

| Contract | Firing proof |
| --- | --- |
| Initial Unix title before GUI | Real child, external ps, stock module absent before deadline; initial task_initialized/result progress real. |
| One deadline in [1,3] seconds | Inject monotonic clock and jitter endpoints 0/2 plus interior; assert no attempt before eligibility, one attempt after, no reschedule on updates. The drive-loop wait is bounded by the deadline even when a subclass hook returns a longer timeout or `None`. Wall-clock tests allow lateness, never require an exact wakeup. |
| Unchanged title no-op | Counting native boundary: repeated identical status/activity requests make one native call; a changed title makes exactly one more; failed application does not populate the cache. An unchanged title still activates at its deadline and is applied unpadded after handoff; later identical requests are skipped. |
| Quiet survivor / short-lived exit | Parent-child acknowledgements and a controlled clock prove activation without later updates, exit without waiting when the deadline is pending, and normal shutdown behavior during an in-flight activation, without asserting completion under forced termination; no sleep-dependent correctness. |
| Accepted stall | Real child with an external heartbeat: the supervisor pause coincides with activation at a loop boundary and the supervised child's own progress continues; report the duration, do not bound it. |
| Padding / complete title | Derive maxima from production format/status inventory; full ps and getter equality at max and short titles, repeated shrink/grow, with and without details; verify maximum-length round trips with the calculated padding; if a newer stock version changes buffer sizing, update the citation and constant rather than failing a product-correctness test for improved behavior. |
| Main-thread ownership | Standard and direct drivers apply titles and activate on the task drive owner; multiple task owners in a PID serialize native writes with one module lock; no extra worker or title queue. |
| Failures and opt-out | Missing first/second dependency, prepare already called, false native return, partial import, registration error, short native capacity (truncation only, nothing raised, nothing detected); no task failure, no repeated attempts, no unsafe fallback. Disabled tasks never initialize. |
| Process and thread scope | Multiple objects share one deadline; spawn child starts fresh; process exit doesn't await pending title work; no registration thread exists; updates and activation execute on the responsible task main/drive thread; no separate thread-name calls are required. No new fork guarantee. |
| Platform and dependency | Real Darwin handoff, Linux existing native titles, Windows existing native behavior; every platform policy row fires. Clean installs select processtitle on Darwin only, retain stock elsewhere, and create no Linux/Windows timer or processtitle import. |
| xdist coexistence | Inspect actual installed worker import path; test with stock already imported and in standalone spawned tasks. On macOS, keeping setproctitle installed keeps xdist's optional import active; do not claim this plan eliminates that independent startup cost. On Linux, retain the existing optional xdist title behavior; no dependency removal is planned. |
| GUI visibility and load | Actual Activity Monitor observation for owned survivor; bounded burst across 24 workers with results, startup/exit evidence, no orphaned owned descendants, registration-time distribution and native samples on stalls. |

## Verification Commands and Evidence

Load `. ./.envrc` before repo commands. Native tests run directly in isolated child interpreters so xdist cannot initialize stock first. Parent owns/reaps all child processes even on assertions/timeouts. Tests for scheduler randomness use controlled values; stress uses the real distribution. No network services except the canonical PostgreSQL wrapper's owned backend.

Planning-only gates:

```bash
uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -n 0
```

Per implementation slice:

```bash
uv run pytest tests/core/test_process_title.py -n 0
uv run pytest tests/cli/test_manager_proctitle.py tests/cli/test_cli_manager.py tests/core/test_manager.py tests/core/test_client.py -n 24 --maxprocesses 24
PYTEST_XDIST_AUTO_NUM_WORKERS=24 uv run pytest-pg -- tests/cli/test_manager_proctitle.py tests/cli/test_cli_manager.py tests/core/test_manager.py tests/core/test_client.py -n 24 --maxprocesses 24
```

Bound the stress cohort by selecting the original failing node IDs if these full modules exceed the agreed limited run; record the exact selection and run once per backend before expanding on new evidence. The -n 0 native test command isolates initializer order; it is not a reduction of suite or stress parallelism.

Final implementation gates:

```bash
uv run pytest
uv run pytest-pg
uv run ruff check .
uv run ruff format --check .
uv run mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
```

Run the repository's configured backstitch check discovered from its current config/entry point; record the exact invocation before spec promotion and rerun it during reconciliation. Do not substitute prose for a required traceability gate. Record commands/results from the final state. Independent lint/type reads may run together; dependency mutations, native ownership tests, and slice promotions are sequential. Avoid simultaneous full suites during initial native diagnosis because it obscures causal evidence.

## Observable-Difference Register

Classes follow the guard-and-custody plan: **A** owner-decided removal; **N** no observable difference in any shipping configuration; **C** observable difference with no loss of capability; **D** diminution, which must be fixed in this plan.

| Difference | Class | Evidence / disposition |
| --- | --- | --- |
| macOS: no Activity Monitor name for the first 1–3 s; none ever for tasks exiting sooner | A | Van 2026-09-10. Activity Monitor samples about once per second, so ephemeral tasks were never visible there. |
| macOS: registration stall moves from before `task_initialized` to a drive-loop turn at least 1 s later | C | Same stall, GIL held either way; no longer on the evidence-publishing startup path. Van 2026-09-10. |
| macOS: unchanged titles no longer reach LaunchServices | C | Fewer synchronous XPC calls; visible titles identical. |
| No separate thread-name maintenance | A on macOS pre-handoff; N elsewhere | Owner decision. Stock already names the calling thread inside its setter (`prctl(PR_SET_NAME)` in `set_ps_display` on Linux, `pthread_setname_np` in `darwin_set_process_title` on macOS), so the explicit `setthreadtitle` call was redundant; Linux `comm`, `pgrep`, and `pkill` behavior is unchanged. Only the macOS pre-handoff window has no thread name. |
| macOS: padded title may be visible during stock import, including a stalled registration | A | Explicitly accepted temporary effect; overwritten immediately when initialization returns. No sampling-interval guarantee. |
| Normal shutdown may wait for an in-flight activation | C | No latency bound for the native call. Forced termination may interrupt the process; pending deadlines are dropped. |
| Linux and Windows | N | Same backend, no deadline, no new imports. |

## Rollout, Rollback, and Runtime Signals

Ship the module, caller changes, tested stock dependencies, and spec mappings together. No queue/storage migration or one-way persisted door. Existing and new task processes may coexist; native initialization is irreversible within a live PID, so changing code or dropping a pending deadline cannot uninitialize LaunchServices. Roll back by reverting only this slice and restarting owned processes through normal lifecycle controls, not by switching native writers in a live PID. The existing enable_process_title=False setting is available if title support must be disabled; do not introduce a new config surface here.

Observe real startup-to-task_initialized and result progress, process lifetime, registration timing, full Unix/GUI titles, and owned-process cleanup. Sampling evidence should identify the blocked native call if a stall returns. Report xdist registration separately from Weft child registration. Never log raw environment or full original command lines while gathering title diagnostics.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

No implementation deviations yet. A feasibility-driven architecture change requires a dated disposition and re-review before production work.

## Review and Execution Record

- Author fresh-eyes review (2026-09-10): corrected the existing observability-test path, retained arbitrary-status inventory instead of silently declaring a universal 71-byte limit, and distinguished accepted LaunchServices use from unproven background responsiveness. Added Linux compatibility evaluation without changing implementation scope.
- Independent initial plan/delta review (2026-09-10): same-family native reviewer PASS as a feasibility-gated plan. A Claude CLI was found but a usable alternate review session was not verified. Accepted both suggestions: explicit already-imported stock ownership and separation of capacity contract from version-specific padding characterization. Final scoped review PASS after the platform recommendation and already-initialized exception were synchronized. Clarified that PR_SET_MM_MAP describes the alternative library, not the selected Linux backend.
- Revision (2026-09-10): user proposed Linux processtitle, then reopened whether Linux/Windows should both retain stock. Recommendation: retain stock on both, share all common phases, and restrict the new backend to macOS. Linux alternative and its compatibility concerns remain documented. Updated policy, delta, dependencies, and tests together.
- Second-review finding: qualified proposed CC-2.4 for already-initialized stock so the no-second-activation branch does not contradict the timing contract.
- Independent review and revision (2026-09-10, Claude, separate session, Van's dispositions): replaced the native probe gate with source-established findings (GIL held by both libraries, import-time registration, per-update LaunchServices calls, one-byte NUL reservation); recorded the moved stall as accepted; added the unchanged-title no-op; switched the deferred mechanism from a timer thread to the drive-loop wake; corrected the exit claim; added the observable-difference register and the pre-handoff thread-name fill; corrected the processtitle maintainer statement; recorded the upstream deferred-GUI request as the durable direction. The thread-name fill in this entry is superseded by the owner clarification below.
- Implementation readiness (superseded 2026-09-10): the owner revisions below required a fresh scoped review, recorded after them; no background responsiveness claim is made.
- Planning verification (2026-09-10): metadata and spec hygiene command passed 8 tests after final review edits; git diff --check passed for the touched tracked documents.

### Owner clarification after implementation-readiness review

- Each task's main/drive thread is the responsible title owner. Separate thread naming is not required; remove its planned native calls and tests.
- Basic memoization of the last successfully applied process title is required. The one-time backend activation and unpadding write are explicit exceptions, not a general cache invalidation framework.
- Temporary padded title visibility is accepted, even during a blocked import.
- Remaining review findings (shared drive/wait integration, partial-initialization/capacity failure policy, and contradictory concurrency wording) were dispositioned in the scoped review recorded at the end of this plan. The slice-1 off-owner call-site list remains open.

## Deferred Accessor and Nonfatal Diagnostics (owner revision)

This section resolves the remaining import and error-policy ambiguity and supersedes earlier references to choosing a fallback during implementation.

`weft/core/deferred.py::get_setproctitle()` performs `import setproctitle` inside the function and returns the module. Python's import cache already provides reuse; no generic lazy loader or second module cache is needed. Add the optional-dependency comment required by import conventions. Importing deferred.py or process_title.py must not import stock. On macOS the accessor is called only after padding at activation, except when stock is already fully initialized externally; on other platforms it is called on first use. Use sys.modules only to detect fully initialized external stock without triggering its import. The accessor propagates ImportError; process_title owns best-effort failure handling. Test absence before access and identity on repeated access. This does not prevent xdist or another library from importing stock independently.

Task main thread means the task's drive-owner thread, not necessarily threading.main_thread(). Construction may perform the initial title on the constructing thread before a drive owner exists; once ownership transfers to the driver, that owner performs later updates and activation. The constructor's title must not be postponed past task_initialized merely to wait for run_forever. No separate thread names are required. Different task owners can exist in one process: one module lock serializes native mutation and the most recent successful call determines the process title. A process has one title, not one per thread. Do not hold the task lifecycle lock while acquiring the native-title lock or calling native code.

Add StateSection.process_title_error: str | None = None. It records the most recent detected title error returned to this task, initially None. BaseTask stores only non-None errors returned by set_process_title or tick; normal success, disabled calls, and no-ops do not erase it. It is Weft-owned diagnostic state under TS-1.4, separate from state.error, status, and return_code. Do not copy a process-global error into unrelated task objects. No extra lifecycle event or synchronous broker write. Log newly returned errors using the existing logger, without adding a second diagnostic-change cache. Persisted diagnostics and user-facing warnings must not expose raw environment, original argv, or tracebacks. Native-boundary debug logging may retain exception tracebacks for diagnosis, without logging environment or argv.

| Failure point | Native behavior | Diagnostic |
| --- | --- | --- |
| Initial backend unavailable/prepare fails | Disable title support for the PID; no eager stock fallback on macOS | Retain initialization error |
| Ordinary setter fails | Preserve last-success cache; a later explicit update may retry, with no automatic retry timer | Record returned error in the calling task; later success leaves the historical diagnostic intact |
| Stock import/initialization fails after handoff begins and ownership is uncertain | Stop all further native writes for PID; do not alternate writers or attempt rollback | Retain initialization error; possible padded title remains accepted |
| Stock import succeeds but applying unpadded title fails | Stock remains sole writer; clear activation deadline; later explicit update may retry | Record returned error in the calling task; later success leaves the historical diagnostic intact |

No pre-detection and no read-back verification (Van, 2026-09-10: that is over-armoring). The module sets, then catches and reports what the library raises. Failures the library swallows, including silent title truncation when native capacity is short, cannot be reported as detected errors; document this diagnostic coverage limit and test false returns/exceptions where exposed.

### Additional exact proposed spec text

02-TaskSpec.md [TS-1]: add optional `process_title_error: string | null`, default null, to the state schema alongside error. [TS-1.4]: add: "state.process_title_error is a nonfatal process-title diagnostic owned by Weft. It does not change status, return_code, or state.error. It is initially null and records the latest detected title-support failure returned to this task. Success or a no-op does not clear this historical diagnostic; it is not a statement of current backend health. Native failures not exposed by the backend cannot be inferred from a successful setter return."

01-Core_Components.md [CC-2.4]: merged into the replacement bullet under Proposed Spec Delta (diagnostic exposure, turn-end activation, wait-boundary cap, drive-owner responsibility). Promote one bullet, not two.

Implementation and verification additions: add deferred accessor tests, schema default/round-trip/error coexistence tests, and a real task completing successfully while a controlled native boundary fails and reports process_title_error. Inspect all explicit state serializers (including TaskSpec summary and status/PONG/log projections) and update the intended diagnostic surface together; do not assume a new Pydantic field automatically reaches hand-built dictionaries. Preserve execution error and return code in that regression. Add the third spec backlink and implementation mapping during promotion. New owner revisions require a fresh scoped review before declaring the plan implementation-ready.

### Scoped review of the owner revisions (2026-09-10, Claude)

Verified against code: `process_once` and `wait_for_activity` are owner-confined templates that release the lifecycle lock before the turn or wait; no task class overrides them or the run loops; Consumer's manual loop uses both; no title call site in BaseTask or Manager sits inside a lock; `_report_state_change` dumps the whole TaskSpec so `process_title_error` reaches the task log automatically; stock already names the calling thread inside its setter on Linux and macOS, so removing `setthreadtitle` is class N there.

Dispositions applied in this revision: removed the capacity pre-detection row and its stop gate (set, then catch and report); gated `tick` on the calling task being enabled; named `_set_activity` as the off-owner audit target and required the slice-1 call-site list; added `get_processtitle()` to `deferred.py` under the house rule that deferred imports live only there; merged the two proposed CC-2.4 texts into one bullet; superseded the stale readiness line; corrected the thread-name register row and the Invariants grammar.

Still owed before implementation: the slice-1 off-owner call-site list; a CHANGELOG entry for the `process_title_error` state-shape change with no compatibility shim; the diagnostic exposure contract below; and, when `deferred.py` lands, a one-line CLAUDE.md §4.2 note pointing function-local imports at it.

### Diagnostic exposure and final consistency revision

Decision: include process_title_error in full serialized TaskSpec.state and therefore task-log snapshots, and in TaskSpec's explicit summary and live STATUS/PONG payloads under the same diagnostic key. Do not fold it into error or terminal status. Update those explicit serializers in the implementation slice and prove initial-null and recorded-error values. Existing publication cadence applies; an error does not create a separate event or synchronous broker write.

Omit this field from the monitor store's materialized columns and reduced historical task-list projections in this slice. Those surfaces continue to report execution errors only; no store migration or new column is needed. Full task-log state and live state/summary responses are the diagnostic surfaces. Tests must assert the advertised surfaces, not assume every reduced task list contains the new key. The proposed TS-1.4 addition includes: "The diagnostic is exposed in full task state, task-log snapshots, TaskSpec summaries, and live STATUS/PONG payloads. It is not a field in the materialized monitor-store projection or reduced historical task lists. Publication uses the existing reporting cadence."

Accepted reread corrections: defined diagnostic visibility (the getter proposal is superseded by direct error returns); chose the diagnostic surfaces and omitted monitor-store columns; removed the stale capacity-fallback stop gate; qualified complete-title guarantees and padding cleanup for native limitations/failure; distinguished normal shutdown from forced termination. The deferred-import restriction is an independently established owner policy, applied here rather than newly introduced by this plan. No unrelated import migration is required.

Verification additions: set_process_title and tick return caught failures directly; BaseTask records non-None results in its state. Success, duplicate suppression, not-due ticks, and disabled calls do not clear a recorded error. No process-global diagnostic is propagated to unrelated tasks. Full-state/log, summary, and STATUS/PONG expose the diagnostic without overwriting execution error; monitor-store schema and reduced historical projections remain unchanged. Off-owner call-site inventory and CHANGELOG/state-shape notes remain implementation tasks.

### Simplification review (2026-09-10)

Removed the proposed error accessor and duplicate process-level error store. Direct str-or-None returns plus a historical task diagnostic avoid a tri-state result, recovery tracking, and extra diagnostic state. Removed the prescribed seven-state representation, retaining only actual backend/deadline/memoization data. Keep one lock because different task drive threads can share a PID; keep the already-imported stock branch because it prevents two native writers; stop native writes after uncertain partial initialization rather than inventing rollback. These are concrete native ownership requirements, not generalized recovery infrastructure.

No buffer probing, runtime read-back, retries on a timer, separate thread names, background registration, backend plugin system, or unrelated import migration. Tests should prove the agreed behavior through production paths; no AST scan of all callers or source-text assertion about upstream NUL handling is required. Retain maximum-title native tests and ordinary formatter/status coverage. The separately established deferred-import policy is unchanged.

### Spec promotion (2026-09-10)

Promoted the owner-approved text to CC-2.4, OBS.4, and TS-1/TS-1.4 against the promotion baseline above; added the state-shape and macOS behavior CHANGELOG notes. The proposed-delta section is now historical review material. Implementation mappings now name process_title.py, deferred.py, BaseTask turn/wait/formatting, and StateSection/to_log_dict ownership. Implementation verification remains in progress.

Documentation verification after promotion: `uv run pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py -n 0` passed 8 tests; touched-document `git diff --check` passed. The traceability command is `../backstitch/.venv/bin/python -m backstitch check --repo-root /Users/van/Developer/weft --no-config --spec-root docs/specifications --plan-root docs/plans --code-root weft --code-root tests --format json`. It returned 28 errors (including existing planned-document headings and unrelated missing mappings), 1,096 warnings, and 591 infos; no error named a new title module or promoted section. This is not a clean repository-wide traceability gate. The sibling corpus test's historical 45-error ledger is stale, so it is not treated as evidence that these 28 findings were introduced by this slice. Results: `/tmp/weft-title-backstitch.json`. Reconciliation must rerun after implementation.

## Implementation review and focused verification (2026-09-10)

Implemented deferred accessors, one process-local native owner, shared turn/wait integration, formatter capacity constants, and historical state.process_title_error. No separate thread naming, runtime buffer probing, or recovery tracker. Native boundaries catch Exception (not BaseException), return diagnostic strings, and use debug logging for exception context. Ruff accepts this explicit logged boundary without a suppression.

Call-site audit: enabled Consumer/Manager/heartbeat/pipeline/interactive lifecycle and activity paths run on their drive owner; construction precedes ownership. TaskMonitor raw external logging can run in a worker clone, but that clone has process titles disabled. Off-owner native requests are skipped; no pending-title queue is introduced. A reviewer caught an unconditional turn-end title refresh that would overwrite explicit paused/draining statuses. Removed it; process_once now only ticks GUI activation. Real PAUSE and leadership-drain regressions preserve explicit titles while durable status stays running.

Focused tests: 16 native/lifecycle/schema tests plus 32 real task-observability tests passed. Native handoff checks use full ps and stock getter equality; no runtime read-back was added. Error tests cover failed padding, uncertain import, failed unpadding and retry, initialization failures, false native return, schema round-trip and separate execution error. Independent scoped review confirmed the paused/draining fix and failure coverage.

Stress: original SQLite cohort passed 7 tests at 24 workers. PostgreSQL wrapper requires `--` before pytest arguments and appends `-n logical`; use PYTEST_XDIST_AUTO_NUM_WORKERS=24 plus --maxprocesses 24 for its actual 24-worker run. That cohort passed 6 shared tests in 6.57 seconds (the remaining SQLite-only selection is not part of shared PG scope).

GUI verification: Activity Monitor displayed `weft-title-check-9876543210:running` for the owned native handoff probe. The filter was cleared and probe reaped. This confirms actual GUI name display, not just Unix ps.

Full-suite verification continues; do not treat these focused results as a full-suite pass.

### Traceability reconciliation (2026-09-10)

Compared the actual backstitch command above against a fresh `git archive HEAD`
checkout at `/tmp/weft-title-trace-baseline`, using diagnostic identity and
multiplicity `(code, path, section_id, message)`, excluding shifted line numbers.
Baseline: 28 errors, 1,086 warnings. Reconciled: 28 errors, 1,078 warnings.
The 28 error identities are unchanged. No title-slice warning remains newly
introduced. Three added warnings belong to earlier repair tests (uv wrapper and
PG harness ambiguous TS-0 references; runner EXEC.7 reciprocal mapping).
Fully qualified the three new mapping symbols; made OBS.4/7/8 ID-bearing
headings with explicit native/formatter mappings, since the scanner only binds
mapping blocks to headings. This also resolves eleven preexisting reciprocal
warnings for those same observability requirements. Requirement text and IDs
are unchanged. Evidence: `/tmp/weft-title-backstitch-baseline.json` and
`/tmp/weft-title-backstitch-reconciled.json`. Existing errors and unrelated
repair warnings were not modified.


### Final policy fixes and review (2026-09-10)

The first full SQLite run finished with 4,739 passing tests and two policy
failures: imports through the core package facade and missing shared-test
manifest registration. Use direct module imports (without same-name aliases,
which Ruff rewrites into facade imports) and register test_process_title.py in
tests/conftest.py. The architecture, audit-policy, native, and observability
cohort then passed all 109 tests. The first PG run had loaded the old manifest
before the edit and stopped on that same failure (3,096 passed, 12 skipped).
Both full commands are being rerun after these fixes.

Final scoped review found no runtime blocker. Clarified that the plan's
no-traceback requirement applies to persisted diagnostics and warnings; debug
exception logging remains available. Ruff check, format check (908 files),
canonical mypy (188 source files), and git diff --check passed.


### Load-run test corrections (2026-09-10)

The second SQLite run passed 4,739 tests and exposed two test defects. The
native probe imposed a 15-second per-message deadline even though the approved
contract accepts blocking LaunchServices calls. That failure did not identify
the native stage, so it is not evidence that a particular native call stalled.
Removed the unsupported per-call latency assertion, retained all real GUI,
ps, and getter checks, and added stage diagnostics plus the PG suite's
900-second whole-test watchdog (signal method preserves owned-child cleanup).
An isolated diagnostic rerun passed in 8.20 seconds. This corrects the test
contract; it does not claim the OS registration stall was eliminated.

The Django cleanup probe scanned an unrelated macOS process and got
AccessDenied before establishing ownership. Discovery now skips inaccessible
candidates; permission errors after ownership is established still propagate.
Two focused cases prove this boundary. The full architecture/policy/native/
observability/cleanup cohort passed 115 tests in 18.45 seconds. Full commands
are running again; no production timeout or parallelism limit changed.


### Final verification (2026-09-10)

- `uv run pytest`: 4,743 passed, 14 skipped in 474.45 seconds.
  Log: `/tmp/weft-title-full-sqlite-verified.log`.
- `uv run pytest-pg`: 4,586 passed, 23 skipped in 688.27 seconds.
  Log: `/tmp/weft-title-full-pg-final.log`.
- The PG full run started before the final test-only probe/cleanup edits.
  Verified those final files with
  `PYTEST_XDIST_AUTO_NUM_WORKERS=24 uv run pytest-pg -- tests/core/test_process_title.py tests/system/test_django_fixture_cleanup.py --maxprocesses 24`:
  22 passed in 6.55 seconds. Log: `/tmp/weft-title-final-pg-regressions.log`.
- Original failure cohort also passed at 24 workers on both backends (above).
- Ruff lint and format checks clean; canonical mypy clean (188 source files).
- Eight plan/spec hygiene tests passed. Traceability baseline differences and
  unchanged preexisting errors are recorded above; no overall clean claim.
- Actual Activity Monitor GUI title and maximum-length Unix titles verified.

Implementation and tests are included in the owner-requested targeted commit. Existing unrelated
working-tree changes were preserved. No native upstream patch is required.


### Status bound derivation (2026-09-10, Van)

`PROCESS_TITLE_STATUS_LENGTH` now derives from the defined status sets rather
than a literal: `PROCESS_TITLE_STATUSES` is `TASK_LIFECYCLE_STATUS_VALUES`
joined with `PROCESS_TITLE_ONLY_STATUSES` (`init`, `paused`, `draining`,
`stopping`), and the bound is the longest member. A new title status is
defined there before use, which widens the handoff capacity automatically.
A formatter test ties the TaskSpec status Literal to the lifecycle set and
proves the longest defined status formats to exactly the shared maximum at
the widest context, name, and details. Call sites still pass string
literals; a status used without being defined is not detected at runtime.
