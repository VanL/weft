# Model-Found Scheduler and Registry Repairs

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.4], [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-5]
Superseded by: none

Class 3. Restore existing selection and latest-record contracts in two owners.
Baseline: b6d850c1. No normative spec change.

## Scope and evidence

- In family scheduling, recover a missing live ancestor after indexing its
  descendants. The frontier changes but pending parent ownership stays stale.
  The generated RED probe picks independent root 200 ahead of eligible tip
  child 102. Rebuild derived parent indexes with the existing recovery rebuild.
  Keep family FIFO membership, held requests and exact broker reservation
  authority. Recovery walks pending ancestry once and rebuilds the bucket heaps. Ordinary select,
  launch and retirement do not gain a backlog scan.
- In Manager registry refresh, process active then stopped, repeated active,
  and active/stopped/active histories within one observed suffix. The real
  broker RED tests show stale or duplicate supersession publications. Select
  at most one candidate per changed peer after reduction to the latest row.
  Keep existing leadership and shutdown rules. No cross-transaction exclusion
  is claimed for writes that arrive after the read.

## Verification

Retain the generated scheduler test for immediate and deferred recovery.
Replace bucket-identity assertions invalidated by rebuilding with behavior and
bounded-storage assertions for re-add and contraction. Test the registry
histories with real broker rows. Run family/scaling, Manager, watcher, reactor
floor and relevant CLI tests using normal concurrency. Use both broker backends
where the suites support them. Check Ruff, mypy and doc paths.

## Deviation log

No contract change. The registry repair was added after the scheduler probe
exposed the need to test batched observations separately from individual rows.

## Review and execution record

RED: scheduler chose 200 instead of 102. The three registry histories all
failed before the repair. Completion checks are recorded below.

The work escalated from a single scheduler correction to Class 3 when the
registry history exposed a separate defect. The dated record followed the
initial probes and edits. Google-family Tier 1 review of the plan and repair
diff (Argon, inline source, read-only) returned PASS, no P1 findings. Its
two observations concerned preserving same-bucket re-add coverage and closing
the plan index. The existing same-bucket deduplication test retains that
coverage. The reviewer did not run tests.

SQLite targeted run: 856 passed, 5 backend-specific skips. Coverage included
scheduler scaling, admission recovery, Manager and CLI, watcher, reactor floor,
and plan metadata. Ruff, format, mypy and doc paths passed.

Liveness probe-clock model: 500 generated traces check generation changes,
unknown intervals, probe pauses and retirement. The elapsed-time oracle uses
no production deadline state. The policy file passed all 11 tests. Ignoring
probe pauses in a test process produced a retirement counterexample. No
production liveness change was needed.

PostgreSQL targeted run: 814 passed, 12 skipped in 103.71s (0:01:43). The run covered the scheduler,
Manager, family maintenance, watcher, reactor floor and Manager CLI.
