# Deep-Dive Review Multi-Phase Remediation Plan

Status: completed
Source specs: docs/specifications/01-Core_Components.md [CC-2.1], [CC-2.4], [CC-2.4.1]; docs/specifications/03-Manager_Architecture.md [MA-1], [MA-1.6a]; docs/specifications/05-Message_Flow_and_State.md [MF-3], [MF-3.1], [MF-3.2], [MF-5]; docs/specifications/07-System_Invariants.md [QUEUE.7], [OBS.6], [OBS.13.7], [LIVENESS.R1], [REDUCER.1]; docs/specifications/10-CLI_Interface.md [CLI-1.2.1], [CLI-1.2.3]; docs/specifications/11-CLI_Architecture_Crosswalk.md [CLI-X1]; docs/specifications/12-Pipeline_Composition_and_UX.md [PL-2.2], [PL-2.3]; docs/specifications/13-Agent_Runtime.md [AR-2.2], [AR-5]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-10.1], [DJ-13.2]; docs/specifications/14-Python_API_Surfaces.md [PY-2], [PY-3]
Superseded by: none

Class: 5 (implementation with spec revision; touches the durable watcher/task/manager spine in Phases 1–3 and updates `docs/specifications/14-Python_API_Surfaces.md` and `docs/specifications/11-CLI_Architecture_Crosswalk.md` in Phase 6; no runbook, skill, or gate changes; one-way doors: none).

---

## 1. Goal

Remediate the defects, contract inconsistencies, and spec drift identified during the 2026-11-06 deep-dive review at `5514f3ec` (`v0.9.121`) in six commit-isolated phases:

- **Phase 1 (Core Runtime & Reactor Mechanics):** Gate `inbox` before reservation when `Consumer` or `PipelineEdgeTask` is paused, evict already-active queues from `MultiQueueWatcher._active_queues` when `_queue_counts_as_wait_activity` turns `False`, and force an immediate re-probe on the turn after `CONTROL_RESUME` or work-item completion (`[WT-4.1]`, `[WT-4.3]`, `[CC-2.1]`, `[CC-2.4]`, `[PL-2.3]`).
- **Phase 2 (Observability, Liveness & State Projections):** Reject retained terminal `weft.state.tasks.<tid>` snapshots (`mapping.get("terminal") is True`) that lack a runtime handle in `weft/core/endpoints.py::_record_owner_is_live` (`[MF-3.1]`, `[WT-3]`), read only the newest row in `HeartbeatTask._endpoint_registry_version` without duplicate pre-resolution reads (`[WT-4.1]`, `[MF-3.2]`), retain the integer message ID returned by `queue.write(...)` in `PipelineTask._write_pipeline_registry_record` (`[WT-4.1]`, `[PL-2.2]`), and align stale comments in `TaskMonitor` (`[WT-2]`, `[OBS.13.7]`).
- **Phase 3 (Manager Supervision & Service Candidate Ordering):** Remove synchronous `time.sleep` retries from `Manager._commit_wrapper_lost_envelope` on normal (`deadline is None`) reactor turns so retries use the existing `child.terminal_retry_after_ns` reactor deadline (`[MA-1]` item 7, `[WT-4.1]`), check `weft.state.tasks.<tid>` before scanning `weft.log.tasks` in `Manager._tracked_service_terminal_proof_visible`, and align `weft/core/manager_services.py` candidate TID ordering on strict `int(candidate.tid)` comparison (`[MA-1.6a]`, `[REDUCER.1]`).
- **Phase 4 (Agent Runtime & Runner Extensions):** Thread `bundle_root` through `LLMBackend` and `LLMBackendSession` using `import_module_ref` and `import_ref` from `weft/core/imports.py` (`[AR-2.2]`, `[AR-5]`), and enforce `runtime_requirements.has_missing_required` in `DockerProviderCLIRunner.run_with_hooks` (`[AR-5]`).
- **Phase 5 (CLI, Commands, Client & `weft_django` Surfaces):** Return the encoded short TID from `cmd_task_tid(..., reverse=...)` (`[PY-2]`), allow human-readable non-persistent `--name` values on `weft run --pipeline` in `_apply_explicit_run_name` while rejecting `_weft.*` in `_derive_name` (`[PY-3]`), replace `_task_snapshot_payload` callers in `weft/cli/app.py` with `_status_task_json_payload` (`[CLI-1.2.1]`), and align `weft_django` views and `WeftDeferredSubmission` with `[DJ-10.1]` and `[DJ-13.2]`.
- **Phase 6 (Specification, Theory Audit & Governance Closeout):** Promote the `docs/specifications/14-Python_API_Surfaces.md` and `docs/specifications/11-CLI_Architecture_Crosswalk.md` updates, refresh the touched `[WT-0]` theory-versus-code audit rows, run and record the Class-5 `[WT-7]` two-factor theory-fit test, and close out `docs/plans/README.md`.

---

## 2. Source Documents

### Governing Specifications
- `docs/specifications/01-Core_Components.md` `[CC-2.1]` (MultiQueueWatcher scheduling and wake arbiter), `[CC-2.4]` (control channel `PAUSE`/`RESUME`), `[CC-2.4.1]` (named endpoints)
- `docs/specifications/03-Manager_Architecture.md` `[MA-1]` item 7 (`wrapper_lost` failsafe publication and reactor retry deadline), `[MA-1.6a]` (managed internal service convergence and candidate ordering)
- `docs/specifications/05-Message_Flow_and_State.md` `[MF-3]` (control messages), `[MF-3.1]` (named endpoint discovery and terminal precedence), `[MF-3.2]` (heartbeat service), `[MF-5]` (task state snapshots and terminal evidence precedence)
- `docs/specifications/07-System_Invariants.md` `[QUEUE.7]` (fixed reactor topology and notification interests), `[OBS.6]` (task state snapshot retention), `[OBS.13.7]` (row-presence handoff between `LivenessMonitor` and `TaskMonitor`), `[LIVENESS.R1]` (unified liveness policy), `[REDUCER.1]` (service convergence reducer)
- `docs/specifications/10-CLI_Interface.md` `[CLI-1.2.1]` (JSON timestamp formatting rules for status and task projections), `[CLI-1.2.3]` (`weft task tid`)
- `docs/specifications/11-CLI_Architecture_Crosswalk.md` `[CLI-X1]` (`system` command family module ownership)
- `docs/specifications/12-Pipeline_Composition_and_UX.md` `[PL-2.2]` (pipeline registry records), `[PL-2.3]` (pipeline edge execution and pause semantics)
- `docs/specifications/13-Agent_Runtime.md` `[AR-2.2]` (spec-bundle import resolution for tools, output schemas, and plugin modules), `[AR-5]` (provider CLI container runtime requirements)
- `docs/specifications/13C-Using_Weft_With_Django.md` `[DJ-10.1]` (`WeftSubmission` and `WeftDeferredSubmission` handle contract), `[DJ-13.2]` (request-local client reuse in synchronous views)
- `docs/specifications/14-Python_API_Surfaces.md` `[PY-2]` (`weft.commands` bijection and semantic execution), `[PY-3]` (submission and non-persistent name validation)

### Program Theory & Historical Plan Context
- `docs/program-theory.md` `[WT-0]` (theory-versus-code audit table), `[WT-2]` (task-local state ownership and `LivenessMonitor` row-presence handoff), `[WT-3]` (liveness authority), `[WT-4.1]` (wake predicates over queued work are true iff actionable work is present; gate watched queues rather than reserve-and-requeue loops; retain append IDs), `[WT-4.3]` (observable quiet), `[WT-7]` (Class-5 two-factor fit test and audit refresh), `[ALT-WT-002]`, `[ALT-WT-019]`
- `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md` (`completed`): established the `[WT-4.1]`, `[WT-4.3]`, and `[WT-7]` verification rules
- `docs/plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md` (`completed`): repaired Manager idle-clock spin and wake latency (`0.0431s` CPU in `3.50s` supervising = `1.23%` of 1 core; `0.0361s` CPU in `3.00s` idle = `1.20%` of 1 core; `20.22 ms` median wake-to-claim latency across 5 sequential submissions at `5514f3ec`)
- `docs/plans/2026-11-02-cli-client-surface-parity-plan.md` (retired at `0ff8cfbf`): established the `weft.commands` ↔ `weft.cli` ↔ `weft.client` parity contract
- `docs/plans/2026-11-02-tid-display-encoding-plan.md` (retired at `0ff8cfbf`): introduced lossless base62 `encode_tid` for `weft task tid --reverse`

### Local Guidance
- `AGENTS.md` §1.1, §3, §4
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

---

## 3. Context and Root-Cause Analysis

### Phase 1: Core Runtime & Reactor Mechanics (`weft/core/tasks/multiqueue_watcher.py`, `base.py`, `consumer.py`, `pipeline.py`, `manager.py`)
- **Symptom & Measurement:** Driving a paused `Consumer` via `run_until_stopped()` with 1 work item in `inbox` executed **324 reserve+requeue cycles in `107.1 ms` (`3,026 cycles/sec`)**. Driving a paused `PipelineEdgeTask` across 5 `process_once()` turns executed 5 `inbox -> reserved -> inbox` moves.
- **Root Cause:**
  1. `Consumer._queue_counts_as_wait_activity` (`weft/core/tasks/consumer.py:165-170`) checks `getattr(self, "_active_work_in_flight", False)` and returns `config.name == self._queue_names["ctrl_in"]`, but does **not** check `getattr(self, "_paused", False)`. `PipelineEdgeTask` (`weft/core/tasks/pipeline.py:144-163`) does not override `_queue_counts_as_wait_activity` at all. While paused, `Consumer._handle_work_message` (`consumer.py:190-210`) reserves the message into `reserved` and calls `reserved_queue.move_one(...)` directly back to `inbox`, and `PipelineEdgeTask._handle_work_message` (`pipeline.py:161-163`) calls `self._requeue_reserved_message()`.
  2. `MultiQueueWatcher._update_active_queues` (`weft/core/tasks/multiqueue_watcher.py:1392-1396`) computes `still_active` using only `self._queue_has_pending(self._queues[name].queue)` without checking `self._queue_counts_as_wait_activity(self._queues[name])`. Thus any queue already in `_active_queues` when its wait-activity predicate turns `False` remains in `_active_queues` on subsequent `_drain_queue_messages` passes. (Note: when `Consumer._active_work_in_flight` is `True`, `Consumer._process_reactor_turn` at `consumer.py:130-142` takes a dedicated busy branch—`_drain_worker_results()` + `_poll_active_control_once()`—bypassing `super()._process_reactor_turn()`, whereas a paused `Consumer` or `PipelineEdgeTask` runs `super()._process_reactor_turn()` -> `_drain_queue_messages()` -> `_update_active_queues()`.)
  3. On `CONTROL_RESUME` (`BaseTask._handle_control_command`, `weft/core/tasks/base.py:1765-1772`) or work completion (`Consumer._handle_active_work_result`, `weft/core/tasks/consumer.py:585-588`), setting `self._pending_messages_precheck_confirmed = True` and calling `self._strategy.notify_activity()` ensures `_update_active_queues()` sets `should_probe_all = True` on the immediate next turn rather than waiting up to `_inactive_probe_interval` (`1.0s`). Because `_update_active_queues()` runs at the start of `_drain_queue_messages` before `ctrl_in` is drained on the `CONTROL_RESUME` turn, a message waiting in `inbox` becomes actionable and is processed on the turn immediately following the `CONTROL_RESUME` turn.
- **Reach into `Manager`:** `Manager` is the other `MultiQueueWatcher` subclass that overrides `_queue_counts_as_wait_activity` (`weft/core/manager.py:1280-1296`), returning `False` for `reserved` and for admission-blocked spawn lanes (unless `self._family_classification_can_advance()` is `True`). Filtering `still_active` by `_queue_counts_as_wait_activity` is safe for `Manager` and improves consistency: `Manager._has_pending_messages` sets `_pending_messages_precheck_confirmed = True` whenever an unblocked lane has pending messages or `_unclassified_source_row_pending()` is `True`, and `Manager._resume_startup_dispatch` / `_clear_admission_retry` re-enable blocked lanes. Phase 1 gates verify `Manager` reactor-floor, admission-recovery, and idle wake latency directly.

