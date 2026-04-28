# Weft Client And Django First-Class Hardening Plan

Status: completed
Source specs: see Source Documents below
Superseded by: none

## Goal

Move `weft.client` and `weft_django` from "good v1 surfaces" to first-class
library surfaces. The work closes the review findings around typed errors,
package typing, deferred submission validation, deferred payload lifetime, and
Channels lifecycle behavior without adding a second execution path or making
Django a runner.

First-class means:

- downstream code can depend on `weft.client` without reaching into private
  modules
- `weft_django` uses only the public client boundary for Weft behavior
- framework transaction hooks validate before app data commits
- installed wheels carry the same typing contract the source tree advertises
- realtime transports do not pin lifecycle hooks or hide cancellation bugs

## Source Documents

Source specs:

- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-0.1], [DJ-2.1],
  [DJ-2.2], [DJ-8.2], [DJ-8.3], [DJ-9.1], [DJ-10.1], [DJ-11.2], [DJ-12.2],
  [DJ-12.3], [DJ-13.4], [DJ-17.2], [DJ-17.3]
- `docs/specifications/05-Message_Flow_and_State.md` [MF-5], [MF-6]
- `docs/specifications/10-CLI_Interface.md` [CLI-1], [CLI-4], [CLI-6]
- `docs/specifications/08-Testing_Strategy.md` [TS-0]

Source guidance:

- `AGENTS.md`
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/writing-plans.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Related plans:

- `docs/plans/2026-04-20-weft-client-pythonic-surface-and-path-unification-plan.md`
  landed the current `weft.client` package split and shared command-layer model.

No current spec covers PEP 561 `py.typed` packaging explicitly. This plan
requires a small spec/documentation update because package typing is part of the
public library contract, not an implementation-only detail.

## Review Findings Addressed

This plan addresses these findings:

- P2: `weft_django.stop()` and `weft_django.kill()` leak raw `TaskNotFound` and
  `ControlRejected` errors because `weft.client` does not expose typed
  exceptions publicly and the Django wrapper only catches `ValueError`.
- P2: the typed source packages do not ship `py.typed`, so installed wheels are
  not first-class typed libraries for downstream users.
- P3: deferred Django submissions close over mutable envelopes and payloads,
  so work can change between `transaction.on_commit()` registration and commit.
- P2: deferred native submissions register callbacks before references,
  TaskSpecs, and submit overrides are resolved and validated, so app rows can
  commit and enqueue can fail afterward.
- P3: the Channels consumer runs the whole `follow=True` stream inside
  `connect()`, which blocks Channels lifecycle handling and makes disconnect
  handling weak for quiet or persistent tasks.

## Completion Notes

Implemented in this slice:

- public `weft.client` exception exports and `PreparedSubmission`
- command-layer `prepare`, `prepare_spec`, `prepare_pipeline`, and
  `submit_prepared`
- Django `*_on_commit` helpers that validate and snapshot before registering
  the commit callback
- Django stop/kill wrappers that translate public client control exceptions
- PEP 561 `py.typed` markers for both root and `weft-django` packages
- cancellable realtime event iteration and non-blocking optional Channels
  connect lifecycle
- spec, README, and integration README updates for the shipped behavior

Verification run:

- `./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0`
- `./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0`
- `./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q -n 0`
- `./.venv/bin/python -m pytest tests/system/test_optional_extras.py tests/system/test_release_script.py -q -n 0`
- `./.venv/bin/python -m pytest tests/core/test_client.py tests/core/test_exceptions.py tests/system/test_optional_extras.py -q -n 0`
- `./.venv/bin/python -m ruff check weft integrations/weft_django tests/core/test_client.py tests/system/test_optional_extras.py integrations/weft_django/tests/test_weft_django.py`
- `./.venv/bin/python -m ruff format --check weft/commands/submission.py weft/commands/types.py weft/client integrations/weft_django/weft_django integrations/weft_django/tests/test_weft_django.py tests/core/test_client.py tests/system/test_optional_extras.py`
- `./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml`
- `git diff --check`
- `uv build` for root and `integrations/weft_django`, with wheel and sdist
  inspection for `weft/py.typed` and `weft_django/py.typed`

The Channels lifecycle regression test is present but skipped in environments
without the optional `channels` dependency.

## Context And Key Files

Current client structure:

- `weft/client/__init__.py` is the public import surface.
- `weft/client/_client.py` owns `WeftClient`.
- `weft/client/_task.py` owns the lazy `Task` handle.
- `weft/client/_namespaces.py` exposes noun-first capability namespaces.
- `weft/client/_types.py` re-exports public command-layer dataclasses.
- `weft/commands/submission.py` owns durable submission, reference resolution,
  submit overrides, pipeline compilation, and manager reconciliation.
