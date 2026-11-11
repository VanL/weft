# Control-Plane, Process Supervision, and Manager Drain Bug Remediation Plan

Status: completed
Source specs: `docs/program-theory.md` ([WT-2a.2], [WT-2a.6], [WT-2a.7], [WT-2a.9], [WT-2a.12], [WT-2a.13], [WT-2a.14], [WT-4.1], [WT-4.2], [WT-4.3], [ALT-WT-009], [WT-7]); `docs/specifications/01-Core_Components.md` ([CC-2.4], [CC-3.1], [CC-3.2]); `docs/specifications/03-Manager_Architecture.md` ([MA-1.4], [MA-1.6a], [MA-3]); `docs/specifications/05-Message_Flow_and_State.md` ([MF-3], [MF-5]); `docs/specifications/06-Resource_Management.md` ([RM-5.1]); `docs/specifications/07-System_Invariants.md` ([OBS.12a], [OBS.13.7], [OBS.13.9], [LIVENESS.R3], [LIVENESS.R5]); `docs/specifications/08-Testing_Strategy.md` ([TS-1]); `docs/specifications/09-Implementation_Plan.md` ([IP-1]); `docs/specifications/10-CLI_Interface.md` ([CLI-0.1], [CLI-1.3], [CLI-4]); `docs/specifications/14-Python_API_Surfaces.md` ([PY-2], [PY-3])
Superseded by: none

Class: 5 (`implementation with spec revision` across retention-prune queue boundaries, control convergence, host process-tree ownership and `[LIVENESS.R5]` identity verification, Manager `STOP` drain target-freezing/visibility, and `[WT-2a.14]` accepted-submission progress; hardened per `docs/agent-context/runbooks/hardening-plans.md`).

## 1. Goal

Remediate the empirically verified, concretely reachable control-plane, process-supervision, and Manager drain defects found in the 2026-11-10 deep-dive review (`HEAD = dbbf5526`, `v0.9.124`) across three commit-isolated slices — without defensive over-armoring, compatibility shims, or test-only production hooks. Companion plans handle agent/pipeline/extension bugs (`docs/plans/2026-11-10-agent-pipeline-and-extension-bug-remediation-plan.md`), extension PEP 561 typing and spec synchronization (`docs/plans/2026-11-10-extension-typing-and-spec-sync-plan.md`), and the `[REV-WT-007]` `[WT-7]` plan-gate theory revision (`docs/plans/2026-11-10-program-theory-fit-cadence-and-gate-plan.md`).

## 2. Source Documents

