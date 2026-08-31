# LivenessMonitor Service and Runtime Probe Package Plan

Status: draft
Source specs: docs/specifications/01-Core_Components.md [CC-2.3], [CC-3.2]; docs/specifications/03-Manager_Architecture.md [MA-1.6a], [MA-1.8]; docs/specifications/05-Message_Flow_and_State.md [MF-3.1], [MF-3.2], [MF-5]; docs/specifications/07-System_Invariants.md [OBS.4], [OBS.10], [OBS.11a], [MANAGER.18]; proposed deltas in docs/specifications/01A-Core_Components_Planned.md [01A-5], docs/specifications/03A-Manager_Architecture_Planned.md [03A-4], docs/specifications/05A-Message_Flow_and_State_Planned.md [05A-5], and docs/specifications/07A-System_Invariants_Planned.md [07A-LIVENESS]
Superseded by: [2026-08-29-liveness-reaper-and-custody-split-plan.md](./2026-08-29-liveness-reaper-and-custody-split-plan.md)

Class: 5. Plan type: implementation with spec revision. Promotion strategy:
C, existing `*A-*.md` planned companions, followed by section-by-section
graduation into the canonical sibling specs before shipped code cites them.
This adds a persistent internal process and changes manager-owned service
inventory and admission accounting. Hardening and independent review are
required.

## 1. Goal

Add a manager-supervised internal `LivenessMonitor` that periodically probes
the current process behind a TID and answers one simple point-in-time question:
`liveness(tid) -> alive | dead | unknown`, with the time remaining before an
uninterrupted unknown result expires to `dead`. Probe observations and timeout
state remain in memory. No new global queue, durable verdict stream, database,
or cleanup authority is introduced.

The existing `weft/runtime_liveness.py` is the seed of the analysis layer. It
moves into a focused `weft/liveness/` package. Core owns generic host-process
analysis; each runtime extension owns inspection of its own runtime and
registers that probe when loaded.

## 2. Source Documents

- [`01-Core_Components.md`](../specifications/01-Core_Components.md) [CC-2.3]
  defines `ServiceTask`; [CC-3.2] defines durable `RunnerHandle` identity and
  the current process-local probe registry.
- [`03-Manager_Architecture.md`](../specifications/03-Manager_Architecture.md)
  [MA-1.6a] owns internal service reconciliation; [MA-1.8] owns admission
  reserve accounting.
- [`05-Message_Flow_and_State.md`](../specifications/05-Message_Flow_and_State.md)
  [MF-3.1] reserves `_weft.*` endpoints, [MF-3.2] provides the closest periodic
  service pattern, and [MF-5] separates runtime observations from lifecycle
  truth.
- [`07-System_Invariants.md`](../specifications/07-System_Invariants.md)
  [OBS.4], [OBS.10], and [OBS.11a] constrain process titles and live evidence;
  [MANAGER.18] constrains the internal-lane reserve.
- Proposed normative text is recorded now in
  [`01A-Core_Components_Planned.md`](../specifications/01A-Core_Components_Planned.md)
  [01A-5],
  [`03A-Manager_Architecture_Planned.md`](../specifications/03A-Manager_Architecture_Planned.md)
  [03A-4],
  [`05A-Message_Flow_and_State_Planned.md`](../specifications/05A-Message_Flow_and_State_Planned.md)
  [05A-5], and
  [`07A-System_Invariants_Planned.md`](../specifications/07A-System_Invariants_Planned.md)
  [07A-LIVENESS]. Promote it to the canonical siblings in the implementation
  slice that makes each statement true.
- [`2026-05-09-runtime-liveness-probe-registry-plan.md`](2026-05-09-runtime-liveness-probe-registry-plan.md)
  is completed and supplies the existing registry seam.
- [`2026-08-27-per-tid-liveness-registry-and-monitor-split-plan.md`](2026-08-27-per-tid-liveness-registry-and-monitor-split-plan.md)
  is superseded by this plan. Its per-TID queue namespace and TaskMonitor split
  are not inherited.
- [`README.md`](../../README.md) defines the Unix-observable process-title and
  queue-first operating model that this design extends.

Required guidance: `AGENTS.md`, `docs/agent-context/decision-hierarchy.md`,
`docs/agent-context/engineering-principles.md`, and the writing, hardening, and
review-loop runbooks under `docs/agent-context/runbooks/`.

## 3. Decisions Locked by This Plan

1. **No new global or dedicated verdict queue.** The monitor uses only its
   ordinary task-local queues and the existing runtime registries. Its verdict
   cache and unknown deadlines are process-local memory; outbox replies are
   short-lived transport artifacts, not a verdict store.
