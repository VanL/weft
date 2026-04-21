# Weft Django V1 Reality Alignment Plan

Status: completed
Source specs: see Source Documents below
Superseded by: ./2026-04-21-weft-client-and-django-first-class-hardening-plan.md

## Goal

Bring `weft_django` up to the current 13C spec without changing the thin
architecture that already landed. The first slice proved packaging, the
broker-backed path, transaction deferral, and read-only diagnostics. This
follow-up slice finishes the missing spec-promised features, closes the known
contract bugs, and makes the docs, tests, and shipped code agree again.

Recommended v1 stance for this slice:

- keep `weft_django` thin and layered over `weft.client`
- keep broker-backed submission as the normal path
- keep HTTP read-only
- do not ship inline/eager execution mode in v1; direct Python calls cover
  unit tests and broker-backed tests cover production semantics
- keep Channels optional behind the package extra, but make the optional path
  real instead of stubbed
- allow small shared-core additions only when the Django transport or handle
  contract cannot be satisfied as a thin wrapper

## Source Documents

Source specs:
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-0.1], [DJ-2.1],
  [DJ-6.1], [DJ-8.1], [DJ-8.2], [DJ-8.4], [DJ-9.1], [DJ-10.1], [DJ-11.1],
  [DJ-11.2], [DJ-12.1], [DJ-12.2], [DJ-12.3], [DJ-13.2], [DJ-15.1],
  [DJ-17.1], [DJ-17.2], [DJ-17.3]
- `docs/specifications/13-Agent_Runtime.md` [AR-0], [AR-2]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]
- `docs/specifications/02-TaskSpec.md` [TS-1], [TS-1.3]
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4]
- `docs/specifications/08-Testing_Strategy.md` [TS-0]

Source guidance:
- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Historical plan context:
- `docs/plans/2026-04-20-weft-django-integration-implementation-plan.md`
  landed the initial package, tests, CI, and release wiring, but closed too
  aggressively relative to the current spec and the actual shipped surface
- `docs/plans/2026-04-20-weft-client-pythonic-surface-and-path-unification-plan.md`
  landed the shared `weft.client` surface the Django package depends on

## Ownership And Boundary

Primary owner boundary:
- `integrations/weft_django/weft_django/` owns the Django-facing API, settings,
  transaction hook behavior, HTTP views, SSE, and optional Channels transport

Shared-core boundary:
- `weft.client` remains the only durable Python submission and observation
  dependency for Django callers
- if the richer handle or realtime contract needs new shared behavior, the only
  acceptable shared-core extension points are small additions in
  `weft/client/` or `weft/commands/events.py`

Not allowed:
- a second Django-owned task truth store
- a Django model for task state
- Django-only queue watchers that bypass the shared client/command layer when a
  shared helper can own the behavior instead

## Context And Current Gaps

Current structure and known drift:

- `weft_django.WeftSubmission` is currently just an alias to
  `weft.client.Task`; it does not satisfy the richer handle described by
  [DJ-10.1]
- `resolve_context_override()` falls back to ordinary core discovery whenever
  any `WEFT_*` env var is present, so unrelated flags like `WEFT_DEBUG=1` can
  redirect the package to the process cwd instead of `settings.BASE_DIR`
- native helper tests currently exercise only the local `payload=` keyword,
  while older draft docs still mention `work_payload=` and `input=`
- `get_realtime_transport()` exists, but transport switching is not fully
  proven through installed optional Channels CI
- `channels.py` is an import guard plus a close-immediately stub consumer, not
  a real transport implementation
- SSE currently maps every non-`result` event to `state`; it does not match the
  `snapshot/state/stdout/stderr/result/end` contract described by [DJ-12.1]
  and [DJ-12.3]
- malformed task ids still escape the read-only HTTP/SSE views as uncaught
  client errors instead of normal operator-facing failures

Comprehension checkpoints before implementation:

- durable submission, status, and result truth stays in Weft queues and logs,
  surfaced through `weft.client`
- the richer Django submission handle is a wrapper, not a second backend
- inline mode is intentionally out of scope for v1
- WebSocket support stays optional even though the optional path must become
  real