- `weft/_exceptions.py` already defines typed exceptions but is private by
  convention.

Current Django structure:

- `integrations/weft_django/weft_django/client.py` owns
  `WeftSubmission`, `WeftDeferredSubmission`, decorated-task submission, native
  submission helpers, and management-command helper functions.
- `integrations/weft_django/weft_django/decorators.py` owns
  `RegisteredWeftTask`, call envelope construction, and `as_taskspec_for_call`.
- `integrations/weft_django/weft_django/channels.py` owns the optional
  WebSocket transport.
- `integrations/weft_django/weft_django/realtime.py` owns the shared
  event-payload shape for SSE and Channels.
- `integrations/weft_django/weft_django/sse.py` owns the default SSE transport.
- `integrations/weft_django/tests/test_weft_django.py` is the broker-backed
  integration suite for Django behavior.

Packaging and release files:

- `pyproject.toml` owns the root `weft` package metadata and extras.
- `integrations/weft_django/pyproject.toml` owns the standalone
  `weft-django` package metadata.
- `tests/system/test_optional_extras.py` and
  `tests/system/test_release_script.py` cover packaging and release metadata.
- `.github/workflows/test.yml` and `.github/workflows/release-gate-django.yml`
  cover CI and tag-triggered release behavior.

Read before editing:

- `weft/commands/submission.py`, especially `_validate_submit_overrides`,
  `apply_submit_overrides`, `submit`, `submit_spec`, and `submit_pipeline`.
- `weft/core/spawn_requests.py`, especially `submit_spawn_request`, to verify
  that pre-commit validation does not write to `weft.spawn.requests`.
- `weft/commands/events.py`, especially `iter_task_realtime_events`, to see
  where synchronous realtime streaming currently waits.
- `integrations/weft_django/weft_django/client.py`, especially all
  `*_on_commit` helpers.
- `integrations/weft_django/weft_django/channels.py`, especially
  `TaskEventsConsumer.connect`.
- `tests/architecture/test_import_boundaries.py`, because it is the durable
  guardrail for `weft.client` and `weft_django` layering.

Comprehension checkpoints:

- Which code path writes `weft.spawn.requests`, and which proposed validation
  work must not write that queue?
- Where does `weft_django` currently call the public client, and where would an
  implementation be tempted to import `weft.commands` or `weft.core` directly?
- Which part of Channels must return promptly for disconnect lifecycle handling
  to work?

## Invariants And Constraints

Preserve these invariants:

- Keep the durable spine:
  `TaskSpec -> Manager -> Consumer -> TaskRunner -> queues/state log`.
- Do not add inline, eager, or in-process execution mode for Django.
- Do not submit to Weft before the outer Django transaction commits.
- Do not let a deferred-submission callback be the first place a spec
  reference, pipeline reference, TaskSpec shape, or submit override is
  validated.
- Do not make Django import `weft.commands.*`, `weft.core.*`, or
  `weft._exceptions` to perform ordinary integration work.
- Do not make Channels a required dependency of `weft-django` or `weft[django]`.
- Do not add HTTP stop/kill endpoints.
- Do not create a Django model or Django result backend for task lifecycle
  truth.
- Do not change TID format, queue names, state transition semantics, or the
  public result payload shape.
- Keep `payload=` as the only native Django helper payload keyword. Do not
  restore `work_payload=` or `input=` aliases.

Error-priority rules:

- Invalid deferred submission configuration is fatal before transaction commit.
- Rollback still prevents enqueue after validation succeeds.
- Optional realtime disconnect cleanup is best effort, but it must not keep a
  request lifecycle method pinned indefinitely.
- Management command helpers should translate client not-found or rejected
  control into Django-facing `CommandError`, not raw tracebacks.

Stop and re-plan if:

- the deferred-validation work starts writing queue messages before commit
- the implementation needs `weft_django` to import `weft.commands` or
  `weft.core`
- Channels cancellation requires a second raw queue watcher in Django instead
  of a shared client/event helper
- the package typing fix starts changing runtime imports or dependency
  metadata beyond marker-file inclusion

Out of scope:

- full async Weft client
- persistent operator-facing agent sessions
- HTTP task control endpoints
- Celery-compatible aliases such as `delay` or `shared_task`
- a sibling repo split for `weft-django`

## Proposed Design

Add a small preparation seam to `weft.client`.

The preparation seam should resolve and validate a submission without writing a
spawn request:

