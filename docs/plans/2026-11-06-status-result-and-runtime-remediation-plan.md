# Status, Result Observation, and Runtime Boundary Remediation Plan

Status: completed
Source specs: `docs/specifications/01-Core_Components.md` ([CC-2.2.1], [CC-3.5]); `docs/specifications/05-Message_Flow_and_State.md` ([MF-5], [MF-6]); `docs/specifications/07-System_Invariants.md` ([OBS.15], [MANAGER.11]); `docs/specifications/12-Pipeline_Composition_and_UX.md` ([PL-1.5], [PL-4.4]); `docs/specifications/13-Agent_Runtime.md` ([AR-2.1])
Superseded by: none
Plan author family: `antigravity` (Google Gemini model family)
Independent reviewer family: `codex` (OpenAI model family, invoked via `skills/call-agent/SKILL.md`)

Class: 5 (implementation with spec revision)

## 1. Goal

Remediate the verified defects and spec-to-code boundary gaps surfaced by the 2026-11-06 (`v0.9.122`, `709fb78112f797f60c4dea6c709d2c062830211d`) deep-dive review and subsequent design reviews across five independently verifiable, commit-isolated, and independently revertible phases:

1. **Phase 1 — Superseded Manager Snapshot Reconciliation (`[OBS.15]`, `[MF-5]`)**: Reconcile non-terminal `weft.state.tasks.<tid>` manager projections (`role: "manager"`) across `weft status`, `weft status --all`, and `weft task status <tid>` when a different active canonical manager has been selected from `weft.state.services`, using three-state runtime liveness evidence (`"live"`, `"non_live"`, `"unknown"`) per `[WT-4.3]`:
   - a **confirmed non-live** superseded manager (`manager_liveness == "non_live"`, e.g. a `SIGKILL`ed manager whose `host-pid` process `(pid, create_time)` is dead) can never finish a drain and is reconciled to `status="failed"` with `reconciliation={"classification": "superseded_manager_record", "reason": "different_active_manager_selected", "lifecycle_status": "failed", "active_manager_tid": active_manager_tid}`;
   - a **live or uncertain** non-canonical manager (`manager_liveness in {"live", "unknown"}` — either a briefly live manager still draining during convergence per `[ALT-WT-007]`, or a record with an absent `runtime_handle`, unsupported control authority, or log-only history fold with no runtime observation per `[WT-4.3]`) preserves `status="running"` (retaining `activity` and `waiting_on`) with `reconciliation={"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": status, "active_manager_tid": active_manager_tid, "runtime_liveness": manager_liveness}`; and
   - an **already-terminal** historical manager projection (`completed`, `stopped`, `failed`, `killed`, `cancelled`, `timeout`) is preserved unchanged (`reconciliation=None`).
2. **Phase 2 — Spec Revision & Settlement Refinement for Unconfigured Terminal Results Under Explicit Timeout (`[MF-5]`)**: Revise `[MF-5]` and update `_CollectedResult._diagnostic`, `_wait_unconfigured_result`, and `_iter_consuming_result` in `weft/commands/_result_wait.py` so that when a task has visible terminal evidence (`materialized.terminal_evidence is not None`) but no usable `observation` configuration (`materialized.config is None`), `weft result <tid> --timeout <N>` waits out the `WEFT_COMPLETED_RESULT_GRACE_SECONDS` (`0.5s`) completion-grace deadline when `terminal.classification == "wrapper_lost"` (via `decision.wait_until`) and:
   - if the caller's `--timeout` budget expires *before* that `wrapper_lost` grace window completes (`deadline < grace_deadline`), preserves `CommandTimeoutError` (CLI exit 124);
   - once terminal evidence is settled (`terminal.classification != "wrapper_lost"`, or `grace_deadline <= deadline` has elapsed), returns immediately with `status="unknown"`, `error="observation metadata unavailable"`, and `reconciliation={"classification": "observation_metadata_unavailable", "observed_terminal_status": terminal.status}` (CLI exit 1) because observation configuration can never arrive after termination.
3. **Phase 3 — Pipeline Control Propagation (`PAUSE` / `RESUME`) and Manager Internal-Lane Reserved Policy (`[PL-1.5]`, `[PL-4.4]`, `[MF-6]`, `[MANAGER.11]`)**:
   - Broadcast `CONTROL_PAUSE` and `CONTROL_RESUME` unconditionally on every explicit `PAUSE` or `RESUME` command in `PipelineTask._handle_control_command` (`weft/core/tasks/pipeline.py`) to all stage and edge `ctrl_in` queues (so repeating `PAUSE` or `RESUME` repairs any partial child write failure without acknowledgement tracking), preserve and restore the coordinator's pre-pause `(activity, waiting_on)` on `RESUME`, and republish the `P{tid}.status` snapshot.
   - Make `Manager._apply_reserved_policy` (`weft/core/manager.py`) iterate `self._spawn_reserved_queue_pairs()` whether `message_timestamp` is `None` or an exact message timestamp, so an unhandled `process_once` exception during an internal spawn-request turn applies the configured `ReservedPolicy` to `T{manager}.internal_reserved` / `weft.spawn.internal` as well as the public spawn lane.
4. **Phase 4 — One-Shot Host Terminal Handoff Sentinel Wake and Codex Ambient `CODEX_HOME` Concurrency Boundary Documentation (`[CC-2.2.1]`, `[CC-3.5]`, `[AR-2.1]`)**:
   - Upgrade `HostTaskRunner._run_one_shot_terminal_handoff` and `HostTaskRunner._terminal_handoff_wait_seconds` (`weft/core/runners/host.py`) to multiplex `process.sentinel` via `wait_terminal_payload(..., producer_sentinel=producer_sentinel, timeout=wait_for)` and gate the `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s`) fallback deadline on `cancel_requested is not None or not has_producer_sentinel`, while preserving execution timeout (`start_time + self._timeout`), active resource-monitor sampling (`next_monitor_at`), and post-exit drain expiry (`drain_deadline`). This adds immediate wake on `process.sentinel` for all one-shot host runs and removes the 50ms poll floor for direct runner calls without a `cancel_requested` callable (`Consumer`-driven runs pass `cancel_requested=self._worker_cancel_requested` and therefore retain the 50ms cancellation poll until cancellation is converted into a waitable event).
   - Document the `codex exec resume --last` ambient `CODEX_HOME` concurrency boundary in `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]`).
5. **Phase 5 — Cross-Cutting Verification, `[WT-0]` Theory Audit Refresh, `[WT-7]` Two-Factor Fit Test, and Plan Closeout**: Run full repository verification gates (`ruff check`, `ruff format --check`, `mypy`, `pytest -m ""`, `pytest extensions integrations -m ""`, `./bin/pytest-pg --all tests`, `test_plan_metadata.py`, `bin/check-doc-paths`, `bin/coalesce-check`), execute the blind other-family `[WT-7]` two-factor fit test over the fixed change diff and `docs/program-theory.md`, refresh the `[WT-0]` audit table, and flip this plan's status to `completed` in both `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md` and `docs/plans/README.md`.

## 2. Source Documents

Read in this exact order before touching any file:

1. `docs/program-theory.md` — `[WT-0]` (Audit Before You Touch), `[WT-1]` (Four Foundational Commitments), `[WT-2]` (`[WT-2a.1]`–`[WT-2a.15]` Identity Invariants), `[WT-3]` (Placement Map), `[WT-4]` (`[WT-4.1]`–`[WT-4.4]` Design Principles, including `[WT-4.3]` Availability vs Death and `[WT-4.4]` No Over-Armoring), `[WT-5]` (Refusal Taxonomy), `[WT-6]` (`[ALT-WT-002]`, `[ALT-WT-007]`, `[ALT-WT-011]`, `[ALT-WT-015]`, `[ALT-WT-017]`, `[ALT-WT-021]`), `[WT-7]` (Tensions and Falsifiers, Two-Factor Fit Test).
2. `docs/agent-context/decision-hierarchy.md` — precedence order, `Classify Before the Preflight`, `Task Classification [DOM-15]`, and spec promotion rules.
3. `docs/agent-context/principles.md` and `docs/agent-context/engineering-principles.md` — SimpleBroker layer boundary, red-green TDD, anti-mocking boundaries.
4. `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/hardening-plans.md`, and `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md` — Class-5 plan structure, Promotion Strategy B rules, and Tier-1 cross-family review protocol.
5. `docs/specifications/07-System_Invariants.md` — `[OBS.15]` (lines 731–733: manager task snapshots vs `weft.state.services`) and `[MANAGER.11]` (lines 222–228: public and internal spawn lanes).
6. `docs/specifications/05-Message_Flow_and_State.md` — `[MF-5]` (lines 1117–1120, 1191–1232: read-only status/result observation, manager supersession reconciliation, and unresolved result settlement) and `[MF-6]` (lines 1345–1385: control handling).
7. `docs/specifications/12-Pipeline_Composition_and_UX.md` — `[PL-1.5]` (lines 52–60: pipelines are normal tasks) and `[PL-4.4]` (lines 673–700: pipeline control handling and fail-fast propagation).
8. `docs/specifications/01-Core_Components.md` — `[CC-2.2.1]` (lines 736–775: reactor/handoff waits) and `[CC-3.5]` (lines 1296–1334: one-shot and session terminal handoff reducer and transport).
9. `docs/specifications/13-Agent_Runtime.md` — `[AR-2.1]` (lines 245–274: `provider_cli` providers and session continuation).
10. Code modules under modification:
    - `weft/commands/_task_snapshot_reducer.py`
    - `weft/commands/system.py`
    - `weft/commands/tasks.py`
    - `weft/commands/_result_wait.py`
    - `weft/core/tasks/pipeline.py`
    - `weft/core/manager.py`
    - `weft/core/runners/host.py`

### Comprehension Questions

An implementer must be able to answer these from the source documents before writing any code:

1. **Q:** Why does a killed manager (`SIGKILL`) remain `status="running"` in `weft status` and `weft task status <dead_mgr_tid>` even after `select_active_manager` selects a replacement manager and removes the dead manager's row from `weft.state.services`, and why must Phase 1 use three-state liveness (`"live"`, `"non_live"`, `"unknown"`) rather than a boolean `_task_process_alive` check?
   **A:** Every manager publishes its own task projection to `weft.state.tasks.<tid>` (`role: "manager"`, `terminal: False`, `projection.status: "running"`). Direct projection reduction (`reduce_task_state_snapshot` at `weft/commands/_task_snapshot_reducer.py:494-551`) does not accept `selected_active_manager_tid` or `manager_liveness`, `_collect_task_snapshot_records` (`weft/commands/system.py:540-585`) and `_merge_raw_task_history` (`system.py:497-537`) pass `selected_active_manager_tid=None`, and `_read_point_observation` (`weft/commands/tasks.py:472-511`) reduces the projection without consulting `weft.state.services`. Per `[WT-4.3]` ("Availability and death are different questions ... A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death") and `[ALT-WT-007]` (a superseded manager performs a one-way drain until owned children finish), a plain boolean `_task_process_alive` check returns `False` not only when a `host-pid` process is confirmed dead, but also when `runtime_handle` is absent or has an unsupported control authority, and `_merge_raw_task_history` supplies `runtime_observation=None`. Using three-state liveness (`"live"`, `"non_live"`, `"unknown"`) reconciles a superseded manager to `status="failed"` (`classification="superseded_manager_record"`) **only** on positive `non_live` evidence, while preserving `status="running"` with `classification="superseded_manager_draining"` (`runtime_liveness: "live" | "unknown"`) when the manager is confirmed alive or liveness is unknown.
2. **Q:** In `weft/commands/_result_wait.py`, what happens when a task's runner process dies (`wrapper_lost` on `T{tid}.ctrl_out`) before publishing `weft.state.tasks.<tid>`, and what precedence applies when the caller's `--timeout` is shorter than the `0.5s` `WEFT_COMPLETED_RESULT_GRACE_SECONDS` window?
   **A:** `_CollectedResult._diagnostic` (`_result_wait.py:750-779`) currently returns `None` unconditionally at line 767 whenever `self.deadline is not None`, `_wait_unconfigured_result` (`_result_wait.py:879-906`) ignores `decision.wait_until` on line 905, and `_iter_consuming_result` (`_result_wait.py:1006-1017`) only returns after `_wait_unconfigured_result` when `deadline is None`. Under the revised `[MF-5]` policy, when `terminal is not None` and `self.mode is None`: if `self.deadline is not None` and `terminal.classification == "wrapper_lost"`, `_diagnostic` computes `grace = self._grace_deadline(materialized)` and returns `None` whenever `(grace is not None and self.deadline < grace) or not self._expired(grace, now)`. Thus `settle()` either waits until `min(self.deadline, grace)` or—whenever `self.deadline < grace` and `self._expired(self.deadline, now)` is `True` (even if `now` is evaluated after `grace` due to scheduling delay)—returns `_ResultSettlement("timeout")` (CLI exit 124). Only when `grace <= self.deadline` and `grace` has elapsed does `_diagnostic` return `_ResultSettlement("result", "unknown", "observation metadata unavailable", reconciliation={"classification": "observation_metadata_unavailable", "observed_terminal_status": terminal.status})` (CLI exit 1).
3. **Q:** Why must `HostTaskRunner._terminal_handoff_wait_seconds` (`weft/core/runners/host.py:871-889`) keep `start_time + self._timeout`, `next_monitor_at`, and `drain_deadline` when gating `ACTIVE_CONTROL_POLL_INTERVAL`, and why do `Consumer`-driven runs still retain the 50ms poll interval?
   **A:** `start_time + self._timeout` bounds total target execution time, `next_monitor_at` drives active resource-limit sampling when a monitor is configured, and `drain_deadline` bounds the post-exit/post-stop pipe drain window (`TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS`). `Consumer` passes `cancel_requested=self._worker_cancel_requested` (`weft/core/tasks/consumer.py:416, 484, 501`), which is a boolean callable rather than an OS waitable handle, so `Consumer`-driven runs still poll cancellation every 50ms while gaining immediate wake on `process.sentinel`; direct `HostTaskRunner` calls with `cancel_requested=None` eliminate the 50ms floor altogether.

## 3. Context and Root-Cause Analysis

### 3.1 Phase 1 Root Cause & Policy Judgment: Superseded Manager Task Projection (`[OBS.15]`, `[MF-5]`, `[WT-4.3]`, `[ALT-WT-007]`)

`docs/specifications/07-System_Invariants.md` (`[OBS.15]`, lines 731–733) and `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]`, lines 1117–1119) require:
> *"manager task snapshots must not publish a non-active historical manager row as `running` when `weft.state.services` has selected a different active manager."*

When a manager process starts, `BaseTask._publish_task_state_snapshot` writes a canonical task-state projection to `weft.state.tasks.<mgr_tid>` with `role: "manager"`, `terminal: False`, and `projection.status = "running"`. If that manager process is later killed (`SIGKILL`) and a replacement manager `M2` is started:
1. `select_active_manager` (`weft/core/manager_runtime.py`) selects `M2` and deletes `M1`'s stale row from `weft.state.services`.
2. However, `weft.state.tasks.<M1_tid>` remains on its per-task state queue (`LivenessMonitor` is the sole component deleter of `weft.state.tasks.*` after the 2,400-second minimum-age fence per `[LIVENESS.R3]`).
3. In `weft/commands/_task_snapshot_reducer.py:494-551`, `reduce_task_state_snapshot(tid, payload, *, now_ns)` is a pure projection reducer that does not accept `selected_active_manager_tid` or `manager_liveness` and therefore reduces `M1`'s projection to `status="running"`, `reconciliation=None`.
4. In `weft/commands/system.py:540-585`, `_collect_task_snapshot_records` reduces `weft.state.tasks.<M1_tid>` via `reduce_task_state_snapshot` without `selected_active_manager_tid`. Even when `include_log_history=True`, `_merge_raw_task_history` (`system.py:497-537`) skips any `tid` already in `current` when `existing.snapshot.reconciliation is None` (lines 515–519) and passes `selected_active_manager_tid=None` on line 532.
5. In `weft/commands/tasks.py:472-511`, `_read_point_observation` reduces `weft.state.tasks.<M1_tid>` via `reduce_task_state_snapshot` without `selected_active_manager_tid`, so `weft task status <M1_tid>` also reports `status="running"`.

**Explicit Policy Judgment on Three-State Manager Liveness (`"live"` / `"non_live"` / `"unknown"`, `[WT-4.3]`, `[ALT-WT-007]`, `[OBS.15]`)**:
- Selection of a canonical Manager in `weft.state.services` establishes **service leadership authority**, whereas process/terminal evidence establishes **task lifecycle completion** (`[WT-4.3]`).
- Under `[ALT-WT-007]`, when two Managers race during bootstrap or a newer Manager supersedes an older one, the losing Manager detects supersession on its next leadership check (bounded by `MANAGER_CONVERGENCE_WINDOW_SECONDS = 2.0s` or the next reactor turn) and enters a one-way drain: it stops reading spawn queues, lets already-owned child tasks run to completion, and then writes a normal terminal projection (`completed` or `stopped`) and exits.
- Crucially, `[WT-4.3]` requires that missing or unsupported probes remain `unknown`, never proof of death. In `weft/commands/system.py:299-307`, `_task_process_alive(mapping_entry)` returns `False` not only when a `host-pid` process is confirmed dead, but also when `runtime_handle` is absent (`None`), has no `scoped_host_pids()`, or uses a non-`host-pid` control authority; similarly, `_merge_raw_task_history` supplies `runtime_observation=None`.
- We therefore classify Manager runtime liveness with a three-state helper `_manager_runtime_liveness(mapping_entry) -> Literal["live", "non_live", "unknown"]` using `inspect_host_process(pid, create_time)` from `weft.liveness.host` (and matching `RuntimeObservation` classification in `_select_snapshot_policy`):
  - **`"live"`**: `handle.control.get("authority") == "host-pid"` with non-empty `handle.scoped_host_processes()` where any `inspect_host_process(pid, create_time).evidence == "live"` (or in `_select_snapshot_policy`, `evidence.runtime_observation is not None and evidence.runtime_observation.live`).
  - **`"non_live"`**: positive non-live runtime evidence — `handle.control.get("authority") == "host-pid"` with non-empty `handle.scoped_host_processes()` where every `inspect_host_process(pid, create_time).evidence == "stale"` (`process_absent`, `process_zombie`, or `identity_mismatch`; or in `_select_snapshot_policy`, `evidence.runtime_observation is not None and not evidence.runtime_observation.live and evidence.runtime_observation.strength == "strong"`).
  - **`"unknown"`**: no runtime handle (`handle is None`), empty `scoped_host_processes()`, missing exact identity (`create_time is None`, which `inspect_host_process` reports as `evidence="unknown", reason="missing_exact_identity"` and `_runtime_evidence_details` reports as `strength="weak"`), a failed OS probe (`psutil.AccessDenied` or `psutil.Error`, which `inspect_host_process` reports as `evidence="unknown", reason="process_unresolved"`), non-`host-pid` authority in direct projection reduction, or `evidence.runtime_observation is None` / `evidence.runtime_observation.strength != "strong"` in `_select_snapshot_policy`.
