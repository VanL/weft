# Weft Django V1 Reality Alignment Plan

Status: active
Source specs: see Source Documents below
Superseded by: none

## Goal

Replace the overly optimistic "completed" status of the initial `weft_django`
landing with an active v1 alignment slice. The package that shipped is a thin
broker-backed, SSE-first Django integration on top of `weft.client`, with
release and CI wiring already in place. The remaining work is to fix the real
public-contract bugs, expand tests around those boundaries, and either
implement or explicitly defer the spec-promised features that are still absent
or stubbed so the spec and README stop outrunning the code.

Recommended v1 stance for this slice:

- keep `weft_django` thin and built on `weft.client`
- keep broker-backed submission as the normal path
- keep HTTP read-only
- keep SSE as the only shipped realtime transport in this slice
- treat inline test mode, real Channels transport, and other larger optional
  surfaces as explicit future work unless separately approved

## Source Documents

Source specs:
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-0.1], [DJ-2.1],
  [DJ-6.1], [DJ-8.1], [DJ-8.2], [DJ-8.4], [DJ-10.1], [DJ-11.1], [DJ-12.1],
  [DJ-12.2], [DJ-12.3], [DJ-13.2], [DJ-15.1], [DJ-17.1], [DJ-17.2],
  [DJ-17.3]
- `docs/specifications/13-Agent_Runtime.md` [AR-0], [AR-2]
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
  landed the shared `weft.client` surface the Django package now depends on

## Context and Key Files

Files to modify:
- `docs/specifications/13C-Using_Weft_With_Django.md`
- `docs/plans/2026-04-20-weft-django-v1-reality-alignment-plan.md` (new)
- `docs/plans/2026-04-20-weft-django-integration-implementation-plan.md`
- `docs/plans/README.md`
- `integrations/weft_django/README.md`
- `integrations/weft_django/weft_django/conf.py`
- `integrations/weft_django/weft_django/client.py`
- `integrations/weft_django/weft_django/views.py`
- `integrations/weft_django/weft_django/sse.py`
- `integrations/weft_django/tests/test_weft_django.py`

Read first:
- `docs/specifications/13C-Using_Weft_With_Django.md`
- `integrations/weft_django/weft_django/client.py`
- `integrations/weft_django/weft_django/conf.py`
- `integrations/weft_django/weft_django/views.py`
- `integrations/weft_django/weft_django/sse.py`
- `integrations/weft_django/tests/test_weft_django.py`
- `weft/client/_task.py`
- `weft/commands/events.py`

Shared paths and helpers to reuse:
- `weft.client.WeftClient` remains the only public submission and observation
  dependency for the Django package
- `Task.result()` and `Task.follow()` remain the canonical result/event paths
  unless the spec is explicitly widened
- Django transaction deferral remains a thin wrapper over
  `django.db.transaction.on_commit()`

Current structure and known drift:
- `weft_django.WeftSubmission` is currently just an alias to
  `weft.client.Task`; it does not match the richer `DJ-10.1` handle currently
  described in the spec
- `resolve_context_override()` falls back to ordinary core discovery whenever
  any `WEFT_*` env var is present, so unrelated flags like `WEFT_DEBUG=1` can
  redirect the package to the process cwd
- native helper tests currently exercise only the local `payload=` keyword,
  while the spec still documents `work_payload=` and `input=`
- `get_test_mode()` and `get_realtime_transport()` exist, but inline mode and
  transport switching are not wired into the runtime path
- `channels.py` is a stub import guard plus a close-immediately consumer, not a
  real transport implementation
- SSE currently maps every non-`result` event to `state`; it does not emit the
  full `snapshot/state/stdout/stderr/result/end` taxonomy promised by the
  current spec

Comprehension questions before editing:
- Which layer owns durable submission and result truth for Django callers?
  Answer: `weft.client` and the underlying Weft queues/logs, not Django-local
  wrappers or models.