## Invariants And Constraints

- Keep the current durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Do not introduce a second Django-owned submission, wait, or result path when
  `weft.client` already provides one.
- `weft_django` must stay thin. No mirrored Django task models, no hidden
  manager startup in `AppConfig.ready()`, and no Django-specific runtime class.
- Native Weft TaskSpecs and pipelines must preserve their declared runner and
  target semantics.
- HTTP remains read-only. No stop/kill endpoints in this slice.
- Django context selection must not use process cwd as the primary default when
  `settings.BASE_DIR` is available.
- Native helper payload input is named `payload` only. Do not add
  `work_payload`, `input`, or other aliases in v1.
- SSE and Channels must share one normalized event contract. Do not duplicate
  event-shaping logic per transport.
- If realtime `stdout` / `stderr` delivery needs more than the current
  `Task.follow()` surface, add the missing behavior once in shared code rather
  than teaching Django transports to read raw queues independently.
- Do not reopen CI or release automation in this slice except for doc or test
  drift directly required by the new implementation work.

Review gates:

- No second execution path for normal broker-backed work.
- No drive-by refactor of `weft.client` or the shared command layer.
- No new dependency outside the existing optional `channels` extra.
- No silent spec drift. If an exact method return value or event shape must be
  clarified, update the spec and nearby implementation notes in the same slice.

## Tasks

1. Reset the active Django execution boundary to "thin but complete", not
   "thin but deferred".
   - Outcome: the active plan and plan corpus explicitly target completion of
     the current 13C surface instead of narrowing 13C down to the thin landing
     that already shipped.
   - Files to touch:
     - `docs/plans/2026-04-20-weft-django-v1-reality-alignment-plan.md`
     - `docs/plans/README.md`
     - `docs/plans/2026-04-20-weft-django-integration-implementation-plan.md`
   - Implement:
     - keep the initial landing plan marked historical/superseded
     - update the active-plan description so it is clear that the remaining
       work is implementation, not scope reduction
     - preserve the thin-layer boundary in the plan itself so future slices do
       not invent Django-local runtime behavior
   - Done signal:
     - plan readers can tell, without inference, that broker-backed execution,
       the richer submission handle, the full realtime contract, and optional
       Channels are current implementation work

2. Fix the two current P1 contract bugs first.
   - Outcome: Django callers resolve the intended Weft context and can use the
     documented native helper keyword without falling into submit-override
     errors.
   - Files to touch:
     - `integrations/weft_django/weft_django/conf.py`
     - `integrations/weft_django/weft_django/client.py`
     - `integrations/weft_django/tests/test_weft_django.py`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-8.2], [DJ-13.2]
     - `weft/context.py`
   - Implement:
     - replace the broad `any(key.startswith("WEFT_") ...)` heuristic with an
       explicit context-selection rule that preserves the `settings.BASE_DIR`
       default and only defers when a real context-selection override is
       intended
     - keep `payload=` as the only native helper payload keyword
     - reject legacy draft names such as `work_payload=` and `input=` locally
   - Tests to add or update:
     - `WEFT_DEBUG=1` must not redirect the package away from `BASE_DIR`
     - explicit `WEFT_DJANGO["CONTEXT"]` still wins
     - `payload=` routes to the durable submission path for TaskSpecs, spec
       refs, and pipelines
     - `work_payload=` and `input=` raise locally with a clear message
     - deferred helpers still reject `wait=True`
   - Done signal:
     - the two known P1 findings are covered by direct regression tests