2. **Restart resets uncertainty.** A monitor restart loses all unknown
   deadlines. Repeated restarts can therefore postpone death forever. This is
   an explicit accepted failure mode, not a persistence bug to repair.
3. **A new service, not a TaskMonitor mode.** `LivenessMonitor` is an internal
   persistent `ServiceTask` with its own manager service key, task class, role,
   and `_weft.liveness` endpoint.
4. **Analysis and policy are separate.** One probe returns `live`, `stale`, or
   `unknown` for one observation. The monitor alone converts consecutive
   `unknown` observations into a deadline and the public-facing
   `alive`/`dead`/`unknown` result.
5. **Runtime ownership stays with extensions.** Core does not import Docker,
   macOS sandbox, Microsandbox, or future runtime internals. Runtime plugins
   register probes in the process where analysis occurs. Registration remains
   a version-coupled internal seam for first-party extensions in this slice;
   `weft.liveness` is not added to [PY-1].
6. **Advisory first slice.** A `dead` liveness result does not mutate TaskSpec
   state, elect a manager, kill a process, or authorize cleanup. Existing
   lifecycle, singleton, admission, and cleanup decisions retain their current
   rules until a separate spec change explicitly consumes this service.
7. **No public CLI in this slice.** The protocol and helper are internal. The
   first slice proves the service boundary without adding a CLI verb or changing
   the 41-command facade bijection.

## 4. Context and Key Files

Files to add:

- `weft/liveness/__init__.py`: narrow private package facade.
- `weft/liveness/models.py`: immutable observation and result values.
- `weft/liveness/registry.py`: moved process-local probe registry.
- `weft/liveness/analysis.py`: one point-in-time evidence reduction.
- `weft/liveness/host.py`: `psutil` host identity and title corroboration.
- `weft/liveness/query.py`: private broker-facing `liveness(...)` helper; it is
  not eagerly re-exported from the package initializer.
- `weft/core/tasks/liveness_monitor.py`: scheduling, cache, deadlines, and
  request/reply handling.
- focused tests under `tests/liveness/` and
  `tests/tasks/test_liveness_monitor.py`.

Files to modify:

- `weft/_constants.py`: service identity plus interval, unknown-timeout, probe
  timeout, wait-cap, and worker-bound constants.
- `weft/core/manager.py`, `weft/core/manager_services.py`, and
  `weft/core/tasks/__init__.py`: build, launch, identify, observe, and reconcile
  the new internal service through the existing path, with process-title
  support enabled in its internal payload.
- `weft/core/endpoints.py` and `weft/core/manager_runtime.py`: import the moved
  registry helper without changing their existing liveness policy.
- `extensions/weft_docker/weft_docker/plugin.py`: import and register through
  `weft.liveness`; preserve Docker probe behavior.
- `extensions/weft_macos_sandbox/weft_macos_sandbox/plugin.py` and
  `extensions/weft_microsandbox/weft_microsandbox/plugin.py`: add
  extension-owned probes if their runtime identity can prove live or stale;
  otherwise register no probe and return `unknown` through the common miss.
- existing registry, manager, endpoint, extension, and architecture tests.
- `README.md`, `00-Quick_Reference.md`, and the canonical source specs during
  the final traceability slice.
- delete `weft/runtime_liveness.py` after every caller migrates. Do not keep a
  compatibility wrapper in the single-current-contract tree.

Read first, with comprehension checks:

- `weft/runtime_liveness.py`: which behavior is registry lookup, and which
  behavior belongs in evidence analysis?
- `weft/core/tasks/heartbeat.py` and `weft/core/tasks/service.py`: how does a
  persistent service expose due time without a private sleep loop, and how are
  blocking workers kept away from broker handles?
- `weft/core/manager.py` around `_build_heartbeat_spawn_payload`,
  `_build_task_monitor_spawn_payload`, `_service_key_for_child`, and
  `_trusted_service_key_from_metadata`: what complete inventory must gain the
  third service so reconciliation cannot create an orphan class?
- `weft/core/tasks/base.py` process-title and TID-mapping publication: why is
  `(pid, create_time)` stronger evidence than a title containing a short TID?
- `weft/_runner_plugins.py`: when does an entry-point plugin load, and why can a
  spawned monitor process not rely on registry mutations made in its parent?

Shared paths, do not duplicate:

- use `ServiceTask` due-time and worker-group machinery;
- use `MultiQueueWatcher` through the shared task reactor;
- use existing TID-mapping reducers and `RunnerHandle.from_dict` at the
  durable boundary;