- When a non-terminal Manager record (`is_manager` and `status not in TERMINAL_TASK_STATUSES`) has `selected_active_manager_tid is not None` and `tid != selected_active_manager_tid`:
  - **If `manager_liveness == "non_live"`** (positive proof that the Manager runtime is not live, e.g. a `SIGKILL`ed local Manager): the superseded Manager can never complete a drain or publish a terminal event. Both `reduce_task_state_snapshot` and `_select_snapshot_policy` reconcile it to `status = "failed"`, `activity = None`, `waiting_on = None`, and `reconciliation = _superseded_manager_reconciliation(active_manager_tid=selected_active_manager_tid)`:
    ```python
    {
        "classification": "superseded_manager_record",
        "reason": "different_active_manager_selected",
        "lifecycle_status": "failed",
        "active_manager_tid": active_manager_tid,
    }
    ```
  - **If `manager_liveness in {"live", "unknown"}`** (either confirmed alive and draining during convergence, or runtime liveness is unknown per `[WT-4.3]`): both `reduce_task_state_snapshot` and `_select_snapshot_policy` preserve `status` as `"running"` (retaining `activity` and `waiting_on`) and attach `_draining_manager_reconciliation(active_manager_tid=selected_active_manager_tid, lifecycle_status=status, runtime_liveness=manager_liveness)`:
    ```python
    {
        "classification": "superseded_manager_draining",
        "reason": "different_active_manager_selected",
        "lifecycle_status": status,
        "active_manager_tid": active_manager_tid,
        "runtime_liveness": manager_liveness,
    }
    ```
  - **If `status in TERMINAL_TASK_STATUSES`**: the Manager has already finished its drain and published a terminal status (`completed`, `stopped`, `failed`, etc.), which is preserved unchanged with `reconciliation = None`.
- **Preserving `_merge_raw_task_history` Fallback for Ordinary Tasks**: In `weft/commands/system.py:513-519`, `_merge_raw_task_history` currently skips a non-terminal log record when `existing is not None and existing.snapshot.reconciliation is None`, allowing an ordinary task whose current `weft.state.tasks.<tid>` entry has an invalid or unavailable projection (`projection_unavailable` or `projection_invalid`) to fall back to usable log history. Rather than skipping every `existing is not None`, we preserve that fallback for ordinary tasks and protect reconciled Manager projections specifically by skipping when `existing.snapshot.reconciliation is None or existing.snapshot.reconciliation.get("classification") in {"superseded_manager_record", "superseded_manager_draining"}`.

### 3.2 Phase 2 Root Cause & Spec Revision: Unconfigured Terminal Result Settlement Under `--timeout` (`[MF-5]`)

Currently, `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]`, lines 1215–1220) states:
> *"Without timeout, unresolved result acquisition returns unknown with `observation_metadata_unavailable` reconciliation (CLI exit 1). With finite timeout, discovery waits until the original caller deadline and raises the existing observation-timeout error (CLI exit 124), without publishing synthetic task timeout."*

The existing code in `weft/commands/_result_wait.py` implements that literal rule, which produces a pathological wait when a task's runner crashes (`wrapper_lost` on `T{tid}.ctrl_out`) before `BaseTask` publishes `weft.state.tasks.<tid>` (or when a task publishes a terminal projection without a usable `observation` section): once terminal evidence is visible and any `wrapper_lost` completion-grace window (`WEFT_COMPLETED_RESULT_GRACE_SECONDS = 0.5s`) has elapsed, observation configuration can **never** arrive, yet `weft result <tid> --timeout <N>` blocks for the caller's entire `--timeout` and exits 124.

Three sites in `weft/commands/_result_wait.py` participate in this behavior:
1. `_CollectedResult._diagnostic` (`_result_wait.py:750-779`) checks `if self.deadline is not None: return None` at line 767 before inspecting `terminal = materialized.terminal_evidence`.
2. `_wait_unconfigured_result` (`_result_wait.py:879-906`) waits on `monitor.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))` at line 905 instead of using `decision.wait_until` (which `collected.settle` computes at lines 862–875 as `min(self.deadline, grace)` when `materialized.terminal_evidence is not None`).
3. `_iter_consuming_result` (`_result_wait.py:1006-1017`) only returns after `_wait_unconfigured_result` when `deadline is None` (line 1015).

**Precedence Between Short Caller Timeouts and `wrapper_lost` Grace**:
When `self.mode is None`, `terminal is not None`, `terminal.classification == "wrapper_lost"`, and `self.deadline is not None`:
- `_diagnostic` computes `grace = self._grace_deadline(materialized)` and returns `None` whenever `(grace is not None and self.deadline < grace) or not self._expired(grace, now)`.
- Inside `_CollectedResult.settle` (`_result_wait.py:845-875`), if `self.deadline < grace` (the caller's `--timeout` budget precedes the `0.5s` `wrapper_lost` grace deadline, e.g. `observed_at=10.0`, `grace=10.5`, `deadline=10.1`), `_diagnostic` always yields to `settle()`, which returns `_ResultSettlement("wait", wait_until=10.1)` while `now < 10.1` and `_ResultSettlement("timeout")` (CLI exit 124) once `now >= 10.1` (including if `settle()` is evaluated late at `now=10.5`).
- When `grace <= self.deadline` and `self._expired(grace, now)` is `True` (e.g. `observed_at=10.0`, `grace=10.5`, `deadline=10.5` or `20.0`, `now >= 10.5`), `_diagnostic` runs before `if expired:` in `settle()` and returns `_ResultSettlement("result", "unknown", "observation metadata unavailable", reconciliation={"classification": "observation_metadata_unavailable", "observed_terminal_status": terminal.status})` (CLI exit 1).

### 3.3 Phase 3 Root Cause: Pipeline `PAUSE` / `RESUME` Propagation & Manager Internal-Lane Reserved Policy (`[PL-1.5]`, `[PL-4.4]`, `[MF-6]`, `[MANAGER.11]`)

1. **Pipeline `PAUSE` / `RESUME`**:
   - Both stage `Consumer` tasks (`weft/core/tasks/consumer.py:165-173`) and `PipelineEdgeTask` tasks (`weft/core/tasks/pipeline.py:158-171`) implement `CONTROL_PAUSE` and `CONTROL_RESUME`, and `BaseTask._handle_control_command` (`weft/core/tasks/base.py:1757, 1766`) makes repeated `PAUSE` or `RESUME` commands idempotent on children.
   - However, `PipelineTask._handle_control_command` (`weft/core/tasks/pipeline.py:908-945`) only intercepts `CONTROL_STATUS`, `CONTROL_STOP`, and `CONTROL_KILL`, falling through to `super()._handle_control_command(request, context)` for `CONTROL_PAUSE` and `CONTROL_RESUME`.
   - Furthermore, `_broadcast_control` (`pipeline.py:835-863`) catches individual child queue write errors as best-effort. If `PipelineTask` gated `_broadcast_control(CONTROL_PAUSE)` or `_broadcast_control(CONTROL_RESUME)` on `if not self._paused:` / `if self._paused:`, a transient child write failure on the first `PAUSE` would flip `self._paused = True` on the coordinator and prevent a retry `PAUSE` from repairing the missed child. Broadcasting on every explicit `CONTROL_PAUSE` and `CONTROL_RESUME` request allows retry recovery without acknowledgement tracking.
   - Finally, `PipelineTask` must save its pre-pause `(self._activity, self._waiting_on)` tuple when transitioning into `paused` and restore that saved activity on `CONTROL_RESUME` (rather than hardcoding `"waiting"` if paused during `"bootstrapping"` or while waiting on a specific queue).
2. **Manager Internal-Lane Reserved Policy**:
   - `Manager._apply_reserved_policy` (`weft/core/manager.py:6394-6416`) is called by `BaseTask._process_reactor_turn` (`weft/core/tasks/base.py:3039`) when an unhandled exception escapes `_process_message`.
   - When `message_timestamp is None`, `Manager._apply_reserved_policy` iterates `for reserved_queue, source_queue in self._spawn_reserved_queue_pairs():`.
   - When `message_timestamp is not None` (`weft/core/manager.py:6401-6408`), it hardcodes `source_queue=self._queue_names["inbox"]` (`weft.spawn.requests`) and `reserved_queue=self._queue_names["reserved"]` (`T{manager}.reserved`), skipping `weft.spawn.internal` and `T{manager}.internal_reserved`.

### 3.4 Phase 4 Root Cause: One-Shot Host Terminal Handoff Sentinel Wake & Codex `CODEX_HOME` Boundary (`[CC-2.2.1]`, `[CC-3.5]`, `[AR-2.1]`)

1. **One-Shot Host Terminal Handoff Sentinel Wake**:
   - In `weft/core/runners/sessions.py:481-503, 779-812`, `AgentSession.execute` uses `wait_terminal_payload(self._response_receiver, producer_sentinel=producer_sentinel, timeout=wait_for)` (`weft/core/terminal_handoff_transport.py:249-302`) and `AgentSession._terminal_handoff_wait_seconds` gates the `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s`) fallback deadline on `cancel_requested is not None`.
   - In `weft/core/runners/host.py:723-734, 871-889`, `HostTaskRunner._run_one_shot_terminal_handoff` still calls `poll_terminal_payload(response_receiver, wait_for)` without `producer_sentinel` and unconditionally includes `now + ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s`) in `_terminal_handoff_wait_seconds`.
   - Passing `producer_sentinel = None if "producer_exited" in progress.consumed_edge_kinds else getattr(process, "sentinel", None)` into `wait_terminal_payload` and gating `ACTIVE_CONTROL_POLL_INTERVAL` on `cancel_requested is not None or not has_producer_sentinel` (while keeping `start_time + self._timeout`, active `next_monitor_at`, and `drain_deadline`) wakes immediately on `process.sentinel` even if a descendant holds the pipe open, and removes the 50ms timer floor for direct runner calls where `cancel_requested is None` (`Consumer`-driven runs pass `cancel_requested=self._worker_cancel_requested` at `weft/core/tasks/consumer.py:416, 484, 501` and therefore keep the 50ms cancellation poll until cancellation becomes a waitable event).
2. **Codex Session Continuation Concurrency Boundary (`[AR-2.1]`)**:
   - `CodexProvider.build_session_invocation` (`weft/core/agents/provider_cli/registry.py:495`) uses `codex exec resume --last` when continuing a persistent session because `codex exec` has no creation-time `--session-id` flag and reads `auth.json` and `config.toml` from `CODEX_HOME` (`~/.codex`). We document this explicit concurrency boundary in `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]`).
   - Note on `OpencodeProvider.build_*_invocation` tool-profile validation: both production entry boundaries (`prepare_provider_cli_turn` at `weft/core/agents/provider_cli/execution.py:100` and `ProviderCLISession._validate_startup` at `weft/core/agents/backends/provider_cli.py:76`) already call `provider.validate_tool_profile(...)` before `build_invocation` / `build_session_invocation` regardless of `preflight`. Per `[WT-4.4]` ("No over-armoring: Trust internal callers and data shapes once validated at the entry boundary"), we do not add duplicate `validate_tool_profile` calls inside `build_*_invocation` (recorded in Section 9 `Out of Scope`).

## 4. Invariants, Difference Registers, Rollout, and Rollback

### 4.1 Invariants Preserved (`[WT-2a]`, `07-System_Invariants.md`)

- **`[WT-2a.1]` / `[IMMUT.3]` (TID format and immutability)**: Untouched; `message_timestamp` in `Manager._apply_reserved_policy` remains the exact 64-bit SimpleBroker hybrid timestamp.
- **`[WT-2a.2]` / `[OBS.15]` / `[LIVENESS.R3]` (Queues are the only source of truth; read-only observation; single task-state deleter)**: Neither `weft status`, `weft task status`, nor `weft result` writes to or deletes from any queue during reconciliation or settlement. Manager supersession is derived at read time by comparing a non-terminal manager projection's `tid` against the selected active canonical manager `tid` from `weft.state.services` and evaluating three-state runtime liveness (`"live"`, `"non_live"`, `"unknown"`).
- **`[WT-2a.3]` / `[STATE.1]`, `[STATE.2]` (Forward-only state transitions)**: Superseded manager snapshots are reconciled to `"failed"` at read time only when `projection["status"] not in TERMINAL_TASK_STATUSES` and `manager_liveness == "non_live"`; live or uncertain (`"live"` / `"unknown"`) managers stay `"running"` (`superseded_manager_draining`) and terminal manager records (`completed`, `failed`, `stopped`, `killed`, `cancelled`, `timeout`) are never overwritten.
- **`[WT-2a.6]` / `[CC-2.2.1]` (Single arbiter over real readiness/deadlines, no polling wait-caps)**: Phase 2 adds zero polling loops (reusing `_grace_deadline` and `decision.wait_until`), and Phase 4 adds `process.sentinel` wakeup to `HostTaskRunner._run_one_shot_terminal_handoff` while removing the `ACTIVE_CONTROL_POLL_INTERVAL` floor when `cancel_requested is None` and `process.sentinel` is present.
- **`[WT-2a.7]` / `[QUEUE.6]`, `[EXEC.1]` (Reserved queue policy)**: `Manager._apply_reserved_policy` honors `ReservedPolicy` symmetrically across both `(T{manager}.reserved, weft.spawn.requests)` and `(T{manager}.internal_reserved, weft.spawn.internal)`.

### 4.2 Difference Registers

1. **Phase 1 Difference Register (`reduce_task_state_snapshot` vs `_select_snapshot_policy`)**:
   - Both `reduce_task_state_snapshot` (`_task_snapshot_reducer.py:494-551`) and `_select_snapshot_policy` (`_task_snapshot_reducer.py:447-457`) evaluate non-terminal manager records when `selected_active_manager_tid is not None` and `tid != selected_active_manager_tid` using three-state liveness (`"live"`, `"non_live"`, `"unknown"`):
     - When `manager_liveness == "non_live"` (`evidence.runtime_observation is not None and not evidence.runtime_observation.live and evidence.runtime_observation.strength == "strong"` in `_select_snapshot_policy`): status becomes `"failed"`, `activity = None`, `waiting_on = None`, and `reconciliation = _superseded_manager_reconciliation(active_manager_tid=selected_active_manager_tid)` (`{"classification": "superseded_manager_record", "reason": "different_active_manager_selected", "lifecycle_status": "failed", "active_manager_tid": selected_active_manager_tid}`).
     - When `manager_liveness in {"live", "unknown"}` (either confirmed alive, or missing handle / missing exact `create_time` identity (`strength == "weak"`) / failed OS probe (`AccessDenied`) / unsupported control authority / log-only history fold where `evidence.runtime_observation is None` or `strength != "strong"`): status remains `"running"` (preserving `activity` and `waiting_on`) and `reconciliation = _draining_manager_reconciliation(active_manager_tid=selected_active_manager_tid, lifecycle_status=status, runtime_liveness=manager_liveness)` (`{"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": status, "active_manager_tid": selected_active_manager_tid, "runtime_liveness": manager_liveness}`).
2. **Phase 2 Difference Register (`_CollectedResult._diagnostic` and `settle()` with and without `deadline`)**:
   - When `self.mode is None` (`materialized.config is None`) and `terminal is None`: behavior is unchanged (`self.deadline is None` returns `observation_metadata_unavailable`; `self.deadline is not None` returns `None` so `settle()` waits until `self.deadline` and returns `"timeout"` / CLI exit 124 if no configuration arrives).
   - When `self.mode is None` and `terminal is not None`:
     - If `self.deadline is not None` and `terminal.classification == "wrapper_lost"`, `_diagnostic` computes `grace = self._grace_deadline(materialized)` and returns `None` whenever `(grace is not None and self.deadline < grace) or not self._expired(grace, now)`. In `settle()`, if `self.deadline < grace`, `settle()` waits until `self.deadline` and then returns `_ResultSettlement("timeout")` (CLI exit 124) once `self._expired(self.deadline, now)` is `True` (even if `now >= grace`). If `grace <= self.deadline` and `not self._expired(grace, now)`, `settle()` returns `_ResultSettlement("wait", wait_until=grace)` and `_wait_unconfigured_result` waits until `decision.wait_until`.
     - Otherwise (once `grace <= self.deadline` has elapsed, or immediately when `terminal.classification != "wrapper_lost"` or `self.deadline is None`), `_diagnostic` returns `_ResultSettlement("result", "unknown", "observation metadata unavailable", reconciliation={"classification": "observation_metadata_unavailable", "observed_terminal_status": terminal.status})` (CLI exit 1), and `_iter_consuming_result` yields that `TaskResult` and returns.
3. **Phase 4 Difference Register (`HostTaskRunner._run_one_shot_terminal_handoff` vs `AgentSession.execute`)**:
   - Both one-shot host execution (`weft/core/runners/host.py`) and session execution (`weft/core/runners/sessions.py`) use `wait_terminal_payload(response_receiver, producer_sentinel=producer_sentinel, timeout=wait_for)`.
   - `AgentSession._terminal_handoff_wait_seconds` (`sessions.py:779-811`) gates `ACTIVE_CONTROL_POLL_INTERVAL` on `if cancel_requested is not None:`, whereas `HostTaskRunner._terminal_handoff_wait_seconds` gates `ACTIVE_CONTROL_POLL_INTERVAL` on `if cancel_requested is not None or not has_producer_sentinel:` so any host process object lacking a `sentinel` attribute safely falls back to the 50ms poll interval.
   - `HostTaskRunner._terminal_handoff_wait_seconds` preserves all three one-shot deadlines (`start_time + self._timeout`, `next_monitor_at`, and `drain_deadline`). Direct runner callers with `cancel_requested=None` and a valid `process.sentinel` have no 50ms poll floor; `Consumer`-driven calls pass `cancel_requested=self._worker_cancel_requested` and keep the 50ms cancellation poll while gaining immediate wake on `process.sentinel`.

### 4.3 Rollout and Rollback

