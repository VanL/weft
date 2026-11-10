# Measure Admission on an Ops-Shaped Burst

Status: completed
Source specs: docs/specifications/03-Manager_Architecture.md [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-1], [MF-5]; docs/specifications/07-System_Invariants.md [MANAGER.18]; docs/specifications/08-Testing_Strategy.md [TS-0]
Superseded by: none

Class 3. Add a reproducible application-shaped system workload and a released
version comparison. Existing admission, launch, result, and recovery contracts
govern the run. The testing spec delta is an implementation mapping.

## Evidence and scope

The October 13 ops incident logged 1,318 role-limit rejections with a 300
`mm_weft` role limit on a 500-connection server. The 154 enrichment requests
arrived over 36.247 s. Thirty children completed only after their parents timed
out. On October 14 the dedicated child FIFO queued LLM calls behind enrichment
parents from the same rollup. See the [connection-aware plan](2026-10-13-connection-aware-child-admission-plan.md)
and [measured family plan](2026-10-14-measured-admission-family-scheduling.md).

The *Investigate broken Weft ops* and numbered follow-up threads distinguish
production load from the synthetic deep stress case. October 20 had a backlog
with 57–65 sampled Weft connections and a 1.133 s mean launch interval.
October 22 reported a 2.65 s median parent start to child submission and a
0.576 s mean launch interval with overlapping startup feedback. November 6
reconstructed 51 overlapping tasks, sampled 110–134 Weft connections and saw
CPU saturation on eight cores. November 7 submitted 120 enrichment parents in
about 5.55 s and observed 126 child attempts, a 91.6 s median parent start lag
and child runtime p50/p95 of 21.92/27.36 s. Its release used Weft 0.9.122.
These are observations from older code and varying load. The local workload
scales the fanout to the owner's requested 100–175 total tasks, including
descendants, and uses an isolated server with native max_connections=300.

The rollup submits and returns in `../mm-governance/agents/monitoring/wazuh/case_rollup/run.py`.
`../mm-governance/agents/monitoring/enrichment/vulnerability_context.py`
closes Django's connection before the child call and owns a retained
`WeftClient` across that call. The callee submits and waits in
`../mm-governance/agents/monitoring/llm_call.py::call_structured_llm_task`.
The fixture models those Weft lifetimes and substitutes timed work for the
application preparation and provider response. It uses successful child
calls. Its results characterize the local runtime and this workload.

Consulted `docs/program-theory.md` [WT-4.2], [WT-4.3], [WT-4.4],
[ALT-WT-001], [ALT-WT-017], the agent-context README, decision hierarchy,
principles, engineering principles, testing and review runbooks, and lessons.
The test-audit authoring gate favors one burst path with two sizes. Existing
`tests/helpers/family_workload.py` intentionally synchronizes phases and
materializes pools to prove a controlled pressure envelope. The new workload
lets launches and results proceed at natural timing through real Weft APIs.

## Implementation

1. Add two function targets in `tests/helpers/admission_workload.py` (moved
   on 2026-11-09 to `benchmarks/admission_workload.py`). One
   rollup submits 50 or 87 enrichment tasks at a nominal 50 ms spacing and
   returns their TIDs immediately after the last submission. Each enrichment
   models 2.65 s of preparation before it submits one leaf, then waits for
   the real result within the same `connect()` scope. Reuse `admission_work`
   as the leaf. The test draws one `Random(20261107).randint(16, 28)` per
   enrichment index and passes that value through both tasks. Enrichments and
   the rollup report to a shared test-owned outbox. The rollup returns its
   accepted enrichment TIDs in index order. Each enrichment returns its
   index, leaf TID, and process CPU time and elapsed time around
   `child.result`. Leaves use the default per-task outbox. Total launched
   tasks including the rollup and leaves are 101 or 175.
2. Extend `tests/system/test_admission_stress.py` (moved on 2026-11-09 to
   `benchmarks/test_admission_stress.py`) with one shared slow test
   parameterized by these two sizes. Start a real Manager, register the outbox
   monitor, then submit the rollup through `weft.client.connect`. Use the
   existing SQLite harness and isolated PostgreSQL server fixture. The
   fixture sets `WEFT_ADMISSION_MAX_CONNECTIONS=300` on PostgreSQL, matching
   the verified ops configuration and the test server's native limit. It
   selects zero on SQLite and sets `WEFT_ADMISSION_RESERVE_FRACTION=0.1`.
   Use a 600 s absolute deadline for outputs and drain, and the regular
   concurrent runner.
3. Parameterize the existing output collector's count. Expect 51 or 88 rows
   in the ops outbox, and inspect deepest-chain evidence only during the
   deep workload's held-pressure phase. Keep the same producer drain boundary.
   Use the rollup's returned accepted TIDs and
   each enrichment's returned leaf TID to assert unique indices, exact
   lineage and one launch, start and successful task-owned completion per
   expected TID. Verify no surplus outputs and zero PostgreSQL slot rejection
   signatures after harness cleanup and before removal of the server.
4. Generalize the workload log scan to accept a set of function targets and
   retain launch-return, start and completion timestamps. The existing deep
   workload supplies its original single target, and the ops test supplies
   the rollup, enrichment and leaf targets. `task_spawned.launched_ns` is process-start
   return, and `work_started.timestamp` is the wrapper's work-start event.
   For enrichments and leaves separately report median and p95 of TID time to
   process-start return and process-start return to work start. The TID's
   hybrid counter changes its low bits only. Report median and p95 of
   TID-to-work-start directly too. Use `int(tid)` in nanoseconds for
   subtraction, with a sub-5 μs low-bit counter contribution. The first
   interval includes launch selection, admission, and the process-start call.
   Report `(max(enrichment_completion_ns) - min(enrichment_tid_ns)) / 1e9`,
   sampled peak connections and
   work overlap, and enrichment worker CPU/wall time during result waits.
   CPU evidence covers the function worker during the wait. Keep wall-time
   metrics descriptive and keep existing launch/refusal checks for the deep
   workload.

