# Owner-Thread Topology Mutation Plan

Status: completed
Source specs: docs/specifications/07-System_Invariants.md [QUEUE.7], [QUEUE.8]; docs/specifications/01-Core_Components.md [CC-2.1]
Superseded by: none

Class: 5. This changes permitted owner-thread behavior and the normative
[QUEUE.8] contract. Hardening applies because the public mutation contract and
runtime resource lifecycle are involved. Review corrected the original Class 3
claim on 2026-09-21. `BaseTask` stays construction-fixed under [QUEUE.7].

## Goal and Source Documents

Enable a standalone watcher's drive owner to refresh membership between dispatch
passes, using the existing topology transaction and preserving foreign requests.
The governing sources are [System Invariants](../specifications/07-System_Invariants.md)
[QUEUE.7], [QUEUE.8] and [Core Components](../specifications/01-Core_Components.md)
[CC-2.1]. Read the repository decision hierarchy, engineering principles, and
the writing-plans, hardening-plans, testing-patterns, and review-loops runbooks
in `docs/agent-context/` before changing this boundary.

## Spec Baseline

Plan type: implementation with spec revision.

Baseline: `c2a724246a2d8b93a8a1859938019bf6fa5b30f7`, specifically
`docs/specifications/07-System_Invariants.md` [QUEUE.8] and
`docs/specifications/01-Core_Components.md` [CC-2.1]. The former contains the
owner rejection; the latter supplies the watcher dispatch contract.

Promotion strategy: B, atomic spec and implementation change. The original
implementation and spec delta arrived together for review. Review corrections
promote the exact wording below before adding coverage. Promotion baseline:
the [QUEUE.8] spec delta against that SHA plus the 2026-09-21 review correction
recorded below. Verification is against the promoted spec, not this proposal.
The corrected spec's SHA-256 is
`34d87a68389ab1e85fb4465d17de435ac02567785f145472e62f6956622a9988`;
combine that digest with the diff base above to identify the promotion baseline.

## Context and Files

`weft/core/tasks/multiqueue_watcher.py` owns admission, transactions, dispatch,
and stop. Reuse `_apply_topology_mutation_on_owner()` and the shared transaction
wrapper; do not introduce another topology implementation. The relevant tests
are in `tests/tasks/test_multiqueue_watcher.py`; the PostgreSQL test wraps the
real waiter to observe native wakeups. Read both before editing.

Touched documentation: this plan, `docs/plans/README.md`,
`docs/specifications/07-System_Invariants.md`, and `CHANGELOG.md`. The original
helper extraction also updates `docs/ruff-suppression-registry.md` and its
inventory test, `tests/specs/test_ruff_policy.py`.

Comprehension checks: why would an owner request waiting on the mutation deque
deadlock? Why must waiter replacement precede closing displaced resources?

## Invariants and Rollback

- One drive owner applies topology effects. Foreign requests remain FIFO and
  may queue during an owner transaction. An owner request is synchronous;
  reentrant owner calls and calls within dispatch are rejected before effects.
- Preserve stop/commit serialization, exact waiter signatures per generation,
  replacement-before-close, fallback polling, and SIGINT error precedence.
  Candidate resources remain rollback-owned until publication. Ordinary
  membership errors return to the caller; infrastructure and fatal failures
  keep their existing retry/unwind behavior.
- BaseTask topology, TaskSpec immutability, queue names, reservation policy,
  task state transitions, and persistence formats do not change. No new
  dependency, CLI, migration, or second execution path is needed.

Rollout: land the spec, implementation, and tests together, then pin that
revision in Taut before enabling owner refresh there. Rollback: first disable
or revert the downstream owner-refresh caller, then revert the owner admission
change with its spec and tests. Foreign mutation remains available; there is
no data migration or one-way persistence change. Runtime success is membership
refresh followed by real queue delivery with the new native wait signature.

## Review Correction Slices

1. Correct classification, spec wording, and traceability. Stop if the correction
   would reject foreign requests or change BaseTask's fixed topology.
2. Add real-broker reentrancy coverage and exercise the existing native waiter
   test from both foreign and owner callers. Keep Queue, dispatch, and PostgreSQL
   LISTEN/NOTIFY real; use wrappers only to observe or inject nested calls.
   Stop if coverage needs a replacement broker or a second transaction path.
3. Run the gates below and independent review; record evidence and dispositions.
   The tests characterize an already-working implementation (testing-patterns
   Rule 5): prove the behavior now and demonstrate failure with the relevant
   guard or owner admission disabled, restoring the source immediately.
   Do not claim completion while review or verification failures remain.

## Problem

`MultiQueueWatcher.add_queue()` / `remove_queue()` exist so a standalone watcher
can change its queue set while running. Today only a foreign thread can use
them: `_submit_topology_mutation()` rejects the drive owner with
`RuntimeError("drive owner cannot mutate topology during dispatch")`. That raise
is not a policy; it exists because the only owner-side path (append to the
mutation deque, then `request.done.wait()`) would self-deadlock, since the
owner is the only thread that drains that deque.

The owner is the natural mutator for a watcher whose membership is derived from
durable state it reads itself (Taut's `TautWatcher` refreshes memberships at the
top of its drain). The owner rule must be "not during a dispatch pass", not
"never".

## Change

1. `_submit_topology_mutation()` gains an owner branch. When the caller is the
   drive owner and no dispatch pass is active, the request is applied
   synchronously through the same `_apply_topology_mutation_on_owner()`
   transaction the apply loop uses, with the same error and SIGINT-critical
   finish handling, and its error is raised to the caller. A reentrant call
   while a topology transaction is already in flight is rejected.
