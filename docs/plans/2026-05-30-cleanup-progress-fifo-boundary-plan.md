# Cleanup Progress FIFO Boundary Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [OBS.13.12]; docs/specifications/05-Message_Flow_and_State.md [MF-5]
Superseded by: none

## 1. Goal

Fix cleanup policy progress reporting when a bounded FIFO age scan selects old
rows and then stops at the first too-young row. That boundary means no more
work is eligible now, so the policy should report `base_reached=True` rather
than keep catch-up pending.

## 2. Source Documents

- `docs/specifications/07-System_Invariants.md [OBS.13.12]`: cleanup policies
  must report base/waypoint/blocked status and avoid spinning when only
  future-eligible work remains.
- `docs/specifications/05-Message_Flow_and_State.md [MF-5]`: cleanup progress
  is cached so PONG/status can distinguish selected work, waypoints, base, and
  deferred work.
- `docs/agent-context/runbooks/testing-patterns.md`: use the narrowest real
  proof and avoid mock-heavy tests for queue semantics.

## 3. Context and Key Files

Files to modify:

- `weft/core/monitor/policies/tid_mapping.py`
- `weft/core/monitor/policies/reserved.py`
- `tests/core/monitor/policies/test_cleanup_progress_boundaries.py`

Read first:

- `weft/core/pruning/policies/older_than.py`
- `weft/core/monitor/progress.py`
- `tests/core/monitor/policies/test_policy_api.py`

Current structure:

- `older_than_candidates()` scans rows in FIFO order and returns
  `stop_reason="first_*_too_young"` when the next row is not old enough.
- `tid_mapping_candidates()` and `terminal_reserved_candidates()` fold private
  cleanup phases into the top-level `runtime_state.retention` and
  `task_local.terminal_runtime` policy identities.
- The bug is only in the progress summary. Candidate selection and deletion
  must not change.

## 4. Invariants and Constraints

- Preserve the five top-level cleanup policy identities from [OBS.13.12].
- Do not change candidate classes, queue names, exact message IDs, or delete
  behavior.
- Treat `first_*_too_young` as future-eligible-only work for that FIFO queue
  window, even when older rows were selected earlier in the same pass.
- Keep bounded waypoint semantics for true limits: scan limit or selection
  limit with remaining eligible work.
- Do not add a new abstraction or shared helper unless duplication grows beyond
  these two local boolean expressions.
- Use direct policy tests with synthetic queue-window rows. This is acceptable
  because the behavior under review is the pure progress contract, not broker
  delete mechanics.

## 5. Tasks

1. Add failing regression tests.
   - File: `tests/core/monitor/policies/test_cleanup_progress_boundaries.py`
   - Cover `tid_mapping_candidates()` with old rows followed by a too-young
     mapping and `scan_limit_reached=True`.
   - Cover `terminal_reserved_candidates()` with old reserved rows followed by
     a too-young reserved row.
   - Assert selected candidates are still returned and the single progress row
     has `base_reached=True`, `waypoint_reached=False`.
   - Stop if the test needs a mocked broker delete path. The bug is before
     apply/delete.

2. Fix `tid_mapping_candidates()`.
   - File: `weft/core/monitor/policies/tid_mapping.py`
   - Change `base_reached` so `first_tid_mapping_too_young` implies base for
     now regardless of selected candidates.
   - Keep the existing waypoint rule for scan-limit runs that select old rows
     without hitting a too-young boundary.

3. Fix `terminal_reserved_candidates()`.
   - File: `weft/core/monitor/policies/reserved.py`
   - Change `base_reached` so any observed `first_reserved_row_too_young`
     boundary implies base for now when the selection limit was not the actual
     stop.
   - Keep `base_reached=True` for no-candidate/no-waypoint runs.

## 6. Testing Plan

Run the new tests first to confirm they fail before the implementation if
practical:

```bash
./.venv/bin/python -m pytest tests/core/monitor/policies/test_cleanup_progress_boundaries.py -q
```

After the fix, run:

```bash
./.venv/bin/python -m pytest tests/core/monitor/policies/test_cleanup_progress_boundaries.py tests/core/monitor/policies/test_policy_api.py tests/core/test_task_monitor_cleanup.py -q
./.venv/bin/ruff check weft tests/core/monitor/policies/test_cleanup_progress_boundaries.py
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
```

Observable success: the new regression tests pass and existing cleanup tests
continue to show the same candidate/delete behavior.

## 7. Verification and Gates

Final gate is the targeted test set plus lint and type check above. A full
suite is not required for this narrow progress-reporting fix unless targeted
tests expose a wider coupling.

Rollback: revert the two boolean-expression changes and the regression tests.
No payload, queue-name, or persisted schema compatibility issue is introduced.

## 8. Independent Review Loop

Self-review is sufficient for this slice because it is a local correction to
progress booleans with no deletion-path or public schema change. External
review would be warranted if the fix starts changing candidate selection,
deletion, runtime scheduling, or the top-level policy identity set.

## 9. Out of Scope

- No cleanup policy consolidation or new policy names.
- No changes to task-log retention base semantics unless a separate
  investigation proves the same bug there.
- No CLI output redesign or PONG payload shape change.

## 10. Fresh-Eyes Review

Self-review completed before implementation. Findings: the plan originally
risked implying broker-backed tests were required; tightened to state that
direct policy tests are the right proof because the bug is in pure progress
folding, not deletion. Residual risk: reserved progress folds multiple queue
probes into one top-level record, so the test must keep selection-limit and
too-young-boundary cases distinct.
