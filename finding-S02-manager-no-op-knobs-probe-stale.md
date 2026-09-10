# S02 — `probe_stale` and other parameters that are accepted and discarded

Source: 2026-09-08 read-only review. Class 0. Area: code slop (no-op knob). Confidence: high.
Verified directly.

## Claim

`probe_stale` is threaded through eleven call sites in two modules and consumed exactly
once, by `del probe_stale`. `_recent_lower_canonical_manager_exists` accepts two
parameters it deletes on the first line. Nothing any caller passes reaches any logic.

## Evidence

- Consumer: `weft/core/manager_runtime.py:181-190 _manager_registry_disposition` → `del probe_stale`.
- Pass-through sites: `manager_runtime.py:220-227, 498-506, 687-702, 717-729, 1176-1181,
  1464-1469, 1499-1505, 1545-1551, 1675-1679, 1757-1761, 1908-1912`;
  `weft/commands/manager.py:204, 242`. Threaded through `_snapshot_registry`,
  `_registry_view`, `list_manager_records`, `select_active_manager`, `ensure_manager`,
  `start_manager`, `_await_manager_start_settlement`, `_reconcile_competing_manager_start`,
  `_foreground_serve_blocking_manager`, `replace_active_manager`.
- `weft/core/manager.py:2101-2105`:
  ```python
  def _recent_lower_canonical_manager_exists(
      self, queue: Queue, *, now_ns: int | None = None
  ) -> bool:
      del queue, now_ns
  ```
  One caller at `manager.py:1920` passes both.

## Why it is slop

Parameter bloat: a feature flag with one value in practice, kept after the behavior it
selected was removed. It costs every reader a false question ("what does probing stale
rows change?") and every test a meaningless kwarg.

## Provenance

`tests/commands/test_manager_commands.py:307, 481-571` pass `probe_stale=True` but assert
nothing on it. `tests/core/test_manager.py:6587-6593` passes two `now_ns` values and expects
the same result for both, so it pins nothing. No spec or lesson mentions `probe_stale`.

## Preferable version

Delete the parameter from every signature and call site; delete the `queue`/`now_ns`
parameters of `_recent_lower_canonical_manager_exists`. Identical behavior.

## How to verify

`grep -rn "probe_stale" weft tests` returns nothing after the change; run
`tests/commands/test_manager_commands.py` and `tests/core/test_manager.py`.