- Each of the 5 phases is a self-contained commit (spec update + tests + implementation for Phases 1–4; cross-cutting audit refresh, two-factor fit test, and plan closeout in Phase 5).
- There are no persistent schema changes, queue name changes, or wire-format migrations.
- Any phase can be rolled back independently with `git revert <phase-commit-sha>` without breaking adjacent phases.

## 4a. Deviation Log

- **Phase 1–2 Mid-Plan Review Refinements (`3b0f5a18`, `e2a98d9c`)**:
  - Updated `select_active_manager` in `weft/core/manager_runtime.py` to pass `prune_stale=probe_stale` into `_registry_view` so passive point status (`task_status`) never sends a `PING` to `T{mgr_tid}.ctrl_in` when an active manager registry record has `liveness == "unknown"`.
  - Updated `_selected_active_manager_tid` in `weft/commands/system.py` to delegate `managers` snapshots to `manager_runtime._select_active_manager_from_snapshot` (stripping `MANAGER_PONG_LIVE_AT_KEY` from copies) so bulk status (`system_status`) and point status (`task_status`) apply identical passive canonical selection rules.
  - Added an explicit guard in `iter_task_realtime_events` (`weft/commands/events.py`) when `materialized.config is None` after the initial `snapshot` turn (`if materialized.config is None and not follow: return`) so non-following realtime event reads do not settle an unconfigured terminal task on Turn 2 while `follow=True, timeout=None` continues to settle via `collected.settle`.
- **Phase 4 Mid-Plan Review Refinements (`08314b51`)**:
  - Updated `HostTaskRunner._run_one_shot_terminal_handoff` (`weft/core/runners/host.py`) to pass `now=time.monotonic()` (fresh post-sample clock reading) into `self._terminal_handoff_wait_seconds(...)`, matching `AgentSession.execute`, and added `test_host_one_shot_terminal_handoff_recomputes_wait_against_fresh_clock_after_monitor_sample`.
  - Updated `_leaking_function_target` in `tests/core/test_terminal_handoff_transport.py` to check `hasattr(candidate, "send_bytes") and hasattr(candidate, "poll")`, set `multiprocessing.current_process().daemon = False`, and call `_leak_sender_to_grandchild(result_sender, pidfile)` for POSIX and Windows portability, and asserted `outcome.diagnostics["handoff_event"] == "drain_expired"` (matching `runner_diagnostics` top-level `extra` merge).
- **Phase 5 Full-Suite Test Contract Alignment**:
  - Updated `test_terminal_projection_without_routes_cannot_collect_result` in `tests/commands/test_task_owned_observation.py` so `timeout=0.0` asserts the revised `[MF-5]` unconfigured terminal settlement (`status == "unknown"`, `classification == "observation_metadata_unavailable"`, `observed_terminal_status == "failed"`) rather than `CommandTimeoutError`.
  - Updated `test_host_blocking_poll_failure_is_consumed_as_next_turn_transport_failure` and `test_one_shot_stop_after_begin_drain_preserves_first_deadline` in `tests/tasks/test_runner.py` to stub `host_module.wait_terminal_payload` (matching the existing `sessions_module.wait_terminal_payload` tests at lines 4066–4206).
  - Updated `test_manager_apply_reserved_policy_with_exact_timestamp_covers_internal_spawn_lane` in `tests/core/test_manager.py` to pass `int` arguments (`1`, `2`) to `make_child_spec` for full-repo `mypy` compliance.

## 4b. Spec Baseline

Fixed repository and specification baseline for this plan:
- **Pre-Promotion Spec Baseline Commit**: `709fb78112f797f60c4dea6c709d2c062830211d` (`v0.9.122`)
- **Promotion Baseline Recording Rule (Strategy B, `writing-plans.md` §4b / §4d)**: After each phase's spec-and-code slice lands in Phases 1–4, record that phase's resulting commit SHA (or `<diff-base-sha> + dirty (<spec-paths>)` before commit) as the **promotion baseline identifier** where the proposed spec delta was applied to `docs/specifications/`, and verify `tests/specs/test_plan_metadata.py` and `bin/check-doc-paths` against that promoted state.
- **Recorded Phase Promotion Baseline Commits**:
  - **Phase 1 (`[OBS.15]`, `[MF-5]`)**: `3b0f5a18` (`Reconcile superseded manager snapshots with three-state liveness`)
  - **Phase 2 (`[MF-5]`)**: `e2a98d9c` (`Settle unconfigured terminal results under explicit timeout`)
  - **Phase 3 (`[PL-4.4]`, `[MANAGER.11]`)**: `0650cd15` (`Broadcast pipeline pause/resume and apply manager internal reserved policy`)
  - **Phase 4 (`[CC-3.5]`, `[AR-2.1]`)**: `08314b51` (`Multiplex producer sentinel in one-shot host terminal handoff`)
- **Touched Specifications at Baseline `709fb78112f797f60c4dea6c709d2c062830211d`**:
  - `docs/specifications/07-System_Invariants.md` (`[OBS.15]` at lines 731–733; `[MANAGER.11]` at lines 222–228)
  - `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]` at lines 1117–1119, 1215–1220; `[MF-6]` at lines 1345–1385)
  - `docs/specifications/12-Pipeline_Composition_and_UX.md` (`[PL-1.5]` at lines 52–60; `[PL-4.4]` at lines 673–685)
  - `docs/specifications/01-Core_Components.md` (`[CC-2.2.1]` at lines 736–775; `[CC-3.5]` at lines 1329–1334)
  - `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]` at lines 258–261)

## 4c. Proposed Spec Delta

### Promotion Strategy

**Strategy B — Atomic spec + code in the same phase commit** (`docs/agent-context/runbooks/writing-plans.md` §4d). No new spec section codes are introduced; each phase refines an existing section code (`[OBS.15]`, `[MF-5]`, `[PL-4.4]`, `[CC-3.5]`, `[AR-2.1]`) and updates its nearby implementation notes in the same commit as the corresponding tests and implementation. Every `Replacement (<path>):` block below states its exact target line span in checkout `709fb78112f797f60c4dea6c709d2c062830211d` and preserves all existing clauses.

### Phase 1 Spec Delta (`docs/specifications/07-System_Invariants.md` lines 731–733 and `docs/specifications/05-Message_Flow_and_State.md` lines 1117–1119)

Target span in `docs/specifications/07-System_Invariants.md` (lines 731–733):
> `- **OBS.15**: manager task snapshots must not publish a non-active historical`
> `  manager row as \`running\` when \`weft.state.services\` has selected a different`
> `  active manager.`

Replacement (`docs/specifications/07-System_Invariants.md`):
```markdown
- **OBS.15**: manager task snapshots must not publish a non-active historical
  manager row as `running` when `weft.state.services` has selected a different
  active manager and positive non-live runtime evidence exists; both direct
  `weft.state.tasks.<tid>` snapshot reduction (`weft status`, `weft status --all`,
  and `weft task status <tid>`) and historical `weft.log.tasks` folding
  reconcile a non-terminal superseded manager record with confirmed non-live
  runtime evidence to `status="failed"` (`classification:
  "superseded_manager_record"`), while a superseded manager whose runtime is
  live or unknown (`runtime_liveness: "live" | "unknown"`) reports
  `status="running"` (`classification: "superseded_manager_draining"`). (Plan:
  `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`)
```

Target span in `docs/specifications/05-Message_Flow_and_State.md` (lines 1117–1119):
> `- manager task snapshots must respect the selected active manager from`
> `  \`weft.state.services\`; a historical non-terminal manager task row must not be`
> `  published as \`running\` when a different active manager has been selected`

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
- manager task snapshots must respect the selected active manager from
  `weft.state.services`; a non-terminal superseded manager task row with
  positive non-live runtime evidence must not be published as `running` when a
  different active manager has been selected, whether reduced directly from
  `weft.state.tasks.<tid>` (`weft status`, `weft status --all`,
  `weft task status <tid>`) or through snapshot policy reduction
  (`status="failed"`, `reconciliation.classification="superseded_manager_record"`),
  whereas a superseded manager whose runtime is live or unknown preserves
  `status="running"` with
  `reconciliation.classification="superseded_manager_draining"`
  (`runtime_liveness: "live" | "unknown"`)
```

### Phase 2 Spec Delta (`docs/specifications/05-Message_Flow_and_State.md` lines 1215–1220)

Target span in `docs/specifications/05-Message_Flow_and_State.md` (lines 1215–1220):
> `Without timeout, unresolved result acquisition returns unknown with`
> `\`observation_metadata_unavailable\` reconciliation (CLI exit 1). With finite`
> `timeout, discovery waits until the original caller deadline and raises the`
> `existing observation-timeout error (CLI exit 124), without publishing synthetic`
> `task timeout. Unresolved task`
> `control routes produce a typed error before any write (CLI exit 1).`

Replacement (`docs/specifications/05-Message_Flow_and_State.md`):
```markdown
Without timeout, unresolved result acquisition returns unknown with
`observation_metadata_unavailable` reconciliation (CLI exit 1). With finite
timeout, if terminal evidence is visible (`terminal_projection` without usable
observation configuration, or `wrapper_lost` whose completion-grace window
completes at or before the caller deadline), unresolved result acquisition
settles with unknown and `observation_metadata_unavailable` reconciliation
(including `observed_terminal_status`, CLI exit 1); if no terminal evidence is
visible, or if the caller deadline expires before a `wrapper_lost`
completion-grace window completes, discovery raises the existing
observation-timeout error at the caller deadline (CLI exit 124), without
publishing synthetic task timeout. Unresolved task control routes produce a
typed error before any write (CLI exit 1).
```

### Phase 3 Spec Delta (`docs/specifications/12-Pipeline_Composition_and_UX.md` lines 673–685)

Target span in `docs/specifications/12-Pipeline_Composition_and_UX.md` (lines 673–685; lines 686–700 `Baseline fail-fast propagation` remain untouched):
> `### Stop and kill [PL-4.4]`
> ...
> `Control should stay visible through normal task control queues and task-log`
> `events.`

Replacement (`docs/specifications/12-Pipeline_Composition_and_UX.md`):
```markdown
### Stop, kill, pause, and resume [PL-4.4]

Target state:

- stopping a pipeline should stop the active stage task and mark the pipeline
  terminal according to the normal task-control rules
- killing a pipeline should kill the active stage task and terminate the
  pipeline run
- pausing a pipeline should broadcast `PAUSE` to stage and edge `ctrl_in`
  queues on every explicit `PAUSE` request, mark the pipeline paused
  (`control_pause`), and republish `P{pipeline_tid}.status` with
  `"activity": "paused"`
- resuming a paused pipeline should broadcast `RESUME` to stage and edge
  `ctrl_in` queues on every explicit `RESUME` request, clear the pipeline
  paused state (`control_resume`), restore the coordinator's pre-pause
  activity, and republish `P{pipeline_tid}.status`
- already-completed stages remain completed

Control should stay visible through normal task control queues and task-log
events (`weft/core/tasks/pipeline.py::PipelineTask._handle_control_command`;
plan: `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`).
```

### Phase 4 Spec Delta (`docs/specifications/01-Core_Components.md` lines 1329–1334 and `docs/specifications/13-Agent_Runtime.md` lines 258–261)

Target span in `docs/specifications/01-Core_Components.md` (lines 1329–1334):
> `_Implementation mapping_: \`weft/core/terminal_handoff.py\` owns the pure reducer,`
> `same-turn selectors, and edge-consumption driver;`
> `\`weft/core/terminal_handoff_transport.py\` owns synchronous private framing;`
> `\`weft/core/runners/host.py\` and \`weft/core/runners/sessions.py\` acquire`
> `observations, apply effects, and own cleanup.`

Replacement (`docs/specifications/01-Core_Components.md`):
```markdown
_Implementation mapping_: `weft/core/terminal_handoff.py` owns the pure reducer,
same-turn selectors, and edge-consumption driver;
`weft/core/terminal_handoff_transport.py` owns synchronous private framing and
sentinel-multiplexed readiness (`wait_terminal_payload`);
`weft/core/runners/host.py` (`HostTaskRunner._run_one_shot_terminal_handoff`,
`HostTaskRunner._terminal_handoff_wait_seconds`) and
`weft/core/runners/sessions.py` (`AgentSession.execute`,
`AgentSession._terminal_handoff_wait_seconds`) acquire observations, apply
effects, and own cleanup. (Plan:
`docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`)
```

Target span in `docs/specifications/13-Agent_Runtime.md` (lines 258–261):
> `  container boundary, not the Codex sandbox, is the isolation there. Persistent`
> `  Codex sessions apply cwd and sandbox flags on the first \`exec\` turn that`
> `  creates the session; resumed turns use \`codex exec resume\` and only repeat`
> `  flags that the resume subcommand accepts.`

Replacement (`docs/specifications/13-Agent_Runtime.md`):
```markdown
  container boundary, not the Codex sandbox, is the isolation there. Persistent
  Codex sessions apply cwd and sandbox flags on the first `exec` turn that
  creates the session; resumed turns use `codex exec resume --last` and only
  repeat flags that the resume subcommand accepts. Because `codex exec` has no
  creation-time session-ID flag and resolves `auth.json` and `config.toml` from
  `CODEX_HOME` (`~/.codex`), concurrent host `codex` tasks that require
  isolated native session history must supply distinct `CODEX_HOME` paths via
  `spec.env` or use container runtime isolation. (Plan:
  `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md`)
```

## 5. Tasks (Multi-Phase Breakdown)

### Global Execution Rules, Anti-Mocking Boundaries, and Stop Conditions

- **Red-Green TDD**: In every phase, write the new test(s) first, run pytest to observe the exact expected RED failure, implement the minimal code and spec change, and re-run to observe GREEN.
- **Anti-Mocking Boundaries**:
  - Do **not** mock SimpleBroker `Queue`, `WeftContext`, `reduce_task_state_snapshot`, `_CollectedResult`, `PipelineTask`, `Manager._apply_reserved_policy`, or `wait_terminal_payload` in tests.
  - Use real SQLite-backed queues via `workdir` / `build_context`, real `HostTaskRunner` child processes via `mp_spawn_context()` for host terminal handoff tests, and literal `_CollectedResult` state transitions.
- **Stop Conditions**:
  - Stop immediately and update Section 4a (`Deviation Log`) if any phase appears to require mutating queue state from a read-only status/result path, introducing a new polling timer floor, or lowering pytest worker concurrency (`-n 0`).

---

### Phase 1: Superseded Manager Snapshot Reconciliation (`[OBS.15]`, `[MF-5]`)

**Goal**: Reconcile non-terminal `weft.state.tasks.<tid>` manager projections (`role: "manager"`) across `weft status`, `weft status --all` (both direct projection and historical `weft.log.tasks` folding), and `weft task status <tid>` when a different active canonical manager has been selected from `weft.state.services` using three-state liveness (`"live"`, `"non_live"`, `"unknown"`): mark confirmed non-live superseded managers (`manager_liveness == "non_live"`) as `status="failed"` with `classification="superseded_manager_record"`, preserve `status="running"` with `classification="superseded_manager_draining"` (`runtime_liveness: "live" | "unknown"`) when the manager is live or liveness is unknown (absent handle, unsupported authority, or log-only fold), preserve already-terminal manager projections, and keep `_merge_raw_task_history`'s log fallback for ordinary tasks with invalid/unavailable projections.

**Files to Modify**:
- `weft/commands/_task_snapshot_reducer.py` (`_select_snapshot_policy` at lines 447–457, `reduce_task_state_snapshot` at lines 494–551, and `_draining_manager_reconciliation` next to `_superseded_manager_reconciliation` at lines 675–681)
- `weft/core/manager_runtime.py` (`_manager_registry_disposition` at line 229, `manager_record` at line 1044, `list_manager_records` at line 1054, and `select_active_manager` at line 1094: honor `probe_stale` so passive `probe_stale=False` callers never trigger `_manager_record_has_matched_pong` or send a control `PING`, and accept `Mapping` in `manager_registry_record_liveness`)
- `weft/commands/system.py` (`_collect_manager_records` at line 214 passing `probe_stale=False`, `_manager_runtime_liveness` delegating to `manager_runtime.manager_registry_record_liveness` at line 306, `_selected_active_manager_tid` using `manager_runtime._select_active_manager_from_snapshot`, `_merge_raw_task_history` at lines 497–537, `_collect_task_snapshot_records` at lines 540–585, `system_status` at lines 1399–1450)
- `weft/commands/tasks.py` (`_read_point_observation` at lines 472–511 and `_enrich_known_snapshot` at lines 878–916)
- `docs/specifications/07-System_Invariants.md` (`[OBS.15]`, lines 731–733)
- `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]`, lines 1117–1119)
- `tests/commands/test_task_snapshot_reducer.py`, `tests/commands/test_status.py`, and `tests/commands/test_task_commands.py`

**Step 1.1 — RED Tests**:
1. In `tests/commands/test_task_snapshot_reducer.py`:
   - Update `test_reduce_task_snapshot_handles_stale_and_manager_reconciliation` (`lines 436-447`) to cover all `RuntimeObservation` states on a superseded manager:
     - `RuntimeObservation(live=False, evidence="host-pid", strength="strong")` (`non_live`) -> `status == "failed"` and `reconciliation == {"classification": "superseded_manager_record", "reason": "different_active_manager_selected", "lifecycle_status": "failed", "active_manager_tid": OTHER_TID}`.
     - `RuntimeObservation(live=True, evidence="host-pid", strength="strong")` (`live`) -> `status == "running"` and `reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": OTHER_TID, "runtime_liveness": "live"}`.
     - `RuntimeObservation(live=False, evidence="host-pid", strength="weak")` (missing exact `create_time` identity -> `unknown`) and `runtime_observation=None` (or `RuntimeObservation(live=False, evidence="none", strength="unknown")`, `unknown`) -> `status == "running"` and `reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": OTHER_TID, "runtime_liveness": "unknown"}`.