- use the manager's current internal-service spawn/reconciliation path;
- use `psutil.Process(pid).create_time()` and zombie rejection already present
  in host liveness code; never reduce identity to `pid_exists(pid)`;
- keep extension loading in `_runner_plugins.py`; do not add a second plugin
  framework.

## 5. Contracts and Invariants

### Point-in-time analysis

`analyze_liveness(tid, snapshot) -> LivenessObservation` returns:

```python
LivenessEvidence = Literal["live", "stale", "unknown"]

@dataclass(frozen=True, slots=True)
class LivenessObservation:
    tid: str
    evidence: LivenessEvidence
    observed_at_monotonic: float
    source: str
    reason: str
    runtime_generation: str | None
```

The runtime generation is the SHA-256 of canonical JSON containing exactly the
validated handle's `runner`, `kind`, `id`, `control.authority`, normalized
`observations.host_processes` `(pid, create_time)` pairs, optional
`observations.liveness_provider`, and the mapping `terminal` hint. Other mutable
observations and metadata do not participate. This prevents diagnostic churn
from resetting a deadline while preventing one process generation's deadline
from being applied to a replacement process under the same TID.

Evidence follows `control.authority`:

1. For `host-pid`, scoped host identities are authoritative. Any exact live
   `(pid, create_time)` pair yields `live`. `stale` requires every valid scoped
   identity to be definitively absent/reused/zombie and no unresolved identity.
   An empty or malformed identity set, permission denial, namespace ambiguity,
   or a mix containing an unresolved identity is `unknown` unless another exact
   identity is live.
2. For `runner` and `external-supervisor`, the selected extension probe is
   authoritative. Host processes and titles are corroboration only; a live
   wrapper cannot mask a dead container or sandbox. Missing provider,
   registration, or conclusive extension output is `unknown`.
3. A positive result from the authoritative source wins an older
   `terminal: true` hint. With no positive result, a definitive authoritative
   stale result or the terminal hint yields `stale`.

Process titles are corroboration only. A title must match the Weft title
grammar and short TID, but title evidence cannot establish exact identity by
itself because short TIDs can collide, `setproctitle` may be unavailable, and
the OS may truncate or hide titles. A title mismatch may explain `unknown`; it
must not override a matching `(pid, create_time)` pair.

The moved registry keeps its current lock, stripped non-empty keys,
last-registration-wins behavior, missing-key `unknown`, and exception/invalid-
result-to-`unknown` guard. Its internal callable changes only to carry the
cooperative budget:

```python
RuntimeLiveness = Literal["live", "stale", "unknown"]
RuntimeLivenessProbe = Callable[[RunnerHandle, float], RuntimeLiveness]

def register_runtime_liveness_probe(
    key: str,
    probe: RuntimeLivenessProbe,
) -> None: ...

def runtime_liveness_from_registered_probe(
    handle: RunnerHandle,
    *,
    timeout_seconds: float = LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS,
) -> RuntimeLiveness: ...
```

Shared lookup selects a stripped string
`handle.observations["liveness_provider"]` when valid, otherwise
`handle.runner`. The monitor lazy-loads that selected key before calling the
same lookup; existing Manager/endpoint callers do not lazy-load, but use the
same provider selection against registrations already loaded in their process.

These remain private, version-coupled imports used by first-party extensions.
A stable third-party registration contract would require a separate [PY-1]
change.

### Monitor policy and simple result

The internal query result is:

```python
LivenessState = Literal["alive", "dead", "unknown"]

@dataclass(frozen=True, slots=True)
class LivenessResult:
    tid: str
    state: LivenessState
    remaining_seconds: float | None
    reason: str
```

- `live` maps to `alive`, clears an unknown deadline, and returns
  `remaining_seconds=None`.
- `stale` maps immediately to `dead`, clears an unknown deadline, and returns
  `remaining_seconds=0.0`.
- The first `unknown` for a runtime generation creates one monotonic deadline
  at `now + LIVENESS_UNKNOWN_TIMEOUT_SECONDS`.
- Further `unknown` observations for that generation do not extend it. Before
  expiry the result is `unknown` with `max(0, deadline - now)`; at or after
  expiry the result is `dead`, `remaining_seconds=0.0`, and reason
  `unknown_timeout`.
- A changed runtime generation resets the deadline. A later positive probe may
  change a prior timeout-derived `dead` result back to `alive`; this surface is
  not a lifecycle state machine.
- A definitive stale result latches `dead` for that generation. Later
  `unknown` observations do not weaken it; only `live` or a changed generation
  clears it. A timeout-derived `dead` likewise stays dead while the same
  generation remains consecutively unknown.

