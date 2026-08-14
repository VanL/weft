# External Log Writer Lease Canonicalization Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [IMPL.11]; docs/specifications/01-Core_Components.md [CC-2.2.1]
Superseded by: none

Class: 4 - risky. This restores the existing single-rotation-owner contract
inside a concurrent persistence path whose failure can lose or corrupt
operator-facing log output.

## Goal

Ensure concurrent `ExternalTaskLogSink` facades for one resolved path lease one
process-local `_PathWriter`. In particular, an actively leased identical
lexical alias must not split into a second writer when Windows resolution
identity changes while the first writer creates the file.

## Evidence and Root Cause

- GitHub Test run `31812585750`, Windows Python 3.14 core-commands job
  `94806520333`, failed
  `test_external_task_log_same_path_concurrent_facades_rotate_complete_rows`.
- Three same-input-path facades acquired more than one `_PathWriter`; their
  separate `RotatingFileHandler` instances raced the same backup rename and
  raised `FileNotFoundError [WinError 2]` for
  `concurrent.jsonl.2 -> concurrent.jsonl.3`.
- `_acquire_path_writer()` currently calls `Path.resolve(strict=False)` before
  taking `_PATH_WRITER_REGISTRY_LOCK`. CI proves that concurrent calls returned
  unequal registry keys for one physical target and admitted multiple writers.
  The first thread creating the target before a later resolution changes its
  Windows spelling is the leading mechanism, supported by the extended path in
  the rollover error, but CI did not capture both keys directly.

## Source Documents and Baseline

- `docs/specifications/07-System_Invariants.md` [IMPL.11] requires same-path
  facades to lease one process-local writer and rotation owner.
- `docs/specifications/01-Core_Components.md` [CC-2.2.1] maps that ownership to
  `weft/core/monitor/external_log.py`.
- Spec baseline: commit `5f8687e172024022cac037a39ff6a054f1093753`.
- No spec delta is required; the observed behavior violates the existing
  contract.

## Invariants and Boundaries

- One active logical path has exactly one `_PathWriter`, handler, lock, and
  rotation owner per process.
- Lexically identical paths must not be resolved again while an active lease
  exists. Equivalent aliases that resolve to the same path must still
  coalesce.
- Once a lexical alias is actively leased, it remains attached to that writer
  even if a symlink in the alias is retargeted. A fresh acquisition after the
  final release resolves the current target.
- The public sink path remains an absolute resolved `Path`.
- Facade-local health and emission counters remain independent.
- The final lease removes every registry identity before closing the writer;
  close failure must not leave poisoned lease state.
- No cross-process file locking, logging format change, rotation policy
  change, timeout increase, or SimpleBroker change belongs in this fix.

## Implementation

1. Add a stable lexical absolute alias identity for active leases. Under the
   existing registry lock, check that identity before filesystem resolution.
2. For a new alias, release the registry lock, resolve the existing canonical
   key, reacquire the lock, and recheck the alias before coalescing or creating
   a writer. Retain `Path.resolve()` output as-is; do not strip Windows
   extended-path prefixes or otherwise add a broader normalization policy.
3. On final release, remove the canonical writer and all aliases before
   closing its handler, but retain the registry lock through `writer.close()`
   so a replacement handler cannot overlap the old one.
4. Add a deterministic regression proving an active same-input alias resolves
   only once and resolves afresh after final release. Preserve coalescing for
   distinct lexical aliases such as `nested/../same.jsonl` and `same.jsonl`.
   Add a barrier-controlled concurrent-first-acquire regression for the second
   alias lookup and a blocking-close regression proving replacement cannot
   overlap final close. Make the concurrent rotation test report worker
   exceptions and writer identities directly.
5. Update the runtime-global allowlist for any added registry and retain the
   existing spec-to-code mapping.

## Hardening

- Hidden coupling: handler creation makes the target start existing, which can
  change later path resolution. Double-check the stable alias under the
  registry lock before and after resolution so that transition cannot admit a
  second writer. Resolution remains outside the global lock because it may
  perform slow filesystem or network-volume I/O.
- Failure containment: registry entries are removed before final handler
  close while the registry lock remains held, preserving the existing ability
  to reacquire after flush/close failure without overlapping handlers.
- Anti-mocking boundary: the concurrent rotation test continues to use real
  files and real `RotatingFileHandler` rollover. Instrumentation is limited to
  controlled resolution results for the two alias races and test-local wrappers
  around the registry lock and final close for the serialization proof.
- Rollback: revert the fix commit and retag only if all publication workflows
  remain unpublished. After publication, correct forward with a patch release;
  do not restore the known multi-writer race.
- Stop condition: if lexical identity cannot coalesce same-input paths without
  regressing resolved aliases, or if the failure requires cross-process file
  locking, stop and revise the plan.

## Verification

1. Focused deterministic and concurrent tests, including repeated runs.
2. Full `tests/core/test_monitor_external_log.py` and runtime-global checks.
3. Ruff check/format and mypy for the touched module.
4. Full local release preflight through
   `uv run python bin/release.py --retag` for version 0.9.96.
5. Exact-tag GitHub Test and Release Gate workflows through successful PyPI
   publication.

## Review

- Independent pre-implementation review: completed 2026-08-14. It blocked the
  first draft because it underspecified final-close locking, proposed unsafe
  broad Windows path normalization, and held the global registry lock across
  filesystem resolution. All three findings are incorporated above through
  lock-retained close, unchanged `Path.resolve()` canonical keys, and the
  double-checked alias algorithm.
- Independent completed-work review: completed 2026-08-14. It required firing
  tests for the post-resolution alias recheck and lock-retained final close,
  plus narrower contract wording. After those changes, re-review found no
  implementation or regression-test issue.

## Verification Results

- Full external-log, runtime-global, and plan-metadata tests: 126 passed.
- Concurrent first-acquire, blocking final-close, and real rotating-writer
  tests: 25 isolated three-test repetitions passed; the real rotating-writer
  test also passed 50 earlier isolated repetitions.
- Ruff check and format check: passed for all touched Python files.
- Mypy: no issues in `weft/core/monitor/external_log.py`.
- Backstitch: completed with the repository's known aggregate debt (27 errors,
  988 warnings); no new issue points at either new regression test or this
  plan.

## Deviation Log

| Planned behavior | Actual behavior | Rationale |
| --- | --- | --- |
| One writer per resolved path with stable active-alias identity | Implemented as planned | No deviation. |