- `WeftClient.prepare(taskspec, *, payload=None, **overrides)`
- `WeftClient.prepare_spec(reference, *, payload=None, **overrides)`
- `WeftClient.prepare_pipeline(reference, *, payload=None, **overrides)`

Each method should return a lightweight `PreparedSubmission` handle. That handle
should store:

- the bound `WeftClient`
- the already resolved and override-applied TaskSpec payload or `TaskSpec`
- a copied payload suitable for spawn-request JSON serialization
- the submission flags needed by the core path, such as `seed_start_envelope`
  and `allow_internal_runtime`
- the stable display name

`PreparedSubmission.submit() -> Task` should be the only operation that writes
to Weft queues. The normal `WeftClient.submit*` methods may delegate through
the same preparation path, but they must preserve current public behavior.

Rationale:

- Django can validate inside the transaction and enqueue only after commit.
- Reference resolution and pipeline compilation stay owned by the client and
  command layers.
- The same seam is useful for future framework integrations without making
  Django special.
- The seam avoids importing `weft.commands.submission` directly from
  `weft_django`.

## Tasks

1. Update the spec and plan traceability before code changes.

   Outcome: the authoritative docs describe the first-class client seam and the
   stricter deferred submission behavior before implementation begins.

   Files to touch:

   - `docs/specifications/13C-Using_Weft_With_Django.md`
   - `docs/plans/2026-04-21-weft-client-and-django-first-class-hardening-plan.md`
   - `docs/plans/README.md`

   Required edits:

   - Add `PreparedSubmission` or equivalent to [DJ-2.1] as the blessed client
     validation seam used by framework integrations.
   - Clarify [DJ-9.1] so deferred helpers validate and snapshot before
     registering `transaction.on_commit()`, while queue submission remains in
     the callback.
   - Clarify [DJ-10.1] that `WeftSubmission.stop()` and `kill()` should surface
     client typed errors consistently through Django wrappers.
   - Clarify [DJ-12.2] that Channels consumers must not block the whole stream
     inside `connect()`.
   - Add a short package-typing note near [DJ-19] or the compatibility section.

   Done signal:

   - A zero-context implementer can read 13C and this plan and know that
     `prepare*` validates without enqueueing, and that on-commit queue writes
     still happen only after commit.

2. Make `weft.client` expose a first-class typed error surface and PEP 561
   typing markers.

   Outcome: downstream code can catch public Weft client exceptions and installed
   wheels advertise type information.

   Files to touch:

   - `weft/client/__init__.py`
   - `weft/client/_types.py` or a new `weft/client/_errors.py`
   - `weft/py.typed` or `weft/client/py.typed` depending on packaging choice
   - `integrations/weft_django/weft_django/py.typed`
   - `pyproject.toml`
   - `integrations/weft_django/pyproject.toml`
   - `tests/core/test_client.py`
   - `tests/system/test_optional_extras.py` or a new packaging test

   Implement:

   - Re-export `WeftError`, `InvalidTID`, `TaskNotFound`, `ControlRejected`,
     `SpecNotFound`, `ManagerNotRunning`, and `ManagerStartFailed` from
     `weft.client`.
   - Do not require Django or other integrations to import `weft._exceptions`.
   - Add `py.typed` marker files to both packages.
   - Ensure both root and Django builds include the marker files.
   - Add tests that import the exceptions from `weft.client`.
   - Add a wheel/sdist metadata test that proves `py.typed` lands in built
     artifacts for both `weft` and `weft-django`.

   Done signal:

   - `from weft.client import TaskNotFound, ControlRejected` works.
   - `uv build` for both packages produces artifacts containing `py.typed`.

