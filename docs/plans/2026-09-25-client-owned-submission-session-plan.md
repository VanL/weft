# Client-Owned Submission Session Reuse

Status: draft
Source specs: docs/specifications/14-Python_API_Surfaces.md [PY-1], [PY-3]; docs/specifications/13C-Using_Weft_With_Django.md [DJ-2.1], [DJ-3.1], [DJ-8.2], [DJ-8.3], [DJ-8.4], [DJ-9.1], [DJ-13.2], [DJ-17.3], [DJ-19]; docs/specifications/05-Message_Flow_and_State.md [MF-1]; docs/specifications/04-SimpleBroker_Integration.md [SB-0.4]
Superseded by: none

Class: 5. This adds a public client lifecycle, changes resource ownership on
the durable submission spine, and coordinates core and Django integration
rollout. Hardening and independent review apply.

## Goal

Let one explicitly retained `WeftClient`, and the Django adapter that owns it,
keep a SimpleBroker `BrokerSession` lease across a burst. Each submission still
owns one short connection operation and transaction. This removes repeated
context resolution, session acquisition, and PostgreSQL pool construction
without holding a checkout or transaction while Django code, manager recovery,
or task execution waits. Each submission still creates its bounded operation
object and may perform backend validation required by SimpleBroker.

The Django surface should reuse that owner automatically across an ordinary
synchronous request, following Django's own lazy owner-local resource pattern.
Explicit client scopes cover management commands, background workers, and
caller-defined batches. The design must not require a raw core client keyword
on one selected helper or introduce a process-global client/session singleton.

## Source Documents

Normative sources:

- `docs/specifications/14-Python_API_Surfaces.md` [PY-1] owns the public
  `WeftClient` and context lifecycle; [PY-3] owns preparation, durable
  submission, typed failures, accepted-TID handling, and the current bounded
  session implementation note.
- `docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1] owns use of the
  public core client; [DJ-3.1] owns app-ready side effects; [DJ-8.2], [DJ-8.3], and
  [DJ-8.4] own native, decorated, reference, pipeline, and deferred submission
  surfaces; [DJ-9.1] owns `transaction.on_commit()` behavior; [DJ-13.2] owns
  settings and context snapshot timing; [DJ-17.3] owns broker-backed proof; and
  [DJ-19] owns paired core/integration rollout.
- `docs/specifications/05-Message_Flow_and_State.md` [MF-1] owns queue-first
  acceptance and the rule that the spawn write commits before readiness
  recovery.
- `docs/specifications/04-SimpleBroker_Integration.md` [SB-0.4] owns resolved
  target/config identity and Weft's use of SimpleBroker lifecycle primitives.
- `../simplebroker/docs/specs/16-python-library-api.md` "Process session
  handle" defines `BrokerSession` leases, per-thread cached cores, per-operation
  PostgreSQL checkouts, close ordering, and inherited-handle fork rejection.

Required implementation guidance:

- `docs/agent-context/decision-hierarchy.md` [DOM-15]
- `docs/agent-context/principles.md`
- `docs/agent-context/engineering-principles.md`
- `docs/agent-context/runbooks/runtime-and-context-patterns.md`
- `docs/agent-context/runbooks/testing-patterns.md`
- `docs/agent-context/runbooks/adversarial-acceptance-probes.md`
- `docs/agent-context/runbooks/hardening-plans.md`
- `docs/agent-context/runbooks/review-loops-and-agent-bootstrap.md`

Relevant historical context:

- `docs/plans/2026-09-15-explicit-broker-session-lifetimes-plan.md` is
  completed. Its command-owned scope and helper-borrows-owner rule remains the
  base rule. This plan adds a longer-lived, explicit client owner.
- `docs/plans/2026-09-17-submission-manager-check-cost-plan.md` is completed.
  Its single-operation spawn-write plus initial-observation path, fresh
  observation per task, accepted-TID annotation, and connection-before-recovery
  ordering remain intended.
- `docs/plans/2026-09-14-django-core-context-resolution-plan.md` is completed.
  Core still owns resolution; Django must not reconstruct Weft config.

## Spec Baseline and Promotion Strategy

Baseline commit: `cfa5bb2174fe101ecb26e435e05f2e83ec983336`.

The governing pre-change text is
`docs/specifications/14-Python_API_Surfaces.md` [PY-1], [PY-3] and
`docs/specifications/13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-3.1],
[DJ-8.2], [DJ-8.3], [DJ-8.4], [DJ-9.1], [DJ-13.2], [DJ-17.3], and [DJ-19]
at that commit. The pre-change contract says every submission owns a bounded
session and Django acquires a fresh client for each helper call.

Promotion uses strategy A, in-file text first. Promote the reviewed lifecycle
and ownership rules plus plan backlinks into both governing specs before
production code cites or implements them. Run metadata and spec-hygiene tests
on that spec-only delta, then implement against the promoted text. Because this
work remains uncommitted unless the user asks otherwise, the recorded promotion
baseline is the commit above plus the explicit worktree diff for those two spec
files.

## Django Lifecycle Investigation

The supported package range is Django `>=5.2,<7`. The 5.2 and 6.1 source trees
use the same `BaseConnectionHandler` shape: lazy wrappers stored in
`asgiref.local.Local`, with `thread_critical=True` for database connections.
Database wrappers record creator thread, create the physical connection lazily,
and close conditionally at request start/finish. Django's cache handler uses the
same generic shape and unconditionally calls `close_all()` on request finish.

The supported pieces to reuse directly are the public request/settings signals,
`transaction.on_commit()`, and `asgiref.local.Local`. `BaseConnectionHandler`
is an internal utility with no registration point for a third-party resource;
`django.db.connections.close_all()` emits no extension hook. Copy its small
owner-local pattern rather than placing Weft in the ORM registry or mutating
handler internals.