### Phase 2: Observability, Liveness & State Projections (`weft/core/endpoints.py`, `weft/core/tasks/heartbeat.py`, `weft/core/tasks/pipeline.py`, `weft/core/monitor/task_monitor.py`)
- **`endpoints._record_owner_is_live` (`weft/core/endpoints.py:280-307`):** Checks `task_statuses.get(record.tid) in TERMINAL_TASK_STATUSES` (from `weft.log.tasks`) and then reads `mapping = tid_states.get(record.tid)` (from `weft.state.tasks.<tid>`). Once `TaskMonitor` compacts `weft.log.tasks`, a completed task whose `weft.state.tasks.<tid>` snapshot is retained for 40 minutes (`WEFT_TASK_OBSERVATION_RETENTION_SECONDS`, `[OBS.6]`) with `terminal: True` and `runtime_handle: None` hits `if handle_payload is None: return True` at line 296 and is misclassified as live. (Snapshots that still carry a dead host-PID handle already return `False` via `handle_has_live_host_process`; the bug specifically affects terminal snapshots without a runtime handle.) Checking `if mapping.get("terminal") is True: return False` before `if handle_payload is None: return True` matches `mapping_row_is_live` (`weft/liveness/policy.py:182-209`).
- **`HeartbeatTask._service_ownership` & `_endpoint_registry_version` (`weft/core/tasks/heartbeat.py:630-678`):** `_endpoint_registry_version` iterates every row in `WEFT_ENDPOINTS_REGISTRY_QUEUE` via `peek_generator(with_timestamps=True)` to compute `max(timestamp)`, and `_service_ownership` calls `_endpoint_registry_version()` twice before `resolve_endpoint` when the cached version misses. SimpleBroker timestamps are strictly monotonic; `queue.peek_many(limit=1, order="newest", with_timestamps=True)` returns the newest `(body, timestamp)` tuple in one read.
- **`PipelineTask._write_pipeline_registry_record` (`weft/core/tasks/pipeline.py:771-811`):** Discards the integer message ID returned by `queue.write(...)` and calls `_latest_pipeline_registry_message_id()` to scan and JSON-decode the entire `weft.state.pipelines` queue. Assigning `self._registry_message_id = queue.write(...)` directly follows `[WT-4.1]` ("Keep an identity you already have"), while retaining `_latest_pipeline_registry_message_id()` only on the deletion path (`_delete_pipeline_registry_record`) if cleanup is reached with `self._registry_message_id is None`.
- **`TaskMonitor` comments (`weft/core/monitor/task_monitor.py:2154-2160, 2188-2194, 2807-2865, 3275-3279`):** Code uses row-presence on `weft.state.tasks.<tid>` (`payload.get("terminal") is not True`), but comments still describe pre-split host-PID and undecidable runtime-handle checks.

### Phase 3: Manager Supervision & Service Candidate Ordering (`weft/core/manager.py`, `weft/core/manager_services.py`)
- **`Manager._commit_wrapper_lost_envelope` (`weft/core/manager.py:4994-5042`):** Loops `TERMINAL_EVENT_WRITE_RETRIES = 3` with `time.sleep(0.2)` even when `deadline is None` (normal `_cleanup_children` and `_retire_scheduler_residents` turns), stalling the Manager reactor thread for up to `0.4s` per child even though `_cleanup_children` (`line 5079`) already arms `child.terminal_retry_after_ns` on `Manager.next_wait_timeout` (`[MA-1]` item 7) and `_retire_scheduler_residents` (`line 3249`) retries on the next scheduler refresh. Bounded in-place retries should run only when `deadline is not None` (shutdown drain).
- **`Manager._tracked_service_terminal_proof_visible` (`weft/core/manager.py:7161-7188`):** Checks `ctrl_out` and then immediately scans `weft.log.tasks` without first checking `read_task_state_snapshot(self._context, tid)` for `snapshot[1].get("terminal") is True`.
- **`weft/core/manager_services.py` (`lines 183, 226, 230, 391-395`):** Orders validated `ServiceCandidate` records by `candidate.tid` (`str`) rather than `int(candidate.tid)`. Real 19-digit `time.time_ns()` TIDs have identical lexicographic and numeric order until year 2286, so this is a contract consistency cleanup aligning `manager_services.py` with `service_owner_tid_key` in `weft/core/service_convergence.py:103-106`.

### Phase 4: Agent Runtime & Runner Extensions (`weft/core/agents/backends/llm.py`, `extensions/weft_docker/weft_docker/agent_runner.py`)
- **`LLMBackend` (`weft/core/agents/backends/llm.py:33-117, 248-260`):** `execute`, `start_session`, and `validate` execute `del bundle_root`; `_register_plugin_modules` and `_resolve_output_schema` call `importlib.import_module` instead of `import_module_ref` / `import_ref` from `weft/core/imports.py:20-54`; and `LLMBackendSession.execute` calls `resolve_agent_tools` without `bundle_root`.
- **`DockerProviderCLIRunner.run_with_hooks` (`extensions/weft_docker/weft_docker/agent_runner.py:128-142`):** Calls `runtime_requirements = self._resolve_container_runtime(...)` without checking `if runtime_requirements.has_missing_required:`, unlike `DockerRunnerPlugin._validate_agent_taskspec` (`extensions/weft_docker/weft_docker/plugin.py:642-649`).

### Phase 5: CLI, Commands, Client & `weft_django` Surfaces (`weft/commands/tasks.py`, `weft/commands/run.py`, `weft/cli/app.py`, `integrations/weft_django`)
- **`cmd_task_tid` (`weft/commands/tasks.py:2321-2327`):** When `reverse is not None`, calls `encode_tid(candidate)` for validation and returns `candidate` instead of `encode_tid(candidate)`, forcing `weft/cli/app.py:1880` to call `encode_tid(result)` in the CLI adapter (`[PY-2]`).
- **`_apply_explicit_run_name` & `_derive_name` (`weft/commands/run.py:699-730, 766-783, 955`):** `_apply_explicit_run_name` has a single caller—`_execute_pipeline` (`weft/commands/run.py:955`)—and calls `validate_endpoint_claim_name(explicit_name)` unconditionally before checking `taskspec.spec.persistent`, causing `weft run --pipeline <pipeline> --name "<free text>"` to fail with `RunUsageError` even though pipelines are non-persistent (inline and `--spec` runs already accept free-text names via `_derive_name` and `apply_submit_overrides`). Meanwhile, `_derive_name` (`lines 766-783`) does not reject the reserved `_weft.` prefix (`INTERNAL_ENDPOINT_NAMESPACE_PREFIX`) on inline runs.
- **`_task_snapshot_payload` (`weft/cli/app.py:1545, 1560-1573, 1737`):** Used by `task_list` (`line 1545`) and `task_status` (`line 1737`), unconditionally formats `snapshot.last_timestamp` and `reconciliation["observed_at"]` with `format_message_id()`, violating `[CLI-1.2.1]` for wall-clock timestamps. `_status_task_json_payload` (`weft/cli/app.py:129-161`) already implements the complete `[CLI-1.2.1]` contract; updating `task_list` and `task_status` to call `_status_task_json_payload` directly allows removing `_task_snapshot_payload`.
- **`weft_django` (`integrations/weft_django/weft_django/views.py:22-30`, `client.py:76-115`):** `_task_snapshot_or_404` calls `get_core_client().task(tid)` instead of reusing `_current_client().core_client.task(tid)` (`[DJ-13.2]`), and `WeftDeferredSubmission` omits `snapshot()` and `terminal_snapshot(timeout=0.0)` (`[DJ-10.1]`).

### Phase 6: Specification, Theory Audit & Governance Synchronization
- `docs/specifications/14-Python_API_Surfaces.md` (`lines 178, 298, 440, 443`) and `docs/specifications/11-CLI_Architecture_Crosswalk.md` (`line 34`) omit the 42nd verb `cmd_system_stop` (`SystemStopResult`, `weft/commands/system.py`) and describe `task tid` return as `"full TID"` instead of `"full/encoded TID"`.
- Class-5 completion (`[WT-0]`, `[WT-7]`) requires refreshing the touched rows of the theory-versus-code audit table and recording the two-factor theory-fit test in `## 8. Independent Review Loop`.

---

## 4. Invariants, Difference Registers, Rollout, and Rollback

### Standing Invariants
- **Single-Thread Broker Ownership & Construction-Fixed Task Topology (`[SB-0.4]`, `[QUEUE.7]`, `[IMPL.10]`):** Never call `add_queue()` or `remove_queue()` on `BaseTask`. Pause/busy gating uses `_queue_counts_as_wait_activity` and `_update_active_queues`. Keep the existing `if self._paused:` and `if self._active_work_in_flight:` requeue blocks inside `_handle_work_message` (`consumer.py:190-220`, `pipeline.py:161-163`) as defensive same-pass guards if a control command and work message were drained in the same pass before `_paused` turned `True`.
- **Canonical Boundaries (`[WT-4]`):** Trust documented SimpleBroker contracts (`queue.write(...) -> int`, `peek_many(..., with_timestamps=True) -> list[tuple[str, int]]`) and validated numeric TIDs (`int(candidate.tid)`), while keeping `except (BrokerError, OSError, RuntimeError)` defenses around actual broker I/O failure boundaries.

### Difference Register 1: `_status_task_json_payload` vs. Removed `_task_snapshot_payload` (`weft/cli/app.py`)
| Field / Behavior | `_status_task_json_payload` (`app.py:129-161`) | Removed `_task_snapshot_payload` (`app.py:1560-1573`) | Phase 5 Resolution |
| :--- | :--- | :--- | :--- |
| `asdict(snapshot)` + `host_pids`, `managed_pids`, `live_managed_pids` list conversion | Yes (`lines 130, 157-159`) | Yes (`lines 1562, 1570-1572`) | Call `_status_task_json_payload` directly in `task_list` (`line 1545`) and `task_status` (`line 1737`); delete `_task_snapshot_payload` |
| `last_timestamp` when `event in WALL_CLOCK_TASK_LAST_TIMESTAMP_EVENTS` or `classification in WALL_CLOCK_TASK_LAST_TIMESTAMP_CLASSIFICATIONS` | Kept as JSON `int` (Unix wall clock) per `[CLI-1.2.1]` | Unconditionally converted via `format_message_id()` | Unified on `_status_task_json_payload` per `[CLI-1.2.1]` |
| `reconciliation["observed_at"]` | Converted via `format_message_id()` only when `classification in BROKER_BACKED_RECONCILIATION_OBSERVATION_CLASSIFICATIONS` and `observed_at > 0` | Unconditionally converted via `format_message_id()` | Unified on `_status_task_json_payload` per `[CLI-1.2.1]` |

### Difference Register 2: `--name` Validation Across `submission.py` and `run.py`
| Case | `apply_submit_overrides` (`submission.py:180-189`) | `_apply_explicit_run_name` (`run.py:699-730`, `--pipeline`) | `_derive_name` (`run.py:766-783`, inline) | Phase 5 Resolution |
| :--- | :--- | :--- | :--- | :--- |
| Persistent spec (`spec.persistent == True`) | Validates with `validate_endpoint_claim_name(name)` and sets `metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY]` | Validates with `validate_endpoint_claim_name` and sets `metadata[INTERNAL_RUNTIME_ENDPOINT_NAME_KEY]` | N/A | Preserve (`[PY-3]`) |
| Non-persistent spec with `_weft.*` reserved prefix | Rejects via `validate_endpoint_claim_name(name)` | Rejects via `validate_endpoint_claim_name` (`RunUsageError`) | Did not check `_weft.*` prefix | Reject `_weft.*` prefix (`INTERNAL_ENDPOINT_NAMESPACE_PREFIX`) with `RunUsageError` in both `_apply_explicit_run_name` and `_derive_name` |
| Non-persistent spec with human-readable name (e.g. `"my human name"`) | Allowed | Rejected with `RunUsageError` | Allowed | Allow in `_apply_explicit_run_name` (`[PY-3]`), rejecting empty/whitespace-only names |

### Commit Isolation, Rollback, and Independent Review Schedule
- **Per-Phase Commits & Rollback:** Each phase (Phase 1, Phase 2, Phase 3, Phase 4, and Phase 5+6) lands as its own atomic commit so any phase can be reverted independently with `git revert` without state migration (no queue name or wire schema changes). Phase 5 and Phase 6's `14-Python_API_Surfaces.md` `task tid` return-matrix edit are committed together under Promotion Strategy A/B.
- **Completed-Work Review Schedule (`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`):**
  1. Run a Tier-1 cross-family completed-work review (`skills/call-agent/SKILL.md`) after completing the durable spine phases (Phases 1–3) alongside the full SQLite and PostgreSQL gates.
  2. Run a final Tier-1 cross-family completed-work review and the Class-5 `[WT-7]` blind two-factor theory-fit account across the full change boundary (`5514f3ec..HEAD`) at Phase 6 before flipping plan status to `completed`.

---

## 4a. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

---

## 4b. Spec Baseline

