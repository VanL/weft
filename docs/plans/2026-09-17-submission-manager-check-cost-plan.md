# Submission Manager Check Cost

Status: draft
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1], [MA-3]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-6], [MF-7]; docs/specifications/14-Python_API_Surfaces.md [PY-3]
Superseded by: none

Class: 4. Broker resource ownership changes on the durable submission spine;
no intended public behavior or normative spec change. Hardening applies.

## Goal

Measure local-ready and keyed-PING-ready submission checks and reduce redundant
registry scans and broker setup while preserving fresh readiness evidence on
every submission. No TTL, batch API, Django callback change, or new dependency.

## Spec Baseline

`3faeff21f527bbd32c8ccfb53d662a68a0ccb024` is the committed baseline for all
source specs above. This is implementation against existing behavior. Spec
maintenance is limited to plan backlinks and implementation notes, not normative
rules. A no-v1 initial scan already verifies absence; post-delete verification
remains mandatory after any v1 deletion.

## Source Documents and Context

Read the source specs above plus `docs/specifications/07-System_Invariants.md`
[MANAGER.8], [IMPL.8], [IMPL.9], [IMPL.10], [IMPL.11], [QUEUE.8];
`docs/agent-context/runbooks/runtime-and-context-patterns.md`,
`docs/agent-context/runbooks/testing-patterns.md`,
`docs/agent-context/runbooks/hardening-plans.md`, and
`docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`.

Files to modify:
- `weft/core/service_convergence.py`: schema discard currently scans twice even
  when no retired row exists. Both scans include claimed rows.
- `weft/core/manager_runtime.py`: observation currently opens separate registry,
  backlog and keyed-probe resources when no caller broker is supplied.
- `weft/commands/submission.py`: prepared submission owns enqueue separately from
  readiness; `ensure_manager_after_submission` owns per-TID recovery decisions.
- `tests/core/test_manager_runtime_connections.py`,
  `tests/core/test_service_convergence.py`, `tests/commands/test_submission.py`;
  narrowly adjust existing injected test functions if the internal optional
  session keyword changes their callable shape.
- Source spec implementation notes/backlinks and `docs/plans/README.md`.

Reuse `WeftContext.session()`, `BrokerSession.connection()`, the existing
`broker=` borrowing seams, keyed probes, and `reconcile_submitted_spawn`.
Do not build a second session framework or bypass SimpleBroker APIs.
Comprehension checks: why are claimed rows included in schema validation but
excluded from manager selection? Why must an active connection scope end before
calling reconciliation/startup helpers that close same-key sessions?

## Invariants and Constraints

- Spawn write returns the committed TID before readiness is observed. Preserve
  immutable TaskSpec fields, queue names, request bodies and receipt identity.
- Accepted work is never deleted/resubmitted by recovery. Reserved/spawned work
  ends submission-scoped startup; authoritative rejection stays typed; unknown
  evidence and I/O failures never become absence.
- Every call reads fresh manager evidence and checks exact process identity or
  keyed PONG. No cross-call readiness or liveness cache.
- Future schemas fail before any v1 deletion, including claimed future rows.
  Only exact v1 IDs may be removed. Reappearing v1 after deletion still fails.
  Keep full generator reads; selection still excludes claimed rows.
- Session and connection scopes belong to the caller thread and exact effective
  runtime context, including TaskSpec context redirection. Connection lifetime
  must not extend transaction lifetime. No transaction across PING waits.
- Borrowers never close caller resources. End the borrowed connection before
  reconciliation or startup; nested session exit can recycle same-key thread
  resources and must not encounter an outer active connection.
- Close owned resources on success and exceptions. A cleanup failure after
  enqueue must retain the accepted-TID error annotation. Do not relabel arbitrary
  programmer defects as manager uncertainty.
- Preserve CLI/client public signatures. An optional same-call observation on
  the internal shared submission helper is not a public adapter API change.

## Rollout and Rollback