## Implementation mapping delta

Under `docs/specifications/08-Testing_Strategy.md` [TS-0], extend the existing
admission test mapping with the sentence below. This is an implementation
note on baseline `b6d850c1` plus the nested admission mapping.

> `test_ops_burst_admission` models a rollup with 50 or 87 enrichments that
> each await one leaf. It reports submission-to-start phases and retained-client
> result-wait CPU on both backends. See the
> [ops-shaped admission burst plan](../plans/2026-11-07-ops-shaped-admission-burst-plan.md).

## Verification

```bash
. ./.envrc
.venv/bin/python -m pytest -m slow benchmarks/test_admission_stress.py -k ops_burst
PYTEST_ADDOPTS=-rP ./bin/pytest-pg benchmarks/test_admission_stress.py::test_ops_burst_admission
.venv/bin/python -m pytest tests/specs/test_plan_metadata.py tests/specs/test_test_audit_policy.py
bin/check-doc-paths
git diff --check
```

Compare the PostgreSQL cases with the same test, fixture, configuration, and
dependencies in an isolated `v0.9.122` checkout. `git show v0.9.122:weft/core/manager.py`
confirms that the release publishes `scheduler_frontiers`, `submitter_tid`
and `launched_ns`, and uses the native PostgreSQL ceiling when configured
with zero. Apply only test-side files to the release checkout. Report exact
metrics, along with any inability to run a matched control. Check Ruff,
formatting, and mypy. Require an independent plan and completed-work review.
The existing deep workload
provides a separate capacity-stress witness and a calibrated native-rejection
oracle. The new test's distinct evidence is progress and latency through a
single producer's burst, including after the producer's terminal event.

## Review and execution record

Plan baseline `b6d850c1` with the nested admission workload and its runtime
changes. Record scope deviations, review dispositions and measurements here.

Gemini 4 Argon Medium plan review. Accepted the explicit collector count,
multi-target scan, mapping backlink, clock arithmetic and deterministic
configuration findings. Checked the tag's actual source and declined the
proposed older-release fallbacks. The named evidence and automatic capacity
path are present in that release.

The first PostgreSQL attempt stopped during fixture validation. Add explicit
per-task control routes to the shared-outbox TaskSpecs and verify via the public
client normalizer. Both corrected cases passed with automatic native capacity.
The November 6 watch recorded `WEFT_ADMISSION_MAX_CONNECTIONS=300` on ops.
Use the explicit ceiling in the matched cases to exercise the deployed path.

Keep measured admission active at the configured 300 ceiling for all
PostgreSQL comparisons. The isolated server enforces the same native cap.
Use retained launch timing evidence to locate delay and the rejection oracle
to validate each run.

Gemini plan re-review passed after the tag's source and mapping were supplied.
The independent completed-work review found no blocker. Ruff, formatting,
mypy, plan metadata, test audit and document path checks passed.

The regular runner collected four slow cases on SQLite and six shared slow
cases on PostgreSQL in the admission stress module. Both new cases passed on
each backend and on the released PostgreSQL control. Each PostgreSQL run
verified exact work and zero native slot rejections at 300. Timings start at
the first enrichment TID and end at the last enrichment terminal event.

| Build and backend | Tasks | Elapsed s | Enrichment start p50/p95 s | Sampled PG peak |
| --- | ---: | ---: | ---: | ---: |
| v0.9.122 PostgreSQL | 101 | 68.916 | 9.738 / 31.015 | 248 |
| Current PostgreSQL | 101 | 69.541 | 9.529 / 31.241 | 250 |
| Current SQLite | 101 | 57.647 | 7.265 / 22.756 | — |
| v0.9.122 PostgreSQL | 175 | 92.976 | 28.192 / 56.096 | 246 |
| Current PostgreSQL | 175 | 92.147 | 27.343 / 54.670 | 244 |
| Current SQLite | 175 | 84.995 | 21.726 / 48.186 | — |

The 175-task current PG run spent a median 26.906 s from accepted TID to
launch return and 0.485 s from launch return to wrapper work start. It launched
64 enrichments after the rollup's terminal event. Only startup-feedback
refusals appeared in either regular PG build's checkpoints. The v0.9.122
enrichment workers used 11.702 CPU s across 2,233.060 aggregate s waiting for
child results. Current workers used 17.277 CPU s across 2,265.204 aggregate s.
These measure the worker's wait path on this host.

A separate 175-task diagnostic retained the built-in Manager phase spans and a
Manager-only call profile using a temporary pytest diagnostic plugin. It kept
the same 300 ceiling and oracles. It completed in 82.896 s, sampled a peak of
278 sessions and had zero native rejections. During 55.341 s with accepted
enrichment work awaiting launch return, Manager turns accounted for 35.657 s
and its activity-wait path for 19.337 s. Inclusive spans included child cleanup
9.632 s, scheduler checkpointing 9.813 s, wait setup 6.507 s and admission
checks 1.501 s. There were 2.602 s of activity-wait spans ending with the
capacity block active. Phase spans can nest and the launch worker overlaps the
reactor, so these durations cannot be added across categories. The instrumented
Manager recorded 17,328 PostgreSQL cursor executions, 741 scheduler checkpoint
calls, 55,572 child-exit checks and 2,024 backend-plugin lookups. The profile
guides further diagnosis. Its time and call counts include instrumentation and
the Manager lifetime around the burst.
