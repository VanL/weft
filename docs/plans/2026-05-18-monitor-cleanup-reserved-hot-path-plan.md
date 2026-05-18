# Monitor Cleanup Reserved Hot Path Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-6], docs/specifications/07-System_Invariants.md [OBS.13], [OBS.17]
Superseded by: none

## Source Of Truth

- `docs/specifications/05-Message_Flow_and_State.md` cleanup boundary and TaskMonitor behavior.
- `docs/specifications/07-System_Invariants.md` [OBS.13], [OBS.17].
- Implementation owner: `weft/core/monitor/cleanup.py`.

## Requested Outcomes

- Stop the supervised TaskMonitor's ordinary cleanup cycle from probing
  task-local `T{tid}.reserved` queues for most task-log retention work.
- Avoid scanning for claimed task-log rows when `weft.log.tasks` reports zero
  claimed rows.
- Treat terminal task-log proof as terminal events first; do not let ordinary
  `task_activity` rows with terminal-looking status split lifecycle families
  before the real terminal event.
- Keep deletion exact-message based and preserve recovery-sensitive reserved
  work by default.

## Invariants

- `weft.log.tasks` cleanup may delete only exact selected message IDs.
- Runtime lifecycle truth does not move into Monitor-derived summaries or
  cleanup stats.
- Reserved work stays protected unless an explicit terminal-proven reserved
  cleanup path is enabled.
- PONG/status diagnostics must remain cached from the last cycle; no diagnostic
  path may scan queues on demand.

## Implementation

1. Add an internal `reserved_cleanup_enabled` switch to
   `TaskMonitorCleanupConfig`, defaulting to `False`.
2. Guard `_terminal_reserved_candidates()` behind that switch and skip the
   follow-up when the task-log policy budget is exhausted.
3. Use the existing queue `claimed` count as a cheap gate before the expensive
   claimed-row scan.
4. Narrow terminal detection so rows with non-terminal events such as
   `task_activity` are not treated as terminal solely because they carry a
   terminal status. Keep status-only terminal rows supported.
5. Update tests so default cleanup proves failed terminal task-log rows do not
   trigger reserved probes, while the explicit reserved-cleanup helper remains
   covered.
6. Update the cleanup specs to say ordinary supervised cleanup does not probe
   `.reserved` queues.

## Verification

- Focused tests:
  `./.venv/bin/python -m pytest tests/core/test_task_monitor_cleanup.py`
- Lint touched Python:
  `./.venv/bin/ruff check weft/core/monitor/cleanup.py tests/core/test_task_monitor_cleanup.py`
- Post-deploy signal: monitor cleanup cycles should delete task-log rows in
  bounded batches without py-spy showing `_terminal_reserved_candidates()` in
  ordinary cycles.

## Fresh-Eyes Self-Review

- Risk: disabling default reserved cleanup can preserve old failed-work
  reserved rows longer. This is acceptable because reserved queues are
  recovery-sensitive by design and should not be scanned as part of ordinary
  task-log retention.
- Risk: an explicit reserved cleanup path still needs careful operator
  semantics before becoming a public feature. This plan leaves it internal and
  test-covered rather than promoting it.
- Ambiguity resolved: `batch_size` caps selected cleanup candidates; it should
  not permit thousands of empty reserved queue probes after a full task-log
  batch.
- Ambiguity resolved: terminal status attached to `task_activity` is diagnostic
  state, not the terminal lifecycle event that should close a cleanup family.