No storage format or policy change; old and new readers can coexist. Rollback is
reverting the scoped Python changes and associated tests/docs. No one-way door
or new cleanup owner is introduced. Runtime success is fewer physical connection
opens and scans with the same ready reasons, queue acceptance and completed work.

## Implementation Slices

1. **Measure and prove baseline.** Run a repeatable isolated real-manager SQLite
   benchmark for both paths, reporting sample count, median/p95, registry scans,
   physical opens and limitations. Force only the runtime liveness observation
   to unknown for PING; keep broker and responder real. Add real-broker regression
   tests that fail on redundant clean-registry scans and connection churn.
   Stop if setup measures manager startup rather than steady-state checks.
2. **Remove redundant no-op scan.** Return after the first schema scan when it
   finds neither future nor v1 rows. Retain the entire delete-and-verify path when
   v1 exists. Exercise clean/malformed/current/claimed/future/reappearing cases.
   Stop if this requires changing claimed-row selection or schema policy.
3. **Share bounded operation resources.** Make standalone
   `observe_manager_availability` own one session/connection for registry,
   backlog and PING, recursively borrowing through its existing `broker=` seam.
   Keep session acquisition within its existing I/O uncertainty boundary.
   Add an optional same-call `ManagerAvailabilityObservation` to
   `ensure_manager_after_submission`. Prepared submission owns one effective-context
   session and connection for committed enqueue plus initial observation; close
   that operation before handing the observation to ensure's unchanged recovery
   path. Standalone ensure still observes once itself. Expand
   accepted-TID annotation to include session-exit failure after acceptance.
   Standalone callers keep the existing API and gain consolidated observation.
   Stop if a connection would remain open across reconciliation/startup or
   cleanup errors would erase durable acceptance.
4. **Verify and review.** Repeat benchmark, run targeted shared tests on SQLite
   and PostgreSQL using `bin/pytest-pg`, neighboring submission/manager tests,
   repository lint/type checks, and plan/spec traceability gates. Independently
   review diff and address findings. Record measured deltas and limitations.

## Verification

Source `.envrc`; use repository `.venv` executables. Keep real broker reads,
committed writes, PING replies and cleanup. Instrument connection creation and
registry iterator calls as supporting performance evidence, not fake brokers.
Use fault injection only for process visibility and exceptional I/O/cleanup.

Required regression evidence: clean schema discard is one scan; v1 delete still
rechecks; future/claimed future prevents deletion; local-ready and PING-ready
prepared submissions share a physical caller connection; fresh stopped records
are observed on later calls; post-acceptance errors preserve the TID; slow
recovery and real execution still work; all physical resources close, including
when another same-key handle survives. Verify explicit context/config custody.

Commands:
- `./.venv/bin/python -m pytest tests/core/test_manager_runtime_connections.py tests/core/test_service_convergence.py tests/commands/test_submission.py`
- `bin/pytest-pg tests/core/test_manager_runtime_connections.py tests/core/test_service_convergence.py tests/commands/test_submission.py`
- `./.venv/bin/python -m pytest tests/commands/test_manager_commands.py tests/core/test_control_probe.py tests/commands/test_run.py`
- `./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_spec_hygiene.py`
- `./.venv/bin/ruff check .`
- `./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml`

Formatting is scoped to edited Python files. No commit or push without user
instruction; report uncommitted implementation for review rather than claiming
landed completion.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |

## Execution Log

- Plan self-review: caught unconditional second schema scan (three total scans,
  not two), and claimed-row mismatch preventing naive scan fusion. Narrowed scan
  change to no-op verification removal. Caught same-key session-close refusal;
  optional session borrowing ends operation scope before slow recovery.
- Reviewer availability: Claude CLI is installed; request read-only cross-family
  plan review before production edits. Independent review pending.
