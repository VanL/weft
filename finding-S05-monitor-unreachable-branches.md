# S05 — TaskMonitor carries unreachable branches from the pre-worker-lane reactor

Source: 2026-09-08 read-only review. Class 0. Area: code slop (dead code). Confidence: high.
Verified directly by reading the control flow.

## Claim

After the worker-lane refactor, `_run_monitor_cycle` returns early for the builtin
processor, leaving a later "if not custom processor" block that can never run. All three
worker lanes are registered before dispatch, so the explicit lane branches in
`_handle_worker_result` never execute. `families_retired` is initialised to zero and
accumulated as `+= 0` forever.

## Evidence

- `weft/core/monitor/task_monitor.py:4811-4820`:
  ```python
  if not self._custom_processor_enabled():
      self._maybe_start_builtin_cycle_worker(...)
      return
  ```
  then at 4847-4855 `if not self._custom_processor_enabled(): candidates = () ...` — unreachable.
  The same check inside `_process_monitor_candidates` (5178-5183, sole caller 4873) is also
  unreachable. The `raw_external` reset block at 4826-4843 is a verbatim copy of 5008-5024.
- `task_monitor.py:5260-5277 _handle_worker_result`:
  ```python
  if result.lane in self._service_worker_registrations:
      super()._handle_worker_result(result)
      return
  if result.lane == TASK_MONITOR_BUILTIN_CYCLE_WORKER_LANE:
      ...
  if result.lane == TASK_MONITOR_CONTROL_CLEANUP_WORKER_LANE:
      ...
  ```
  `_register_task_monitor_service_workers` (1234-1254) registers all three lanes, so the
  first `if` always fires for them. The test at `:7795` passes a registered lane and goes
  through `super()`.
- `families_retired`: initialised 0 at `:3776` in `_run_terminal_control_cleanup_slice`,
  never changed, returned at `:3922`, accumulated at `:5401`.

## Why it is slop

Remnants of `docs/plans/2026-05-20-monitor-reactor-worker-refactor-plan.md` that nobody
removed when the lanes were extracted. Dead branches cost every reader the work of proving
they are dead.

## Provenance

No test exercises the dead paths (they cannot be reached). No spec names them.

## Preferable version

`_run_monitor_cycle` becomes "if builtin: submit worker; else: run custom inline". Delete
the two dead branches, the duplicate reset block, the three explicit lane branches (keep
the `ServiceWorkerEvent` passthrough), and the `families_retired` field on this result.

## How to verify

Run `tests/tasks/test_task_monitor.py` (134 tests) and `tests/core/test_task_monitor_cleanup.py`.