### Internal request protocol

The service claims `_weft.liveness`. The helper resolves that endpoint and
writes an ordinary message to its inbox:

```json
{"type":"liveness_query","request_id":"<opaque>","tid":"<full-tid>","expires_at_ns":1735689600000000000}
```

The response is written to the monitor's ordinary outbox:

```json
{"type":"liveness_result","request_id":"<same>","tid":"<full-tid>","state":"alive|dead|unknown","remaining_seconds":null,"reason":"<diagnostic>","expires_at_ns":1735689600000000000}
```

Request IDs are caller-generated UUIDs. A reader accepts only an exact
`(type in {"liveness_result", "liveness_error"}, request_id, tid)` tuple and
exact-deletes that row by broker message ID. `reason` is opaque diagnostic text;
callers must not branch on it.
No caller-specific reply queue and no new global queue are added. The monitor
exact-deletes expired response rows from its own outbox; on transport timeout,
the helper also makes one best-effort exact-delete sweep for its own tuple.
`observed_at_monotonic` remains monitor-local cache metadata and is never sent
across processes. `expires_at_ns` is Unix epoch nanoseconds used only for
transport-row retirement.

The private leaf helper is exactly:

```python
def liveness(
    tid: str,
    *,
    context: str | Path | None = None,
    timeout_seconds: float = LIVENESS_QUERY_TIMEOUT_SECONDS,
) -> LivenessResult: ...
```

It lives at `weft.liveness.query.liveness` and is not eagerly imported by
`weft.liveness.__init__`, keeping registry/analysis imports broker-free and
cycle-safe. It requires the canonical full TID format, ensures or discovers the Manager,
resolves `_weft.liveness`, sends one UUID-keyed query, and waits for the exact
reply tuple. `timeout_seconds` must be finite and satisfy
`0 < timeout_seconds <= LIVENESS_QUERY_TIMEOUT_SECONDS`; invalid TID or timeout
raises `ValueError`. Missing/disabled/replaced endpoint
raises private `LivenessUnavailableError`; elapsed transport wait raises private
`LivenessQueryTimeoutError`. Those errors are not `dead` results. This package
and its exceptions remain private under [PY-1].

A cached observation may answer only when its monotonic age is at most
`LIVENESS_OBSERVATION_FRESHNESS_SECONDS`. A cached `unknown` recomputes
remaining time from its original deadline and never extends it. Otherwise the
query schedules an immediate probe. The query wait is a transport timeout, not
the unknown liveness deadline.

### Persistent service behavior

- On startup the monitor performs one generator-based TID-mapping replay. It
  then consumes only rows newer than its last message-ID cursor and maintains
  an in-memory latest-row index. A 10-minute full reconciliation detects manual
  deletion and repairs cursor/index drift. There is one due-heap entry per
  TID/generation; stale entries are discarded by token comparison.
- All runtime/host inspection runs in a bounded `ServiceTask` worker group.
  The reactor alone reads/writes Weft queues, updates deadlines, and replies.
- Each due TID has at most one in-flight probe. Work carries a unique token and
  runtime generation; the reactor discards a late result unless both still
  match current state. Late cycles coalesce and do not replay missed intervals.
- Extension probes receive the 2-second cooperative budget and must apply it to
  their runtime API. When the monitor's decision budget expires it records
  `unknown` and ignores a later result. Python threads cannot preempt a broken
  extension: a hung callback continues to occupy one of eight bounded lanes;
  lane exhaustion degrades new probes to `unknown` and emits a diagnostic until
  the monitor restarts. Process isolation is deliberately out of scope.
- The monitor loads the runner plugin named by a validated handle before
  probing so registration exists in the monitor process. The selected registry
  key is `observations.liveness_provider` when present, otherwise
  `handle.runner`; the monitor loads and looks up that same provider key.
  Extensions register only their own entry-point key, not shared aliases such
  as `manager-supervisor`. Any handle producer whose runner is an alias must
  publish `liveness_provider`; pre-change alias rows without it remain
  `unknown`. This optional observation is extension routing metadata, not a new
  top-level `RunnerHandle` field.
- An unknown runner or failed optional plugin load yields `unknown`; it does
  not prevent service startup.