3. Ship a real Django submission handle on top of `weft.client.Task`.
   - Outcome: decorated task methods and native helpers return thin wrappers
     that satisfy [DJ-10.1] without creating a second result backend.
   - Files to touch:
     - `integrations/weft_django/weft_django/client.py`
     - `integrations/weft_django/weft_django/__init__.py`
     - `integrations/weft_django/README.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
     - `integrations/weft_django/tests/test_weft_django.py`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-8.1], [DJ-10.1]
     - `weft/client/_task.py`
   - Implement:
     - add a small `WeftSubmission` wrapper with `tid`, `name`, `status()`,
       `wait(timeout=None)`, `result(timeout=None)`, `stop()`, `kill()`, and
       `events(follow=False)` over an underlying `Task`
     - keep `WeftDeferredSubmission` as the `transaction.on_commit()` handle,
       but make its pre-bind behavior explicit and local rather than exposing a
       partially initialized raw task
     - if the exact return values of `status()` or `wait()` are too loose in
       the current spec text, clarify them in the spec and README in the same
       slice instead of leaving them implicit
   - Tests to add or update:
     - direct helper submission returns the richer wrapper
     - decorated `enqueue()` returns the richer wrapper
     - deferred handles expose `name`, gain `tid` after commit, and fail
       locally with a clear error if result-like methods are called before bind
     - wrapper `stop()` / `kill()` still delegate to the shared task controls
   - Stop and re-evaluate if:
     - the wrapper starts caching task state instead of delegating
     - the design starts inventing Django-only result objects outside the thin
       adapter boundary
   - Done signal:
     - the public Python surface described by [DJ-10.1] is real in code and
       covered by tests

4. Remove inline/eager mode from the v1 surface.
   - Outcome: `weft_django` has one production-equivalent execution path:
     broker-backed Weft submission through `weft.client`.
   - Files to touch:
     - `integrations/weft_django/weft_django/conf.py`
     - `integrations/weft_django/weft_django/client.py`
     - `integrations/weft_django/README.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
     - `integrations/weft_django/tests/test_weft_django.py`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-9.1],
       [DJ-17.1], [DJ-17.2], [DJ-17.3]
   - Implement:
     - remove `WEFT_DJANGO["TEST_MODE"]` and `get_test_mode()`
     - remove in-process `Consumer` execution from `weft_django`
     - keep `transaction.on_commit()` semantics broker-backed, including
       rollback suppression
     - document direct Python calls as the narrow unit-test path
   - Tests to add or update:
     - rollback prevents `enqueue_on_commit()` binding
     - `weft_django.client` does not import core runtime internals
   - Stop and re-evaluate if:
     - unit-test convenience starts recreating a fake manager, fake task ids, or
       a shadow lifecycle store
   - Done signal:
     - there is no inline/eager mode setting, code path, or documentation

5. Harden the read-only HTTP and SSE surfaces and implement the full realtime
   contract.
   - Outcome: malformed input is handled as a normal operator error and the
     default realtime transport matches [DJ-12.1] and [DJ-12.3].
   - Files to touch:
     - `integrations/weft_django/weft_django/views.py`
     - `integrations/weft_django/weft_django/sse.py`
     - `integrations/weft_django/weft_django/client.py`
     - `weft/commands/events.py` or related shared event helpers, if needed
     - `docs/specifications/13C-Using_Weft_With_Django.md`
     - `integrations/weft_django/tests/test_weft_django.py`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-11.1],
       [DJ-11.2], [DJ-12.1], [DJ-12.3]
     - `weft/commands/events.py`
     - `weft/client/_task.py`
   - Implement:
     - validate TIDs before creating task handles and return a normal HTTP
       error before the SSE response starts streaming
     - introduce one normalized event-shaping path that both transports can
       reuse
     - emit `snapshot`, `state`, `stdout`, `stderr`, `result`, and `end` using
       shared Weft event truth
     - if `stdout` / `stderr` cannot be represented from the current shared
       event helpers, extend shared code once rather than adding Django-only
       queue watchers
   - Tests to add or update:
     - malformed task id on detail endpoint
     - malformed task id on SSE endpoint
     - unknown-but-well-formed task id behavior
     - authorized and unauthorized read/stream requests
     - first SSE chunks and terminal chunks under the normalized contract
   - Stop and re-evaluate if:
     - satisfying the realtime contract requires a second Django-only event
       source
   - Done signal:
     - the fourth review finding is closed and the default realtime transport
       matches the documented payload taxonomy