2. A dispatch-pass flag is set only around the round-robin / priority passes in
   `_drain_queue()`. Owner mutation inside a pass (for example from a handler)
   remains rejected before effects, because the pass iterates `_active_queues`
   and a handler may hold the Queue object a remove would close. Subclass
   pre-drain work runs outside the flag and may mutate.
3. Shared transaction body: the apply loop and the owner branch call one helper
   so error precedence, `_topology_inflight` bookkeeping and
   `_finish_topology_sigint_critical()` are identical on both paths.

## Proposed Spec Delta

Replace the opening of [QUEUE.8], through "rejected before effects", with:

> **QUEUE.8**: a running standalone `MultiQueueWatcher` has one drive owner for
> dynamic topology effects. Foreign `add_queue()` and `remove_queue()` calls
> are synchronous requests applied in a deterministic linear order between
> wait and drain phases; foreign requests may queue while a topology
> transaction is in flight. The drive owner may mutate topology synchronously
> between dispatch passes through the same transaction, which is how a
> standalone watcher refreshes membership from durable state it reads itself;
> owner mutation during a dispatch pass, and reentrant owner mutation while a
> topology transaction is in flight, is rejected before effects.

The remainder of [QUEUE.8] is unchanged. After promotion the spec is canonical.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|
| [QUEUE.8] | Reject owner calls during drive | Admit owner calls between passes | Enables durable membership refresh without a self-wait | Proposed Spec Delta above, promoted with implementation |
| [QUEUE.8] | Initial promotion said any in-flight mutation is rejected | Foreign requests queue; only owner reentrancy is rejected | Initial wording was broader than the plan and implementation | Review correction above, promoted before coverage changes |

## Review Dispositions

2026-09-21 independent review found no concrete runtime defect. Accepted:
F1 narrow the spec rejection to owner reentrancy; F2 add a firing reentrancy
test; F3 correct classification and plan evidence. Also accepted the coverage
observation: exercise native rebinding through owner admission, not only through
the foreign-request deque. Correction review and current gate evidence follow
in the verification record.

## Tests

- owner-thread add from subclass pre-drain work: applied in the same turn, new
  queue dispatches, waiter re-signatured (SQLite; PostgreSQL through the
  existing native-rebind test shape);
- owner-thread remove: queue removed and closed in the same turn;
- owner-thread duplicate add raises `ValueError` synchronously and leaves
  topology unchanged;
- nested owner add and remove both raise before effects during candidate
  construction; the outer add still dispatches and clears in-flight ownership;
- real PostgreSQL native rebinding runs for both owner and foreign admission,
  including delivery after add and removal of the old subscription;
- existing handler-time mutation test still rejects;
- existing foreign-thread, stop-race and SIGINT-probe tests unchanged.

## Verification

```bash
. ./.envrc
./.venv/bin/python -m pytest tests/tasks/test_multiqueue_watcher.py tests/tasks/test_signal_deferral.py tests/specs -q
bin/pytest-pg --fast tests/tasks/test_multiqueue_watcher.py
./.venv/bin/ruff check . && ./.venv/bin/ruff format --check .
./.venv/bin/mypy weft tests bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
git diff --check
```

The author owns formatting. Mutation checks run alone and restore the exact
source in a `finally` block. SQLite, PostgreSQL, and static verification may
run concurrently only after restoration. Spec traceability is checked by
`tests/specs/test_spec_hygiene.py` plus inspection of the [QUEUE.8] implementation
mapping and reciprocal plan link. No backstitch executable or configuration is
present in this checkout or loaded PATH; no backstitch pass is claimed.

## Verification Record

- Independent correction-plan review: PASS (native same-family reviewer).
  Accepted its request to use full repository mypy scope and record the actual
  traceability gate available here.
- Characterization proof: both nested mutation cases pass with the existing
  guard and fail when that guard is temporarily disabled (pytest exit 1).
  The PostgreSQL owner case fails with the original blanket owner rejection
  temporarily restored (one failing test; xdist stop-on-failure exit 2).
  Both experiments restore the original implementation byte-for-byte.
- Current SQLite watcher, signal-deferral, and complete spec-test selection:
  passed, with the two PostgreSQL-only parameter cases skipped.
- PostgreSQL watcher selection: 80 passed, including owner and foreign native
  add/remove rebinding and both nested owner mutation cases.
- Full repository mypy: passed, 435 source files. Full repository Ruff check
  passed; formatting passed for all 759 files; `git diff --check` passed.
- Independent correction review identified a test-only ordering risk: the
  membership-refresh trigger could commit after the new native subscription
  was installed and contaminate wakeup assertions. An explicit event now orders
  trigger commit before owner mutation. PostgreSQL rerun: 80 passed. Independent
  round-two review: PASS, no new defect. Author fresh-eyes review agrees; the
  correction retains the initial runtime implementation exactly.
- Full application suite is outside this correction's verification scope;
  no full-suite pass is claimed. No permanent runtime implementation change
  was required to resolve the review findings.

Landing: completed for the 0.9.105 release with user authorization.

## Downstream

Taut's reactor restoration plan (F15) pins the Weft revision that carries this
change so its owner-thread membership refresh works under the exact copy.
Recorded 2026-09-22: Taut pins `9fc913c1` (0.9.105), whole-file SHA-256
`3afa84fc7998644cc63d40374fad9326236b73ba3e408a86086a6dc33089da89`, and marks
F15 resolved upstream. The file is unchanged in 0.9.106 (`cfa5bb21`). The
[QUEUE.8] implementation mapping now carries this downstream note.