2. In `tests/commands/test_status.py`:
   - Add `test_reduce_task_state_snapshot_reconciles_non_live_live_and_unknown_superseded_managers_and_preserves_terminal_manager`:
     - Verify that `reduce_task_state_snapshot(old_mgr_tid, nonterminal_mgr_payload, now_ns=..., selected_active_manager_tid=new_mgr_tid, manager_liveness="non_live")` returns `status == "failed"`, `activity is None`, `waiting_on is None`, and `reconciliation == {"classification": "superseded_manager_record", "reason": "different_active_manager_selected", "lifecycle_status": "failed", "active_manager_tid": new_mgr_tid}`.
     - Verify that `reduce_task_state_snapshot(old_mgr_tid, nonterminal_mgr_payload, now_ns=..., selected_active_manager_tid=new_mgr_tid, manager_liveness="live")` returns `status == "running"`, preserves `activity`, and sets `reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": new_mgr_tid, "runtime_liveness": "live"}`.
     - Verify that `reduce_task_state_snapshot(old_mgr_tid, nonterminal_mgr_payload, now_ns=..., selected_active_manager_tid=new_mgr_tid, manager_liveness="unknown")` (the default when handle is absent, exact identity is missing, probe fails, or authority is unsupported) returns `status == "running"`, preserves `activity`, and sets `reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": new_mgr_tid, "runtime_liveness": "unknown"}`.
     - Verify that `_manager_runtime_liveness` returns `"unknown"` when `runtime_handle` is absent, when `host_processes` lacks `create_time` (missing exact identity), or when `psutil.Process` raises `psutil.AccessDenied` (failed OS probe), returns `"non_live"` when `psutil.Process` raises `psutil.NoSuchProcess` on a scoped `(pid, create_time)` identity, and returns `"live"` when `(pid, create_time)` matches the live current process.
     - Verify that a terminal manager payload (`terminal: True`, `projection.status: "completed"`) with `selected_active_manager_tid=new_mgr_tid` retains `status == "completed"` and `reconciliation is None`.
   - Update `test_cmd_status_marks_superseded_manager_record_failed` (`tests/commands/test_status.py:1850-1912`, whose `old_tid` fixture is written by `_write_task_log_entry` without a `runtime_handle`, i.e. `manager_liveness="unknown"`) so that `old_tid` keeps `status == "running"` in both `default_tasks` and `all=True` with `tasks[old_tid]["reconciliation"] == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": active_tid, "runtime_liveness": "unknown"}`.
   - Add `test_system_status_distinguishes_dead_live_absent_handle_and_invalid_projection_fallback`:
     - Build `context = build_context(spec_context=workdir)`.
     - Seed `weft.state.tasks.<dead_mgr_tid>` with a non-terminal manager projection whose `runtime_handle` has `authority="host-pid"` and a dead scoped `(pid=999999, create_time=100.0)` identity, `weft.state.tasks.<draining_mgr_tid>` with a non-terminal manager projection whose `runtime_handle` points to the live current test process (`os.getpid()` and `psutil.Process().create_time()`), `weft.state.tasks.<absent_handle_mgr_tid>` with a non-terminal manager projection with no `runtime_handle`, `weft.state.tasks.<missing_identity_mgr_tid>` with a non-terminal manager projection whose `runtime_handle` has `host_pids=[999999]` without `create_time`, and `weft.state.tasks.<invalid_task_tid>` with an ordinary task state entry whose `projection` is invalid (`projection: "corrupt"`) alongside a valid non-terminal `weft.log.tasks` entry for `invalid_task_tid`.
     - Seed `weft.state.services` and `weft.state.tasks.<new_mgr_tid>` with an active canonical manager `new_mgr_tid` (live current process PID).
     - Call `system_status(context, include_terminal=False)`, `system_status(context, include_terminal=True)`, and `system_status(context, include_terminal=True, include_log_history=True)`.
     - Assert that when `include_terminal=False`, `new_mgr_tid` (`status == "running"`, `reconciliation is None`), `draining_mgr_tid` (`status == "running"`, `reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": new_mgr_tid, "runtime_liveness": "live"}`), `absent_handle_mgr_tid` (`status == "running"`, `runtime_liveness == "unknown"`), and `missing_identity_mgr_tid` (`status == "running"`, `runtime_liveness == "unknown"`) appear while `dead_mgr_tid` is excluded as terminal; when `include_terminal=True`, `dead_mgr_tid` appears with `status == "failed"` and `reconciliation["classification"] == "superseded_manager_record"`; and when `include_terminal=True, include_log_history=True`, `dead_mgr_tid` keeps `status == "failed"` (`superseded_manager_record`), `draining_mgr_tid` keeps `runtime_liveness == "live"`, and `invalid_task_tid` falls back to its usable `weft.log.tasks` running record (`status == "running"`, `reconciliation is None`).
3. In `tests/commands/test_task_commands.py`, add `test_task_status_reconciles_dead_live_and_absent_handle_superseded_manager_projections`:
   - Build `context = build_context(spec_context=workdir)`.
   - Seed `dead_mgr_tid` (non-terminal in `weft.state.tasks.<dead_mgr_tid>` with dead scoped `(pid=999999, create_time=100.0)` `host-pid`), `draining_mgr_tid` (non-terminal in `weft.state.tasks.<draining_mgr_tid>` with live current process PID + `create_time`), `absent_handle_mgr_tid` (non-terminal in `weft.state.tasks.<absent_handle_mgr_tid>` without `runtime_handle`), and active `new_mgr_tid` in `weft.state.services`.
   - Call `task_status(dead_mgr_tid, context=context)` and assert `snapshot.status == "failed"`, `snapshot.activity is None`, `snapshot.waiting_on is None`, and `snapshot.reconciliation == {"classification": "superseded_manager_record", "reason": "different_active_manager_selected", "lifecycle_status": "failed", "active_manager_tid": new_mgr_tid}`.
   - Call `task_status(draining_mgr_tid, context=context)` and assert `snapshot.status == "running"` and `snapshot.reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": new_mgr_tid, "runtime_liveness": "live"}`.
   - Monkeypatch `weft.commands.system.STATUS_RUNTIMELESS_STALE_AFTER_SECONDS` to `0.0` (or test `_enrich_known_snapshot` with `last_timestamp=1`) so `_stale_liveness_reason` returns `"runtime_missing_after_stale_window"`, call `task_status(absent_handle_mgr_tid, context=context)`, and assert `snapshot.status == "running"` and `snapshot.reconciliation == {"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": "running", "active_manager_tid": new_mgr_tid, "runtime_liveness": "unknown"}` (confirming `_enrich_known_snapshot` preserves `superseded_manager_draining` when `_stale_liveness_reason` fires rather than overwriting it with `stale_liveness`).
4. Run pytest on those tests and confirm RED failure (`dead_mgr_tid` reports `status == "running"` and `reconciliation is None`).

**Step 1.2 — GREEN Implementation**:
1. In `weft/commands/_task_snapshot_reducer.py`:
   - Add `_draining_manager_reconciliation(*, active_manager_tid: str, lifecycle_status: str, runtime_liveness: Literal["live", "unknown"] = "unknown") -> dict[str, Any]` next to `_superseded_manager_reconciliation` (`lines 675-681`):
     ```python
     def _draining_manager_reconciliation(
         *,
         active_manager_tid: str,
         lifecycle_status: str,
         runtime_liveness: Literal["live", "unknown"] = "unknown",
     ) -> dict[str, Any]:
         return {
             "classification": "superseded_manager_draining",
             "reason": "different_active_manager_selected",
             "lifecycle_status": lifecycle_status,
             "active_manager_tid": active_manager_tid,
             "runtime_liveness": runtime_liveness,
         }
     ```
   - In `_select_snapshot_policy` (`lines 447-457`), update the superseded manager branch to require positive `strong` non-live runtime evidence before reconciling to `failed`:
     ```python
     if (
         lifecycle_status not in TERMINAL_TASK_STATUSES
         and (local_evidence is None or not local_evidence.terminal)
         and _is_manager_task_payload(taskspec)
         and evidence.selected_active_manager_tid is not None
         and record.tid != evidence.selected_active_manager_tid
     ):
         if (
             evidence.runtime_observation is not None
             and not evidence.runtime_observation.live
             and evidence.runtime_observation.strength == "strong"
         ):
             public_status = "failed"
             reconciliation = _superseded_manager_reconciliation(
                 active_manager_tid=evidence.selected_active_manager_tid,
             )
         else:
             runtime_liveness: Literal["live", "unknown"] = (
                 "live"
                 if (
                     evidence.runtime_observation is not None
                     and evidence.runtime_observation.live
                 )
                 else "unknown"
             )
             reconciliation = _draining_manager_reconciliation(
                 active_manager_tid=evidence.selected_active_manager_tid,
                 lifecycle_status=public_status,
                 runtime_liveness=runtime_liveness,
             )
     ```
   - In `reduce_task_state_snapshot` (`lines 494-551`), add keyword parameters `selected_active_manager_tid: str | None = None` and `manager_liveness: Literal["live", "non_live", "unknown"] = "unknown"`. After `projection` is parsed/defaulted, compute:
     ```python
     status = projection["status"]
     is_manager = (
         payload.get("role") == "manager" or projection["metadata"].get("role") == "manager"
     )
     if (
         diagnostic is None
         and status not in TERMINAL_TASK_STATUSES
         and is_manager
         and selected_active_manager_tid is not None
         and tid != selected_active_manager_tid
     ):
         if manager_liveness == "non_live":
             status = "failed"
             diagnostic = _superseded_manager_reconciliation(
                 active_manager_tid=selected_active_manager_tid
             )
         else:
             diagnostic = _draining_manager_reconciliation(
                 active_manager_tid=selected_active_manager_tid,
                 lifecycle_status=status,
                 runtime_liveness=manager_liveness,
             )
     ```
   - Use `status` in `TaskSnapshot(status=status, activity=_normalized_text(payload.get("activity")) if status not in TERMINAL_TASK_STATUSES else None, waiting_on=_normalized_text(payload.get("waiting_on")) if status not in TERMINAL_TASK_STATUSES else None, ...)`.
2. In `weft/commands/system.py`:
   - Pass `probe_stale=False` from `_collect_manager_records` to `manager_runtime.list_manager_records`, and add helper `_manager_runtime_liveness(mapping_entry: Mapping[str, Any] | None) -> Literal["live", "non_live", "unknown"]` next to `_task_process_alive` (`system.py:299-307`) delegating to `manager_runtime.manager_registry_record_liveness` so snapshot liveness shares the exact container PID-namespace and supervisor-probe policy:
     ```python
     def _manager_runtime_liveness(
         mapping_entry: Mapping[str, Any] | None,
     ) -> Literal["live", "non_live", "unknown"]:
         liveness = manager_runtime.manager_registry_record_liveness(mapping_entry)
         if liveness == "live":
             return "live"
         if liveness == "stale":
             return "non_live"
         return "unknown"
     ```
   - Add helper `_is_non_terminal_manager_state_entry(payload: Mapping[str, Any]) -> bool` checking `payload.get("terminal") is not True` and `(payload.get("role") == "manager" or (isinstance(payload.get("projection"), Mapping) and isinstance(payload["projection"].get("metadata"), Mapping) and payload["projection"]["metadata"].get("role") == "manager"))`.
   - Add helper `_selected_active_manager_tid(ctx: WeftContext, *, managers: Sequence[Mapping[str, Any]] | None = None, broker: Any | None = None) -> str | None` that delegates `managers` snapshots to `manager_runtime._select_active_manager_from_snapshot(ctx, snapshot)` and delegates `managers is None` to `manager_runtime.select_active_manager(ctx, broker=broker)` (where `_manager_registry_disposition` and `list_manager_records` in `weft/core/manager_runtime.py` honor `probe_stale=False` so passive bulk and point status callers never send a control `PING`).
   - In `_merge_raw_task_history` (`system.py:497-537`), add `selected_active_manager_tid: str | None = None`, preserve the existing fallback for ordinary tasks (`existing.snapshot.reconciliation is None`) while protecting reconciled Manager projections specifically:
     ```python
     existing = current.get(tid)
     existing_classification = (
         existing.snapshot.reconciliation.get("classification")
         if existing is not None and isinstance(existing.snapshot.reconciliation, Mapping)
         else None
     )
     if (
         record.status not in TERMINAL_TASK_STATUSES
         and existing is not None
         and (
             existing.snapshot.reconciliation is None
             or existing_classification
             in {"superseded_manager_record", "superseded_manager_draining"}
         )
     ):
         continue
     ```
     and pass `selected_active_manager_tid=selected_active_manager_tid` into `SnapshotEvidence(...)` at line 525.
   - In `_collect_task_snapshot_records` (`system.py:540-585`), add `selected_active_manager_tid: str | None = None`. When `selected_active_manager_tid is None` and (`include_log_history` or any `_is_non_terminal_manager_state_entry(payload)` for `payload` in `tid_state_entries.values()`), resolve `selected_active_manager_tid = _selected_active_manager_tid(ctx, broker=broker)`. For each `tid, payload` in `tid_state_entries`, compute `manager_liveness = _manager_runtime_liveness(payload) if (selected_active_manager_tid is not None and tid != selected_active_manager_tid and _is_non_terminal_manager_state_entry(payload)) else "unknown"` and pass `selected_active_manager_tid=selected_active_manager_tid, manager_liveness=manager_liveness` to `reduce_task_state_snapshot`, and pass `selected_active_manager_tid=selected_active_manager_tid` to `_merge_raw_task_history`.
   - In `system_status` (`system.py:1399-1450`), pass `selected_active_manager_tid=_selected_active_manager_tid(context, managers=managers)` to `_collect_task_snapshot_records`.
3. In `weft/commands/tasks.py`:
   - In `_read_point_observation` (`lines 472-511`), when `mapping is not None`, if `system_cmd._is_non_terminal_manager_state_entry(mapping)`, resolve `selected_active_manager_tid = system_cmd._selected_active_manager_tid(ctx, broker=broker)` and `manager_liveness = system_cmd._manager_runtime_liveness(mapping)` (when `selected_active_manager_tid is not None and tid != selected_active_manager_tid`), and pass `selected_active_manager_tid=selected_active_manager_tid, manager_liveness=manager_liveness` to `reduce_task_state_snapshot`.
   - In `_enrich_known_snapshot` (`lines 878-916`), guard `_stale_liveness_reconciliation` so an existing Manager supersession reconciliation (`superseded_manager_record` or `superseded_manager_draining`) is never overwritten by `stale_liveness`:
     ```python
     existing_classification = (
         snapshot.reconciliation.get("classification")
         if isinstance(snapshot.reconciliation, Mapping)
         else None
     )
     if stale is not None and existing_classification not in {
         "superseded_manager_record",
         "superseded_manager_draining",
     }:
         diagnostic = _stale_liveness_reconciliation(
             reason=stale,
             lifecycle_status=snapshot.status,
             public_status=snapshot.status,
             service_key=None,
             active_service_tid=None,
         )
     ```
4. Apply the Phase 1 Spec Delta to `docs/specifications/07-System_Invariants.md` and `docs/specifications/05-Message_Flow_and_State.md`.

**Step 1.3 — Phase 1 Verification & Commit Gate**:
```bash
./.venv/bin/python -m pytest tests/commands/test_task_snapshot_reducer.py tests/commands/test_status.py tests/commands/test_task_commands.py tests/specs/test_plan_metadata.py -q
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python -m ruff check weft/commands/_task_snapshot_reducer.py weft/commands/system.py weft/commands/tasks.py tests/commands/test_task_snapshot_reducer.py tests/commands/test_status.py tests/commands/test_task_commands.py
./.venv/bin/python -m ruff format --check weft/commands/_task_snapshot_reducer.py weft/commands/system.py weft/commands/tasks.py tests/commands/test_task_snapshot_reducer.py tests/commands/test_status.py tests/commands/test_task_commands.py
./.venv/bin/python -m mypy weft/commands/_task_snapshot_reducer.py weft/commands/system.py weft/commands/tasks.py
```
Commit Phase 1 as an independent commit and record the resulting Phase 1 commit SHA as its promotion baseline identifier.

---

### Phase 2: Spec Revision & Settlement Refinement for Unconfigured Terminal Results Under Explicit Timeout (`[MF-5]`)

**Goal**: Revise `[MF-5]` and update `_CollectedResult._diagnostic`, `_wait_unconfigured_result`, and `_iter_consuming_result` in `weft/commands/_result_wait.py` so that when `materialized.config is None` and `materialized.terminal_evidence is not None`, `weft result <tid> --timeout <N>` (`self.deadline is not None`) waits for the `0.5s` `WEFT_COMPLETED_RESULT_GRACE_SECONDS` window via `decision.wait_until` when `terminal.classification == "wrapper_lost"`, preserves `CommandTimeoutError` (CLI exit 124) if `self.deadline` expires before `_grace_deadline`, and settles with `status="unknown"`, `error="observation metadata unavailable"`, and `reconciliation={"classification": "observation_metadata_unavailable", "observed_terminal_status": terminal.status}` (CLI exit 1) once terminal evidence is settled, with `_iter_consuming_result` returning immediately after `_wait_unconfigured_result` yields a `TaskResult`.

**Files to Modify**:
- `weft/commands/_result_wait.py` (`_CollectedResult._diagnostic` at lines 750–779, `_wait_unconfigured_result` at lines 879–906, and `_iter_consuming_result` at lines 1006–1017)
- `weft/commands/events.py` (`iter_task_realtime_events` at lines 559–570: guard unconfigured `materialized.config is None` after the initial `snapshot` turn so non-consuming `iter_task_realtime_events(..., follow=False)` returns after `snapshot` without settling an unconfigured `result`/`end` event)
- `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]`, lines 1215–1220)
- `tests/commands/test_result_settlement_policy.py` and `tests/commands/test_retained_result_history.py`

**Step 2.1 — RED Tests**:
1. In `tests/commands/test_result_settlement_policy.py`, add unit policy tests on `_CollectedResult.settle` with `mode=None` (`config=None`):
   - When `mode=None`, `terminal=_WRAPPER_LOST`, `observed_at=10.0`, `deadline=20.0`, and `now=10.0`: assert `decision.action == "wait"` and `decision.wait_until == 10.5` (`10.0 + WEFT_COMPLETED_RESULT_GRACE_SECONDS`).
   - When `mode=None`, `terminal=_WRAPPER_LOST`, `observed_at=10.0`, `deadline=10.1` (caller budget shorter than the `10.5` `wrapper_lost` grace deadline), and `now=10.1` **or** `now=10.5` (delayed evaluation after grace): assert `decision.action == "timeout"` in both cases (preserving CLI exit 124 whenever caller budget precedes grace expiry).
   - When `mode=None`, `terminal=_WRAPPER_LOST`, `observed_at=10.0`, `deadline=10.5` (or `20.0`), and `now=10.5`: assert `decision.action == "result"`, `decision.status == "unknown"`, `decision.error == "observation metadata unavailable"`, and `decision.reconciliation == {"classification": "observation_metadata_unavailable", "observed_terminal_status": "failed"}`.
   - When `mode=None`, `terminal=_terminal("failed", source="state", classification="terminal_projection")`, `observed_at=10.0`, `deadline=20.0`, and `now=10.0`: assert `decision.action == "result"`, `decision.status == "unknown"`, and `decision.reconciliation == {"classification": "observation_metadata_unavailable", "observed_terminal_status": "failed"}`.