6. Replace the Channels stub with a real optional transport.
   - Outcome: projects that install the `channels` extra and enable the
     transport get a working WebSocket consumer that reuses the same normalized
     event contract as SSE.
   - Files to touch:
     - `integrations/weft_django/weft_django/channels.py`
     - `integrations/weft_django/weft_django/conf.py`
     - `integrations/weft_django/weft_django/__init__.py`
     - optional new routing helper under `integrations/weft_django/weft_django/`
     - `integrations/weft_django/README.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
     - `integrations/weft_django/tests/test_weft_django.py`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-12.2], [DJ-12.3]
     - `integrations/weft_django/weft_django/channels.py`
     - the existing package `pyproject.toml` extra definitions
   - Implement:
     - make `WEFT_DJANGO["REALTIME"]["TRANSPORT"]` actually select between
       `none`, `sse`, and `channels`
     - provide a real consumer that validates authz, validates TIDs, and emits
       the shared normalized event payloads
     - keep the import guard and optional dependency boundary explicit
   - Tests to add or update:
     - transport setting validation
     - clean import-guard behavior when Channels is unavailable
     - consumer-level behavior when Channels is available, or a direct unit
       test of the shared transport adapter if the test environment does not
       ship Channels
   - Stop and re-evaluate if:
     - the WebSocket path starts diverging from the SSE payload contract
     - the transport selection logic starts implying a mandatory Channels
       dependency
   - Done signal:
     - the optional WebSocket surface is real, optional, and contract-matched

7. Expand the Django test surface and sync the docs to the shipped contract.
   - Outcome: the package stops depending on happy-path manual repros for
     contract-sensitive edges, and the README/spec implementation notes match
     what the code now does.
   - Files to touch:
     - `integrations/weft_django/tests/test_weft_django.py`
     - optional helper test modules under `integrations/weft_django/tests/`
     - `integrations/weft_django/README.md`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
   - Add coverage for:
     - duplicate task-name registration failure
     - URL import failure without `AUTHZ`
     - native helper `payload=` behavior and legacy keyword rejection
     - richer submission-handle behavior
     - direct-call unit-test guidance versus broker-backed submission behavior
     - malformed TID handling
     - context-selection behavior under representative `WEFT_*` env settings
     - SSE / Channels contract behavior through the shared normalizer
   - Documentation work:
     - package README sections for install, `@weft_task`, native submission
       helpers, richer handle semantics, broker-backed tests, and
       SSE/Channels operator notes
     - nearby spec implementation notes and backlinks for the modules that now
       own the completed behavior
   - Done signal:
     - the integration package has enough negative-path coverage that the main
       remaining risk is product design choice, not contract drift

## Testing Plan

- Keep broker-backed submission, result, and `on_commit()` proofs real.
- Use direct Django config tests for context selection and transport selection
  where a full broker run is not needed.
- Keep malformed-TID endpoint checks in the integration test suite so the HTTP
  behavior is proven through the real Django URLs.
- If shared event helpers change, add the smallest shared-core tests there
  rather than proving everything only through Django views.
- Do not add inline-mode tests. Narrow unit tests should call Python functions
  directly; submission behavior should use real broker-backed tests.

## Verification

Run the smallest meaningful sets first:

```bash
./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0
./.venv/bin/python -m pytest tests/core tests/commands -q -n 0
./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml
./.venv/bin/python -m ruff check weft integrations/weft_django docs/plans docs/specifications
```

Success looks like:

- the known Django review findings are fixed and covered by tests
- `weft_django` exposes the richer submission handle described by [DJ-10.1]
  without creating a second backend
- there is no inline/eager execution mode in the v1 Django package
- SSE and optional Channels share one event contract that matches the docs
- and the README/spec/plan corpus describe the same thin but now-complete v1
  package

## Required Action

- Implement tasks 2 through 6 in order.
- Run an independent review after task 3 because it changes the public Python
  surface.
- Run a second independent review after task 6 because it changes runtime and
  transport behavior across both HTTP and optional WebSocket paths.
- Do not mark the Django slice complete until task 7 lands with the doc and
  regression-test updates.

## Out Of Scope

- Admin integration
- HTTP control endpoints
- A Django model or database table for task truth
- Mandatory WebSocket / Channels dependency
- Reworking the already-landed CI and release automation beyond test or doc
  drift directly needed for this implementation slice