- A validated request is exact-acknowledged after its pending record is
  installed. Malformed input is exact-acknowledged without reply. Duplicate
  exact `(request_id, tid)` requests share one pending probe; reuse of one
  request ID for another TID yields a `liveness_error` reply and no probe.
  Pending query count is capped; `busy` is a transport error, not `unknown`.
  The request `expires_at_ns` is derived from the helper's transport timeout.
  Ingestion never trusts it beyond the protocol maximum: effective expiry is
  the earlier of the supplied epoch deadline and monitor receipt time plus
  `LIVENESS_QUERY_TIMEOUT_SECONDS`, converted immediately to a monitor-local
  monotonic deadline. Reply expiry is independently set by the monitor from
  `LIVENESS_REPLY_RETENTION_SECONDS`.
  The reactor removes expired pending records even when their worker is hung;
  a result for an expired request is discarded and emits no reply. A request
  already expired when read is acknowledged without reply. An exact duplicate
  adds neither a second waiter nor a second response; the original one-row
  response serves retry correlation.
- Probes are read-only. Registration grants no permission to mutate a runtime,
  process, TaskSpec, queue, mapping, or durable handle.
- Memory and steady-state scheduling are `O(current retained TIDs)`. Unknown
  mapping retention can therefore make memory grow with the retained registry.
  This is accepted for the first slice; capacity eviction is forbidden because
  it would silently reset unknown deadlines.

### Existing invariants that must not change

- TID format and immutability, forward-only lifecycle transitions, TaskSpec
  immutability, and reserved-queue policy.
- Queue-backed lifecycle state remains canonical. Liveness is advisory and
  ephemeral.
- The existing manager/bootstrap/endpoint consumers of the registry keep their
  current direct `live/stale/unknown` semantics after the module move. They do
  not silently start consulting timeout-derived monitor verdicts.
- Cleanup still treats undecidable mapping evidence as protected. This plan
  does not authorize deletion of old undecidable mappings.
- `weft.state.*` remains runtime-only and excluded from dumps.
- No new dependency: `psutil` and `setproctitle` already exist in the project.

First-slice defaults are part of the proposal, not implementation choices left
open. `_constants.py` owns:

| Name | Value | Meaning |
|---|---:|---|
| `WEFT_LIVENESS_MONITOR_ENABLED` | environment key | Boolean service flag. |
| `LIVENESS_MONITOR_ENABLED_DEFAULT` | `True` | Default for the service flag. |
| `LIVENESS_MONITOR_PROBE_INTERVAL_SECONDS` | `5.0` | Normal re-probe cadence. |
| `LIVENESS_UNKNOWN_TIMEOUT_SECONDS` | `30.0` | Consecutive-unknown deadline. |
| `LIVENESS_RUNTIME_PROBE_TIMEOUT_SECONDS` | `2.0` | Cooperative extension/decision budget. |
| `LIVENESS_QUERY_TIMEOUT_SECONDS` | `5.0` | Helper transport wait. |
| `LIVENESS_OBSERVATION_FRESHNESS_SECONDS` | `5.0` | Maximum cached-observation age. |
| `LIVENESS_REPLY_RETENTION_SECONDS` | `60.0` | Orphan response lifetime. |
| `LIVENESS_FULL_RECONCILE_INTERVAL_SECONDS` | `600.0` | Mapping index repair cadence. |
| `LIVENESS_MONITOR_MAX_IN_FLIGHT_PROBES` | `8` | Worker-lane bound. |
| `LIVENESS_MONITOR_MAX_PENDING_QUERIES` | `1024` | Pending waiter bound. |

Only the enabled flag is configurable in the first slice. Numeric values remain
code constants until operating evidence justifies more configuration surface.

Fatal versus best effort:

- malformed public/internal query envelopes are rejected and acknowledged by
  ordinary task error handling;
- corrupt monitor-owned state or an impossible reducer transition is fatal;
- individual probe failures, missing extensions, permission errors, title
  reads, and response delivery failures are best-effort diagnostics and must
  not terminate the monitor.

## 6. Rollout, Rollback, and One-Way Doors

Roll out in one release because callers and extensions import the moved module.
There is no persisted payload migration and no queue cutover. The service can
be disabled with a dedicated `WEFT_LIVENESS_MONITOR_ENABLED` configuration
value while the existing direct liveness paths continue to work.

Rollback removes the manager service inventory, endpoint, and package callers,
then restores `weft/runtime_liveness.py` and the prior imports. Since all new
state is in memory, rollback requires no data cleanup. The only compatibility
edge is Python import location; coordinated first-party extension updates are
therefore part of the same release.

The deliberate one-way policy is timeout-derived `dead` as an advisory result.
It is contained by not granting that result lifecycle, cleanup, election, or
kill authority in this slice.

## Spec Baseline