2. In `tests/commands/test_retained_result_history.py`, add integration test `test_unconfigured_wrapper_lost_settles_after_grace_under_explicit_timeout`:
   - Seed `T{tid}.ctrl_out` with a valid manager `wrapper_lost` terminal envelope literal (`json.dumps({"type": TERMINAL_ENVELOPE_TYPE, "tid": tid, "status": "failed", "event": "work_failed", "source": "manager", "error": WRAPPER_LOST_ERROR})`) with no `weft.state.tasks.<tid>` entry.
   - Call `result_cmd.await_task_result(context, tid, timeout=5.0)` and measure elapsed wall time.
   - Assert it returns in `< 2.0s` (well below the 5.0s timeout) with `result.status == "unknown"`, `result.error == "observation metadata unavailable"`, and `result.reconciliation == {"classification": "observation_metadata_unavailable", "observed_terminal_status": "failed"}`.
3. Run pytest on those tests and confirm RED failure (at `now=10.5` with `deadline=20.0`, current code returns `decision.action == "wait", decision.wait_until == 20.0` instead of `decision.action == "result", decision.status == "unknown"`, and the integration test raises `CommandTimeoutError` after 5.0s).

**Step 2.2 — GREEN Implementation**:
1. In `weft/commands/_result_wait.py` (`_CollectedResult._diagnostic`, lines 750–779):
   ```python
   def _diagnostic(
       self, materialized: ResultMaterialization, *, now: float
   ) -> _ResultSettlement | None:
       terminal = materialized.terminal_evidence
       if (
           terminal is not None
           and terminal.classification == "claimed_result_without_terminal"
           and not self.claimed_output
       ):
           return _ResultSettlement(
               "result",
               terminal.status,
               terminal.error,
               reconciliation=terminal.reconciliation,
           )
       if self.mode is not None:
           return None
       if terminal is not None:
           if self.deadline is not None and terminal.classification == "wrapper_lost":
               grace = self._grace_deadline(materialized)
               if (grace is not None and self.deadline < grace) or not self._expired(
                   grace, now
               ):
                   return None
           return _ResultSettlement(
               "result",
               "unknown",
               "observation metadata unavailable",
               reconciliation={
                   "classification": "observation_metadata_unavailable",
                   "observed_terminal_status": terminal.status,
               },
           )
       if self.deadline is not None:
           return None
       return _ResultSettlement(
           "result",
           "unknown",
           "observation metadata unavailable",
           reconciliation={"classification": "observation_metadata_unavailable"},
       )
   ```
2. In `weft/commands/_result_wait.py` (`_wait_unconfigured_result`, lines 879–906):
   - Replace line 905 (`monitor.wait(None if deadline is None else max(0.0, deadline - time.monotonic()))`) with:
     ```python
     wait = (
         None
         if decision.wait_until is None
         else max(0.0, decision.wait_until - time.monotonic())
     )
     monitor.wait(wait)
     ```
3. In `weft/commands/_result_wait.py` (`_iter_consuming_result`, lines 1006–1017):
   - Replace lines 1006–1017 with:
     ```python
     if materialized.config is None:
         for result in _wait_unconfigured_result(
             tid,
             materialized,
             collected,
             monitor,
             deadline=deadline,
             timeout=timeout,
         ):
             yield result
             return
         continue
     ```
4. Apply the Phase 2 Spec Delta to `docs/specifications/05-Message_Flow_and_State.md` (`[MF-5]`).

**Step 2.3 — Phase 2 Verification, Mid-Plan Review Gate, & Commit Gate**:
```bash
./.venv/bin/python -m pytest tests/commands/test_result_settlement_policy.py tests/commands/test_retained_result_history.py tests/cli/test_cli_result.py tests/specs/test_plan_metadata.py -q
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python -m ruff check weft/commands/_result_wait.py tests/commands/test_result_settlement_policy.py tests/commands/test_retained_result_history.py
./.venv/bin/python -m ruff format --check weft/commands/_result_wait.py tests/commands/test_result_settlement_policy.py tests/commands/test_retained_result_history.py
./.venv/bin/python -m mypy weft/commands/_result_wait.py
./bin/pytest-pg --all tests/commands/test_status.py tests/commands/test_task_commands.py tests/commands/test_result_settlement_policy.py tests/commands/test_retained_result_history.py
```
Commit Phase 2 as an independent commit (recording the resulting Phase 2 commit SHA as its promotion baseline identifier), then run an independent Tier-1 cross-family review pass (`codex` via `skills/call-agent/SKILL.md`) over the Phase 1–2 status/result observation slice before starting Phase 3.

---

### Phase 3: Pipeline Control Propagation (`PAUSE` / `RESUME`) and Manager Internal-Lane Reserved Policy (`[PL-1.5]`, `[PL-4.4]`, `[MF-6]`, `[MANAGER.11]`)

**Goal**:
1. Make `PipelineTask._handle_control_command` (`weft/core/tasks/pipeline.py:908-945`) broadcast `CONTROL_PAUSE` and `CONTROL_RESUME` unconditionally on every explicit `PAUSE` / `RESUME` request to all stage and edge `ctrl_in` queues (allowing repeated commands to repair partial child write failures), preserve and restore the coordinator's pre-pause `(activity, waiting_on)` only on the `paused -> running` transition, and republish the `P{tid}.status` snapshot.
2. Make `Manager._apply_reserved_policy` (`weft/core/manager.py:6394-6416`) iterate `self._spawn_reserved_queue_pairs()` whether `message_timestamp` is `None` or an exact message timestamp, so an unhandled `process_once` exception during an internal spawn-request turn applies `policy: ReservedPolicy` to `T{manager}.internal_reserved` / `weft.spawn.internal`.

**Files to Modify**:
- `weft/core/tasks/pipeline.py` (`PipelineTask.__init__`, `PipelineTask._bootstrap_children`, and `PipelineTask._handle_control_command` at lines 908–945)
- `weft/core/manager.py` (`Manager._apply_reserved_policy`, lines 6394–6416)
- `docs/specifications/12-Pipeline_Composition_and_UX.md` (`[PL-4.4]`, lines 673–685)
- `tests/tasks/test_pipeline_runtime.py` and `tests/core/test_manager.py`

**Step 3.1 — RED Tests**:
1. In `tests/tasks/test_pipeline_runtime.py`, add:
   - `test_pipeline_pause_and_resume_broadcasts_to_stages_and_edges_and_restores_activity`:
     - Construct a `PipelineTask` with two stages and edges, advance it to `status == "running"`, set a specific pre-pause activity/waiting_on (and also test from post-bootstrap `"waiting"`), and write `CONTROL_PAUSE` to `T{pipeline_tid}.ctrl_in` (driving via `pipeline.process_once()`).
     - Assert that `CONTROL_PAUSE` was written to every stage's `ctrl_in_queue` and every edge's `ctrl_in` queue, `pipeline._paused is True`, and the latest `P{pipeline_tid}.status` snapshot has `activity == "paused"`.
     - Write `CONTROL_RESUME` to `T{pipeline_tid}.ctrl_in` (driving via `pipeline.process_once()`).
     - Assert that `CONTROL_RESUME` was written to every stage's `ctrl_in_queue` and every edge's `ctrl_in` queue, `pipeline._paused is False`, and the latest `P{pipeline_tid}.status` snapshot restores the pre-pause `activity` and `waiting_on`.
   - `test_pipeline_repeated_pause_and_resume_repairs_failed_child_control_write_without_clobbering_activity`:
     - Inject a single write failure on one child `ctrl_in` queue during the first `CONTROL_PAUSE` turn so that child misses the first broadcast while `pipeline._paused` becomes `True`.
     - Send a second `CONTROL_PAUSE` to `T{pipeline_tid}.ctrl_in` and drive `pipeline.process_once()`; assert that the repeated `CONTROL_PAUSE` broadcasts again and delivers `CONTROL_PAUSE` to the previously missed child `ctrl_in` queue.
     - Inject a single write failure on one child `ctrl_in` queue during the first `CONTROL_RESUME` turn so that child misses the first resume broadcast while `pipeline._paused` becomes `False` and `pipeline._activity` is restored to its pre-pause value (e.g. `"bootstrapping"` or a custom activity/waiting_on).
     - Send a second `CONTROL_RESUME` to `T{pipeline_tid}.ctrl_in` and drive `pipeline.process_once()`; assert that the repeated `CONTROL_RESUME` broadcasts again and delivers `CONTROL_RESUME` to the previously missed child `ctrl_in` queue without overwriting the restored `activity` / `waiting_on` with `("waiting", None)`.
2. In `tests/core/test_manager.py`, add `test_manager_apply_reserved_policy_with_exact_timestamp_covers_internal_spawn_lane`:
   - Construct a `Manager`, write a spawn payload into `weft.spawn.internal`, move it into `T{manager}.internal_reserved` capturing its `message_timestamp`, and call `manager._apply_reserved_policy(ReservedPolicy.REQUEUE, message_timestamp=message_timestamp)` (and test `ReservedPolicy.CLEAR` as well).
   - Assert that `ReservedPolicy.REQUEUE` moves the exact message from `T{manager}.internal_reserved` back to `weft.spawn.internal`, and `ReservedPolicy.CLEAR` deletes it from `T{manager}.internal_reserved`.
3. Run pytest on those tests and confirm RED failure.

**Step 3.2 — GREEN Implementation**:
1. In `weft/core/tasks/pipeline.py`:
   - Import `CONTROL_PAUSE` and `CONTROL_RESUME` from `weft._constants` where `CONTROL_KILL`, `CONTROL_STATUS`, and `CONTROL_STOP` are imported.
   - Initialize `self._pre_pause_activity: tuple[str | None, str | None] | None = None` in `PipelineTask.__init__` (`pipeline.py:336-339`), and clear `self._pre_pause_activity = None` in `PipelineTask._bootstrap_children` (`pipeline.py:507-509`).
   - In `PipelineTask._handle_control_command` (`pipeline.py:908-945`), add `CONTROL_PAUSE` and `CONTROL_RESUME` handling before `return super()._handle_control_command(request, context)`:
     ```python
     if command == CONTROL_PAUSE:
         if not self._paused and self._activity != "paused":
             self._pre_pause_activity = (self._activity, self._waiting_on)
         self._broadcast_control(CONTROL_PAUSE)
         handled = super()._handle_control_command(request, context)
         self._set_activity("paused", waiting_on=None)
         self._publish_pipeline_snapshot()
         return handled
     if command == CONTROL_RESUME:
         was_paused = self._paused
         self._broadcast_control(CONTROL_RESUME)
         handled = super()._handle_control_command(request, context)
         if was_paused and not self.should_stop and self.taskspec.state.status == "running":
             restored_activity, restored_waiting_on = (
                 self._pre_pause_activity
                 if self._pre_pause_activity is not None
                 else ("waiting" if self._bootstrapped else "bootstrapping", None)
             )
             self._pre_pause_activity = None
             if restored_activity is not None:
                 self._set_activity(restored_activity, waiting_on=restored_waiting_on)
         self._publish_pipeline_snapshot()
         return handled
     ```
2. In `weft/core/manager.py` (`Manager._apply_reserved_policy`, lines 6394–6416):
   ```python
   def _apply_reserved_policy(
       self,
       policy: ReservedPolicy,
       message_timestamp: int | None = None,
   ) -> None:
       """Apply manager reserved policy across all attached spawn sources."""

       for reserved_queue, source_queue in self._spawn_reserved_queue_pairs():
           self._apply_spawn_reserved_policy(
               policy,
               source_queue=source_queue,
               reserved_queue=reserved_queue,
               message_timestamp=message_timestamp,
           )
   ```
3. Apply the Phase 3 Spec Delta to `docs/specifications/12-Pipeline_Composition_and_UX.md` (`[PL-4.4]`).

**Step 3.3 — Phase 3 Verification & Commit Gate**:
```bash
./.venv/bin/python -m pytest tests/tasks/test_pipeline_runtime.py tests/core/test_manager.py tests/specs/test_plan_metadata.py -q
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python -m ruff check weft/core/tasks/pipeline.py weft/core/manager.py tests/tasks/test_pipeline_runtime.py tests/core/test_manager.py
./.venv/bin/python -m ruff format --check weft/core/tasks/pipeline.py weft/core/manager.py tests/tasks/test_pipeline_runtime.py tests/core/test_manager.py
./.venv/bin/python -m mypy weft/core/tasks/pipeline.py weft/core/manager.py
```
Commit Phase 3 as an independent commit (recording the resulting Phase 3 commit SHA as its promotion baseline identifier).

---

### Phase 4: One-Shot Host Terminal Handoff Sentinel Wake and Codex Ambient `CODEX_HOME` Concurrency Boundary Documentation (`[CC-2.2.1]`, `[CC-3.5]`, `[AR-2.1]`)

**Goal**:
1. Upgrade `HostTaskRunner._run_one_shot_terminal_handoff` and `HostTaskRunner._terminal_handoff_wait_seconds` (`weft/core/runners/host.py`) to multiplex `process.sentinel` via `wait_terminal_payload(response_receiver, producer_sentinel=producer_sentinel, timeout=wait_for)` and gate `ACTIVE_CONTROL_POLL_INTERVAL` (`0.05s`) on `cancel_requested is not None or not has_producer_sentinel`, while preserving `start_time + self._timeout`, `next_monitor_at`, and `drain_deadline`.
2. Document the `codex exec resume --last` ambient `CODEX_HOME` concurrency boundary in `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]`).

**Files to Modify**:
- `weft/core/runners/host.py` (`_run_one_shot_terminal_handoff` at lines 723–734 and `_terminal_handoff_wait_seconds` at lines 871–889)
- `docs/specifications/01-Core_Components.md` (`[CC-3.5]`, lines 1329–1334)
- `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]`, lines 258–261)
- `tests/core/test_terminal_handoff_transport.py`

**Step 4.1 — RED Tests**:
1. In `tests/core/test_terminal_handoff_transport.py`, add:
   - `test_host_terminal_handoff_wait_seconds_preserves_timeout_monitor_and_drain_deadlines_without_poll_floor`:
     - Construct a `HostTaskRunner` with `timeout=None` and verify `runner._terminal_handoff_wait_seconds(now=100.0, start_time=95.0, next_monitor_at=None, drain_deadline=None, cancel_requested=None, has_producer_sentinel=True) is None`.
     - Verify `runner._terminal_handoff_wait_seconds(now=100.0, start_time=95.0, next_monitor_at=None, drain_deadline=None, cancel_requested=lambda: False, has_producer_sentinel=True) == pytest.approx(0.05)` (confirming callers with `cancel_requested` retain the 50ms cancellation poll).
     - Verify `runner._terminal_handoff_wait_seconds(now=100.0, start_time=95.0, next_monitor_at=None, drain_deadline=100.2, cancel_requested=None, has_producer_sentinel=False) == pytest.approx(0.05)` and with `has_producer_sentinel=True` returns `pytest.approx(0.2)`.
     - Construct a `HostTaskRunner` with `timeout=5.0` and verify `runner._terminal_handoff_wait_seconds(now=97.0, start_time=95.0, next_monitor_at=98.5, drain_deadline=None, cancel_requested=None, has_producer_sentinel=True) == pytest.approx(1.5)` (and `pytest.approx(3.0)` when `next_monitor_at=None`).
   - `test_host_one_shot_terminal_handoff_wakes_on_producer_sentinel_and_expires_drain_when_descendant_holds_pipe`:
     - Monkeypatch `ACTIVE_CONTROL_POLL_INTERVAL` in `weft.core.runners.host` to `5.0` seconds so any reliance on the old `ACTIVE_CONTROL_POLL_INTERVAL` poll floor is immediately distinguishable from sentinel readiness.
     - Run `HostTaskRunner.run_with_hooks` on a real spawned target (`timeout=None`, `limits=None`, `cancel_requested=None`) that spawns a detached child holding an inherited pipe descriptor open for 10 seconds and then exits immediately (`os._exit(7)`) without sending a terminal payload.
     - Assert that `run_with_hooks` wakes immediately on `process.sentinel` (even though `wait_for is None` on the initial wait), starts the `0.2s` `TERMINAL_HANDOFF_DRAIN_TIMEOUT_SECONDS` drain window, and returns in `< 2.0s` (well under the `5.0s` patched poll interval and grandchild hold window) with `outcome.status == "error"`, `outcome.returncode == 7`, `outcome.error == "Worker result channel failed before a result was received"`, and `outcome.diagnostics["handoff_event"] == "drain_expired"` (while cleaning up the helper child in a `finally` block).
   - `test_host_one_shot_terminal_handoff_recomputes_wait_against_fresh_clock_after_monitor_sample`:
     - Verify that when `monitor.check_limits()` advances `time.monotonic()` past `start_time + self._timeout` while setting `next_monitor_at = now + self._monitor_interval`, `_run_one_shot_terminal_handoff` passes a fresh post-sample `time.monotonic()` reading into `_terminal_handoff_wait_seconds` and requests `timeout == 0.0`.
2. Run pytest on those tests and confirm RED failure.

**Step 4.2 — GREEN Implementation**:
1. In `weft/core/runners/host.py`:
   - Import `wait_terminal_payload` from `weft.core.terminal_handoff_transport`.
   - In `_run_one_shot_terminal_handoff` (`host.py:723-734`), replace lines 723–734 with:
     ```python
     if step is None:
         producer_sentinel = (
             None
             if "producer_exited" in progress.consumed_edge_kinds
             else getattr(process, "sentinel", None)
         )
         sampling_deadline = (
             next_monitor_at
             if (
                 monitor is not None
                 and progress.accepted_stop is None
                 and "limit_reached" not in progress.consumed_edge_kinds
                 and "producer_exited" not in progress.consumed_edge_kinds
             )
             else None
         )
         wait_for = self._terminal_handoff_wait_seconds(
             now=time.monotonic(),
             start_time=start_time,
             next_monitor_at=sampling_deadline,
             drain_deadline=drain_deadline,
             cancel_requested=cancel_requested,
             has_producer_sentinel=producer_sentinel is not None,
         )
         try:
             wait_terminal_payload(
                 response_receiver,
                 producer_sentinel=producer_sentinel,
                 timeout=wait_for,
             )
         except (OSError, ValueError) as exc:
             pending_transport_failure = str(exc)
         continue
     ```
   - In `_terminal_handoff_wait_seconds` (`host.py:871-889`), update to:
     ```python
     def _terminal_handoff_wait_seconds(
         self,
         *,
         now: float,
         start_time: float,
         next_monitor_at: float | None,
         drain_deadline: float | None,
         cancel_requested: Callable[[], bool] | None = None,
         has_producer_sentinel: bool = True,
     ) -> float | None:
         """Return the bounded wait until the next owned observation boundary."""

         deadlines: list[float] = []
         if cancel_requested is not None or not has_producer_sentinel:
             deadlines.append(now + ACTIVE_CONTROL_POLL_INTERVAL)
         if self._timeout is not None:
             deadlines.append(start_time + self._timeout)
         if next_monitor_at is not None:
             deadlines.append(next_monitor_at)
         if drain_deadline is not None:
             deadlines.append(drain_deadline)
         if not deadlines:
             return None
         return max(0.0, min(deadlines) - now)
     ```