- Baseline measurement delegated using temporary, isolated fixtures.
- Baseline SQLite benchmark (macOS arm64, Python 3.14.4, real detached manager):
  local ensure n=100 median/p95 1.553/1.742 ms; prepared local n=20
  3.139/5.580 ms. Local ensure/prepared open 1/2 physical connections.
  PING ensure n=100 382.008/703.157 ms; prepared PING n=20 64.924/231.573 ms,
  with 3/4 physical connections. All cases perform three registry scans.
  Independent idle-only n=40 PING median 1017.546 ms: reactor activity dominates
  PING timing, so do not attribute wall-time variation to connection changes.
  Temporary rerunner `/tmp/weft_submission_benchmark.py` uses five warmups,
  caller-thread counters, real keyed PONGs, and forces only process liveness
  unknown. Preparation is excluded from submission timing.
- Baseline failing tests: clean schema expects one scan but observes two;
  prepared local/PING single-connection assertions fail; session-exit failure
  test cannot fire because baseline has no enclosing submission session.
  Claimed-future protection and fresh stopped-record tests pass. Commands and
  logs: `/tmp/weft-submission-cost-red.txt`,
  `/tmp/weft-submission-cost-cleanup-red.txt`. Baseline mypy passes 434 files.
- Additional self-review: caller-supplied session acquisition occurs before the
  observation function's own exception boundary. Preserve registry-read-error
  uncertainty for BrokerError/OSError there, rather than leaking it as a new
  post-acceptance failure. Programmer/cleanup RuntimeError still propagates.
- Independent bounded preimplementation review (Codex): PASS on the two changed
  boundaries, checked against installed SimpleBroker. Confirmed safe session
  sequence and clean-scan verification compliance. Accepted guard: capture TID
  inside the enqueue connection before its exit. Cross-family whole-plan review
  continues as supplemental review; production scope is limited to these reviewed
  boundaries.
- Cross-family plan review (Claude): PASS. F1 incorrectly identified the new
  regression tests as committed baseline tests; `git show HEAD:tests/core/test_manager_runtime_connections.py`
  ends before them, and the recorded red runs establish the baseline. No user
  decision is needed for that mistaken premise. F2 (I/O acquisition boundary)
  and F3 (one-connection claim applies only to ready paths) accepted and retained.
- Slice 2/3 SQLite run: 100 targeted tests pass after three injected test functions
  accepted the new internal borrowing keywords. Neighbor run exposed a test
  patching the shared stdlib monotonic clock with two values; scope that clock
  to manager_runtime so real broker setup does not consume the fake clock.

## Revision: Share the Initial Operation, Not Only Its Session

PG verification showed two short-lived target/schema validation connections and
one pooled connection despite a shared core. Installed
`BrokerSession.connection()` constructs a new DBConnection on each call; its
first use validates the target. Therefore refine slice 3: enqueue and initial
observation borrow the SAME connection operation, then close it before ensure's
recovery body. Replace the optional session keyword on the internal ensure helper
with an optional `ManagerAvailabilityObservation` from that immediately preceding
observation. Default standalone ensure still observes afresh. This is same-call
handoff, never a cross-call cache. Enqueue commits before observation and TID
capture remains before any resource exit. Acquisition now precedes enqueue;
observation I/O remains inside its normal uncertainty boundary; post-acceptance
connection/session exit failures remain annotated. No policy or public adapter
change. Independent scoped review requested before applying this revision.

PG verification must distinguish broker core, physical checkout and pool lifetime.
Spy on the real enqueue and observation to prove identical non-null broker
identity. After submission, a surviving sibling must obtain a DIFFERENT core,
proving caller-thread cleanup; the PG pool may remain alive until the sibling
closes. Keep the one-physical-open assertion for SQLite only; require all counted
physical connections closed after sibling closure on both backends. Validation
connection reduction is measurement evidence, not a new fixed PG pool-size rule.

- Independent review of the PG-driven revision (Codex): PASS. All guards accepted:
  immediately captured TID; same-context, same-call observation; one connection
  operation ends before recovery; no cross-call cache; resource tests distinguish
  caller core cleanup from surviving pool ownership. Revised targeted SQLite and
  PostgreSQL suites both pass 100 tests. Repository lint and mypy pass.