3. Add the client preparation seam without changing durable submission
   semantics.

   Outcome: code can resolve and validate task, spec, and pipeline submissions
   without writing to `weft.spawn.requests`.

   Files to touch:

   - `weft/commands/submission.py`
   - `weft/commands/types.py`
   - `weft/client/_client.py`
   - `weft/client/_types.py`
   - `tests/core/test_client.py`
   - optionally `tests/commands/test_submission.py` if a command-layer test
     already exists or is the cleaner seam

   Implement:

   - Add a command-layer `PreparedSubmission` data shape or equivalent that
     carries an already normalized submission request.
   - Add `prepare`, `prepare_spec`, and `prepare_pipeline` command helpers that
     reuse `_validate_submit_overrides`, `normalize_taskspec`,
     `apply_submit_overrides`, `specs.resolve_spec_reference`, and
     `compile_linear_pipeline`.
   - Ensure prepare helpers do not call `submit_spawn_request`,
     `generate_tid`, or `ensure_manager_after_submission`.
   - Add `submit_prepared(context, prepared)` or an equivalent helper that
     writes the prepared request through the existing durable submission path.
   - Add `WeftClient.prepare*` methods returning a public
     `PreparedSubmission` handle with `.submit() -> Task`.
   - Make existing `WeftClient.submit*` methods delegate through the same code
     path or prove with tests that they remain behaviorally identical.

   Tests:

   - Preparing an invalid spec reference raises `SpecNotFound` immediately.
   - Preparing with an unknown submit override raises `TypeError` immediately.
   - Preparing a valid submission does not create a task snapshot, manager, or
     spawn request.
   - Calling `.submit()` on the prepared handle creates the task and returns a
     normal `Task`.
   - Pipeline preparation preserves the existing `seed_start_envelope=False`
     and `allow_internal_runtime=True` behavior when submitted.

   Done signal:

   - `client.prepare_spec("missing")` fails before enqueue.
   - `client.prepare(valid).submit().result(timeout=...)` follows the same
     durable path as `client.submit(valid).result(timeout=...)`.

4. Use the preparation seam in all Django deferred submission helpers.

   Outcome: `*_on_commit` validates and snapshots before commit, but still
   writes to Weft only after commit.

   Files to touch:

   - `integrations/weft_django/weft_django/client.py`
   - `integrations/weft_django/weft_django/decorators.py` if envelope copying
     belongs there
   - `integrations/weft_django/tests/test_weft_django.py`
   - `integrations/weft_django/README.md`

   Implement:

   - For decorated `enqueue_on_commit`, build and deep-copy the envelope before
     registering the callback.
   - For native `submit_taskspec_on_commit`, `submit_spec_reference_on_commit`,
     and `submit_pipeline_reference_on_commit`, call the appropriate
     `get_core_client().prepare*` method before registering the callback.
   - Store the prepared handle in the closure and call `.submit()` inside the
     callback.
   - Copy `overrides` before validation and before closure capture.
   - Copy payloads by the same JSON-serialization contract the spawn request
     already requires. If a value cannot be serialized, raise before commit.
   - Keep `wait=True` rejection local and before any transaction callback is
     registered.
   - Keep rollback behavior unchanged: rollback prevents `.submit()`.

   Tests:

   - Invalid spec reference inside `transaction.atomic()` raises before commit
     and rolls back a created fixture row.
   - Unknown override inside `transaction.atomic()` raises before commit and
     rolls back a created fixture row.
   - Mutating a native payload dict after registering `*_on_commit` does not
     change the submitted payload.
   - Mutating a decorated-task argument dict after registering
     `enqueue_on_commit` does not change the submitted call envelope.
   - Rollback still leaves `WeftDeferredSubmission.task is None`.

   Done signal:

   - The only code inside the on-commit callback is binding the result of
     prepared `.submit()` to `WeftDeferredSubmission`.

5. Fix Django stop/kill error translation using public client exceptions.

   Outcome: Django helpers and management commands fail clearly without leaking
   private or raw client exceptions.

   Files to touch:

   - `integrations/weft_django/weft_django/client.py`
   - `integrations/weft_django/weft_django/management/commands/weft_task_stop.py`
   - `integrations/weft_django/weft_django/management/commands/weft_task_kill.py`
   - `integrations/weft_django/tests/test_weft_django.py`

   Implement:

   - Import `ControlRejected`, `InvalidTID`, and `TaskNotFound` from
     `weft.client`.
   - Catch those public exceptions in `weft_django.stop()` and
     `weft_django.kill()` and return `False`.
   - Leave successful stop/kill semantics unchanged.
   - Keep management command wrappers converting `False` into `CommandError`.

   Tests:

   - `weft_task_status` still reports known tasks.
   - `weft_task_stop` on a syntactically valid unknown TID raises
     `CommandError`, not `TaskNotFound`.
   - `weft_task_kill` on a syntactically valid unknown TID raises
     `CommandError`, not `TaskNotFound`.
   - Invalid TIDs remain clean Django command failures.

   Done signal:

   - No `weft_django` production module imports `weft._exceptions`.