- `74a2d3bc184ff6bc649291299569cb301749b465` —
  `01-Core_Components.md`, `03-Manager_Architecture.md`,
  `05-Message_Flow_and_State.md`, `07-System_Invariants.md`, their planned
  companions, and `14-Python_API_Surfaces.md` at plan authoring time.
- Plan type: implementation with spec revision.
- Promotion baseline: pending. When the spec-promotion slice is applied, record
  its commit SHA or the baseline SHA plus exact spec diff and rerunnable spec
  gate if the user requests uncommitted review.

## Proposed Spec Delta

Promotion strategy C uses the repository's existing `*A-*.md` planned-spec
classification. The exact review text is already staged in the following
sections; it is not current behavior and shipped code must not cite it before
promotion.

| Planned spec | Strategy | Exact section |
|---|---|---|
| `docs/specifications/01A-Core_Components_Planned.md` | C | [01A-5] LivenessMonitor and package ownership, evidence authority, timeout policy |
| `docs/specifications/03A-Manager_Architecture_Planned.md` | C | [03A-4] service inventory, desired-service rules, admission floor |
| `docs/specifications/05A-Message_Flow_and_State_Planned.md` | C | [05A-5] request/reply and periodic probe flow |
| `docs/specifications/07A-System_Invariants_Planned.md` | C | [07A-LIVENESS] LIVENESS.P1-P9 |

Graduation is section-by-section, not a file rename: before code, insert each
reviewed requirement into its canonical sibling under [CC-2.3]/[CC-3.2],
[MA-1.6a]/[MA-1.8], [MF-3.1]/[MF-5], and the observability/manager invariant
groups; add the plan backlink; remove [01A-5], [03A-4], [05A-5], and
[07A-LIVENESS] from the planned companions. Do not add implementation mappings
in that slice. Add mappings and reciprocal code `Spec:` references together in
the final traceability slice. `14-Python_API_Surfaces.md` receives no delta:
`weft.liveness` and the query helper remain private, and first-party extension
registration remains version-coupled.

## 7. Implementation Tasks

1. **Spec-promotion slice.** Graduate the four exact planned sections as stated
   in `## Proposed Spec Delta`, remove those planned copies, add canonical plan
   backlinks, run `tests/specs`, `check-doc-paths`, and the repository
   traceability scanner, then record the promotion baseline. Stop before code if
   review findings are not fully dispositioned or the canonical owners cannot
   express the delta without contradiction.

2. **Promote the package and point-in-time analyzer.** Move the registry into
   `weft/liveness/registry.py`; add models, host inspection, analysis, and a
   narrow private `weft.liveness.__all__`. Migrate all existing imports and remove the
   old module in the same slice. Red-green tests cover registry miss/hit/error,
   exact PID/create-time identity, zombie rejection, title corroboration,
   authority-aware evidence conflicts, multi-PID truth-table cases, malformed
   handles, and canonical generation fingerprints.
   Stop if the package starts importing `weft.core`, runtime extensions, or
   broker state directly.

3. **Make extension registration available in the spawned process.** Add the
   optional `observations.liveness_provider` routing rule to every alias-handle
   producer, teach analysis to load and look up that exact provider or
   `handle.runner` through the existing runner loader, and update Docker
   registration/imports. Do not register a shared `manager-supervisor` alias.
   Docker registers `docker`, Microsandbox registers `microsandbox`, and macOS
   sandbox registers `macos-sandbox`, each using its existing runtime identity
   and cooperative timeout. macOS keeps `control.authority="runner"`; its
   extension-owned read-only probe evaluates the published `(pid, create_time)`
   rather than changing control ownership. Add regression tests for Manager and
   endpoint Docker alias handles after the shared alias registration is removed.
   Test with fake external runtime clients; do not mock registry selection.
   Stop if core needs an extension name table or if plugin loading mutates
   durable task state.

4. **Implement `LivenessMonitor`.** Add the internal `ServiceTask`, monotonic
   deadline reducer, due heap, bounded probe workers, in-memory cache, endpoint
   claim, incremental mapping cursor, full reconciliation, strict query parser,
   bounded and expiring pending set, and expiring outbox replies. Use an injected monotonic
   clock in unit tests and the real broker in service tests. Cover
   unknown countdown without extension, reset on `live`, immediate `stale`,
   stale latch, generation reset, late positive revival, restart reset,
   coalescing, token/generation race rejection, cooperative-budget expiry, lane
   saturation, request collision, capacity error, reply expiry, and unrelated
   outbox rows. Prove timed-out callers behind hung probes cannot permanently
   exhaust pending capacity, and cover a request already expired at ingestion.
   Stop if implementation asks for a verdict queue, SQLite table,
   private sleep loop, capacity eviction, or worker-owned broker connection.