- Which currently documented Django features are real today versus merely
  stubbed? Answer: broker-backed submission, transaction deferral, read-only
  SSE, management commands, CI, and packaging are real; inline mode and real
  Channels transport are not.

## Invariants and Constraints

- Keep the current durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Do not introduce a second Django-owned submission, wait, or event-reading
  path when `weft.client` already provides one.
- `weft_django` must stay a thin integration layer. No mirrored Django task
  models, no hidden manager startup in `AppConfig.ready()`, and no
  Django-specific runtime class.
- Native Weft TaskSpecs and pipelines must preserve their declared runner and
  target semantics.
- HTTP remains read-only. No stop/kill endpoints in this slice.
- Django context selection must not use process cwd as the primary default when
  `settings.BASE_DIR` is available.
- If the Django helper keyword contract is widened, keep backward compatibility
  for the already-shipped `payload=` keyword unless the user explicitly asks to
  break it.
- Do not reopen CI or release automation in this slice except for doc or test
  drift directly required by the new scope statement.
- Broker-backed and transaction-sensitive proofs must stay real. Do not mock
  away `transaction.on_commit()`, manager startup, or result visibility.

Review gates:
- No second execution path.
- No drive-by refactor of `weft.client` or the shared command layer.
- No new dependency.
- No silent spec drift. If the intended v1 surface narrows, the spec must be
  updated in the same slice instead of leaving dead promises in place.

## Tasks

1. Reset the normative v1 boundary for `weft_django`.
   - Outcome: the spec, README, and plan corpus describe the actual intended
     v1 package rather than the optimistic first-pass landing.
   - Files to touch:
     - `docs/specifications/13C-Using_Weft_With_Django.md`
     - `integrations/weft_django/README.md`
     - `docs/plans/2026-04-20-weft-django-integration-implementation-plan.md`
     - `docs/plans/README.md`
   - Read first:
     - `integrations/weft_django/weft_django/client.py`
     - `integrations/weft_django/weft_django/channels.py`
     - `integrations/weft_django/weft_django/sse.py`
   - Implement:
     - update `DJ-10.1` so the public submission handle truthfully describes
       the shared `weft.client.Task`-based surface, or explicitly call out the
       follow-up if a richer wrapper is still desired
     - narrow or defer the unshipped optional surfaces:
       inline test mode, real Channels transport, and any SSE event taxonomy
       that the code does not actually emit today
     - add backlinks from the spec to this plan and mark the initial landing
       plan as superseded
   - Stop and re-evaluate if:
     - the preferred outcome becomes "implement inline mode and Channels now"
       instead of tightening the v1 boundary
     - the work starts inventing a Django-specific result backend instead of
       documenting the shared `weft.client` handle
   - Done signal:
     - the spec no longer promises dead code or stubs as part of the current v1
       contract

2. Fix Django context selection and native helper keyword drift.
   - Outcome: Django callers resolve the intended Weft context and can use the
     documented native helper keywords without falling into submit-override
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
     - accept the spec-owned helper keywords (`work_payload=` for TaskSpec
       helpers and `input=` for spec/pipeline helpers) while preserving the
       current `payload=` alias for backward compatibility
     - reject ambiguous calls where both names are supplied at once
   - Tests to add or update:
     - `WEFT_DEBUG=1` must not redirect the package away from `BASE_DIR`
     - explicit `WEFT_DJANGO["CONTEXT"]` still wins
     - `work_payload=` and `input=` route to the same durable submission path
     - ambiguous duplicate payload arguments raise locally
     - deferred helpers still reject `wait=True`
   - Stop and re-evaluate if:
     - the fix requires importing low-level submission internals into the
       Django package instead of staying on `weft.client`
   - Done signal:
     - the two existing P1 findings are covered by broker-backed or direct
       Django-config regression tests