This design also follows Django's PostgreSQL direction: its documented ASGI
guidance favors backend pooling over persistent checked-out connections. A
Weft request client retains a SimpleBroker session/pool lease, not a checkout.
Each operation still returns its checkout immediately. See Django's
[persistent connection guidance](https://docs.djangoproject.com/en/5.2/ref/databases/#persistent-connections),
[request signals](https://docs.djangoproject.com/en/5.2/ref/signals/#request-response-signals),
and [`on_commit` timing/order](https://docs.djangoproject.com/en/5.2/topics/db/transactions/#performing-actions-after-commit).

Do not reuse `django.db.connection` itself. The Weft broker may target a
different backend/database, SimpleBroker owns its schema and retry semantics,
and an ORM connection would couple durable spawn acceptance to Django's current
transaction and autocommit state.

## Context and Key Files

Current flow:

1. Each Django submission helper calls
   `integrations/weft_django/weft_django/client.py::get_core_client`.
2. `get_core_client()` resolves a fresh core context and constructs a new
   `WeftClient`.
3. `PreparedSubmission.submit()` calls
   `weft.commands.submission._submit_prepared_outcome()` with only a context.
4. `_submit_prepared_outcome()` owns and closes a fresh `BrokerSession`, while
   one `session.connection()` operation contains the spawn insert and initial
   manager observation.
5. That connection ends before `ensure_manager_after_submission()` performs
   any reconciliation, process startup, or wait.

Files expected to change:

- `weft/client/_client.py`: public client scope, lazy submission-session
  ownership, fork/thread guards, close behavior, and the internal prepared
  submission dispatch seam.
- `weft/client/_prepared.py` and `weft/client/_types.py`: route a prepared
  request through its owning client rather than bypassing the client lifecycle.
- `weft/commands/submission.py`: accept an optional borrowed `BrokerSession`,
  distinguish borrowed from owned cleanup, and retain the current bounded
  connection operation.
- `integrations/weft_django/weft_django/client.py`: deepen
  `DjangoWeftClient` into an explicit context-managed submission owner; make
  module helpers borrow the active request client or use one-shot ownership.
- `integrations/weft_django/weft_django/lifecycle.py` (new, if a separate
  module keeps `client.py` cohesive): a small request-local registry modeled on
  Django's connection/cache handlers, plus lifecycle signal receivers.
- `integrations/weft_django/weft_django/apps.py`: idempotently register the
  public Django lifecycle signals without resolving settings or touching a
  broker in `ready()`.
- `integrations/weft_django/weft_django/__init__.py`: preserve the public
  inventory; explicit clients clean up through their own context/`close()`.
- `tests/core/test_client.py`, `tests/core/test_public_client_contracts.py`,
  `tests/commands/test_submission.py`, and
  `integrations/weft_django/tests/test_weft_django.py`: contract, resource,
  transaction, fork, and integration tests.
- The source specs above and `docs/plans/README.md`: normative promotion,
  implementation mapping, backlinks, and plan indexing.

Do not add a parallel connection pool, Django model, background cleanup thread,
or generic resource container. Reuse `WeftContext.session()`,
`BrokerSession.connection()`, `PreparedSubmissionRequest`,
`submit_spawn_request(..., broker=...)`, and
`observe_manager_availability(..., broker=...)`.

## Invariants and Constraints

- A `BrokerSession` is a lease on process-shared backend state. It is not a
  checked-out PostgreSQL connection. The client may retain the lease; every
  submission must enter and exit its own `session.connection()` operation.
- The spawn insert and initial availability observation continue to share one
  connection operation. The insert commits and its TID is captured before
  initial observation or resource cleanup can fail.
- The connection operation must end before
  `ensure_manager_after_submission()` can reconcile, start a manager, wait for
  PONG, or enter any nested same-key session. No transaction or pool checkout
  spans those actions.
- Slow readiness recovery may open and close another same-key session and
  recycle the retained thread core after the original operation ends. That is
  acceptable: the lease/pool survives and the next submission may lazily
  recreate the core. This plan does not optimize recovery.
- Manager evidence remains fresh for every submission. This change introduces
  no readiness TTL, liveness cache, or cross-submission observation reuse.
- A capability that receives a borrowed session never closes it, recycles its
  thread cache, or transfers its ownership. It closes only the operation it
  opened. The same capability must retain its current owned-session behavior
  when no session is supplied.
- A retained client may lend its session only when the materialized TaskSpec's
  effective runtime context is the client's exact resolved context. An
  explicitly different `spec.weft_context` uses the existing bounded owned
  path. Do not add an unbounded session-per-root map.
- Retained reuse applies consistently to `submit`, `submit_spec`,
  `submit_pipeline`, `submit_command`, and `PreparedSubmission.submit()`.
  Factor command TaskSpec preparation from
  `weft.commands.submission.submit_command()` so the client can use the same
  dispatch seam; do not add a second enqueue implementation or a new public
  command-preparation API.
- Retained submission resources are lazy. Constructing a client may keep the
  existing bounded `build_context()` broker ensure/target validation required
  by [PY-1], but construction, entry, preparation, settings import, and callback
  registration create no retained `BrokerSession`, persistent Queue, or
  long-lived checkout. Request lifecycle signals with no submission do not
  construct a client and remain broker-free.
- The retained-session mode is owner-thread scoped. Submissions and `close()`
  on an active retained client from another thread raise before broker I/O.
  Record the strong `threading.current_thread()` object, not only a reusable
  integer thread ID. Ordinary unentered clients preserve one-shot behavior.
- The client lifecycle has three states: bounded/unentered, retained-active,
  and cleanup-pending. Successful `close()` releases the retained submission
  session and returns to bounded mode. If close raises, preserve the handle and
  ownership in cleanup-pending state; forbid submission/re-entry, and allow
  only the same owner thread to retry `close()`. A successful retry returns to
  bounded mode. Never clear the only named lease owner after failed cleanup.
- `WeftClient.__exit__` matches `BrokerSession.__exit__` priority. Without a
  body exception, ordinary close failure propagates and leaves cleanup pending.
  With a body exception, that exception remains primary and ordinary cleanup
  failure is attached diagnostically; cleanup remains retryable. A non-ordinary
  cleanup `BaseException` keeps upstream priority.
- In a forked child, an inherited `BrokerSession` is never used. Before any
  inherited Weft lock or thread check, compare PID and call the inherited
  handle's public `close()`. SimpleBroker guarantees that inherited close
  touches no parent resource and detaches the child copy safely. Then clear the
  handle and use child-owned bounded state unless explicitly re-entered. The
  Django request registry similarly replaces the inherited entry. The parent
  remains valid.
- The Django registry has a module owner PID. Every request/settings/lookup
  entry point compares PID before acquiring the generation lock or touching
  `Local`. A child replaces the inherited lock, generation state, and local
  container first; it never acquires or releases a parent lock copy. It then
  reads only the fork-surviving current context's entry from the saved old
  `Local`, child-closes that client's session through the public fork-safe path,
  discards the old container, and builds child-owned state. Entries owned by
  vanished parent threads are never traversed or closed in the child.
- A retained client keeps the `WeftContext`, Config, and target snapshot it
  resolved at construction. Settings, environment, project-file, and CWD
  changes do not retarget it. A new `get_client()` observes new settings.
- Automatic lifetime is request-scoped, not thread- or process-scoped. A
  private registry uses `asgiref.local.Local(thread_critical=True)`, the same
  owner-local primitive as Django database connections, and public
  `request_started`/`request_finished` signals. WSGI runs directly on the
  request thread. Under ASGI, automatic reuse is limited to synchronous work in
  Django's thread-sensitive request executor; direct async-view helper calls
  remain one-shot in this slice.
- On supported ASGI versions, `ASGIHandler` wraps the request in one
  `ThreadSensitiveContext`. Django 5.2 and 6.1 use `Signal.asend()` for start
  and thread-sensitive response close. Therefore
  request-start, a synchronous view, and request-finish receivers use the same
  single-thread executor. This is a source-tested compatibility fact, not an
  assumption that synchronous signal receivers run on the event-loop thread.
  Keep a version-aware firing thread-identity test on both supported series.
- `request_finished` closes and deletes only the current request-local client.
  On ordinary cleanup failure it retries once immediately on the same owner
  thread. A successful retry deletes the entry after logging the first failure.
  Repeated failure is logged as an owner-lifecycle invariant violation and
  leaves the entry cleanup-pending; any later access in that same surviving
  owner context retries before admitting work. Django does not guarantee such
  an access, especially after ASGI `ThreadSensitiveContext` teardown, so the
  operational remedy for a repeated failure is process recycling. The adapter
  does not add a cross-thread cleanup registry or silently close from another
  owner. Non-ordinary `BaseException` keeps priority, and explicit `close()`
  propagates failures. No finalizer or `atexit` performs broker I/O or recycles
  a foreign thread's cache.
- Outside an active request, use `with get_client() as client:` or explicit
  same-thread cleanup in a worker `finally` block. Module helpers outside a
  request remain bounded one-shot operations and retain nothing.
- In normal request flow, Django runs `on_commit` callbacks when the atomic
  block exits, before response close and `request_finished`, so one request
  client covers the burst. A callback executed later (including
  `captureOnCommitCallbacks(execute=True)` after response close) uses the
  captured client/context through the bounded path; it never rereads settings.
- For explicit clients, place `with get_client() as client:` outside
  `transaction.atomic()` to get retained callback reuse. Reversed nesting is
  valid but bounded after client close, not silently redirected.
- Keep one Django `transaction.on_commit()` callback per requested task. This
  preserves registration order and nested-savepoint rollback semantics. Do not
  combine callbacks into an implicit batch.
- A later callback still runs after an earlier accepted submission has degraded
  readiness. Broker-write errors and authoritative manager rejection keep the
  current behavior. Cleanup failure after acceptance retains the accepted-TID
  annotation.
- Queue names, TaskSpec schema, TID generation, receipt/result shapes, manager
  policy, and durable outbox behavior do not change.

## Proposed Spec Delta

Use specification-first promotion. Before production code changes, replace or
add the following normative rules and obtain independent approval of the spec
diff.

### `14-Python_API_Surfaces.md` [PY-1]

Replace the statement that `WeftClient` never caches a live session with this
contract:

- `WeftContext.session()` remains a factory for caller-owned
  `BrokerSession` handles.
- An ordinary, unentered `WeftClient` preserves bounded per-operation session
  ownership.
- `WeftClient` is a context manager. Entering it activates a single-thread,
  lazy retained-submission scope; the first same-context submission creates one
  `BrokerSession`, and later same-context submissions borrow it.
- A successful `close()` is idempotent and closes the retained handle on its
  owner thread. The client returns to bounded behavior and may be entered again
  sequentially. Nesting/re-entering an active client is invalid. A failed close
  preserves the handle in cleanup-pending state; submission/re-entry are
  forbidden until same-owner retry succeeds.
- `__exit__` preserves an active body exception over ordinary close failure by
  attaching cleanup diagnostics. With no body exception, close failure
  propagates. Non-ordinary cleanup exceptions keep SimpleBroker's priority.
- Retention spans only the session lease. It never spans a queue operation,
  backend checkout, SQL transaction, or readiness wait.
- Active retained clients are thread-affine and abandon inherited submission
  resources after a fork through inherited `BrokerSession.close()` as stated in
  this plan's invariants. Unentered and successfully closed clients keep bounded compatibility. Already returned `Task` and
  `PreparedSubmission` handles remain usable after client close.

Update the [PY-1] implementation mapping to name `_client.py` lifecycle tests
and the upstream SimpleBroker process-session contract.

### `14-Python_API_Surfaces.md` [PY-3]

Promote the current implementation note into a resource-ownership rule:

- `_submit_prepared_outcome(..., session=None)` owns one bounded session when
  no session is supplied and borrows the supplied session otherwise.
- Both paths open one bounded connection operation for the committed spawn
  write and immediate availability observation, capture the accepted TID
  before operation exit, and release the operation before recovery.
- A borrowed session is never closed or recycled by submission.
- `PreparedSubmission.submit()` asks its client to select the retained or
  bounded path. It may outlive a retained scope and then uses its captured
  context through bounded ownership. Within an active retained scope it borrows
  its owning client's session; after scope exit or in a forked child it uses
  bounded ownership and never borrows a fork-inherited session.
- Every `WeftClient` submission method, including `submit_command()`, uses that
  same selection seam; no method silently keeps per-call session churn while
  the client is retained.
- Alternate effective runtime roots never borrow the base client's session in
  this version.

Keep the existing fresh-readiness and typed-error rules unchanged.

### `13C-Using_Weft_With_Django.md` [DJ-2.1], [DJ-8.2], [DJ-8.3], [DJ-8.4]

Add the following public contract:

- `DjangoWeftClient` is a context manager and exposes the decorated, TaskSpec, spec-reference, and
  pipeline immediate/deferred submission methods represented by the existing
  module helpers.
- Module helpers are compatibility facades. During an active synchronous
  request they delegate through one lazy request-local `DjangoWeftClient`;
  outside a request they use an unentered one-shot client. There is no special
  `client=` keyword on only `submit_taskspec_on_commit`.
- `get_client()` remains an explicitly owned fresh client for application
  batches. Entering it activates retained submission reuse; `close()` is
  deterministic and same-thread. It does not return the private request cache.
- The request-local or explicitly entered wrapper owns an active core client
  scope, but the broker session remains lazy until a same-context submission.
- Deferred validation and payload snapshots still occur before callback
  registration. The callback holds the `PreparedSubmission`, not ambient
  settings. If its retained request/client scope has ended, submission falls
  back to bounded ownership using that captured context.

### `13C-Using_Weft_With_Django.md` [DJ-9.1]

Add that callbacks registered in one Django transaction remain separate, run
in registration order under Django's rules, and may reuse the explicitly
entered or request-local client's session. Normal request callbacks execute
before `request_finished`. Test callbacks or reversed explicit nesting may run
after retained-scope close and then use the bounded path. Rollback-discarded
callbacks create no retained submission session or spawn row; first client
resolution may already have performed [PY-1]'s bounded broker ensure.

### `13C-Using_Weft_With_Django.md` [DJ-13.2]

Keep "does not cache contexts globally" and distinguish the private
request-local snapshot from explicit `get_client()`. One synchronous request
normally resolves once; the next request or explicit client may observe changed
`WEFT_DJANGO`, `BASE_DIR`, environment, or CWD. `setting_changed` for
`WEFT_DJANGO`/`BASE_DIR` increments a process generation, closes/deletes only
the current-local client after successful cleanup while preserving the separate
request-active marker, and causes other owner contexts to rotate on next
access. A cleanup failure parks that client for same-owner retry rather than
installing a replacement. It never closes another thread's resources.
Inherited request entries are discarded by PID before use and rebuilt in the
child. A settings change is the explicit exception to once-per-request
resolution: already prepared callbacks keep the old immutable client/context,
while later helpers in that request resolve the new generation.

### `13C-Using_Weft_With_Django.md` [DJ-17.3], [DJ-19]

Add request-local and explicit-client resource lifecycle, WSGI and ASGI-sync,
real-backend, fork, streaming-response, settings-generation, and transaction
scope cases to broker-mode guidance. State the coordinated release order: core
client/session borrowing first, then Django request lifecycle support against
that declared core floor.

### `13C-Using_Weft_With_Django.md` [DJ-3.1]

Permit `AppConfig.ready()` to register idempotent lifecycle signal receivers
while preserving the existing rule that ready-time imports, autodiscovery, and
registration do not resolve a Weft context or touch the broker.

### `05-Message_Flow_and_State.md` [MF-1]

Update the resource note to cover both owned and borrowed sessions. In both
cases the spawn insert commits, the accepted TID is captured, and the shared
connection operation ends before manager reconciliation or startup. Session
retention must not change queue-first acceptance.

### `04-SimpleBroker_Integration.md` [SB-0.4]

Add that an explicitly scoped public client may own a lazy `BrokerSession`
lease for its exact context/config identity. Helpers borrow it, operations own
their checkouts, and alternate roots use bounded sessions. Link the new plan in
every touched spec's Related Plans section and update nearby implementation
mappings in the same spec-promotion slice.

## Design Decisions and Rejected Alternatives

**Chosen: client lifecycle plus Django object methods.** The core client is the
right owner because `PreparedSubmission` already retains it and because core,
not Django, can compare the effective runtime context. The Django wrapper is
the right adapter owner because every helper family can share one behavior.

**Chosen: copy Django's public lifecycle pattern, not its ORM connection.**
Django 5.2 and 6.1 use owner-local lazy wrappers, creator-thread enforcement,
and request lifecycle hooks for database/cache resources. Use the public
`request_started`, `request_finished`, and `setting_changed` signals plus
`asgiref.local.Local(thread_critical=True)`. Do not put Weft in
`django.db.connections`: that registry has no third-party extension hook, and
borrowing an ORM connection would couple Weft acceptance to Django transaction
state. Do not subclass `BaseConnectionHandler` or mutate its private
`_connections`/`_settings`; its small stable pattern is copied locally so Weft
can add PID, generation, request-active, and cleanup-error rules explicitly.

**Rejected: add only `client=` to `submit_taskspec_on_commit`.** That leaves
decorated tasks, spec references, pipelines, and immediate submission on the
slow path. It also makes resource ownership an optional convention at one
function instead of a client invariant.

**Rejected: let a client own a checked-out PostgreSQL connection.** A pool
checkout or SQL transaction across Django work and manager waits would reduce
pool capacity and make failures much harder to isolate. The retained object is
the session lease; the connection remains operation-scoped.

**Rejected: process-global or arbitrary thread-lifetime cache.** Django has no
general thread-teardown signal. The automatic scope therefore exists only
between request start/finish in the same synchronous/thread-sensitive request
context. Explicit client scope gives non-request code a real lifecycle owner.

**Rejected: session map keyed by alternate TaskSpec roots.** It is unbounded
and not needed for the measured Django path. Alternate roots keep the correct
bounded behavior.

**Rejected: implicit `submit_many`.** Batching needs a separate contract for
partial acceptance, callback/savepoint behavior, error attribution, and
readiness observation. Session reuse delivers the immediate setup win without
coupling those decisions.

## Dependency-Ordered Implementation Slices

### Slice 1: Baseline and red tests

1. Record the committed spec baseline SHA and the installed SimpleBroker
   version/API contract in the Execution Log.
2. Add focused failing tests before changing production code:
   - a context-managed core client performs two same-root prepared submissions
     with one session handle but two independently exited connection operations;
   - the baseline does not expose `close()`/context management or route
     prepared submission through a client borrower;
   - a real WSGI request registering two Django on-commit submissions resolves
     one request client and one retained session rather than two;
   - no retained session, persistent Queue, or long-lived checkout exists
     before commit; the diagnostic separately records the existing bounded
     context/broker ensure during first client resolution.
3. Add a repeatable production-shaped diagnostic with monotonic spans around:
   Django/core client resolution, submission operation acquisition, the spawn
   insert/TID return, and post-TID readiness. Use real SQLite first and
   PostgreSQL through `bin/pytest-pg`; counters are supporting evidence only.
   Record sample count, median/p95, backend, and limitations. Do not add a
   timing threshold to CI.

Stop if the measured churn is outside context/session/target acquisition or if
the test can pass with a fake broker that never commits a spawn row.

### Slice 2: Promote the normative contract

1. Apply the exact spec delta above, update implementation mappings and Related
   Plans backlinks, and run metadata/spec-hygiene tests.
2. Request independent review of only the spec/plan delta. Resolve every
   blocker before changing production code.
3. If review changes lifecycle, thread, fork, callback, or cleanup semantics,
   revise this plan first and record the deviation.

### Slice 3: Add the core owner/borrower seam

1. Add `WeftClient.__enter__`, `__exit__`, and `close`. Activation records PID
   and thread identity but creates no session. Close returns the client to
   bounded mode; sequential re-entry is allowed and active nesting is rejected.
   Keep lifecycle state private; do not add a public introspection property.
   Add cleanup-pending state: failed close retains its handle, blocks all work
   except same-owner close retry, and clears only after retry succeeds.
2. Move prepared dispatch behind a private method on `WeftClient`.
   It chooses borrowed same-context submission only for an active, open client
   on its owner PID/thread. It chooses the bounded path for an unentered/closed
   client, a prepared handle submitted after scope exit, or an alternate
   runtime root.
3. Factor the existing command-to-TaskSpec body into one private preparation
   helper and route `WeftClient.submit_command()` through the same client-owned
   dispatch. Preserve the public command signature and validation/errors.
4. Add `session: BrokerSession | None = None` to the internal submission
   capability. Factor one private body so owned and borrowed paths cannot drift.
   In the borrowed branch open/close only `session.connection()`; in the owned
   branch preserve the existing session context manager. Do not move
   `ensure_manager_after_submission()` into the connection block.
5. On PID change, before touching inherited client locks or thread state, call
   the inherited `BrokerSession.close()` and then clear it. Its documented fork
   path touches no parent resource. Fall back to child-owned bounded submission;
   a later explicit entry may retain a child session. On ordinary close,
   require the owner thread and preserve cleanup-pending state and upstream
   exception priority.

Run the core and command tests after each step. Stop if a borrowed session is
closed by a helper, a pool checkout remains active during ensure/recovery, an
accepted TID can be lost, or inherited session state is touched in the child.

### Slice 4: Reuse Django's request lifecycle across every facade

1. Move the bodies of decorated, TaskSpec, spec-reference, and pipeline
   submission helpers onto `DjangoWeftClient`. Keep shared private preparation
   helpers where they already avoid duplication. Make each module function a
   thin delegate to the current request client or a one-shot client.
   Observation/control facades (`status`, `terminal_snapshot`, `snapshot`,
   `result`, `stop`, and `kill`) use the same request client for context reuse,
   but retain their existing operation ownership and public results/errors.
2. Make `DjangoWeftClient.__enter__`, `__exit__`, and `close` delegate to its
   core client. `get_client()` still resolves a fresh explicit client; it does
   not expose the private request entry.
3. Implement a small private registry with
   `Local(thread_critical=True)`. Keep request-active state separate from the
   replaceable client record; the latter contains PID, settings generation, and
   at most one `DjangoWeftClient`. It lazily resolves
   the client on the first module submission, enters its retained scope, and
   never creates one merely for request start/finish.
4. In `AppConfig.ready()`, connect stable-UID synchronous receivers to
   `request_started`, `request_finished`, and `setting_changed`. Request start
   resets stale current-owner state and marks the scope active. Finish closes
   and deletes only that context's client. Setting change for `WEFT_DJANGO` or
   `BASE_DIR` advances a process generation and closes/deletes only the current
   client after success without clearing request-active state; other contexts
   rotate on next access. On ordinary cleanup failure, park the entry and log
   at request finish/setting change; the next owner-thread access retries and
   raises if retry still fails. At request start, retry stale cleanup before
   marking the new request active; repeated failure propagates and aborts start
   rather than admitting work alongside an orphaned lease. Non-ordinary
   exceptions always propagate. Registration and empty lifecycle events
   perform no broker I/O. Protect the generation counter
   with a small lock; never hold that lock while resolving settings, opening a
   client/session, submitting, or closing resources. At request finish, retry
   one ordinary close failure immediately on the same owner thread. Delete the
   entry only after successful close. On repeated failure, log that the owner
   context may end before another retry and retain cleanup-pending state for
   any later access in that same surviving context; do not create a cross-thread
   retry registry.
   Guard every registry/receiver entry with a lock-free module-PID comparison;
   on mismatch replace the generation lock, counter, and `Local` before any
   inherited lock/local access.
5. Detect a running event loop before automatic borrowing. Direct async-view
   immediate submissions stay one-shot. Deferred helpers and ORM transaction
   work retain Django's requirement to run through a synchronous
   thread-sensitive bridge; this slice does not make them async-safe. Sync
   views under ASGI's thread-sensitive executor use the request entry. Prove on
   both supported Django series that the version-specific request-start send,
   the sync view, thread-sensitive response close, and the synchronous
   `request_finished` receiver all use the same `ThreadSensitiveContext`
   executor thread. Do not rely on an undocumented signal-thread assumption.
6. Preserve one callback per call and the existing prepare-before-register
   order. A callback executed before request finish borrows the retained
   session. One executed after finish uses its captured core client in bounded
   mode. Neither path rereads settings.
7. Add public same-thread cleanup for background/management owners or document
   context-manager use. Do not add middleware, finalizers, `atexit`, a
   background reaper, or a process cache.

Stop if rollback opens a session, a helper reacquires settings inside its
callback, one helper family bypasses the request client, `request_finished`
closes a foreign thread/context, a direct async view shares a blocking client,
or already-prepared work is redirected.

### Slice 5: PostgreSQL proof, docs, and release boundary

1. Run the real PostgreSQL resource tests. Prove repeated same-root submissions
   share the process-session/pool identity while every submission returns its
   checkout before the next readiness/recovery phase. Force one broken
   connection and prove SimpleBroker discards/replaces it without replacing the
   client or losing later submissions.
2. Repeat the burst diagnostic and report pre/post medians for all four spans.
   The acceptance claim is reduced client/context/session churn and stable
   pool identity, not a fixed wall-clock speedup under arbitrary manager load.
3. Update Django usage docs with automatic sync-request reuse, direct-async and
   streaming-response limits, explicit client/transaction nesting for
   non-request batches, same-thread cleanup for workers, settings snapshots,
   and the distinction between a retained lease and a checkout.
4. Update `CHANGELOG.md` and release notes with the new public client lifecycle,
   bounded-checkout guarantee, Django request reuse, async limits, and any
   Django integration core-version floor change.
5. If the Django package declares a core version floor, obtain owner approval
   for the project-config edit, then raise it to the first core release that
   provides this lifecycle. Release core before the Django integration. Do not
   ship a reflection-based compatibility fallback.
6. Prove the lifecycle tests against Django 5.2 and the newest supported 6.x.
   If this requires a CI/release-gate matrix change, obtain owner approval for
   that project-config edit. Do not claim the `>=5.2,<7` contract from one
   environment.
7. Run full gates and an independent final diff review. Close the plan only
   after specs, implementation mappings, tests, measurements, and release notes
   agree.

## Test and Acceptance Matrix

### Core lifecycle

- Request start/finish without submission is broker-I/O-free. Client
  construction may perform the existing bounded context/broker ensure; entry
  and preparation add no retained session, persistent Queue, or checkout.
- First same-context submit lazily creates one session; N sequential submits use
  that handle and N independently exited connection operations.
- `submit`, `submit_spec`, `submit_pipeline`, `submit_command`, and a separately
  prepared handle all select the same retained seam; their existing input and
  error contracts do not change.
- Unentered clients retain N bounded owned-session calls. Closing an unentered
  client is harmless; repeated close is harmless; work after close uses bounded
  ownership; sequential re-entry creates a new retained lease.
- Force same-key close refusal with a live sibling operation on the owner
  thread. The client keeps its handle in cleanup-pending state, rejects
  submission/re-entry, then closes successfully after the operation exits.
- If a `with client:` body raises while ordinary close also fails, the body
  exception remains primary with cleanup diagnostics and same-owner retry can
  finish cleanup. Without a body exception, close failure propagates. Exercise
  non-ordinary cleanup priority separately.
- A prepared request made before close uses the live client when submitted in
  scope. Prepared and new submission after close use bounded ownership with the
  same captured context.
- Active-client use or close from a foreign thread fails without changing the
  owner session. The owner can still submit and close afterward.
- An explicit alternate TaskSpec root uses a bounded alternate session and does
  not enter the base retained session or add a root-cache entry.
- A real fork probe creates the retained parent session, calls the inherited
  handle's public child-safe `close()`, and proves parent pool/locks are untouched. The inherited client submits
  through child-owned bounded state, a fresh/entered child client can retain a
  child session, and the parent still submits/closes afterward. Skip only where
  `os.fork` is unavailable. Run the probe in a dedicated subprocess helper,
  not by forking an xdist worker that may already own pools/listener threads;
  report child results through a pipe and use `os._exit()`.

### Durable submission and cleanup

- A real spawn row is committed and its TID returned on every successful item.
  Failure on item K does not roll back items 1 through K-1.
- On each backend, submit one explicit TID, provoke a real duplicate-TID
  integrity failure, then submit a fresh TID through the same retained client.
  The first and third rows remain independently committed and the client stays
  usable after rollback.
- Spawn write and initial availability observation receive the same non-null
  broker operation; ensure/recovery runs after that operation exits.
- Every call observes fresh readiness. A manager state change between two calls
  is visible even though the session is reused.
- Borrowed-session success, insert failure, observation failure, connection-exit
  failure, and ensure failure leave the borrowed handle open for its owner.
- Owned-session paths still close on all outcomes. Post-acceptance cleanup
  errors carry the accepted TID. An active application exception retains
  priority over ordinary cleanup failure under SimpleBroker's rules.

### Django lifecycle and transaction semantics

- Add fixture URL routes/views for a synchronous WSGI burst, synchronous ASGI
  burst with a barrier for concurrency, direct-async immediate submit,
  synchronous streaming response, and exception response. Instrument core
  client identity plus enter/close PID and strong thread identity while keeping
  real broker writes. Use `Client(raise_request_exception=False)` for the 500
  path. Drive the real `ASGIHandler` with ASGI scopes for concurrent sync-view
  ownership proof because `AsyncClientHandler` does not create production's
  `ThreadSensitiveContext` and closes responses with `thread_sensitive=False`.
  Use `AsyncClient` only for the direct-async one-shot case.
- Every immediate and deferred helper family delegates through
  `DjangoWeftClient`; public signatures and error types remain unchanged except
  for new methods on the object returned by `get_client()`.
- A real WSGI request with N callbacks prepares before registration, opens no
  session before commit, executes in order, reuses one request-local lazy
  session, and closes/deletes it on response close. The next request resolves a
  new client snapshot.
- A nested-savepoint rollback discards only its callbacks; surviving callbacks
  still share the client and no resource is created for discarded work.
- `captureOnCommitCallbacks(execute=True)` after response close submits through
  the captured client's bounded path. Prove this with a `django.test.TestCase`
  outer transaction around a non-streaming `Client` request, so response close
  fires first and captured callbacks execute afterward. Explicit reversed
  nesting does the same; neither path rereads settings.
- `override_settings` for `WEFT_DJANGO` and `BASE_DIR` does not retarget an
  existing request/explicit client. Generation changes rotate only the current
  local immediately; other contexts rotate on access. Prepared callbacks retain
  their absolute root and Config after scope close. A two-root request test
  registers old-generation work, changes settings, registers later work, and
  proves the old callback reaches only the old broker while the later helper
  reaches only the new broker.
- A real ASGI sync view and its callbacks reuse one thread-sensitive request
  client and close it on the same `ThreadSensitiveContext` executor thread.
  Assert the thread identity at request-start receiver, sync view,
  response-close/request-finish receiver, and client close. Concurrent requests
  do not share clients. A direct async-view immediate helper call is one-shot;
  deferred/ORM helpers are tested only through a sync thread-sensitive bridge.
- A streaming response retains the request session until response close;
  exception responses close it; repeated signal registration is idempotent;
  `AppConfig.ready()` and requests with no submission perform no broker work.
- Ordinary request-finish close failures are logged with owner context and
  retried once immediately on the owner thread. Successful retry deletes the
  entry. Repeated failure retains cleanup-pending state and blocks replacement
  in any later access by that same surviving owner context, but ASGI executor
  teardown may end that retry opportunity; the diagnostic requires process
  recycling rather than foreign-thread cleanup. Explicit client close still
  propagates the same failure, and non-ordinary cleanup exceptions are never
  swallowed.
- A setting-generation change preserves `request_active`, rotates the client
  after successful cleanup, and parks/retries on cleanup failure without
  closing another owner context.
- After fork, the private registry discards the inherited entry and creates a
  fresh child client. Parent and child cleanup do not affect each other.
- In the isolated fork helper, hold the registry generation lock from a parent
  sibling thread across fork. Bound child lookup/submission completion, prove
  the child replaces rather than enters that lock, then prove the parent
  registry/client still works and no cross-process close occurred.
- A background thread that closes its entered client in `finally` leaves no
  cached core or session lease.
- An inventory test enumerates every public module facade and asserts submission
  plus observation/control functions take the request-client path intended by
  this plan; export-only/decorator names are excluded explicitly.

### Backend-specific evidence

- SQLite: a retained same-thread client reuses the thread core during its
  scope; close removes it; the database remains readable by a fresh client.
- PostgreSQL: all retained clients for the same target/config use one
  process-shared pool identity; concurrent submissions never hold more
  checkouts than active operations; checkouts return after each operation;
  broken checkout replacement and final pool shutdown follow SimpleBroker.
- Install counters around public `psycopg_pool.ConnectionPool.getconn()` and
  `putconn()` before context construction. Record pool identity and active
  count. Use manager-ready evidence that avoids extra recovery/probe checkouts;
  assert active count is zero inside the wrapped
  `ensure_manager_after_submission()` call and after every submission. For the
  broken-checkout case, close one real checked-out connection before its insert,
  then prove balanced return/discard, replacement, and later successful commit.
- Keep a same-key sibling session alive while the client closes and prove the
  sibling still reads/writes. After every lease closes, existing physical-close
  assertions prove the pool shuts down.
- Keep a same-key sibling operation active on the owner thread and prove close
  refusal does not lose the client lease; exit the operation, retry close, and
  then prove final pool shutdown.
- Do not assert "one physical PostgreSQL connection". Pools may create
  validation and replacement connections. Assert lease/pool identity, bounded
  active checkouts, successful independent commits, and cleanup.

## Verification Commands

Source `.envrc` and use repository-managed executables.

```bash
./.venv/bin/python -m pytest \
  tests/core/test_client.py \
  tests/core/test_public_client_contracts.py \
  tests/commands/test_submission.py \
  integrations/weft_django/tests/test_weft_django.py

bin/pytest-pg \
  tests/core/test_client.py \
  tests/commands/test_submission.py \
  integrations/weft_django/tests/test_weft_django.py

uv run --isolated --python 3.14 --with 'django>=6.1,<6.2' \
  --with pytest --with pytest-xdist --with pytest-timeout \
  --with-editable . --with-editable integrations/weft_django \
  pytest integrations/weft_django/tests/test_lifecycle.py \
  integrations/weft_django/tests/test_weft_django.py

./.venv/bin/python -m pytest \
  tests/commands/test_run.py \
  tests/commands/test_manager_commands.py \
  tests/core/test_manager_runtime_connections.py \
  tests/context/test_context.py \
  tests/system/test_django_fixture_cleanup.py

./.venv/bin/python -m pytest \
  tests/specs/test_plan_metadata.py \
  tests/specs/test_spec_hygiene.py

./.venv/bin/ruff check .

./.venv/bin/mypy weft tests bin \
  integrations/weft_django/weft_django \
  extensions/weft_docker/weft_docker \
  extensions/weft_macos_sandbox/weft_macos_sandbox \
  extensions/weft_microsandbox/weft_microsandbox \
  --config-file pyproject.toml
```

Before declaring the implementation ready to land, run the full non-slow test
suite, the Django 5.2/latest-6.x lifecycle matrix, and any release-contract
tests affected by a dependency-floor change.
Formatting is limited to edited Python files. No commit or push without user
instruction; if the implementation remains uncommitted, report that state in
the handoff rather than recording it in this plan.

## Rollout, Observability, and Rollback

Roll out core first because the Django adapter will depend on the borrowed
session capability and client lifecycle. Then release the Django integration
with its exact core floor. Existing clients that do not enter a retained scope
keep bounded behavior, so old adapters remain compatible with the new core.

For the first production burst, record the same four monotonic spans used in
the baseline plus PostgreSQL pool/check-out counts and manager spawn delay.
Success is a material reduction in pre-TID client/session setup, stable pool
identity across the burst, no growth in active checkouts after each submission,
and unchanged durable acceptance/error behavior. Manager spawn delay is a
separate capacity signal and is not an acceptance metric for this change.

Rollback is ordered Django first, core second. Reverting Django returns module
helpers to one-shot clients. The additive core lifecycle may remain without
changing queue or storage compatibility. No persisted schema, queue name,
message shape, or TID changes, so rollback requires only process restart to
discard retained in-memory clients and sessions. If the new cleanup path leaks
or holds checkouts, disable Django reuse by reverting its request-lifecycle
slice before investigating; do not change SimpleBroker pool limits as a mask.

## Out of Scope

- `submit_many`, bulk callback registration, or multi-row transaction semantics
- manager admission control for nested LLM processes
- manager PING/PONG or startup policy changes
- durable Django outbox changes
- thread/process-wide caches or caches for arbitrary alternate TaskSpec roots
- new SimpleBroker pooling, retry, fork, or cleanup behavior
- cross-thread sharing of one active retained `WeftClient`
- automatic retained submission from direct async-view code

## Independent Review and Fresh-Eyes Gates

Before implementation:

1. A fresh-eyes author pass must check that a zero-context implementer can
   identify the owner, borrow boundary, cleanup thread, fork behavior, settings
   snapshot, alternate-root fallback, and exact stop conditions without making
   a policy choice.
2. An independent reviewer must compare the proposed contract with both Weft
   and installed SimpleBroker code, then review the spec delta. Record findings
   and dispositions in the Execution Log.
3. After each production slice, independently review only that bounded diff and
   rerun its targeted tests. A final reviewer must inspect the complete diff,
   traceability, cleanup failure paths, and PostgreSQL evidence.

Use a different agent family when available. If only same-family review is
available, record that limitation and compensate with direct source citations,
real-backend tests, and a second bounded review pass.

## Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
| --- | --- | --- | --- | --- |
| [DJ-13.2] | Cleanup-pending request clients always retry at the next same-owner request/access. | Request finish retries once immediately; later retry is available only if the owner-local context survives. Repeated ASGI close failure requires process recycling. | Django destroys the per-request `ThreadSensitiveContext` executor, so no supported owner-thread hook can guarantee a future access. A cross-thread registry would violate the ownership contract. | Promoted with the implementation slice. |
| Django compatibility gate | Run request-lifecycle acceptance on Django 4.2 and latest 5.x. | Support `django>=5.2,<7`, dropping unsupported 4.2 and admitting the supported 6.x line. Test current Django 6.1 with the integration package installed normally. | The owner explicitly approved updating the package range after waiving the 4.2 gate. Django 7.0/7.1 were renamed 2028/2029; `<7` is the 6.x major-series cap. | Approved package metadata change; document the supported range. |

## Execution Log

- Planning baseline: `cfa5bb2174fe101ecb26e435e05f2e83ec983336`.
- Initial author analysis rejected a permanently checked-out connection, a
  one-helper `client=` keyword, a process-global cache, and an alternate-root
  session map. The proposed owner is a reusable core client lease; each
  submission operation remains bounded.
- Independent architecture review (same-family): rejected the first draft's
  arbitrary thread/process cache because Django has no general thread-teardown
  hook. A follow-up source audit found a smaller supported boundary: public
  request/settings signals plus Django's thread-critical `Local` pattern give
  automatic reuse for WSGI and thread-sensitive synchronous ASGI work, while
  explicit clients cover non-request code. The review also required bounded
  fallback for prepared callbacks executed after response close, PID-based
  inherited-entry replacement, base-context-only borrowing, and no persistent
  spawn Queue.
- Final bounded architecture review found four blockers in the hybrid draft:
  failed close could lose its lease owner; `__exit__` did not state body-versus-
  cleanup exception priority; fork handling said to detach rather than call the
  public inherited-handle `close()`; and settings rotation could erase the
  request-active marker. The plan now defines cleanup-pending retry, upstream
  exception priority, public inherited close before child reuse, and separate
  request/client state with non-cross-thread rotation.
- Architecture re-review found and resolved one final false premise:
  `WeftClient.from_context()` preserves [PY-1]'s bounded broker ensure during
  construction. The plan now promises no retained submission session/Queue/
  checkout before commit, not zero broker I/O. Final architecture verdict:
  PASS, with no remaining lifecycle or spec blocker.
- Independent test/acceptance review (same-family): added real-backend
  owner/borrower failure tests, independent-transaction recovery, PostgreSQL
  checkout/return balance, sibling-lease survival, alternate-root isolation,
  same-thread enforcement, real-fork coverage, nested-savepoint callbacks, and
  the four-span diagnostic. The plan deliberately does not assert one physical
  PostgreSQL connection or a host-sensitive CI latency threshold.
- Test re-review required exact multi-generation settings semantics, an
  isolated fork probe, public PostgreSQL pool checkout counters, an
  immediate-only direct-async contract, concrete WSGI/ASGI/stream/error
  fixtures, executable Django-version commands, and a complete facade
  inventory; all are now explicit. Its final fork-hardening finding required a
  module PID check and replacement of the inherited generation lock/`Local`
  before child registry access, including a held-parent-lock fork probe.
- Final test/acceptance verdict: PASS, with no remaining verification blocker.
- Fresh-eyes author review: confirmed the revised hybrid plan names the lifecycle
  owner, borrow boundary, operation boundary, cleanup thread, fork rule,
  settings snapshot, callback fallback, alternate-root behavior, error
  priority, WSGI/ASGI boundary, streaming lifetime, rollout order, and stop
  conditions. Corrected the earlier terminal-close rule so Django test
  callbacks and reversed explicit nesting use bounded captured-context
  submission.
- Cross-family review: Claude Opus 4.6 reported one P2 and three P3 findings.
  Accepted the undefined `ClientContextHandle` correction, clarified that only
  fork-inherited sessions are forbidden, and added the explicit `CHANGELOG.md`
  release step. Rejected the P2 claim that ASGI synchronous signal receivers
  run on the event-loop thread: Django 5.2 `ASGIHandler` establishes one
  `ThreadSensitiveContext`; `Signal.asend()` wraps synchronous receivers in
  default thread-sensitive `sync_to_async`; sync views and response close use
  the same context. A local firing probe observed request-start, sync-view, and
  request-finish receivers on the same executor thread. The plan now requires
  that exact source/thread-identity proof on Django 5.2 and the newest 6.x.
  Cross-family verdict after disposition: no unresolved blocker.
- Implementation reconnaissance corrected an ASGI cleanup detail before
  production edits. ASGI tears down its per-request
  executor, so owner-local cleanup-pending state may have no later same-thread
  retry. The plan now requires one immediate same-owner retry, retains and logs
  repeated failure for any surviving owner context, and names process recycling
  instead of an unsupported cross-thread cleanup registry.
- Implementation completed in the uncommitted release worktree. Core
  submission accepts a borrowed same-context `BrokerSession`; `WeftClient`
  owns a same-thread retained lease only inside its explicit context lifecycle;
  and Django uses a PID-guarded, thread-critical request-local registry with
  bounded fallbacks for direct async work, closed requests, callbacks, and
  alternate roots. Core and Django independent implementation reviews both
  returned PASS after their findings were resolved.
- Release metadata is staged for the coordinated release: Weft `0.9.107`,
  `weft-django` `0.9.42`, `weft>=0.9.107`, and Django `>=5.2,<7`. The complete
  SQLite/default repository suite, Ruff, formatter, mypy, lock check, and diff
  hygiene are clean. The focused lifecycle suite also passed with Django 6.1,
  and the canonical PostgreSQL borrowed-session checkout proof passed. The
  production four-span burst measurement remains a post-deploy validation step;
  it cannot be reproduced faithfully in the local repository environment.