- `5514f3ec` (`v0.9.121`) — governing specs and `docs/program-theory.md` at plan authoring time.
- Plan type: **implementation with spec revision** (Promotion Strategy A/B for `docs/specifications/14-Python_API_Surfaces.md` `task tid` return description paired with Phase 5's `cmd_task_tid(reverse=...)` fix; Promotion Strategy D for `cmd_system_stop` / `SystemStopResult` in `docs/specifications/14-Python_API_Surfaces.md` and `docs/specifications/11-CLI_Architecture_Crosswalk.md`).
- Promotion Baseline Identifier: `74e6221a` (commit on `main` immediately before Phase 6 spec promotion).

---

## 4c. Proposed Spec Delta

Promotion strategy: **A/B (atomic promotion with Phase 5/6 implementation)** for `task tid` return description (`full/encoded TID`), and **D (clarification of already-shipped behavior)** for the 42-verb `cmd_system_stop` (`SystemStopResult`) synchronization in `docs/specifications/14-Python_API_Surfaces.md` and `docs/specifications/11-CLI_Architecture_Crosswalk.md`.

| Spec file | Strategy | Sections touched |
|-----------|----------|------------------|
| `docs/specifications/14-Python_API_Surfaces.md` | A/B (for `task tid`) + D (for `cmd_system_stop`) | `[PY-2]` verb count (`41` -> `42`, `6 system` -> `7 system`), `### Public command types` (`41 functions` -> `42 functions`, add `SystemStopResult`), `### Deterministic return matrix` (`task tid` return description and `system` row), `## Related Plans` |
| `docs/specifications/11-CLI_Architecture_Crosswalk.md` | D — clarification | `[CLI-X1]` `system` command family row (add `weft/commands/system.py`), `## Related Plans` |

### 1. `docs/specifications/14-Python_API_Surfaces.md` `[PY-2]` — verb count paragraph (lines 176–181)

Replace the span opening with `Every canonical CLI verb has one actual implementation` and closing with `The package resolves exports lazily and caches them.`:

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```markdown
Every canonical CLI verb has one actual implementation exported from
`weft.commands`: `cmd_` plus its full CLI path, joined with underscores and
hyphenated segments normalized to underscores. The bijection covers 42 verbs: 4 root,
14 queue, 6 spec, 6 task, 5 manager, and 7 system. The root callback and
global `--version` are parser features, not verbs. The package resolves exports
lazily and caches them.
```

### 2. `docs/specifications/14-Python_API_Surfaces.md` `[PY-2]` — `### Public command types` (lines 298–309)

Replace the span opening with `` `weft.commands.__all__` exports the 41 functions `` and closing with `` `TaskMonitorRecord`, and `TaskMonitorSummary`. ``:

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```markdown
`weft.commands.__all__` exports the 42 functions and these consumption types:
`InitResult`, `RunSpecDescription`, `RunSession`, `CommandStream`,
`RunExecutionResult`, `SubmittedTaskReceipt`, `TaskSnapshot`, `TaskResult`,
`TaskEvent`, `ServiceSnapshot`, `TaskPingResult`, `TaskControlResult`,
`TaskControlFailure`,
`QueueEntry`, `QueueInfo`, `QueueWriteReceipt`, `QueueMoveResult`,
`QueueDeleteReceipt`, `QueueBroadcastReceipt`, `QueueAliasRecord`,
`EndpointResolution`, `ManagerSnapshot`, `SpecRecord`,
`SpecValidationResult`, `SpecMutationResult`, `SystemStatusSnapshot`,
`SystemStopResult`, `SystemTidyResult`, `SystemLoadResult`, `SystemDumpResult`,
`SystemPruneResult`, `BuiltinSpecRecord`, `TaskMonitorConfig`,
`TaskMonitorResult`, `TaskMonitorRecord`, and `TaskMonitorSummary`.
```

### 3. `docs/specifications/14-Python_API_Surfaces.md` `[PY-2]` — `### Deterministic return matrix` (lines 440–443)

Replace the four table rows from `| task list/status/ping/stop/kill/tid |` through `| system tidy/load/task-monitor/prune/dump/builtins |`:

Replacement (`docs/specifications/14-Python_API_Surfaces.md`):

```markdown
| task list/status/ping/stop/kill/tid | snapshots/events, ping/control result, or full/encoded TID |
| manager start/status/list | snapshot or tuple; serve blocks and returns `None` |
| manager stop | terminal snapshot, or `None` when already absent |
| system tidy/load/task-monitor/prune/dump/builtins/stop | corresponding named outcome |
```

### 4. `docs/specifications/11-CLI_Architecture_Crosswalk.md` `[CLI-X1]` — `system` row (line 34)

Replace the `| system |` table row:

Replacement (`docs/specifications/11-CLI_Architecture_Crosswalk.md`):

```markdown
| `system` | `weft/commands/system.py`, `weft/commands/builtins.py`, `weft/commands/tidy.py`, `weft/commands/dump.py`, `weft/commands/load.py`, `weft/commands/task_monitor.py`, `weft/commands/prune.py` | system-wide stop orchestration, builtin inventory, maintenance, broker-state operations, foreground task monitoring, and explicit prune dispatch |
```

---

## 5. Tasks (Multi-Phase Breakdown)

### Phase 1: Core Runtime & Reactor Mechanics — Gate Paused and Busy Work Queues (`[WT-4.1]`, `[WT-4.3]`, `[CC-2.1]`, `[CC-2.4]`, `[PL-2.3]`)

- **Files to Modify:**
  - `weft/core/tasks/multiqueue_watcher.py`
  - `weft/core/tasks/base.py`
  - `weft/core/tasks/consumer.py`
  - `weft/core/tasks/pipeline.py`
  - `tests/tasks/test_multiqueue_watcher.py`
  - `tests/tasks/test_control_channel.py`
  - `tests/tasks/test_pipeline_runtime.py`
  - `tests/system/test_manager_reactor_floor.py`
- **Implementation Steps:**
  1. **Write failing red-green TDD tests first:**
     - **Watcher predicate eviction test (`tests/tasks/test_multiqueue_watcher.py`):** Add `test_update_active_queues_evicts_queue_when_wait_activity_predicate_turns_false`: subclass or configure `MultiQueueWatcher` with two queues (`ctrl` and `work`), write to both and call `_update_active_queues()` so both are in `_active_queues`, flip the `work` queue's `_queue_counts_as_wait_activity` predicate to `False`, call `_update_active_queues()` / `_drain_queue_messages()`, and assert `work` is evicted from `_active_queues` and its message remains untouched in `work`.
     - **Paused `Consumer` custody, control responsiveness, and next-turn resume test (`tests/tasks/test_control_channel.py`):** Add `test_paused_consumer_leaves_inbox_unreserved_and_resumes_on_next_turn`:
       - Create a `Consumer`, wrap `inbox.move_one` and `reserved.move_one` to count reserve/requeue operations, send `CONTROL_PAUSE` on `ctrl_in` plus a keyed `CONTROL_PING` (`reply_to=reply_queue_name`), and run `process_once()` until the `PONG` on `reply_queue` confirms `paused=True`.
       - Write a work item to `inbox` and record its `(body, original_msg_id)` via `inbox.peek_one(with_timestamps=True)`.
       - Run `process_once()` with no control message pending, then send a keyed `CONTROL_PING` on `ctrl_in` and call `process_once()`; assert `PONG` arrives on `reply_queue` while `inbox.move_one` and `reserved.move_one` counts remain `0`, `reserved.peek_one() is None`, and `inbox.peek_one(with_timestamps=True) == (body, original_msg_id)` (unchanged custody and message ID).
       - Send `CONTROL_RESUME` on `ctrl_in`, drive the `RESUME` turn (`process_once()`) and then the work turn (`process_once()`) without advancing any clock or sleeping, and assert the work item is executed and published to `outbox`.
     - **Busy `Consumer` custody, control responsiveness, and immediate next-turn progress test (`tests/tasks/test_control_channel.py`):** Add `test_busy_consumer_leaves_second_item_in_inbox_and_processes_it_on_next_turn_after_completion`:
       - Write two work items to a persistent `Consumer`'s `inbox` and start item 1 so `task._active_work_in_flight` is `True` (with item 1 in `reserved` and item 2 still in `inbox` with its `original_msg_id`).
       - Send a keyed `CONTROL_PING` (`reply_to=reply_queue_name`), call `task.process_once()`, and assert `PONG` is emitted while item 2 remains in `inbox` with `original_msg_id` (`0` additional `inbox.move_one` or `reserved.move_one` calls).
       - Complete item 1 (`_handle_active_work_result`) and call `task.process_once()` once without advancing the clock; assert item 2 is immediately reserved and started without waiting for `_next_inactive_probe_at`.
     - **Paused `PipelineEdgeTask` custody and next-turn resume test (`tests/tasks/test_pipeline_runtime.py`):** Add `test_paused_pipeline_edge_leaves_inbox_unreserved_and_forwards_on_turn_after_resume`:
       - Write a payload to a `PipelineEdgeTask`'s `inbox`, record `(body, original_msg_id)`, pause the `PipelineEdgeTask`, and run `process_once()` across multiple turns.
       - Assert `inbox.move_one` / `reserved.move_one` counts while paused are `0`, `reserved.peek_one() is None`, and `inbox.peek_one(with_timestamps=True) == (body, original_msg_id)`.
       - Send `CONTROL_RESUME` on `ctrl_in`, drive the `RESUME` turn (`process_once()`) and then the work turn (`process_once()`) without advancing any clock, and assert the payload is handed off to `target_queue` on that second turn.
     - **Real-process paused `Consumer` CPU quiet probe (`tests/system/test_manager_reactor_floor.py`):** Add `test_paused_consumer_does_not_spin_with_queued_input` alongside `test_manager_does_not_spin_while_supervising_running_task`:
       - Spawn a real persistent `Consumer` process via `WeftTestHarness`, pause it (`CONTROL_PAUSE`) and confirm `paused` state, enqueue a work item to `T{tid}.inbox`, measure `psutil.Process(consumer_pid).cpu_times()` across a `3.0s` wall-clock window while paused with queued input, and assert `cpu_seconds < 0.25` (versus ~`3.0s` / 100% core spin before the fix). Then resume the task and verify it processes the queued message cleanly.
  2. **Update `MultiQueueWatcher._update_active_queues` (`weft/core/tasks/multiqueue_watcher.py:1392-1396`):**
     ```python
     still_active: list[str] = [
         name
         for name in self._active_queues
         if self._queue_counts_as_wait_activity(self._queues[name])
         and self._queue_has_pending(self._queues[name].queue)
     ]
     ```
  3. **Update `BaseTask._handle_control_command` (`weft/core/tasks/base.py:1765-1772`):**
     - When `command == CONTROL_RESUME` transitions `self._paused` from `True` to `False`, set `self._pending_messages_precheck_confirmed = True` and call `self._strategy.notify_activity()`.
  4. **Update `Consumer` (`weft/core/tasks/consumer.py:165-170, 585-588`):**
     - In `Consumer._queue_counts_as_wait_activity(self, config: QueueRuntimeConfig) -> bool`:
       ```python
       if getattr(self, "_active_work_in_flight", False) or getattr(self, "_paused", False):
           return config.name == self._queue_names["ctrl_in"]
       return super()._queue_counts_as_wait_activity(config)
       ```
     - In the `finally:` block of `Consumer._handle_active_work_result` (`weft/core/tasks/consumer.py:585-588`), when `self._active_work_in_flight` is reset to `False`, if `not self.should_stop and not self._paused`, set `self._pending_messages_precheck_confirmed = True` and call `self._strategy.notify_activity()`. Keep the existing `if self._paused:` and `if self._active_work_in_flight:` requeue blocks in `_handle_work_message` as defensive same-pass guards.
  5. **Update `PipelineEdgeTask` (`weft/core/tasks/pipeline.py:157`):**
     - Override `_queue_counts_as_wait_activity(self, config: QueueRuntimeConfig) -> bool` on `PipelineEdgeTask` to return `config.name == self._queue_names["ctrl_in"]` when `getattr(self, "_paused", False)` is `True`, and `super()._queue_counts_as_wait_activity(config)` otherwise. Keep `_requeue_reserved_message` in `_handle_work_message` as the same-pass guard.
- **Stop Gates:**
  - Do NOT call `add_queue()` or `remove_queue()` on `BaseTask`.
  - Do NOT change `HeartbeatTask._queue_counts_as_wait_activity` (`[MF-3.2]` requires invalid `inbox` writes to be rejected even while paused).
- **Phase 1 Verification Gates (including `Manager` reach & wake latency):**
  ```bash
  ./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_control_channel.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_heartbeat.py tests/core/test_manager.py tests/core/test_admission_recovery.py tests/core/test_manager_family_maintenance.py tests/system/test_manager_reactor_floor.py -q
  ./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
  ```

---

### Phase 2: Observability, Liveness & State Projections — Endpoint Terminal Precedence, Heartbeat Version Check, Pipeline Registry Append ID, and `TaskMonitor` Comments (`[WT-2]`, `[WT-3]`, `[WT-4.1]`, `[MF-3.1]`, `[MF-3.2]`, `[PL-2.2]`, `[OBS.13.7]`)

- **Files to Modify:**
  - `weft/core/endpoints.py`
  - `weft/core/tasks/heartbeat.py`
  - `weft/core/tasks/pipeline.py`
  - `weft/core/monitor/task_monitor.py`
  - `tests/tasks/test_task_endpoints.py`
  - `tests/tasks/test_heartbeat.py`
  - `tests/tasks/test_pipeline_runtime.py`
- **Implementation Steps:**
  1. **Write failing red-green TDD tests first:**
     - In `tests/tasks/test_task_endpoints.py`, add `test_endpoint_resolution_rejects_terminal_tid_state_without_runtime_handle_when_log_compacted`: register an active endpoint row for `tid` in `weft.state.endpoints`, write a retained terminal snapshot without a runtime handle (`{"full": tid, "short": encode_tid(tid), "terminal": True, "runtime_handle": None, "projection": {"status": "completed"}}`) to `weft.state.tasks.<tid>` with `weft.log.tasks` empty, and assert `list_resolved_endpoints(ctx) == []` and `resolve_endpoint(ctx, name) is None`.
     - In `tests/tasks/test_heartbeat.py`, add `test_heartbeat_endpoint_registry_version_uses_newest_row_without_full_scan`: populate `weft.state.endpoints` with multiple rows, wrap the queue to fail if `peek_generator` is called inside `_endpoint_registry_version`, and assert `_endpoint_registry_version()` returns the newest row's integer timestamp via `peek_many(limit=1, order="newest", with_timestamps=True)` and `_service_ownership()` calls `_endpoint_registry_version()` at most twice (before and after `resolve_endpoint`) on a cache miss and once on a cache hit.
     - In `tests/tasks/test_pipeline_runtime.py`, add `test_pipeline_registry_write_retains_append_id_without_rescanning_queue` (modeled on `test_endpoint_claim_retains_append_id_across_interleaved_peer_append` in `tests/tasks/test_task_endpoints.py:526-573`): intercept `WEFT_PIPELINES_STATE_QUEUE` on a `PipelineTask`, fail if `peek_generator` is called during `_write_pipeline_registry_record()`, interleave a peer write inside `write()`, and assert `_write_pipeline_registry_record()` stores the exact `int` returned by `queue.write(...)` in `self._registry_message_id` and `_delete_pipeline_registry_record()` deletes only that exact `message_id`.
  2. **Update `weft/core/endpoints.py::_record_owner_is_live` (`lines 280-307`):**
     - Immediately after `if mapping is None: return False`, add:
       ```python
       if mapping.get("terminal") is True:
           return False
       ```
     - Keep `if handle_payload is None: return True` and the `handle_has_live_host_process` / `runtime_liveness_from_registered_probe` checks below it for non-terminal snapshots.
  3. **Update `HeartbeatTask._service_ownership` and `_endpoint_registry_version` (`weft/core/tasks/heartbeat.py:630-678`):**
     - In `_endpoint_registry_version(self) -> int | None`, unpack SimpleBroker's documented `(body, timestamp)` tuple directly:
       ```python
       queue = self._queue(WEFT_ENDPOINTS_REGISTRY_QUEUE)
       try:
           rows = queue.peek_many(limit=1, order="newest", with_timestamps=True)
       except (BrokerError, OSError, RuntimeError):
           return None
       if not rows:
           return None
       _body, timestamp = rows[0]
       return int(timestamp)
       ```
     - In `_service_ownership(self)`: read `version_before = self._endpoint_registry_version()` once at the top; if `not self._ownership_check_required and version_before is not None and version_before == self._last_endpoint_registry_version`, return `self._cached_service_ownership`; otherwise reuse `version_before` without calling `self._endpoint_registry_version()` a second time before `resolve_endpoint(...)`.
  4. **Update `PipelineTask._write_pipeline_registry_record` (`weft/core/tasks/pipeline.py:793-796`):**
     - Assign the integer message ID returned by `queue.write(...)` directly:
       ```python
       queue = self._queue(WEFT_PIPELINES_STATE_QUEUE)
       self._registry_message_id = queue.write(json.dumps(payload, ensure_ascii=False))
       ```
     - Keep `_latest_pipeline_registry_message_id()` only for the deletion fallback in `_delete_pipeline_registry_record()` when `self._registry_message_id is None`.
  5. **Update `TaskMonitor` comments/docstrings (`weft/core/monitor/task_monitor.py:2154-2160, 2188-2194, 2807-2865, 3275-3279`):**
     - Update the comments and docstrings on `_emit_monitor_store_summaries`, `_active_runtime_tids`, `_destruction_protected_runtime_tids`, `_nonterminal_mapping_row_tids`, and `_run_reserved_cleanup_slice` so they describe the `[WT-2]` / `[OBS.13.7]` row-presence handoff from `LivenessMonitor`.
- **Phase 2 Verification Gates:**
  ```bash
  ./.venv/bin/python -m pytest tests/tasks/test_task_endpoints.py tests/core/test_heartbeat_helpers.py tests/tasks/test_heartbeat.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_task_monitor.py -q
  ./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
  ```

---

### Phase 3: Manager Supervision & Service Candidate Ordering — Non-Blocking Reactor `wrapper_lost` Commit and Strict Numeric Service TID Ordering (`[WT-4.1]`, `[MA-1]`, `[MA-1.6a]`, `[REDUCER.1]`)

- **Files to Modify:**
  - `weft/core/manager.py`
  - `weft/core/manager_services.py`
  - `tests/core/test_manager.py`
  - `tests/core/test_manager_services.py`
- **Implementation Steps:**
  1. **Write failing red-green TDD tests first:**
     - In `tests/core/test_manager.py`, add `test_cleanup_children_wrapper_lost_write_failure_schedules_reactor_retry_without_sleeping`: inject a `BrokerError` on writing to `child_ctrl_out` and monkeypatch `weft.core.manager.time.sleep` to fail if called during the reactor turn; run `manager._cleanup_children()` (`deadline=None`) after `child.terminal_proof_deadline` grace has elapsed, and assert:
       1. `time.sleep` was **not** called and `_cleanup_children()` attempted the write once;
       2. `child.terminal_retry_after_ns > 0` was armed and `manager.next_wait_timeout()` publishes that retry deadline;
       3. Reset `child.terminal_retry_after_ns = 0`, restore `time.sleep` recording, call `manager._cleanup_children(deadline=time.monotonic() + 1.0)` (shutdown drain with `deadline is not None`), and assert bounded retries (`TERMINAL_EVENT_WRITE_RETRIES = 3`) are attempted.
     - In `tests/core/test_manager.py`, add `test_tracked_service_terminal_proof_visible_uses_task_state_snapshot_before_global_log`: write a terminal snapshot (`{"full": tid, "short": encode_tid(tid), "terminal": True}`) to `weft.state.tasks.<tid>`, monkeypatch `iter_queue_json_entries` in `weft.core.manager` to fail if `WEFT_GLOBAL_LOG_QUEUE` is scanned, and assert `manager._tracked_service_terminal_proof_visible(tid, child) is True`.
     - In `tests/core/test_manager_services.py`, add `test_service_candidate_selection_and_summary_order_by_int_tid`: pass `ServiceCandidate` items with valid decimal TIDs spanning different decimal widths (`"999999999999999999"` and `"1844674407370955161"`, where lexicographic order would pick `"1844674407370955161"` first while numeric order picks `"999999999999999999"` first), and assert the public `select_canonical_live_candidate` and `summarize_service_candidates` (`candidates` and `uncertain`) order them numerically (`"999999999999999999"` before `"1844674407370955161"`).
  2. **Update `Manager._commit_wrapper_lost_envelope` (`weft/core/manager.py:4994-5042`):**
     - Set `attempts = 1 if deadline is None else max(1, TERMINAL_EVENT_WRITE_RETRIES)`. When `deadline is None`, a failed write logs at `logger.warning` and returns `False` immediately without sleeping; when `deadline is not None`, preserve the bounded retry loop capped by `self._remaining_deadline(deadline)`.
  3. **Update `Manager._tracked_service_terminal_proof_visible` (`weft/core/manager.py:7161-7188`):**
     - After `if self._child_terminal_proof_visible(tid, child) is True: return True`, read `snapshot = read_task_state_snapshot(self._context, tid)` inside a `try ... except (BrokerError, OSError, RuntimeError):` block; if `snapshot is not None and snapshot[1].get("terminal") is True`, return `True` without scanning `WEFT_GLOBAL_LOG_QUEUE`.
  4. **Update `weft/core/manager_services.py` (`lines 183, 226, 230, 391-395`):**
     - Define strict numeric TID key conversion for validated candidates:
       ```python
       def _candidate_tid_key(candidate: ServiceCandidate) -> int:
           return int(candidate.tid)
       ```
     - Use `_candidate_tid_key` in `select_canonical_live_candidate` (`min(live, key=_candidate_tid_key)`), in `summarize_service_candidates` (`sorted(..., key=_candidate_tid_key)` at lines 226 and 230), and as the tie-breaker in `_candidate_timestamp_key(candidate)` at lines 391–395.
- **Stop Gates:**
  - Do NOT change `Manager._seed_child_inbox` (`weft/core/manager.py:2408-2436`).
- **Phase 3 Verification Gates (Spine Milestone Gate):**
  ```bash
  ./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py tests/core/test_admission_recovery.py tests/core/test_manager_burst_bookkeeping.py -q
  ./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
  ./.venv/bin/python -m pytest -q
  ./bin/pytest-pg --all tests
  ```
  - Run the intermediate Tier-1 cross-family completed-work review (`skills/call-agent/SKILL.md`) over Phases 1–3 after the spine milestone gates pass.

---

### Phase 4: Agent Runtime & Runner Extensions — `LLMBackend` Bundle Root Propagation and `DockerProviderCLIRunner` Runtime Requirement Enforcement (`[AR-2.2]`, `[AR-5]`)

- **Files to Modify:**
  - `weft/core/agents/backends/llm.py`
  - `extensions/weft_docker/weft_docker/agent_runner.py`
  - `tests/core/test_agent_runtime.py`
  - `extensions/weft_docker/tests/test_agent_runner.py`
- **Implementation Steps:**
  1. **Write failing red-green TDD tests first:**
     - In `tests/core/test_agent_runtime.py`, add a small bundle fixture helper and three focused test functions:
       1. `test_llm_backend_resolves_bundle_plugin_and_output_schema_in_one_shot`: create `bundle_a` with distinct `bundle_plugin_mod.py` and `bundle_schema_mod.py`, and verify `LLMBackend.validate(..., bundle_root=str(bundle_a))` and `LLMBackend.execute(..., bundle_root=str(bundle_a))` resolve `runtime_config={"plugin_modules": ["bundle_plugin_mod"]}` and `output_schema="bundle_schema_mod:OutputSchema"`.
       2. `test_llm_backend_session_propagates_bundle_root_for_tools_and_schema`: create `bundle_a` with `bundle_tool_mod.py` and `bundle_schema_mod.py`, start a session via `LLMBackend.start_session(..., bundle_root=str(bundle_a))`, call `session.execute(work_item)`, and verify both the bundle tool and bundle output schema resolve.
       3. `test_llm_backend_isolates_colliding_bundle_module_names_across_bundles`: create `bundle_a` and `bundle_b` defining colliding module names (`bundle_schema_mod.py` and `bundle_tool_mod.py`) with distinct schema fields and tool outputs, execute `bundle_a` then `bundle_b` in the same process, and assert each bundle resolves its own module via `_weft_bundle_<digest>` isolation.
     - In `extensions/weft_docker/tests/test_agent_runner.py`, add `test_docker_provider_cli_runner_rejects_missing_required_provider_runtime_requirements`: configure `DockerProviderCLIRunner` with valid (or empty) `work_item_mounts`, return a `ProviderContainerRuntimeResolution` with `has_missing_required == True` from `resolve_provider_container_runtime`, and assert `runner.run_with_hooks(...)` raises `ValueError("Docker-backed provider_cli agent runtime requirements are not satisfied...")` before `ensure_agent_image` or `docker_client` is called.
  2. **Update `weft/core/agents/backends/llm.py`:**
     - Import `import_module_ref` and `import_ref` from `weft.core.imports`.
     - Update `_register_plugin_modules(plugin_modules: Any, *, bundle_root: str | None = None) -> None` to load each `module_name` via `import_module_ref(module_name, bundle_root=bundle_root)`.
     - Update `_resolve_output_schema(schema: dict[str, Any] | str | None, *, bundle_root: str | None = None) -> Any` to resolve string refs via `import_ref(schema, bundle_root=bundle_root)`.
     - Thread `bundle_root: str | None = None` through `_resolve_model`, `_execute_with_conversation`, `execute`, `validate`, and `start_session`.
     - Store `self._bundle_root = bundle_root` on `LLMBackendSession.__init__` and pass `bundle_root=self._bundle_root` to both `resolve_agent_tools(..., bundle_root=self._bundle_root)` and `self._backend._execute_with_conversation(..., bundle_root=self._bundle_root)` in `LLMBackendSession.execute`.
  3. **Update `extensions/weft_docker/weft_docker/agent_runner.py`:**
     - Import `format_provider_container_runtime_diagnostics` from `weft.core.agents.provider_cli.container_runtime`.
     - In `DockerProviderCLIRunner.run_with_hooks` (`after line 131`, right after `runtime_requirements = self._resolve_container_runtime(...)`), enforce the missing-requirement check matching `DockerRunnerPlugin._validate_agent_taskspec` (`extensions/weft_docker/weft_docker/plugin.py:642-649`):
       ```python
       if runtime_requirements.has_missing_required:
           detail = format_provider_container_runtime_diagnostics(
               runtime_requirements.diagnostics
           )
           raise ValueError(
               "Docker-backed provider_cli agent runtime requirements are not "
               f"satisfied for provider '{provider.name}': {detail}"
           )
       ```
- **Phase 4 Verification Gates:**
  ```bash
  ./.venv/bin/python -m pytest tests/core/test_agent_runtime.py tests/core/test_agent_validation.py extensions/weft_docker/tests/test_agent_runner.py extensions/weft_docker/tests/test_docker_plugin.py -q
  ```

---

### Phase 5: CLI, Commands, Client & `weft_django` Surfaces — `cmd_task_tid --reverse`, Pipeline Non-Persistent `--name`, Task Snapshot JSON Clocks, and Django Client Parity (`[PY-2]`, `[PY-3]`, `[CLI-1.2.1]`, `[DJ-10.1]`, `[DJ-13.2]`)

- **Files to Modify:**
  - `weft/commands/tasks.py`
  - `weft/commands/run.py`
  - `weft/cli/app.py`
  - `integrations/weft_django/weft_django/views.py`
  - `integrations/weft_django/weft_django/client.py`
  - `tests/commands/test_task_commands.py`
  - `tests/commands/test_run.py`
  - `tests/cli/test_cli_list_task.py`
  - `tests/cli/test_cli_status_rendering.py`
  - `integrations/weft_django/tests/test_weft_django.py`
- **Implementation Steps:**
  1. **Write failing red-green TDD tests first:**
     - In `tests/commands/test_task_commands.py`, add `test_cmd_task_tid_reverse_returns_encoded_short_tid`: assert `cmd_task_tid(reverse="1844674407370955161", context=ctx.root) == "1K6CSK6CSK6CS"` (literal expected value) and verify `weft task tid --reverse 1844674407370955161` on the CLI outputs `"1K6CSK6CSK6CS"`.
     - In `tests/commands/test_run.py`, add `test_apply_explicit_run_name_allows_human_readable_non_persistent_pipeline_name_and_rejects_reserved_prefix`: verify `_apply_explicit_run_name(non_persistent_spec, "my human name")` succeeds with `spec.name == "my human name"` and no `INTERNAL_RUNTIME_ENDPOINT_NAME_KEY` in `metadata`; verify `_apply_explicit_run_name(persistent_spec, "my human name")` raises `RunUsageError`; and verify both `_apply_explicit_run_name(non_persistent_spec, "_weft.heartbeat")` and `_derive_name(name="_weft.heartbeat", ...)` raise `RunUsageError`.
     - In `tests/cli/test_cli_status_rendering.py` (or `tests/cli/test_cli_list_task.py`), add `test_task_list_and_task_status_json_use_status_task_json_payload_for_wall_clock_timestamps`: construct a `TaskSnapshot` with `event="ctrl_out_terminal"`, `last_timestamp=1770000000000000000`, and `reconciliation={"classification": "stale_created", "observed_at": 1770000000000000000}`, verify `_status_task_json_payload(snap)` keeps both timestamps as `int`, and verify `task_list` and `task_status` `--json` output use `_status_task_json_payload`.
     - In `integrations/weft_django/tests/test_weft_django.py`, add `test_deferred_submission_delegates_snapshot_and_terminal_snapshot_once_bound` and `test_task_detail_view_reuses_request_owned_client`.
  2. **Update `cmd_task_tid` (`weft/commands/tasks.py:2321-2327`) and `task_tid` (`weft/cli/app.py:1880`):**
     - In `weft/commands/tasks.py:2321-2327`, return `encode_tid(candidate)` directly when `reverse is not None`.
     - In `weft/cli/app.py:1880`, change `typer.echo(encode_tid(result) if reverse is not None else result)` to `typer.echo(result)`.
  3. **Update `_apply_explicit_run_name` and `_derive_name` (`weft/commands/run.py:699-783`):**
     - In `_apply_explicit_run_name(taskspec: TaskSpec, explicit_name: str) -> TaskSpec`: strip `candidate = explicit_name.strip()`; if `not candidate`, raise `RunUsageError("Task name must not be empty", param_hint="--name")`; check `is_persistent = bool(getattr(taskspec.spec, "persistent", False))`; call `validate_endpoint_claim_name(candidate)` only when `is_persistent or candidate.startswith(INTERNAL_ENDPOINT_NAMESPACE_PREFIX)` (matching `apply_submit_overrides` in `weft/commands/submission.py:180-189`), translating `ValueError` to `RunUsageError(str(exc), param_hint="--name")`.
     - In `_derive_name` (`weft/commands/run.py:766-783`): when `name is not None`, strip `candidate = name.strip()`; if `not candidate` or `candidate.startswith(INTERNAL_ENDPOINT_NAMESPACE_PREFIX)`, validate with `validate_endpoint_claim_name(candidate)` and raise `RunUsageError(str(exc), param_hint="--name")` on `ValueError`.
  4. **Remove `_task_snapshot_payload` and call `_status_task_json_payload` directly (`weft/cli/app.py:1545, 1560-1573, 1737`):**
     - Update `task_list` (`line 1545`) and `task_status` (`line 1737`) to call `_status_task_json_payload(snapshot)` directly, and delete `_task_snapshot_payload` (`lines 1560-1573`).
  5. **Update `integrations/weft_django/weft_django/views.py` and `integrations/weft_django/weft_django/client.py`:**
     - Add `snapshot(self) -> TaskSnapshot | None` and `terminal_snapshot(self, timeout: float = 0.0) -> TaskTerminalSnapshot` to `WeftDeferredSubmission` (`client.py:76-115`).
     - In `views.py:11, 22-30`, import `_current_client` from `weft_django.client` and use `_current_client().core_client.task(tid)` in `_task_snapshot_or_404(tid)`.
- **Phase 5 Verification Gates:**
  ```bash
  ./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/commands/test_run.py tests/commands/test_submission.py tests/cli/test_cli_list_task.py tests/cli/test_cli_status_rendering.py integrations/weft_django/tests/test_weft_django.py integrations/weft_django/tests/test_lifecycle.py -q
  ```

---

### Phase 6: Specification, Theory Audit, Two-Factor Fit Test & Governance Closeout (`[DOM-15]`, `[WT-0]`, `[WT-7]`, `[CLI-X1]`, `[PY-2]`)

- **Files to Modify:**
  - `CHANGELOG.md`
  - `docs/specifications/14-Python_API_Surfaces.md`
  - `docs/specifications/11-CLI_Architecture_Crosswalk.md`
  - `docs/plans/README.md`
  - `docs/plans/2026-11-06-deep-dive-review-remediation-plan.md`
- **Implementation Steps:**
  1. Apply the four `## 4c. Proposed Spec Delta` replacements to `docs/specifications/14-Python_API_Surfaces.md` and `docs/specifications/11-CLI_Architecture_Crosswalk.md`, add plan backlinks under `## Related Plans` in both specs, record the `[QUEUE.8]` `weft/core/tasks/multiqueue_watcher.py` copy-boundary notice and user-facing changes in `CHANGELOG.md`, and record the Promotion Baseline Identifier in `## 4b. Spec Baseline`.
  2. Complete the `[WT-0]` **theory-versus-code audit table refresh** in `## 6a. Theory-Versus-Code Audit Refresh` below with final post-implementation probe measurements, carrying unchanged rows forward by reference.
  3. Run the Class-5 `[WT-7]` **two-factor theory-fit test** across the full change boundary (`git diff 5514f3ec -- weft extensions integrations tests docs/specifications CHANGELOG.md ':(exclude)tests/system/test_pytest_pg_script.py'`):
     - Write and seal the author's five-part fit account first (verdict; sections the change rests on; what it refuses and the `[ALT-WT-*]` entry that refuses it; what it predicts that could prove it wrong; sentence that fits worst).
     - Invoke a cross-family agent (`codex` via `skills/call-agent/SKILL.md`, `codex exec --skip-git-repo-check -s read-only -C <scratch_dir_outside_repo>`) from an empty scratch directory **outside** the repository so it cannot read `AGENTS.md`, plan files, review logs, or ambient workspace context, supplying `docs/program-theory.md` and the code/spec diff (`git diff 5514f3ec -- weft extensions integrations tests docs/specifications CHANGELOG.md ':(exclude)tests/system/test_pytest_pg_script.py'`) inline in the prompt, and record its five-part fit account and any divergence classifications in `## 8. Independent Review Loop`.
  4. Run the full final gate suite in `## 7. Verification and Gates` and flip this plan's status to `completed` in both `docs/plans/2026-11-06-deep-dive-review-remediation-plan.md` and `docs/plans/README.md`.

---

## 6. Testing Plan

- **Harnesses & Fixtures (`docs/specifications/08-Testing_Strategy.md`):**
  - Use `broker_env`, `workdir`, and `WeftTestHarness` with real SimpleBroker queues and real processes for `Consumer`, `PipelineEdgeTask`, `PipelineTask`, `HeartbeatTask`, `Manager`, and `endpoints` tests.
  - Never mock `MultiQueueWatcher`, `Queue`, `BrokerSession`, or `TaskSpec` state transitions in Phase 1–3 tests.
  - Reserve private `_active_queues` assertions for `tests/tasks/test_multiqueue_watcher.py`; test `Consumer` and `PipelineEdgeTask` through observable custody (`inbox.peek_one(with_timestamps=True)`, `reserved.peek_one()`), move counts (`inbox.move_one`, `reserved.move_one`), real-process CPU usage (`psutil.Process(pid).cpu_times()`), control `PING`/`PONG`, and next-turn progress after `CONTROL_RESUME` or completion.
  - Every phase begins by running its new regression test(s) against the unpatched code and recording the exact failure assertion in the execution log before editing the production module.

---

## 6a. Theory-Versus-Code Audit Refresh (`[WT-0]`, `[WT-7]`)

Unchanged claims carry forward by reference to `docs/plans/2026-11-02-program-theory-reconciliation-plan.md`, `docs/plans/2026-11-05-program-theory-behaviour-and-refusal-revision-plan.md`, and `docs/plans/2026-11-05-manager-wake-idle-clock-and-settlement-repairs-plan.md`. Touched rows audited at baseline `5514f3ec` and refreshed at closeout:

| Touched claim | Contract | Evidence at baseline `5514f3ec` | Baseline verdict | Closeout verdict & probe (`HEAD`) |
|---------------|----------|----------------------------------|------------------|-----------------------------------|
| `[WT-4.1]` Wake predicates over queued work are true iff actionable work is present; gate watched queues rather than reserve-and-requeue loops | `[CC-2.1]`, `[CC-2.4]`, `[PL-2.3]`, `[MA-1.8]` | `Consumer._queue_counts_as_wait_activity` ignored `_paused`; `PipelineEdgeTask` did not override `_queue_counts_as_wait_activity`; `MultiQueueWatcher._update_active_queues` kept `still_active` queues even when `_queue_counts_as_wait_activity` turned `False`. Paused `Consumer` spun at 3,026 reserve+requeue cycles/sec (`2.30s` CPU in `3.0s`). | drifted (code) | **aligned** (`62a4504b`, `02b7509d`: `MultiQueueWatcher._update_active_queues` evicts queues whose wait-activity predicate turns `False`; `Consumer` and `PipelineEdgeTask` mark `_pending_messages_prechecked` and call `_strategy.notify_activity()` when reopening an evicted queue, and `Manager._clear_admission_retry` calls `_mark_queue_active(queue_name)` on reopened pending spawn lanes; verified by `test_update_active_queues_evicts_queue_when_wait_activity_predicate_turns_false`, `test_paused_consumer_leaves_inbox_unreserved_and_resumes_on_next_turn`, `test_busy_consumer_leaves_second_item_in_inbox_and_processes_it_on_next_turn_after_completion`, `test_paused_pipeline_edge_leaves_inbox_unreserved_and_forwards_on_turn_after_resume`, and `test_clear_admission_retry_marks_evicted_spawn_lane_for_immediate_reprobe`) |
| `[WT-4.3]` A quiet task costs nothing an operator can see (supervising Manager and paused tasks) | `[CC-2.2.1]`, `[CC-2.4]`, `[MA-1]` item 5 | Supervising Manager CPU at `5514f3ec`: `0.0431s` in `3.50s` (`1.23%`); idle Manager CPU: `0.0361s` in `3.00s` (`1.20%`); idle Manager wake-to-claim latency: `20.22 ms` median. However, a paused `Consumer` with 1 queued `inbox` item burned `2.30s` CPU in `3.0s` spinning `inbox <-> reserved`. | drifted for paused `Consumer` / `PipelineEdgeTask`; aligned for supervising/idle `Manager` | **aligned** (`62a4504b`: `test_paused_consumer_does_not_spin_with_queued_input` passes its `< 0.25s` CPU ceiling over a `3.0s` measurement window while paused with queued `inbox` work and responsive to control messages, and `test_manager_does_not_spin_while_user_task_runs` passes its `< 1.5s` CPU ceiling over a `3.0s` measurement window in `tests/system/test_manager_reactor_floor.py`) |
| `[WT-4.1]` Keep an identity you already have (retain append IDs; avoid full-queue scans) | `[MF-3.2]`, `[PL-2.2]` | `PipelineTask._write_pipeline_registry_record` discarded the `int` returned by `queue.write(...)` and rescanned `weft.state.pipelines`; `HeartbeatTask._endpoint_registry_version` scanned all rows of `weft.state.endpoints`. | drifted in `PipelineTask` and `HeartbeatTask` | **aligned** (`5694b40f`: `self._registry_message_id = queue.write(...)` with `0` `peek_generator` scans in `test_pipeline_registry_write_retains_append_id_without_rescanning_queue`; `peek_many(limit=1, order="newest", with_timestamps=True)` in `test_heartbeat_endpoint_registry_version_uses_newest_row_without_full_scan`) |
| `[WT-3]` / `[MF-3.1]` Endpoint liveness honors terminal state precedence before runtime-handle fallback | `[MF-3.1]`, `[OBS.6]`, `[LIVENESS.R1]` | `weft/core/endpoints.py::_record_owner_is_live` returned `True` for retained terminal `weft.state.tasks.<tid>` snapshots (`terminal: True`, `runtime_handle: None`) after `TaskMonitor` compacted `weft.log.tasks`. | drifted (code) | **aligned** (`5694b40f`: `if mapping.get("terminal") is True: return False` before `if handle_payload is None: return True`, verified by `test_endpoint_resolution_rejects_terminal_tid_state_without_runtime_handle_when_log_compacted`) |
| `[WT-2]` / `[OBS.13.7]` Row-presence handoff between `LivenessMonitor` and `TaskMonitor` | `[OBS.13.7]` | `TaskMonitor` code already used row-presence on `weft.state.tasks.<tid>`, but docstrings/comments still described host-PID and undecidable runtime-handle checks. | aligned in code; stale comments | **aligned** (`5694b40f`: comments in `weft/core/monitor/task_monitor.py` synchronized with `[OBS.13.7]` row-presence rule) |

---

## 7. Verification and Gates

### Per-Phase Gate Commands
```bash
# Phase 1 (includes Manager reactor-floor and admission-recovery reach suites)
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_control_channel.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_heartbeat.py tests/core/test_manager.py tests/core/test_admission_recovery.py tests/core/test_manager_family_maintenance.py tests/system/test_manager_reactor_floor.py -q
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml

# Phase 2
./.venv/bin/python -m pytest tests/tasks/test_task_endpoints.py tests/core/test_heartbeat_helpers.py tests/tasks/test_heartbeat.py tests/tasks/test_pipeline_runtime.py tests/tasks/test_task_monitor.py -q
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml

# Phase 3 (Spine Milestone — includes canonical mypy, full SQLite fast suite, and full PostgreSQL suite)
./.venv/bin/python -m pytest tests/core/test_manager.py tests/core/test_manager_services.py tests/core/test_service_convergence.py tests/core/test_admission_recovery.py tests/core/test_manager_burst_bookkeeping.py -q
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python -m pytest -q
./bin/pytest-pg --all tests

# Phase 4
./.venv/bin/python -m pytest tests/core/test_agent_runtime.py tests/core/test_agent_validation.py extensions/weft_docker/tests/test_agent_runner.py extensions/weft_docker/tests/test_docker_plugin.py -q

# Phase 5
./.venv/bin/python -m pytest tests/commands/test_task_commands.py tests/commands/test_run.py tests/commands/test_submission.py tests/cli/test_cli_list_task.py tests/cli/test_cli_status_rendering.py integrations/weft_django/tests/test_weft_django.py integrations/weft_django/tests/test_lifecycle.py -q
```

### Final Gate Commands (Mandatory Before Closing Plan)
```bash
./.venv/bin/ruff check .
./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/python bin/ruff_suppression_index.py --check
./.venv/bin/python bin/check-doc-paths
./.venv/bin/python bin/check-dom15-fixtures
./.venv/bin/python bin/coalesce-check
./.venv/bin/python -m pytest tests/specs/ tests/architecture/ -q
./.venv/bin/python -m pytest -q
./.venv/bin/python -m pytest -m "" -q
./.venv/bin/python -m pytest -m "" integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests -q
./bin/pytest-pg --all tests
./bin/pytest-pg --all integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests
```

### Recorded Final Gate Results (`2026-11-06`)

- `./.venv/bin/ruff check .`: `All checks passed!`
- `./.venv/bin/ruff format --check .`: `645 files already formatted`
- `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`: `Success: no issues found in 496 source files`
- `./.venv/bin/python bin/ruff_suppression_index.py --check`: `OK`
- `./.venv/bin/python bin/check-doc-paths`: `OK`
- `./.venv/bin/python bin/check-dom15-fixtures`: `OK`
- `./.venv/bin/python bin/coalesce-check`: `OK` (all cues resolve locally; harvest candidates `3 < 5`, unindexed plan files `0`)
- `./.venv/bin/python -m pytest tests/specs/ tests/architecture/ -q`: `230 passed`
- `./.venv/bin/python -m pytest -q`: passed (`0` failures)
- `./.venv/bin/python -m pytest -m "" -q`: passed (`0` failures)
- `./.venv/bin/python -m pytest -m "" integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests -q`: `390 passed, 3 skipped`
- `./bin/pytest-pg --all tests`: `6,748 passed, 40 skipped in 434.38s`
- `./bin/pytest-pg --all integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests`: `389 passed, 4 skipped in 15.50s`

---

## 8. Independent Review Loop

- **Author family:** Google (`agy` / Gemini)
- **Reviewer family:** OpenAI (`codex`, Tier-1 cross-family review via `skills/call-agent/SKILL.md`) and Repository Owner (two review passes)

### Round 1 (`2026-11-06`, `codex`, thread `01a1b191-b410-77e1-b78c-40a841ebda94`, baseline `5514f3ec`)
- **Verdict:** `BLOCKED` (7 findings: `F1`–`F7`)
- **Findings and Dispositions:**

| ID | Severity | Location | Summary of Reviewer Finding | Disposition | Author Action / Pointer to Final Requirement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **F1** | P1 | Phase 4 | `_bundle_import_path` does not exist in `weft/core/agents/tools.py`; the shared resolver is `import_module_ref` and `import_ref` in `weft/core/imports.py:20-54`. | **Accept (`fixed in plan`)** | Phase 4 Step 2 uses `import_module_ref` and `import_ref` from `weft.core.imports`, and Phase 4 Step 1 tests cross-bundle collision isolation. |
| **F2** | P1 | Phase 2 Step 2; Phase 3 Steps 3–4 | `task_state_terminal_evidence` takes 3 args and requires `terminal` truthy; Phase 2 should check `if mapping.get("terminal") is True: return False`, and Phase 3 should target `ServiceCandidate` in `weft/core/manager_services.py` and `snapshot[1].get("terminal") is True` in `Manager._tracked_service_terminal_proof_visible`. | **Accept (`fixed in plan`)** | Updated Phase 2 Step 2 and Phase 3 Steps 3–4 accordingly. |
| **F3** | P2 | Phase 1 Step 4 | Completion clears `self._active_work_in_flight = False` in `Consumer._handle_active_work_result` (`consumer.py:585-588`), not `_finish_reactor_work_item`. | **Accept (`fixed in plan`)** | Phase 1 Step 4 places the immediate re-probe in `Consumer._handle_active_work_result`'s `finally:` block. |
| **F4** | P2 | Phase 4 | `DockerProviderCLIRunner.run_with_hooks` already applies `_docker_nano_cpus(self._limits)` at `agent_runner.py:204-206, 547-554`. | **Accept (`fixed in plan`)** | Removed disproven Docker CPU item; Phase 4 scopes `DockerProviderCLIRunner` to `runtime_requirements.has_missing_required`. |
| **F5** | P2 | Phase 4 Step 1 | Docker test must not fail earlier in `_resolve_work_item_mounts`, and `LLMBackend` bundle tests must use distinct module names for plugin, schema, and tool. | **Accept (`fixed in plan`)** | Updated Phase 4 Step 1 with valid `work_item_mounts` and distinct module names. |
| **F6** | P2 | Phase 1 & Phase 3 tests | Synchronize paused `Consumer` test on `CONTROL_PING`/`PONG` after `PAUSE`, and reset `child.terminal_retry_after_ns = 0` before testing shutdown retries in Phase 3. | **Accept (`fixed in plan`)** | Updated Phase 1 Step 1 and Phase 3 Step 1. |
| **F7** | P3 | Phase 5 & Phase 6 | Pair `cmd_task_tid(reverse=...)` with `14-Python_API_Surfaces.md` under Strategy A/B and assert literal `"1K6CSK6CSK6CS"`. | **Accept (`fixed in plan`)** | Updated §4, §4b, §4c, and Phase 5 Step 1. |

### Round 2 (`2026-11-06`, `codex`, thread `01a1b191-b410-77e1-b78c-40a841ebda94`, baseline `5514f3ec`)
- **Verdict:** `FAIL` (`F1`–`F5` and `F7` verified resolved; 3 findings `R2-1`–`R2-3`)
- **Findings and Dispositions:**

| ID | Severity | Location | Summary of Reviewer Finding | Disposition | Author Action / Pointer to Final Requirement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **R2-1** | P2 | Phase 4 Step 3 | Format `runtime_requirements.diagnostics` with `format_provider_container_runtime_diagnostics(...)` matching `DockerRunnerPlugin._validate_agent_taskspec` (`plugin.py:642-649`). | **Accept (`fixed in plan`)** | Updated Phase 4 Step 3. |
| **R2-2** | P2 | Phase 1 Step 1 | `CONTROL_PING` replies are written to `reply_to` (`[MF-3]`), not `ctrl_out`. | **Accept (`fixed in plan`)** | Updated Phase 1 Step 1 to use `reply_to=reply_queue_name`. |
| **R2-3** | P2 | Phase 1 Step 1 | Confirm `paused=True` via keyed `PONG` before writing work to `inbox`. | **Accept (`fixed in plan`)** | Updated Phase 1 Step 1. |

### Round 3 (`2026-11-06`, `codex`, thread `01a1b191-b410-77e1-b78c-40a841ebda94`, baseline `5514f3ec`)
- **Verdict:** `PASS` (`R2-1`–`R2-3` verified resolved).

### Owner Review Passes 1–4 (`2026-11-06`, baseline `5514f3ec`)
- **Findings and Dispositions:**

| ID | Severity | Location | Summary of Owner Finding | Disposition | Author Action / Pointer to Final Requirement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **OR-1** | P2 | Phase 1 Step 1 (`Consumer` paused test & real-process proof) | `Consumer._handle_work_message` (`consumer.py:190-210`) calls `reserved_queue.move_one(...)` directly rather than `_requeue_reserved_message`, so a `_requeue_reserved_message` counter stays `0` even while spinning. Also PING traffic does not establish reactor quiet between events, and in-process tests should be paired with a real-process CPU probe while paused with queued input. | **Accept (`fixed in plan`)** | Phase 1 Step 1 now observes `inbox.move_one` and `reserved.move_one`, verifies unchanged `inbox` custody and original message ID, and adds the real-process CPU probe `test_paused_consumer_does_not_spin_with_queued_input` in `tests/system/test_manager_reactor_floor.py`. |
| **OR-2** | P2 | Phase 1 Step 1 (`Consumer` busy test & `Manager` reach) | Busy `Consumer._process_reactor_turn` (`consumer.py:130-142`) bypasses `super()._process_reactor_turn()`, so `process_once()` never updates `_active_queues` while busy. Test watcher predicate eviction in `tests/tasks/test_multiqueue_watcher.py` and test busy `Consumer` via observable custody, control responsiveness, and next-turn progress. Also document why filtering `still_active` in `MultiQueueWatcher` is safe for `Manager` and add Manager suites + wake-latency check to Phase 1 gates. | **Accept (`fixed in plan`)** | Added `test_update_active_queues_evicts_queue_when_wait_activity_predicate_turns_false` in `tests/tasks/test_multiqueue_watcher.py`, removed private `_active_queues` assertions from `Consumer`/`PipelineEdgeTask` tests, documented `Manager` reach in §3, and added Manager suites to Phase 1 gates. |
| **OR-3** | P2 | Phase 1 Step 1 (`PipelineEdgeTask` resume turn) | Active queues are refreshed before `CONTROL_RESUME` is handled, so the re-enabled `inbox` is drained on the turn following `CONTROL_RESUME`. Drive the `RESUME` turn and then the work turn without advancing the clock. | **Accept (`fixed in plan`)** | Updated Phase 1 Step 1 (`Consumer` and `PipelineEdgeTask` tests) to drive the `RESUME` turn and then the work turn without advancing any clock. |
| **OR-4** | P1 | §6a, §7, §8 (Gates & Class-5 Theory Requirements) | Final gates and Phase 1–3 gates must include the canonical `mypy` command, full SQLite fast (`pytest -q`) and all-tests (`pytest -m "" -q`) runs, and `./bin/pytest-pg --all tests`, plus the Class-5 `[WT-0]` theory-versus-code audit refresh and `[WT-7]` two-factor theory-fit test. | **Accept (`fixed in plan`)** | Added §6a (`Theory-Versus-Code Audit Refresh`), Phase 6 Step 3 (`[WT-7]` two-factor fit test), and updated §5 and §7 with canonical `mypy`, full SQLite fast/all suites, and `./bin/pytest-pg --all tests`. |
| **OR-5** | P3 | Phase 2 Steps 3–4 (Contract boundaries) | Assign `self._registry_message_id = queue.write(...)` directly in `PipelineTask._write_pipeline_registry_record` without `isinstance` / rescan fallback (retaining `_latest_pipeline_registry_message_id` only on deletion fallback); unpack `_body, timestamp = rows[0]` directly in `HeartbeatTask._endpoint_registry_version`; and note that `endpoints._record_owner_is_live` fix applies to retained terminal snapshots without a runtime handle. | **Accept (`fixed in plan`)** | Updated §3 and Phase 2 Steps 1, 3, and 4. |
| **OR-6** | P3 | Phase 3 Step 4 (TID ordering) | Real 19-digit TIDs have identical string and numeric order until year 2286; frame numeric TID ordering in `manager_services.py` as a contract consistency cleanup using strict `int(candidate.tid)` with no permissive non-numeric fallback. | **Accept (`fixed in plan`)** | Updated §3 and Phase 3 Steps 1 and 4. |
| **OR-7** | P3 | Phase 4 Step 1 & Phase 5 Steps 3–4 | Split the `LLMBackend` bundle test into three focused tests (one-shot resolution, session propagation, cross-bundle isolation); remove `_task_snapshot_payload` in `weft/cli/app.py` and call `_status_task_json_payload` directly; and state that `_apply_explicit_run_name` (`weft/commands/run.py:699-730`) is called only by the `--pipeline` path (`run.py:955`). | **Accept (`fixed in plan`)** | Updated §3, §4, Phase 4 Step 1, and Phase 5 Steps 1, 3, and 4. |
| **OR-8** | P3 | Header & §4 (Structure & commit isolation) | Simplify the `Class:` header line, shorten the operative plan by stating each invariant/repair once, state that each phase commits separately for independent rollback, and specify when the completed-work review runs. | **Accept (`fixed in plan`)** | Simplified `Class:` line, removed duplicate sections, and specified per-phase commits and completed-work review checkpoints in §4 and Phase 3/6. |
| **OR-9** | P2 | Phase 1 Step 1 & §6 (`wait_for_activity` return value) | `MultiQueueWatcher.wait_for_activity` (`weft/core/tasks/multiqueue_watcher.py:1299`) returns `None`, not a `bool`. Do not change its API; remove the Boolean `wait_for_activity(...) is False` assertions and use unchanged message custody and move counts for gating, plus the real-process CPU probe for quiet behavior. | **Accept (`fixed in plan`)** | Removed all `wait_for_activity(timeout=0.05) is False` assertions from Phase 1 Step 1 and §6; gating is verified via unchanged `inbox` custody and `0` move counts, and quiet behavior via the real-process CPU probe `test_paused_consumer_does_not_spin_with_queued_input`. |
| **OR-10** | P3 | Phase 3 Step 1 (`test_service_candidate_selection_and_summary_order_by_int_tid`) | Two equal-width 19-digit TIDs have identical string and numeric order, and checking the private helper's return type enforces implementation rather than selection behavior. Use valid TIDs spanning different decimal widths and assert public selection and summary order. | **Accept (`fixed in plan`)** | Updated Phase 3 Step 1 to pass valid decimal TIDs of different widths (`"999999999999999999"` and `"1844674407370955161"`) and assert public `select_canonical_live_candidate` and `summarize_service_candidates` order. |
| **OR-11** | P3 | §7 (`Final Gate Commands` supplemental roots) | `pyproject.toml` sets `testpaths = ["tests"]`, so `pytest -q` and `pytest-pg --all tests` skip `extensions/*/tests` and `integrations/weft_django/tests`. Add the full supplemental roots on both SQLite and PostgreSQL at closure. | **Accept (`fixed in plan`)** | Added `./.venv/bin/python -m pytest -m "" integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests -q` and `./bin/pytest-pg --all integrations/weft_django/tests extensions/weft_docker/tests extensions/weft_macos_sandbox/tests extensions/weft_microsandbox/tests` to `## 7. Verification and Gates`. |
| **OR-12** | P3 | Phase 6 Step 3 (`[WT-7]` fit-test isolation) | Running `codex` inside the repository can pull in `AGENTS.md` and other ambient workspace context. Run it from a scratch directory outside the repository with `docs/program-theory.md` and the diff supplied inline. | **Accept (`fixed in plan`)** | Updated Phase 6 Step 3 to invoke `codex exec --skip-git-repo-check -s read-only -C <scratch_dir_outside_repo>` from an empty scratch directory outside the repository with both inputs supplied inline. |

### Intermediate Completed-Work Review (`2026-11-06`, `codex`, thread `01a1b21f-a8e2-7e82-b47c-bdc5ab8b9c3f`, Phases 1–3 `5514f3ec..0a9045c1`)
- **Verdict:** `PASS WITH 1 MEDIUM FINDING` (fixed in `02b7509d`)
- **Findings and Dispositions:**

| ID | Severity | Location | Summary of Reviewer Finding | Disposition | Author Action / Pointer to Final Requirement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CW1-1** | P2 | `weft/core/manager.py::_clear_admission_retry` | When `MultiQueueWatcher._update_active_queues` evicts an admission-blocked spawn lane (`weft.spawn.requests` or `weft.spawn.internal`) from `_active_queues`, clearing the admission block via `_clear_admission_retry()` must re-enable the blocked lane (`_mark_queue_active(queue_name)`) so the Manager immediately re-probes the newly unblocked lane on the next turn without waiting for `_inactive_check_interval` or spuriously probing unblocked lanes. | **Accept (`fixed in 02b7509d` & Phase 6)** | Updated `Manager` (`weft/core/manager.py:712, 3313-3314, 3379-3474`) to record blocked lanes at the decision points (`_record_admission_blocked_lanes` and `_admission_allows_queue`) while keeping `_queue_counts_as_wait_activity` pure, call `self._mark_queue_active(queue_name)` in `_clear_admission_retry`, and added `test_clear_admission_retry_marks_evicted_spawn_lane_for_immediate_reprobe` (`tests/core/test_manager.py:13407-13433`). |
| **CW1-2** | P3 | `tests/system/test_manager_reactor_floor.py` | Use a requester control queue (`T{requester_tid}.ctrl_in`) for `STATUS` `reply_to` in `test_paused_consumer_does_not_spin_with_queued_input` to follow `[WT-2a.12]`. | **Accept (`fixed in 02b7509d` & Phase 6)** | Updated `reply_to` in `tests/system/test_manager_reactor_floor.py:121-123` to use `f"T{ctrl_in.generate_timestamp()}.ctrl_in"`. |

### Final Completed-Work Review Round 1 (`2026-11-06`, `codex`, thread `01a1b23c-0945-7a63-900a-4a6cccc28de1`)
- **Verdict:** `FAIL` (no production regression in Phase 4–5 paths; 6 verification/closeout findings `CW2-1`–`CW2-6`)
- **Findings and Dispositions:**

| ID | Severity | Location | Summary of Reviewer Finding | Disposition | Author Action / Pointer to Final Requirement |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **CW2-1** | P2 | `tests/core/test_agent_runtime.py:308` | `BundleTestModel.execute` constructed `payload = {schema_field: value}` from the template string without reading `prompt.schema`, so the tests would pass even if `_resolve_output_schema` returned `None`. | **Accept (`fixed`)** | Updated `BundleTestModel.execute` in `tests/core/test_agent_runtime.py:308-317` to assert `prompt.schema is not None`, extract `schema_dict["properties"].keys()`, assert `schema_fields == [schema_field]`, and build the response payload from `schema_fields[0]`. |
| **CW2-2** | P2 | `## 6a. Theory-Versus-Code Audit Refresh` | Row 2 cited `test_manager_does_not_spin_while_supervising_running_task` instead of the actual test `test_manager_does_not_spin_while_user_task_runs` (`< 1.5s` threshold over `3.0s`). | **Accept (`fixed`)** | Corrected the test name and `3.0s` measurement window in `## 6a. Theory-Versus-Code Audit Refresh`. |
| **CW2-3** | P2 | `#### 8.1 Author's Five-Part Fit Account` | Author's fit account misstated that shutdown (`deadline is not None`) removed synchronous sleeps (Phase 3 Step 2 removes `time.sleep` on steady-state reactor turns where `deadline is None` and schedules retry via `child.terminal_retry_after_ns` and `_manager_child_timeouts`, while retaining bounded retries during shutdown), misidentified `[ALT-WT-006]`/`[ALT-WT-011]`, and quoted an older `[WT-4.1]` sentence. | **Accept (`fixed`)** | Corrected `#### 8.1 Author's Five-Part Fit Account` to accurately state steady-state vs. shutdown retry behavior (`_manager_child_timeouts`), cite `_inactive_probe_interval`, cite `[ALT-WT-009]`/`[ALT-WT-010]`/`[ALT-WT-012]`/`[ALT-WT-015]`, and quote the exact sentence at `docs/program-theory.md:449`. |
| **CW2-4** | P2 | Phase 6 Step 3 | `git diff 5514f3ec..HEAD` excludes uncommitted Phase 5–6 working-tree edits unless committed or diffed against `5514f3ec` with explicit paths and pathspec exclusion for `tests/system/test_pytest_pg_script.py`. | **Accept (`fixed`)** | Updated Phase 6 Step 3 and `#### 8.1`/`#### 8.2` to use `git diff 5514f3ec -- weft extensions integrations tests docs/specifications CHANGELOG.md ':(exclude)tests/system/test_pytest_pg_script.py'`. |
| **CW2-5** | P2 | `CHANGELOG.md` (`[QUEUE.8]`) | Modifying `weft/core/tasks/multiqueue_watcher.py` requires a `CHANGELOG.md` notice and new whole-file SHA-256 digest under `[QUEUE.8]` (`docs/specifications/07-System_Invariants.md:242`) so Taut can re-pin its vendored copy. | **Accept (`fixed`)** | Added `## [Unreleased]` notice in `CHANGELOG.md` recording the `weft/core/tasks/multiqueue_watcher.py` copy-boundary change and its new SHA-256 digest `713060f2880b401220505e82f9fad11916740a8355cad026f869ac61dfdbf086`. |
| **CW2-6** | P3 | `tests/core/test_manager.py:13349` | Patching `manager_mod.time.sleep` mutates shared stdlib `time.sleep`; replace `manager_mod.time` with a `SimpleNamespace` binding instead. | **Accept (`fixed`)** | Updated `tests/core/test_manager.py:13349-13379` to patch `manager_mod.time` with a `SimpleNamespace`. |

### Final Completed-Work Review Rounds 2 & 3 (`2026-11-06`, `codex`, thread `01a1b23c-0945-7a63-900a-4a6cccc28de1`)
- **Verdict:** `PASS` (`CW2-1`–`CW2-6` and `Manager._clear_admission_retry` lane-reactivation scope verified resolved).

### Class-5 `[WT-7]` Two-Factor Theory-Fit Test (`5514f3ec` through Phase 6)

#### 8.1 Author's Five-Part Fit Account (Sealed Prior to Cross-Family Blind Run)
- **Boundary:** `git diff 5514f3ec -- weft extensions integrations tests docs/specifications CHANGELOG.md ':(exclude)tests/system/test_pytest_pg_script.py'` (Phases 1–6 code, tests, changelog, and specification changes, excluding `docs/plans/` and pre-existing edits in `bin/pytest-pg` and `tests/system/test_pytest_pg_script.py`).
- **1. Verdict:** `fits with strain`
- **2. Sections the change rests on:**
  - `[WT-2a.6]` ("One wake arbiter per reactor") and `[WT-4.1]` ("A wake predicate reads facts and guarantees progress; keep an identity you already have; canonicalize at boundaries, then stay strict"): Phase 1 gates `inbox` and `reserved` in `Consumer` and `PipelineEdgeTask` via `_queue_counts_as_wait_activity` and evicts non-actionable queues from `MultiQueueWatcher._active_queues`, while calling `_mark_pending_messages_prechecked()` + `_strategy.notify_activity()` when `CONTROL_RESUME` or work completion re-enables a gated queue (and calling `_mark_queue_active(queue_name)` on recorded `_admission_blocked_queues` in `Manager._clear_admission_retry()`); Phase 2 retains the integer message ID returned by `queue.write(...)` in `PipelineTask._write_pipeline_registry_record` and uses `peek_many(limit=1, order="newest", with_timestamps=True)` in `HeartbeatTask._endpoint_registry_version`; Phase 3 removes synchronous `time.sleep` from steady-state (`deadline is None`) `Manager._commit_wrapper_lost_envelope` calls so retries are scheduled through `child.terminal_retry_after_ns` and `_manager_child_timeouts` on the reactor deadline, and orders `ServiceCandidate` records by `int(candidate.tid)`.
  - `[WT-2a.7]` ("Exactly once per reservation") and `[WT-4.3]` ("A quiet task costs nothing an operator can see"): A paused or busy task leaves unactionable work in `inbox` with its original message ID intact rather than reserving and requeuing it on every reactor turn.
  - `[WT-2a.9]` ("One current contract"), `[WT-2a.13]` ("Independent lifetimes; death needs proof"), and `[WT-3]` ("Core concepts and ownership"): Phase 2 checks `mapping.get("terminal") is True` on `weft.state.tasks.<tid>` before falling back to `runtime_handle` in `weft/core/endpoints.py::_record_owner_is_live`; Phase 3 checks `snapshot[1].get("terminal") is True` in `Manager._tracked_service_terminal_proof_visible` so compacted `weft.log.tasks` rows do not hide task-owned terminal proof; Phases 4–6 align `LLMBackend` bundle-root imports, `DockerProviderCLIRunner` runtime requirement checks, `cmd_task_tid(reverse=...)`, non-persistent pipeline `--name`, CLI task JSON clock projection, and `weft_django` request-client reuse with their single canonical owners.
- **3. What it refuses and the `[WT-6]` entry that refuses it:**
  - Refuses `[ALT-WT-002]` ("Short fixed polling wait-caps in task and control watchers"): Phase 1 and Phase 3 refuse to mask unactionable work or steady-state `wrapper_lost` write failures with polling loops, reserve-and-requeue churn, or `time.sleep()` inside the Manager reactor turn, instead gating watched queues on actionable state, waking the single `PollingStrategy` arbiter via `notify_activity()` on state transitions, and scheduling steady-state retries through the reactor's deadline wait (`_manager_child_timeouts`).
  - Refuses `[ALT-WT-009]` ("Every waiter scans the shared task log for completion"), `[ALT-WT-010]` ("A new scheduler, event bus, index, or cache subsystem"), `[ALT-WT-012]` ("Backward-compatibility code for changed contracts"), and `[ALT-WT-015]` ("Manager-local child tracking or OS `ppid` as the liveness/lineage source"): Phase 2 and Phase 3 refuse speculative non-integer `queue.write()` fallbacks in `PipelineTask`, remove the duplicate `_task_snapshot_payload` formatter in `weft/cli/app.py`, and honor task-owned `weft.state.tasks.<tid>` terminal snapshots directly in `endpoints.py` and `Manager._tracked_service_terminal_proof_visible` before scanning `weft.log.tasks`.
- **4. What it predicts that could prove it wrong:**
  - **Prediction 1 (Reactor wake & progress):** If any transition that flips `_queue_counts_as_wait_activity` from `False` to `True` in a `MultiQueueWatcher` subclass omits `_mark_pending_messages_prechecked()` + `_strategy.notify_activity()` (or `_mark_queue_active(queue_name)` in `Manager._clear_admission_retry()`), an evicted queue with already-queued messages will not be re-probed on the immediate next reactor turn and will stall until `_inactive_probe_interval` expires (`60s` in tests if not triggered).
  - **Prediction 2 (Paused CPU & custody):** A paused `Consumer` or `PipelineEdgeTask` with queued `inbox` messages will execute `0` `inbox.move_one` / `reserved.move_one` operations, preserve the original `inbox` message ID, and consume `< 0.25s` CPU over a `3.0s` wall-clock window while still replying to `CONTROL_PING`/`STATUS` and processing the queued item on the turn immediately following `CONTROL_RESUME`.
  - **Prediction 3 (Steady-state vs. shutdown `wrapper_lost` retry):** On steady-state reactor turns (`deadline is None`), if `_commit_wrapper_lost_envelope` hits a transient broker write failure, `_cleanup_children` makes a single write attempt (`attempts == 1`), does not call `time.sleep`, and schedules the retry via `child.terminal_retry_after_ns` (`_manager_child_timeouts`), whereas shutdown drain (`deadline is not None`) retains bounded `TERMINAL_EVENT_WRITE_RETRIES` attempts capped by `self._remaining_deadline(deadline)`.
- **5. The sentence in `docs/program-theory.md` that fits worst:**
  - In `[WT-4.1]` ("A wake predicate reads facts and guarantees progress", `docs/program-theory.md:449`): *"A predicate that decides whether queued work grants a turn is answered from queue rows, never from memory that only a turn can update."* — `BaseTask._paused` and `Consumer._active_work_in_flight` are in-memory task state updated during a reactor turn (`CONTROL_PAUSE`/`CONTROL_RESUME` or work completion) and read by `_queue_counts_as_wait_activity(queue_name)` to gate `inbox` and `reserved`, requiring explicit `_mark_pending_messages_prechecked()` + `_strategy.notify_activity()` calls when those flags clear so the wake predicate's progress guarantee holds.

#### 8.2 Cross-Family Blind Five-Part Fit Account (`2026-11-06`, `codex`, thread `01a1b23b-ef58-7b62-8c41-46393d627cf2`, run from `/tmp/weft-wt7-fit.3nptUF` outside repo)
- **1. Verdict:** `fits with strain`
  - Most of the diff restores queue-backed facts, explicit reactor wakes, shared command paths, and task-owned lifecycle evidence where code had drifted toward polling, rescans, or split surfaces. Two strains remain: the new wait-activity predicates consult in-memory task flags (`_paused`, `_active_work_in_flight`) where `[WT-4.1]` states wake predicates are answered from queue rows rather than memory that only a turn can update; and the initial paused-consumer CPU probe used `reply_to=f"T{tid}.probe.ctrl_in"` instead of a requester TID's `T{requester_tid}.ctrl_in` (subsequently repaired in `tests/system/test_manager_reactor_floor.py:121-123`).
- **2. Sections the change rests on:**
  - `[WT-2]` item 4, `[WT-2a.6]`, `[WT-2a.8]`, `[WT-4.1]`, `[WT-4.3]`: `MultiQueueWatcher._update_active_queues` evicts queues that stop counting as wait activity, while `BaseTask`, `Consumer`, `PipelineEdgeTask`, and `Manager._clear_admission_retry` gate non-actionable queues and call `_mark_pending_messages_prechecked()` + `_strategy.notify_activity()` when work becomes actionable again.
  - `[WT-4.1]` ("Keep an identity you already have; canonicalize at boundaries, then stay strict") and `[WT-2a.11]`: `PipelineTask` retains the integer message ID returned by `queue.write(...)`, `HeartbeatTask` reads only the newest row (`limit=1, order="newest", with_timestamps=True`) for endpoint registry version changes, `manager_services.py` orders service candidates by `int(candidate.tid)`, and `run.py` rejects the reserved `_weft.` prefix while allowing free-form non-persistent pipeline names.
  - `[WT-2a.2]`, `[WT-2a.13]`, `[WT-3]`: `endpoints._record_owner_is_live` rejects `terminal: True` on `weft.state.tasks.<tid>` before runtime-handle fallback, `Manager._tracked_service_terminal_proof_visible` checks `weft.state.tasks.<tid>` before scanning `weft.log.tasks`, and `Manager._commit_wrapper_lost_envelope` avoids sleeping inside steady-state reactor turns (`deadline is None`).
  - `[WT-3]`, `[WT-4.2]`, `[WT-4.4]`, `[WT-5]`: `LLMBackend` reuses `import_module_ref`/`import_ref` with `bundle_root`, `DockerProviderCLIRunner` enforces `runtime_requirements.has_missing_required`, `cmd_task_tid(reverse=...)` matches `weft task tid --reverse`, CLI task JSON uses `_status_task_json_payload` for integer nanosecond wall-clock timestamps, and `weft_django` reuses the request-owned client and delegates deferred-submission `snapshot()`/`terminal_snapshot()`.
- **3. What it refuses and the `[WT-6]` entry that refuses it:**
  - `[ALT-WT-002]`: refuses burning CPU or sleeping inside reactor turns on paused/busy queues or steady-state `wrapper_lost` write retries.
  - `[ALT-WT-010]` and `[ALT-WT-011]`: refuses treating `weft.log.tasks` scans as the primary liveness/terminal oracle when `weft.state.tasks.<tid>` already records `terminal: True`.
  - `[ALT-WT-012]`: refuses maintaining parallel legacy snapshot formatters (`_task_snapshot_payload`) alongside `_status_task_json_payload`.
  - `[ALT-WT-015]` and `[ALT-WT-020]`: refuses letting agent backends or `weft_django` bypass shared validation, bundle isolation, or task-handle surfaces.
- **4. What it predicts that could prove it wrong:**
  - Paused `Consumer` and `PipelineEdgeTask` instances leave queued messages in `inbox` with `0` `move_one` calls and `< 0.25s` CPU over `3.0s`, and process the message on the turn after `CONTROL_RESUME` without waiting for `_inactive_check_interval`.
  - Busy `Consumer` instances leave a second message in `inbox` while the first runs and reserve it on the turn after completion without waiting for `_inactive_check_interval`.
  - Clearing an admission block in `Manager._clear_admission_retry()` re-enables an evicted spawn lane on the next `_update_active_queues()` pass without waiting `60s`.
  - Steady-state `Manager._cleanup_children()` (`deadline is None`) makes 1 write attempt without calling `time.sleep` and sets a positive `terminal_retry_after_ns` deadline; shutdown (`deadline is not None`) retries up to `TERMINAL_EVENT_WRITE_RETRIES`.
  - Terminal `weft.state.tasks.<tid>` snapshots (`terminal: True`, `runtime_handle: None`) are rejected by `resolve_endpoint` and accepted as service terminal proof by `Manager` even when `weft.log.tasks` is empty.
  - `PipelineTask` updates/deletes its registry row with `0` `peek_generator` scans, and `HeartbeatTask` checks endpoint registry version via `peek_many(limit=1, order="newest", with_timestamps=True)`.
  - `LLMBackend` isolates colliding bundle module names across bundles (`_weft_bundle_<digest>`), and `DockerProviderCLIRunner` raises `ValueError` before `ensure_agent_image` or `docker_client` when required provider mounts are missing.
- **5. The sentence in `docs/program-theory.md` that fits worst:**
  - In `[WT-4.1]` (`docs/program-theory.md:449`): *"A predicate that decides whether queued work grants a turn is answered from queue rows, never from memory that only a turn can update."*

#### 8.3 `[WT-7]` Comparison and Divergence Classifications
- **Convergence:** Both accounts independently reached the same verdict (`fits with strain`), cited the same governing sections (`[WT-2a.6]`, `[WT-2a.13]`, `[WT-3]`, `[WT-4.1]`, `[WT-4.3]`), cited the same core refusals (`[ALT-WT-002]`, `[ALT-WT-011]`, `[ALT-WT-012]`), made the same falsifiable predictions across Phases 1–5, and selected the **exact same sentence** in `[WT-4.1]` (`"A predicate that decides whether queued work grants a turn is answered from queue rows, never from memory that only a turn can update."`) as the sentence that fits worst.
- **Divergence 1 (Test requester control queue naming in `test_paused_consumer_does_not_spin_with_queued_input`):** The blind reviewer noted that `reply_to=f"T{tid}.probe.ctrl_in"` in `tests/system/test_manager_reactor_floor.py` used a synthetic `.probe.ctrl_in` suffix rather than a requester's own `T{requester_tid}.ctrl_in` (`[WT-2a.12]`).
  - **Classification:** `change deficiency` (in test fixture only) — repaired in `tests/system/test_manager_reactor_floor.py:121-123` by generating a canonical hybrid-timestamp requester TID (`requester_tid = str(ctrl_in.generate_timestamp())`) and using `f"T{requester_tid}.ctrl_in"`.
- **Divergence 2 (`[WT-4.1]` wording on in-memory task flags in wake predicates):** Both accounts noted that `_paused` and `_active_work_in_flight` are in-memory task state updated during a reactor turn (on `CONTROL_PAUSE`/`CONTROL_RESUME` or local work completion, which `[WT-4.1]`'s third sentence allows as arbiter inputs: *"Local completions and deadlines remain arbiter inputs ([WT-2a.6])."*). Because the first sentence of `[WT-4.1]` was written specifically against the Manager reading its in-memory family scheduler instead of queue rows (`2026-11-05 review`), and the third sentence already admits local completions and deadlines as arbiter inputs when paired with explicit re-probe notification, no revision to `docs/program-theory.md` is required in this plan.


---

## 9. Out of Scope

- Changing `Manager._seed_child_inbox` (`weft/core/manager.py:2408-2436`) retry backoff (last-chance write before permanent spawn rejection, not a recurring reactor loop).
- Removing `_latest_task_statuses` from `weft/core/endpoints.py` (required by `[MF-3.1]` and `tests/tasks/test_task_endpoints.py:732-831`).
- Changing `DjangoWeftClient.status(tid)` return type in `integrations/weft_django/weft_django/client.py:356-360` (requires an owner decision because existing `weft_django` tests assert snapshot return).
- Rewriting `DockerProviderCLIRunner` to use `run_monitored_subprocess` (Phase 4 scopes to `has_missing_required` parity; `nano_cpus` is already enforced in `run_with_hooks`).
- Moving `_write_stdout_bytes` / `_silence_closed_stdout` out of `weft/commands/_output.py`.