- Final independent scoped diff review (Codex): no blocker. Reviewer independently
  reran the two core test modules: 51 pass. Checked clean scan, same-context
  connection reuse, operation exit before recovery, accepted-TID annotation,
  fresh evidence and borrowed-resource tests. No actionable findings.
- Verification: targeted SQLite 100 pass; PostgreSQL via `bin/pytest-pg --fast`
  100 pass. Neighbor CLI/manager/client/control, Django integration, plan metadata
  and spec hygiene run passes with three backend-specific PostgreSQL skips under
  SQLite. Full-repository Ruff check and mypy (434 files) pass. The manager-policy
  test's fake monotonic clock is now module-local so broker retry/cleanup clocks
  stay real. No runtime policy was changed to accommodate a test.

## Measurement Results

Final isolated SQLite rerun uses the same temporary benchmark, five warmups and
real detached manager. Local timing completed before a reviewer's short test run;
that test run overlapped part of the mixed-workload PING measurements. The separate
idle-only measurement ran after all tests ended. Keep PING timing conclusions
qualified: reactor scheduling is the dominant source of variation.

| Path | Samples | Before median / p95 ms | After median / p95 ms | SQLite opens before / after | Registry scans before / after |
| --- | --- | --- | --- | --- | --- |
| Local ensure | 100 | 1.553 / 1.742 | 1.470 / 1.732 | 1 / 1 | 3 / 2 |
| Local prepared submit | 20 | 3.139 / 5.580 | 1.804 / 2.079 | 2 / 1 | 3 / 2 |
| PING ensure after fanout | 100 | 382.008 / 703.157 | 217.922 / 795.529 | 3 / 1 | 3 / 2 |
| PING prepared submit | 20 | 64.924 / 231.573 | 57.758 / 174.367 | 4 / 1 | 3 / 2 |
| Idle PING ensure | 40 | 1017.546 / 1030.943 | 1013.849 / 1029.635 | 3 / 1 | 3 / 2 |

Local prepared submission median is about 42.5% lower in this run. Local ensure's
small timing difference is not strong speedup evidence. Idle PING remains about
one second: this work removes setup/scan costs, without changing manager reactor
wake behavior. Timings are SQLite-only, not PostgreSQL speedup evidence.
PostgreSQL correctness/resource tests prove same-operation reuse and cleanup;
its pool and validation connections must not be equated with one SQLite core.
All after paths enter one BrokerSession per check/submission.

Reproduce: source `.envrc`, then run the benchmark invocation recorded above with
`--samples 100 --submit-samples 20` and `--samples 40 --submit-samples 0`.
Before/after JSON logs are `/tmp/weft_submission_benchmark_before.jsonl`,
`/tmp/weft_submission_benchmark_after.jsonl`,
`/tmp/weft_submission_benchmark_idle_before.jsonl`, and
`/tmp/weft_submission_benchmark_idle_after.jsonl`. Temporary files are supporting
session evidence; permanent regression coverage is in the tests listed below.

## Review File Inventory

- `weft/commands/submission.py`
- `weft/core/manager_runtime.py`
- `weft/core/service_convergence.py`
- `tests/commands/test_submission.py`
- `tests/commands/test_manager_commands.py`
- `tests/core/test_manager_runtime_connections.py`
- `tests/core/test_service_convergence.py`
- `docs/specifications/03-Manager_Architecture.md`
- `docs/specifications/04-SimpleBroker_Integration.md`
- `docs/specifications/05-Message_Flow_and_State.md`
- `docs/specifications/14-Python_API_Surfaces.md`
- `docs/plans/README.md`
- `docs/plans/2026-09-17-submission-manager-check-cost-plan.md`

Final test totals: 100 targeted SQLite, 100 targeted PostgreSQL, 489 neighboring
SQLite/client/CLI/Django/documentation tests passed; three backend-specific tests
were skipped in that neighboring SQLite run. No full-repository runtime suite or
production environment benchmark is claimed. Independent reviewer reran 51 of
the core tests successfully. Leave landing to explicit user instruction.
