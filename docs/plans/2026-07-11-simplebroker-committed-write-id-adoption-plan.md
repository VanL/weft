# SimpleBroker Committed Write ID Adoption Plan

Status: completed
Source specs: docs/specifications/05-Message_Flow_and_State.md [MF-1]; docs/specifications/07-System_Invariants.md [MANAGER.4]
Superseded by: none

## Goal

Adopt SimpleBroker 5.3.1's `Queue.write()` return value for ordinary spawn
submission so the spawn request and its task ID are committed atomically. This
removes the high-contention failure caused by preallocating an ID through
`Queue.generate_timestamp()` and then inserting the request separately, while
preserving exact-ID submission for callers of the shared helper that already
own a TID, including the CLI's compiled-pipeline path.

## Source Documents

- [`docs/specifications/05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-1]: the spawn-request message ID becomes the task TID and the manager
  expands the queued TaskSpec.
- [`docs/specifications/07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [MANAGER.4]: the spawn-request message ID becomes the child task TID.
- [`docs/agent-context/engineering-principles.md`](../agent-context/engineering-principles.md)
  sections 3, 4, 4.1, and 4.2: normalize once, keep real broker proof, prove the
  regression red first, and update all consumers together.
- SimpleBroker 5.3.1 `Queue.write()`: returns the committed row's exact message
  ID after allocating it inside the write transaction.

## Spec Baseline

- `403bcc77584ae91f291f8ab6870dab449f1d0413` —
  `docs/specifications/05-Message_Flow_and_State.md` [MF-1] and
  `docs/specifications/07-System_Invariants.md` [MANAGER.4] at plan authoring.
- Plan type: implementation without a spec revision. The observable contract
  does not change; this fixes the implementation of the existing contract.

## Context and Key Files

Files to modify:

- `weft/core/spawn_requests.py`: canonical spawn payload preparation and queue
  write owner.
- `weft/commands/run.py`: CLI wrapper that currently preallocates every missing
  TID before calling the canonical helper.
- `weft/commands/submission.py`: public client wrapper with the same
  preallocation pattern.
- `tests/core/test_spawn_requests.py`: focused real-broker regression for the
  committed write ID contract.
- `tests/core/test_manager.py`: template-to-resolved proof at the manager
  boundary, including queue-name rewriting and bundle-root preservation.
- `tests/commands/test_submission.py`: receipt-level proof that the client uses
  the ID returned by submission when its TaskSpec has no TID.
- `tests/commands/test_dump_load.py`: update the existing implicit spawn export
  assertion from a pre-resolved payload to the preserved template payload.
- `tests/conftest.py`: register both new shared test modules in the explicit
  backend-audit classification table.

Read first:

- `weft/core/manager.py` `_handle_work_message` and `_build_child_spec`: the
  manager resolves the child TaskSpec against the consumed message timestamp.
- `weft/core/pipelines.py` and `weft/core/tasks/pipeline.py`: pipeline runtime
  work preallocates IDs because those IDs are embedded in a compiled graph.
- `weft/core/manager_runtime.py` `_generate_tid`: manager identity is allocated
  before any spawn request exists and is not part of this change.
- `weft/core/manager.py` `_enqueue_managed_service_request`: manager-owned
  autostart services already call `Queue.write()` directly. This path does not
  call `submit_spawn_request()` and is not changed by this dependency-adoption
  slice. For autostart pipelines, the compiled payload carries a precompiled
  pipeline TID while the direct write commits another spawn-row ID;
  `_build_child_spec()` makes the row ID authoritative for the outer child.
  That pre-existing mismatch is not an exact-ID guarantee and is not created or
  fixed here. Its existing real autostart-pipeline test remains a regression
  gate; correlation semantics need a separate investigation if changed.

Comprehension checks before editing:

1. Which value is authoritative when a queued TaskSpec contains another TID?
   Answer: the consumed spawn-request timestamp, applied by
   `Manager._build_child_spec`.
2. Which submissions must retain exact-ID insertion? Answer: any caller that
   passes the `tid=` keyword to `submit_spawn_request()`, including compiled
   pipeline stage/edge work and explicit user-supplied TaskSpec IDs. A `tid`
   present only inside a mapping does not select exact insertion; this preserves
   current helper precedence, which replaces it with the allocated message ID.

Shared path, do not duplicate:

- `submit_spawn_request()` remains the only queue-write owner for ordinary CLI,
  client, and compiled-pipeline submissions that already use the shared helper.
  Manager-owned service scheduling has a separate existing direct-write owner
  and stays out of this diff.
- `resolve_taskspec_payload()` remains the only runtime TaskSpec expansion
  path; ordinary implicit-ID submissions must queue a template and let the
  manager resolve it from the committed message ID.
- `_taskspec_payload_for_spawn()` remains the single payload-copy path. Both
  branches must keep `apply_bundle_root_to_taskspec_payload()`; the implicit
  branch must also retain or inject `spec.weft_context` before queueing.

## Invariants and Constraints

- The committed `weft.spawn.requests` or `weft.spawn.internal` message ID stays
  identical to the returned TID and to the manager-resolved child TID.
- TIDs remain immutable, broker-valid 19-digit hybrid timestamps.
- Queue-first submission, rollback deletion, reserved policy, state
  transitions, public/internal metadata stripping, and per-task queue naming
  do not change.
- Explicit `tid=` keeps exact-ID insertion. Do not convert pipeline or manager
  identity allocation to ordinary queue writes.
- Only the `tid=` argument selects exact insertion. If a direct mapping contains
  an embedded `tid` but the argument is omitted, the write is implicit and the
  manager overwrites the embedded value and default `T{tid}` queues from the
  committed row ID, matching current precedence.
- Implicit-ID submission writes exactly one spawn request. Do not add an
  allocation-marker queue, placeholder message, or second execution path.
- Do not add compatibility branching for SimpleBroker versions whose
  `Queue.write()` returns `None`; the dependency update is intentional and the
  code moves forward with the new contract.
- No dependency, public CLI, payload-schema, queue-name, or spec change.
- Keep real SQLite-backed queue behavior in the focused regression. A narrow
  patch may guard against calling `Queue.generate_timestamp()`, but it must not
  replace the real write/read assertion.
- Rollback is a source revert. Implicit queued payloads change state from
  pre-resolved TaskSpecs to TaskSpec templates, although their JSON envelope
  shape is unchanged. The current and rollback manager reader are compatible
  because `Manager._build_child_spec()` already calls
  `resolve_taskspec_payload(..., tid=str(message_timestamp))` for every request.
  The manager-boundary regression must prove this before rollout; there is no
  one-way door.
- Rollout requires SimpleBroker 5.3.1 before or with this Weft change. Old and
  new manager processes may coexist with pending templates because both use the
  same timestamp-authoritative `_build_child_spec()` path.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|----------|------------------|-----------------|-----------|---------------|

## Tasks

1. Add a red regression for implicit and explicit submission.
   - Add `tests/core/test_spawn_requests.py` with `pytestmark =
     [pytest.mark.shared]` and the backend-neutral `weft_harness` fixture.
   - Add both new test modules to `tests/conftest.py` `_SHARED_MODULES`; the
     module mark and audit table are both required.
   - For implicit submission, assert the returned ID equals the timestamp read
     from the spawn queue, the payload is a valid template, and preallocation
     through `Queue.generate_timestamp()` is not used.
   - For explicit submission, assert the supplied ID remains the queued message
     ID and the payload remains resolved for that ID.
   - Add a direct-mapping case with an embedded stale TID but no `tid=` argument
     to lock the argument-precedence rule.
   - Stop if the test needs to mock the actual queue write. A narrow guard that
     fails on `Queue.generate_timestamp()` is allowed only alongside the real
     write/read assertion.
   - Verify red against the current code with the focused pytest node.

2. Make the canonical helper choose the correct write mode.
   - In `weft/core/spawn_requests.py`, prepare a template payload when `tid` is
     absent, serialize it once, and return `queue.write(message_json)`.
   - Keep the current resolve plus exact-ID insert path when `tid` is present.
   - Reuse one payload-copy step for both branches. Preserve
     `_weft_bundle_root` with `apply_bundle_root_to_taskspec_payload()` and
     preserve `inherited_weft_context` by setting the template's
     `spec.weft_context` only when absent.
   - Preserve metadata trust-boundary stripping in both branches.
   - Stop if implementation wants a reservation queue, retry loop, or manager
     payload change.
   - Verify the focused spawn-request tests pass.

3. Remove unnecessary wrapper preallocation.
   - In both wrappers, pass only `taskspec.tid`, capture
     `submitted_tid = submit_spawn_request(...)`, and use the returned value
     unconditionally for reconciliation, waits, and receipts.
   - For explicit IDs, assert in tests that the helper returns the supplied ID.
   - Add or update the client receipt test in
     `tests/commands/test_submission.py`.
   - Stop if any compiled-pipeline or manager-runtime allocator becomes part of
     the diff.

4. Prove manager resolution and update dump/load expectations.
   - In `tests/core/test_manager.py`, submit an implicit template through the
     real broker, consume its body and timestamp, and pass them through the
     real `Manager._build_child_spec()` path. Assert the returned submission
     ID, child `tid`, inbox, outbox, both control queues, and any rewritten
     embedded TID all equal the committed message ID.
   - Give that TaskSpec a real bundle root and explicit inherited context;
     assert the resolved child retains both.
   - In `tests/commands/test_dump_load.py`, keep the exact imported row-ID
     assertion but expect the imported `taskspec` to remain a template. Resolve
     it through the same manager boundary or shared resolver before asserting
     the runtime TID.
   - Do not weaken dump/load's exact row-ID preservation contract.

5. Re-run the reported long-session regression and close traceability.
   - Run the exact reported test with the repository virtualenv.
   - Run
     `test_manager_autostart_pipeline_target_launches_pipeline_run` unchanged
     to prove the separate direct manager `Queue.write()` path still works; do
     not bring that path into this diff.
   - Run focused spawn, run-command, submission, and long-session tests; then
     the full fast suite, mypy, and ruff.
   - Confirm the MF-1 implementation mapping still names
     `submit_spawn_request`; no mapping ownership changes.
   - Record independent review findings and resolve each before completion.

## Testing Plan

- Red-green proof: focused real-broker implicit submission test. Before the
  fix it fails because the current path calls `Queue.generate_timestamp()`;
  after the fix it proves the returned committed write ID is the queued row ID.
- Wrapper proof: a prepared TaskSpec without a TID returns a receipt whose TID
  is the canonical helper's returned ID and passes that same ID to manager
  reconciliation.
- Existing explicit-ID and internal spawn tests protect compiled/internal
  callers and trust-boundary metadata.
- The real manager-boundary test protects template expansion, stale embedded
  TID replacement, per-task queue rewriting, bundle root, and inherited
  context. Do not substitute direct resolver-only assertions for this proof.
- The dump/load test continues to prove exact message-ID preservation while
  adopting template payload state.
- `WeftTestHarness` long-session test remains the real multi-process contention
  proof. Do not replace it with a mock-only concurrency test.
- Edge case retained in scope: TaskSpecs with an explicit TID must not switch to
  auto-allocation, because pipeline graphs embed those IDs before submission.

## Verification and Gates

Per-task:

```bash
./.venv/bin/python -m pytest tests/core/test_spawn_requests.py -q -n0
./.venv/bin/python -m pytest -m "" tests/core/test_manager.py -q -n0 -k 'resolves_implicit_spawn_from_committed_message_id or autostart_pipeline_target'
./.venv/bin/python -m pytest tests/commands/test_submission.py -q -n0
./.venv/bin/python -m pytest tests/commands/test_dump_load.py -q -n0 -k spawn_request_message_id
./.venv/bin/python -m pytest -m "" tests/cli/test_cli_long_session.py::test_cli_long_session_produces_identical_transcript_across_backends -q -n0
```

Final:

```bash
./.venv/bin/python -m pytest
./.venv/bin/mypy weft bin extensions/weft_docker extensions/weft_macos_sandbox
./.venv/bin/ruff check weft tests/core/test_spawn_requests.py tests/commands/test_submission.py
./.venv/bin/python -m pytest tests/specs/test_plan_metadata.py -q -n0
```

Success is one durable spawn row per submission, with its committed message ID
returned as the public TID, plus no `exhausted retries` failure in the reported
long-session test.

## Independent Review Loop

- Reviewer: another Codex agent, because no different agent family is exposed
  in this session.
- Review the plan, [MF-1], [MANAGER.4], `weft/core/spawn_requests.py`, both
  command wrappers, and the proposed tests.
- Stance: find contract drift, template/resolved TaskSpec mistakes, missed
  explicit-ID callers, and mock-heavy verification. State whether a
  zero-context engineer could implement the plan correctly.
- Feed each finding back into the plan or record why it is rejected before
  implementation.

## Out of Scope

- Changing SimpleBroker timestamp retry policy.
- Refactoring pipeline compilation or manager identity allocation.
- Changing or legitimizing the pre-existing manager-autostart pipeline
  compiled-TID/spawn-row-ID mismatch. That requires a separate root-cause and
  contract investigation.
- Changing spawn payload shape, queue names, manager reservation behavior, or
  CLI output.
- Supporting SimpleBroker versions older than 5.3.1 on the new path.

## Review Findings and Resolution

- Accepted: make bundle-root and inherited-context transport explicit; add a
  manager-boundary proof.
- Accepted: update the existing dump/load implicit-submission assertion rather
  than silently weakening it.
- Accepted: define `tid=` argument precedence and require wrappers to use the
  helper's return value unconditionally.
- Accepted: classify the new test as `shared` and use the repository's
  backend-neutral harness.
- Scoped with evidence: manager-owned autostart uses a separate direct
  `Queue.write()` path that already has a real end-to-end pipeline test. Its
  compiled pipeline TID can differ from the authoritative spawn-row/outer-child
  TID today; this plan makes no exact-ID claim for that path, adds the unchanged
  test to the gate, and does not expand the production diff into unrelated
  manager scheduling.
- Completed-work review found no code defects. It caught a stale focused-test
  filter and the missing [MANAGER.4] plan backlink; both were corrected before
  final verification. The plan and index were held at `draft` while this
  verified slice was uncommitted, per the repository completion gate. The slice
  landed in commit `c96ab1e` ("Update to use new simplebroker API") and shipped
  in release v0.9.90, so both were promoted to `completed` on 2026-07-13.

## Fresh-Eyes Review

Completed after independent review. The revised plan names every affected
producer and existing regression, distinguishes payload shape from template
state, fixes the bundle-root/context omission, and states exact-ID precedence.
Residual risk is backend contention behavior under the full long-session test;
that test is a required gate rather than an inferred claim.