5. **Add manager supervision.** Extend every closed internal-service inventory:
   constants, task-class resolution, payload construction, trusted metadata,
   service-key reduction, status summaries, convergence, and child cleanup.
   Set the common admission reserve formula to
   `max(ceil(N * f), 3 + int(liveness_monitor_enabled))`; backend-specific
   usage observation stays unchanged. Harness tests prove
   bootstrap, endpoint ownership, restart after death, duplicate convergence,
   disable behavior, and no public submission of the reserved class/key.
   Stop if a second service reconciler or direct process launcher appears.

6. **Expose the internal helper and integrate scheduling.** Implement the exact
   private `weft.liveness.query.liveness(...)` helper and private transport errors,
   resolve `_weft.liveness`, correlate the full response tuple, and apply the
   bounded transport wait and timeout sweep. Add a real
   end-to-end test: manager starts monitor, a live host task answers `alive`, a
   fabricated undecidable handle counts down, and no new global queue appears.
   Stop if this requires a CLI verb, caller-selected reply queue, or durable
   cached result.

7. **Reconcile traceability and operational docs.** Update
   `00-Quick_Reference.md`, README internal-service/admission/process-liveness
   passages, exact canonical `_Implementation mapping_` notes, module
   docstrings, and reciprocal `Spec:` backlinks together. Record any accepted
   deviations before changing the specs, rerun the traceability scanner against
   the recorded promotion baseline with no new errors or warnings from this
   slice, and remove any obsolete pointer to `weft/runtime_liveness.py`.

## 8. Testing and Acceptance

Keep real: `WeftTestHarness`, SimpleBroker queues, manager supervision,
endpoint registration, task-local inbox/outbox correlation, spawned process
isolation, and psutil identity checks against a real short-lived child.

Mock only runtime boundaries that cannot be deterministic in CI: Docker daemon,
macOS sandbox process inspection, Microsandbox API, permission denial, and a
hung extension probe. Inject the monotonic clock instead of sleeping through
unknown deadlines.

Required focused coverage:

- analyzer truth table and conflict precedence;
- timeout reducer boundary at just before and exactly at expiry;
- process-local registry isolation plus plugin load in the monitor process;
- strict request/reply schema and request-ID correlation;
- no response-row theft with concurrent callers, same-TID fan-in, ID collision,
  caller timeout, and late reply retirement;
- malformed input cannot poison the service; valid requests are acknowledged at
  the specified pending-record boundary;
- bounded workers and pending queries; one in-flight probe per TID; old-token,
  old-generation, and post-budget results are ignored;
- incremental cycles do not rescan unchanged mapping history or grow the heap;
- fresh cached `unknown` recomputes remaining time without extending it;
- manager service convergence and admission floors of four when enabled and
  three when disabled;
- endpoint missing or replacement during a query yields the named transport
  failure rather than `dead`;
- restart clears memory and restarts the full unknown timeout;
- timeout-derived `dead` causes no lifecycle, cleanup, kill, or election effect;
- all prior manager and endpoint liveness tests pass unchanged after import
  migration.

Final gates:

```bash
./.venv/bin/python -m pytest tests/liveness tests/tasks/test_liveness_monitor.py -q
./.venv/bin/python -m pytest
./.venv/bin/python -m pytest -m ""
./.venv/bin/mypy weft bin integrations/weft_django/weft_django extensions/weft_docker/weft_docker extensions/weft_macos_sandbox/weft_macos_sandbox extensions/weft_microsandbox/weft_microsandbox --config-file pyproject.toml
./.venv/bin/ruff check .
./.venv/bin/python -m pytest tests/specs -q
```

Runtime acceptance: `weft status` shows one canonical LivenessMonitor; its
endpoint resolves; live/stale/unknown fixtures return the specified values; an
unknown result reaches zero without extending its own deadline; restart resets
that deadline; queue listing shows only the service's standard task-local
queues and existing global registries.

## 9. Deviation Log

| Spec ref | Planned behavior | Actual behavior | Rationale | Spec proposal |
|---|---|---|---|---|

## 10. Independent Review Loop

Before implementation, a fresh-context reviewer must read this plan, the four
planned spec sections, `weft/runtime_liveness.py`, `ServiceTask`, Heartbeat,
manager service convergence, and all first-party runtime plugins. The review
must focus on: evidence precedence, process-title overclaiming, registry loading
inside a spawned process, timeout reset/revival semantics, shared-outbox reply
correlation, and whether advisory results leak into destructive authority.