6. Make the optional Channels transport lifecycle-correct.

   Outcome: WebSocket `connect()` accepts or rejects promptly, stream delivery
   runs in a cancellable task, and disconnect stops the stream loop.

   Files to touch:

   - `weft/commands/events.py`
   - `weft/client/_task.py`
   - `integrations/weft_django/weft_django/realtime.py`
   - `integrations/weft_django/weft_django/channels.py`
   - `integrations/weft_django/tests/test_weft_django.py`
   - `integrations/weft_django/README.md`
   - `docs/specifications/13C-Using_Weft_With_Django.md`

   Implement:

   - Add an optional cancellation signal to the realtime event iterator in the
     shared command/client path, such as a `threading.Event`-like object or a
     narrow protocol with `is_set()`.
   - Keep `Task.realtime_events(follow=True)` backward-compatible when no
     cancellation signal is supplied.
   - Replace the sync `JsonWebsocketConsumer` streaming-in-`connect()` pattern
     with an async Channels consumer or another Channels-native cancellable
     structure.
   - `connect()` should validate transport, authorize, resolve the task,
     accept, start a background stream task, and return.
   - `disconnect()` should signal cancellation and wait boundedly for the stream
     task to exit.
   - Reuse `weft_django.realtime.task_event_payload`; do not duplicate payload
     shaping in Channels.

   Tests:

   - The module still raises `ImproperlyConfigured` when Channels is not
     installed.
   - When Channels is installed, the consumer exposes URL patterns and uses the
     shared payload helper.
   - Add a lifecycle test that proves `connect()` does not synchronously drain a
     never-ending event iterator before returning. Prefer Channels' own testing
     tools if available without a new runtime dependency.
   - Add a cancellation test around the shared realtime iterator or the Django
     wrapper so disconnect stops iteration without waiting for task completion.

   Done signal:

   - A quiet long-running task can have a WebSocket viewer disconnect without
     pinning `connect()` until task completion.

7. Strengthen documentation, install guidance, and migration notes.

   Outcome: users see `weft.client` and `weft_django` as stable public surfaces,
   with clear transaction and typing behavior.

   Files to touch:

   - `README.md`
   - `integrations/weft_django/README.md`
   - `docs/specifications/13C-Using_Weft_With_Django.md`

   Implement:

   - Add a short `weft.client` section covering prepared submissions and typed
     exceptions.
   - In the Django README, document that deferred helpers validate before
     commit and enqueue after commit.
   - Document that payloads are snapshotted at deferred registration time.
   - Document that `weft-django` is typed and ships `py.typed`.
   - Add an operator note that Channels support is lifecycle-correct but still
     optional and diagnostic, not product truth.

   Done signal:

   - The docs explain why a bad deferred spec reference fails before commit
     instead of after app rows are committed.

8. Run the verification ladder and update lessons only if a repeated failure
   mode was confirmed.

   Outcome: first-class claims are backed by real tests, packaging checks, and
   import-boundary checks.

   Required commands:

   ```bash
   ./.venv/bin/python -m pytest tests/core/test_client.py -q -n 0
   ./.venv/bin/python -m pytest integrations/weft_django/tests/test_weft_django.py -q -n 0
   ./.venv/bin/python -m pytest tests/architecture/test_import_boundaries.py -q -n 0
   ./.venv/bin/python -m pytest tests/system/test_optional_extras.py tests/system/test_release_script.py -q -n 0
   ./.venv/bin/python -m ruff check weft integrations/weft_django tests
   ./.venv/bin/python -m ruff format --check weft integrations/weft_django tests
   ./.venv/bin/python -m mypy weft integrations/weft_django/weft_django --config-file pyproject.toml
   uv build
   cd integrations/weft_django && uv build
   ```

   Additional package proof:

   - Inspect the built root wheel and sdist for `weft/py.typed`.
   - Inspect the built Django wheel and sdist for `weft_django/py.typed`.
   - Install built wheels in a temporary environment and import
     `weft.client`, public exceptions, and `weft_django`.

   Review gate:

   - Run an independent completed-work review after tasks 1 through 7 land.
   - Ask the reviewer to focus on public API completeness, transaction safety,
     package typing, import boundaries, and Channels lifecycle behavior.

## Rollback

Rollback should be straightforward because the plan does not change durable
queue names, state payloads, or task execution semantics.

Safe rollback path:

- Revert `weft.client.prepare*` and the Django `*_on_commit` use of prepared
  submissions together. Do not leave Django calling a removed client seam.
- Revert Channels lifecycle work independently if it proves unstable. SSE must
  remain the default diagnostic transport.
- Keep `py.typed` marker files if they have shipped in a public release. Removing
  package typing after publishing would be a public contract regression.
- Keep public exception exports if they have shipped. Downstream integrations
  may start catching them immediately.

Rollout order:

1. ship public exception exports and `py.typed`
2. ship prepare/submit-prepared client seam
3. switch Django deferred helpers to the seam
4. switch Channels lifecycle implementation
5. update docs and release

No database migration or queue migration is required.