2. Apply the Phase 4 Spec Delta to `docs/specifications/01-Core_Components.md` (`[CC-3.5]`) and `docs/specifications/13-Agent_Runtime.md` (`[AR-2.1]`).

**Step 4.3 — Phase 4 Verification, Review Gate, & Commit Gate**:
```bash
./.venv/bin/python -m pytest tests/core/test_terminal_handoff_transport.py tests/core/test_runner_plugins.py tests/specs/test_plan_metadata.py -q
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python -m ruff check weft/core/runners/host.py tests/core/test_terminal_handoff_transport.py
./.venv/bin/python -m ruff format --check weft/core/runners/host.py tests/core/test_terminal_handoff_transport.py
./.venv/bin/python -m mypy weft/core/runners/host.py
./bin/pytest-pg --all tests/tasks/test_pipeline_runtime.py tests/core/test_manager.py tests/core/test_terminal_handoff_transport.py
```
Commit Phase 4 as an independent commit (recording the resulting Phase 4 commit SHA as its promotion baseline identifier), then run an independent Tier-1 cross-family review pass (`codex` via `skills/call-agent/SKILL.md`) over the Phase 3–4 runtime/pipeline/manager/host slice before starting Phase 5.

---

### Phase 5: Cross-Cutting Verification, `[WT-0]` Theory Audit Refresh, `[WT-7]` Two-Factor Fit Test, and Plan Closeout

**Goal**: Run the full repository verification suite (including PostgreSQL), execute the blind other-family `[WT-7]` two-factor fit test over `docs/program-theory.md` and the `709fb78112f797f60c4dea6c709d2c062830211d..HEAD` diff, record both five-part accounts and their comparison in Section 6a and Section 8, refresh the `[WT-0]` audit table, and flip this plan's status to `completed` in both `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md` and `docs/plans/README.md`.

**Files to Modify**:
- `docs/plans/2026-11-06-status-result-and-runtime-remediation-plan.md` (`Status: completed`, Section 6a and Section 8 refresh)
- `docs/plans/README.md` (flip `2026-11-06-status-result-and-runtime-remediation-plan.md` row from `draft` / `promoted` to `completed`)