- **Program Theory (`docs/program-theory.md`)**:
  - `[WT-2a.2]` (*Queues are the only durable truth*) & `[WT-4.2]` (*One owner for each resource* — externally owned custom/shared queues are never wiped by `TaskMonitor` or ordinary retention prune)
  - `[WT-2a.6]` (*Every task is one event-driven reactor*) & `[WT-4.3]` (*One reactor, one arbiter, a closed set of inputs*; *Availability and death are different questions*)
  - `[WT-2a.7]` (*Each reservation executes at most once* — peer Managers preserve a draining Manager's `.internal_reserved` custody)
  - `[WT-2a.9]` (*One current contract* — replace bare `force_kill_pids` outright with `(pid, create_time)` `force_kill_processes`; zero dual fields)
  - `[WT-2a.12]` (*Everything is a task, and every protocol is a message* — `STOP` requesters observe terminal broadcast on `ctrl_out`)
  - `[WT-2a.13]` (*Independent lifetimes; death needs proof* — `report_unknown` is never stop proof; `create_time=None` handles are `unknown` under `[LIVENESS.R3]` and never authorize `SIGKILL` under `[LIVENESS.R5]`)
  - `[WT-2a.14]` (*Submission never blocks*) & `[WT-4.1]` (*A wake predicate reads facts and guarantees progress* — accepted submissions racing a Manager `STOP` must still progress without polling timers)
  - `[ALT-WT-009]` (*Every waiter scans the shared task log for completion* — completion stays task-local; pre-spawn registry wake scope and strain recorded in §8)
- **Agent Context & Runbooks**:
  - `AGENTS.md`, `docs/agent-context/decision-hierarchy.md` (`[DOM-15]`), `docs/agent-context/engineering-principles.md`, `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/hardening-plans.md`, `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`, `docs/agent-context/runbooks/testing-patterns.md`
- **Governing Specifications**:
  - `docs/specifications/01-Core_Components.md` `[CC-2.4]`, `[CC-3.1]`, `[CC-3.2]`
  - `docs/specifications/03-Manager_Architecture.md` `[MA-1.4]`, `[MA-1.6a]`, `[MA-3]`
  - `docs/specifications/05-Message_Flow_and_State.md` `[MF-3]`, `[MF-5]`
  - `docs/specifications/07-System_Invariants.md` `[OBS.12a]`, `[OBS.13.7]`, `[OBS.13.9]`, `[LIVENESS.R3]`, `[LIVENESS.R5]`
  - `docs/specifications/10-CLI_Interface.md` `[CLI-0.1]`, `[CLI-1.3]`, `[CLI-4]`
  - `docs/specifications/14-Python_API_Surfaces.md` `[PY-2]`, `[PY-3]`

## 3. Reachability Triage & Current-Structure Root-Cause Analysis

### 3.1 Included Defects: Concrete Reachability & Root-Cause Traces

1. **Foreground `retention` / `task-local` prune deletes external messages from custom/shared queues (`weft/core/pruning/retention.py::_task_local_candidates`, `_task_local_candidates_for_tid`)**:
   - *Reachability condition*: Under default `TaskMonitor` (`delete` mode), raw `weft.log.tasks` rows are folded into `MonitorStore` and deleted, so `--family retention` sees `0` unfolded log rows once collated. When raw `weft.log.tasks` rows remain present (`WEFT_TASK_MONITOR_MODE=report_only`, before `TaskMonitor` folds the log, or via `--family task-local`), `_task_local_candidates_for_tid` resolves `outbox_queue` and `ctrl_out_queue` from `TaskSpec.io` (`task_outbox_queue_name` / `task_ctrl_out_queue_name`) and `ctrl_in` from `io.control["ctrl_in"]`. If completed task `T1` configured `io.outputs["outbox"] = "shared.coordination.outbox"`, `peek_final_outbox_evidence` (`weft/core/task_evidence.py`) treats a single unrelated message in `"shared.coordination.outbox"` as `T1`'s result, and `cmd_system_prune(family="retention", apply=True, min_age=0)` (`force=False`) archives and deletes that external message (`[WT-2a.2]`, `[WT-4.2]`, `[OBS.13.9]`).
2. **`_stop_resolved_tasks` counts `report_unknown` as accepted (`weft/commands/tasks.py::_stop_resolved_tasks`)**:
   - *Trace*: When `STOP` and `_stop_via_fallback` fail to produce terminal proof within `_await_control_surface`, `reduce_control_convergence` returns `decision.action == "report_unknown"`. Unlike `_kill_resolved_tasks`, which increments `killed` only on proven convergence and skips `report_unknown`, `_stop_resolved_tasks` falls through unconditionally to `count += 1`. `stop_task` returns `None` instead of raising `ControlRejected`, and `cmd_task_stop` / `weft task stop` reports `accepted=[tid]` (`[WT-2a.13]`, `[OBS.12a]`).
3. **`weft queue` ignores upward `.weft/` discovery from subdirectories & `build_context(..., create_dirs=False)` writes `.weft/config.json` (`weft/commands/queue.py::_context`, `weft/cli/app.py::_stdin_message`, `weft/context.py::build_context`, `_load_project_config`)**:
   - *Trace*: `queue.py::_context` passes `spec_context=os.getcwd()` and `app.py::_stdin_message` passes `spec_context=os.environ.get(WEFT_CONTEXT_ENV) or os.getcwd()`, bypassing `find_broker_project_config()` upward discovery and creating a nested `sub1/sub2/.weft/broker.db` (`[CLI-0.1]`). Independently, `build_context` calls `_load_project_config` before checking `if create_dirs:`, unconditionally writing `.weft/config.json` even when `create_dirs=False`.
4. **`prepare_pipeline` leaks raw `FileNotFoundError` for missing stage specs & `QueuesNamespace` skips `@alias` resolution (`weft/commands/submission.py::prepare_pipeline`, `weft/client/_namespaces.py::QueuesNamespace`)**:
   - *Trace*: `prepare_pipeline._load_pipeline_stage` calls `resolve_named_spec` outside `try:`, leaking raw `FileNotFoundError` instead of `SpecNotFound` (`[PY-3]`). `QueuesNamespace` methods call `queues.*` helpers without `_canonical_queue_operand(ctx, name)`, breaking `@alias` queue operands documented in `09-Implementation_Plan.md` (`[IP-1]`, `[PY-2]`).
5. **Process-tree helpers steal `os.waitpid` (`ECHILD -> returncode=0`), interactive `CONTROL_STOP` collapses `SIGTERM` grace to `0.0s`, and Manager force-kill paths lack single-contract `(pid, create_time)` verification**:
   - *Reap ownership (`weft/core/runners/host.py::HostTaskRunner._run_command_with_hooks`, `weft/core/runners/sessions.py::CommandSession.terminate`, `AgentSession.terminate`)*: `terminate_process_tree` / `kill_process_tree` call `psutil.wait_procs` on the root child (`os.waitpid`), causing CPython `Popen.wait()` to hit `ECHILD` and set `returncode = 0`. On one-shot `HostTaskRunner._run_command_with_hooks`, timeout (`124`) and cancellation (`cancelled`) already decide task status upstream, so switching to `terminate_verified_process_tree` + owned `Popen.wait()` restores single reap ownership (`[WT-4.2]`) and `(pid, create_time)` verification (`[LIVENESS.R5]`). On interactive `CommandSession.terminate`, restoring real negative signal exit codes (`-15` / `-9`) directly fixes `Consumer._cleanup_task_resources` mislabeling force-killed interactive sessions as `completed(0)`.
   - *Interactive teardown & caller budget coordination (`weft/core/tasks/interactive.py::InteractiveTaskMixin._interactive_shutdown`, `_interactive_on_stop`)*: `_interactive_on_stop` has zero callers anywhere in `weft/` or `tests/` (dead code). Meanwhile, reachable `CONTROL_STOP` (`reason="cancelled"`) and `CONTROL_KILL` (`reason="killed"`) call `_interactive_shutdown(reason=...)` with `deadline=None`: the stdin-EOF wait consumes the entire `INTERACTIVE_STOP_GRACE_SECONDS` (`2.0s`) budget and passes an already-expired `shutdown_deadline` to `session.terminate`, escalating straight to `SIGKILL` with `0.0s` `SIGTERM` grace on `STOP` while making `KILL` wait `2.0s` on stdin EOF. Moreover, `_stop_resolved_tasks` only waits `CONTROL_SURFACE_WAIT_TIMEOUT` (`2.0s`) before `_stop_via_fallback(..., timeout=0.2)` escalates to `SIGKILL`, so stdin-EOF drain (`INTERACTIVE_STDIN_EOF_GRACE_SECONDS = 0.5s`) plus the remaining `~1.5s` `CommandSession.terminate` teardown budget (`remaining / 2.0` -> `~0.75s` `SIGTERM` grace + `~0.75s` `SIGKILL` escalation/reap) must fit **within** `INTERACTIVE_STOP_GRACE_SECONDS` (`2.0s`) so graceful `SIGTERM` handlers finish before caller fallback!
   - *Single-contract `(pid, create_time)` force-kill (`weft/core/manager.py`)*: `Manager._tracked_service_candidate` and `Manager._service_owner_record_evidence` emit bare `force_kill_pids`, `Manager._kill_duplicate_service_pid` calls unverified `kill_process_tree`, and `Manager._runtime_handle_force_kill_pids` delegates to `live_host_processes_from_handle` (`pid_matches_create_time(pid, None) -> True`), authorizing `SIGKILL` from handles with `create_time=None` (`unknown` under `[LIVENESS.R3]`, violating `[WT-2a.13]` and `[LIVENESS.R5]`). Per `[WT-2a.9]`, replace `force_kill_pids` outright with `force_kill_processes`.
6. **Manager `STOP` drain strands concurrent submissions (`[WT-2a.14]`) & omits `"draining"` peers in `.internal_reserved` cleanup**:
   - *Window A (During drain)*: `Manager._begin_shutdown_drain` sets `self._draining = True` and stops draining `weft.spawn.requests`, but leaves `status="active"` in `weft.state.services` until exit. Submissions arriving during drain observe `status="active"` + live host PID -> `outcome="ready"`, skip starting a replacement Manager, and hang in result wait after `M1` exits without claiming `tid`.
   - *Drain target-snapshot ordering hazard*: `_collect_drain_stop_targets()` includes peer-owned live user work under system-`STOP` rules (`[MA-3]`). Therefore `M1` **must** apply `_apply_reserved_policy(policy)` and freeze `self._drain_stop_targets = self._collect_drain_stop_targets()` **before** publishing `status="draining"` in `weft.state.services` — otherwise a replacement Manager `M2` could start and launch the racing submission *before* `M1` snapshots `_drain_stop_targets`, causing `M1` to send `STOP` to `M2`'s newly launched task!
   - *Window B (Post-check `STOP` race)*: Even once `M1` publishes `status="draining"`, `submit` checks `observe_manager_availability` only once right after writing `weft.spawn.requests`. If `M1` receives `STOP` immediately after that single check (or requeues a reserved `tid` back to `weft.spawn.requests` via `self._apply_reserved_policy`), `_iter_consuming_result` (`weft/commands/_result_wait.py`) and `iter_task_realtime_events` (`weft/commands/events.py`) watch only `[weft.state.tasks.<tid>, T<tid>.outbox, T<tid>.ctrl_out]` and never wake on `weft.state.services` to ensure a Manager, stranding the caller (`[WT-2a.14]` violation).
   - *Peer `.internal_reserved` custody*: `Manager._cleanup_stale_internal_reserved_queues` protects only `SERVICE_STATUS_ACTIVE`, risking deletion of a live `"draining"` peer Manager's `T{tid}.internal_reserved` queue (`[WT-2a.7]`, `[MA-3]`), while `manager_runtime._manager_record_diagnostic` sets `dispatch_eligible = (liveness == "live")` even when `status == "draining"`.

### 3.2 Triaged Out: Authoritative Deep-Dive Non-Bugs, Over-Armoring & Owner Deferrals

Per the owner's filter (*"addressing real reachable bugs, not just over-armoring"*), these 17 candidates from the deep dive and review rounds are intentionally excluded from code changes:

| Candidate | Classification | Concrete Reason Excluded |
| :--- | :--- | :--- |
| Live `weft.state.tasks.<tid>` snapshot compaction (`pruning/runtime.py`, `liveness_monitor.py`) | **Owner-deferred (Decision #1)** | Owner decision (`2026-11-10`): *"We will keep as-is for now."* |
| Plan-corpus coalescing sweep (`docs/plans/README.md`) | **Owner-deferred (Decision #3)** | Owner decision (`2026-11-10`): *"We will coalesce after."* |
| `descendant_processes(root)` re-reading `psutil.Process(root.pid).create_time()` (`weft/core/process_tree.py`) | **Verified spec contract (`[RM-5.1]`)** | `06-Resource_Management.md` `[RM-5.1]` and `tests/core/test_process_tree.py::test_descendant_processes_survives_a_stale_root_creation_time` require fresh kernel `create_time` reads so backward wall-clock steps do not hide live descendants. |
| `Manager._handle_service_worker_event` `next(iter(self._active_child_launches.values()))` (`weft/core/manager.py`) | **Unreachable (Over-armoring)** | `Manager` suppresses dispatch while `_has_active_child_launches()` is true, `ServiceTask._start_service_worker` rejects an already-active worker group, and `_retry_stale_child_launches` returns early while `_has_worker_activity()` is true. |
| `PipelineEdgeTask` `events_queue.write(checkpoint)` fault handling (`weft/core/tasks/pipeline.py`) | **Verified spec contract (`[PL-4.3]`)** | `12-Pipeline_Composition_and_UX.md` `[PL-4.3]` specifies non-atomic `move_one` before checkpoint emission, deferring lag to recovery rather than failing an already-moved item. |
| `weft_django/realtime.py` importing `weft.helpers._register_observation_wakeup` | **Verified spec contract (`[DJ-12.2]`)** | Documented in `13C-Using_Weft_With_Django.md` `[DJ-12.2]` and permitted by `tests/architecture/test_import_boundaries.py`. |
| `weft_microsandbox/plugin.py` `TemporaryDirectory(dir="/tmp")` on macOS | **False positive (Not a bug)** | Verified in post-split review (`F10`): `TemporaryDirectory(..., dir="/tmp")` preserves the `/tmp/...` prefix and copies host files into guest `/tmp/...` without host-mount validation; resolving `/tmp` to `/private/tmp` on macOS would create `/private/tmp` paths inside the Linux guest. |
| `WeftClient.prepare(bad_dict)` raising `ValidationError` & `client.managers.stop()` raising `ManagerNotRunning` | **Verified spec contract (`[PY-2]`, `[PY-3]`)** | Enforced by `14-Python_API_Surfaces.md` `[PY-3]` and `tests/core/test_public_client_contracts.py`. |
| `QueuesNamespace.move` returning `QueueMoveReceipt` without `entries` | **Verified spec contract** | Intentional compact client receipt distinct from `QueueMoveResult`. |
| `MonitorStore._merge_record` `control_observation is not None` guard (`weft/core/monitor/store.py`) | **Verified intentional design (`53b4ea12`)** | Summary invalidation applies when task-log terminal proof supersedes a prior control projection; clarified in `[OBS.13.13]` prose in Plan 3. |
| `open_process_exit_source` Solaris/AIX `kqueue` & Windows `winerror == 5` (`weft/core/process_exit.py`) | **Unreachable on supported targets** | Supported targets are Linux (`pidfd_open`), macOS/BSD (`kqueue`), and same-user Windows children. |
| HLC vs wall-clock skew in `select_retirable_task_collations` (`weft/core/monitor/sql.py`) | **Over-armoring** | Sub-ms HLC logical drift vs 48h (`172,800s`) default retention window. |
| `HeartbeatTask` registration delete retry cursor (`weft/core/tasks/heartbeat.py`) | **Over-armoring** | Startup registration writes to the same local broker immediately prior to reactor entry. |
| `_worker_runtime_context` `os.getcwd()` in `spawn` workers (`weft/core/runners/host.py`) | **Over-armoring** | OS `posix_spawn`/`CreateProcess` fails before Python entry if parent `cwd` was unlinked. |
| `_poll_monitor_limits` outer `OSError` retry counter (`weft/core/runners/sessions.py`) | **Over-armoring** | `ResourceMonitor.check_limits()` already catches per-process `psutil` errors internally. |
| WSGI `sse_response` synthetic keepalive frames (`weft_django/sse.py`) | **Over-armoring / wire churn** | `[DJ-12]` scopes WSGI SSE to local/diagnostic use and ships `_AsyncSSEStream` for ASGI. |
| `weft spec validate` exit code `1` vs `2` & `weft result --stream` `reconciliation` dict | **Cosmetic churn** | Risk of breaking existing callers/tests exceeds benefit. |

### 3.3 Comprehension Questions & Answers

1. **Q: Why must `Manager._begin_shutdown_drain` freeze `self._drain_stop_targets = self._collect_drain_stop_targets()` AFTER `self._apply_reserved_policy(policy)` and BEFORE `self._unregister_manager(status=SERVICE_STATUS_DRAINING)`?**
   - **A**: `_apply_reserved_policy` must run first so any unlaunched reserved spawn request is back in `weft.spawn.requests` before callers wake on `status="draining"`. And `_collect_drain_stop_targets()` must run **before** `status="draining"` is published because `_collect_drain_stop_targets()` collects peer-owned live user work under system-`STOP` rules (`[MA-3]`); publishing `draining` earlier would allow replacement Manager `M2` to launch a racing submission that `M1` then snapshots into `_drain_stop_targets` and stops!
2. **Q: Why must `_interactive_shutdown`'s stdin-EOF wait plus `CommandSession.terminate` teardown stay within `INTERACTIVE_STOP_GRACE_SECONDS` (`2.0s`) instead of using `2.0s + 2.0s`?**
   - **A**: `_stop_resolved_tasks` waits `CONTROL_SURFACE_WAIT_TIMEOUT` (`2.0s`) on `_await_control_surface` before `_stop_via_fallback` escalates to `HostRunnerPlugin.stop(..., timeout=0.2)` (`SIGKILL`). Capping stdin-EOF drain at `INTERACTIVE_STDIN_EOF_GRACE_SECONDS` (`0.5s`) within the `2.0s` overall `INTERACTIVE_STOP_GRACE_SECONDS` budget leaves a `~1.5s` remaining teardown budget (`CommandSession.terminate` splits `remaining / 2.0` into `~0.75s` `SIGTERM` grace + `~0.75s` `SIGKILL` escalation and owned `Popen.wait` reap) so graceful `SIGTERM` handlers and terminal publication complete before caller fallback fires.

## 4. Invariants, Difference Registers, Rollout, and Rollback

### 4.1 Invariants Preserved

- **Queue-first authority & externally owned custom queues (`[WT-2a.2]`, `[WT-4.2]`, `[OBS.13.7]`, `[OBS.13.9]`)**: Neither `TaskMonitor` nor ordinary foreground `weft system prune` (`force=False`) inspects or deletes rows from non-standard custom/shared queue names; `weft.state.tasks.<tid>` deletion remains exclusively owned by `LivenessMonitor`.
- **Single current contract (`[WT-2a.9]`)**: Replace `force_kill_pids` outright with `force_kill_processes` (`tuple[tuple[int, float], ...]`); no dual compatibility field and no extra `weft.helpers` wrapper.
- **Death/stop proof & `(pid, create_time)` signal authority (`[WT-2a.13]`, `[WT-4.3]`, `[OBS.12a]`, `[LIVENESS.R3]`, `[LIVENESS.R5]`)**: `report_unknown` never increments accepted stops; host `SIGTERM`/`SIGKILL` is authorized only by verified `(pid, create_time)` pairs (`create_time is not None`) captured at launch or persisted on `RunnerHandle.scoped_host_processes()`.
- **Single child reap owner (`[WT-4.2]`)**: Every `subprocess.Popen` or `BaseProcess` root is reaped solely by its creator (`wait()` / `join()`).
- **Non-blocking submission progress & event-driven waiting (`[WT-2a.14]`, `[WT-2a.6]`, `[WT-4.1]`, `[WT-4.3]`, `[MF-5]`)**: Accepted-submission result/event waits include `weft.state.services` in their `QueueChangeMonitor` set and run pre-spawn Manager recovery checks only until task-local state, terminal, or result activity appears, recovering Manager availability on durable registry transitions with zero polling timers, while bare-TID `weft result <tid>` and `terminal_snapshot` remain strictly non-spawning.

### 4.2 Difference Registers

1. **`_stop_resolved_tasks` vs `_kill_resolved_tasks` (`weft/commands/tasks.py`)**:
   - *Converged*: Both run `reduce_control_convergence`, execute runner fallback on `"escalate_runner"`, re-observe via `_await_control_surface`, and `continue` without incrementing accepted count on `"report_unknown"`.
   - *Intentional differences*: `stop` sends `CONTROL_STOP`, calls `_stop_via_fallback`, has no separate `"escalate_host"` reducer rung, and increments `count` only on `"accept_terminal"`; `kill` sends `CONTROL_KILL`, calls `_kill_via_fallback`, has an `"escalate_host"` `SIGKILL` rung, and accepts `"accept_terminal"` and post-`SIGKILL` `"accept_dead_runtime"`.
2. **Accepted-submission waiters (`_LiveRunSession` / `Task` with `_accepted_taskspec`) vs bare-TID observers (`weft result <tid>` / `terminal_snapshot`)**:
   - *Converged*: Both use `_result_observation_names` and `QueueChangeMonitor` over task-owned surfaces without scanning `weft.log.tasks`.
   - *Intentional difference*: Only callers holding an accepted submission (`materialized.taskspec_payload is not None`) include `weft.state.services` in `_result_observation_names` and, prior to first task-local state/terminal/result evidence, invoke `ensure_manager_after_submission` when `observe_spawn_request_custody(context, tid, reserved_routes=(), broker=broker).state == "queued"` with no `ready` Manager; bare-TID `weft result <tid>` (`taskspec_payload is None`) and `terminal_snapshot` never subscribe to `weft.state.services` or start a Manager.

### 4.3 Anti-Mocking, Commit Isolation & Rollback

- **Anti-mocking (`[TS-1]`)**: Do not mock `SimpleBroker` `Queue`, `WeftContext`, `subprocess.Popen`, `reduce_control_convergence`, `_stop_via_fallback`, or `observe_manager_availability`, and do **not** alter `_await_control_surface` for test speed (the single POSIX `SIGSTOP` Consumer test runs against the real `2.0s` `CONTROL_SURFACE_WAIT_TIMEOUT`).
- **Commit isolation & review schedule**: Each slice (Slices 1, 2, 3) lands as one atomic commit pairing code, tests, and Strategy B spec updates. Run an independent cross-family slice review after Slice 2 + Slice 3 and a final completion review before closing.
- **Rollback**: Zero schema or public wire-format migrations (`weft.service_owner.v2` already includes `"draining"`; `force_kill_processes` is Manager-internal candidate metadata). Any slice reverts cleanly via `git revert <commit>`.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| `[MF-5]` | Drop `WEFT_SERVICES_REGISTRY_QUEUE` from `_result_observation_names` dynamically once task-local evidence appears | Retain `WEFT_SERVICES_REGISTRY_QUEUE` stably in `_result_observation_names` when `materialized.taskspec_payload is not None`, while gating `_check_prespawn_recovery_needed` on `_accepted_submission_needs_manager_watch(materialized)` | `[SB-0.4]` permits dynamic waiter membership replacement, so this is an implementation tradeoff rather than a broker prohibition: keeping the accepted-submission watcher set stable avoids closing and recreating `QueueChangeMonitor` (and tearing down active queue connections) on first task-local activity, while `_check_prespawn_recovery_needed` still stops all registry/custody recovery reads immediately once task-local state, terminal, or result evidence appears | Updated `[MF-5]` replacement text in place (Strategy B) |

## Spec Baseline

- **Baseline Commit / Tag**: `dbbf5526` (`v0.9.124` on `main`, `2026-11-10`)
- **Plan Type**: `implementation with spec revision`
- **Promotion Strategy**: **Strategy B (atomic per slice)** across `docs/specifications/07-System_Invariants.md` (Slice 1), `docs/specifications/03-Manager_Architecture.md` (Slices 2 & 3), and `docs/specifications/05-Message_Flow_and_State.md` (Slice 3).
- **Promotion Baseline Identifier**: `ddd6697d` (Slice 1), `6806b0e0` (Slice 2), and this Slice 3 closeout commit

## Proposed Spec Delta

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/07-System_Invariants.md` | B — atomic in Slice 1 | `[OBS.13.9]` custom/shared queue non-interference in foreground retention prune |
| `docs/specifications/03-Manager_Architecture.md` | B — atomic in Slice 2 & Slice 3 | `[MA-1.6a]` implementation mapping (`force_kill_processes`); `[MA-1.4]` shutdown drain `status="draining"` ordering & `.internal_reserved` protection; `## Related Plans` |
| `docs/specifications/05-Message_Flow_and_State.md` | B — atomic in Slice 3 | `[MF-5]` accepted-submission pre-spawn `weft.state.services` wake & Manager recovery; `## Related Plans` |

### 1. `docs/specifications/07-System_Invariants.md` (`[OBS.13.9]` — Slice 1)

- **Exact pre-change target (`docs/specifications/07-System_Invariants.md:705-708`)**:
  - Starts with: `    [MF-5], including forced-death and stale-service-owner cleanup. Standard reserved`
  - Ends with: `    older Monitor schemas use migration, not a permanent compatibility lane.`
- **Clause audit**: Preserves every `[OBS.13.9]` clause verbatim and makes explicit that neither `TaskMonitor` nor ordinary foreground `retention` / `task-local` prune scans or deletes rows from non-standard custom/shared queue names.

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
    [MF-5], including forced-death and stale-service-owner cleanup. Standard reserved
    remains owned by its existing cleanup policy. Custom/shared queues remain externally
    owned: neither `TaskMonitor` nor ordinary foreground `retention` / `task-local` prune
    scans or deletes rows from non-standard custom/shared queue names. Monitor repair
    covers only reachable current-writer/current-cleanup states;
    older Monitor schemas use migration, not a permanent compatibility lane.
```

### 2. `docs/specifications/03-Manager_Architecture.md` (`[MA-1.6a]` & `[MA-1.4]` — Slices 2 & 3)

- **Exact pre-change target A (`docs/specifications/03-Manager_Architecture.md:756`, Slice 2)**:
  - Substring within the `[MA-1.6a]` implementation-mapping bullet starting ``published through `BaseTask.next_wait_timeout` and `ServiceTask.next_wait_timeout`), `Manager._candidate_force_kill_pids`, `Manager._runtime_handle_force_kill_pids`, `Manager._enqueue_managed_service_request` `` and ending `` `Manager._enqueue_managed_service_request` ``.
- **Clause audit A**: Updates the two renamed `Manager` helper names (`_candidate_force_kill_processes`, `_runtime_handle_force_kill_processes`) in place; preserves all surrounding `[MA-1.6a]` mapping text untouched.

Replacement (`docs/specifications/03-Manager_Architecture.md`):
```markdown
published through `BaseTask.next_wait_timeout` and `ServiceTask.next_wait_timeout`), `Manager._candidate_force_kill_processes`, `Manager._runtime_handle_force_kill_processes`, `Manager._enqueue_managed_service_request`
```

- **Exact pre-change target B (`docs/specifications/03-Manager_Architecture.md:906-913`, Slice 3)**:
  - Starts with: `- a manager with non-persistent children that voluntarily yields leadership`
  - Ends with: `  external readers have current evidence`
- **Clause audit B**: Preserves leadership-yield and self-liveness bullets verbatim; inserts one bullet specifying `STOP` shutdown drain's exact ordering (`_apply_reserved_policy`, freezing `_drain_stop_targets`, then publishing `status="draining"` before signaling/waiting on children) and stale `.internal_reserved` protection of `"draining"` Manager records.

Replacement (`docs/specifications/03-Manager_Architecture.md`):
```markdown
- a manager with non-persistent children that voluntarily yields leadership
  enters leadership drain and revalidates the replacement; if the replacement
  becomes stale or unknown, it may publish `manager_leadership_resumed`, restore
  active ownership, and return to the ordinary manager loop
- a manager entering graceful `STOP` shutdown drain applies its reserved-queue
  policy, snapshots its `STOP`-drain target set, and immediately publishes
  `status="draining"` in `weft.state.services` before signaling or waiting on
  active children so replacement managers do not have newly launched work swept
  into the old drain and concurrent availability/recovery checks do not treat a
  stopping manager as `ready`; stale `.internal_reserved` queue cleanup protects
  both `active` and `draining` canonical manager records
- a manager treats its own active canonical registry row as live while it has
  not unregistered and is not stopping; host/PONG liveness probes are for other
  processes, and the manager refreshes its own registry row periodically so
  external readers have current evidence
```

### 3. `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]` Accepted-Submission Pre-Spawn Recovery — Slice 3)

- **Exact pre-change target (`docs/specifications/05-Message_Flow_and_State.md:1267-1271`)**:
  - Starts with: `Ordinary result waits with known immutable task configuration observe the configured`
  - Ends with: `persistent work boundaries, output grace, and claim settlement apply.`
- **Clause audit**: Preserves every clause on outbox/`ctrl_out`/task-local observation, non-use of `weft.log.tasks`, and terminal/output settlement; adds the pre-spawn `weft.state.services` wake and `ensure_manager_after_submission` recovery rule for accepted-submission waiters while keeping bare-TID `weft result` and `terminal_snapshot` strictly non-spawning.

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
Ordinary result waits with known immutable task configuration observe the configured
outbox and `ctrl_out`, plus the existing task-local runtime wake/evidence surfaces;
when waiting on an accepted submission, they also include `weft.state.services` in
that wake set and, before any task-local state, terminal, or output evidence appears,
re-run post-submission Manager recovery if the request remains `queued` with no
`ready` Manager ([WT-2a.14]; bare-TID `weft result` and `terminal_snapshot` never
start a Manager). They
do not subscribe to or scan `weft.log.tasks` for live completion. Task-local completion
records carry the same lifecycle fact as global history; existing terminal precedence,
persistent work boundaries, output grace, and claim settlement apply.
```

## 5. Tasks

### Slice 1: Data-Safety, Control Convergence & Context Discovery

1. **Task 1.1: Restrict Foreground `retention` / `task-local` Prune to Standard `T{tid}.*` Queues (`[WT-2a.2]`, `[WT-4.2]`, `[OBS.13.9]`)**
   - **Outcome**: Ordinary `cmd_system_prune` (`force=False`) under `family="retention"` or `family="task-local"` never inspects, archives, or deletes messages from custom/shared queues configured via `TaskSpec.io`.
   - **Files to touch**: `weft/core/pruning/retention.py`, `docs/specifications/07-System_Invariants.md` (§4c.1 + `## Related Plans`), `tests/commands/test_retention_prune.py`.
   - **Read first**: `weft/core/pruning/retention.py` (`_task_local_candidates`, `_task_local_candidates_for_tid`), `weft/core/monitor/policies/runtime_control.py` (standard `T{tid}.*` guard), `weft/commands/prune.py::cmd_system_prune`.
   - **Reuse**: Standard queue-name equality predicates (`f"T{tid}.{QUEUE_OUTBOX_SUFFIX}"`, `f"T{tid}.{QUEUE_CTRL_OUT_SUFFIX}"`, `f"T{tid}.{QUEUE_CTRL_IN_SUFFIX}"`, `f"T{tid}.{QUEUE_INBOX_SUFFIX}"`, `f"T{tid}.{QUEUE_RESERVED_SUFFIX}"`) using `weft._constants`.
   - **Constraints**: Do not weaken `force=True` on standard `T{tid}.*` queues; do not invent a queue-ownership registry.
   - **RED -> GREEN test**: In `tests/commands/test_retention_prune.py`, run with `WEFT_TASK_MONITOR_MODE=report_only` so raw `weft.log.tasks` rows remain unfolded (and also exercise `family="task-local"`): complete a real task `T1` with `io.outputs["outbox"] = "shared.coordination.outbox"` and `io.control["ctrl_out"] = "shared.coordination.ctrl_out"`, consume `T1`'s result, write `"external-live-workflow-message"` to `"shared.coordination.outbox"`, run `cmd_system_prune(family="retention", apply=True, min_age=0, archive=..., context=...)`, and assert `"external-live-workflow-message"` remains unconsumed.
   - **Done when**: Fails RED on `HEAD` (shared outbox emptied) and passes GREEN with `pytest tests/commands/test_retention_prune.py -q`.

2. **Task 1.2: Refuse False `stop` Acceptance on `report_unknown` (`[WT-2a.13]`, `[OBS.12a]`)**
   - **Outcome**: `stop_task`, `cmd_task_stop`, `weft task stop`, and `weft_django` task stop fail / raise `ControlRejected` when `STOP` + runner fallback end in `decision.action == "report_unknown"`.
   - **Files to touch**: `weft/commands/tasks.py` (`_stop_resolved_tasks` only), `tests/commands/test_task_commands.py`.
   - **Read first**: `weft/commands/tasks.py` (`_stop_resolved_tasks` vs `_kill_resolved_tasks`, `stop_task`, `cmd_task_stop`), `weft/commands/control_convergence.py::reduce_control_convergence`.
   - **Reuse**: Mirror `_kill_resolved_tasks`: if `decision.action in {"escalate_runner", "report_unknown"} and not handled_by_runner`, run `_stop_via_fallback(task_entry)`, re-observe via `_await_control_surface`, and re-run `reduce_control_convergence("escalating_runner", ...)`; increment `count += 1` **only** when `decision.action == "accept_terminal"`, and `continue` on `"report_unknown"`.
   - **Constraints**: Do **not** edit `_await_control_surface` in production, and do **not** mock `reduce_control_convergence` or `_stop_via_fallback`. On POSIX (`os.name == "posix"`), launch a real sleeping command task via `WeftTestHarness`, pause the `Consumer` PID with `signal.SIGSTOP` so it cannot publish terminal proof during the real `2.0s` `CONTROL_SURFACE_WAIT_TIMEOUT`, assert `stop_task(tid, context=ctx)` raises `ControlRejected` and `cmd_task_stop(tid, context=root)` returns `accepted == ()` with `tid` in `failures`, and in `finally:` send `signal.SIGCONT` + `kill_task`.
   - **Done when**: Fails RED on `HEAD` (`accepted=(tid,)`) and passes GREEN (`pytest tests/commands/test_task_commands.py -q`).

3. **Task 1.3: Fix `weft queue` Upward `.weft/` Discovery & `build_context(..., create_dirs=False)` Disk Write**
   - **Outcome**: `weft queue` verbs (argv and piped stdin) from nested subdirectories discover the enclosing `.weft/` root via upward walk rather than creating `sub1/sub2/.weft/`, and `build_context(..., create_dirs=False)` never writes `.weft/config.json`.
   - **Files to touch**: `weft/commands/queue.py::_context`, `weft/cli/app.py::_stdin_message`, `weft/context.py` (`build_context`, `_load_project_config`), `tests/context/test_context.py`, `tests/cli/test_cli_queue.py`, `tests/commands/test_queue.py`.
   - **Constraints**: Pass `spec_context=None` when neither `spec_context` nor `WEFT_CONTEXT` is set (`explicit > WEFT_CONTEXT > upward discovery > cwd fallback`), pass `create_dirs=False` in `_stdin_message`, and gate `write_json_atomically` in `_load_project_config` on `persist_if_missing=create_dirs`.
   - **Done when**: `build_context(spec_context=empty_dir, create_dirs=False, create_database=False)` leaves `empty_dir / ".weft"` absent, and `weft queue write`/`peek` from `project/sub1/sub2` uses `project/.weft/broker.db` without creating `project/sub1/sub2/.weft`.

4. **Task 1.4: Translate Missing Pipeline Stage `FileNotFoundError` & Canonicalize `@alias` in `QueuesNamespace`**
   - **Outcome**: `prepare_pipeline` raises `SpecNotFound` (chaining `FileNotFoundError` per `[PY-3]`) when a stage's referenced TaskSpec is missing, and `QueuesNamespace` resolves `@alias` queue operands identically to `cmd_queue_*`.
   - **Files to touch**: `weft/commands/submission.py::prepare_pipeline`, `weft/client/_namespaces.py::QueuesNamespace`, `tests/core/test_client.py`.
   - **Reuse**: `_canonical_queue_operand` from `weft/commands/queue.py` (including validating `canonical_source != canonical_destination` in `QueuesNamespace.move`).
   - **Constraints**: Preserve `WeftClient.prepare(bad_dict)` raising `ValidationError` and `client.managers.stop()` raising `ManagerNotRunning` (§3.2).
   - **Done when**: `pytest tests/core/test_client.py tests/core/test_public_client_contracts.py tests/context/test_context.py tests/cli/test_cli_queue.py -q` passes.

### Slice 2: Process-Tree Reap Ownership, Interactive Teardown & Single-Contract `force_kill_processes`

5. **Task 2.1: Restore Single Child Reap Ownership via `terminate_verified_process_tree` (`[WT-4.2]`, `[LIVENESS.R5]`)**
   - **Outcome**: `HostTaskRunner._run_command_with_hooks`, `CommandSession.terminate`, and `AgentSession.terminate` signal process trees via `terminate_verified_process_tree(pid, create_time, timeout=..., kill=...)` and reap roots solely via owned `Popen.wait()` / `BaseProcess.join()`, preventing `ECHILD -> returncode=0`.
   - **Files to touch**: `weft/core/runners/host.py::HostTaskRunner._run_command_with_hooks`, `weft/core/runners/sessions.py` (`CommandSession`, `AgentSession`), `tests/tasks/test_runner.py`, `tests/tasks/test_task_interactive.py`.
   - **Read first**: `weft/helpers/__init__.py::terminate_verified_process_tree`, `weft/core/runners/subprocess_runner.py` (reference owned-reap pattern), `docs/specifications/06-Resource_Management.md` `[RM-5.1]`.
   - **Constraints**: Capture `create_time = process_create_time(pid)` immediately after `Popen` / `process.start()`. Do **not** touch `weft/core/process_tree.py::descendant_processes` (§3.2).
   - **RED -> GREEN tests**: Real `CommandSession` running `python -c "import time; time.sleep(30)"`: call `session.terminate()`, assert `session.returncode() != 0` (POSIX `< 0`, fails RED on `HEAD` with `0`); plus real `HostTaskRunner._run_command_with_hooks` cancellation reap test.
   - **Done when**: `pytest tests/core/test_process_tree.py tests/tasks/test_runner.py -q` passes with zero zombies.

6. **Task 2.2: Delete Dead `_interactive_on_stop` & Coordinate `STOP` vs `KILL` Grace Within Caller Control Budget**
   - **Outcome**: Remove unused `InteractiveTaskMixin._interactive_on_stop`. Add `INTERACTIVE_STDIN_EOF_GRACE_SECONDS: Final[float] = 0.5` in `weft/_constants.py` (with docstring explaining that stdin-EOF drain + `CommandSession.terminate` teardown must complete inside `INTERACTIVE_STOP_GRACE_SECONDS` / `CONTROL_SURFACE_WAIT_TIMEOUT = 2.0s` before `_stop_resolved_tasks` escalates to runner fallback). In `InteractiveTaskMixin._interactive_shutdown`:
     - When `reason == "killed"`: close stdin best-effort and escalate immediately (`session.terminate(deadline=time.monotonic())`).
     - When `reason != "killed"`: compute `overall_deadline = deadline if deadline is not None else (now + INTERACTIVE_STOP_GRACE_SECONDS)`, wait at most `eof_deadline = min(overall_deadline, now + min(INTERACTIVE_STDIN_EOF_GRACE_SECONDS, max(0.0, (overall_deadline - now) * 0.25)))` for clean stdin-EOF exit, and if still alive pass `deadline=overall_deadline` (`~1.5s` remaining teardown budget when `deadline is None`, giving `~0.75s` `SIGTERM` grace via `CommandSession.terminate`'s `remaining / 2.0` split plus escalation/reap) to `session.terminate(deadline=overall_deadline)`.
   - **Files to touch**: `weft/_constants.py`, `weft/core/tasks/interactive.py` (`_interactive_shutdown`, delete `_interactive_on_stop`), `tests/tasks/test_task_interactive.py`.
   - **Constraints**: Do **not** add a synthetic `"None means failed"` branch to `_interactive_finalize_session` (Task 2.1's negative signal exit codes handle `Consumer._cleanup_task_resources` directly).
   - **RED -> GREEN tests**: (a) Real interactive task via `WeftTestHarness` running a Python child that ignores stdin EOF, traps `SIGTERM`, sleeps **`0.5s`** inside its `SIGTERM` handler (`time.sleep(0.5)`), emits `"CLEAN_SIGTERM_EXIT"`, and exits `0`: invoke public `cmd_task_stop` and assert `accepted == (tid,)`, status is `cancelled`, AND `"CLEAN_SIGTERM_EXIT"` was emitted without fallback `SIGKILL` preemption (fails RED on `HEAD`); (b) real interactive `Consumer` cleanup (`reason=None`) where child ignores stdin EOF: assert terminal status is `failed` (not `completed(0)`).
   - **Done when**: `pytest tests/tasks/test_task_interactive.py -q` passes.

7. **Task 2.3: Replace `force_kill_pids` Outright with Verified `force_kill_processes` (`[WT-2a.9]`, `[LIVENESS.R5]`)**
   - **Outcome**: Replace `force_kill_pids` everywhere in `Manager` with `force_kill_processes: tuple[tuple[int, float], ...]` (`create_time is not None`), ensuring neither duplicate-service termination nor runtime-handle force-kill ever authorizes `SIGKILL` without verified `(pid, create_time)` identity.
   - **Files to touch**: `weft/core/manager.py` (`_tracked_service_candidate`, `_service_owner_record_evidence`, `_terminate_duplicate_service_candidates`, `_candidate_force_kill_processes`, `_kill_duplicate_service_process`, `_runtime_handle_force_kill_processes`), `docs/specifications/03-Manager_Architecture.md` (§4c.2A), `tests/core/test_manager.py`, `tests/core/test_manager_services.py`.
   - **Read first**: `weft/ext.py::RunnerHandle.scoped_host_processes`, `weft/core/manager.py::_launched_child_create_time`, `docs/specifications/07-System_Invariants.md` `[LIVENESS.R3]`, `[LIVENESS.R5]`.
   - **Constraints**: Zero dual `force_kill_pids` field (`[WT-2a.9]`), zero new `weft.helpers` wrapper, and never read `process_create_time(pid)` fresh right before signaling. Only pairs with `create_time is not None` from `_launched_child_create_time(tid, child)` or `runtime_handle.scoped_host_processes()` may be passed to `terminate_verified_process_tree(pid, create_time, timeout=0.2, kill=True)`.
   - **Done when**: Real-process tests prove a `RunnerHandle` / `ServiceOwnerRecord` with `host_pids=[live_pid]` and `host_processes=()` (`create_time=None`) kills nothing (`live_pid` survives), while a record with matching `(live_pid, create_time)` terminates cleanly.

### Slice 3: Manager `STOP` Drain Availability & Non-Blocking Submission Progress (`[WT-2a.14]`)

8. **Task 3.1: Freeze Drain Targets Before Publishing `status="draining"` & Recover Pre-Spawn Accepted-Submission Waiters**
   - **Outcome**: Submissions racing a Manager `STOP` — whether arriving during the drain window or immediately before `STOP` lands — never strand waiting callers and are never swept into `M1`'s `STOP` target snapshot after `M2` launches them; `manager_runtime` never reports a `draining` Manager as `dispatch_eligible`; and peer Managers never delete a live `draining` Manager's `.internal_reserved` queue.
   - **Files to touch**:
     - `weft/core/manager.py`: in `Manager._begin_shutdown_drain`, run `self._apply_reserved_policy(policy)`, then `if not self._admission_bootstrapped: self._refresh_scheduler_evidence()`, then `self._drain_stop_targets = self._collect_drain_stop_targets()`, and **immediately after freezing `_drain_stop_targets`** call `self._unregister_manager(status=SERVICE_STATUS_DRAINING)` *before* `self._signal_children_to_stop(...)`; in `Manager._cleanup_stale_internal_reserved_queues`, include `SERVICE_STATUS_DRAINING` alongside `SERVICE_STATUS_ACTIVE` in `protected_tids`.
     - `weft/core/manager_runtime.py` (`_manager_record_diagnostic`: set `dispatch_eligible=(status == SERVICE_STATUS_ACTIVE and liveness == "live")`).
     - `weft/commands/_result_wait.py` (`_result_observation_names`, `_iter_consuming_result`) and `weft/commands/events.py` (`iter_task_realtime_events`): when `materialized.taskspec_payload is not None`, include `WEFT_SERVICES_REGISTRY_QUEUE` in the watched queue names, and while `materialized.mapping_entry is None`, `materialized.terminal_evidence is None`, and `not materialized.result_surface_had_activity`, track `last_recovery_registry_horizon: int | None = -1` and whenever `broker.latest_pending_timestamp(WEFT_SERVICES_REGISTRY_QUEUE) != last_recovery_registry_horizon`, check `_read_recovery_snapshot(context, broker=broker)[0] != "ready"` and `observe_spawn_request_custody(context, tid, reserved_routes=(), broker=broker).state == "queued"`, releasing the borrowed connection before calling `ensure_manager_after_submission(context, submitted_tid=tid, deadline=..., cancel_event=...)`.
     - `docs/specifications/03-Manager_Architecture.md` (§4c.2B + `## Related Plans`) and `docs/specifications/05-Message_Flow_and_State.md` (§4c.3 + `## Related Plans`).
     - `tests/core/test_manager.py`, `tests/system/test_manager_process.py`, `tests/commands/test_run.py`.
   - **Read first**: `weft/core/manager.py::_begin_shutdown_drain`, `weft/core/spawn_requests.py::observe_spawn_request_custody`, `weft/commands/submission.py::ensure_manager_after_submission`, `weft/commands/_result_wait.py`, `weft/commands/events.py`.
   - **Constraints**: Zero polling timers (`[WT-2a.6]`, `[WT-4.3]`, `[ALT-WT-002]`); once task-local mapping, terminal, or result evidence appears, `_check_prespawn_recovery_needed` stops performing registry/custody recovery reads while `_result_observation_names` keeps the watcher set stable (see `## Deviation Log`); bare-TID `weft result <tid>` (`materialized.taskspec_payload is None`) must never subscribe to `weft.state.services` or start a Manager.
   - **RED -> GREEN tests** (use deterministic queue/file readiness synchronization rather than blind sleeps):
     1. *During-drain race & target-freeze safety*: Start `M1` running a child task `T_hold` that signals a ready file, traps `SIGTERM`, and waits on a release file before exiting; send `STOP` to `M1`, wait until `M1`'s `weft.state.services` row transitions to `status="draining"` while `T_hold` keeps `M1` draining, then run `cmd_run(..., wait=True)` (and `Task.follow()`). Assert `M2` starts, executes the new task to `completed` with expected output *while `M1` is still draining* (proving `M1` did not `STOP` `M2`'s child), and release `T_hold` afterward (fails RED on `HEAD`).
     2. *Post-check `STOP` race*: Enqueue a spawn request while `M1` is `ready` (pausing `M1` or holding `M1` before dispatch), enter `_iter_consuming_result` / `iter_task_realtime_events` so the waiter is already parked before `STOP` arrives, deliver `STOP` to `M1`, and assert the waiter wakes on `M1`'s `draining`/`stopped` registry publication, starts `M2`, and returns the completed result.
     3. *Peer `.internal_reserved` protection*: Assert `M2._cleanup_stale_internal_reserved_queues()` preserves `T{M1}.internal_reserved` while `M1` has a live `status="draining"` record.
   - **Done when**: All three regression tests fail RED on `HEAD` and pass GREEN on SQLite and PostgreSQL.

## 6. Testing Plan

- **Harnesses**: Real `WeftTestHarness` (`tests/helpers/weft_harness.py`), real SQLite + PostgreSQL brokers, and real `subprocess.Popen` child processes (`[TS-1]`).
- **Protected invariants**: `[WT-2a.2]` / `[WT-4.2]` / `[OBS.13.9]` (`test_retention_prune.py`), `[WT-2a.13]` / `[OBS.12a]` (`test_task_commands.py`), `[CLI-0.1]` (`test_cli_queue.py`, `test_context.py`), `[WT-4.2]` / `[LIVENESS.R5]` / `[WT-2a.9]` (`test_runner.py`, `test_task_interactive.py`, `test_manager_services.py`), and `[WT-2a.14]` / `[MA-1.4]` / `[MF-5]` (`test_manager_process.py`, `test_run.py`).

## 7. Verification and Gates

```bash
./bin/mypy-check
./.venv/bin/ruff check weft tests bin
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python bin/check-doc-paths --self-test && ./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/check-dom15-fixtures
./.venv/bin/python -m pytest -m "" tests/specs/ tests/architecture/ tests/commands/ tests/context/ tests/core/ tests/tasks/ tests/system/ tests/cli/ -q
./bin/pytest-pg --all
```

## 8. Independent Review Loop

### 8.1 Author's `[WT-7]` Theory Fit Account

- **Verdict**: Fits with strain.
- **Rests on**:
  - `[WT-2a.2]` (*Queues are the only durable truth*) & `[WT-4.2]` (*One owner for each resource*): custom/shared queues remain externally owned and untouched by ordinary foreground `retention` / `task-local` prune (`[OBS.13.9]`), and `Popen` / `BaseProcess` creators retain exclusive `waitpid` reap ownership.
  - `[WT-2a.7]` (*Each reservation executes at most once*): peer Managers preserve a live `draining` Manager's `.internal_reserved` queue (`[MA-3]`).
  - `[WT-2a.9]` (*One current contract*): `force_kill_pids` is replaced outright by `(pid, create_time)` `force_kill_processes` with zero dual fields.
  - `[WT-2a.12]`, `[WT-2a.13]` & `[WT-4.3]` (*Independent lifetimes; death needs proof*; *Availability and death are different questions*): `stop_task` refuses `report_unknown` (`[OBS.12a]`), and `Manager` never sends `SIGKILL` from `create_time=None` handles (`[LIVENESS.R3]`, `[LIVENESS.R5]`).
  - `[WT-2a.14]`, `[WT-2a.6]` & `[WT-4.1]` (*Submission never blocks*; *A wake predicate reads facts and guarantees progress*): `Manager._begin_shutdown_drain` freezes `_drain_stop_targets` and publishes `status="draining"`, while pre-spawn accepted-submission waiters include `weft.state.services` in their `QueueChangeMonitor` set to recover Manager availability on durable registry transitions without polling timers.
- **Refuses**:
  - `[ALT-WT-002]` (no fixed polling timer caps in result/event waiters).
  - `[ALT-WT-006]` (no automatic task-work requeue; only unlaunched Manager spawn requests follow `_apply_reserved_policy`).
  - `[ALT-WT-007]` (a draining Manager finishes its drain and exits; a fresh Manager `M2` launches new work).
  - `[ALT-WT-009]` for completion scanning (`weft.log.tasks` is never scanned by waiters) and `[ALT-WT-012]` (no dual `force_kill_pids` compatibility field).
- **Predicts**:
  - Submitting work via `cmd_run(..., wait=True)` or `Task.follow()` while `M1` is draining (or immediately before `M1` receives `STOP`) starts `M2` and completes without `M1` stopping `M2`'s child or stranding the waiter; `stop_task` on a `SIGSTOP`ped `Consumer` raises `ControlRejected`; `cmd_task_stop` on an interactive task with a `0.5s` `SIGTERM` handler exits cleanly with `cancelled`; a `RunnerHandle` with `host_pids=[pid]` and `host_processes=()` kills zero host processes.
- **Fits worst**:
  - `[ALT-WT-009]` states *"A waiter with known configuration watches its own queues only."* Including `weft.state.services` in `_result_observation_names` on the accepted-submission `QueueChangeMonitor` registration (kept stable across the wait as an implementation tradeoff to avoid monitor/connection teardown churn on first task-local activity, while `_check_prespawn_recovery_needed` stops recovery reads once `weft.state.tasks.<tid>` or result/control activity appears; see `## Deviation Log`) stretches that literal wording and exposes accepted-submission waiters to low-frequency service-registry owner-row wakeups in order to satisfy `[WT-2a.14]` (*Submission never blocks*) when a Manager `STOP` lands after `submit`'s initial availability check.

### 8.2 Blind Other-Family `[WT-7]` Fit Account & Divergence Classification

- **Reviewer family & isolated invocation (`2026-11-10`)**: OpenAI Codex CLI (`codex exec --skip-git-repo-check -s read-only -C /tmp/weft-wt7-blind-6cb1b97f/p1 -c 'model_reasoning_effort="high"'`, thread `01a1c763-b87d-7c03-be1e-eb3dd0030e37`), given **only** `program-theory.md` and `boundary.diff` (zero plan prose, review logs, or author account).
- **Blind account summary**:
  - *Verdict*: `fits with strain` (valid 5-part account).
  - *Rests on*: `[WT-3]`, `[WT-2a.13]`, and `[WT-4.3]` (availability vs death, `report_unknown` rejection, `(pid, create_time)` verification, `draining` vs dispatch-eligible); `[WT-4.2]` (single owner for standard task queues and `Popen`/`BaseProcess` reaping); `[WT-2a.7]` (draining peer `.internal_reserved` custody); `[WT-2a.2]` (drain state as queue fact); `[WT-2a.6]`, `[WT-4.1]`, `[WT-2a.14]`, and `[ALT-WT-007]` (registry wake input and starting a fresh Manager rather than reviving a draining one); `[WT-1]` and `[WT-4.4]` (context discovery, `create_dirs=False`, `@alias` parity, `SpecNotFound`).
  - *Refuses*: `[WT-2a.13]` (treating `unknown` as termination), `[WT-4.2]` (competing cleanup/reap owners), `[ALT-WT-002]` (fixed polling caps), `[ALT-WT-009]` (`weft.log.tasks` scans), `[ALT-WT-007]` (reusing a stopping/superseded Manager).
  - *Predicts*: Custom/shared queues untouched by ordinary prune; `report_unknown` never increments accepted count; missing `create_time` never kills a PID; interactive `KILL` skips stdin-EOF wait while graceful `STOP` keeps bounded `SIGTERM`; queued submission during Manager drain launches via recovery without duplicate execution while bare-TID `weft result` starts no Manager.
  - *Fits worst*: `[ALT-WT-009]` (*"A waiter with known configuration watches its own queues only"*): watching `weft.state.services` pre-launch exceeds that task-local-only sentence and could wake pre-spawn waiters on shared registry refreshes.
- **Divergence classification**:
  - **Pre-comparison agreement on governing entries**: Both accounts independently grounded the boundary in `[WT-2a.13]` / `[WT-4.3]` (availability vs death, `report_unknown` rejection, `(pid, create_time)` verification), `[WT-4.2]` (single resource/reap owner), `[WT-2a.7]` (draining peer `.internal_reserved` custody), `[WT-2a.2]` (drain status as queue fact), and `[WT-2a.14]` / `[ALT-WT-002]` / `[ALT-WT-007]` (event-driven Manager availability recovery without polling timers or reviving a draining Manager).
  - **Initial divergence & accepted refinement (`[ALT-WT-009]`)**: The author's original pre-comparison account returned `Fits` and named `[WT-2a.14]`'s accepted-submission vs bare-TID observation split as its worst-fit tension without citing `[ALT-WT-009]`. The blind account returned `fits with strain` and located the sharper tension in `[ALT-WT-009]` (*"A waiter with known configuration watches its own queues only"* vs watching shared `weft.state.services` pre-launch). Classified as an **author-account omission resolved in §8.1 above** (not a theory or code deficiency): `_result_observation_names` keeps the accepted-submission watcher set stable as an implementation tradeoff to avoid recreating `QueueChangeMonitor` on first task-local activity (while `[SB-0.4]` itself permits dynamic replacement; see `## Deviation Log`), `_check_prespawn_recovery_needed` skips recovery reads immediately once `weft.state.tasks.<tid>` or result/control activity appears, and `horizon` gating (`latest_pending_timestamp`) performs pre-spawn recovery reads only when `weft.state.services` actually changes while `M1` is not `ready`.

### 8.3 Cross-Family Plan Review Log

- **Pre-split reviews (`2026-11-10`)**: Codex Rounds 1–6 (`01a1c709-d416-76b3-b3de-feafb3caac56`) and Claude empirical probe review (`dbbf5526`) established the §3.2 triage exclusions and identified post-check Manager `STOP` stranding (`[WT-2a.14]`), dead `_interactive_on_stop()`, single-contract `force_kill_processes` (`[WT-2a.9]`), and exact `[WT-*]` citation hygiene.
- **Post-split Round 1 (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `BLOCKED` (`F1`, `F2`, `F3`, plus `F8`, `F9`, `F10` affecting Plan 1).
  - **Dispositions (all verified against live code and applied in place)**:
    - `F1` (`[P2]`, Task 3.1 `_begin_shutdown_drain` ordering): **Accept (`fixed in plan`)** — `Manager._begin_shutdown_drain` now freezes `self._drain_stop_targets = self._collect_drain_stop_targets()` *after* `_apply_reserved_policy(policy)` and *before* publishing `status="draining"` so `M2`'s newly launched tasks cannot be swept into `M1`'s `STOP` target snapshot; updated §4c.2B and Task 3.1 test to assert synchronized concurrency while `M1` is still draining.
    - `F2` (`[P2]`, Task 2.2 interactive `STOP` budget vs `_stop_resolved_tasks` `2.0s` fallback): **Accept (`fixed in plan`)** — added `INTERACTIVE_STDIN_EOF_GRACE_SECONDS = 0.5` so stdin-EOF wait (`<= 0.5s`) + `~1.5s` remaining `CommandSession.terminate` teardown budget (`~0.75s` `SIGTERM` grace + `~0.75s` escalation/reap) complete inside `INTERACTIVE_STOP_GRACE_SECONDS` / `CONTROL_SURFACE_WAIT_TIMEOUT` (`2.0s`), verified via `cmd_task_stop` against a `0.5s` `SIGTERM` handler.
    - `F3` (`[P2]`, Task 3.1 `observe_spawn_request_custody` signature): **Accept (`fixed in plan`)** — updated Task 3.1 and §4.2 to the exact live signature `observe_spawn_request_custody(context, tid, reserved_routes=(), broker=broker)`.
    - `F8`, `F9`, `F10` (`[P3]`, `[WT-2a.6]`/`[WT-4.3]`/`[OBS.13.7]` citations, `_task_local_candidates` symbol name, and adding `weft_microsandbox` `/tmp` non-bug to §3.2): **Accept (`fixed in plan`)**.
- **Post-split Round 2 (`2026-11-10`)**:
  - `F1`, `F3`, `F8`, `F9`, `F10` verified resolved on `01a1c763-c263-7b31-96a5-17058e503b35`; accepted `R2-2` (`[P3]`: state `~1.5s` accurately as remaining teardown budget -> `~0.75s` `SIGTERM` grace under `CommandSession.terminate`'s `remaining / 2.0` split) and `R2-3` (`[P3]`: distinguish pre-comparison author `Fits` account from post-comparison `[ALT-WT-009]` incorporation in §8.2).
- **Post-split Round 3 Scoped Verification (`2026-11-10`)**:
  - **Reviewer family & invocation**: OpenAI Codex CLI (`codex exec -C /Users/van/Developer/weft resume -c 'sandbox_mode="read-only"' -c 'model_reasoning_effort="high"' 01a1c763-c263-7b31-96a5-17058e503b35`).
  - **Verdict**: `PASS` (zero open findings; `R2-2` and `R2-3` verified resolved).

### 8.4 Post-Implementation Completion Review

- **Reviewer family**: OpenAI Codex CLI (`codex exec -s read-only -C /Users/van/Developer/weft -c 'model_reasoning_effort="high"'`, thread `01a1c827-8bfd-7cd0-961a-1dddc2318f0e`)
- **Round 1 (`2026-11-10`)**: Reviewed full `833dd4b8..working-tree` diff across Slices 1, 2, and 3; verified Slice 1 and Slice 2 implementations clean and identified 3 Slice-3 findings (`[P2]` pre-spawn recovery ignoring observer `timeout` / `cancel_event`, `[P2]` `0.05s` polling caps in `_wait_ambiguous_backlog_grace` and `_wait_manager_registry_change` violating `[ALT-WT-002]`, and `[P3]` Strategy B spec/plan synchronization on stable `_result_observation_names` membership). All 3 reproduced RED and fixed.
- **Rounds 2–3 (`2026-11-10`)**: Scoped follow-up passes on thread `01a1c827-8bfd-7cd0-961a-1dddc2318f0e` verified observer deadline threading into nested `_registry_view` / `_await_manager_start_settlement`, non-poisoning of `probe_cache` on interrupted probes, `[SB-0.4]` Deviation Log rationale accuracy, and caught that `watcher._strategy.notify_activity` in `weft/core/control_probe.py` only armed a local flag without interrupting a parked blocking wait; replaced the `cancel_event` probe wait path with `QueueChangeMonitor` + `monitor.wake()` and added `test_send_keyed_ping_probe_wakes_immediately_on_cancel_event` (`RED` `0.559s` -> `GREEN` `< 0.15s` on SQLite and PostgreSQL 18).
- **Round 4 (`2026-11-10`)**: Scoped verification on thread `01a1c827-8bfd-7cd0-961a-1dddc2318f0e` verified immediate probe cancellation wake (`0.0001s`), callback deregistration, and cleanup with zero remaining findings.
- **Round 5 (`2026-11-10`)**: Full-suite verification surfaced two edge-case regressions (`_await_launcher_first_line` positional fallback when `deadline` and `cancel_event` are `None` in `weft/core/manager_runtime.py`, and `_interactive_drain_remaining` waiting event-driven on `_interactive_activity` for `not session.is_alive()` when pipe EOF precedes `_wait_for_exit` lock release on `KILL` in `weft/core/tasks/interactive.py`). Both reproduced `RED` (`test_first_launcher_event_retains_child_ownership[cancelled]`, `test_interactive_kill_waits_for_exit_publication_after_pipe_eof`), fixed `GREEN`, and verified on thread `01a1c827-8bfd-7cd0-961a-1dddc2318f0e` with zero findings.
- **Round 6 (`2026-11-10`)**: Full PostgreSQL 18 verification (`./bin/pytest-pg --all`) surfaced a Manager drain wakeup race in `weft/core/manager.py` when an in-flight internal service launch (`heartbeat-service`) in `_active_child_launches` completed mid-turn after `_continue_shutdown_drain()`, leaving `phase_active` false and `_manager_drain_timeouts` returning `[]` while `self._draining` remained `True`. Reproduced `RED` in `test_draining_manager_wakes_immediately_when_last_launch_commits_after_drain_step`, fixed `GREEN` across `_clear_active_child_launch`, `_manager_drain_timeouts`, and `_process_manager_reactor_turn` (plus aligning `_stop_all_active_managers_and_wait` in `tests/commands/test_run.py` with `MANAGER_SHUTDOWN_DRAIN_TIMEOUT_SECONDS`), and verified on thread `01a1c827-8bfd-7cd0-961a-1dddc2318f0e` with zero findings.
- Final Completion Review Verdict: APPROVE

## 9. Out of Scope

- Agent runtime, pipeline stage validation, `weft_docker`, and `weft_microsandbox` fixes (`docs/plans/2026-11-10-agent-pipeline-and-extension-bug-remediation-plan.md`).
- Extension `py.typed` markers, `mypy` test-tree errors, release/CI gates, and standalone spec sync (`docs/plans/2026-11-10-extension-typing-and-spec-sync-plan.md`).
- `[REV-WT-007]` `[WT-7]` plan-metadata gate and theory revision (`docs/plans/2026-11-10-program-theory-fit-cadence-and-gate-plan.md`).
- All 17 items in §3.2 Triaged Out.

## 10. Fresh-Eyes Review

- Completed post-split author fresh-eyes pass and Round-1/2/3 disposition audit (`2026-11-10`): verified `_begin_shutdown_drain` target-freezing order, `2.0s` caller/session `STOP` budget alignment (`0.5s` stdin-EOF + `~1.5s` remaining teardown -> `~0.75s` `SIGTERM` grace), `observe_spawn_request_custody(..., reserved_routes=(), ...)` signature, exact `[WT-*]`/`[OBS.*]` citations, and byte-for-byte `Replacement (...)` matches against `HEAD = dbbf5526`.

## Revision Log

| Date | Baseline | Section | Change and reason | Re-review required? |
|------|----------|---------|-------------------|---------------------|
| 2026-11-10 | `dbbf5526` (post-split R1–R3) | §2, §3, §4c.2B, Tasks 1.1, 2.2, 3.1, §8.2, §8.3 | Resolved Codex `F1`–`F3`, `F8`–`F10`, `R2-2`, `R2-3` and recorded blind `[WT-7]` fit account (`01a1c763-b87d-7c03-be1e-eb3dd0030e37`). | Completed (`Verdict: PASS` on `01a1c763-c263-7b31-96a5-17058e503b35`) |