Each finding is dispositioned as adopted, rebutted with evidence, or explicitly
deferred. A different model family is preferred; if unavailable, record that
limitation and use a fresh-context reviewer plus a separate author fresh-eyes
pass.

### Independent Review Record

Completed 2026-08-29 with three fresh-context reviewers. A different model
family was not available; all reviewers were independent Codex contexts. Final
passes: architecture PASS, contract PASS, testability PASS.

| Severity | Finding | Disposition |
|---|---|---|
| blocking | A hard two-second wall-clock bound cannot preempt `ServiceTask` threads. | Adopted cooperative budget, late-result discard, bounded-lane degradation, and explicit restart recovery; subprocess isolation remains out of scope. |
| blocking | Provider loading followed by alias-key lookup could invoke the wrong extension and regress direct Manager/endpoint callers. | Adopted one provider-selection rule in shared lookup; monitor lazy-loads and looks up the same key; shared aliases are removed; alias producers publish `liveness_provider`. |
| blocking | Generic host-positive evidence could mask a dead external runtime. | Adopted authority-aware reduction: host identity is authoritative only for `host-pid`; extension result is authoritative for `runner`/`external-supervisor`. |
| blocking | Shared persistent outbox replies and pending callers could accumulate forever. | Added request expiry, bounded pending count, result/error TTL, monitor exact-delete sweep, caller timeout sweep, and expired-result discard. |
| blocking | Reply correlation by request ID alone could cross TIDs or result/error types. | Match exact `(type, request_id, tid)`; UUID IDs; collision error; exact broker-message deletion. |
| blocking | `LivenessResult.observed_at_monotonic` could not truthfully cross processes. | Removed it from the returned model; monotonic observation time stays monitor-local; wire expiry is Unix epoch nanoseconds. |
| should-fix | Evidence generation, multi-PID reduction, stale-to-unknown behavior, and late worker races were ambiguous. | Enumerated fingerprint fields, multi-PID truth table, per-generation stale/timeout latches, and token-plus-generation commit guard. |
| should-fix | Full TID-mapping replay every five seconds and unbounded retained undecidable TIDs were hidden costs. | Added startup replay, cursor-based incremental reads, 600-second reconciliation, one heap entry per generation, explicit `O(current retained TIDs)` memory, and no capacity eviction. |
| should-fix | The new service had no owning Manager planned-spec delta, and disable/reserve semantics were unclear. | Added [03A-4], independent desired-service rule, launch-only disable semantics, and common reserve formula `max(ceil(N*f), 3 + enabled)`. |
| should-fix | `weft.liveness` conflicted with [PY-1], and a broker helper in the package facade risked cycles. | Kept the package private/version-coupled; placed transport in non-eager `weft.liveness.query`; no Python public-surface delta. |
| should-fix | Probes were not explicitly read-only and cache/reason semantics were incomplete. | Added no-side-effect authority rule, exact freshness constant, cached-unknown countdown rule, and opaque diagnostic `reason`. |
| should-fix | Caller-controlled timeout/expiry could defeat pending bounds. | Require finite `0 < timeout <= 5.0`; server caps supplied epoch expiry at receipt plus five seconds and converts it to monotonic time. |
| minor | macOS sandbox was incorrectly assigned the generic `host-pid` path despite runner control authority. | Require an extension-owned `macos-sandbox` probe over its existing PID/create-time observation without changing control authority. |
| minor | The older per-TID registry/TaskMonitor-split draft conflicts with this design. | Marked it superseded; no queue-layout or TaskMonitor split work is inherited. |

## 11. Out of Scope

- a new global queue, verdict log, database table, or persisted timeout;
- changing `weft.state.tid_mappings` layout or cleanup retention;
- splitting or renaming TaskMonitor;
- using liveness results to kill, clean, elect, admit, or synthesize lifecycle
  state;
- a new CLI verb or stable `weft.client` API;
- PING/PONG as an active probe in the first slice;
- remote-machine process discovery outside a runtime extension;
- title-only proof of exact TID identity.

## 12. Fresh-Eyes Review

Completed 2026-08-29 after the independent-review revisions. The author pass
checked the user decisions against every layer: no new global/dedicated verdict
queue; in-memory deadline reset on restart; a distinct persistent
`LivenessMonitor`; `runtime_liveness.py` moved into `weft/liveness/`; and
extension-owned runtime inspection. It also checked the uncomfortable edges:
hung extension threads degrade instead of pretending to be preemptible;
retained undecidable mappings imply proportional memory; outbox rows are
durable transport residue but bounded and not liveness state; and `dead`
remains advisory. No undispositioned finding remains. The plan stays `draft`
until the owner approves the proposal.