3. Harden the read-only HTTP and SSE surfaces.
   - Outcome: malformed input is handled as a normal operator error and the
     documented realtime contract matches what the code actually emits.
   - Files to touch:
     - `integrations/weft_django/weft_django/views.py`
     - `integrations/weft_django/weft_django/sse.py`
     - `integrations/weft_django/tests/test_weft_django.py`
     - `docs/specifications/13C-Using_Weft_With_Django.md`
   - Read first:
     - `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-11.1],
       [DJ-12.1], [DJ-12.3]
     - `weft/commands/events.py`
   - Implement:
     - validate TIDs before creating task handles and return a normal HTTP error
       before the SSE response starts streaming
     - choose one v1 event contract and make code, tests, and docs agree on it
       in the same change
     - keep the transport on the shared `Task.follow()` path rather than
       opening a second outbox/control watcher surface from Django
   - Tests to add or update:
     - malformed task id on detail endpoint
     - malformed task id on SSE endpoint
     - unknown-but-well-formed task id behavior
     - authorized and unauthorized read/stream requests
     - first SSE chunk shape under the chosen v1 contract
   - Stop and re-evaluate if:
     - supporting `stdout` / `stderr` event types requires a new core event path
       rather than a thin Django wrapper
   - Done signal:
     - the fourth review finding is closed and the SSE spec no longer outruns
       the current event source

4. Expand the Django test surface around the real contract.
   - Outcome: the package has regression tests for the contract-sensitive edges
     that were missing from the initial happy-path suite.
   - Files to touch:
     - `integrations/weft_django/tests/test_weft_django.py`
     - optional small helper test modules under `integrations/weft_django/tests/`
   - Reuse:
     - the existing fixture project
     - real `transaction.atomic()` / `transaction.on_commit()`
     - real broker-backed Weft execution for submission and result behavior
   - Add coverage for:
     - duplicate task-name registration failure
     - URL import failure without `AUTHZ`
     - native helper keyword aliases and ambiguous-call rejection
     - malformed TID handling
     - context-selection behavior under representative `WEFT_*` env settings
   - Stop and re-evaluate if:
     - new tests start mocking the manager or queue layer to make the package
       look more complete than it is
   - Done signal:
     - the package test tree proves the negative cases that currently require
       manual repros

5. Defer the larger optional surfaces explicitly instead of leaving stubs.
   - Outcome: the remaining big-ticket features are either split into future
     work or intentionally removed from the current v1 story.
   - Features to decide explicitly in docs:
     - inline test mode (`DJ-17.1`)
     - real Channels transport (`DJ-12.2`)
     - any richer Django-specific result handle beyond the shared
       `weft.client.Task` surface
     - duplicated envelope `tid` for worker-side correlation if a clean
       public-core path does not exist yet
   - Rule:
     - do not quietly leave `get_test_mode()`, `get_realtime_transport()`, or
       `channels.py` as "future promises" while the spec still calls them part
       of the current contract
   - Done signal:
     - the remaining not-now surfaces are visibly deferred rather than
       accidentally implied

## Testing Plan

- Keep broker-backed submission, result, and `on_commit()` proofs real.
- Use direct Django config tests for the context-selection helper where a full
  broker run is not needed.
- Keep malformed-TID endpoint checks in the integration test suite so the HTTP
  behavior is proven through the real Django URLs.
- Do not add a mock-only inline-mode test in this slice. If inline mode remains
  in scope, it needs its own plan and real design pass.

## Verification

Run the smallest meaningful sets first:

```bash
./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0
./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml
./.venv/bin/python -m ruff check integrations/weft_django docs/plans docs/specifications
```

Success looks like:

- the known Django review findings are covered by tests and fixed,
- the spec and README describe the same v1 package that the code actually
  ships,
- the initial Django landing plan is clearly historical rather than the active
  execution document,
- and the remaining big optional surfaces are either implemented in a separate
  approved slice or explicitly deferred.

## Out of Scope

- Real Channels/WebSocket transport implementation in this slice
- Inline-mode execution design and implementation in this slice
- Admin integration
- Reworking the already-landed CI/release automation beyond doc or test drift
- A second Django-local result backend or task-truth store