**Step 5.1 — Blind Other-Family `[WT-7]` Two-Factor Fit Test**:
Invoke `codex exec` in a fresh context whose **only** inputs are `docs/program-theory.md` and `git diff 709fb78112f797f60c4dea6c709d2c062830211d..HEAD -- weft tests docs/specifications` (explicitly excluding `docs/plans/` and the author's account in Section 6a), asking for the five-part `[WT-7]` structure:
1. Verdict (`fits`, `does not fit`, or `fits with strain`)
2. The sections of `docs/program-theory.md` the change rests on
3. What the change refuses and the `[WT-6]` / `[WT-*]` entry that refuses it
4. What the change predicts that could prove it wrong (falsifiable prediction)
5. The sentence in `docs/program-theory.md` that fits worst
Record the second account verbatim in Section 6a and classify any divergence (`document deficiency`, `change deficiency`, or `agent reasoning`).

**Step 5.2 — Full Repository Verification Suite**:
```bash
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/coalesce-check
./.venv/bin/python -m pytest -m ""
./.venv/bin/python -m pytest extensions integrations -m ""
./bin/pytest-pg --all tests
```
(Note: per `pyproject.toml` lines 139–140 and `AGENTS.md` §5, `-m ""` includes `@pytest.mark.slow` tests at the configured `-n logical --maxprocesses 12 --dist load` concurrency, `pytest extensions integrations -m ""` runs the `extensions/` and `integrations/` test roots outside default `testpaths = ["tests"]`, and `./bin/pytest-pg --all tests` exercises the PostgreSQL broker backend. Any tests skipped due to unavailable external daemons/prerequisites such as Docker, Microsandbox, or PostgreSQL must be reported with their exact skip counts and scope.)

**Step 5.3 — Plan Status Closeout & Commit Gate**:
Flip `Status:` to `completed` in this plan and in `docs/plans/README.md`, re-run `test_plan_metadata.py` and `bin/check-doc-paths`, and commit Phase 5.

---

## 6. Testing Plan

| Phase | Test File | New / Updated Tests | Failure Mode Verified (RED -> GREEN) |
| :--- | :--- | :--- | :--- |
| Phase 1 | `tests/commands/test_task_snapshot_reducer.py`, `tests/commands/test_status.py`, `tests/commands/test_task_commands.py` | `test_reduce_task_snapshot_handles_stale_and_manager_reconciliation`, `test_reduce_task_state_snapshot_reconciles_non_live_live_and_unknown_superseded_managers_and_preserves_terminal_manager`, `test_cmd_status_marks_superseded_manager_record_failed`, `test_system_status_distinguishes_dead_live_absent_handle_and_invalid_projection_fallback`, `test_task_status_reconciles_dead_live_and_absent_handle_superseded_manager_projections` | Confirmed non-live (`manager_liveness="non_live"`) superseded managers reconcile to `status="failed"`, `classification="superseded_manager_record"`; live (`"live"`) and uncertain/absent-handle (`"unknown"`) superseded managers keep `status="running"`, `classification="superseded_manager_draining"` (`runtime_liveness="live" | "unknown"`); terminal manager records preserved; ordinary tasks with invalid projections still fall back to `_merge_raw_task_history` |
| Phase 2 | `tests/commands/test_result_settlement_policy.py`, `tests/commands/test_retained_result_history.py` | Unconfigured terminal policy cases (including short `deadline < grace_deadline` timeout precedence) + `test_unconfigured_wrapper_lost_settles_after_grace_under_explicit_timeout` | Unconfigured terminal task settles with `status="unknown"`, `classification="observation_metadata_unavailable"` after `0.5s` `wrapper_lost` grace window under `--timeout` (while preserving exit 124 when `deadline < grace_deadline`) |
| Phase 3 | `tests/tasks/test_pipeline_runtime.py`, `tests/core/test_manager.py` | `test_pipeline_pause_and_resume_broadcasts_to_stages_and_edges_and_restores_activity`, `test_pipeline_repeated_pause_and_resume_repairs_failed_child_control_write_without_clobbering_activity`, `test_manager_apply_reserved_policy_with_exact_timestamp_covers_internal_spawn_lane` | `PipelineTask` broadcasts `PAUSE`/`RESUME` on every explicit command (repairing partial child write failures on retry), restores pre-pause activity only on the `paused -> running` transition, and updates `P{tid}.status`; `Manager._apply_reserved_policy` with exact `message_timestamp` covers `T{manager}.internal_reserved` |
| Phase 4 | `tests/core/test_terminal_handoff_transport.py` | `test_host_terminal_handoff_wait_seconds_preserves_timeout_monitor_and_drain_deadlines_without_poll_floor`, `test_host_one_shot_terminal_handoff_wakes_on_producer_sentinel_and_expires_drain_when_descendant_holds_pipe`, `test_host_one_shot_terminal_handoff_recomputes_wait_against_fresh_clock_after_monitor_sample` | `HostTaskRunner._run_one_shot_terminal_handoff` wakes on `process.sentinel` even when a descendant holds the pipe open and expires `drain_deadline` (`Worker result channel failed before a result was received`, `handoff_event == "drain_expired"`), recomputes `wait_for` against a fresh post-sample clock reading, and removes the 50ms floor when `cancel_requested is None` while keeping timeout, monitor, drain, and cancellation deadlines |
| Phase 5 | Full suite (`tests/specs/test_plan_metadata.py`, `bin/check-doc-paths`, `pytest`, `pytest-pg`) | Repository-wide regression & traceability suite | Zero regressions across all specs, plans, commands, extensions, PostgreSQL backend, and runtime tests |

## 6a. Theory-Versus-Code Audit Refresh

### `[WT-0]` Theory-Versus-Code Audit Table

All 46 baseline rows from `docs/plans/2026-11-02-program-theory-reconciliation-plan.md` §6, `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md` §6, and `docs/plans/2026-11-06-deep-dive-review-remediation-plan.md` §6a are carried forward by reference. The rows touched by this remediation are re-audited below:

| Audit Row / Touchpoint | Governing Theory & Spec | Pre-Remediation State (`709fb781`) | Post-Remediation State |
| :--- | :--- | :--- | :--- |
| **Row O-4 / Service registry & Manager snapshot reconciliation** (`weft/commands/_task_snapshot_reducer.py`, `weft/commands/system.py`, `weft/commands/tasks.py`) | `[WT-2a.2]`, `[WT-2a.3]`, `[WT-3]` (Service registry), `[WT-4.1]`, `[WT-4.3]`, `[ALT-WT-007]`, `[OBS.15]`, `[MF-5]` | **Drifted**: Event-log folding marked all superseded managers `failed` regardless of runtime observation, while direct `weft.state.tasks.<tid>` projection reduction ignored `selected_active_manager_tid` and published `SIGKILL`ed superseded managers as `running` with `reconciliation=None` | **Aligned**: Both direct `weft.state.tasks.<tid>` reduction and snapshot policy reduction evaluate three-state liveness (`"live"`, `"non_live"`, `"unknown"`), reconciling only confirmed non-live superseded managers (`manager_liveness="non_live"`) to `status="failed"` (`superseded_manager_record`), keeping live or uncertain superseded managers (`"live"` / `"unknown"`) at `status="running"` (`superseded_manager_draining`), preserving terminal manager records, and preserving `_merge_raw_task_history` fallback for ordinary tasks with invalid projections |
| **Row O-5 / Unconfigured terminal result settlement** (`weft/commands/_result_wait.py`) | `[WT-2a.2]`, `[WT-2a.6]`, `[WT-4.3]`, `[MF-5]` | **Spec-refinement needed**: Under the prior `[MF-5]` wording, `_CollectedResult._diagnostic` returned `None` whenever `self.deadline is not None`, `_wait_unconfigured_result` ignored `decision.wait_until`, and `_iter_consuming_result` did not return on unconfigured result when `deadline is not None`, waiting out the full caller timeout even after terminal evidence was settled | **Aligned (with `[MF-5]` revision)**: Terminal unconfigured tasks wait for the `0.5s` `wrapper_lost` completion-grace deadline (`decision.wait_until`) when applicable, preserve exit 124 if the caller deadline expires before grace completes, and settle with `status="unknown"` (`observation_metadata_unavailable`, `observed_terminal_status`, exit 1) once terminal evidence is settled, with zero polling loops |
| **Row T-3 / Pipeline control propagation & Manager internal spawn reserved policy** (`weft/core/tasks/pipeline.py`, `weft/core/manager.py`) | `[WT-1]`, `[WT-2a.7]`, `[WT-3]` (Pipeline), `[PL-1.5]`, `[PL-4.4]`, `[MF-6]`, `[MANAGER.11]` | **Drifted**: `PipelineTask` broadcast `STOP`/`KILL` to stage/edge queues but not `PAUSE`/`RESUME`; `Manager._apply_reserved_policy` with `message_timestamp != None` checked only the public spawn lane | **Aligned**: `PipelineTask` broadcasts `PAUSE`/`RESUME` on every explicit command (supporting retry repair), restores pre-pause activity on `RESUME`, and refreshes `P{tid}.status`; `Manager._apply_reserved_policy` iterates both public and internal spawn reserved queue pairs regardless of `message_timestamp` |
| **Row R-2 / One-shot host terminal handoff readiness & Codex session boundary** (`weft/core/runners/host.py`, `docs/specifications/13-Agent_Runtime.md`) | `[WT-2a.6]`, `[WT-3]` (Agent runtime), `[WT-4.3]`, `[WT-4.4]`, `[CC-2.2.1]`, `[CC-3.5]`, `[AR-2.1]` | **Partially aligned**: `HostTaskRunner._run_one_shot_terminal_handoff` waited on `response_receiver` without `process.sentinel` and with an unconditional 50ms timer floor; `13-Agent_Runtime.md` did not state the `CODEX_HOME` concurrency boundary for `codex exec resume --last` | **Aligned**: Sentinel wake (`process.sentinel`) added to one-shot host terminal handoff via `wait_terminal_payload`, and the 50ms floor is removed for direct runner calls without a `cancel_requested` callable (`Consumer`-driven runs keep the 50ms cancellation poll until cancellation becomes a waitable event); `13-Agent_Runtime.md` documents the `CODEX_HOME` concurrency boundary |

### `[WT-7]` Two-Factor Fit Test

#### Account 1 — Author Account (Five-Part `[WT-7]` Structure)

1. **Verdict**: `fits with strain`.
2. **Sections the change rests on**:
   - `[WT-1]` & `[WT-3]` (Pipelines are tasks whose stages and edges are ordinary tasks controlled through normal `ctrl_in` messages; Service registry selects one active Manager in `weft.state.services`; `LivenessMonitor` is the sole deleter of `weft.state.tasks.*`).
   - `[WT-2a.2]`, `[WT-2a.3]`, `[WT-4.1]`, `[WT-4.3]`, & `[ALT-WT-007]` (Queues carry facts; status/result readers reconcile superseded non-terminal Manager projections and unconfigured terminal evidence at read time without mutating queue state, reconciling to `failed` only on positive `non_live` evidence while keeping live and uncertain (`unknown`) superseded Managers `running` with `superseded_manager_draining`).
   - `[WT-2a.6]` & `[WT-4.3]` (Single-arbiter readiness and real deadlines: `HostTaskRunner._run_one_shot_terminal_handoff` joins `process.sentinel` with `response_receiver` in `wait_terminal_payload` and removes the 50ms timer floor for direct runner calls without a `cancel_requested` callback; `_wait_unconfigured_result` waits on `decision.wait_until` rather than a polling loop).
   - `[WT-2a.7]` (Reserved queue policy is honored across both public and internal Manager spawn lanes).
   - `[WT-4.4]` (No over-armoring: provider CLI `tool_profile` validation remains at the entry boundaries `execution.py:100` and `backends/provider_cli.py:76` rather than being duplicated inside internal `build_*_invocation` methods).
3. **What it refuses and the entry that refuses it**:
   - Refuses deleting or mutating `weft.state.tasks.<old_mgr_tid>` from `weft status` or `weft task status` when reconciling a superseded Manager (`[ALT-WT-015]` and `[WT-3]` Runtime-state queue: `LivenessMonitor` is the sole deleter of `weft.state.tasks.*`).
   - Refuses marking a **live** or **unknown-liveness** superseded Manager `failed` without positive `non_live` runtime evidence (`[WT-4.3]` Availability vs Death: *"A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death"* and `[ALT-WT-007]` one-way non-stopping drain).
   - Refuses adding a recurring discovery poll timer in `_wait_unconfigured_result` (`[ALT-WT-002]` and `[REV-WT-001]`: defensive polling wait-caps are rejected in favor of real event and grace deadlines).
   - Refuses duplicate `validate_tool_profile` checks inside internal `build_*_invocation` methods (`[WT-4.4]` No over-armoring) and refuses hidden startup preflight or redirecting `CODEX_HOME` away from user auth in `CodexProvider` (`[ALT-WT-011]`).
4. **What it predicts that could prove it wrong (falsifiable predictions)**:
   - **Prediction 1**: After killing Manager `M1` with `SIGKILL` and starting replacement Manager `M2`, both `weft status --all` and `weft task status <M1_tid>` report `M1` as `status="failed"` with `reconciliation.classification == "superseded_manager_record"`, whereas a briefly live losing Manager `M_drain` (`runtime_liveness="live"`) and a handle-less Manager record `M_unknown` (`runtime_liveness="unknown"`) report `status="running"` with `reconciliation.classification == "superseded_manager_draining"`, and `weft.state.tasks.<M1_tid>` retains its original row count (`1`) in the broker.
   - **Prediction 2**: When a task's runner process dies (`wrapper_lost` on `T{tid}.ctrl_out`) before publishing `weft.state.tasks.<tid>`, `weft result <tid> --timeout 5.0` wakes at the `0.5s` `WEFT_COMPLETED_RESULT_GRACE_SECONDS` deadline and returns `status="unknown"`, `reconciliation={"classification": "observation_metadata_unavailable", "observed_terminal_status": "failed"}` in `< 2.0s`, while a call whose `--timeout` expires before the `0.5s` grace window finishes raises `CommandTimeoutError` (exit 124).
   - **Prediction 3**: When `HostTaskRunner._run_one_shot_terminal_handoff` runs a target with `timeout=None`, `limits=None`, and `cancel_requested=None` (and `ACTIVE_CONTROL_POLL_INTERVAL` patched to `5.0s`) whose worker spawns a child that holds the pipe open and then exits (`os._exit(7)`), `wait_terminal_payload` wakes immediately on `process.sentinel`, starts `drain_deadline` (`0.2s`), and returns protocol failure (`outcome.returncode == 7`, `outcome.error == "Worker result channel failed before a result was received"`, `handoff_event == "drain_expired"`) in `< 2.0s`.
5. **The sentence in `docs/program-theory.md` that fits worst**:
   - In `[WT-4.3]` ("Availability and death are different questions", line 470): *"A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death. Task-owned terminal proof wins over a Manager `wrapper_lost` record, and reservation/checkpoint custody survives until task-snapshot handoff or positive settlement."* That passage fits with the most tension because for a superseded Manager whose `host-pid` process is confirmed dead (`manager_liveness == "non_live"`, e.g. `SIGKILL`ed before writing a task-owned terminal projection), `[OBS.15]` reconciles the abandoned Manager projection to terminal `"failed"` (`superseded_manager_record`) at read time by combining two positive facts (`weft.state.services` selected a different active Manager and `inspect_host_process` proved the superseded Manager's scoped `(pid, create_time)` process is absent/stale), whereas missing handles, missing `create_time`, or failed OS probes remain `"unknown"` (`status="running"`, `superseded_manager_draining`).

#### Account 2 — Other-Family Blind Account (`[WT-7]`, `codex` session `01a1b6ed-cad0-7bb3-b7c6-783522c8db2c`)

Recorded verbatim from `codex exec` receiving only `docs/program-theory.md` and `git diff 709fb78112f797f60c4dea6c709d2c062830211d..08314b511f9c162abef6bf5fead0fb8a2851ab56 -- weft tests docs/specifications`:

> **(1) Verdict: fits with strain.**
>
> The change mostly restores Weft’s existing model: Manager selection is separate from process death; missing result metadata produces uncertainty; pipeline controls travel through ordinary queues; reservation cleanup stays with the Manager; and host supervision gains producer-sentinel readiness.
>
> The clearest strain is retained fixed polling for cancellation callbacks and missing sentinels. A second strain is the agent runtime’s per-task conversation promise: the Codex specification now makes native-history isolation depend on caller-supplied `CODEX_HOME` paths or containers.
>
> **(2) Sections of `docs/program-theory.md` the change rests on.**
>
> - **[WT-2a.13], [WT-4.3], and [WT-2] item 3:** independent lifetimes and proof of death justify preserving a superseded Manager’s running projection when runtime evidence is live or unknown. A replacement’s selection alone cannot establish death.
> - **[WT-2a.2], [WT-3] Evidence, and [WT-4.1]:** status and result readers interpret queue facts and scoped process evidence. Missing observation configuration remains an observation failure rather than becoming a fabricated task outcome.
> - **[WT-2a.6], [WT-2] item 4, and [WT-4.3]:** multiplexing producer exit with the response channel, recomputing waits after monitoring, and retiring consumed readiness inputs move supervision toward the reactor model.
> - **[WT-2a.7], [WT-2a.12], and [WT-3] Pipeline:** exact-ID reservation disposition and pipeline PAUSE/RESUME compose existing task and queue operations.
> - **[WT-3] Agent runtime and session, [WT-4.4] No over-armoring, and [WT-5]:** the Codex documentation places native configuration and runtime isolation at an explicit operator boundary. The tension is whether that precondition adequately supports the theory’s per-task conversation ownership.
>
> **(3) What the change refuses, and the entry that refuses it.**
>
> It refuses treating supersession as death or permission to stop existing work: [ALT-WT-007], [ALT-WT-015], and [WT-2a.13]. Live and uncertain former Managers remain distinguishable from confirmed dead records.
>
> It refuses converting an observer’s deadline or missing metadata into task failure: [WT-2a.13] and [WT-4.3] “Availability and death are different questions.” The caller can time out while the task’s lifecycle remains untouched.
>
> It preserves the Manager-only scope of spawn requeue: [ALT-WT-006]. Extending exact-ID disposition across existing spawn sources introduces no new task retry policy or public child lane ([ALT-WT-001]).
>
> Pipeline controls follow [WT-2a.12] rather than gaining a privileged control channel. The readiness repair also acts on [ALT-WT-002] and [ALT-WT-021], but incompletely: callback and sentinel-absent branches retain the rejected polling mechanism.
>
> **(4) What the change predicts that could prove it wrong.**
>
> These predictions are unmeasured in this blind reading.
>
> - With a confirmed active replacement, a superseded Manager becomes `failed` only with positive non-live evidence. Live or unknown evidence preserves its lifecycle status with draining reconciliation. Task-owned terminal projections remain intact. Bulk, point, and historical views should agree on these distinctions; passive reads should emit no PING or lifecycle mutation.
> - An unconfigured result with `wrapper_lost` settles as `unknown` after its completion grace when the caller deadline permits. A deadline preceding grace produces an observation timeout. Equality permits settlement. None of these outcomes should publish synthetic task timeout.
> - Unconfigured realtime following should wait for discovery or its caller deadline without spinning; a non-following read should return.
> - A producer exiting while a descendant retains the response pipe should wake supervision through the sentinel and settle within the drain budget. With no cancellation callback and a valid sentinel, quiet supervision should have no fixed wake cap. Periodic wakeups on that path would refute the readiness claim.
> - Exact-ID CLEAR or REQUEUE should disposition the matching internal or ordinary spawn reservation while preserving unrelated rows.
> - Repeated pipeline controls should resend child messages and preserve the original pre-pause activity. The injected-write-failure test exposes a limit: the coordinator can publish `paused` while a child missed PAUSE. That snapshot cannot establish that every child has paused.
> - Concurrent Codex tasks using the documented isolation should retain separate conversations across resumes. A cross-task resume under those conditions would refute the documented isolation prescription. Shared-home interleaving would test how far the theory’s per-task promise depends on that prescription.
>
> **(5) The sentence that fits worst, quoted verbatim.**
>
> From [WT-4.3]:
>
> > Every task waits at one `PollingStrategy` arbiter driven by backend hints/notifications, local `notify_activity()` events, OS sentinel adapters, and explicit deadlines—never defensive polling wait-caps.
>
> `_terminal_handoff_wait_seconds` still inserts `ACTIVE_CONTROL_POLL_INTERVAL` whenever a cancellation callback exists or a producer sentinel is unavailable. New tests explicitly preserve the 50 ms cap. Preserving cancellation latency explains the choice, but [ALT-WT-002] requires a notifying adapter. The change reduces this inherited drift while continuing to encode it.

#### Two-Factor Comparison and Divergence Classification (`[WT-7]`)

- **Agreement**: Both Account 1 (`antigravity`) and Account 2 (`codex`, blind) converge on the same verdict (`fits with strain`), the same governing sections (`[WT-2a.2]`, `[WT-2a.6]`, `[WT-2a.7]`, `[WT-2a.12]`, `[WT-2a.13]`, `[WT-3]`, `[WT-4.1]`, `[WT-4.3]`, `[WT-4.4]`), the same refusal entries (`[ALT-WT-002]`, `[ALT-WT-007]`, `[ALT-WT-015]`, `[ALT-WT-021]`), and the same falsifiable predictions across Phases 1–4.
- **Divergence Classification (`agent reasoning` — complementary strain selection within `[WT-4.3]`, zero `document deficiency` and zero `change deficiency`)**:
  - Account 1 selected the `[WT-4.3]` sentence on availability vs death (*"A missing snapshot, an expired window, or a failed probe is `unknown`, never proof of death..."*), noting how three-state liveness resolves the tension by reconciling only confirmed `non_live` superseded Managers to `failed` while keeping `unknown` and `live` superseded Managers `running` (`superseded_manager_draining`).
  - Account 2 confirmed that Phase 1's three-state liveness fully satisfies `[WT-4.3]`'s availability-vs-death rule, and therefore selected the `[WT-4.3]` reactor sentence (*"Every task waits at one `PollingStrategy` arbiter ... never defensive polling wait-caps"*) because `HostTaskRunner._terminal_handoff_wait_seconds` (like `AgentSession._terminal_handoff_wait_seconds`) still retains the `0.05s` `ACTIVE_CONTROL_POLL_INTERVAL` when `cancel_requested is not None` (`Consumer`-driven runs) until `Consumer` cancellation is converted from a boolean callable into a waitable OS event (explicitly recorded in Section 9 Out of Scope). Neither `docs/program-theory.md` nor the change slice requires modification.

## 7. Verification and Gates

Every phase commit (Phases 1–4) must pass:
1. Phase-scoped red-green pytest suite (verified RED before code change, GREEN after).
2. `./.venv/bin/python -m ruff check`, `./.venv/bin/python -m ruff format --check`, and `./.venv/bin/python -m mypy` on all touched files with zero errors.
3. `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q` and `./.venv/bin/python bin/check-doc-paths` with zero errors.
4. Independent Tier-1 cross-family code review (`codex` via `skills/call-agent/SKILL.md`) and PostgreSQL gate (`./bin/pytest-pg --all ...`) after Phase 2 (status/result slice) and after Phase 4 (pipeline/manager/host slice).
5. Before closing Phase 5:
   - `./.venv/bin/ruff check .`
   - `./.venv/bin/ruff format --check .`
   - `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`
   - `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q`
   - `./.venv/bin/python bin/check-doc-paths`
   - `./.venv/bin/python bin/coalesce-check`
   - `./.venv/bin/python -m pytest -m ""`
   - `./.venv/bin/python -m pytest extensions integrations -m ""`
   - `./bin/pytest-pg --all tests`
   - Blind other-family `[WT-7]` two-factor fit test account recorded and compared in Section 6a.

## 8. Independent Review Loop

- **Plan Author Family**: `antigravity` (Google Gemini model family)
- **Independent Reviewer Family**: `codex` (OpenAI model family, invoked via `skills/call-agent/SKILL.md`), plus external design Reviews 1 and 2 incorporated in Pass 4 below.

### Plan Review Pass 1 (`codex` via `skills/call-agent/SKILL.md`, session `01a1b383-5199-7161-a228-40e63136c70a`)

- **Verdict**: `REVISE` (7 findings: `F1`–`F4` P1, `F5`–`F7` P2).
- **Findings and Dispositions**:
  - **`F1` (P1 — Phase 4 host wait dropped `start_time + self._timeout` and `drain_deadline`)**: **Accepted**. Updated Step 4.1 and Step 4.2 so `HostTaskRunner._terminal_handoff_wait_seconds` preserves `start_time + self._timeout`, `next_monitor_at`, and `drain_deadline`, only gating `now + ACTIVE_CONTROL_POLL_INTERVAL` on `cancel_requested is not None or not has_producer_sentinel`. Added a real spawned-process regression test (`test_host_one_shot_terminal_handoff_wakes_on_producer_sentinel_and_expires_drain_when_descendant_holds_pipe`) covering producer exit with a descendant holding the pipe open and subsequent `drain_deadline` expiry.
  - **`F2` (P1 — Phase 2 custody wait proposed a polling cadence prohibited by `[MF-5]`)**: **Accepted**. Removed the proposed custody polling loop from bare-TID `weft result <tid>` without `--timeout` (which `[MF-5]` lines 1215–1220 and 1244 intentionally define as returning `observation_metadata_unavailable` without polling non-task queues) and focused Phase 2 purely on event-driven unconfigured terminal settlement using existing `_grace_deadline` and `decision.wait_until`.
  - **`F3` (P1 — Symbol drift in Phases 2–4 against checkout `709fb781`)**: **Accepted**. Rewrote Phase 2 against module-level `_wait_unconfigured_result`, `_iter_consuming_result`, `_CollectedResult._diagnostic`, `_CollectedResult._grace_deadline`, `_CollectedResult._expired`, and `result_cmd.await_task_result`; rewrote Phase 3 against `ReservedPolicy` and `PipelineTask._handle_control_command` (leaving `PipelineTask.control_policy = TaskControlPolicy(...)` untouched). Also corrected Phase 1's root-cause text to note that `reduce_task_state_snapshot` is a pure reducer without an existing PID-liveness branch, and added terminal-manager preservation and `weft.log.tasks` folding coverage to Step 1.1.
  - **`F4` (P1 — Section 4c replacement boundaries risked deleting unrelated clauses in `[PL-4.4]`, `[OBS.15]`, and `[MF-5]`)**: **Accepted**. Scoped every `Replacement (<path>):` block in Section 4c to its exact target line span in `709fb781`, preserving `already-completed stages remain completed` and `Baseline fail-fast propagation` in `[PL-4.4]`, preserving the exact `[OBS.15]` invariant in `07-System_Invariants.md`, and preserving the exact `[MF-5]` paragraphs in `05-Message_Flow_and_State.md`.
  - **`F5` (P2 — Class-5 execution gates, fixed spec-baseline SHA, mid-plan review gates, stop conditions, and anti-mocking boundaries)**: **Accepted**. Added the fixed baseline SHA (`709fb78112f797f60c4dea6c709d2c062830211d`) and per-phase promotion baseline rule in Section 4b, explicit anti-mocking and stop conditions in Section 5, and mid-plan Tier-1 review gates after Phase 2 and Phase 4 in Section 5 and Section 7.
  - **`F6` (P2 — `[WT-7]` two-factor fit test structure and `[WT-0]` audit table carry-forward)**: **Accepted**. Rewrote Section 6a with the full five-part `[WT-7]` Author Account (verdict, sections rested on, refusals with `[ALT-WT-*]` citations, falsifiable predictions, and worst-fitting sentence), carried the 46 baseline `[WT-0]` audit rows forward by reference while re-auditing all touched rows, and added Phase 5 Step 5.1 to run the blind other-family account over `docs/program-theory.md` + the fixed diff only.
  - **`F7` (P2 — Phase 5 verification scope and formatter check)**: **Accepted**. Added `ruff format --check` to every phase gate and expanded Phase 5 Step 5.2.

### Plan Review Pass 2 (`codex` via `skills/call-agent/SKILL.md`, session `01a1b38b-9d44-7423-b81e-b1b4f39aaf7a`)

- **Verdict**: `REVISE` (confirmed `F2`, `F4`, `F5`, and `F6` resolved; 5 remaining items `R1`–`R5`).
- **Findings and Dispositions**:
  - **`R1` (P1 — Phase 1 test call signatures for `system_status` and `task_status`, and `include_log_history=True`)**: **Accepted**. Updated Step 1.1 to construct `context = build_context(spec_context=workdir)`, call `system_status(context, include_terminal=False)`, `system_status(context, include_terminal=True)`, and `system_status(context, include_terminal=True, include_log_history=True)`, and call `task_status(old_mgr_tid, context=context)`.
  - **`R2` (P2 — Phase 1 `_merge_raw_task_history` evidence dataclass name)**: **Accepted**. Updated Step 1.2 to name `SnapshotEvidence(...)` (the actual dataclass at `weft/commands/system.py:525`).
  - **`R3` (P1 — Phase 4 spawned-process test oracle for `drain_expired` and sentinel readiness isolation)**: **Accepted**. Updated Step 4.1 and Section 6a Prediction 3 to assert `outcome.error == "Worker result channel failed before a result was received"`, `outcome.returncode == 7`, and `outcome.diagnostics["extra"]["handoff_event"] == "drain_expired"` (matching `weft/core/runners/host.py:795-838` when `step.event.kind == "drain_expired"`), and to monkeypatch `ACTIVE_CONTROL_POLL_INTERVAL` to `5.0s` so the `< 1.5s` bound proves wake via `process.sentinel` rather than polling.
  - **`R4` (P2 — Phase 4 provider contract wording)**: **Superseded in Pass 4** (dropped duplicate `build_*_invocation` validation per `[WT-4.4]` No Over-Armoring).
  - **`R5` (P1 — Phase 5 full verification commands matching `AGENTS.md` and `pyproject.toml`)**: **Accepted**. Updated Phase 5 Step 5.2 and Section 7 to run `./.venv/bin/ruff check .`, `./.venv/bin/ruff format --check .`, `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`, `./.venv/bin/python -m pytest -m ""`, and `./.venv/bin/python -m pytest extensions integrations -m ""`.

### Plan Review Pass 3 (`codex` via `skills/call-agent/SKILL.md`, session `01a1b38e-fb0f-78e0-a03b-0267f3a968d9`)

- **Verdict**: `PASS` (`R1`–`R5` verified resolved in the operative plan sections).

### External Design Reviews 1 & 2 Reconciliation

- **Findings and Dispositions**:
  - **Review 1 Finding 1 (P1, gates — missing PostgreSQL gate)**: **Accepted**. Added `./bin/pytest-pg --all ...` to the Phase 2 review gate (Step 2.3), the Phase 4 review gate (Step 4.3), and the Phase 5 final closure gate (Step 5.2 and Section 7).
  - **Review 1 Finding 2 (P1, process — author family not recorded)**: **Accepted**. Recorded `Plan author family: antigravity (Google Gemini model family)` and `Independent reviewer family: codex (OpenAI model family)` in the plan header and Section 8.
  - **Review 1 Finding 3 (P2, Phase 4 host floor outcome overstated)**: **Accepted**. Updated Section 1, Section 3.4, Section 4.1, Section 4.2, and Section 6a (`[WT-0]` Row R-2 and `[WT-7]` Author Account) to state accurately that `process.sentinel` wake is added to one-shot host terminal handoff while the 50ms floor is removed only for direct runner calls without a `cancel_requested` callable (`Consumer`-driven runs pass `cancel_requested=self._worker_cancel_requested` and retain the 50ms cancellation poll until cancellation becomes a waitable event).
  - **Review 1 Finding 4 (P2, Phase 4 provider validation affects no production path)**: **Accepted**. Dropped the duplicate `validate_tool_profile` calls inside `build_*_invocation` per `[WT-4.4]` ("No over-armoring") because both production entry paths (`execution.py:100` and `backends/provider_cli.py:76`) already call `provider.validate_tool_profile(...)` before `build_*_invocation` regardless of `preflight`, and moved it to Section 9 (`Out of Scope`).
  - **Review 1 Finding 5 (P3, Phase 2 framing as spec revision)**: **Accepted**. Reframed Phase 2 across Section 1, Section 3.2, and Section 6a as a Class-5 `[MF-5]` spec revision and settlement refinement rather than a violation of the prior spec text.
  - **Review 1 Finding 6 (P3, Phase 1 tension on live non-canonical managers) & Review 2 Unnumbered Policy Finding (Live losing/draining manager vs dead superseded manager)**: **Accepted**. Defined the explicit policy in Section 3.1, Section 4.2, Section 4c, and Phase 1: a briefly live losing Manager (`live_runtime=True` during the `[ALT-WT-007]` convergence/drain window) keeps `status="running"` with `reconciliation={"classification": "superseded_manager_draining", "reason": "different_active_manager_selected", "lifecycle_status": status, "active_manager_tid": selected_active_manager_tid}`, whereas a dead or runtimeless superseded Manager (`live_runtime=False`) reconciles to `status="failed"` with `reconciliation={"classification": "superseded_manager_record", "reason": "different_active_manager_selected", "lifecycle_status": "failed", "active_manager_tid": selected_active_manager_tid}`. Added unit and integration tests for both live-draining and dead superseded Managers, updated `test_cmd_status_marks_superseded_manager_record_failed`, and changed the Section 6a `[WT-7]` Author Account verdict to `fits with strain`.
  - **Review 1 Finding 7 (P3, Phase 3 `RESUME` activity restoration) & Review 2 Finding 3 (P2, Pipeline control retries cannot repair partial delivery)**: **Accepted**. Updated `PipelineTask._handle_control_command` to broadcast `CONTROL_PAUSE` / `CONTROL_RESUME` unconditionally on every explicit command (so repeating `PAUSE` or `RESUME` repairs partial child write failures without acknowledgement tracking) and to save/restore `self._pre_pause_activity` only on the `paused -> running` transition (`was_paused = self._paused`). Added `test_pipeline_repeated_pause_and_resume_repairs_failed_child_control_write_without_clobbering_activity` to Step 3.1.
  - **Review 2 Finding 1 (P2, Short timeouts vs `wrapper_lost` grace window)**: **Accepted**. Updated `_CollectedResult._diagnostic` so that when `terminal.classification == "wrapper_lost"`, `_diagnostic` returns `None` whenever `(grace is not None and self.deadline < grace) or not self._expired(grace, now)`. Thus whenever `deadline < grace` (e.g. `observed_at=10.0`, `grace=10.5`, `deadline=10.1`), `settle()` returns `_ResultSettlement("timeout")` (CLI exit 124) both at `now=10.1` and on delayed evaluation at `now=10.5`. Added both policy test cases in Step 2.1 and documented the precedence in Section 3.2, Section 4.2, and Section 4c.
  - **Review 2 Finding 2 (P2, Terminal-result fixture requires `"type": "terminal"`)**: **Accepted**. Updated Step 2.1's `test_unconfigured_wrapper_lost_settles_after_grace_under_explicit_timeout` to seed `T{tid}.ctrl_out` with a valid literal envelope containing `"type": TERMINAL_ENVELOPE_TYPE` (`"terminal"`), `"tid": tid`, `"status": "failed"`, `"event": "work_failed"`, `"source": "manager"`, and `"error": WRAPPER_LOST_ERROR`.
  - **Review 2 Finding 4 (P2, Phase 3 names the wrong pipeline test file)**: **Accepted**. Replaced all references to `tests/core/test_pipeline_runtime.py` with `tests/tasks/test_pipeline_runtime.py`.

### Plan Review Pass 4 (`codex` via `skills/call-agent/SKILL.md`, session `01a1b667-09ef-7881-9e06-326633467968`)

- **Verdict**: `REVISE` (verified external Reviews 1 and 2 largely integrated; surfaced 8 precision items across Phase 1–4).
- **Findings and Dispositions**:
  - **Pass 4 Finding 1 (P1 — Guard `evidence.runtime_observation is not None` in `_select_snapshot_policy` and preserve live-draining current-view entries in `_merge_raw_task_history`)**: **Accepted**. Guarded `evidence.runtime_observation is not None and evidence.runtime_observation.live` in `_select_snapshot_policy` and updated `_merge_raw_task_history` to skip any `existing is not None` when `record.status not in TERMINAL_TASK_STATUSES`.
  - **Pass 4 Finding 2 (P1 — `docs/specifications/13-Agent_Runtime.md` replacement line span and quoted baseline text)**: **Accepted**. Corrected the target span to lines 258–261 and matched the exact baseline wording (`resumed turns use codex exec resume and only repeat`) so line 262 (`- **agy**`) is untouched.
  - **Pass 4 Finding 3 (P2 — Delayed evaluation of short timeout `deadline < grace` at `now >= grace`)**: **Accepted**. Updated `_CollectedResult._diagnostic` to check `(grace is not None and self.deadline < grace) or not self._expired(grace, now)` and added `now=10.5` coverage for `deadline=10.1` in Step 2.1.
  - **Pass 4 Finding 4 (P2 — Nonexistent `build_terminal_envelope` helper)**: **Accepted**. Replaced with a literal JSON dict using `TERMINAL_ENVELOPE_TYPE` and `WRAPPER_LOST_ERROR`.
  - **Pass 4 Finding 5 (P2 — Repeated `CONTROL_RESUME` overwriting restored activity)**: **Accepted**. Gated activity restoration on `was_paused = self._paused` while keeping `_broadcast_control(CONTROL_RESUME)` unconditional, and expanded the Step 3.1 retry test to cover repeated `CONTROL_RESUME`.
  - **Pass 4 Finding 6 (P2 — Symbol and section-code accuracy)**: **Accepted**. Replaced `PipelineTask._bootstrap` with `PipelineTask._bootstrap_children`, `AgentSession._execution_wait_seconds` with `AgentSession._terminal_handoff_wait_seconds`, and cleaned up the Section 2 `[WT-*]` / `[DOM-*]` citations.
  - **Pass 4 Finding 7 (P2 — Promotion baseline wording)**: **Accepted**. Clarified in Section 4b and Steps 1.3/2.3/3.3/4.3 that each phase records its resulting phase commit SHA (or `<diff-base-sha> + dirty (<spec-paths>)`) as the promotion baseline identifier.
  - **Pass 4 Finding 8 (P3 — Expected RED result in Step 2.1 and `AgentSession` difference-register note)**: **Accepted**. Clarified the exact RED failure at `now=10.5` in Step 2.1 and noted in Section 3.4 and Section 4.2 that `AgentSession._terminal_handoff_wait_seconds` gates polling on `cancel_requested is not None` while `HostTaskRunner._terminal_handoff_wait_seconds` gates on `cancel_requested is not None or not has_producer_sentinel`.

### Plan Review Pass 5 (`codex` via `skills/call-agent/SKILL.md`, session `01a1b667-09ef-7881-9e06-326633467968`)

- **Verdict**: `PASS` (verified all findings from External Reviews 1 and 2 and Pass 4 resolved against checkout `709fb78112f797f60c4dea6c709d2c062830211d`; zero remaining findings).

### Follow-Up Phase 1 Design Review Reconciliation & Plan Review Pass 6 (`codex` via `skills/call-agent/SKILL.md`, session `01a1b667-09ef-7881-9e06-326633467968`)

- **Findings and Dispositions**:
  - **Finding A (P2/P3 — Manager policy collapsed `unknown` liveness into `failed`)**: **Accepted**. Replaced boolean `live_runtime: bool` with three-state `manager_liveness: Literal["live", "non_live", "unknown"] = "unknown"` via `_manager_runtime_liveness(mapping_entry)` in `weft/commands/system.py` (and matching `RuntimeObservation` evaluation in `_select_snapshot_policy`). Reconciles a non-terminal superseded Manager to `status="failed"` (`superseded_manager_record`) **only** on positive `non_live` evidence (`manager_liveness == "non_live"`, or `evidence.runtime_observation is not None and not evidence.runtime_observation.live and evidence.runtime_observation.strength == "strong"`), while preserving `status="running"` with `classification="superseded_manager_draining"` and `runtime_liveness="live" | "unknown"` when the Manager is confirmed alive or liveness is unknown (absent handle, missing exact `create_time` identity, failed OS probe, unsupported control authority, or log-only fold without runtime observation per `[WT-4.3]`). Added absent-handle (`"unknown"`), missing-identity, and failed-probe test cases alongside confirmed-dead (`"non_live"`) and live-losing (`"live"`) test cases in `tests/commands/test_task_snapshot_reducer.py`, `tests/commands/test_status.py`, and `tests/commands/test_task_commands.py`.
  - **Finding B (P2 — `_merge_raw_task_history` guard was wider than the Manager fix)**: **Accepted**. Narrowed the skip guard in `weft/commands/system.py:513-519` to `existing.snapshot.reconciliation is None or existing_classification in {"superseded_manager_record", "superseded_manager_draining"}`, preserving the existing log-history fallback for ordinary tasks whose current projection is invalid or unavailable (`projection_invalid` / `projection_unavailable`) while protecting reconciled Manager projections. Added explicit test coverage for an ordinary task with an invalid projection falling back to `weft.log.tasks` in `test_system_status_distinguishes_dead_live_absent_handle_and_invalid_projection_fallback`.
  - **Pass 6 Finding 1 (P1 — `handle_has_live_host_process` collapsed `psutil.AccessDenied` and missing `create_time` into `non_live`, and `_select_snapshot_policy` treated `strength="weak"` as `non_live`)**: **Accepted**. Updated `_manager_runtime_liveness` to use `inspect_host_process(pid, create_time)` from `weft.liveness.host` across `handle.scoped_host_processes()` so `AccessDenied` (`process_unresolved`) and `create_time is None` (`missing_exact_identity`) return `"unknown"`, and updated `_select_snapshot_policy` to require `evidence.runtime_observation.strength == "strong"` before reconciling to `failed`.
  - **Pass 6 Finding 2 & Pass 7 Finding 1 (P2 — `_enrich_known_snapshot` in `weft/commands/tasks.py` overwrote `superseded_manager_draining` with `stale_liveness` on aged absent-handle Managers, and `_stale_liveness_reason` checks `snapshot.last_timestamp` rather than `started_at`)**: **Accepted**. Updated `_enrich_known_snapshot` (`weft/commands/tasks.py:878-916`) to guard `if stale is not None and existing_classification not in {"superseded_manager_record", "superseded_manager_draining"}:` and updated `test_task_status_reconciles_dead_live_and_absent_handle_superseded_manager_projections` to monkeypatch `weft.commands.system.STATUS_RUNTIMELESS_STALE_AFTER_SECONDS = 0.0` (or pass `last_timestamp=1` to `_enrich_known_snapshot`) so `_stale_liveness_reason` fires `"runtime_missing_after_stale_window"` during the test.
  - **Pass 6 Finding 3 (P3 — Verbatim `[WT-4.3]` quote in Section 6a item 5)**: **Accepted**. Replaced with the exact verbatim passage from `docs/program-theory.md` line 470.
- **Pass 8 Verdict (`codex` session `01a1b667-09ef-7881-9e06-326633467968`)**: `PASS` (all follow-up Phase 1 findings and Pass 6/7 items verified against checkout `709fb78112f797f60c4dea6c709d2c062830211d`).

### Mid-Plan Implementation Review (Phases 1–2, `codex` session `01a1b667-09ef-7881-9e06-326633467968`)

- **Findings and Dispositions**:
  - **Finding 1 (P2 — `select_active_manager(ctx, broker=broker)` defaulted `probe_stale=False` but passed `prune_stale=True` to `_registry_view`, causing `_manager_registry_disposition` to send a `PING` to `T{tid}.ctrl_in` when an active manager registry record had `liveness == "unknown"`)**: **Accepted**. Updated `select_active_manager` in `weft/core/manager_runtime.py:1094` to pass `prune_stale=probe_stale` to `_registry_view(...)` (all callers that want active `PING` probing already pass `probe_stale=True`), and added regression assertions in `tests/commands/test_status.py` and `tests/commands/test_task_commands.py` verifying that `T{mgr_tid}.ctrl_in` remains empty after `system_status` and `task_status`.
  - **Finding 2 (P2 — `_selected_active_manager_tid` in `weft/commands/system.py` used `_active_canonical_manager_records(managers)` shortcuts that selected an unproven `liveness == "unknown"` namespace-ambiguous manager in bulk status while `select_active_manager` returned `None` in point status)**: **Accepted**. Updated `_selected_active_manager_tid` in `weft/commands/system.py` to delegate `managers` snapshots to `manager_runtime._select_active_manager_from_snapshot(ctx, snapshot)` (stripping `MANAGER_PONG_LIVE_AT_KEY` from copies so bulk and point status apply identical passive canonical selection rules), and added `test_system_status_and_task_status_stay_passive_and_agree_when_only_active_manager_is_unproven` in `tests/commands/test_status.py`.
  - **Finding 3 (P2 — `iter_task_realtime_events(..., follow=False)` in `weft/commands/events.py` sets `deadline = time.monotonic()` and called `collected.settle(...)` on Turn 2 when `materialized.config is None`, emitting spurious `state`/`result`/`end(status="unknown")` events after `snapshot`)**: **Accepted**. Added `if materialized.config is None and not follow: return` in `iter_task_realtime_events` (`weft/commands/events.py:560-561`) after the initial `snapshot` turn, restoring `test_realtime_unknown_metadata_requires_explicit_terminal_evidence[control]` while preserving `follow=True` settlement in `test_http_views_handle_invalid_and_unknown_tids_cleanly`.
- **Verdict (`codex` session `01a1b667-09ef-7881-9e06-326633467968`)**: `PASS` at `3b0f5a18` (Phase 1) and `e2a98d9c` (Phase 2).

### Mid-Plan Implementation Review (Phases 3–4, `codex` session `01a1b667-09ef-7881-9e06-326633467968`)

- **Findings and Dispositions**:
  - **Phase 3 (`0650cd15`)**: Zero defects found.
  - **Phase 4 Finding 1 (P2 — `HostTaskRunner._run_one_shot_terminal_handoff` passed pre-sample `now` to `_terminal_handoff_wait_seconds` after `monitor.check_limits()`)**: **Accepted**. Updated `weft/core/runners/host.py` to pass `now=time.monotonic()` into `_terminal_handoff_wait_seconds`, matching `AgentSession.execute`, and added `test_host_one_shot_terminal_handoff_recomputes_wait_against_fresh_clock_after_monitor_sample` (patching `host_runner_mod.time` via `SimpleNamespace` so the shared stdlib `time` module is never mutated).
  - **Phase 4 Finding 2 (P2 — `_leaking_function_target` in `tests/core/test_terminal_handoff_transport.py` used `isinstance(candidate, Connection)` and `subprocess.Popen(..., pass_fds=...)`, which are POSIX-only)**: **Accepted**. Updated `_leaking_function_target` to detect `result_sender` via `hasattr(candidate, "send_bytes") and hasattr(candidate, "poll")`, set `multiprocessing.current_process().daemon = False`, and call `_leak_sender_to_grandchild(result_sender, pidfile)` from `tests.helpers.leaked_sender`.
- **Verdict (`codex` session `01a1b667-09ef-7881-9e06-326633467968`)**: `PASS` at `0650cd15` (Phase 3) and `08314b51` (Phase 4).

### Post-Implementation Review & Final Closure Verification

- **Findings and Dispositions**:
  - **Finding 1 (Bulk status still sent a `PING`)**: **Accepted**. Updated `_manager_registry_disposition` (`weft/core/manager_runtime.py:229`) to honor `probe_stale` before calling `_manager_record_has_matched_pong`, added `probe_stale: bool | None = None` to `list_manager_records` (`weft/core/manager_runtime.py:1054`), and updated `_collect_manager_records` (`weft/commands/system.py:214`) to pass `probe_stale=False`. Removed the `ctrl_in` drain from `test_system_status_and_task_status_stay_passive_and_agree_when_only_active_manager_is_unproven` (`tests/commands/test_status.py:3256`) and asserted both `ctrl_in.peek_many(limit=10) == []` and zero `send_keyed_ping_probe` calls immediately after `system_status` and after each `task_status` call.
  - **Finding 2 (`_manager_runtime_liveness` lost the container PID-namespace safeguard)**: **Accepted**. Updated `_manager_runtime_liveness` (`weft/commands/system.py:306`) to delegate directly to `manager_runtime.manager_registry_record_liveness(mapping_entry)` (`weft/core/manager_runtime.py:928`), mapping `"live" -> "live"`, `"stale" -> "non_live"`, and `"unknown" -> "unknown"` so container observers treat an absent host PID (`reason == "process_absent"` when `detect_container_runtime() is not None`) as `"unknown"` rather than `"non_live"` per `[WT-4.3]`. Added `test_superseded_manager_liveness_preserves_container_pid_namespace_safeguard` in `tests/commands/test_status.py` and container-namespace coverage in `tests/commands/test_task_commands.py`.
- **Verdict (`codex` session `01a1b667-09ef-7881-9e06-326633467968`)**: `PASS` (both post-implementation findings verified resolved; zero defects found).

## 9. Out of Scope

- Adding duplicate `self.validate_tool_profile(tool_profile)` calls inside `provider_cli` `build_invocation` and `build_session_invocation` (`weft/core/agents/provider_cli/registry.py`): rejected per `[WT-4.4]` ("No over-armoring: Trust internal callers and data shapes once validated at the entry boundary"). Both production entry paths (`prepare_provider_cli_turn` at `weft/core/agents/provider_cli/execution.py:100` and `ProviderCLISession._validate_startup` at `weft/core/agents/backends/provider_cli.py:76`) already call `provider.validate_tool_profile(...)` before `build_*_invocation` regardless of `preflight`.
- Converting `Consumer` worker cancellation (`self._worker_cancel_requested`) from a boolean callable into an OS-level waitable event handle across all runner plugins (separate architectural slice; Phase 4 adds `process.sentinel` wake to one-shot host terminal handoff and removes the 50ms floor when `cancel_requested is None`).
- Changing `CodexProvider` to override `CODEX_HOME` to a temporary directory (rejected: `codex exec` has no creation-time `--session-id` flag and depends on `CODEX_HOME` for `auth.json` and `config.toml`; documented as a concurrency boundary in `13-Agent_Runtime.md` `[AR-2.1]`).
- Adding submission-custody polling to bare-TID `weft result <tid>` without `--timeout` (rejected per `[MF-5]` lines 1215–1220, 1226–1227, and 1244, and `[ALT-WT-002]`).
- Structural decomposition of `weft/core/manager.py`, `weft/core/tasks/base.py`, or `weft/commands/system.py` (explicitly non-issues per `AGENTS.md` §1.1: cohesive state ownership).
- New CLI commands, new queue names, or TaskSpec schema changes.

## 10. Fresh-Eyes Review

Performed a fresh-eyes pass over this revised plan against checkout `709fb78112f797f60c4dea6c709d2c062830211d`, `docs/agent-context/runbooks/writing-plans.md`, `docs/agent-context/runbooks/hardening-plans.md`, and all findings from Reviews 1, 2, Pass 4, and the follow-up Phase 1 review:
- Verified every function, method, class, and test file path (`tests/tasks/test_pipeline_runtime.py`, `tests/commands/test_task_snapshot_reducer.py`, `tests/commands/test_status.py`, `tests/commands/test_task_commands.py`, `tests/commands/test_result_settlement_policy.py`, `tests/commands/test_retained_result_history.py`, `tests/core/test_manager.py`, `tests/core/test_terminal_handoff_transport.py`) against the repository checkout.
- Verified that `_superseded_manager_reconciliation` (`reason: "different_active_manager_selected"`) and `_draining_manager_reconciliation` (`runtime_liveness: "live" | "unknown"`) match existing reducer conventions in `weft/commands/_task_snapshot_reducer.py:675-681`, that only positive `non_live` evidence marks a superseded Manager `failed`, and that `_merge_raw_task_history` preserves the log fallback for ordinary tasks with invalid/unavailable projections.
- Verified that `_CollectedResult._diagnostic` preserves `_ResultSettlement("timeout")` (exit 124) whenever `deadline < grace_deadline` (both at `now == deadline` and at `now >= grace_deadline`) and returns `observation_metadata_unavailable` (exit 1) when `grace_deadline <= deadline`.
- Verified that the literal `T{tid}.ctrl_out` test envelope includes `"type": TERMINAL_ENVELOPE_TYPE` (`"terminal"`) required by `coerce_terminal_envelope`.
